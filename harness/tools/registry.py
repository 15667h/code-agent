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
from typing import Any, Callable, Dict, List, Optional

from ..context import build_tool_spec
from ..policy import Action, ActionKind
from ..protocol import ToolResult


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
