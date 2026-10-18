"""证据携带终止（ETC）—— 完成证书与确定性校验。

依据：arXiv 2608.23623《When May an Agent Stop? Evidence-Carrying Termination
for Tool-Using LLMs》。

## 论文要解决的问题

现有停止信号都很容易伪造：
模型输出 `DONE`、自查清单写 `APPROVED`、启发式观察到"答案很长且调用过工具"、
LLM 裁判觉得"看起来合理"。
**这些都不能说明：哪一次观测支持了哪一条最终声明。**

方案：只有当**类型化证书**把每一条必需声明绑定到**有效、在范围内的轨迹证据**，
且**确定性重放**能重建所声称的值时，agent 才能返回 `COMPLETE`。

实验结果（全文最硬的数字）：

| 指标 | ECT | 对照（终止评论器） |
|---|---|---|
| 不安全完成（48 任务 × 8 种故障） | **0/288** | 252/288 |
| 过早无支持终止（576 轨迹） | **0/66** | 40/66 |

## 本实现的三个取舍（都写清了理由）

### 1. 证书**不由模型填写**

这一点最关键。如果让模型自己写"我完成了 X，证据是第 42 条"，
那它同样可以写错、写假、或者干脆编一个不存在的 seq —— 只是把
"输出 DONE" 换成了 "输出一个 JSON"，**伪造成本没有变化**。

所以本实现的证书是**推导出来的**：从 `TraceFacts`（只含结构化字段）
与事件流里逐条重建。模型能影响的只有它实际做了什么，
而不是它声称证明了什么。这也是论文 "binding" 一词的含义 ——
绑定是**轨迹到声明的映射**，不是声明对自己的背书。

### 2. 「确定性重放」= 重新核一遍**不可变事实**，不是重跑工具

论文原文用"封闭变换语言"精确重放派生声明。我们做不到那个强度
（那需要一份形式化的派生语言与解释器），也不该假装做到。

我们的"重放"是：对每条证据，回到**已落盘的事件流**里查
- 那个 seq **存在**吗？
- 它的类型是 `tool.output` 吗？
- 它的 `ok` 是 true 吗？（**失败的工具输出不构成证据**）
- 它声明的产物（`files_changed` / `exit_code`）与证书里写的一致吗？
- 它落在**声明的范围**内吗？（工作区内 / 沙箱允许的动作）

这四项加起来的强度弱于论文（不覆盖"派生链"，只覆盖"单点事实"），
但它是**真能拦住假完成的那一部分**——而"假完成"正是我们要治的病。
函数名与文档都如实写明这一层差异，不冒称等价。

### 3. 拒绝时给**原因码**，而不是一句话

论文的 "拒绝" 维度：任一检查失败 → 返回 `continue` + **原因码**。
原因码是给模型看的结构化反馈 —— 它能让模型**针对性修正**
（"你引用的 seq=42 不存在，请检查你是否真的执行了那一步"），
而一句"任务未完成"只能让它原地打转。

## 与既有三道闸门的分工

| 机制 | 依据 | 回答 |
|---|---|---|
| `Verifier` | 单次工具结果 | 这次调用本身成功吗 |
| `GoalVerifier` | 调用方声明的条件 | 你说要做的事有证据了吗 |
| `PreCompletion` | 任务文本 + 会话过程 | 按这类任务该做的做了吗 |
| **`Certificate`（本模块）** | **证书 ↔ 轨迹的绑定** | **你声称完成的每一件事，能指到轨迹里的哪一条？** |

前三者回答"该做的做了没有"，证书回答"**你说做了的，凭什么**"。
它们是互补的：证书不产生新的要求，它把已有事实**强制绑定到具体 seq**，
使"声称"与"事实"之间没有空隙可钻。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from .policy import SandboxMode
from .intent import mentioned_files

# ══════════════════════════════════════════════════════════════════
# 原因码
# ══════════════════════════════════════════════════════════════════
class RejectCode(str, Enum):
    """证书被拒的原因码。

    **每条都要能让模型知道下一步改什么。** 这不是把错误分个类就完事 ——
    原因码是给模型的输入，写不出"该改什么"的码等于没提供信息。
    """

    NO_ARTIFACTS = "no_artifacts"              # 一次成功的工具调用都没有
    WRITE_CLAIMED_NO_EVIDENCE = "write_claimed_no_evidence"
    # 声明里说改了什么，但没有任何成功的写操作留下产物
    SEQ_NOT_FOUND = "seq_not_found"            # 引用的 seq 在轨迹里不存在
    SEQ_NOT_TOOL_OUTPUT = "seq_not_tool_output"  # 引用的那一条不是工具结果
    EVIDENCE_FAILED = "evidence_failed"        # 引用的那一条 ok=False
    ARTIFACT_MISMATCH = "artifact_mismatch"    # 证书写的产物与证据记录不一致
    OUT_OF_SCOPE = "out_of_scope"              # 证据动作超出声明范围
    UNSAFE_COMPLETION = "unsafe_completion"    # 声称完成但整体状态不是 completed
    EMPTY_CLAIM = "empty_claim"                # 证书里没有任何可核验的声明

    @property
    def remedy(self) -> str:
        """给模型看的**下一步**。刻意写成动作指令，不是解释。"""
        return {
            self.NO_ARTIFACTS:
                "你还没有成功执行过任何工具。请实际调用工具完成任务，"
                "而不是只给出结论。",
            self.WRITE_CLAIMED_NO_EVIDENCE:
                "你的回答里提到做了改动，但轨迹里没有任何成功的写操作。"
                "请实际调用 apply_patch 或 shell 落盘，然后再收尾。",
            self.SEQ_NOT_FOUND:
                "证书引用的 seq 在轨迹里不存在。不要凭空构造 seq，"
                "只引用你**实际**看到过的工具结果。",
            self.SEQ_NOT_TOOL_OUTPUT:
                "证书引用的 seq 不是一条工具结果。证据只能来自工具执行。",
            self.EVIDENCE_FAILED:
                "证书引用的那条工具结果是**失败**的。失败的操作不能作为完成证据。"
                "请修掉失败原因后重试，或改用一个能成功的做法。",
            self.ARTIFACT_MISMATCH:
                "证书里写的产物与轨迹记录的产物不一致。"
                "请只声明你实际落盘的文件。",
            self.OUT_OF_SCOPE:
                "证据动作超出了本次任务的声明范围。"
                "请在任务范围内完成，不要做任务没要求的事。",
            self.UNSAFE_COMPLETION:
                "当前运行状态不是 completed，不能签发完成证书。",
            self.EMPTY_CLAIM:
                "证书里没有任何可核验的声明。若任务确实无需改动（如纯查询），"
                "请明确说明这是只读任务，并给出你读取过的依据。",
        }[self]


# ══════════════════════════════════════════════════════════════════
# 声明与证据
# ══════════════════════════════════════════════════════════════════
@dataclass
class Evidence:
    """一条证据 = 轨迹里的一次工具执行。

    字段全部来自 `TraceFacts`（结构化），**没有一个来自模型文本**。
    """

    seq: int
    tool: str
    ok: bool
    exit_code: Optional[int] = None
    files_changed: List[str] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "tool": self.tool,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "files_changed": self.files_changed,
        }


@dataclass
class Claim:
    """一条"我声称完成了"的声明，及其绑定的证据。

    `kind` 只有两种，刻意不细分：
      · `wrote`  —— 改了工作区（需要写类证据）
      · `ran`    —— 跑了命令（需要 shell 证据 + 退出码）
    细分到"修了函数""建了测试"这种粒度需要语义理解，那是模型擅长、
    但我们不该采信的地方（它正是被注入攻击的入口）。
    """

    kind: str                       # wrote | ran
    subject: str                    # 文件路径 / 命令
    evidence: List[Evidence] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "subject": self.subject,
            "evidence": [e.to_json() for e in self.evidence],
        }


@dataclass
class Problem:
    code: RejectCode
    detail: str
    subject: str = ""
    seq: Optional[int] = None

    def to_json(self) -> Dict[str, Any]:
        return {
            "code": self.code.value,
            "detail": self.detail,
            "subject": self.subject,
            "seq": self.seq,
            "remedy": self.code.remedy,
        }

    def to_text(self) -> str:
        loc = f"（{self.subject}）" if self.subject else ""
        return f"[{self.code.value}] {self.detail}{loc} → {self.code.remedy}"


@dataclass
class Certificate:
    """完成证书。

    ## 四个检查维度的落点（对应论文的表）

    | 论文术语 | 本实现的字段 |
    |---|---|
    | 绑定 | `task_digest` + `ledger_digest`（轨迹摘要） |
    | 回执 | `claims[].evidence[]`（每条带 seq/tool/ok/产物） |
    | 重放 | `replay()` 回到事件流逐条复核 |
    | 拒绝 | `problems[]`（带原因码 + remedy） |

    `issues` 为空且 `claims` 非空 = 可以签发 COMPLETE。
    """

    task_digest: str = ""
    ledger_digest: str = ""
    claims: List[Claim] = field(default_factory=list)
    problems: List[Problem] = field(default_factory=list)
    # 签发时的运行状态
    status: str = ""
    # 证书自身的指纹（覆盖全部字段），用于外部核对"这份证书没被改过"
    digest: str = ""

    # ---------- 判定 ----------
    @property
    def ok(self) -> bool:
        """能否签发。

        ## 这里曾有一个真实的缺陷，值得留在代码里

        早期实现写的是 `not self.problems and bool(self.claims)`。
        于是**只读任务**（本来就该没有写/跑声明，`claims` 为空、
        `problems` 也为空）被判成"拒绝"—— 但拒绝时一个理由都给不出来。

        「ok=False 且 problems 为空」是最坏的一种状态：人在界面上看到
        "被拒绝"，却看不到任何可修正的原因，只能猜。
        判定器的每一种"否"都必须能说出为什么否 —— 这条在
        GoalVerifier / preflight 上都踩过，这里第三次。

        所以现在的判据是：**没有问题时就是可以签发**。
        `claims` 为空但没问题，意味着"这是一次没有产物的运行，
        而任务也不要求产物" —— 那正是只读任务的正常形态。
        """
        return not self.problems

    def reason_codes(self) -> List[str]:
        return [p.code.value for p in self.problems]

    # ---------- 序列化 ----------
    def to_json(self) -> Dict[str, Any]:
        return {
            "task_digest": self.task_digest,
            "ledger_digest": self.ledger_digest,
            "status": self.status,
            "ok": self.ok,
            "claims": [c.to_json() for c in self.claims],
            "problems": [p.to_json() for p in self.problems],
            "digest": self.digest,
            # 能力边界，与论文的声明一致：证书证明的是
            # "在声明的假设下、记录轨迹中的支持"，不是外部真理性。
            "scope": "本证书只证明「声称的事项在记录的轨迹里有成功证据」。"
                     "它**不**证明产物在语义上正确、程序能运行、"
                     "也不证明任务的全部意图被满足。",
        }

    def to_text(self) -> str:
        lines = []
        if self.ok:
            lines.append("完成证书：✓ 签发（每条声明都有可复核证据）")
        else:
            lines.append(f"完成证书：✗ 拒绝（{len(self.problems)} 项问题）")
        lines.append(f"  轨迹摘要：{self.ledger_digest[:16]}")
        for c in self.claims:
            mark = "✓" if c.evidence else "✗"
            lines.append(f"  {mark} {c.kind}: {c.subject}")
            for e in c.evidence:
                lines.append(f"      ← seq={e.seq} {e.tool}"
                             + (f" exit={e.exit_code}" if e.exit_code is not None else "")
                             + (f" → {', '.join(e.files_changed[:3])}"
                                if e.files_changed else ""))
        for p in self.problems:
            lines.append("  ! " + p.to_text())
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# 指纹
# ══════════════════════════════════════════════════════════════════
def _digest(obj: Any) -> str:
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def task_digest(prompt: str) -> str:
    """任务描述摘要。

    为什么要把任务绑进证书：证书证明的是"**这个任务**被完成"。
    没有任务摘要，同一份轨迹的证据可以被嫁接到另一个任务的证书上 ——
    那是"绑定"缺失的典型形态。取前 512 字符避免超长 prompt 影响可比性
    （但**不截断后当全文用**：摘要就是摘要，不假装等于任务）。
    """
    text = (prompt or "").strip()
    return _digest({"task": text[:512], "len": len(text)})


def ledger_digest(executions: Sequence[Dict[str, Any]]) -> str:
    """归一化证据账本摘要。

    只取与"能否作为证据"相关的字段（seq / tool / ok / 产物 / 退出码），
    不取 args 与输出正文 —— 后者会让摘要随无关变化而变，
    而摘要的用途是**标识这份轨迹**，不是给它做完整快照。
    """
    rows = []
    for e in executions:
        rows.append({
            "seq": int(e.get("seq") or 0),
            "tool": e.get("tool") or "",
            "ok": bool(e.get("ok")),
            "exit": e.get("exit_code"),
            "files": sorted(e.get("files_changed") or []),
        })
    return _digest(rows)


# ══════════════════════════════════════════════════════════════════
# 从事实推导证书
# ══════════════════════════════════════════════════════════════════
# 写类工具。判据是"它能产生文件改动"，不是"名字里带 write"。
_WRITE_TOOLS = ("apply_patch", "shell")

# 回答里表示"我改了东西"的措辞。**只用于发现"声明了但没证据"这一种情形**，
# 任何情况下都不把它当作证据本身。
_WRITE_CLAIM_HINTS = (
    "已修改", "已创建", "已新建", "已写入", "已生成", "已删除", "已重命名",
    "改好了", "建好了", "写好了", "修好了", "删掉了",
    "i created", "i added", "i modified", "i fixed", "i removed",
)
# 回答里表示"纯查询"的措辞（用于给只读任务一条合法出口）。
# 答案里的"只读声明"。这是**另一个概念**：它不是任务意图，
# 而是**模型自己说"这是只读任务"**。所以刻意不复用 intent.READONLY_INTENT
# （那是用户的话），而 intent 的只读措辞用于判断任务本身。
_READONLY_CLAIM_HINTS = (
    "只读", "没有改动", "未做任何改动", "不需要修改", "无需修改",
    "read-only", "no changes", "did not modify",
)


def build_certificate(*, prompt: str, executions: Sequence[Dict[str, Any]],
                      status: str, sandbox: SandboxMode,
                      workspace: str,
                      answer: str = "") -> Certificate:
    """从结构化事实推导一份完成证书。

    ## 入参里为什么有 `answer`

    它不是用来"判断对不对"的 —— 那是 LLM 裁判的活，我们不做。
    它只有一个用途：发现**「回答里声称改了，但轨迹里没有写操作」**
    这一种自相矛盾。这是 `claim` 与 `evidence` 之间唯一能找到的
    文本级矛盾点，而且方向单一（只用来报警，不用来放行）。

    即使这个检测漏了，也不会让假完成通过 ——
    因为签发与否由 `problems` 决定，而 `problems` 的主体来自结构化事实。

    ## 为什么 `status` 必须参与判定

    论文的"拒绝"维度包含"执行状态"。一个 `escalated` / `max_iterations`
    的运行，即便有证据，也不该被签成 COMPLETE ——
    那会把"中途失败"包装成"完成"。
    """
    cert = Certificate(
        task_digest=task_digest(prompt),
        ledger_digest=ledger_digest(executions),
        status=status,
    )

    # ── 1. 收集成功证据（失败的操作**永远**不是证据）──
    successes = [e for e in executions if e.get("ok")]
    write_ev = [e for e in successes
                if (e.get("tool") in _WRITE_TOOLS) and (e.get("files_changed") or [])]
    shell_ev = [e for e in successes
                if e.get("tool") == "shell" and e.get("exit_code") is not None]

    # ── 2. 推导声明：文件 → 声明 wrote；命令 → 声明 ran ──
    by_path: Dict[str, List[Evidence]] = {}
    for e in write_ev:
        for p in (e.get("files_changed") or []):
            by_path.setdefault(p, []).append(Evidence(
                seq=int(e.get("seq") or 0), tool=e.get("tool") or "",
                ok=True, exit_code=e.get("exit_code"),
                files_changed=list(e.get("files_changed") or [])))
    for path, evs in by_path.items():
        cert.claims.append(Claim(kind="wrote", subject=path, evidence=evs))

    for e in shell_ev:
        cmd = str((e.get("args") or {}).get("command") or "").strip()
        if not cmd:
            continue
        cert.claims.append(Claim(
            kind="ran", subject=cmd[:160],
            evidence=[Evidence(seq=int(e.get("seq") or 0), tool="shell",
                               ok=True, exit_code=e.get("exit_code"),
                               files_changed=list(e.get("files_changed") or []))]))

    # ── 3. 拒绝判定 ──

    # 3.1 状态不是 completed → 不能签发
    if status != "completed":
        cert.problems.append(Problem(
            RejectCode.UNSAFE_COMPLETION,
            f"运行结束状态是 `{status}`，不是 `completed`。"))

    # 任务类型：决定"零产物"是否算问题。
    # 复用 preflight 的判据，保证两处口径只有一份。
    is_read = _is_read_task(prompt or "")

    # 3.2 一条成功调用都没有
    #
    #     **只在写类任务上报。** 问答类任务（"入口文件是哪个？"）的合法形态
    #     就是零工具调用直接给答案——对它报 no_artifacts 等于要求
    #     每个问答都必须动一次工具，那是凭空多跑两轮。
    #     这是本项目第五次踩"判定器只会在错误时拦、不会在正确时放行"。
    if not successes and not is_read:
        cert.problems.append(Problem(
            RejectCode.NO_ARTIFACTS,
            f"轨迹里有 {len(executions)} 次工具调用，其中成功 0 次。"))

    # 3.3 回答声称改了，但轨迹里没有写证据
    #     —— 这是**假完成的文本形态**，也是 ECT 最想抓的那一类。
    #
    #     注意：`claims_write` 只认**第一人称的完成声明**（"已修改"/"改好了"）。
    #     实测踩到：判据过宽时，一句"好了"也会命中，于是零调用的运行被报成
    #     "声称改了但没有写操作" —— 那个原因码是错的，模型会去改一件
    #     它从没说过的事。原因码指错方向的危害与"拒绝却不给理由"同级。
    low = (answer or "").lower()
    claims_write = any(h in low for h in _WRITE_CLAIM_HINTS)
    readonly_task = any(h in low for h in _READONLY_CLAIM_HINTS)

    # 3.4 任务点名了文件、却没有任何成功的写操作
    #     判据复用 preflight，保证两处口径一致
    #     （同一轨迹不能既"通过闸门"又被判"没做"）。
    # 用 mentioned_files：模块名 / MIME 类型不该被当成"点名的文件"，
    # 否则会把一个诚实完成判成"写的不是该写的地方"。
    named = mentioned_files(prompt or "")
    named_untouched: List[str] = []
    if named and not write_ev and not is_read:
        named_untouched = [p for p in named if p not in by_path]

    # 3.3 与 3.4 指向同一件事（该写而没写），**合并成一条**。
    #     早期实现分别 append，同一条轨迹会收到两条一模一样的纠正，
    #     重复的原因码会把真正的原因挤下去。
    if (claims_write or named_untouched) and not write_ev and not readonly_task:
        if named_untouched:
            detail = (f"任务点名了 {len(named_untouched)} 个文件，"
                      "但轨迹里没有任何成功的写操作。")
            subject = ", ".join(named_untouched[:5])
        else:
            detail = "回答里声称做了改动，但轨迹里没有任何**成功的写操作**。"
            subject = ""
        cert.problems.append(Problem(
            RejectCode.WRITE_CLAIMED_NO_EVIDENCE, detail, subject=subject))

    # 3.4b **写了，但写的不是该写的地方**
    #
    # ## 这是 SWE-bench Pro 基准跑出来的真实缺口
    #
    # 上面的 3.3/3.4 只在"完全没有写操作"时触发。于是一个
    # **随便改个无关文件**的运行能拿到完整证书 —— 它确实"写了"，
    # 只是写的地方与任务要求无关。
    #
    # 这正是 BenchJack 记录的作弊形态：不改实现、改测试文件
    # （`conftest.py` 钩住 pytest 把结果改写成 passed）。
    # 我们当时的证书对此**毫无反应**，6 道真实题里 6 道全部放行。
    #
    # 判据：任务点名了文件（`named` 非空）且是写任务时，
    # **至少一个**被点名文件必须出现在写入产物里。
    # "至少一个"而不是"全部"：真实任务常有主文件 + 若干附件，
    # 要求全部会被"只改了主文件"这种合理情形误拦。
    if write_ev and named and not is_read and not readonly_task:
        # 用 `path_covers` 而不是严格相等 —— 必须与 preflight 同一套规则。
        # 实测踩到：任务说 `a.py`、实际写 `src/a.py`，严格相等会判成
        # "写的不是该写的地方"，把一个正确完成拦掉。
        from .intent import path_covers
        written_paths = sorted(by_path)
        hit_named = [p for p in named
                     if any(path_covers(p, w) for w in written_paths)]
        if not hit_named:
            cert.problems.append(Problem(
                RejectCode.ARTIFACT_MISMATCH,
                f"写操作没有触及任务点名的任何文件。"
                f"任务要求：{', '.join(named[:3])}；"
                f"实际写入：{', '.join(written_paths[:3])}。",
                subject=", ".join(written_paths[:3])))

    # 3.5 声明为空（既没写也没跑）→ 无法判断完成
    #
    #     **这里踩过一个真实的误拦，值得留档。**
    #
    #     早期版本判断"是不是只读任务"用的是 `readonly_task`，
    #     而那个变量来自**答案里的措辞**（"这是只读任务"）。
    #     问题在于：模型不会主动写这句话。于是每一个读类任务
    #     （"读一下 README 说明项目"）都会被报 `EMPTY_CLAIM` ——
    #     真机表现为：模型已经读完并给出正确答案，
    #     却被拒签、回灌 2 次、最终答案被替换成
    #     "我已经完成了你能离线验证的部分"。
    #
    #     正确的判据是**任务本身**是不是读类（`_is_read_task`，
    #     与 preflight 同源），而不是"模型有没有说它是只读"。
    #     要求模型自报任务性质，等于把判定权交回给被判定的一方。
    #     另一条出口是 `readonly_task`（用户/答案明确声明），
    #     两者满足其一即可。
    if not cert.claims and not readonly_task and not is_read:
        if successes:
            cert.problems.append(Problem(
                RejectCode.EMPTY_CLAIM,
                f"有 {len(successes)} 次成功的工具调用，但没有产生可绑定的产物"
                "（既没改文件也没跑命令）。"))
        elif not cert.problems:
            cert.problems.append(Problem(
                RejectCode.EMPTY_CLAIM, "证书里没有任何可核验的声明。"))

    # 3.6 范围检查：证据动作是否超出沙箱允许
    for e in write_ev:
        if sandbox is SandboxMode.READ_ONLY:
            cert.problems.append(Problem(
                RejectCode.OUT_OF_SCOPE,
                "只读沙箱下出现了写操作。",
                subject=", ".join((e.get("files_changed") or [])[:3]),
                seq=int(e.get("seq") or 0)))
            break
    outside = _paths_outside_workspace(write_ev, workspace)
    if outside:
        cert.problems.append(Problem(
            RejectCode.OUT_OF_SCOPE,
            "写操作落在工作区之外。", subject=", ".join(outside[:3])))

    cert.digest = _cert_digest(cert)
    return cert


def _is_read_task(prompt: str) -> bool:
    """复用 preflight 的判据，保证口径只有一份。"""
    try:
        from .preflight import looks_like_edit_task
        return not looks_like_edit_task(prompt)
    except ImportError:
        return False


def _paths_outside_workspace(executions: Sequence[Dict[str, Any]],
                             workspace: str) -> List[str]:
    ws = os.path.realpath(workspace)
    out: List[str] = []
    for e in executions:
        for p in (e.get("files_changed") or []):
            rp = os.path.realpath(p) if os.path.isabs(p) \
                else os.path.realpath(os.path.join(ws, p))
            if rp != ws and not rp.startswith(ws + os.sep):
                if p not in out:
                    out.append(p)
    return out


def _cert_digest(cert: Certificate) -> str:
    """证书指纹：覆盖全部语义字段，**不包括 digest 自身**。"""
    return _digest({
        "task": cert.task_digest,
        "ledger": cert.ledger_digest,
        "status": cert.status,
        "claims": [c.to_json() for c in cert.claims],
        "problems": [p.code.value for p in cert.problems],
    })


# ══════════════════════════════════════════════════════════════════
# 确定性重放（回执校验）
# ══════════════════════════════════════════════════════════════════
def replay(cert: Certificate,
           events: Sequence[Dict[str, Any]],
           workspace: str = "") -> List[Problem]:
    """回到**已落盘的事件流**，逐条复核证书里的每个证据引用。

    ## 这一步是本模块与"看结构化字段"之间真正的差别

    `build_certificate` 是从内存里的 facts 推导的 —— 它可能被上游 bug 污染。
    `replay` 换一个数据源（落盘的事件流），独立地再查一遍：
    **同一个事实在两处都成立，才算数**。

    这就是论文"确定性重放"在我们能力范围内的合理落地：
    我们不重跑工具（那会引入副作用），但重查**不可变的记录**。

    ## 覆盖的四项（对应论文的回执维度）

    1. **值路径**：seq 在事件流里存在吗？
    2. **执行状态**：那一条是 `tool.output` 且 `ok=true` 吗？
    3. **产物一致**：事件里记的 `files_changed` 与证书里写的一致吗？
    4. **范围**：落在工作区内吗？

    返回的问题列表为空 = 重放通过。
    """
    problems: List[Problem] = []
    index: Dict[int, Dict[str, Any]] = {}
    for ev in events:
        try:
            index[int(ev.get("seq"))] = ev
        except (TypeError, ValueError):
            continue

    for claim in cert.claims:
        if not claim.evidence:
            problems.append(Problem(
                RejectCode.EMPTY_CLAIM,
                f"声明「{claim.subject}」没有绑定任何证据。",
                subject=claim.subject))
            continue
        for e in claim.evidence:
            ev = index.get(e.seq)
            if ev is None:
                problems.append(Problem(
                    RejectCode.SEQ_NOT_FOUND,
                    f"seq={e.seq} 在事件流里不存在。",
                    subject=claim.subject, seq=e.seq))
                continue
            if ev.get("type") != "tool.output":
                problems.append(Problem(
                    RejectCode.SEQ_NOT_TOOL_OUTPUT,
                    f"seq={e.seq} 的类型是 `{ev.get('type')}`，不是工具结果。",
                    subject=claim.subject, seq=e.seq))
                continue
            d = ev.get("data") or {}
            if not d.get("ok"):
                problems.append(Problem(
                    RejectCode.EVIDENCE_FAILED,
                    f"seq={e.seq} 的工具结果是**失败**的，不能作为证据。",
                    subject=claim.subject, seq=e.seq))
                continue
            actual = sorted(d.get("files_changed") or [])
            claimed = sorted(e.files_changed or [])
            if claimed and actual != claimed:
                problems.append(Problem(
                    RejectCode.ARTIFACT_MISMATCH,
                    f"seq={e.seq} 的产物与证书不一致："
                    f"事件记录 {actual[:4]}，证书写 {claimed[:4]}。",
                    subject=claim.subject, seq=e.seq))

    # 范围：与 build 阶段同一判据，但基于**事件里的**产物
    if workspace:
        ws = os.path.realpath(workspace)
        for claim in cert.claims:
            for e in claim.evidence:
                for p in (e.files_changed or []):
                    rp = os.path.realpath(p) if os.path.isabs(p) \
                        else os.path.realpath(os.path.join(ws, p))
                    if rp != ws and not rp.startswith(ws + os.sep):
                        problems.append(Problem(
                            RejectCode.OUT_OF_SCOPE,
                            f"证据产物 {p} 落在工作区之外。",
                            subject=claim.subject, seq=e.seq))
                        break

    return problems


def verify_certificate(cert: Certificate,
                       events: Sequence[Dict[str, Any]],
                       workspace: str = "") -> Dict[str, Any]:
    """对外的一站式核验：证书 + 事件流 → 是否可信。

    返回结构刻意与 `chain.verify_chain` 同构（ok / checked / problems），
    这样审计侧可以用同一套方式处理两种核验结果。
    """
    replayed = replay(cert, events, workspace)
    stored_ok = _cert_digest(cert) == cert.digest
    return {
        "ok": bool(stored_ok and not replayed and not cert.problems),
        "checked": len(cert.claims),
        "cert_digest_ok": stored_ok,
        "build_problems": [p.to_json() for p in cert.problems],
        "replay_problems": [p.to_json() for p in replayed],
        "reason_codes": sorted({p.code.value for p in list(cert.problems) + replayed}),
        "scope": cert.to_json()["scope"],
    }


# ══════════════════════════════════════════════════════════════════
# 回灌：给模型的结构化反馈
# ══════════════════════════════════════════════════════════════════
def feedback_text(cert: Certificate) -> str:
    """把拒绝转成一条给模型的纠正消息。

    ## 为什么要用原因码而不是一句"没完成"

    论文的拒绝维度明确要求**返回 continue + 原因码**。原因码的价值是
    **可针对性修正**：模型看到 `evidence_failed` 就会去修那一步；
    看到 `write_claimed_no_evidence` 就会真的去落盘。
    而"任务未完成"这五个字它只能原地打转 —— 问它"哪里没完成"它也答不出。
    """
    lines = ["[这不是工具结果，是一条完成证书被拒绝的通知]",
             "你在上一条回复里宣布完成，但**完成证书**无法签发："]
    for p in cert.problems:
        lines.append(f"  · [{p.code.value}] {p.detail}")
        lines.append(f"    下一步：{p.code.remedy}")
    lines.extend([
        "",
        "完成证书要求：你声称完成的每一件事，都必须能指向轨迹里"
        "**一次真实的、成功的工具调用**。请补齐后再收尾。",
        "如果这个任务确实不需要改动工作区（纯查询类），"
        "请在最终回答里明确说明「这是只读任务」，并给出你读取过的文件。",
    ])
    return "\n".join(lines)
