"""纠正层：故障分类 → 检测 → 分级恢复 → 熔断终止。

书 §5「故障与错误恢复」是全书最能拉开工程差距的一节，本模块逐条落地。

## 四层故障分类

    API 层    限流 429 / 过载 / 超时 / 连接中断 / 输出触顶
    工具层    幻觉调用 / 参数畸形 / 执行抛异常 /
              **最危险的一种：工具反复返回同一个错误，而模型不加改变地反复重试**
    上下文层  窗口溢出 / 压缩失败 / 轨迹结构损坏（工具调用缺配对结果）
    控制流层  死循环（反复执行相同操作却毫无进展）/
              **死亡螺旋**（恢复逻辑自身又调 LLM、再次出错、连锁反应）

## 检测：**先分类，再计数**

书里的原话值得直接引用：

> 捕获故障后的第一个判断不是"要不要重试"，而是**"值不值得重试"**。
> 可重试的错误（限流、过载、网络抖动）重试才有意义；
> 不可重试的错误（参数不合法、权限不足、工具不存在）原样重试多少次都是同样的结果，
> **必须改变输入或策略**。

所以 `classify()` 返回的是「可重试性」，而不是一个笼统的 True。

## 分级恢复：逐级透明

    1. 静默重试          —— 指数退避 + 随机抖动；区分前台/后台
    2. 降级与接续        —— 改变请求本身再试
    3. 暴露给用户        —— 所有自动手段用尽，**并附上已尝试过的恢复动作**

## 终止：每条恢复路径都要有上限

书给了真实阈值来源：Claude Code 的压缩熔断是「连续 3 次」，
因为实测出现过单会话连续失败三千余次、逾千会话 50 次以上连续失败，
3 次正是「绝大多数故障已恢复」与「继续重试基本无望」的拐点。
本模块用同样的 3 作为默认，但把阈值做成参数，理由写在注释里。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence

from .protocol import (
    ERR_API, ERR_CONTEXT, ERR_CONTROL, ERR_TOOL, ToolCall, ToolResult,
)


# ══════════════════════════════════════════════════════════════════
# 分类
# ══════════════════════════════════════════════════════════════════
RETRYABLE_BY_LAYER = {
    ERR_API: True,        # 限流/过载/抖动 —— 原样重试有意义
    ERR_TOOL: False,      # 参数不合法/权限不足/工具不存在 —— 重试无用，必须改输入
    ERR_CONTEXT: False,   # 窗口溢出/结构损坏 —— 需要先修上下文
    ERR_CONTROL: False,   # 死循环/死亡螺旋 —— 重试只会更深
}

# 值得重试的 API 层特征（书 §5：可重试 vs 不可重试）
RETRYABLE_API_MARKERS = [
    r"\b429\b", r"rate.?limit", r"too many requests",
    r"\b50[0234]\b", r"overloaded", r"temporarily unavailable",
    r"timeout", r"timed out", r"connection reset", r"connection aborted",
    r"econnreset", r"econnrefused", r"socket hang up", r"broken pipe",
]
# 明确**不值得**重试的（覆盖上面的宽松匹配）
NON_RETRYABLE_MARKERS = [
    r"\b400\b", r"\b401\b", r"\b403\b", r"\b404\b", r"invalid_request",
    r"authentication", r"permission denied", r"not found",
    r"context.?length.?exceeded", r"context_length_exceeded",
]


class FaultClass(str, Enum):
    """一条故障的最终归类。"""

    RETRYABLE = "retryable"
    NEEDS_INPUT_CHANGE = "needs_input_change"     # 改参数/换工具
    NEEDS_CONTEXT_FIX = "needs_context_fix"
    CONTROL_FLOW = "control_flow"
    FATAL = "fatal"


@dataclass
class Fault:
    layer: str
    cls: FaultClass
    reason: str
    retry_after_s: Optional[float] = None
    detail: str = ""


def classify(result: ToolResult) -> Optional[Fault]:
    """把一次工具结果归类。

    第一判断是「值不值得重试」，而不是「要不要重试」。

    ## 分层必须在"显式声明"之前

    实测踩到的坑：早期实现先看 `result.retryable is False` 就直接返回
    「工具声明为不可重试」，**不管它其实是 API 层的错误**。
    于是模型接口返回的 400 被渲染成"工具声明…"，最终上报时又说
    "请检查 API key、模型名与配额" —— 两句话互相矛盾，
    而真正该做的是换模型或改请求。**指错方向比不给提示更糟。**

    所以顺序改成：先判层（API / 上下文 / 控制流 / 工具），
    层内再看显式声明。`retryable` 是工具的自我声明，
    它只在**工具层**有权威性；API 层该由 `model.py` 的判据说了算。
    """
    if result.ok:
        return None

    layer = result.error_layer or ERR_TOOL
    text = (result.text or "").lower()

    # ── API 层：由本模块的标记表判定，不采信"工具声明" ──
    # 为什么：`retryable` 这个字段的语义是"这个工具调用值不值得重试"，
    # 但 API 层失败的原因（限流 / 鉴权 / 请求不合法）与工具无关，
    # 套用工具的自我声明会给出错误的方向。
    if layer == ERR_API:
        for pat in NON_RETRYABLE_MARKERS:
            if re.search(pat, text):
                return Fault(layer, FaultClass.NEEDS_INPUT_CHANGE,
                             f"模型接口错误命中不可重试特征（{pat}）："
                             "需要改配置或改请求，重试无意义",
                             detail=result.text[:400])
        for pat in RETRYABLE_API_MARKERS:
            if re.search(pat, text):
                ra = _parse_retry_after(result.text or "")
                return Fault(layer, FaultClass.RETRYABLE,
                             f"模型接口错误命中可重试特征（{pat}）",
                             retry_after_s=ra, detail=result.text[:400])
        if result.retryable is False:
            return Fault(layer, FaultClass.NEEDS_INPUT_CHANGE,
                         "模型接口返回不可重试的错误：需要改配置或改请求，重试无意义",
                         detail=result.text[:400])
        # 未识别 → 保守视为可重试一次（由熔断兜底）
        return Fault(layer, FaultClass.RETRYABLE, "API 层错误未识别，按可重试处理（熔断兜底）",
                     detail=result.text[:400])

    if layer == ERR_CONTEXT:
        return Fault(layer, FaultClass.NEEDS_CONTEXT_FIX, "上下文层故障，需先修上下文",
                     detail=result.text[:400])

    if layer == ERR_CONTROL:
        return Fault(layer, FaultClass.CONTROL_FLOW, "控制流层故障", detail=result.text[:400])

    # ── 工具层：这时"显式声明"才有权威性 ──
    if result.retryable is True:
        return Fault(layer, FaultClass.RETRYABLE, "工具声明为可重试", detail=result.text[:400])
    if result.retryable is False:
        return Fault(layer, FaultClass.NEEDS_INPUT_CHANGE,
                     "工具声明为不可重试，需改变输入或换工具", detail=result.text[:400])

    # 工具层：默认不可重试 —— 书 §5 明确「原样重试多少次都是同样的结果」
    return Fault(layer, FaultClass.NEEDS_INPUT_CHANGE,
                 "工具层错误，原样重试无意义，需改变输入或换工具", detail=result.text[:400])


def _parse_retry_after(text: str) -> Optional[float]:
    """从错误里提取服务端建议的等待时长（书 §5：尊重服务端的等待时长提示）。"""
    m = re.search(r"retry[- _]?after[:\s]+(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"try again in (\d+(?:\.\d+)?)\s*(?:s|sec|seconds)", text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


# ══════════════════════════════════════════════════════════════════
# 检测：重复调用指纹
# ══════════════════════════════════════════════════════════════════
class LoopDetector:
    """重复调用指纹检测。

    书 §5：
    > 对"工具名 + 参数"计算指纹，相同指纹反复出现就是无进展循环的明确信号 ——
    > 第一章消融实验中 Agent 反复调用同一工具，正是这种模式。

    实现要点：**被检测的是「相同的调用 + 相同的结果」**。
    如果同一调用返回了不同结果（比如重试后成功了），那不算无进展 ——
    所以指纹里包含结果摘要。
    """

    def __init__(self, threshold: int = 3) -> None:
        # 阈值 3 与书里压缩熔断的阈值同源：绝大多数故障在此之前已恢复。
        self.threshold = threshold
        self._counts: Dict[str, int] = {}
        self._last: Dict[str, str] = {}

    def observe(self, call: ToolCall, result: ToolResult) -> Optional[str]:
        """返回命中的指纹说明；未命中返回 None。"""
        call_fp = call.fingerprint()
        # 结果摘要用「ok + 前 200 字符」，避免把长输出塞进指纹
        res_sig = hashlib.sha1(
            f"{result.ok}|{(result.text or '')[:200]}".encode("utf-8")
        ).hexdigest()[:12]
        fp = f"{call_fp}→{res_sig}"

        self._counts[fp] = self._counts.get(fp, 0) + 1
        self._last[fp] = result.text
        if self._counts[fp] >= self.threshold:
            return (f"检测到无进展循环：同样的调用 `{call.name}` 与同样的结果重复了 "
                    f"{self._counts[fp]} 次。继续重试不会改变结果。")
        return None

    def reset(self, call: Optional[ToolCall] = None) -> None:
        if call is None:
            self._counts.clear()
            self._last.clear()
            return
        prefix = call.name + ":"
        for k in [k for k in self._counts if k.startswith(prefix)]:
            self._counts.pop(k, None)
            self._last.pop(k, None)


# ══════════════════════════════════════════════════════════════════
# 熔断
# ══════════════════════════════════════════════════════════════════
@dataclass
class BreakerState:
    consecutive: int = 0
    open: bool = False
    opened_at: Optional[float] = None
    opened_count: int = 0
    last_reason: str = ""


class CircuitBreaker:
    """每条恢复路径一个熔断器。

    书 §5：
    > 恢复机制本身也可能失效，因此每条恢复路径都必须有明确的熔断上限：
    > 上下文压缩连续失败若干次就放弃压缩，权限分类连续失败就回退到人工询问，
    > 输出接续最多尝试固定轮数。**阈值从哪来？答案是生产数据而非拍脑袋。**

    默认 3 抄自 Claude Code 的压缩熔断（书里有详细的数据支撑）。
    """

    def __init__(self, name: str, limit: int = 3) -> None:
        self.name = name
        self.limit = limit
        self.state = BreakerState()

    def record_failure(self, reason: str = "") -> bool:
        """记一次失败。返回熔断器是否**刚刚**打开。"""
        s = self.state
        s.consecutive += 1
        s.last_reason = reason
        if not s.open and s.consecutive >= self.limit:
            s.open = True
            s.opened_at = time.time()
            s.opened_count += 1
            return True
        return False

    def record_success(self) -> None:
        """成功一次就复位 —— 书里的熔断是**连续**失败计数，不是累计。"""
        self.state.consecutive = 0
        self.state.open = False
        self.state.opened_at = None

    @property
    def is_open(self) -> bool:
        return self.state.open

    def to_json(self) -> Dict[str, Any]:
        return {"name": self.name, "limit": self.limit,
                "consecutive": self.state.consecutive,
                "open": self.state.open,
                "opened_count": self.state.opened_count,
                "reason": self.state.last_reason}


# ══════════════════════════════════════════════════════════════════
# 空闲看门狗
# ══════════════════════════════════════════════════════════════════
class Watchdog:
    """空闲看门狗。

    书 §5：
    > 流式连接最危险的失败模式不是断开（这会立即报错），而是**静默卡死** ——
    > 连接建立成功但数据流停止，像水管通着但不出水；
    > SDK 的超时机制往往只覆盖初始连接而非传输过程。
    > 可推广为一条原则：**每个长连接都需要活性信号，而非仅依赖连接超时。**

    因此本类检测的是「**多久没有新事件**」，而不是「请求跑了多久」。
    """

    def __init__(self, idle_timeout_s: float = 90.0) -> None:
        self.idle_timeout_s = idle_timeout_s
        self._last_beat = time.time()

    def beat(self) -> None:
        """收到任何活性信号（新事件、流式增量）时调用。"""
        self._last_beat = time.time()

    def idle_seconds(self) -> float:
        return time.time() - self._last_beat

    def is_stalled(self) -> bool:
        return self.idle_seconds() > self.idle_timeout_s

    def to_json(self) -> Dict[str, Any]:
        return {"idle_s": round(self.idle_seconds(), 2),
                "timeout_s": self.idle_timeout_s,
                "stalled": self.is_stalled()}


# ══════════════════════════════════════════════════════════════════
# 死亡螺旋防护
# ══════════════════════════════════════════════════════════════════
class Corrector:
    """纠正器：分级恢复 + 熔断 + 死亡螺旋防护。

    书 §5 关于死亡螺旋的原文：
    > 比单点熔断更隐蔽的是**死亡螺旋**：错误处理路径中的逻辑本身又调用 LLM，
    > 再次出错，引发连锁反应。一种真实发生过的连锁故障是：
    > Agent 因上下文溢出而停止，触发"结束时自动提交代码"的停止钩子，
    > 钩子调用 LLM 生成 commit message，再次发生上下文溢出，又一次触发钩子。
    > 防护靠两条：**在错误路径上禁用一切会再次调用模型的副作用逻辑**
    > （宁可丢掉一次辅助功能），以及**用递归深度计数器检测并打断残余的连锁**。
    """

    MAX_RECOVERY_DEPTH = 4          # 递归深度上限
    BASE_BACKOFF_S = 0.5
    MAX_BACKOFF_S = 8.0
    JITTER_RATIO = 0.25

    def __init__(self, bus=None) -> None:
        self.bus = bus
        self._recovery_depth = 0
        self._breakers: Dict[str, CircuitBreaker] = {
            # 与书里点名"必须有熔断"的三条恢复路径一一对应
            "retry": CircuitBreaker("retry", 3),
            "compact": CircuitBreaker("compact", 3),
            "continue": CircuitBreaker("continue", 3),
        }
        self._attempted: List[str] = []      # 已尝试的恢复动作（最终要如实告知用户）
        self._on_error_path = False          # 错误路径标记

    # ---------- 深度 ----------
    def enter_recovery(self) -> None:
        self._recovery_depth += 1

    def exit_recovery(self) -> None:
        self._recovery_depth = max(0, self._recovery_depth - 1)

    def depth_exceeded(self) -> bool:
        """递归深度超限 —— 打断残余的连锁。"""
        return self._recovery_depth > self.MAX_RECOVERY_DEPTH

    # ---------- 错误路径上的副作用禁令 ----------
    def safe_on_error_path(self) -> bool:
        """错误路径上是否允许执行「会再次调用模型」的副作用逻辑。

        书的要求是**禁用**。这里返回 False，调用方据此跳过
        （例如自动生成 commit message、自动提取记忆这类辅助功能）。
        """
        return not self._on_error_path

    def mark_error_path(self, on: bool = True) -> None:
        self._on_error_path = on

    # ---------- 退避 ----------
    def backoff(self, attempt: int, retry_after_s: Optional[float] = None) -> float:
        """指数退避 + 随机抖动。

        书 §5：
        > 指数退避叠加随机抖动，**避免大量客户端同步重试造成二次拥塞**，
        > 并尊重服务端返回的等待时长提示。

        抖动作用在「已算出的延迟」上而不是 attempt 上 ——
        否则两个并发会话会同步重试（这条与前端 reconnect.ts 的设计一致）。
        """
        if retry_after_s is not None:
            return max(0.0, retry_after_s)
        cap = min(self.BASE_BACKOFF_S * (2 ** max(0, attempt)), self.MAX_BACKOFF_S)
        jit = cap * self.JITTER_RATIO
        return cap - jit + random.random() * 2 * jit

    # ---------- 决策 ----------
    def plan(self, fault: Fault, attempt: int, foreground: bool = True) -> Dict[str, Any]:
        """给出恢复方案。

        分级升级：能用低级别解决就不升级（书 §5）。
        后台调用直接放弃 —— 否则"重试放大"会挤占主链路配额。
        """
        if not foreground:
            return {"action": "drop",
                    "reason": "后台调用失败直接放弃，避免重试放大挤占主链路的配额。",
                    "transparency": 0}

        if fault.cls is FaultClass.RETRYABLE:
            b = self._breakers["retry"]
            if b.is_open:
                return {"action": "escalate",
                        "reason": f"重试熔断已打开（连续 {b.limit} 次失败）：{b.state.last_reason}",
                        "transparency": 2}
            delay = self.backoff(attempt, fault.retry_after_s)
            self._attempted.append(f"静默重试第 {attempt + 1} 次（退避 {delay:.1f}s）")
            return {"action": "retry", "delay_s": delay,
                    "reason": f"可重试故障：{fault.reason}", "transparency": 1}

        if fault.cls is FaultClass.NEEDS_INPUT_CHANGE:
            # 书 §5：工具层错误**不终止会话，把错误变成模型的输入**
            return {"action": "feed_back",
                    "reason": f"{fault.reason}。错误已作为结构化结果回传模型，由它自行纠正。",
                    "transparency": 1}

        if fault.cls is FaultClass.NEEDS_CONTEXT_FIX:
            b = self._breakers["compact"]
            if b.is_open:
                return {"action": "escalate",
                        "reason": f"压缩熔断已打开（连续 {b.limit} 次失败）：{b.state.last_reason}",
                        "transparency": 2}
            self._attempted.append("尝试压缩上下文")
            return {"action": "compact", "reason": fault.reason, "transparency": 1}

        if fault.cls is FaultClass.CONTROL_FLOW:
            return {"action": "terminate",
                    "reason": f"控制流故障：{fault.reason}。继续只会更深，直接终止并上报。",
                    "transparency": 2}

        return {"action": "escalate", "reason": fault.reason, "transparency": 2}

    def note_failure(self, path: str, reason: str) -> bool:
        b = self._breakers.get(path)
        if b is None:
            return False
        opened = b.record_failure(reason)
        if opened and self.bus is not None:
            from .protocol import EventType
            self.bus.emit(EventType.CIRCUIT_OPEN, **b.to_json())
        return opened

    def note_success(self, path: str) -> None:
        b = self._breakers.get(path)
        if b is not None:
            b.record_success()

    # ---------- 用户可见的恢复记录 ----------
    def attempted_actions(self) -> List[str]:
        """书 §5：暴露给用户时**必须附上已经尝试过的恢复动作**。"""
        return list(self._attempted)

    def breakers_json(self) -> List[Dict[str, Any]]:
        return [b.to_json() for b in self._breakers.values()]
