"""ToolRouter：策略判定 → 执行 → 事件。

管线对齐 Codex 的 `ToolOrchestrator::run()`：

    工具调用 → ToolRouter
      ├─ 1. 解析成结构化 Action（由工具实现方负责，不经模型）
      ├─ 2. Policy.decide() → run / ask / refuse
      ├─ 3. ask  → 发 approval.required 事件并**挂起**，等人工决定
      ├─ 4. refuse → 把拒绝**作为结构化工具结果**喂回模型
      └─ 5. run  → 执行 → 结果截断/落盘 → 发 tool.output
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from ..errors import tool_error
from ..policy import Action, Decision, Policy, PolicyResult
from ..protocol import (
    EventBus, EventType, ERR_TOOL, ToolCall, ToolResult, truncate_output,
    DEFAULT_OUTPUT_LIMIT,
)
from .registry import Tool, ToolRegistry


class ApprovalRequest:
    """一次待决的审批。审批闸门的数据结构。"""

    def __init__(self, call_id: str, tool: str, action: Action, res: PolicyResult,
                 deferred_events: int = 0) -> None:
        self.call_id = call_id
        self.tool = tool
        self.action = action
        self.result = res
        self.deferred_events = deferred_events
        self.decided: Optional[str] = None      # "approved" | "rejected" | None
        self.remember: bool = False

    def to_json(self) -> Dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool": self.tool,
            "action": self.action.kind.value,
            "summary": self.action.summary or self.action.command,
            "risk": self.result.risk,
            # 升格来源要带出去：前端据此区分「危险动作专项闸门」与「普通审批」，
            # 两者在 UI 上的语气与默认按钮不同（危险动作不该有"记住"选项）。
            "escalated_by": self.result.escalated_by,
            "reason": self.result.reason,
            "matched_rule": self.result.matched_rule,
            "deferred": self.deferred_events,
            "paths": self.action.paths,
            # 危险动作不得被"批准并记住"
            "rememberable": self.result.escalated_by != "danger",
        }


class ToolRouter:
    def __init__(self, registry: ToolRegistry, policy: Policy, bus: EventBus,
                 workspace: str,
                 output_limit: int = DEFAULT_OUTPUT_LIMIT,
                 artifact_dir: Optional[str] = None,
                 approval_handler: Optional[Callable[[ApprovalRequest], Optional[str]]] = None) -> None:
        self.registry = registry
        self.policy = policy
        self.bus = bus
        self.workspace = os.path.realpath(workspace)
        self.output_limit = output_limit
        self.artifact_dir = artifact_dir or os.path.join(self.workspace, ".crucible", "artifacts")
        os.makedirs(self.artifact_dir, exist_ok=True)

        # 审批处理器：返回 "approved"/"rejected"，或 None 表示**挂起**（等待外部决定）。
        # 挂起是默认行为 —— 对应 Codex 的 ExecApprovalRequestEvent：
        # 发事件后由 CLI/TUI 前端提示用户，再把响应回灌。
        self.approval_handler = approval_handler

        # 已记住的批准（"批准并在本会话记住"）
        self._session_allow: set = set()
        # 待决审批（挂起中）
        self.pending: Dict[str, ApprovalRequest] = {}
        # 本会话累计
        self._executed: List[Dict[str, Any]] = []

        # 变更集记录（改坏了能还原，不靠 git）。
        # 由 session 注入 —— router 不自己创建它，因为它的落盘位置
        # 属于 run 目录（session 的地盘）。
        self.changesets = None      # 类型为 changeset.ChangeSetStore 或 None
        # 事件序号提供者：用于把变更挂到具体 seq 上（支持"回滚到第 N 步"）
        self.seq_provider: Optional[Callable[[], int]] = None

        # 能力账本（G-5，CapScope）。由 loop 在 run() 开头注入 ——
        # 上限必须从**任务文本**推导，而任务文本只有 loop 知道。
        # **只观察，不参与判定**：它不改变 res，只看。
        self.ledger = None          # 类型为 capability.CapabilityLedger 或 None

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def route(self, call: ToolCall) -> ToolResult:
        call_id = "call_" + uuid.uuid4().hex[:12]

        # ── 1. 工具是否存在 ──
        tool = self.registry.tool(call.name)
        if tool is None:
            # 书 §5：工具层错误**不终止会话，把错误变成模型的输入**。
            # 幻觉调用会收到「工具不存在」的结构化错误结果，由模型下一轮自行纠正。
            res = tool_error(
                call.name, "unknown_tool",
                f"工具 `{call.name}` 不存在。可用工具："
                f"{', '.join(self.registry.names())}。")
            self.bus.emit(EventType.TOOL_OUTPUT, call_id=call_id, tool=call.name,
                          ok=False, text=res.text, error_layer=ERR_TOOL)
            return res

        # ── 2. 解析成结构化 Action ──
        try:
            action = tool.describe_action(call.args)
        except Exception as e:
            res = tool_error(call.name, "bad_args", f"参数无法解析：{e}")
            self.bus.emit(EventType.TOOL_OUTPUT, call_id=call_id, tool=call.name,
                          ok=False, text=res.text, error_layer=ERR_TOOL)
            return res

        action.tool = call.name
        if not action.summary:
            action.summary = call.name + " " + json.dumps(call.args, ensure_ascii=False)[:120]

        # ── 3. 策略判定 ──
        # 能力观察放在判定**之前**。理由：被 policy 拒绝的动作
        # 恰恰是越权的最硬证据 —— 等拒绝之后再看，就看不到它们了。
        # `observe()` 只看不判，返回值不参与下面的流程控制。
        if self.ledger is not None:
            for ev in self.ledger.observe(action, call.name, seq=self._next_seq()):
                self.bus.emit(EventType.CHECK, scope="capability",
                              ok=False, out_of_scope=True,
                              code=ev.code, capability=ev.capability,
                              severity=ev.severity, agent_id=ev.agent_id,
                              reason=ev.detail, evidence=ev.evidence,
                              tool=ev.tool)

        verdict = self.policy.decide(action)
        self.bus.emit(EventType.TOOL_REQUESTED,
                      call_id=call_id, tool=call.name, args=call.args,
                      action=action.kind.value, paths=action.paths,
                      decision=verdict.decision.value, risk=verdict.risk,
                      reason=verdict.reason, escalated_by=verdict.escalated_by)

        if verdict.decision is Decision.REFUSE:
            # 拒绝也作为**结构化工具结果**喂回模型（书 §5：错误变成模型的输入）
            res = tool_error(call.name, "refused",
                             f"操作被策略拒绝：{verdict.reason}",
                             escalated_by=verdict.escalated_by)
            # 拒绝单独留一笔能力记录。它已被沙箱处理、不需再拦，
            # 但"拒绝次数突然变多"本身就是注入尝试的线索 —— 必须可观察。
            if self.ledger is not None:
                self.ledger.note_refused(action, call.name, verdict.reason,
                                         seq=self._next_seq())
            self.bus.emit(EventType.TOOL_OUTPUT, call_id=call_id, tool=call.name,
                          ok=False, text=res.text, error_layer=ERR_TOOL)
            return res

        if verdict.decision is Decision.ASK:
            ok = self._request_approval(call_id, tool, action, verdict)
            if not ok:
                res = tool_error(
                    call.name, "approval_rejected",
                    f"用户拒绝了这次执行（{verdict.reason}）。工作区未做任何改动。")
                self.bus.emit(EventType.TOOL_OUTPUT, call_id=call_id, tool=call.name,
                              ok=False, text=res.text, error_layer=ERR_TOOL)
                return res

        # ── 4. 执行 ──
        t0 = time.time()

        # 变更集：**执行前**必须把旧内容拍下来。
        # 这是唯一时机 —— 文件一旦被写，旧内容就没了，事后无法补救。
        #
        # 两种预判方式，按代价从低到高：
        #   · 工具已给出结构化路径（action.paths，如 apply_patch）→ 只读那几个文件
        #   · 无法预知（shell）→ 但有副作用时，拍一份工作区内容快照
        #
        # 只读工具（side_effect=False）**完全不付代价** ——
        # 这正对应 arXiv 2604.28138 的实测："**>75% 的轮次不产生与恢复相关的状态**"。
        pre_content: Dict[str, Optional[str]] = {}
        pre_is_full = False
        if self.changesets is not None:
            if action.paths:
                pre_content = self.changesets.capture_before(action.paths)
            elif action.side_effect:
                # 全工作区内容快照 —— 标记为"完整快照"，
                # 这样工具事后报出的新文件才能被正确判为"当时不存在"。
                # 否则 shell 建文件会整条漏记（实测踩到，且不报错，很隐蔽）。
                pre_content = self.changesets.snapshot_workspace_content()
                pre_is_full = True

        try:
            res = tool.handler(call.args)
        except Exception as e:
            res = tool_error(call.name, "exception",
                             f"工具执行抛异常：{type(e).__name__}: {e}")
        dur = round(time.time() - t0, 3)

        # 执行后记录变更。
        # 优先用工具**自己报告**的 files_changed（结构化、最准）；
        # 若它没报，则用"执行前快照 vs 当前内容"做差分兜底（覆盖 shell 写文件）。
        if self.changesets is not None and res.ok:
            if res.files_changed:
                self.changesets.capture(res.files_changed, call.name,
                                        seq=self._next_seq(), before=pre_content,
                                        before_was_full_snapshot=pre_is_full)
            elif pre_content:
                self.changesets.capture(list(pre_content.keys()), call.name,
                                        seq=self._next_seq(), before=pre_content,
                                        before_was_full_snapshot=pre_is_full)

        # ── 5. 输出截断 + 落盘 ──
        res = self._cap_output(res, call.name, call_id)

        self._executed.append({
            "call_id": call_id, "tool": call.name, "args": call.args,
            "ok": res.ok, "dur": dur, "paths": action.paths,
        })

        self.bus.emit(EventType.TOOL_OUTPUT,
                      call_id=call_id, tool=call.name, ok=res.ok, dur=dur,
                      text=res.text, truncated=res.truncated,
                      full_output_path=res.full_output_path,
                      exit_code=res.exit_code,
                      files_changed=res.files_changed,
                      error_layer=res.error_layer,
                      verdict=verdict.decision.value)

        # 回填 seq：让这次结果有一个**可被证书引用的锚点**。
        # 必须在 emit 之后取 —— emit 内部才把 seq 递增到这一条。
        # 有了它，ECT 证书里的"证据"就能写成 (seq=42, tool=apply_patch)，
        # 而确定性校验器可以去事件流里查"第 42 条真的存在且成功吗"。
        res.seq = int(getattr(self.bus, "seq", 0) or 0)
        res.call_id = call_id

        # 文件改动单独发事件 —— 前端据此更新审查界面（Codex 的 file.changed）
        for p in res.files_changed:
            self.bus.emit(EventType.FILE_CHANGED, path=p, tool=call.name,
                          diff=res.meta.get("diff", "") if res.meta else "")

        return res

    # ------------------------------------------------------------------
    # 变更集
    # ------------------------------------------------------------------
    def _next_seq(self) -> int:
        """取当前事件序号，用于把变更挂到具体 seq 上。

        为什么需要 seq 而不是只用记录顺序：seq 是**跨记录可对齐**的标识 ——
        回滚时能说"回到第 12 步之前"，而"第 3 条变更记录"这种说法
        在混着事件流看时没有意义。
        """
        if self.seq_provider is not None:
            try:
                return int(self.seq_provider())
            except Exception:
                pass
        return 0

    # ------------------------------------------------------------------
    # 审批
    # ------------------------------------------------------------------
    def _request_approval(self, call_id: str, tool: Tool, action: Action,
                          verdict: PolicyResult) -> bool:
        # 会话内已记住批准（仅在用户显式选择"批准并记住"时才会进入这里）。
        # 注意：**危险动作不参与记忆** —— 即使记住了，也每次都要问。
        # 这是「约束的是动作，而非仅仅是结果」的直接体现。
        if verdict.escalated_by != "danger":
            key = (tool.name, action.kind.value, tuple(sorted(action.paths)))
            if key in self._session_allow:
                self.bus.emit(EventType.APPROVAL_REQUIRED,
                              call_id=call_id, auto=True, reason="本会话已记住该操作的批准",
                              decision="approved")
                return True

        req = ApprovalRequest(call_id, tool.name, action, verdict)
        self.pending[call_id] = req

        # 发闸门事件。事件的 `deferred` 表示「后续有多少个事件已被暂缓」——
        # 这是本项目的记忆点：把「流水线被截断」这件事显式化。
        self.bus.emit(EventType.APPROVAL_REQUIRED, **req.to_json())

        if self.approval_handler is None:
            # 无人处理 → 默认拒绝（故障安全）。绝不默认放行。
            req.decided = "rejected"
            self.pending.pop(call_id, None)
            return False

        outcome = self.approval_handler(req)
        if outcome is None:
            # **挂起**：由外部（HTTP 层的 /approve 端点）后续决定。
            # 这里返回 False 会让本次调用被记为「未执行」，
            # 但对外的语义是「等待决定」——由 loop.py 识别 pending 状态后重新驱动。
            self.bus.emit(EventType.APPROVAL_REQUIRED,
                          call_id=call_id, state="suspended",
                          reason="等待外部审批决定")
            return False

        req.decided = outcome
        self.pending.pop(call_id, None)
        if outcome == "approved":
            if req.remember:
                self._session_allow.add((tool.name, action.kind.value, tuple(sorted(action.paths))))
            return True
        return False

    def resolve_approval(self, call_id: str, decision: str, remember: bool = False) -> bool:
        """外部（HTTP 端点）回灌审批决定。

        返回是否找到并处理了该待决请求。

        **危险动作不接受 remember**：即使调用方传了 remember=True，
        也不会写入会话记忆。这是「约束的是动作」在执行路径上的第二道防线 ——
        第一道在 `_request_approval` 的查表处。
        """
        req = self.pending.get(call_id)
        if req is None:
            return False
        req.decided = decision
        if decision == "approved" and remember and req.result.escalated_by != "danger":
            req.remember = True
            key = (req.tool, req.action.kind.value, tuple(sorted(req.action.paths)))
            self._session_allow.add(key)
        self.bus.emit(EventType.APPROVAL_REQUIRED,
                      call_id=call_id, state="resolved", decision=decision,
                      remember=req.remember,
                      ignored_remember=(remember and not req.remember))
        return True

    # ------------------------------------------------------------------
    # 输出治理
    # ------------------------------------------------------------------
    def _cap_output(self, res: ToolResult, tool: str, call_id: str) -> ToolResult:
        """超过上限就截断并把全文落盘。

        书 §5：保留**前若干行（错误上下文）与后若干行（错误总结）**，
        中间以一行提示替代，并**说明完整输出已保存到临时文件供按需查看**。
        """
        if len(res.text) <= self.output_limit:
            return res

        full = res.text
        path = os.path.join(self.artifact_dir, f"{tool}-{call_id}.txt")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(full)
        except OSError:
            path = None  # 落盘失败不影响主流程

        capped, _ = truncate_output(full, self.output_limit)
        note = ""
        if path:
            note = f"\n\n（完整输出已保存到 {path}，可用 read_file 按行查看）"
        res.text = capped + note
        res.truncated = True
        if path:
            res.full_output_path = path
        return res

    # ------------------------------------------------------------------
    def summary(self) -> List[Dict[str, Any]]:
        return list(self._executed)
