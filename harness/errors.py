"""统一的工具错误模板。

## 为什么需要统一

书 §5 有一条原则反复出现：

> **反馈越结构化越好。** 工具层错误不终止会话，而是**变成模型的输入**；
> 幻觉调用会收到"工具不存在"的结构化错误结果，参数校验失败会收到
> 附带输入约束提示的错误 —— 由模型在下一轮自行纠正。

但"变成模型的输入"只是必要条件，不是充分条件。真正决定模型能否自我纠正的
是**这段错误文本里有多少可执行的信息**。

## 前沿数据

《Harness Engineering Survey》汇总的工程共识给了一组很具体的数字：

> 把 30 个常见错误改写为「下一步指令」，无人干预的恢复率 **~50% → >80%**。

也就是说：**同样一次失败，告诉模型"下一步调什么"，恢复率接近翻倍。**

反面形态（我们改造前就有不少）：
    `ToolResult.error(str(e))`            —— 只说了"出错了"，没说怎么办
    `"正则表达式无法编译：..."`            —— 说了原因，没说该改哪里

## 三段式模板

每条错误都必须回答三个问题：

    [出了什么错]   客观事实（哪个参数、哪个路径、什么层）
    [期望是什么]   约束条件（工具要求什么形态的输入）
    [下一步]       **具体动作**（调哪个工具、改哪个参数）

第三段是关键，也是最常被漏的。写"请检查参数"等于没写 ——
模型没有"检查"这个动作可执行。必须写到"把 path 改成相对路径"这个粒度。

## 与 `classify()` 的分工

`correct.py` 的 `classify()` 判定的是**可重试性**（值不值得重试）；
本模块管的是**措辞**（怎么把错误说清楚）。两者互补：
先分类决定策略，再用模板生成给模型看的话。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .protocol import ToolResult


# ══════════════════════════════════════════════════════════════════
# 错误码 → 三段式文案
# ══════════════════════════════════════════════════════════════════
# 键是各工具已经在用的 `kind`。集中放这里的好处：
#   · 同一个错误在 read_file / list_dir / grep 里措辞**完全一致** ——
#     模型面对同一类问题学一次就够
#   · 改措辞只改一处，不会出现"三个工具三种说法"
#
# `next_step` 必须是**可执行动作**（调哪个工具 / 改哪个参数）。
# 写抽象建议（"请检查"）比不写更坏：它占用了模型的注意力却没给出行动。
_CATALOG: Dict[str, Dict[str, str]] = {
    "bad_path": {
        "expect": "工具只接受**工作区内的相对路径**，例如 `src/main.py`。",
        "next_step": "改传相对路径；若不确定当前有哪些文件，先用 list_dir 看目录。",
    },
    "bad_args": {
        "expect": "该工具要求的参数名与类型见它的定义。",
        "next_step": "按工具定义补齐或改正参数名后重试；不确定可先 list_dir 了解工作区。",
    },
    "not_found": {
        "expect": "要读取/修改的文件必须已存在。",
        "next_step": "用 glob（按文件名）或 grep（按内容）找到正确路径；"
                     "若要新建文件，apply_patch 请用 `*** Add File:` 段。",
    },
    "not_dir": {
        "expect": "list_dir 只能作用于目录。",
        "next_step": "若目标是文件，改用 read_file；若不确定，先用 list_dir 看上级目录。",
    },
    "is_dir": {
        "expect": "read_file 只能读取文件，不能读取目录。",
        "next_step": "改用 list_dir 列出该目录的内容。",
    },
    "io": {
        "expect": "文件系统操作应当成功。",
        "next_step": "确认路径拼写与权限；若是临时被占用，稍后重试同一调用即可。",
    },
    "exists": {
        "expect": "`*** Add File:` 只用于**新建**文件。",
        "next_step": "目标已存在时改用 `*** Update File:` 段，"
                     "并先用 read_file 读取**当前实际内容**再写补丁。",
    },
    "no_unique_match": {
        "expect": "补丁的上下文必须能在文件里**唯一**定位。",
        "next_step": "先用 read_file 读回那段区域，把上下文多带几行前后文，"
                     "或在 `@@` 后写更贴近的锚点；**不要**原样反复重试同一补丁。",
    },
    "parse_error": {
        "expect": "补丁必须符合 V4A 格式：以 `*** Begin Patch` 开始、`*** End Patch` 结束，"
                  "内容行以 `+` / `-` / 空格开头。",
        "next_step": "按格式修正后重发整个补丁；常见原因是漏了 Begin/End 标记。",
    },
    "empty_hunk": {
        "expect": "每个 `@@` 修改块至少要有一行内容。",
        "next_step": "给该修改块补上下文行或 +/- 行，或删掉这个空块。",
    },
    "bad_range": {
        "expect": "start_line / end_line 必须落在文件的实际行数范围内。",
        "next_step": "不传 end_line 让工具自动取到文件末尾，或先读一次确认总行数。",
    },
    "bad_regex": {
        "expect": "搜索工具用的是 Python 正则语法。",
        "next_step": "检查括号/方括号是否配对；若只想做字面量匹配，"
                     "把特殊字符用 `\\` 转义。",
    },
    "timeout": {
        "expect": "命令应当在超时时间内结束。",
        "next_step": "把长任务拆成更小的步骤；若命令在等待输入，改用非交互形式"
                     "（加 `-y` / `--yes` / `</dev/null`）。",
    },
    "shell_dead": {
        "expect": "终端会话应当可用。",
        "next_step": "会话已自动重置，直接重发同一条命令即可。",
    },
    "unknown_tool": {
        "expect": "只能调用已注册的工具。",
        "next_step": "改用错误信息里列出的可用工具之一，或先说明需要什么能力。",
    },
    "bad_glob": {
        "expect": "glob 模式是非空的路径匹配式，例如 `**/*.py`。",
        "next_step": "补上模式；只用文件名匹配，不要写正则。",
    },
    "too_long": {
        "expect": "单个参数不应超过工具的长度上限。",
        "next_step": "拆成多次较小的调用；写文件用 apply_patch 而不是把全文塞进参数。",
    },
    "exception": {
        "expect": "工具执行不应抛出未处理异常。",
        "next_step": "这是 harness 侧的问题，请把报错原文与你的调用参数一起说明，"
                     "或改换等价的其他工具完成同一件事。",
    },
    "refused": {
        "expect": "当前沙箱/审批策略不允许该动作。",
        "next_step": "换一个**在允许范围内**的等价做法；"
                     "若确实需要该权限，请在最终回答里说明需要人工调整策略。",
    },
    "approval_rejected": {
        "expect": "被用户拒绝的操作不应重试。",
        "next_step": "不要重发同一调用；改用不同方案，或在最终回答里说明被拒绝的原因。",
    },
}

# 没有登记的错误码的兜底文案（保持同样的三段式）
_FALLBACK = {
    "expect": "工具调用应当成功。",
    "next_step": "确认参数与文件状态后重试；若反复失败，换一个等价工具或改变做法。",
}


# ══════════════════════════════════════════════════════════════════
# 生成
# ══════════════════════════════════════════════════════════════════
def tool_error(tool: str, kind: str, problem: str,
               expect: Optional[str] = None,
               next_step: Optional[str] = None,
               retryable: bool = False,
               layer: str = "tool",
               **meta: Any) -> ToolResult:
    """按三段式模板构造一个工具错误。

    `problem` 是**本次的具体事实**（哪个路径、哪个行号），
    `expect` / `next_step` 默认取错误码对应的通用文案。

    为什么把"通用文案"与"具体事实"分开：
    只有具体事实是每次不同的；期望与下一步**同类错误完全相同**。
    分开存既避免措辞漂移，也让"同一类错误对模型说的话始终一致"——
    模型对同一类问题只需要学一次。
    """
    entry = _CATALOG.get(kind, _FALLBACK)
    exp = expect or entry.get("expect", _FALLBACK["expect"])
    nxt = next_step or entry.get("next_step", _FALLBACK["next_step"])

    text = (f"[工具错误] {tool}"
            f"\n· 出了什么错：{problem}"
            f"\n· 期望是什么：{exp}"
            f"\n· 下一步：{nxt}")

    return ToolResult(ok=False, text=text, error_layer=layer,
                      retryable=retryable,
                      meta={"kind": kind, "tool": tool, **meta})


def is_standard_error(res: ToolResult) -> bool:
    """这个错误是否已按标准模板生成（便于测试与迁移）。"""
    return isinstance(res.text, str) and res.text.startswith("[工具错误] ")


def missing_catalog_entries(kinds: Sequence[str]) -> List[str]:
    """哪些错误码还没有专属文案（供测试断言"不许有裸错误"）。

    兜底文案不会让功能出错，但它意味着这类错误**没说清下一步** ——
    而这正是本模块存在的理由。所以用测试把它钉住：
    出现新的错误码时，作者必须顺手补上三段式文案。
    """
    return sorted({k for k in kinds if k and k not in _CATALOG})


def catalog() -> Dict[str, Dict[str, str]]:
    """返回错误文案表（供文档生成与审查）。"""
    return {k: dict(v) for k, v in _CATALOG.items()}


def has_next_step(kind: str) -> bool:
    """该错误码是否给出了**可执行的**下一步。

    判据刻意粗（只查"是否非空且不是纯请检查式措辞"）：
    细的语义判断需要人看，这里只挡住最典型的空洞措辞。
    """
    entry = _CATALOG.get(kind)
    if not entry:
        return False
    nxt = (entry.get("next_step") or "").strip()
    if not nxt:
        return False
    vague = ("请检查", "注意检查", "仔细检查", "请确保", "注意")
    return not any(v in nxt for v in vague)
