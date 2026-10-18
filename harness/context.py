"""上下文构造：把第二章的三条铁律变成可断言的代码。

书 §2 三条铁律（原文标注为「只需记住这三条」）：

  1. 系统提示词和工具定义一旦确定就**不要改**。
     任何改动，哪怕多一个空格，都可能改变 token 序列，使首个不同 token 及其后的
     缓存无法复用；**改动越靠前，延迟和成本影响越大**。
  2. **动态信息永远追加到末尾** —— 时间戳、用户状态等，作为新消息追加到对话末尾，
     而不是修改已有的系统提示词。
  3. 使用标准 API 格式，**不要自行拼接消息**。

本模块的核心设计：**把「前缀不变」从口头纪律变成可断言的测试**。
`static_prefix_hash()` 是一个会真正被测试调用的函数，而不是写在注释里的约定。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from .protocol import (
    Message, ROLE_ASSISTANT, ROLE_SYSTEM, ROLE_TOOL, ROLE_USER, ToolCall,
)


# ══════════════════════════════════════════════════════════════════
# 静态前缀：一旦构建就冻结
# ══════════════════════════════════════════════════════════════════
class StaticPrefix:
    """系统提示词 + 工具定义。**构建后不可变。**

    书 §2 的「缓存作为架构约束」指出：提示词的结构首先由**缓存的经济性**决定，
    其次才是语义逻辑。每个运行时条件（OS、模式、语言）若放在缓存边界之前，
    会使缓存键的变体数量翻倍（N 个二值条件 → 2^N 种组合），
    因此**所有动态元素都必须放到边界之后**。

    本类的做法：把所有会变的东西（时间、环境、用户偏好）从构造参数里彻底拿掉，
    强迫它们走 `ContextBuilder.append()` 的末尾追加路径。
    """

    def __init__(self, system_prompt: str, tools_spec: List[Dict[str, Any]]) -> None:
        self._system = system_prompt
        # 工具按**注册顺序**保存。书 §2 的错误模式之一就是「工具定义动态排序」：
        # 它会让缓存从首个变动的工具起全部失效，而实测表明保持固定顺序
        # 对模型选择工具的能力几乎无影响。
        self._tools = [json.loads(json.dumps(t, ensure_ascii=False)) for t in tools_spec]
        self._frozen = True

    @property
    def system(self) -> str:
        return self._system

    @property
    def tools(self) -> List[Dict[str, Any]]:
        # 返回深拷贝，防止调用方拿到引用后原地改坏前缀
        return json.loads(json.dumps(self._tools, ensure_ascii=False))

    def hash(self) -> str:
        """前缀指纹。**测试会在多轮之后重新取它并断言不变。**

        序列化必须稳定：sort_keys 只用于 tool 内部的 key 排序，
        而 tools 列表本身的顺序**保持不变**（这正是「工具顺序固定」这条纪律）。
        """
        payload = json.dumps(
            {"system": self._system, "tools": self._tools},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def tool_names_in_order(self) -> List[str]:
        return [t.get("function", {}).get("name", "") for t in self._tools]


# ══════════════════════════════════════════════════════════════════
# 上下文构造器
# ══════════════════════════════════════════════════════════════════
class ContextBuilder:
    """把「静态前缀 + 轨迹」组装成一次模型请求。

    书 §2 的结论：**Agent 的上下文 = 静态前缀 + 轨迹**。
    Harness 的核心工作就是管理这个列表 —— 在合适时机追加，然后整体送出。

    三条对外承诺：
      1. 前缀**永不改变**（`prefix_hash()` 可断言）。
      2. 轨迹**只增不改**（只暴露 append，不暴露任何原地修改）。
      3. 动态信息（时间、环境）走 `inject_status()`，**只追加到末尾**。
    """

    def __init__(self, prefix: StaticPrefix) -> None:
        self._prefix = prefix
        self._trajectory: List[Message] = []
        self._observed_hash = prefix.hash()

    # ---------- 前缀 ----------
    @property
    def prefix(self) -> StaticPrefix:
        return self._prefix

    def prefix_hash(self) -> str:
        return self._prefix.hash()

    def assert_prefix_stable(self) -> None:
        """断言前缀与创建时一致。

        这条断言的意义不只是自测 —— 它可以在**每次模型调用前**跑一遍，
        一旦有人在别处偷偷改了前缀（比如注入当前时间），立刻抛错而不是
        悄悄把缓存全打掉。书里那个「加一行时间戳，账单翻倍」的故事，
        正是靠这种断言才能被提前拦住。
        """
        now = self._prefix.hash()
        if now != self._observed_hash:
            raise AssertionError(
                f"静态前缀被修改了（{self._observed_hash} → {now}）。"
                "书 §2 铁律 1：系统提示词与工具定义一旦确定就不要改；"
                "动态信息必须走 append 追加到末尾。"
            )

    # ---------- 轨迹（只增不改） ----------
    def append(self, msg: Message) -> Message:
        msg.seq = len(self._trajectory) + 1
        self._trajectory.append(msg)
        return msg

    def append_user(self, text: str) -> Message:
        return self.append(Message.user(text))

    def append_assistant(self, content: str = "", reasoning: Optional[str] = None,
                         tool_calls: Optional[List[ToolCall]] = None,
                         signature: Optional[str] = None,
                         origin_model: Optional[str] = None) -> Message:
        return self.append(Message.assistant(content, reasoning, tool_calls,
                                            signature, origin_model))

    def append_tool_result(self, call_id: str, content: str, name: str = "") -> Message:
        return self.append(Message.tool_result(call_id, content, name))

    def inject_status(self, status: Dict[str, Any]) -> Message:
        """**追加**一条状态栏消息（Agent Status Bar，书 §2）。

        环境信息（工作目录、git 分支、变更概览）绝不能硬编码进静态系统提示词 ——
        那样会破坏 KV Cache。正确做法是作为**追加式**的消息实时生成。

        这里用 role="user" 承载，并加上 `meta.kind="status"` 标记，
        以便 verify / UI 区分它与真实用户输入。
        """
        body = json.dumps(status, ensure_ascii=False, sort_keys=True, indent=2)
        m = Message.user(f"[环境状态]\n{body}")
        m.meta["kind"] = "status"
        return self.append(m)

    def trajectory(self) -> List[Message]:
        return list(self._trajectory)

    def __len__(self) -> int:
        return len(self._trajectory)

    # ---------- 组装 ----------
    def build(self, provider: str = "generic") -> Dict[str, Any]:
        """产出发给模型的请求体。

        顺序严格固定：system → 轨迹。
        书 §2 铁律 3：不要自行拼成 `USER: ... ASSISTANT: ...` 明文流，
        那会偏离模型训练时见过的格式。
        """
        self.assert_prefix_stable()

        msgs: List[Dict[str, Any]] = [
            {"role": ROLE_SYSTEM, "content": self._prefix.system}
        ]
        for m in self._trajectory:
            msgs.append(m.render_for(provider))

        return {
            "messages": msgs,
            "tools": self._prefix.tools,
        }

    # ---------- 压缩：只动中段，且只追加 ----------
    def compact(self, summarizer, keep_recent: int = 6) -> Optional[Message]:
        """上下文压缩。

        **本方法刻意不修改任何已有消息** —— 只在轨迹末尾**追加**一条摘要。
        书 §2 的 append-only 纪律与 §5 的「只增不改」设计模式都要求如此。

        对比书里点名的错误做法「滑动窗口」：它丢弃最早的消息，
        既破坏前缀一致性，又**丢失关键工具结果** —— 实测会让 Agent 陷入
        反复调用同一工具的循环（因为它"忘记"了之前已经获得的结果）。

        返回被追加的摘要消息；轨迹太短则返回 None。

        实现说明：真正的摘要由调用方注入的 `summarizer`（可以是模型，也可以是
        本地规则）。本类不依赖模型 —— 书 §5「提议者—审核者」的纪律：
        校验/治理层不应与产出层共享同一上下文。
        """
        if len(self._trajectory) <= keep_recent:
            return None

        # 中段 = 除最近 keep_recent 条以外的历史
        middle = self._trajectory[:-keep_recent]
        if not middle:
            return None

        summary_text = summarizer(middle)

        m = Message.user(
            "[上下文压缩摘要] 以下是较早轨迹的结构化摘要。"
            "原始消息仍完整保留在 rollout 中，可按需检索。\n" + summary_text
        )
        m.meta["kind"] = "compaction"
        m.meta["covers_seq"] = [middle[0].seq, middle[-1].seq]
        return self.append(m)

    # ---------- 序列化 ----------
    def trajectory_json(self) -> List[Dict[str, Any]]:
        return [m.to_json() for m in self._trajectory]


# ══════════════════════════════════════════════════════════════════
# 系统提示词：按书 §2「提示工程」的组织方式
# ══════════════════════════════════════════════════════════════════
DEFAULT_SYSTEM_PROMPT = """\
你是 Crucible，一个代码库内的工程 agent。你通过工具在真实工作区里做事，
而不是在对话里给建议。

