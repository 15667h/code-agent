"""类型化上下文保留：把「压缩悬崖」这件事从架构上堵掉。

## 问题：一条安全规则和一段情节日志在抢同一个 token 预算

arXiv 2608.22752（CIKM 2026）《The Compaction Cliff in Long-Running AI Agent Memory》
指出的结构性冲突：

> 一条安全规则和一段情节日志在同一个 token 预算里竞争。
> 预算溢出时，两者被**以同样的速率**摘要 —— 但只有安全规则
> 需要**精确措辞**才能继续被执行。

实测（Claude Code 的 /compact，Sonnet 4.6，20 个生产配置）：
安全规则保留率 **1 轮后 53%、5 轮后 10%**。这就是「压缩悬崖」。

对本项目的直接后果很严重：我们有一整套硬约束
（不改工作区外路径、危险动作必须审批、补丁必须唯一匹配……）。
它们是**安全边界**，如果被摘要成"注意安全"这种话，就失效了。

## 解法：先分类，再按类型决定保真度

不是"做更好的摘要"，而是承认**不同类型的内容有不同的保留要求**：

| 类型 | 例子 | 保留策略 |
| --- | --- | --- |
| `constraint` | 系统提示里的硬约束、审批规则 | **永不进摘要器**，原样保留并前置 |
| `fact` | 工具返回的文件内容、命令输出、测试结果 | 结构化压缩：保留结论与关键数据，丢弃冗余 |
| `decision` | 我们做过的取舍与理由 | 保留一行结论（避免重复走弯路） |
| `episode` | 探索过程的碎步（"先看看目录"） | 可大幅压缩或丢弃 |
| `reasoning` | 模型的思考 | 可丢弃（书 §5 也不主张回放思考） |

第二个要点来自 arXiv 2610.20927（Source-Judged Compaction）：

> 压缩摘要不只是"发生了什么"的记录。因为压缩提示词要求"决策与下一步"，
> 每一份摘要都会把**环境确立的事实**与**压缩器自己写的推理**混在一起，
> 而 agent 事后**分不出来**。83% 的失败运行可追溯到对某条摘要条目的执行，
> 而其中大多**没有任何环境输出支持**。

所以摘要条目必须**标注来源**：有环境输出支持的写成事实，模型推断必须显式标注为推测。
绝不能让推断以事实的语气进入后续上下文。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .protocol import Message, ROLE_ASSISTANT, ROLE_SYSTEM, ROLE_TOOL, ROLE_USER


# ══════════════════════════════════════════════════════════════════
# 知识类型
# ══════════════════════════════════════════════════════════════════
class Kind:
    """内容类型。字符串常量而非 Enum —— 它会被写进 JSON 落盘。"""

    CONSTRAINT = "constraint"   # 硬约束：永不压缩
    FACT = "fact"               # 环境事实：结构化压缩
    DECISION = "decision"       # 决策与理由：保留结论
    EPISODE = "episode"         # 探索碎步：大幅压缩
    REASONING = "reasoning"     # 模型思考：可丢


# 判断「这条约束是不是硬约束」的模式。
# 刻意保守：宁可多留几条，不可漏掉真正的安全边界。
# 漏掉的代价是安全失效；多留的代价只是几个 token。
_CONSTRAINT_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        # 中文：禁止/不得/必须/只能/绝不
        r"不(要|得|可|能|允许|准)\s*(修改|删除|写入|访问|执行|读取|越过|绕过|跳过)",
        r"(必须|务必|一定要)\s*(先|经过|得到|获得|确认|检查|审批|批准|授权|同意)",
        r"(必须|务必|一定要)\s*[^。；，,\n]{0,12}(审批|批准|授权|确认|同意|征询)",
        r"(只能|仅能|只可)\s*(在|访问|写入|使用)",
        r"(绝不|严禁|禁止)\s*",
        r"(硬约束|安全边界|红线|不得违反)",
        # 英文
        r"\b(do not|don't|never|must not|must never)\b",
        r"\b(only|exclusively)\s+(within|inside|allow)",
        r"\brequires?\s+(approval|confirmation|consent)",
        r"\b(mandatory|forbidden|prohibited)\b",
    )
]

# 「这一条像不像是模型自己的推断」
_HEDGE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"我认为|我觉得|应该是|大概是|可能是|大概是|估计|猜测|推测|看起来|似乎",
        r"\b(I think|I believe|probably|likely|may be|might be|seems?|appears?|guess|assume)\b",
    )
]


def is_constraint(text: str) -> bool:
    """这段文本是否含硬约束。

    用于两类判断：
      1. 系统提示整体 → 全部按 constraint 处理（它本来就全是规则）
      2. 摘要候选 → 若摘要器产出了带约束的句子，重新按 verbatim 保留
    """
    if not text:
        return False
    return any(p.search(text) for p in _CONSTRAINT_PATTERNS)


def is_hedged(text: str) -> bool:
    """这段文本是否带有"这是推测"的语言标记。"""
    if not text:
        return False
    return any(p.search(text) for p in _HEDGE_PATTERNS)


def classify(msg: Message) -> str:
    """给一条消息判定内容类型。

    **只用结构信息与轻量文本特征，不调模型** —— 书 §5 的纪律：
    治理层不应与产出层共享同一上下文，分类也必须确定性可复现。
    """
    # 系统消息 = 规则本体
    if msg.role == ROLE_SYSTEM:
        return Kind.CONSTRAINT

    # 压缩摘要自身：标为 episode（它已经是压缩产物，不该再被二次压缩成更糊的东西）
    if msg.meta.get("kind") == "compaction":
        return Kind.EPISODE

    # 环境状态栏：不是事实也不是约束，是被动注入的元信息
    if msg.meta.get("kind") == "status":
        return Kind.EPISODE

    # 工具结果 = 环境事实（最有价值，必须结构化保留）
    if msg.role == ROLE_TOOL:
        return Kind.FACT

    # 用户消息：可能是任务描述（含约束）也可能是补充说明
    if msg.role == ROLE_USER:
        if msg.meta.get("kind") == "status":
            return Kind.EPISODE
        if is_constraint(msg.content or ""):
            return Kind.CONSTRAINT
        return Kind.FACT

    # assistant：思考可丢，正文要看内容
    if msg.role == ROLE_ASSISTANT:
        if msg.tool_calls:
            # 带工具调用的回复 = 一次动作（决策）
            return Kind.DECISION
        return Kind.REASONING

    return Kind.EPISODE


# ══════════════════════════════════════════════════════════════════
# 压缩计划
# ══════════════════════════════════════════════════════════════════
class CompactPlan:
    """一次压缩的**不可变计划**：哪条保留、哪条进摘要器。

    为什么要先出计划再执行：让「哪些东西被压缩了」这件事**可审计**。
    AHE（arXiv 2604.25850）的经验是，只有把每次改动变成可核验的清单，
    迭代才不会退化成盲目试错。压缩同理 —— 事后能查"第 42 条为什么没了"。
    """

    def __init__(self) -> None:
        self.verbatim: List[Message] = []      # 原样保留（constraint）
        self.to_summarize: List[Message] = []  # 交给摘要器
        self.dropped: List[Message] = []       # 直接丢弃（reasoning）
        self.by_kind: Dict[str, int] = {}

    def to_json(self) -> Dict[str, Any]:
        return {
            "verbatim": [m.seq for m in self.verbatim],
            "to_summarize": [m.seq for m in self.to_summarize],
            "dropped": [m.seq for m in self.dropped],
            "by_kind": dict(self.by_kind),
        }

    @property
    def saved_count(self) -> int:
        return len(self.dropped)


def plan_compaction(messages: Sequence[Message]) -> CompactPlan:
    """把一段历史拆成「原样保留 / 待摘要 / 直接丢弃」。

    规则（都来自上面的类型表）：
      · constraint → verbatim（**永不进摘要器**）
      · fact / decision → 待摘要（结构化压缩，保留结论）
      · reasoning → 直接丢弃（思考不需要回放，书 §5 也不主张）
      · episode → 待摘要
    """
    plan = CompactPlan()
    for m in messages:
        k = classify(m)
        plan.by_kind[k] = plan.by_kind.get(k, 0) + 1
        if k == Kind.CONSTRAINT:
            plan.verbatim.append(m)
        elif k == Kind.REASONING:
            plan.dropped.append(m)
        else:
            plan.to_summarize.append(m)
    return plan


# ══════════════════════════════════════════════════════════════════
# 摘要拼装：区分「环境事实」与「模型推断」
# ══════════════════════════════════════════════════════════════════
def build_summary_text(plan: CompactPlan,
                       summarizer,
                       max_fact_chars: int = 300) -> str:
    """生成压缩块的正文。

    结构刻意分成两栏：

        [硬约束 · 原样保留]
        ...（一条一行，**不过摘要器**）

        [已发生的事]
        ...（由 summarizer 产出，每条都带来源标注）

    为什么约束要放在**前面**：上下文末尾的内容对模型影响更大，
    但约束是"任何时候都不能违反"的，放前面 + 明确标记为约束，
    比藏在摘要里更稳。而"最近发生了什么"才需要靠后。
    """
    parts: List[str] = []

    # ---- 1. 硬约束：原样 ----
    if plan.verbatim:
        lines = ["[硬约束 · 原样保留 · 不可被后续摘要改写]"]
        for m in plan.verbatim:
            text = (m.content or "").strip()
            if not text:
                continue
            # 约束可能很长（整段系统提示），截到合理长度但**不改变措辞**
            if len(text) > 1200:
                text = text[:1200] + "\n…（以下省略，原文见 rollout）"
            lines.append(text)
        parts.append("\n".join(lines))

    # ---- 2. 事件摘要：带来源标注 ----
    if plan.to_summarize:
        try:
            raw = summarizer(plan.to_summarize)
        except Exception as e:
            # 摘要器失败绝不能导致上下文丢失 —— 退化成"结构化清单"
            raw = _fallback_summary(plan.to_summarize, max_fact_chars)
            raw += f"\n（注：摘要器异常，已退回结构化清单。原因：{type(e).__name__}）"

        parts.append("[已发生的事 · 标注了来源]")
        parts.append(
            "（说明：`[环境]` 表示有工具输出直接支持；"
            "`[推断]` 表示这是压缩时的推测，未经环境验证，"
            "**不要把 `[推断]` 当作已确认的事实**。）"
        )
        parts.append(raw)

    return "\n\n".join(parts)


def _fallback_summary(messages: Sequence[Message], max_chars: int) -> str:
    """摘要器不可用时的确定性退路。

    不追求好看，只保证**信息不丢、来源可辨**。
    每条一行：`[环境] seq=12 tool=read_file: 读到 34 行，首行是 ...`
    """
    lines: List[str] = []
    for m in messages:
        if m.role == ROLE_TOOL:
            body = (m.content or "").replace("\n", " ").strip()
            ok = m.meta.get("ok")
            flag = "成功" if ok is not False else "失败"
            lines.append(f"[环境] seq={m.seq} 工具结果（{m.meta.get('name') or m.role}，{flag}）："
                         f"{body[:max_chars]}")
        elif m.role == ROLE_ASSISTANT:
            body = (m.content or m.reasoning or "").replace("\n", " ").strip()
            if not body:
                continue
            tag = "[环境]" if m.tool_calls else "[模型]"
            act = ""
            if m.tool_calls:
                act = "调用 " + ", ".join(c.name for c in m.tool_calls) + "；"
            lines.append(f"[{tag.strip('[]')}] seq={m.seq} {act}{body[:max_chars]}")
        else:
            body = (m.content or "").replace("\n", " ").strip()
            if body:
                lines.append(f"[环境] seq={m.seq} {body[:max_chars]}")
    return "\n".join(lines)


def audit_summary(summary: str) -> Dict[str, Any]:
    """自检压缩产物。

    检查两件事（都来自 SJC 的教训）：
      1. **约束是否还在** —— 输入里有约束、输出里没有 = 压缩违规
      2. **有多少条是纯推断** —— 推断比例过高说明摘要器在编
    """
    lines = [l for l in (summary or "").split("\n") if l.strip()]
    hedged = [l for l in lines if is_hedged(l)]
    return {
        "lines": len(lines),
        "constraint_lines": sum(1 for l in lines if is_constraint(l)),
        "hedged_lines": len(hedged),
        "hedged_ratio": round(len(hedged) / max(1, len(lines)), 3),
        "has_constraint_block": "[硬约束" in (summary or ""),
        "unlabeled_claims": [
            l[:100] for l in lines
            if not l.startswith("[环境]") and not l.startswith("[推断]")
            and not l.startswith("[硬约束") and not l.startswith("[已发生")
            and not l.startswith("（")
        ][:5],
    }
