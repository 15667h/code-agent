"""工具注册表与调用路由。

## 管线（对齐 Codex）

Codex 的 Rust core 维护 `ToolRegistry` 与 `ToolRouter`，所有工具调用走同一条管线：

    模型发出 tool call → ToolRouter → 审批检查 → 沙箱选择 → 拒绝/允许 → 执行 → 结果回模型

（`codex-rs/core/tests/suite/approvals.rs` 的 `ToolOrchestrator::run()` 是这条管线的入口。）

本模块把这条管线拆成两个文件：
    registry.py —— 声明与查找（**注册顺序即输出顺序**）
    router.py   —— 判定与执行

## 为什么注册顺序必须固定

书 §2 的错误模式之三：**工具定义动态排序**。
工具定义在上下文中占很大篇幅（每个工具数百 token），改变顺序会让
缓存从首个变动的工具起全部失效。而实测表明，**保持固定顺序对模型选择工具的能力
几乎没有影响，对性能的提升却很显著**。

因此本类用 `dict`（Python 3.7+ 保证插入顺序）而非 `set`，
并在 `specs()` 里原序输出，同时提供 `assert_order_stable()` 供测试断言。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..context import build_tool_spec
from ..policy import Action, ActionKind
from ..protocol import ToolResult


# ══════════════════════════════════════════════════════════════════
# 参数归一化：剥掉模型多包的一层壳
# ══════════════════════════════════════════════════════════════════
# 实测（deepseek-v4.1-flash，文本协议）：模型会把 OpenAI function calling
# 的**完整外壳**当成参数发出来 ——
#
#     {"arguments": {"patch": "*** Begin Patch\n..."}}
#
# 语义完全正确，只是多了一层。旧实现把 args 原样交给 handler，
# handler 找不到 `patch` 就报「patch 参数为空」。这句报错**没提它收到的是
# `arguments`**，模型看不出该改哪里，于是把内容正确的补丁重发了 3 次，
# 最后被无进展循环拦停 —— 一个本来能做完的项目因此一个文件都没落盘。
#
# 这是「模型做对了但 harness 把它丢了」，比「模型选错」危险得多：
# 它的表现是正常报错，看不出是 harness 的锅。
#
# 三条纪律：
#   1. 只在**无歧义**时剥 —— 键名是已知包装名、内层是对象、内层含声明过的参数。
#   2. 工具若真的声明了同名参数（如 arguments），一律不剥（硬护栏）。
#   3. 剥了几层要**报出去**（事件里带 args_unwrapped），不静默改 ——
#      参数保真性要求「模型看到的世界 == 工具操作的世界」，静默改会破坏归因。
_WRAPPER_KEYS: Tuple[str, ...] = ("arguments", "args", "parameters", "input", "params", "payload")
_MAX_UNWRAP = 4      # 只防死循环；实测最多两层，给到 4 足够


class Tool:
    """一个工具的完整定义。

    `describe_action` 是这个设计的要点：**工具负责把自己的一次调用翻译成结构化的
    `Action`**，而不是把命令原文交给策略层去猜。理由来自书 §1：

    > 安全检查只看结构化数据（如工具返回的 JSON 字段），而不看模型自由生成的文本，
    > 因为后者可能已被提示注入操纵。

    如果让 policy 去解析 shell 字符串来判断意图，注入就赢了 ——
    攻击者只需把危险命令伪装成无害文本。而结构化 Action 是工具实现方
    （受信任的代码）产出的，不经模型之手。
    """

    def __init__(self,
                 name: str,
                 description: str,
                 properties: Dict[str, Any],
                 handler: Callable[[Dict[str, Any]], ToolResult],
                 describe_action: Callable[[Dict[str, Any]], Action],
                 required: Optional[List[str]] = None,
                 examples: Optional[List[Dict[str, Any]]] = None,
                 supports_parallel: bool = False,
                 category: str = "general") -> None:
        self.name = name
        self.description = description
        self.properties = properties
        self.handler = handler
        self.describe_action = describe_action
        self.required = required or []
        self.examples = examples or []
        # 书 §5「并行工具调用」：**每个工具定义应声明自己是否支持并发执行
        # （默认为否，失败安全）**。默认 False 正是「故障安全默认值」。
        self.supports_parallel = supports_parallel
        self.category = category
        self.spec = build_tool_spec(name, description, properties, self.required, self.examples)

    def to_spec(self) -> Dict[str, Any]:
        return json.loads(json.dumps(self.spec, ensure_ascii=False))

    # ------------------------------------------------------------------
    # 参数归一化
    # ------------------------------------------------------------------
    def normalize_args(self, args: Any) -> Tuple[Dict[str, Any], int, str]:
        """把模型给的参数整理成 tool 真正声明的形状。

        返回 `(参数, 剥掉的层数, 说明)`。说明非空时应当**展示给用户与模型**
        （「我把你多包的那层剥了」是信息，不是内部细节）。

        这一步刻意放在 registry（工具声明处）而不是各解析分支：
        原生 function calling、三种文本协议、子代理、回放**都**走 router，
        在 router 里单点归一化，才不会有第五份副本（§判据单一来源）。
        """
        note = ""

        # 整串是 JSON 文本（部分端点会把 arguments 再序列化一次）
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return {}, 0, ("参数不是 JSON 对象（收到的是一段无法解析的文本）。"
                               "请按工具定义传一个 JSON 对象。")
            note = "参数原本是一段 JSON 字符串，已解析为对象。"

        # 解析层留下的畸形原文：{"__malformed__": "<json>"}
        if isinstance(args, dict) and set(args) == {"__malformed__"}:
            raw = args["__malformed__"]
            if isinstance(raw, str):
                try:
                    loaded = json.loads(raw)
                except json.JSONDecodeError:
                    return {}, 0, ("参数无法解析成 JSON（`__malformed__` 里的原文不是合法 JSON）。"
                                   "请检查引号与花括号是否闭合。")
                if isinstance(loaded, dict):
                    args = loaded
                    note = "参数原本是畸形字符串，已从原文中恢复为对象。"
            if isinstance(args, dict) and set(args) == {"__malformed__"}:
                return {}, 0, ("参数无法解析成 JSON 对象。"
                               "请按工具定义传一个 JSON 对象，不要传单个裸值。")

        if not isinstance(args, dict):
            return {}, 0, (f"参数必须是 JSON 对象，收到的是 {type(args).__name__}。")

        declared = set(self.properties or {})
        cur: Dict[str, Any] = args
        layers = 0

        while layers < _MAX_UNWRAP:
            # 已经含声明过的参数 → 这就是真参数，收手
            if set(cur) & declared:
                break
            # 硬护栏：工具真的声明了同名参数，绝不剥
            if "arguments" in declared and "arguments" in cur:
                break
            key = next((k for k in _WRAPPER_KEYS if k in cur), None)
            if key is None:
                break
            inner = cur[key]

            # 先试 JSON：壳里可能是被再序列化一次的对象
            # （`{"arguments": "{\"patch\": ...}"}`），那仍然要按对象剥。
            as_obj = None
            if isinstance(inner, str):
                try:
                    loaded = json.loads(inner)
                except json.JSONDecodeError:
                    loaded = None
                if isinstance(loaded, dict) and loaded:
                    as_obj = loaded

            # 壳里是**裸标量**（实测：`{"arguments": "node -e \"...\""}`）。
            # 无法解析成对象，但意图是明确的：只有一个必填参数的工具，
            # 这个值只能是它。只在**恰好一个必填参数 + 类型对得上**时补，
            # 且要报出去 —— 这是推断，不是还原。
            if as_obj is None and isinstance(inner, str) and len(cur) == 1:
                if len(self.required) == 1:
                    only = self.required[0]
                    prop = (self.properties or {}).get(only) or {}
                    if prop.get("type", "string") == "string":
                        return ({only: inner}, layers + 1,
                                f"参数被包在 `{key}` 里且是裸文本（{len(inner)} 字符）；"
                                f"本工具只有一个必填参数 `{only}`，已按它填充。")
                break

            inner = as_obj if as_obj is not None else inner
            if not isinstance(inner, dict) or not inner:
                break
            # 内层必须「更像参数」：命中声明，或它自己还是一层壳（双层壳）
            if not (set(inner) & declared) and not any(k in inner for k in _WRAPPER_KEYS):
                break
            cur = inner
            layers += 1

        if layers:
            got = ", ".join(f"`{k}`" for k in cur)
            note = (f"参数被多包了 {layers} 层（形如 {{\"arguments\": {{...}}}}），"
                    f"已自动剥开；实际使用的参数是 {got}。")

        return cur, layers, note

    def missing_required(self, args: Dict[str, Any]) -> List[str]:
        """缺哪些必填参数。只在**一个必填都没给**时才值得拦下来报错 ——
        缺一部分的报错由 handler 给出，那里更清楚该补什么。"""
        if not self.required:
            return []
        missing = [k for k in self.required if k not in args]
        if len(missing) == len(self.required):
            return missing
        return []

    def describe_received_args(self, args: Dict[str, Any]) -> str:
        """把「你实际传了什么」说清楚 —— 这是让模型一次改对的关键。

        旧文案「patch 参数为空」只说了缺什么，没说它收到的是 `arguments`，
        模型看不出自己错在结构上，只能盲目重试。
        """
        if not args:
            return "你这次没有传任何参数。"
        parts = []
        for k, v in list(args.items())[:6]:
            if isinstance(v, str):
                desc = f"字符串，{len(v)} 字符"
            elif isinstance(v, bool):
                desc = f"布尔值 {str(v).lower()}"
            elif isinstance(v, (int, float)):
                desc = f"数字 {v}"
            elif isinstance(v, dict):
                desc = f"对象，键为 {', '.join(list(v)[:6])}"
            elif isinstance(v, list):
                desc = f"数组，{len(v)} 项"
            else:
                desc = type(v).__name__
            parts.append(f"`{k}`（{desc}）")
        return "你实际传入的参数是：" + "、".join(parts) + "。"



class ToolRegistry:
    """注册表。**插入顺序 == 输出顺序**，这是缓存纪律的一部分。"""

    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}
        self._order: List[str] = []

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"工具 {tool.name} 已注册，不允许覆盖（注册顺序是不变式）")
        self._tools[tool.name] = tool
        self._order.append(tool.name)
        return tool

    def tool(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return list(self._order)

    def has(self, name: str) -> bool:
        return name in self._tools

    def specs(self) -> List[Dict[str, Any]]:
        """按注册顺序输出工具定义 —— 这个顺序会被冻结进静态前缀。"""
        return [self._tools[n].to_spec() for n in self._order]

    def assert_order_stable(self, expected: List[str]) -> None:
        """断言工具顺序未变。测试用它，运行时可选的廉价自检也用它能提前发现重排。"""
        got = self.names()
        if got != expected:
            raise AssertionError(
                f"工具注册顺序发生变化：\n  期望 {expected}\n  实际 {got}\n"
                "书 §2 错误模式之三：工具定义动态排序会让缓存从首个变动的工具起全部失效。"
            )

    def categories(self) -> Dict[str, List[str]]:
        """按类别分组 —— 书 §4「层次化组织」：当工具数量增长时，
        按信息源性质分类比扁平列表更有效，且在系统提示词中说明分类
        能帮模型快速定位到相关的工具组。"""
        out: Dict[str, List[str]] = {}
        for n in self._order:
            out.setdefault(self._tools[n].category, []).append(n)
        return out