# 工作方式
1. 先读再改。改动前确认目标文件的当前内容与上下文，不要凭记忆下笔。
2. 用工具而不是描述。文件有专门的读取、搜索、补丁工具时优先用它们，
   而不是用 shell 拼命令——专用工具会给你带行号的结果与结构化错误。
3. 每次改动尽量小、可单独回滚，并在补丁里写清为什么改。
4. 完成的标准是**验证通过**，不是"代码写完了"。改完要跑检查；
   检查没过就继续修，不要报告完成。

# 工具使用规范
- 读取文件用 read_file，它返回的内容每行都带真实行号，可直接用于引用与补丁定位。
- 搜索代码用 grep，它只匹配文件**内容**，不匹配文件名；找文件名请用 glob。
- 改文件用 apply_patch。补丁的上下文必须能唯一定位，
  匹配不唯一时工具会拒绝执行——此时补更多上下文重来，不要反复重试同一个补丁。
- 运行命令用 shell。命令受沙箱与审批策略约束，越界的动作会被拒绝或要求确认。

# 工具调用方式（**唯一方式**）
你的接口本次**没有**提供原生 function calling 通道。
所有工具调用都必须用下面的文本格式表达，写在回复正文里：

<tool_call>{"name": "工具名", "arguments": {"参数名": "参数值"}}</tool_call>

