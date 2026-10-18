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
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .capability import CapabilityLedger, LedgerBook, derive_ceiling
from .context import ContextBuilder, StaticPrefix
from .correct import Corrector, Fault, FaultClass, LoopDetector, Watchdog, classify
from .model import BaseModel, ModelReply
from .policy import ApprovalPolicy, Policy, SandboxMode
from .preflight import PreCompletionMiddleware, TraceFacts
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

    # ── 上下文压缩（arXiv 2608.22752：不压缩/乱压缩都会出事）──
    # 在 token 用量达到预算的这个比例时触发压缩，**而不是等爆掉**。
    # 为什么不等到 100%：书里与前沿都指出，模型在窗口填满**之前**
    # 回忆能力就开始下降（context rot），"接近上限才压缩"是错的时机。
    compact_at_ratio: float = 0.55
    # 压缩时保留最近多少条消息不参与压缩
    compact_keep_recent: int = 8
    # 两次压缩之间至少间隔多少轮（避免反复压缩同一条尾巴）
    compact_min_interval: int = 6
    # 最多压缩几次（每次压缩都有信息损失，必须有上限）
    max_compactions: int = 6

    # 即时 lint 开关（书 §5「即时语法反馈机制」）
    lint_after_edit: bool = True

    # ── 完成前检查（LangChain PreCompletionChecklistMiddleware，实测 +13.7pp）──
    # 在收尾前拦截，对照任务规格检查"要求的动作是否真的做过"。
    # 上限取 2：与 goal / intent 两类纠正一致 —— 两次足够补一次漏掉的动作，
    # 再多次说明模型确实做不到，继续问只会把任务拖成超时。
    preflight_enabled: bool = True
    preflight_max_rounds: int = 2

    # 是否在每轮推理前注入环境状态栏（书 §2 的 Agent Status Bar）。
    # 默认关闭以保持最纯粹的前缀稳定性，需要时显式打开 ——
    # 打开时它**追加到末尾**，不破坏前缀。
    inject_status: bool = True

    # ── 能力账本（G-5，CapScope）──
    # 从任务文本推导上限，对照观察每次调用，把越界记成结构化信号。
    # **只观察、不判定** —— 拒绝仍由 policy 负责。理由见 capability.py 开头的说明。
    # 默认开：它不改变任何执行结果，但一次都不开的话，
    # "注入尝试执行了多少次"这个问题在事后永远无法回答（缺历史）。
    capability_ledger: bool = True


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
        # 注入变更集记录器：让 router 在每次写操作前后留痕（可回滚）。
        # 由 loop 注入而不是 router 自己创建 —— 落盘位置属于 run 目录
        # （session 的地盘），router 不该知道 run 目录在哪。
        self.router.changesets = getattr(session, "changesets", None)
        # seq 提供者：把变更挂到事件序号上，支持"回滚到第 N 步"
        self.router.seq_provider = lambda: self.bus.seq
        self.verifier = Verifier(session.meta.workspace)
        self.corrector = Corrector(bus=self.bus)
        self.loop_detector = LoopDetector()
        self.watchdog = Watchdog(self.cfg.idle_timeout_s)
        self.goal = GoalVerifier(goal_conditions or [])

        # 能力账本（G-5，CapScope）：上限在 `run()` 的第一行从**用户任务文本**
        # 推导，这里先建登记处。刻意不在构造时推导 —— 因为上限必须在
        # **读到任何仓库内容之前**确定（CapScope 第 1 点），
        # 而构造函数运行在 append_user 之后、读工具之前，时点不对。
        self.ledgers = LedgerBook(session.meta.workspace)
        self.ledger: Optional[CapabilityLedger] = None

        self._consecutive_failures = 0
        self._iterations = 0
        self._tool_calls = 0
        self._usage: Dict[str, Any] = {}
        # 是否观察到过**原生** tool_calls。用来决定要不要在状态栏里提醒文本协议：
        # 端点支持原生调用时完全不提；不支持时每轮提醒（提醒是追加式的，不进前缀）。
        self._saw_native_tool_calls = False
        # "只描述意图、不动手"的累计次数。上限 2，避免把它变成另一种死循环
        # （书 §5：每条恢复路径都要有上限）。
        self._intent_retries = 0
        # 当前这批工具调用是否来自文本协议。工具结果要以对应形态回灌，
        # 否则上游会 400（tool calls / tool results 对不上）。
        self._current_batch_is_text = False
        # 上一条模型回复的工具调用是否来自文本协议（供 _execute_tool_calls 读取）
        self._last_reply_was_text_protocol = False
        # 上下文压缩计数（用于间隔与上限闸）
        self._compactions = 0
        self._last_compact_iter = -999
        # 目标未达成 / 完成前检查共用的纠正次数上限由 preflight 持有。
        # 两道闸门共享同一个额度，因为它们回灌的是**同一份**反馈 ——
        # 各自计上限会让总轮数翻倍。
        # 已经发过事件的目标条件（避免同一条重复刷屏）
        self._goal_announced: set = set()
        # 完成前检查（LangChain 实测 +13.7pp 的那一项）。
        # 它只在**收尾前**被问一次「真的可以结束了吗」。
        self.preflight = PreCompletionMiddleware(session.meta.workspace,
                                                max_rounds=self.cfg.preflight_max_rounds)
        # 结构化事实：只记工具名/参数/ok/退出码/落盘清单。
        # 不记模型文本 —— 记了就会有人忍不住去解析它。
        self.facts = TraceFacts()
        self._user_prompt = ""

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

        # 目标条件里如果有**无法自动判定**的，开场就说清楚。
        # 这一类既不满足也不阻断，但绝不能沉默 —— 沉默会让人以为
        # "没有报未达成" 等于 "已达成"，那正是书 §1 警告的"看上去正常的失败"。
        unmatched0 = self.goal.unmatched() if self.goal.conditions else []
        if unmatched0:
            self.bus.emit(EventType.CHECK, scope="goal", ok=False,
                          warning="unmatched_conditions",
                          reason=(f"{len(unmatched0)} 条目标条件里没有任何可识别的工具信号，"
                                  "**无法自动判定**，需要人工复核。"),
                          items=[{"text": c, "ok": False, "status": "unmatched"}
                                 for c in unmatched0])

        m = self.context.append_user(user_message)
        self.session.persist_message(m)
        # 记下任务文本：完成前检查要靠它判断"这类任务该做什么"。
        # 这是**用户输入**，不是模型输出 —— 可信度与系统提示同级。
        self._user_prompt = user_message
        self.facts = TraceFacts()

        # ── 能力上限：必须在**读到任何仓库内容之前**确定 ──
        # CapScope 第一步就是这个顺序要求。理由是因果而非性能：
        # 上限一旦晚推导，就有可能被已经读进来的不可信内容影响 ——
        # 而仓库内容正是间接注入的载体（"能命名一个资源，就足以对它动手"）。
        # 所以这一行放在 append_user 之后、第一次工具调用之前的唯一窗口里。
        if self.cfg.capability_ledger:
            ceiling = derive_ceiling(self._user_prompt, self.policy.sandbox,
                                     grantee="main")
            self.ledger = self.ledgers.open(ceiling)
            self.router.ledger = self.ledger
            self.bus.emit(EventType.CHECK, scope="capability", ok=True,
                          reason="已从任务文本推导本次会话的能力上限。",
                          ceiling=ceiling.to_json())
        else:
            self.ledger = None
            self.router.ledger = None

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

            # ── 上下文压缩（在状态栏之前做，让新状态栏落在压缩块之后）──
            self._maybe_compact()

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

            # 端点支持原生 function calling 时就不再提醒文本格式了。
            # 判据是**观察到的行为**（reply.tool_calls_were_text），不是配置猜测。
            if reply.tool_calls and not reply.tool_calls_were_text:
                self._saw_native_tool_calls = True

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
            # 标记"这批工具调用是用文本协议表达的"。
            # 回放时必须按原样还原（见 protocol.render_for）——
            # 伪装成原生 tool_calls 会让上游报
            # `tool calls and tool results do not match`。
            #
            # 判据用**模型实例的模式**而不是单条回复的标记：
            # 文本协议端点一旦进入该模式，整条会话都必须是文本形态，
            # 否则历史里会混着两种形态，上游一样会拒绝。
            force_text = bool(getattr(self.model, "text_protocol_only", False))
            if reply.tool_calls_were_text or force_text:
                am.meta["tool_calls_were_text"] = True
            self._last_reply_was_text_protocol = bool(
                reply.tool_calls_were_text or force_text)
            self.session.persist_message(am)

            # ── 没有工具调用 → 任务结束 ──
            if not reply.tool_calls:
                answer = reply.content or ""

                # **意图描述检测**（实测踩到的确切形态）：
                # 模型有时会写"我先读一下 README 看看结构"然后就此打住 ——
                # 既没有 tool_calls，也没有 <tool_call> 块。
                # 若不拦，循环会把它当成"最终回答"并在第一轮"成功完成"，
                # 事件流看起来毫无破绽。这是书 §1 警告的"看上去正常的失败"。
                #
                # 判据刻意保守：只在**尚未调用过任何工具**、且回答里出现
                # 第一人称的动作承诺时才拦一次。已经干过活的收尾语不该被拦。
                if self._looks_like_intent_only(answer, reply.reasoning):
                    self._intent_retries += 1
                    if self._intent_retries <= 2:
                        self.bus.emit(EventType.ERROR, error_layer=ERR_CONTROL,
                                      cls="intent_without_action",
                                      reason="模型描述了将要执行的动作，但没有实际调用工具。"
                                             "已把这一点作为反馈回灌，要求它真正调用。")
                        self._append_correction(
                            "[这不是工具结果，是一条纠正指令] 你上一条回复只是在**描述**"
                            "将要做什么，但没有实际发起工具调用，因此什么都没有发生。"
                            "如果你确实需要读取文件或执行操作，请立即发出工具调用；"
                            "如果任务已经完成，请直接给出最终结论并说明依据。",
                        )
                        continue
                    # 连续多次仍不调用 → 如实报告，不假装完成
                    return self._finish(
                        "escalated",
                        "模型连续多次只描述意图而不调用工具，任务无法推进。"
                        f"最后一条回复：{answer[:200]}",
                        escalate=True,
                    )

                # ── 收尾闸门：目标证据 + 完成前检查，合成一次的反馈 ──
                #
                # 书 §5：「给出了回答」不等于「完成了任务」。
                #
                # 这里刻意**不是**直接终止。早期版本一到这儿就收尾，结果是
                # 闸门只会"报告失败"而从不尝试补救 —— 那比没有闸门更坏：
                # 它把未完成固化成一条看起来严谨的失败理由，人反而更容易接受。
                #
                # 两道闸门（GoalVerifier 的目标缺口、PreCompletion 的动作缺口）
                # **合并成一条**纠正消息发给模型。分开发两条会让模型只回应其中一条，
                # 另一条被无声丢掉。
                gate_reason = self._run_completion_gate(answer)
                if gate_reason is not None:
                    if gate_reason == "__retry__":
                        continue
                    return self._finish("completed", gate_reason, answer=answer)

                return self._finish("completed", "任务完成。", answer=answer)

            # ── 执行工具（含审批闸门）──
            suspended = self._execute_tool_calls(reply.tool_calls)
            if suspended is not None:
                return suspended

        return self._finish("completed", "循环结束。")

    # ------------------------------------------------------------------
    # 收尾闸门
    # ------------------------------------------------------------------
    def _run_completion_gate(self, answer: str) -> Optional[str]:
        """任务即将结束时跑一次检查。

        返回值语义（用一个哨兵字符串区分"继续循环"与"收尾并说明原因"）：
          · `None`        → 全部通过，可以正常收尾
          · `"__retry__"` → 有缺口且还有纠正额度，已回灌，主循环应 continue
          · 其它字符串     → 额度用尽，以这个理由收尾（如实报告，不假装完成）

        这是 LangChain `PreCompletionChecklistMiddleware` 的落地
        （实测 +13.7pp，是投入产出比最高的一环）。
        """
        if not self.cfg.preflight_enabled:
            return None

        unmet = self.goal.unmet() if self.goal.conditions else []

        # 额度判断放在最前：用尽后不再回灌，直接如实报告。
        # 两道闸门共享同一个上限（`preflight.max_rounds`），
        # 因为它们回灌的是**同一份**反馈 —— 各自计上限会让总轮数翻倍。
        #
        # 这里**必须把 blocking 一起报出来**。早期实现只汇总了 warnings，
        # 于是两次拦下的硬缺口被静默丢弃，最终理由居然报"任务完成。" ——
        # 那正是本中间件要消灭的"假成功报告"，我自己犯了。
        if self.preflight.exhausted:
            rounds_used = self.preflight.rounds
            cp = self.preflight.check(self._user_prompt, self.facts, None)
            parts = []
            if unmet:
                parts.append("· 目标条件未满足：\n"
                             + "\n".join(f"    - {c}" for c in unmet))
            for f in cp.blocking:
                parts.append(f"· {f.detail}\n"
                             + ("    依据：" + "；".join(f.evidence) + "\n" if f.evidence else ""))
            for f in cp.warnings:
                parts.append(f"· （提醒）{f.detail}")
            if not parts:
                return None
            return ("已尝试纠正 " + str(rounds_used) + " 次仍未补齐，"
                    "以下在结束时检查出的缺口**未了结**：\n"
                    + "\n".join(parts)
                    + "\n回答内容如下，请自行判断是否可接受 —— "
                      "harness **不声称任务已完成**。")

        cp = self.preflight.check(self._user_prompt, self.facts, unmet)

        if cp.ok and not cp.warnings:
            # 干净通过 —— 这正是"判定器必须能在正确时放行"的那一面。
            # 一个永远报警的闸门会退化成噪音（GoalVerifier 曾经就是这样）。
            return None

        # 只有警告、没有硬缺口：不拦，但**如实记一条事件**让人看见。
        if cp.ok:
            self.bus.emit(EventType.CHECK, scope="preflight", ok=True,
                          warnings=[f.to_json() for f in cp.warnings],
                          reason="完成前检查通过，但有提醒项。")
            return None

        # 有硬缺口 → 回灌纠正
        self.preflight.note_round()
        codes = [f.code for f in cp.blocking]
        self.bus.emit(EventType.ERROR, error_layer=ERR_CONTROL,
                      cls="preflight_incomplete",
                      reason=f"完成前检查发现 {len(cp.blocking)} 项要求的动作没有发生，"
                             "已把缺口作为反馈回灌。",
                      findings=[f.to_json() for f in cp.blocking],
                      attempt=self.preflight.rounds)

        feedback = cp.feedback()
        if cp.warnings:
            feedback += ("\n\n（另有提醒，非阻断）\n"
                         + "\n".join(f"  · {f.detail}" for f in cp.warnings))
        self._append_correction(feedback)
        return "__retry__"

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
        # 记录本批调用的来源形态：工具结果要按对应形态回灌（见 _append_tool_result）。
        self._current_batch_is_text = self._last_reply_was_text_protocol

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

            # ── 记录结构化事实（完成前检查的输入）──
            # 只记结构：工具名、参数、ok、退出码、落盘清单、错误类型。
            # **不记任何模型文本** —— 这是 verify/preflight 共用的一条铁律。
            self.facts.executions.append({
                "tool": call.name,
                "args": dict(call.args or {}),
                "ok": bool(result.ok),
                "exit_code": result.exit_code,
                "files_changed": list(result.files_changed or []),
                "side_effect": bool(result.side_effect),
                "error_kind": (result.meta or {}).get("kind"),
            })

            # ── 目标证据绑定（确定性，只吃结构化字段）──
            # 位置刻意在验证通过之后：**失败的工具输出不构成证据**。
            # 这里只做匹配，不做判断 —— 见 verify.GoalVerifier 的说明。
            if self.goal.conditions:
                newly = self.goal.observe(call.name, call.args, result,
                                          seq=self.context.trajectory()[-1].seq
                                          if self.context.trajectory() else None)
                for cond in newly:
                    if cond in self._goal_announced:
                        continue
                    self._goal_announced.add(cond)
                    self.bus.emit(EventType.CHECK, scope="goal", ok=True,
                                  newly_met=cond,
                                  satisfied=self.goal.report()["satisfied"],
                                  total=self.goal.report()["total"],
                                  items=self.goal.report()["items"])

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
        # 文本协议下的工具结果必须以**普通用户消息**回灌（见 protocol.render_for）：
        # 上游根本没有原生 tool_call_id 可以对上，硬塞 role=tool 会被 400 拒绝。
        if self._current_batch_is_text:
            m.meta["text_protocol_result"] = True
            m.meta["tool_name"] = name
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

    def _append_correction(self, text: str) -> None:
        """回灌一条**harness 自己的**纠正指令。

        ## 为什么不能复用 `_append_tool_result`

        实测踩到：目标未达成时，模型上一条回复是"空口说完成了"，
        **里面根本没有 tool_calls**。此时若追加一条 role=tool 的消息，
        历史里就会出现一条孤立的工具结果 —— 上游立刻 400：
        `tool calls and tool results do not match, please start a new
        conversation and retry`。

        这不是配额或模型名的问题，是轨迹结构被我们搞坏了。
        工具结果只能紧跟**它自己那条** tool_calls；harness 的纠正
        不属于任何一次工具调用，所以必须走普通用户消息。

        这个 bug 的精妙之处在于它**第一轮不炸**：崩溃点落在第二次请求上，
        而且报的是 400（被归类为"不可重试"），看起来像配置错误。
        """
        m = Message.user(text)
        m.meta["kind"] = "correction"
        m.meta["from"] = "harness"
        self.context.append(m)
        self.session.persist_message(m)

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

        # ── 工具调用格式提醒（仅在需要时注入）──
        # 背景：有的 OpenAI 兼容端点完全不实现原生 function calling
        # （实测 deepseek-v4.1-flash：tool_calls 恒为 None，即使 tool_choice=required）。
        # 静态系统提示词里虽然写了格式，但实测**模型并不照做** ——
        # 它会在思考里写"我打算读取 README"然后就此打住，任务第一轮就"成功完成"。
        #
        # 决定性实验发现：**只要在对话里给出一个具体的格式示例，模型就会照做。**
        # 所以这里把示例作为**追加式**提醒注入（而不是改静态前缀，那会破坏 KV-cache）。
        # 只在确实需要时注入：模型已经能输出结构化 tool_calls 时完全不提，
        # 免得白白占用上下文。
        if self._needs_text_protocol():
            status["工具调用格式"] = (
                "你的接口不支持原生 function calling。"
                "调用工具必须写成下面这个格式（正文里直接写，不要只描述意图）：\n"
                '<tool_call>{"name": "工具名", "arguments": {"参数": "值"}}</tool_call>\n'
                "示例：要读 README.md 就写 "
                '<tool_call>{"name": "read_file", "arguments": {"path": "README.md"}}</tool_call>'
            )

        self.context.inject_status(status)
        # 状态栏消息也落盘（它是轨迹的一部分，重放时需要）
        if self.context.trajectory():
            self.session.persist_message(self.context.trajectory()[-1])

    def _maybe_compact(self) -> None:
        """在合适的时机压缩上下文。

        这是之前**完全缺失**的一环：`context.compact()` 早就写好了，
        但从来没有调用点 —— 长会话根本不压缩，超过窗口后行为未定义。

        触发条件是**用量比例**而不是"等爆"：前沿共识是模型在窗口填满
        **之前**回忆能力就开始下降（context rot），等到 100% 才压缩已经太晚。

        三道闸（任何一道不满足就不压）：
          1. 用量占比 ≥ `compact_at_ratio`
          2. 距上次压缩 ≥ `compact_min_interval` 轮（避免反复压同一条尾巴）
          3. 累计压缩次数 < `max_compactions`（每次都有信息损失，必须有上限）

        摘要器刻意**不调模型**：治理层不与产出层共享上下文（书 §5），
        而且压缩发生在循环关键路径上，一次额外的模型往返会显著拖慢。
        `retention.build_summary_text` 提供了确定性的结构化摘要，
        它已经区分了硬约束 / 环境事实 / 模型推断。
        """
        if self.cfg.max_compactions <= 0:
            return
        if self._compactions >= self.cfg.max_compactions:
            return
        if self._iterations - self._last_compact_iter < self.cfg.compact_min_interval:
            return

        used = self._total_tokens()
        ratio = used / max(1, self.cfg.max_tokens_budget)
        if ratio < self.cfg.compact_at_ratio:
            return

        # 摘要器：用保留模块里的确定性摘要（不调模型）
        def _summarizer(messages):
            from .retention import _fallback_summary
            return _fallback_summary(messages, max_chars=240)

        block = self.context.compact(
            _summarizer, keep_recent=self.cfg.compact_keep_recent)
        if block is None:
            return

        self._compactions += 1
        self._last_compact_iter = self._iterations
        # 落盘 + 发事件：压缩必须是**可观察**的，否则"东西什么时候没的"无从追查
        self.session.persist_message(block)
        self.bus.emit(EventType.COMPACTED,
                      covers=block.meta.get("covers_seq"),
                      plan=block.meta.get("plan"),
                      audit=block.meta.get("audit"),
                      usage_ratio=round(ratio, 3),
                      compaction_no=self._compactions)

    def _needs_text_protocol(self) -> bool:
        """模型是否需要用文本协议表达工具调用。

        判据来自**观察**而不是配置猜测：只要模型此前已经产出过原生 tool_calls，
        就说明它支持，此后不再提醒；否则持续提醒（每轮都要，
        因为提醒是在末尾追加、不进前缀，不会破坏缓存）。
        """
        return not self._saw_native_tool_calls

    def _looks_like_intent_only(self, answer: str, reasoning: Optional[str]) -> bool:
        """判断这条回复是不是"只在描述意图、没有真的动手"。

        实测形态：模型写 "Let me start by reading README.md and exploring
        the workspace." 或 "我先读一下 README" 然后就此结束。

        判据**刻意保守**，宁可漏判也不误判 —— 误判会把正常的收尾回答
        当成未完成，凭空多跑两轮；漏判只是维持现有的（较差的）行为。
        所以要求同时满足：
          1. 本次会话**还没有成功调用过任何工具**（已经干过活的收尾语不该被拦）
          2. 文本里出现「第一人称 + 动作承诺」的表述
          3. 文本很短（真的收尾回答通常有实质结论，不会只是一句承诺）
        """
        if self._tool_calls > 0:
            return False
        text = ((answer or "") + " " + (reasoning or "")).strip()
        if not text or len(text) > 240:
            return False
        patterns = (
            # 中文
            r"(我|让我|我先|首先)\s*(先|去|来|将|会|要)?\s*(读|看|查看|检查|列|搜索|搜|找|运行|执行|创建|写|改|修|实现)",
            r"接下来(我|将|会)",
            # 英文
            r"\b(let me|i'?ll|i will|i'?m going to|first,? i)\b.*\b(read|look|check|list|search|find|run|create|write|implement|explore|start)\b",
        )
        return any(re.search(p, text, re.IGNORECASE) for p in patterns)

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
        # 能力账本随结束事件一起给出。为什么放在这里而不是让人去查：
        # "这次会话里，有没有出现过任务之外的能力"是审计第一眼要看的东西，
        # 藏在需要额外调用的地方就等于没有。
        # 落盘成独立文件也是为了**不依赖进程还活着**（与哈希链同理）。
        if self.ledger is not None:
            cap_report = self.ledger.report()
            data["capability"] = cap_report["summary"]
            try:
                self.session.write_capability(cap_report)
            except Exception as e:
                # 落盘失败不终止循环（书 §5：错误路径上禁用副作用逻辑），
                # 但要计数 —— 它是一次真实的数据丢失风险。
                self.session.note_persist_failure(f"capability: {e}")
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
