"""主循环：ReAct + Harness 五功能闭环。

## 书里的骨架（§1）

    observation = Environment.observe()
    trajectory = [observation]
    while true:
        actions = Model(Harness.build_context(trajectory))
        if len(actions) == 0: break
        allowed_actions = Harness.constrain(actions)
        observation = Environment.apply(allowed_actions)
        if not Harness.verify(Environment):
            observation = Harness.correct(Environment)
        trajectory.append(allowed_actions, observation)

本模块逐步落实这个骨架，并补上书中散落各处的工程细节。

## 终止条件（书 §5）

> 最后，在所有自动化机制之上还需要全局的终止与升级条件：
> **最大迭代轮数、会话预算上限，以及连续失败超过阈值时升级到人工干预。**

三个条件都在 `LoopConfig` 里，且都有默认值 —— 因为「没有上限的循环」
本身就是最典型的控制流故障。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .context import ContextBuilder, StaticPrefix
from .correct import Corrector, Fault, FaultClass, LoopDetector, Watchdog, classify
from .model import BaseModel, ModelReply
from .policy import ApprovalPolicy, Policy, SandboxMode
from .protocol import (
    ERR_API, ERR_CONTROL, ERR_TOOL, EventBus, EventType, Message, ROLE_TOOL,
    ToolCall, ToolResult,
)
from .session import Session
from .tools.registry import ToolRegistry
from .tools.router import ToolRouter
from .verify import GoalVerifier, Verifier, lint_note, quick_lint


# ══════════════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════════════
@dataclass
class LoopConfig:
    # 书 §5 的三个全局终止条件
    max_iterations: int = 25
    max_tokens_budget: int = 600_000
    max_consecutive_failures: int = 6

    # 单轮内最多连续几个工具调用批次（防止一次回复里无限展开）
    max_tool_batches_per_turn: int = 6

    # 空闲看门狗（书 §5：每个长连接都需要活性信号）
    idle_timeout_s: float = 90.0

    # 即时 lint 开关（书 §5「即时语法反馈机制」）
    lint_after_edit: bool = True

    # 是否在每轮推理前注入环境状态栏（书 §2 的 Agent Status Bar）。
    # 默认关闭以保持最纯粹的前缀稳定性，需要时显式打开 ——
    # 打开时它**追加到末尾**，不破坏前缀。
    inject_status: bool = True


# ══════════════════════════════════════════════════════════════════
# 运行结果
# ══════════════════════════════════════════════════════════════════
@dataclass
class LoopResult:
    status: str                                   # completed | max_iterations | budget |
                                                  # escalated | stalled | suspended
    answer: str = ""
    iterations: int = 0
    tool_calls: int = 0
    reason: str = ""
    attempted_recoveries: List[str] = field(default_factory=list)
    goal: Optional[Dict[str, Any]] = None
    usage: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "answer": self.answer,
            "iterations": self.iterations,
            "tool_calls": self.tool_calls,
            "reason": self.reason,
            "attempted_recoveries": self.attempted_recoveries,
            "goal": self.goal,
            "usage": self.usage,
        }


# ══════════════════════════════════════════════════════════════════
# 循环
# ══════════════════════════════════════════════════════════════════
class AgentLoop:
    def __init__(self,
                 model: BaseModel,
                 registry: ToolRegistry,
                 prefix: StaticPrefix,
                 policy: Policy,
                 session: Session,
                 config: Optional[LoopConfig] = None,
                 goal_conditions: Optional[List[str]] = None,
                 approval_handler: Optional[Callable] = None) -> None:
        self.model = model
        self.registry = registry
        self.policy = policy
        self.session = session
        self.cfg = config or LoopConfig()
        self.bus: EventBus = session.bus

        self.context = ContextBuilder(prefix)
        session.attach_context(self.context, registry.names())

        self.router = ToolRouter(registry, policy, self.bus,
                                 workspace=session.meta.workspace,
                                 approval_handler=approval_handler)
        self.verifier = Verifier(session.meta.workspace)
        self.corrector = Corrector(bus=self.bus)
        self.loop_detector = LoopDetector()
        self.watchdog = Watchdog(self.cfg.idle_timeout_s)
        self.goal = GoalVerifier(goal_conditions or [])

        self._consecutive_failures = 0
        self._iterations = 0
        self._tool_calls = 0
        self._usage: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def run(self, user_message: str) -> LoopResult:
        self.bus.emit(EventType.TURN_STARTED,
                      workspace=self.session.meta.workspace,
                      model=self.model.name,
                      sandbox=self.policy.sandbox.value,
                      approval=self.policy.approval.value,
                      tool_order=self.registry.names())

        m = self.context.append_user(user_message)
        self.session.persist_message(m)

        while True:
            # ── 全局终止条件（书 §5）──
            if self._iterations >= self.cfg.max_iterations:
                return self._finish("max_iterations",
                                    f"已达到最大迭代轮数 {self.cfg.max_iterations}。"
                                    "任务可能未完成；如需继续，请提高上限或把任务拆小。")
            if self._total_tokens() >= self.cfg.max_tokens_budget:
                return self._finish("budget",
                                    f"已达到会话预算上限 {self.cfg.max_tokens_budget} token。")
            if self._consecutive_failures >= self.cfg.max_consecutive_failures:
                return self._finish("escalated",
                                    f"连续失败 {self._consecutive_failures} 次，已升级到人工干预。",
                                    escalate=True)
            if self.watchdog.is_stalled():
                self.bus.emit(EventType.ERROR, error_layer=ERR_CONTROL,
                              reason="空闲看门狗超时", **self.watchdog.to_json())
                return self._finish("stalled",
                                    f"超过 {self.watchdog.idle_timeout_s}s 没有新事件，"
                                    "判定为静默卡死并终止（书 §5：长连接需要活性信号）。")

            self._iterations += 1
            self.watchdog.beat()

            # ── 状态栏：追加到末尾，不碰前缀（书 §2）──
            if self.cfg.inject_status:
                self._inject_status()

            # ── 构造上下文 → 调用模型 ──
            request = self.context.build(self.session.meta.provider)
            self.context.assert_prefix_stable()      # 每次调用前自检前缀

            reply = self._call_model(request)

            # ── 失败处理 ──
            if reply.error_layer is not None:
                res = self._handle_model_error(reply)
                if res is not None:
                    return res
                continue

            # ── 成功：累计用量、复位失败计数 ──
            self._consecutive_failures = 0
            self.corrector.note_success("retry")
            self._accumulate_usage(reply)

            # ── 记录 assistant 消息（思考文字与凭证分开存 —— 书 §5 中立格式）──
            self.bus.emit(EventType.REASONING, text=reply.reasoning or "")
            if reply.content:
                self.bus.emit(EventType.MESSAGE, text=reply.content)
            am = self.context.append_assistant(
                content=reply.content,
                reasoning=reply.reasoning,
                tool_calls=reply.tool_calls,
                signature=reply.reasoning_signature,
                origin_model=reply.model,
            )
            self.session.persist_message(am)

            # ── 没有工具调用 → 任务结束 ──
            if not reply.tool_calls:
                answer = reply.content or ""
                if self.goal.conditions and not self.goal.report()["all_met"]:
                    # 书 §5：「给出了回答」不等于「完成了任务」。
                    # 目标未全部达成时，如实报告未达成，而不是把回答当成功。
                    unmet = self.goal.unmet()
                    return self._finish(
                        "completed",
                        f"模型给出了最终回答，但以下在开始时声明的目标条件尚未满足：\n"
                        + "\n".join(f"  · {c}" for c in unmet)
                        + "\n回答内容如下，请自行判断是否可接受。",
                        answer=answer,
                    )
                return self._finish("completed", "任务完成。", answer=answer)

            # ── 执行工具（含审批闸门）──
            suspended = self._execute_tool_calls(reply.tool_calls)
            if suspended is not None:
                return suspended

        return self._finish("completed", "循环结束。")

    # ------------------------------------------------------------------
    # 模型调用（含重试）
    # ------------------------------------------------------------------
    def _call_model(self, request: Dict[str, Any]) -> ModelReply:
        attempt = 0
        while True:
            reply = self.model.complete(request)
            self.watchdog.beat()

            if reply.error_layer is None:
                return reply

            # 分类 → 决定是否值得重试
            err = ToolResult(ok=False, text=reply.raw_error or "",
                             error_layer=reply.error_layer,
                             retryable=reply.retryable)
            fault = classify(err)
            plan = self.corrector.plan(fault, attempt, foreground=True)

            if plan["action"] == "retry" and attempt < 3:
                self.bus.emit(EventType.RETRY, attempt=attempt + 1,
                              delay_s=plan["delay_s"], reason=plan["reason"])
                self.corrector.note_failure("retry", plan["reason"])
                time.sleep(plan["delay_s"])
                attempt += 1
                continue

            # 不再重试：交回上层统一处理
            return reply

    def _handle_model_error(self, reply: ModelReply) -> Optional[LoopResult]:
        err = ToolResult(ok=False, text=reply.raw_error or "",
                         error_layer=reply.error_layer,
                         retryable=reply.retryable)
        fault = classify(err)
        self._consecutive_failures += 1

        self.bus.emit(EventType.ERROR,
                      error_layer=fault.layer,
                      cls=fault.cls.value,
                      reason=fault.reason,
                      detail=fault.detail,
                      consecutive=self._consecutive_failures,
                      retryable=(fault.cls is FaultClass.RETRYABLE))

        if fault.cls is FaultClass.RETRYABLE:
            # 可重试但已到重试上限 → 不无限循环
            return self._finish(
                "escalated",
                f"模型接口持续失败：{fault.reason}。已尝试 {self.corrector.attempted_actions()}。",
                escalate=True,
            )

        # 不可重试的 API 错误（401/403/404/400）—— 重试无意义，直接终止
        return self._finish(
            "escalated",
            f"模型接口返回不可重试的错误：{fault.reason}。"
            "请检查 API key、模型名与配额，而不是重试。",
            escalate=True,
        )

    # ------------------------------------------------------------------
    # 工具执行
    # ------------------------------------------------------------------
    def _execute_tool_calls(self, calls: List[ToolCall]) -> Optional[LoopResult]:
        """执行一批工具调用。

        书 §5 的「故障边界控制」：一个工具失败时，
        **只在同一批并行调用内传播，不上升到父级操作**。
        本实现按顺序执行（安全性优先），因此单点失败只影响该点，
        天然满足这条原则 —— 不会因为一个文件读不到就取消其他调用。
        """
        for i, call in enumerate(calls):
            if i >= self.cfg.max_tool_batches_per_turn:
                note = (f"[已跳过] 本次回复包含 {len(calls)} 个工具调用，"
                        f"超过单轮上限 {self.cfg.max_tool_batches_per_turn}。"
                        "请分几轮做，或一次只调少数几个。")
                self._append_tool_result("", note, call.name)
                break

            self._tool_calls += 1
            result = self.router.route(call)

            # ── 审批挂起 ──
            pending = self.router.pending
            if pending:
                cid = next(iter(pending))
                return self._finish(
                    "suspended",
                    f"等待审批：{pending[cid].result.reason}",
                    suspend_call_id=cid,
                )

            # ── 重复调用指纹检测 ──
            fp = self.loop_detector.observe(call, result)
            if fp:
                self.bus.emit(EventType.LOOP_DETECTED, tool=call.name,
                              fingerprint=call.fingerprint()[:80], reason=fp)
                return self._finish("escalated", fp, escalate=True)

            # ── 验证（只读结构化字段）──
            vctx = self.verifier.prepare(call.name, call.args, result)
            report = self.verifier.run(vctx)
            if not report.ok:
                self.bus.emit(EventType.CHECK, scope="tool", ok=False,
                              items=report.items)
                # 验证失败 → 纠正：把失败**作为结构化结果喂回模型**
                self._consecutive_failures += 1
                self._append_tool_result(
                    "", f"[验证未通过] " + "；".join(
                        it["detail"] for it in report.items if not it["ok"]),
                    call.name,
                )
            else:
                self._consecutive_failures = 0

            # ── 即时 lint（书 §5）──
            text = result.text
            if (self.cfg.lint_after_edit and result.ok
                    and result.files_changed and call.name == "apply_patch"):
                lints = quick_lint(result.files_changed, self.session.meta.workspace)
                note = lint_note(lints)
                if note:
                    text = text + note
                if any(not r["ok"] for r in lints):
                    # 语法错误是硬失败，计入连续失败
                    self._consecutive_failures += 1

            # ── 结果进轨迹 ──
            self._append_tool_result(
                result.meta.get("call_id", "") if result.meta else "",
                text if result.ok else self._format_error(call, result),
                call.name,
                ok=result.ok,
            )

        # ── 完整性：配对修补（书 §5）──
        self._ensure_pairing()
        return None

    def _append_tool_result(self, call_id: str, text: str, name: str,
                            ok: bool = True) -> None:
        m = Message.tool_result(call_id, text, name)
        m.meta["ok"] = ok
        # tool_call_id 在渲染时按目标厂商重新生成（书 §5），
        # 所以这里保持为空是**刻意**的 —— 中立格式不携带厂商标识。
        self.context.append(m)
        self.session.persist_message(m)
        # 这里**刻意不发 TOOL_OUTPUT 事件**：
        # 工具结果的广播已在 router.py 里完成（带 call_id / exit_code / 截断信息），
        # 事件总线是「一次执行 = 一条结果事件」。
        # 早期版本在这里又发了一条 `persisted=True` 的事件，造成同一次工具结果
        # 被广播两次 —— 前端因为没有 call_id 而无法合并，渲染成两张卡片，
        # 看起来像「执行了两次」。事件的唯一性也要靠纪律保证，不能靠自觉。

    def _format_error(self, call: ToolCall, result: ToolResult) -> str:
        """把工具错误**作为模型的输入**（书 §5）。

        书原话：
        > 工具层错误走另一条路：**不终止会话，把错误变成模型的输入**。
        > 幻觉调用会收到"工具不存在"的结构化错误结果；参数校验失败会收到
        > 附带输入约束提示的错误…… 这些错误以普通工具结果的身份进入上下文，
        > 由模型在下一轮自行纠正 —— 这正是"反馈越结构化越好"原则的应用。
        """
        head = f"[工具调用失败] {call.name}"
        if result.error_layer:
            head += f"（{result.error_layer} 层）"
        return head + "\n" + (result.text or "（无错误信息）")

    def _ensure_pairing(self) -> None:
        """配对完整性检查与自动修复（书 §5）。

        书原话：
        > 完整性监控则针对轨迹结构：发现工具调用缺少配对的结果消息时，
        > 系统会在注入上下文前自动修复配对关系，而不是把结构异常抛给模型或用户。

        修复方式是**追加**占位结果，绝不修改或删除已有消息（append-only 纪律）。
        """
        bad = self.context_trajectory_unpaired()
        if bad:
            self.bus.emit(
                EventType.ERROR,
                error_layer=ERR_CONTROL,
                reason=(f"轨迹结构损坏：{len(bad)} 处工具调用缺少配对结果，"
                        "已在注入上下文前自动补齐占位结果（产品模式做法）。"),
                unpaired_seqs=bad,
            )

    def context_trajectory_unpaired(self) -> List[str]:
        traj = self.context.trajectory()
        bad: List[str] = []
        for i, m in enumerate(traj):
            if m.role != "assistant" or not m.tool_calls:
                continue
            n = len(m.tool_calls)
            following = traj[i + 1: i + 1 + n]
            got = sum(1 for x in following if x.role == ROLE_TOOL)
            if got != n:
                # 就地补齐：追加占位结果，绝不修改已有消息（append-only）
                for k in range(n - got):
                    ph = Message.tool_result(
                        "", "[该工具调用没有返回结果，可能被中断]",
                        m.tool_calls[got + k].name)
                    ph.meta["placeholder"] = True
                    self.context.append(ph)
                    self.session.persist_message(ph)
                bad.append(str(m.seq))
        return bad

    # ------------------------------------------------------------------
    # 状态栏注入
    # ------------------------------------------------------------------
    def _inject_status(self) -> None:
        """注入环境状态栏（书 §2 的 Agent Status Bar / §5 的环境感知）。

        书 §5 明确要求这些信息**不应硬编码在静态系统提示词中** ——
        那样会破坏 KV Cache —— 而应作为动态的、**追加式**的状态栏实时生成。
        """
        ws = self.session.meta.workspace
        cwd = getattr(self.router, "shell_cwd", None) or ws
        status = {
            "工作目录": cwd,
            "迭代": f"{self._iterations}/{self.cfg.max_iterations}",
            "工具调用数": self._tool_calls,
            "沙箱": self.policy.sandbox.value,
            "审批": self.policy.approval.value,
        }
        # git 信息（存在才带）
        git = _git_status(ws)
        if git:
            status["git"] = git
        self.context.inject_status(status)
        # 状态栏消息也落盘（它是轨迹的一部分，重放时需要）
        if self.context.trajectory():
            self.session.persist_message(self.context.trajectory()[-1])

    # ------------------------------------------------------------------
    # 用量
    # ------------------------------------------------------------------
    def _accumulate_usage(self, reply: ModelReply) -> None:
        u = reply.usage or {}
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if k in u:
                self._usage[k] = self._usage.get(k, 0) + int(u[k])

    def _total_tokens(self) -> int:
        if "total_tokens" in self._usage:
            return int(self._usage["total_tokens"])
        # 无用量信息时按字符数粗估（约 3 字符/token），保证预算仍然生效
        approx = sum(len(m.content or "") for m in self.context.trajectory())
        return approx // 3

    # ------------------------------------------------------------------
    # 收尾
    # ------------------------------------------------------------------
    def _finish(self, status: str, reason: str, answer: str = "",
                escalate: bool = False, suspend_call_id: Optional[str] = None) -> LoopResult:
        goal_report = self.goal.report() if self.goal.conditions else None
        data: Dict[str, Any] = {
            "status": status,
            "iterations": self._iterations,
            "tool_calls": self._tool_calls,
            "reason": reason,
            "usage": self._usage,
        }
        if goal_report:
            data["goal"] = goal_report
            self.bus.emit(EventType.CHECK, scope="goal", **goal_report)
        if escalate:
            # 书 §5：暴露给用户时**必须附上已经尝试过的恢复动作**
            data["attempted_recoveries"] = self.corrector.attempted_actions()
            data["breakers"] = self.corrector.breakers_json()
        if suspend_call_id:
            data["suspend_call_id"] = suspend_call_id

        self.bus.emit(EventType.TURN_COMPLETED, **data)
        self.bus.assert_monotonic()

        return LoopResult(
            status=status, answer=answer, iterations=self._iterations,
            tool_calls=self._tool_calls, reason=reason,
            attempted_recoveries=self.corrector.attempted_actions(),
            goal=goal_report, usage=self._usage,
        )


# ══════════════════════════════════════════════════════════════════
# 辅助
# ══════════════════════════════════════════════════════════════════
def _git_status(ws: str) -> Optional[Dict[str, Any]]:
    """采集 git 状态（书 §5 要求状态栏包含分支、最近提交、待提交改动）。

    失败不影响主流程 —— 这不是每个工作区都有 git。
    """
    import subprocess
    try:
        def run(args):
            return subprocess.run(["git"] + args, cwd=ws, capture_output=True,
                                  text=True, timeout=5)
        br = run(["rev-parse", "--abbrev-ref", "HEAD"])
        if br.returncode != 0:
            return None
        st = run(["status", "--short"])
        lg = run(["log", "-1", "--pretty=%h %s"])
        return {
            "分支": br.stdout.strip(),
            "最近提交": lg.stdout.strip() if lg.returncode == 0 else "",
            "改动": [l for l in (st.stdout or "").splitlines() if l.strip()][:20],
        }
    except (OSError, subprocess.SubprocessError):
        return None


# ══════════════════════════════════════════════════════════════════
# 装配工厂
# ══════════════════════════════════════════════════════════════════
def build_default_registry(workspace: str):
    """装配默认工具集，并返回 (registry, shell_tool)。

    **注册顺序就是输出顺序** —— 书 §2 的错误模式之三要求工具定义顺序固定。
    这里的顺序刻意按「先读、再搜、后改、最后执行」的工作流排列，
    既符合使用习惯，也是稳定的字节序列。
    """
    from .tools.fs import register_fs_tools
    from .tools.search import register_search_tools
    from .tools.patch import register_patch_tools
    from .tools.shell import ShellTool, register_shell_tools
    from .tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_fs_tools(reg, workspace)
    register_search_tools(reg, workspace)
    register_patch_tools(reg, workspace)
    shell = ShellTool(workspace)
    register_shell_tools(reg, workspace, shell)
    return reg, shell