示例 —— 要读 README.md 就输出：
<tool_call>{"name": "read_file", "arguments": {"path": "README.md"}}</tool_call>

示例 —— 要列当前目录就输出：
<tool_call>{"name": "list_dir", "arguments": {"path": "."}}</tool_call>

要点（务必遵守）：
- 一次可以写多个 <tool_call> 块，按顺序执行。
- **只描述意图不算调用工具**：写"我先读一下 README"不会被执行。
  必须真的写出 <tool_call> 块。这是最容易犯的错。
- 除 <tool_call> 块外不要写别的解释性文字，保持输出干净。
- 不要使用任何其它标记（例如 <｜｜DSML｜｜...>），只用 <tool_call>。
- 任务全部完成、不需要再调用工具时，直接写最终回答，不加 <tool_call> 块。

# 边界
- 不要修改工作区以外的任何路径，不要访问网络，除非当前策略明确允许。
- 不确定的事就说"我不确定，需要先确认 X"，不要编造 API、函数签名或文件内容。
- 遇到工具报错，先读错误信息再决定下一步；原样重试不会改变结果。
"""


def build_tool_spec(name: str, description: str,
                    properties: Dict[str, Any],
                    required: Optional[List[str]] = None,
                    examples: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """构造一个工具 schema。

    书 §4 的三条硬纪律体现在这个签名的形状上：
      · `description` 必须回答**「什么时候用」**，而不只是「能做什么」。
      · 要写清**边界（做不到什么）** —— 书原话：大多数工具调用失败的根因
        不是模型不知道能做什么，而是不知道不能做什么。
      · `examples` 可选但强烈建议：JSON Schema 只能描述类型，
        无法表达调用方式与典型参数组合；加示例后准确率可从 ~72% 提升到 ~90%。
    """
    spec: Dict[str, Any] = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                **({"required": required} if required else {}),
                "additionalProperties": False,
            },
        },
    }
    if examples:
        # 示例直接写进 description —— 这是各家 API 唯一能容纳自由文本的位置，
        # 且它属于静态前缀，与工具定义一起被缓存，不额外消耗每轮预算。
        spec["function"]["description"] = (
            description + "\n\n调用示例：\n"
            + "\n".join(json.dumps(e, ensure_ascii=False) for e in examples)
        )
    return spec
