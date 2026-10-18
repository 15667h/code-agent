"""完成前检查（PreCompletion）—— 在 agent 宣布"做完了"之前拦一次。

## 为什么这是投入产出比最高的一环

LangChain 在 Terminal-Bench 2.0（89 个任务）上只改 harness、模型固定为
`gpt-5.2-codex`：

| 配置 | 分数 | 排名 |
| --- | --- | --- |
| 默认 harness | 52.8% | Top 30 开外 |
| 加三个中间件后 | **66.5%** | **Top 5** |

其中第一项就是 `PreCompletionChecklistMiddleware` —— **在 agent 退出前拦截，
强制对照任务规格做一次验证**。一行模型代码没动，**+13.7 个百分点**。
（来源：LangChain, *Improving Deep Agents with harness engineering*, 2026-02-17）

## 它为什么有用：agent 的"完成"是个自我声明

模型宣布完成时，它给出的其实是一个**声明**，不是证据。
书 §5 反复强调：**「给出了回答」不等于「完成了任务」**。

最典型的三种"看起来完成了"：
  1. **说了要改，但从没动手** —— 回复里写"我把 X 改成 Y 了"，事件流里没有 apply_patch
  2. **改了，但没验证** —— 真的落盘了，但没有跑测试/构建/语法检查
  3. **验证了，但没过** —— 跑是跑了，退出码非 0，然后宣布完成

这三种都会产生一个**读起来毫无破绽的最终回答**。这正是书 §1 警告的
"上下文残缺时典型的失败不是报错退出，而是一个看上去毫无破绽的答案"。

## 与 GoalVerifier 的分工（两者互补，不重复）

| | 判据来源 | 回答的问题 |
| --- | --- | --- |
| `GoalVerifier` | **调用方声明的目标条件** | "你一开始说要做的那些事，做了吗" |
| `PreCompletion` | **任务文本 + 会话过程** | "按这类任务的一般要求，该做的做了吗" |

GoalVerifier 依赖外部传入条件；没传条件时它完全透明。
PreCompletion 不需要任何外部输入，从 prompt 与轨迹里自己推断 ——
**它是那个"永远在场"的兜底闸门**。

## 纪律：只用结构化事实

与 `verify.py` 同一条铁律 —— **不解析模型自由生成的文本**。
检查项的输入只有：
  · 任务文本（这是用户给的，不是模型生成的，可信度等同系统输入）
  · 工具调用记录（name + args，结构化）
  · 工具结果的结构化字段（ok / exit_code / files_changed）
  · 文件系统事实（落盘文件是否真的存在）

绝不去读模型的 reasoning 或 content 来判断"它是不是说做完了"。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .protocol import ToolCall, ToolResult


# ══════════════════════════════════════════════════════════════════
# 任务意图识别：从**用户的任务文本**里推断"这类任务该做什么"
# ══════════════════════════════════════════════════════════════════
# 注意这里读的是 prompt（用户输入），**不是模型的输出**。
# 用户的任务描述是可信输入；模型的自我声明不是。

# 判据本身在 `intent.py`（单一来源）。
#
# 为什么改成导入：审计发现写意图词在本文件 41 个、在 verify.py 22 个，
# **互相缺词**（verify 缺 `实现/修复/fix`，本文件缺 `保存/补丁`）。
# 后果是同一轨迹会得到矛盾结论 —— 任务「修好这个 bug」在这里判为写任务，
# 在 verify 那边则可能判为不要求改动。
# 判据只能有一份，这是本项目第四次因为"两处各写一遍"而漂移。
from .intent import (PATH_RE as _PATH_RE,
                     READONLY_INTENT as _READONLY_INTENT,
                     VERIFY_CMDS as _VERIFY_CMDS,
                     WRITE_INTENT as _WRITE_INTENT,
                     mentioned_files as _mentioned_files,
                     mentioned_paths as _mentioned_paths,
                     wants_readonly as _wants_readonly,
                     wants_write as _wants_write)

# 这些意图要求"必须有一次真正的写操作"（不是只看不改）
_VERIFY_HINT = (
    "测试", "验证", "跑通", "通过", "确保", "检验", "检查", "确认能",
    "test", "verify", "validate", "pass", "ensure", "check that",
)

# 纯问答/解释类任务的形态标记。这些任务**不该**被写检查拦住 ——
# 误拦的代价是凭空多跑两轮、甚至把问答拖成超时。
# 为什么需要它：写意图里必须包含单字「改」（中文里它是最核心的改动动词，
# 实测「把 a.py 改成 2」这种说法极常见），但「改」也会出现在
# 「这个项目改用的是什么框架？」这类**纯提问**里。
_QUESTION_SHAPE = re.compile(
    r"[?？]\s*$"                                    # 以问号结尾
    r"|^(什么|哪些|哪个|哪里|怎么|如何|为何|为什么|是不是|有没有|能否|可以|是否)"
    r"|^(what|which|where|how|why|who|when|is|are|does|do|can|could|should)\b"
    r"|(是什么|在哪|怎么用|如何用|介绍一下|解释一下|讲讲|说明一下)"
    r"|\b(explain|describe|tell me|what is|how do|how to|difference between)\b",
    re.IGNORECASE,
)

def looks_like_edit_task(prompt: str) -> bool:
    """这条任务是否要求"改动工作区"。

    只看用户给的 prompt。判据是词面级（保守），
    认不出就当作"读类任务"，不施加写检查 —— 宁可漏检也不误拦。

    两道排除（都为了压住误拦）：
      1. **明确说了不要改** → 一定不是写任务
      2. **纯提问形态**（问号结尾 / 疑问词开头）→ 不是写任务，
         即使句子里出现了"改"字（"这个项目改用的是什么框架？"）
    """
    text = (prompt or "").strip()
    if not text:
        return False
    low = text.lower()

    if _wants_readonly(text):
        return False

    if not _wants_write(text):
        return False

    # 有写意图，但整句是提问形态 → 判为问答任务，不施加写检查。
    # 这一条就是单字「改」的护栏。
    if _QUESTION_SHAPE.search(text):
        return False

    return True


def wants_verification(prompt: str) -> bool:
    """用户是否明确要求"跑通/验证"。"""
    low = (prompt or "").lower()
    return any(k in low for k in _VERIFY_HINT)


def mentioned_paths(prompt: str) -> List[str]:
    """从任务文本里提取提到的文件路径（实现见 `intent.mentioned_paths`）。

    **这是"所有路径样 token"，包含模块名与 MIME 类型。**
    要做"任务点名了哪些**文件**"的判定，请用 `mentioned_files`。
    """
    return _mentioned_paths(prompt)


def mentioned_files(prompt: str) -> List[str]:
    """只要看起来是**仓库文件**的路径。

    `named_path_untouched` 必须以它为准 —— 把 Python 模块名
    （`ansible.module_utils.urls`）或 MIME 类型（`text/plain`）
    当成"你点名了却没改"的文件，会让诚实完成被误拦。
    实测：SWE-bench Pro 的 6 道真实题里有 1 道栽在这里。
    """
    return _mentioned_files(prompt)


# ══════════════════════════════════════════════════════════════════
# 会话事实：从轨迹里收集的结构化记录
# ══════════════════════════════════════════════════════════════════
@dataclass
class TraceFacts:
    """一次会话的**结构化事实**。

    刻意只存结构化的东西：工具名、参数里的路径、退出码、落盘清单。
    不含任何模型文本 —— 存了就会有人忍不住去解析它。
    """

    # 每次工具执行：(tool, args, ok, exit_code, files_changed)
    executions: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.executions)

    def tools_used(self) -> List[str]:
        seen: List[str] = []
        for e in self.executions:
            if e["tool"] not in seen:
                seen.append(e["tool"])
        return seen

    def wrote_anything(self) -> bool:
        """是否发生过**成功的**写操作。

        判据是 `files_changed` 非空，而不是"调过 apply_patch" ——
        实测模型常用 shell 重定向建文件，只看工具名会漏。
        """
        return any(e["ok"] and e.get("files_changed") for e in self.executions)

    def wrote_paths(self) -> List[str]:
        out: List[str] = []
        for e in self.executions:
            if not e["ok"]:
                continue
            for p in (e.get("files_changed") or []):
                if p not in out:
                    out.append(p)
        return out

    def attempted_writes(self) -> List[Dict[str, Any]]:
        """所有**写意图**的执行（含失败的）。

        写意图的判定：apply_patch 一定算；shell 则看结构化字段
        `side_effect`，而不是去正则匹配命令文本。
        """
        out = []
        for e in self.executions:
            if e["tool"] == "apply_patch":
                out.append(e)
            elif e["tool"] == "shell" and e.get("side_effect"):
                out.append(e)
        return out

    def failed_writes(self) -> List[Dict[str, Any]]:
        return [e for e in self.attempted_writes() if not e["ok"]]

    def verification_runs(self) -> List[Dict[str, Any]]:
        """执行过的**验证类**命令。

        判据刻意保守：命令里出现已知的测试/构建可执行名才算。
        不猜"这条命令是不是在验证" —— 猜错会产生假失败。
        """
        out = []
        for e in self.executions:
            if e["tool"] != "shell":
                continue
            cmd = str((e.get("args") or {}).get("command") or "").lower()
            if any(v in cmd for v in _VERIFY_CMDS):
                out.append(e)
        return out

    def has_passing_verification(self) -> bool:
        """是否留下过**真正意义上的**验证证据（退出码 0）。

        为什么不把 `apply_patch` 之后的即时 lint 算进来：
        `quick_lint` 只做语法级检查（py_compile / json.load），它证明
        "文件能被解析"，**不证明"改动的行为是对的"**。
        早期实现把 lint 当成"有验证"，于是"改了文件但没跑测试"这条
        警告被整个吞掉 —— 正好放走了这道闸门最想抓的场景
        （书 §5 把"测试通过"而非"代码写完"定义为完成标准）。
        """
        return any(e["ok"] and e.get("exit_code") == 0
                   for e in self.verification_runs())


# ══════════════════════════════════════════════════════════════════
# 检查项
# ══════════════════════════════════════════════════════════════════
@dataclass
class Finding:
    """一条未通过的检查。"""

    code: str
    severity: str          # block | warn
    detail: str
    remedy: str            # 给模型的**具体**补救指示
    evidence: List[str] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {"code": self.code, "severity": self.severity,
                "detail": self.detail, "remedy": self.remedy,
                "evidence": self.evidence}


@dataclass
class Checkpoint:
    """一次完成前检查的结果。"""

    findings: List[Finding] = field(default_factory=list)

    @property
    def blocking(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "block"]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity != "block"]

    @property
    def ok(self) -> bool:
        return not self.blocking

    def to_json(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "blocking": [f.to_json() for f in self.blocking],
            "warnings": [f.to_json() for f in self.warnings],
        }

    def feedback(self) -> str:
        """把未通过项渲染成给模型的**可执行**反馈。

        纪律（书 §5「反馈越结构化越好」）：每条都必须给出
        **具体做什么**，而不是"请检查一下"。含糊的反馈只会换来
        一轮无意义的重复。
        """
        items = self.blocking
        if not items:
            return ""
        lines = ["[这不是工具结果，是完成前检查] 你准备结束任务，"
                 "但检查发现以下动作没有发生过："]
        for i, f in enumerate(items, start=1):
            lines.append(f"  {i}. {f.detail}")
            if f.evidence:
                lines.append(f"     依据：{'；'.join(f.evidence)}")
            lines.append(f"     要做的：{f.remedy}")
        lines.append("完成这些之后再说任务完成。如果某项确实不适用"
                     "（例如任务文本被误解），请明确说明理由。")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# 中间件本体
# ══════════════════════════════════════════════════════════════════
class PreCompletionMiddleware:
    """完成前检查。

    ## 为什么是"中间件"而不是"又一层验证"

    LangChain 的三个中间件是横切的：它们不改模型、不改工具，
    只在**流程的特定时刻**插入一道判断。本类同理 —— 它不参与执行，
    只在 `run()` 即将返回 `completed` 的那一刻被问一句
    「真的可以结束了吗」。

    ## 默认保守：不制造假失败

    每条检查都要求**明确的、结构化的反证**才判定为问题。
    宁可漏检（退回原来的行为），不可误拦（凭空多跑几轮、
    甚至把本来正确的任务拖成超时）。

    误拦的危害在本项目里被实测证实过：GoalVerifier 曾经恒假，
    每次都报"未达成"，于是闸门退化成噪音。
    这里引以为戒：**判定器先要能在正确时放行。**
    """

    def __init__(self, workspace: str, max_rounds: int = 2) -> None:
        self.workspace = os.path.realpath(workspace)
        # 上限与 loop 侧的 goal/intent 纠正一致取 2：
        # 三次机会足以补一次漏掉的动作，再多次就说明模型确实做不到。
        self.max_rounds = max_rounds
        self._rounds = 0

    # ---------- 计数 ----------
    @property
    def exhausted(self) -> bool:
        return self._rounds >= self.max_rounds

    @property
    def rounds(self) -> int:
        """已用掉的纠正轮数（供上层如实汇报，如"已尝试 N 次仍未补齐"）。"""
        return self._rounds

    def note_round(self) -> None:
        self._rounds += 1

    # ---------- 主入口 ----------
    def check(self, prompt: str, facts: TraceFacts,
              goal_unmet: Optional[Sequence[str]] = None) -> Checkpoint:
        """跑一遍检查清单。

        `goal_unmet` 由外部（loop 的 GoalVerifier）传入，本中间件不自己判断目标 —— 
        那是 GoalVerifier 的职责，这里只把它的结论**并进同一份反馈**，
        避免给模型发两条互相割裂的纠正消息。
        """
        cp = Checkpoint()

        # 逐条跑。每条都是独立的、可单独测试的函数。
        self._check_promise_without_action(prompt, facts, cp)
        self._check_write_failed_silently(prompt, facts, cp)
        self._check_verification(prompt, facts, cp)
        self._check_claimed_path_missing(prompt, facts, cp)
        self._check_goal_gap(goal_unmet, cp)

        return cp

    # ---------- 各项检查 ----------
    def _check_promise_without_action(self, prompt: str, facts: TraceFacts,
                                      cp: Checkpoint) -> None:
        """任务要求改动，但**一次写操作都没发生**。

        这是最常见的形态，也是危害最大的：模型写一段"我已经把
        X 改成 Y 了"的解释，事件流里什么也没有，最终回答读起来
        毫无破绽。
        """
        if not looks_like_edit_task(prompt):
            return
        if facts.attempted_writes():
            return
        cp.findings.append(Finding(
            code="no_write_attempt",
            severity="block",
            detail="任务要求对工作区做改动，但整个会话里**没有任何写操作**。",
            remedy=("用 apply_patch 真正落盘改动（或用 shell 执行会产生文件改动的命令），"
                    "不要让改动只停留在回答的文字里。"),
            evidence=[f"任务文本含改动意图；本会话工具调用共 {facts.total} 次，"
                      f"使用过：{', '.join(facts.tools_used()) or '无'}"],
        ))

    def _check_write_failed_silently(self, prompt: str, facts: TraceFacts,
                                     cp: Checkpoint) -> None:
        """尝试过写，但**全部失败**，且没有任何一次成功。

        区别对待"没试"与"试了但失败"：后者更危险，因为模型容易把
        "我执行了补丁"与"补丁生效了"混为一谈。
        """
        attempts = facts.attempted_writes()
        if not attempts or facts.wrote_anything():
            return
        codes = []
        for e in attempts:
            if e.get("error_kind"):
                codes.append(str(e["error_kind"]))
            elif e.get("exit_code") not in (None, 0):
                codes.append(f"exit_code={e['exit_code']}")
        cp.findings.append(Finding(
            code="writes_all_failed",
            severity="block",
            detail=f"尝试了 {len(attempts)} 次写操作，但**没有一次成功落盘**。",
            remedy=("先读回目标文件的**当前实际内容**（read_file），"
                    "据此修正补丁后重试。若工具报的是定位歧义，"
                    "请多带几行前后文或在 `@@` 后写更贴近的锚点。"),
            evidence=[f"失败原因：{', '.join(codes) if codes else '未记录'}"] if codes else [],
        ))

    def _check_verification(self, prompt: str, facts: TraceFacts,
                            cp: Checkpoint) -> None:
        """验证证据检查 —— 一个检查，三档严重度。

        为什么要分档，而不是"没验证就拦"：

        | 情形 | 严重度 | 理由 |
        | --- | --- | --- |
        | 有通过的验证命令 | 不报 | 证据充分 |
        | 跑过但没通过 | **block** | 已经在跑了，说明需要它；没过就不算完成 |
        | 没跑，但用户明确要求跑通 | **block** | 这是用户写下来的完成标准 |
        | 没跑，用户也没要求 | warn | 改文档/改配置确实没有可跑的验证，硬拦会制造大量假失败 |

        书 §5 把「测试通过」而非「代码写完」定义为完成标准。
        但**只有用户要求时才算用户的标准** —— 否则就是我们替用户立规矩，
        那会产生假失败，而假失败会让整道闸门退化成噪音。
        """
        if not facts.wrote_anything():
            return

        # ── 情形一：有通过的验证 → 干净通过 ──
        if facts.has_passing_verification():
            return

        runs = facts.verification_runs()
        required = wants_verification(prompt)

        # ── 情形二：跑过，但一次都没通过 ──
        # 最容易被忽略的一类：事件流里有 pytest、有输出，看着"验证过了"，
        # 但退出码非 0。跑过本身就说明这项检查是需要的。
        if runs:
            last = runs[-1]
            cp.findings.append(Finding(
                code="verification_not_passing",
                severity="block",
                detail=(f"跑了 {len(runs)} 次验证命令，但没有一次以退出码 0 结束。"),
                remedy=("读回失败输出（完整输出通常在 full_output_path），"
                        "修掉真因后重跑，直到退出码为 0。"
                        "如果失败与本次改动无关（既有问题），请在最终回答里"
                        "明确指出是哪一条、以及为什么可以判定它与本次任务无关。"),
                evidence=[f"最后一次退出码 {last.get('exit_code')}"
                          + (f"，命令：{str((last.get('args') or {}).get('command'))[:80]}"
                             if last.get("args") else "")],
            ))
            return

        # ── 情形三 / 四：一次都没跑 ──
        cp.findings.append(Finding(
            code="no_verification",
            severity="block" if required else "warn",
            detail=("任务要求改动是**通过验证的**，但改动落盘后没有跑过任何验证。"
                    if required else
                    "改动已经落盘，但没有留下任何验证证据。"),
            remedy=("跑一次能证明改动可用的检查（`python -m py_compile <文件>`、"
                    "`npm test`、项目自带的测试命令），并让退出码为 0。"
                    "如果这个改动确实无法自动验证（如改文档），"
                    "请在最终回答里说明这一点。"),
            evidence=[f"已落盘：{', '.join(facts.wrote_paths()[:5])}",
                      f"验证命令执行 0 次{'（用户明确要求验证）' if required else ''}"],
        ))

    def _check_claimed_path_missing(self, prompt: str, facts: TraceFacts,
                                    cp: Checkpoint) -> None:
        """任务点名要处理的文件，**在磁盘上不存在**。

        只对"写类任务"生效，并且要求这个路径确实没被任何成功写操作覆盖过。
        用文件系统事实判定，不读模型的话。
        """
        if not looks_like_edit_task(prompt):
            return
        # 用 `mentioned_files` 而不是 `mentioned_paths`：
        # 后者会把 Python 模块名（`ansible.module_utils.urls`）与
        # MIME 类型（`text/plain`）也算进来，于是"你点名了却没改"误报，
        # 诚实完成被拦。实测：SWE-bench Pro 6 道真实题里 1 道栽在这里。
        named = mentioned_files(prompt)
        if not named:
            return
        written = facts.wrote_paths()
        missing = []
        for want in named:
            full = self._resolve(want)
            if full is None:
                continue                      # 越界或无法解析，交给 policy 层管
            if os.path.exists(full):
                continue                      # 已存在（可能是改已存在的文件）
            if any(_covers(want, w) for w in written):
                continue                      # 已经写过（含新文件）
            # 该文件既不在磁盘上，也没有被写过 → 任务点名的事没做
            missing.append(want)
        if not missing:
            return
        cp.findings.append(Finding(
            code="named_path_untouched",
            severity="block",
            detail="任务点名的这些文件既不在磁盘上，也没有被任何写操作创建过：",
            remedy=("确认这些路径是否写错了（大小写、目录层级、扩展名），"
                    "然后真正创建/写入它们。如果路径其实应该不同，"
                    "请在最终回答里说明正确的路径是什么。"),
            evidence=missing[:8],
        ))

    def _check_goal_gap(self, goal_unmet: Optional[Sequence[str]],
                        cp: Checkpoint) -> None:
        """把 GoalVerifier 的缺口并进同一份反馈。"""
        if not goal_unmet:
            return
        cp.findings.append(Finding(
            code="goal_unmet",
            severity="block",
            detail="以下在开始时声明的目标条件没有挂上任何工具证据：",
            remedy="实际调用工具完成它们，让事件流里留下可复核的痕迹。",
            evidence=list(goal_unmet)[:8],
        ))

    # ---------- 辅助 ----------
    def _resolve(self, rel: str) -> Optional[str]:
        """把相对路径解析成工作区内的绝对路径；越界/绝对路径返回 None。"""
        p = (rel or "").strip().replace("\\", "/")
        if not p or os.path.isabs(p):
            return None
        full = os.path.realpath(os.path.join(self.workspace, p))
        if full != self.workspace and not full.startswith(self.workspace + os.sep):
            return None
        return full


def _covers(pattern: str, actual: str) -> bool:
    """写入路径是否覆盖了任务点名的路径（实现见 `intent.path_covers`）。

    迁到 intent 是因为证书也要用同一套规则 —— 两处各写一遍必然漂移
    （实测：证书用严格相等，于是"任务说 a.py、实际写 src/a.py"
    被判成"写的不是该写的地方"，一个正确完成被拦）。
    """
    from .intent import path_covers
    return path_covers(pattern, actual)
