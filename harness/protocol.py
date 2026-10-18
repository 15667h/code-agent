"""中立轨迹格式与事件协议。

两条纪律来自《深入理解 AI Agent》第五章「接管」一节：

  1. **轨迹不该按任何一家的接口格式存储，而应保存为一份中立格式。**
     每段思考拆成「可移植的文字」与「不可移植的凭证」两部分；工具调用只记录
     **名称与参数**，标识符在渲染成具体请求时按目标厂商重新生成。
     理由：跨厂商接管能带走的是文字，带不走的是凭证。

  2. **事件应区分类型，而不是把所有内容混成字符串。**
     事件流是 CLI / SDK / Web 多入口共享语义的关键。

另一条来自第二章的纪律体现在 ContextBuilder（见 context.py）：
**只增不改（append-only）** —— 前缀一旦确定就不动，动态信息一律追加到末尾。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional


# ══════════════════════════════════════════════════════════════════
# 角色
# ══════════════════════════════════════════════════════════════════
ROLE_SYSTEM = "system"
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"

VALID_ROLES = (ROLE_SYSTEM, ROLE_USER, ROLE_ASSISTANT, ROLE_TOOL)


# ══════════════════════════════════════════════════════════════════
# 中立消息
# ══════════════════════════════════════════════════════════════════
@dataclass
class ToolCall:
    """工具调用：**只记录名称与参数**。

    `call_id` 刻意不放进数据结构 —— 它是「渲染成具体请求时按目标厂商重新生成」的字段。
    见 render_for() 的说明。
    """

    name: str
    args: Dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        """「工具名 + 参数」的稳定指纹。

        用于检测**无进展循环**（书 §5「重复调用指纹」）：
        相同指纹反复出现，就是模型在原地打转的明确信号。

        参数按 key 排序后再序列化，保证 {"a":1,"b":2} 与 {"b":2,"a":1} 指纹相同。
        """
        norm = json.dumps(self.args, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return f"{self.name}:{norm}"

    def to_json(self) -> Dict[str, Any]:
        return {"name": self.name, "args": self.args}

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "ToolCall":
        return cls(name=d.get("name", ""), args=d.get("args") or {})


@dataclass
class Message:
    """中立轨迹中的一条消息。

    字段划分严格遵守书 §5 的「可移植 / 不可移植」二分：

    ┌─ 可移植（换厂商仍有效）───────────────┐
    │ content          正文文字             │
    │ reasoning        思考的**文字**部分    │
    │ tool_calls       名称 + 参数           │
    └───────────────────────────────────────┘
    ┌─ 不可移植（换厂商即失效，切换时丢弃）──┐
    │ reasoning_signature  厂商签发的凭证     │
    │ tool_call_id         厂商生成的调用标识 │
    └───────────────────────────────────────┘

    `seq` 是追加序号。它的作用不是排序（列表本身有序），而是**完整性校验**：
    书 §5 要求「发现工具调用缺少配对的结果消息时，在注入上下文前自动修复配对」，
    配对检查就用 seq 与 role 做。

    `origin_model` 记录产出这条消息的模型名。它的意义在于**降级时剥离旧模型私有格式块**
    （书 §5：主模型过载降级到备用模型时，需先剥离旧模型私有的格式块，否则新模型无法解析）。
    """

    role: str
    content: str = ""
    reasoning: Optional[str] = None
    reasoning_signature: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    seq: int = 0
    origin_model: Optional[str] = None
    # 仅供 UI 展示的元信息，不参与模型请求
    meta: Dict[str, Any] = field(default_factory=dict)

    # ---------- 构造便捷方法 ----------
    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(role=ROLE_SYSTEM, content=content)

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role=ROLE_USER, content=content)

    @classmethod
    def assistant(cls, content: str = "", reasoning: Optional[str] = None,
                  tool_calls: Optional[List[ToolCall]] = None,
                  signature: Optional[str] = None,
                  origin_model: Optional[str] = None) -> "Message":
        return cls(role=ROLE_ASSISTANT, content=content, reasoning=reasoning,
                   tool_calls=tool_calls or [], reasoning_signature=signature,
                   origin_model=origin_model)

    @classmethod
    def tool_result(cls, call_id: str, content: str, name: str = "") -> "Message":
        return cls(role=ROLE_TOOL, content=content, tool_call_id=call_id,
                   meta={"name": name} if name else {})

    # ---------- 移植 ----------
    def portabilize(self) -> "Message":
        """丢凭证、保文字 —— 对应书 §5 的「中立」做法。

        切换厂商时：
          · reasoning_signature 一律丢弃（凭证换一家就失效）
          · tool_call_id 置空（渲染时按目标厂商重新生成）
          · reasoning 文字保留，但**以普通内容的身份带入**，
            而不是塞回目标厂商放思考的位置
        """
        m = self.clone()
        m.reasoning_signature = None
        m.tool_call_id = None
        m.origin_model = None
        return m

    def clone(self) -> "Message":
        return Message(
            role=self.role,
            content=self.content,
            reasoning=self.reasoning,
            reasoning_signature=self.reasoning_signature,
            tool_calls=[ToolCall(c.name, dict(c.args)) for c in self.tool_calls],
            tool_call_id=self.tool_call_id,
            seq=self.seq,
            origin_model=self.origin_model,
            meta=dict(self.meta),
        )

    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    # ---------- 序列化（中立格式，落盘用） ----------
    def to_json(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"role": self.role, "seq": self.seq}
        if self.content:
            d["content"] = self.content
        if self.reasoning:
            d["reasoning"] = self.reasoning
        if self.reasoning_signature:
            d["reasoning_signature"] = self.reasoning_signature
        if self.tool_calls:
            d["tool_calls"] = [c.to_json() for c in self.tool_calls]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.origin_model:
            d["origin_model"] = self.origin_model
        if self.meta:
            d["meta"] = self.meta
        return d

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "Message":
        return cls(
            role=d.get("role", ROLE_USER),
            content=d.get("content", ""),
            reasoning=d.get("reasoning"),
            reasoning_signature=d.get("reasoning_signature"),
            tool_calls=[ToolCall.from_json(c) for c in d.get("tool_calls") or []],
            tool_call_id=d.get("tool_call_id"),
            seq=d.get("seq", 0),
            origin_model=d.get("origin_model"),
            meta=d.get("meta") or {},
        )

    # ---------- 渲染成具体厂商请求 ----------
    def render_for(self, provider: str, call_id_factory=None) -> Dict[str, Any]:
        """把中立消息渲染成某厂商的请求形状。

        `call_id_factory` 由调用方提供，用来**重新生成** tool_call_id ——
        这正是书 §5 强调的「标识符在渲染成具体请求时按目标厂商重新生成」。
        默认工厂用 call_<uuid4 前 8 位>。
        """
        if call_id_factory is None:
            call_id_factory = _default_call_id

        out: Dict[str, Any] = {"role": self.role}

        if self.role == ROLE_ASSISTANT:
            # 思考文字以普通内容的身份带入（书 §5 的「中立」做法），
            # 而不是塞回目标厂商放思考的位置。
            text = self.content
            if self.reasoning and not text:
                text = self.reasoning
            out["content"] = text
            if self.tool_calls:
                out["tool_calls"] = [
                    {
                        "id": call_id_factory(),
                        "type": "function",
                        "function": {"name": c.name,
                                     "arguments": json.dumps(c.args, ensure_ascii=False)},
                    }
                    for c in self.tool_calls
                ]
            return out

        if self.role == ROLE_TOOL:
            out["tool_call_id"] = self.tool_call_id or call_id_factory()
            out["content"] = self.content
            return out

        out["content"] = self.content
        return out


def _default_call_id() -> str:
    return "call_" + uuid.uuid4().hex[:12]


# ══════════════════════════════════════════════════════════════════
# 事件协议
# ══════════════════════════════════════════════════════════════════
class EventType:
    """事件类型常量。

    来自 Codex 的真实事件面（turn.started / model.delta / tool.requested /
    tool.output / file.changed / approval.required / turn.completed），
    加上书第一章的「验证」与第五章的「自证」，补齐 check 与 error。
    """

    TURN_STARTED = "turn.started"
    REASONING = "reasoning"                  # 推理文本增量
    MESSAGE = "message"                      # 给用户的正文增量
    TOOL_REQUESTED = "tool.requested"
    APPROVAL_REQUIRED = "approval.required"
    TOOL_OUTPUT = "tool.output"
    FILE_CHANGED = "file.changed"
    CHECK = "check"                          # 自证 / 目标达成判定
    TURN_COMPLETED = "turn.completed"
    ERROR = "error"
    # 纠正链路的可观测事件
    RETRY = "retry"
    CIRCUIT_OPEN = "circuit.open"            # 熔断
    LOOP_DETECTED = "loop.detected"          # 重复调用指纹命中
    COMPACTED = "context.compacted"


# 错误分层（书 §5「故障分类学」四层）
ERR_API = "api"
ERR_TOOL = "tool"
ERR_CONTEXT = "context"
ERR_CONTROL = "control"


@dataclass
class Event:
    """一条事件。

    `seq` **必须单调递增、无空洞、无回退** —— 前端据此做去重与重连补偿。
    """

    type: str
    seq: int = 0
    ts: float = field(default_factory=time.time)
    turn_id: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        return {"type": self.type, "seq": self.seq, "ts": self.ts,
                "turn_id": self.turn_id, "data": self.data}


class EventBus:
    """事件总线：保证 seq 严格递增，并允许订阅者挂起（审批闸门用）。

    写在这里而不是 loop.py，是因为「seq 单调」是协议级不变式，
    任何发事件的代码路径都必须经过它，不能靠自觉。
    """

    def __init__(self, turn_id: str = ""):
        self.turn_id = turn_id or uuid.uuid4().hex[:8]
        self._seq = 0
        self._log: List[Event] = []
        self._subscribers: List[Any] = []

    # ---------- 发射 ----------
    def emit(self, type_: str, **data: Any) -> Event:
        self._seq += 1
        ev = Event(type=type_, seq=self._seq, turn_id=self.turn_id, data=data)
        self._log.append(ev)
        for fn in list(self._subscribers):
            try:
                fn(ev)
            except Exception:
                # 订阅者（前端推送、日志）出错不应影响主循环。
                # 书 §5 的纪律：错误路径上禁止一切会再次调用模型的副作用逻辑。
                pass
        return ev

    def subscribe(self, fn) -> None:
        self._subscribers.append(fn)

    # ---------- 查询 ----------
    @property
    def seq(self) -> int:
        return self._seq

    def events(self) -> List[Event]:
        return list(self._log)

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(e.to_json(), ensure_ascii=False) for e in self._log)

    def assert_monotonic(self) -> None:
        """自检：seq 必须 1..N 无空洞无回退。

        这是书 §5「完整性监控」在事件层的落地 —— 把不变式做成可断言的检查，
        而不是写在注释里靠人记。
        """
        for i, e in enumerate(self._log, start=1):
            if e.seq != i:
                raise AssertionError(f"事件 seq 不连续：第 {i} 条事件的 seq={e.seq}")


# ══════════════════════════════════════════════════════════════════
# 工具结果：把「截断 + 落盘」做成一等公民
# ══════════════════════════════════════════════════════════════════
DEFAULT_OUTPUT_LIMIT = 10_000  # token 上限的近似（按字符折算），对齐 Codex 的默认值


@dataclass
class ToolResult:
    """工具执行的返回值。

    `ok` / `exit_code` / `truncated` 是**结构化字段** —— 书 §1 明确要求
    「安全检查只看结构化数据，不看模型自由生成的文本，因为后者可能已被提示注入操纵」。
    verify.py 只读这些字段，绝不解析 `text`。

    `full_output_path` 对应书 §5 的上下文管理：命令输出超限时保留前后各若干行，
    中间以提示替代，并**说明完整输出已保存到临时文件供按需查看**。
    """

    ok: bool
    text: str = ""
    exit_code: Optional[int] = None
    truncated: bool = False
    full_output_path: Optional[str] = None
    # 结构化副作用声明，供 policy / verify 判定，不依赖文本
    files_changed: List[str] = field(default_factory=list)
    # 是否「无副作用」——用于沙箱判定（读操作 vs 写操作）
    side_effect: bool = False
    error_layer: Optional[str] = None      # ERR_API / ERR_TOOL / ...
    retryable: Optional[bool] = None       # None = 未判定，由 correct.py 决定
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        d = {
            "ok": self.ok,
            "text": self.text,
            "truncated": self.truncated,
            "side_effect": self.side_effect,
        }
        if self.exit_code is not None:
            d["exit_code"] = self.exit_code
        if self.full_output_path:
            d["full_output_path"] = self.full_output_path
        if self.files_changed:
            d["files_changed"] = self.files_changed
        if self.error_layer:
            d["error_layer"] = self.error_layer
        if self.retryable is not None:
            d["retryable"] = self.retryable
        if self.meta:
            d["meta"] = self.meta
        return d

    @classmethod
    def error(cls, text: str, layer: str = ERR_TOOL, retryable: bool = False,
              **meta) -> "ToolResult":
        return cls(ok=False, text=text, error_layer=layer, retryable=retryable, meta=meta)


def truncate_output(text: str, limit: int = DEFAULT_OUTPUT_LIMIT) -> "tuple[str, bool]":
    """保留**前若干行与后若干行**，中间以一行提示替代。

    刻意不是「截前 N 字符」：书 §5 指出命令输出通常**前段含错误上下文、后段含错误总结**，
    掐头去尾是最差的选择。
    """
    if len(text) <= limit or limit <= 0:
        return text, False

    head_budget = int(limit * 0.6)
    tail_budget = limit - head_budget
    lines = text.splitlines()

    head: List[str] = []
    acc = 0
    for ln in lines:
        if acc + len(ln) + 1 > head_budget:
            break
        head.append(ln)
        acc += len(ln) + 1

    tail: List[str] = []
    acc = 0
    for ln in reversed(lines):
        if acc + len(ln) + 1 > tail_budget:
            break
        tail.append(ln)
        acc += len(ln) + 1
    tail.reverse()

    omitted = len(lines) - len(head) - len(tail)
    if omitted <= 0:
        return text, False

    marker = f"\n… 省略中间 {omitted} 行（完整输出见下方路径）…\n"
    return "\n".join(head) + marker + "\n".join(tail), True


# ══════════════════════════════════════════════════════════════════
# 轨迹：中立格式的容器，负责完整性校验与修复
# ══════════════════════════════════════════════════════════════════
class Trajectory:
    """中立轨迹。

    两条不变式在这里维护：
      · **append-only** —— 只提供 append()，不提供任何原地修改 API。
      · **seq 单调** —— 每条消息入列时分配，可断言。
    """

    def __init__(self) -> None:
        self._msgs: List[Message] = []

    # ---------- 追加（唯一的写入口） ----------
    def append(self, msg: Message) -> Message:
        msg.seq = len(self._msgs) + 1
        self._msgs.append(msg)
        return msg

    def extend(self, msgs: Iterable[Message]) -> None:
        for m in msgs:
            self.append(m)

    # ---------- 读取 ----------
    def __len__(self) -> int:
        return len(self._msgs)

    def __iter__(self):
        return iter(self._msgs)

    def messages(self) -> List[Message]:
        return list(self._msgs)

    def last(self) -> Optional[Message]:
        return self._msgs[-1] if self._msgs else None

    # ---------- 完整性 ----------
    def unpaired_tool_calls(self) -> List[str]:
        """找出**缺少配对结果**的工具调用所属的 assistant 消息序号。

        书 §5：这类结构损坏必须在「注入上下文之前自动修复配对」，
        而不是把结构异常抛给模型或用户。
        """
        bad: List[str] = []
        for i, m in enumerate(self._msgs):
            if m.role != ROLE_ASSISTANT or not m.tool_calls:
                continue
            n = len(m.tool_calls)
            # 紧随其后的 n 条应当都是 tool
            following = self._msgs[i + 1: i + 1 + n]
            got = sum(1 for x in following if x.role == ROLE_TOOL)
            if got != n:
                bad.append(str(m.seq))
        return bad

    def repair_pairing(self, placeholder: str = "[该工具调用没有返回结果]") -> int:
        """补齐缺失的 tool 结果。返回补了几条。

        这是「产品模式」的宽容做法（书 §5）。
        训练数据收集模式应当**拒绝修复**，因为合成占位符会污染训练数据 ——
        见 strict 参数。
        """
        return self._repair(placeholder, strict=False)

    def repair_pairing_strict(self) -> int:
        """训练模式的严格做法：不修补，直接抛错。"""
        return self._repair("", strict=True)

    def _repair(self, placeholder: str, strict: bool) -> int:
        bad = self.unpaired_tool_calls()
        if not bad:
            return 0
        if strict:
            raise AssertionError(f"轨迹结构损坏（产品模式可修补，训练模式拒绝）：assistant seq={bad}")

        fixed = 0
        i = 0
        while i < len(self._msgs):
            m = self._msgs[i]
            if m.role == ROLE_ASSISTANT and m.tool_calls:
                n = len(m.tool_calls)
                following = self._msgs[i + 1: i + 1 + n]
                got = sum(1 for x in following if x.role == ROLE_TOOL)
                if got < n:
                    for k in range(n - got):
                        ph = Message.tool_result("", placeholder)
                        ph.seq = m.seq * 100 + k      # 占位 seq 不参与主序号
                        self._msgs.insert(i + 1 + got + k, ph)
                        fixed += 1
                    i += n
            i += 1
        return fixed

    def assert_seqs(self) -> None:
        for i, m in enumerate(self._msgs, start=1):
            if m.seq != i:
                raise AssertionError(f"轨迹 seq 不连续：第 {i} 条 seq={m.seq}")

    # ---------- 序列化 ----------
    def to_json(self) -> List[Dict[str, Any]]:
        return [m.to_json() for m in self._msgs]

    @classmethod
    def from_json(cls, arr: Iterable[Dict[str, Any]]) -> "Trajectory":
        t = cls()
        for d in arr:
            t.append(Message.from_json(d))
        return t
