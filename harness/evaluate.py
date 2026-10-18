"""轨迹诊断：把 `trace` 变成 `eval` 闭环。

## 为什么这是行业最大缺口

arXiv 综述（*Harness Engineering Survey*, 2026-05）统计了 200+ 个 harness 项目：

> **89% 有 tracing，仅 52% 有 evals。**

也就是说，绝大多数项目把轨迹**存下来了**，但没有任何机制把它变成
"这次比上次好还是坏"的判断。轨迹躺在磁盘上，跑完就再没人看。

我们有同样的问题：`.crucible/runs/<id>/events.jsonl` 有完整事件，
但**没有任何代码读它做分析**。这正是 G-4。

## 做法：纯启发式，不调模型

参考 LiteTrajEval（arXiv 2610.03315，华为）的分层设计：
预处理轨迹 → 标记启发式失败信号 → （可选）单次 rubric 判定。

但**第一层必须是纯规则的**，理由有三条：
1. **不需要真实模型就能跑** —— 离线、确定、可进 CI。
2. **判定器不能自己会犯错**。用 LLM 判断"这个轨迹有没有问题"，
   等于把评估与生成合并 —— 书 §1 明说「自审不可靠」。
   而启发式的误判是**可复现、可修正**的；LLM 判官的误判是随机的。
3. **成本**：LiteTrajEval 的卖点就是比 AgentRx 便宜 6 倍。
   我们连那 1/6 都不用花 —— 纯规则零成本。

## 诊断的五类信号（全部基于结构化字段）

| 信号 | 判据 |
| --- | --- |
| `false_completion` | 宣布完成，但**没有任何成功的写操作**（任务要求改动时） |
| `no_verification` | 改动了，但没跑过任何验证命令 |
| `loop` | 相同工具+参数反复出现（阈值内） |
| `consecutive_tool_failures` | 同一工具连续失败 N 次 |
| `budget_exhausted` | 结束原因是轮数/预算用尽，而不是任务完成 |
| `gate_blocked` | 完成前检查/目标闸门拦过（说明主动纠正是必要的） |
| `over_engineering` | 工具调用数远超产出改动数（"空转"信号） |

## 关键纪律：**不做"正确性"判断**

本模块只回答"**过程有没有问题**"，不回答"**结果对不对**"。
后者需要外部可验证的产物（测试、断言），那是 `verify.py` / `preflight.py`
与用户的职责。混淆这两件事会产生假诊断 —— 而假诊断会让整个评估
失去可信度（本项目在 GoalVerifier 与 hash chain 上各吃过一次）。

所以报告里每个 signal 都带 `evidence`（具体 seq 与事实），
让人能自己复核，而不是接受一个分数。
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _looks_like_edit_task(prompt: str) -> bool:
    """问"这条任务是否要求改动工作区"。

    刻意**复用** `preflight.looks_like_edit_task`，因为判据必须只有一份：
    诊断器与完成前闸门给出矛盾结论是灾难 —— 同一个轨迹会同时
    "通过了闸门"且"被诊断为假完成"。

    导入方式处理了"脚本直跑"的问题（实测踩到两次）：
      `python harness/evaluate.py` 时没有父包，相对导入会 ImportError。
      解法是**先把项目根目录塞进 sys.path，再用绝对导入** ——
      而不是按文件路径 exec_module（那样 preflight 内部的
      `from .protocol import ...` 依然会炸，因为它还是包内相对导入）。
    """
    try:
        from .preflight import looks_like_edit_task as fn
        return fn(prompt)
    except ImportError:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    try:
        from harness.preflight import looks_like_edit_task as fn
        return fn(prompt)
    except ImportError:
        # 连 preflight 都找不到 → 退化判据：宁可保守（多判为写任务），
        # 也不静默返回 False —— 那会让写任务漏检，是最坏的失败方向。
        low = (prompt or "").lower()
        return any(k in low for k in ("改", "创建", "新建", "写", "实现", "修",
                                      "modify", "create", "write", "fix", "add"))


# ══════════════════════════════════════════════════════════════════
# 读取
# ══════════════════════════════════════════════════════════════════
def load_events(run_dir: str) -> List[Dict[str, Any]]:
    """读一个运行的事件流。

    损坏的行**跳过而不是抛错** —— 与 `session._read_jsonl` 同一取向：
    保留此前所有完好的记录。诊断器尤其需要这样：一次崩溃的运行
    恰恰是最值得诊断的对象，不能因为一行损坏就整个读不出来。
    """
    path = os.path.join(run_dir, "events.jsonl")
    out: List[Dict[str, Any]] = []
    if not os.path.isfile(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    return out


def load_task_text(run_dir: str) -> str:
    """从轨迹里取出**最初那条用户消息** —— 也就是任务文本。

    为什么必须读它：没有任务文本就无法区分"读类任务"与"写类任务"，
    于是"没有文件改动"会被一律判成假完成 —— **那正是实测踩到的假警报**
    （12 个演示运行全被误报）。任务文本是判定所需的上下文，缺了它
    诊断器就只能瞎猜。

    为什么读第一条 user 消息而不是别的：状态栏消息也是 role=user
    （`[环境状态]` 开头），必须跳过它们。用"第一条不带 `kind=status`
    且不以 `[环境状态]` 开头的 user 消息"这个判据。
    """
    path = os.path.join(run_dir, "trajectory.jsonl")
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if rec.get("role") != "user":
                continue
            if (rec.get("meta") or {}).get("kind") == "status":
                continue
            content = rec.get("content") or ""
            if content.startswith("[环境状态]") or content.startswith("[工具结果"):
                continue
            if content.startswith("[这不是工具结果"):
                continue          # harness 自己的纠正指令，不是任务
            return content
    return ""


# ══════════════════════════════════════════════════════════════════
# 诊断信号
# ══════════════════════════════════════════════════════════════════
# 严重度含义：
#   fail   —— 明确的过程缺陷，应当阻止"这次算成功"
#   warn   —— 可疑，需要人看一眼
#   info   —— 中性事实，供统计
SEVERITY_ORDER = {"fail": 0, "warn": 1, "info": 2}

# 验证类命令（与 preflight 保持同一张表 —— 两处必须一致，
# 否则同一个轨迹在闸门与诊断器里会得出矛盾结论）
VERIFY_CMDS = ("pytest", "unittest", "py_compile", "npm test", "npm run test",
               "npm run build", "npm run lint", "yarn test", "cargo test",
               "cargo build", "go test", "go build", "mvn test", "mvn package",
               "gradle test", "make", "make test", "tsc", "eslint", "flake8",
               "ruff", "mypy", "jest", "vitest", "dotnet test", "dotnet build")

# 改动类工具
WRITE_TOOLS = ("apply_patch",)


@dataclass
class Signal:
    """一条诊断信号。"""

    code: str
    severity: str            # fail | warn | info
    detail: str
    evidence: List[str] = field(default_factory=list)
    seqs: List[int] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {"code": self.code, "severity": self.severity,
                "detail": self.detail, "evidence": self.evidence,
                "seqs": self.seqs}


@dataclass
class DiagnosticReport:
    """一次轨迹诊断的结论。

    `verdict` 只有三档，刻意不做连续分数：
      · `no_issue`  —— 未发现过程问题
      · `suspect`   —— 有可疑项，需人看
      · `flawed`    —— 有明确缺陷

    为什么不打分：Berkeley BenchJack 的教训是**评分器本身可被操纵/误设**。
    一个 0-100 的分数会让人停止追问"为什么扣了 20 分"；
    而一个明确的 code + evidence 会让人去复核。
    """

    run_dir: str
    verdict: str = "no_issue"
    signals: List[Signal] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    @property
    def fails(self) -> List[Signal]:
        return [s for s in self.signals if s.severity == "fail"]

    @property
    def warns(self) -> List[Signal]:
        return [s for s in self.signals if s.severity == "warn"]

    def to_json(self) -> Dict[str, Any]:
        return {
            "run_dir": self.run_dir,
            "verdict": self.verdict,
            "signals": [s.to_json() for s in self.signals],
            "stats": self.stats,
            "disclaimer": ("本报告只判断**过程**有没有问题，"
                           "不判断**结果**是否正确 —— 后者需要外部可验证产物。"),
        }

    def to_text(self) -> str:
        label = {"no_issue": "未发现过程问题",
                 "suspect": "有可疑项，建议人工复核",
                 "flawed": "存在明确过程缺陷"}[self.verdict]
        lines = [f"运行目录：{self.run_dir}",
                 f"结论：{label}",
                 f"事件 {self.stats.get('events', 0)} 条 · "
                 f"工具调用 {self.stats.get('tool_calls', 0)} 次 · "
                 f"改动文件 {self.stats.get('files_changed', 0)} 个 · "
                 f"迭代 {self.stats.get('iterations', 0)} 轮",
                 f"结束状态：{self.stats.get('status', '?')}"
                 f"（{self.stats.get('reason', '')[:60]}）"]
        if not self.signals:
            lines.append("未发现可疑信号。")
        else:
            lines.append(f"信号 {len(self.signals)} 条：")
            for s in sorted(self.signals, key=lambda x: SEVERITY_ORDER[x.severity]):
                mark = {"fail": "✗", "warn": "!", "info": "·"}[s.severity]
                lines.append(f"  {mark} [{s.code}] {s.detail}")
                for e in s.evidence[:4]:
                    lines.append(f"      {e}")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# 分析
# ══════════════════════════════════════════════════════════════════
def _is_verify_cmd(cmd: str) -> bool:
    low = (cmd or "").lower()
    return any(v in low for v in VERIFY_CMDS)


class TraceAnalyzer:
    """把事件流折叠成一棵"事实树"，再跑启发式检查。

    分两步的理由：**先归一化，再判定**。
    直接在各检查函数里遍历原始事件会导致每个检查都重复解析逻辑，
    而且字段缺失的处理会散落各处（早晚不一致）。
    """

    def __init__(self, events: Sequence[Dict[str, Any]],
                 task_text: str = "") -> None:
        self.events = list(events)
        # 任务文本（用户输入）。缺了它就无法判定"任务是否要求改动"，
        # 于是会产生假警报 —— 见 _check_false_completion 的说明。
        self.task_text = task_text
        self.stats: Dict[str, Any] = {}
        self._fold()

    # ---------- 第一步：折叠成事实 ----------
    def _fold(self) -> None:
        starts = [e for e in self.events if e.get("type") == "turn.started"]
        dones = [e for e in self.events if e.get("type") == "turn.completed"]
        reqs = [e for e in self.events if e.get("type") == "tool.requested"]
        outs = [e for e in self.events if e.get("type") == "tool.output"]
        checks = [e for e in self.events if e.get("type") == "check"]
        errs = [e for e in self.events if e.get("type") == "error"]

        # verdict 决策的事实
        self.turn_started = starts[0] if starts else None
        self.turn_completed = dones[-1] if dones else None
        self.checks = checks
        self.errors = errs

        # 工具调用序列：(tool, args, ok, exit_code, files_changed, seq, verdict)
        self.execs: List[Dict[str, Any]] = []
        for o in outs:
            d = o.get("data") or {}
            self.execs.append({
                "tool": d.get("tool") or "",
                "ok": bool(d.get("ok")),
                "exit_code": d.get("exit_code"),
                "files_changed": list(d.get("files_changed") or []),
                "seq": o.get("seq"),
                "verdict": d.get("verdict"),
                "error_layer": d.get("error_layer"),
                "truncated": d.get("truncated"),
                "call_id": d.get("call_id"),
            })

        # 参数从 requested 侧取（output 事件不带 args）
        args_by_call: Dict[str, Dict[str, Any]] = {}
        for r in reqs:
            d = r.get("data") or {}
            cid = d.get("call_id")
            if cid:
                args_by_call[cid] = d.get("args") or {}
        for e in self.execs:
            e["args"] = args_by_call.get(e.get("call_id") or "", {})

        self.files_changed_seq: List[Tuple[int, str]] = []
        for e in self.execs:
            for p in e["files_changed"]:
                self.files_changed_seq.append((e["seq"] or 0, p))

        self.stats = {
            "events": len(self.events),
            "tool_calls": len(self.execs),
            "executed_tool_calls": sum(1 for e in self.execs),
            "requested_tool_calls": len(reqs),
            "files_changed": len({p for _, p in self.files_changed_seq}),
            "checks": len(checks),
            "errors": len(errs),
            "status": (self.turn_completed or {}).get("data", {}).get("status"),
            "reason": (self.turn_completed or {}).get("data", {}).get("reason", ""),
            "iterations": (self.turn_completed or {}).get("data", {}).get("iterations", 0),
            "usage": (self.turn_completed or {}).get("data", {}).get("usage", {}),
            "by_type": dict(Counter(e.get("type") for e in self.events)),
        }

    # ---------- 便捷查询 ----------
    def succeeded_writes(self) -> List[Dict[str, Any]]:
        return [e for e in self.execs if e["ok"] and e["files_changed"]]

    def verify_runs(self) -> List[Dict[str, Any]]:
        out = []
        for e in self.execs:
            if e["tool"] != "shell":
                continue
            if _is_verify_cmd((e.get("args") or {}).get("command") or ""):
                out.append(e)
        return out

    def passing_verify(self) -> List[Dict[str, Any]]:
        return [e for e in self.verify_runs()
                if e["ok"] and e.get("exit_code") == 0]

    # ---------- 第二步：检查项 ----------
    def analyze(self, run_dir: str) -> DiagnosticReport:
        rep = DiagnosticReport(run_dir=run_dir, stats=dict(self.stats))
        self._check_false_completion(rep)
        self._check_no_verification(rep)
        self._check_loop(rep)
        self._check_consecutive_failures(rep)
        self._check_budget_exhausted(rep)
        self._check_gate_blocks(rep)
        self._check_idle_spin(rep)

        sev = {s.severity for s in rep.signals}
        rep.verdict = ("flawed" if "fail" in sev
                       else "suspect" if "warn" in sev
                       else "no_issue")
        return rep

    def _check_false_completion(self, rep: DiagnosticReport) -> None:
        """**最高价值的一条**：宣布完成，但任务要求的改动没发生。

        判据完全来自结构化字段 + 任务文本（用户输入，非模型生成）。
        不读模型任何文本。

        ## 为什么必须知道"任务要不要改动"

        实测踩到的假警报：不看任务文本时，把"没有文件改动"一律判成假完成，
        于是 12 个演示运行（任务都是"看看工作区"这类只读请求）**全部误报**。
        假警报会让整个评估失去可信度 —— 本项目已经因此吃过两次亏
        （GoalVerifier 恒假、hash chain 把旧运行误判篡改）。

        所以这里复用 `preflight.looks_like_edit_task()`：**同一套判据
        必须在闸门与诊断器里给出同一结论**，否则同一个轨迹会有两种说法。
        """
        st = (self.turn_completed or {}).get("data", {}).get("status")
        if st != "completed":
            return
        if self.succeeded_writes():
            return

        # 任务文本缺失（旧运行可能没存轨迹）→ 无法判定"任务是否要求改动"，
        # 只能降级为提示。但**有一件事不需要任务文本**：
        # 一次工具都没调用就宣布完成 —— 这在任何任务下都不成立，
        # 因为不可能有任何产物（连只读任务也得读了才知道）。
        if not self.task_text:
            if not self.execs:
                rep.signals.append(Signal(
                    code="false_completion", severity="fail",
                    detail="整个运行**一次工具都没调用**就宣布任务完成 —— "
                           "无论任务是什么，都不可能有任何产物。",
                    evidence=[f"结束状态 completed，原因「{self.stats['reason'][:40]}」"],
                    seqs=[(self.turn_completed or {}).get("seq")],
                ))
                return
            rep.signals.append(Signal(
                code="false_completion", severity="warn",
                detail="整个运行没有任何文件改动，且**缺少任务文本无法判定**"
                       "任务是否要求改动。建议人工看一眼。",
                evidence=[f"工具调用 {self.stats['tool_calls']} 次，"
                          f"改动 0 个文件"],
            ))
            return

        # 判据只有一份：复用 preflight 的（并通过 _looks_like_edit_task
        # 处理"脚本直跑"下的导入问题）
        if not _looks_like_edit_task(self.task_text):
            return          # 读类任务，没有改动是**正确**的

        attempts = [e for e in self.execs if e["tool"] in WRITE_TOOLS]
        if attempts:
            sev = "fail"
            why = f"任务要求改动，尝试了 {len(attempts)} 次补丁但没有一次成功落盘。"
        elif self.execs:
            sev = "fail"
            why = "任务要求改动，但整个运行**没有任何写操作**就宣布完成。"
        else:
            sev = "fail"
            why = "任务要求改动，但整个运行**一次工具都没调用**就宣布完成。"
        rep.signals.append(Signal(
            code="false_completion", severity=sev, detail=why,
            evidence=[f"任务：「{self.task_text[:60]}」",
                      f"结束状态 completed，原因「{self.stats['reason'][:40]}」",
                      f"工具调用 {self.stats['tool_calls']} 次，"
                      f"改动文件 {self.stats['files_changed']} 个"],
            seqs=[(self.turn_completed or {}).get("seq")],
        ))

    def _check_no_verification(self, rep: DiagnosticReport) -> None:
        """改动落盘了，但没有**通过的**验证证据。

        严重度 warn：有些改动（文档、配置）确实没有可跑的验证。
        """
        if not self.succeeded_writes():
            return
        if self.passing_verify():
            return
        runs = self.verify_runs()
        if runs:
            rep.signals.append(Signal(
                code="verification_not_passing", severity="warn",
                detail=f"跑了 {len(runs)} 次验证命令，但没有一次退出码为 0。",
                evidence=[f"最后一次退出码 {runs[-1].get('exit_code')}"
                          f"（命令：{str((runs[-1].get('args') or {}).get('command'))[:60]}）"],
                seqs=[runs[-1].get("seq")],
            ))
        else:
            paths = sorted({p for _, p in self.files_changed_seq})[:4]
            rep.signals.append(Signal(
                code="no_verification", severity="warn",
                detail="改动已落盘，但整个运行没有跑过任何验证命令。",
                evidence=[f"已落盘：{', '.join(paths)}"],
                seqs=[s for s, _ in self.files_changed_seq[:2]],
            ))

    def _check_loop(self, rep: DiagnosticReport) -> None:
        """相同工具 + 相同参数反复出现 → 原地打转。

        阈值取 4：正常的探索（读同目录两次）不该报警，
        而"读同一个文件 5 次"几乎必然是循环。
        这个信号与运行时的 LoopDetector 互补 —— 那是**在线拦截**
        （会终止运行），这里是**离线诊断**（只是记账，能看出
        "运行时为什么被拦"）。
        """
        key_count: Dict[str, List[int]] = defaultdict(list)
        for e in self.execs:
            try:
                arg_sig = json.dumps(e.get("args") or {}, sort_keys=True,
                                     ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError):
                arg_sig = ""
            key_count[f"{e['tool']}:{arg_sig}"].append(e.get("seq"))
        for k, seqs in key_count.items():
            if len(seqs) >= 4:
                rep.signals.append(Signal(
                    code="loop", severity="warn",
                    detail=f"同一个调用重复了 {len(seqs)} 次：{k[:90]}",
                    evidence=[f"seq {seqs[:8]}"], seqs=seqs[:8],
                ))

    def _check_consecutive_failures(self, rep: DiagnosticReport) -> None:
        """同一工具连续失败 → 说明模型没在改输入，只是在重试。"""
        run_tool = None
        run_seqs: List[int] = []
        worst = 0
        worst_tool = None
        worst_seqs: List[int] = []
        for e in self.execs:
            if not e["ok"]:
                if e["tool"] == run_tool:
                    run_seqs.append(e.get("seq"))
                else:
                    run_tool, run_seqs = e["tool"], [e.get("seq")]
                if len(run_seqs) > worst:
                    worst, worst_tool, worst_seqs = len(run_seqs), run_tool, list(run_seqs)
            else:
                run_tool, run_seqs = None, []
        if worst >= 3:
            rep.signals.append(Signal(
                code="consecutive_tool_failures", severity="warn",
                detail=f"工具 `{worst_tool}` 连续失败 {worst} 次 —— "
                       "模型多半在重复同一输入，没有改变策略。",
                evidence=[f"seq {worst_seqs[:8]}"], seqs=worst_seqs[:8],
            ))

    def _check_budget_exhausted(self, rep: DiagnosticReport) -> None:
        """结束原因不是"任务完成"，而是撞上了限制。

        这一类必须显式标出来：它**看起来**也是一次正常结束（有 turn.completed），
        所以最容易被当成成功计入统计。
        """
        st = self.stats.get("status")
        if st in (None, "completed"):
            return
        sev = "fail" if st in ("max_iterations", "budget", "stalled") else "warn"
        rep.signals.append(Signal(
            code="budget_exhausted", severity=sev,
            detail=f"结束状态为 `{st}`，不是正常完成任务。",
            evidence=[f"原因：{self.stats.get('reason', '')[:100]}"],
            seqs=[(self.turn_completed or {}).get("seq")],
        ))

    def _check_gate_blocks(self, rep: DiagnosticReport) -> None:
        """闸门拦过 —— 这是**中性事实**，不是缺陷。

        它说明主动纠正机制起作用了。但如果拦了到最后还是没补齐
        （即最终理由里写着"未了结"），那就是缺陷。
        """
        blocked = [e for e in self.errors
                   if (e.get("data") or {}).get("cls") in
                   ("preflight_incomplete", "goal_unmet", "intent_without_action")]
        if not blocked:
            return
        codes = Counter((e.get("data") or {}).get("cls") for e in blocked)
        detail = f"完成前闸门拦下 {len(blocked)} 次（{dict(codes)}）。"
        sev = "info"
        reason = self.stats.get("reason") or ""
        if "未了结" in reason or "尚未满足" in reason:
            sev = "fail"
            detail += " 且最终仍未补齐 —— 这次运行以未完成收尾。"
        rep.signals.append(Signal(
            code="gate_blocked", severity=sev, detail=detail,
            evidence=[f"最后理由：{reason[:80]}"],
            seqs=[e.get("seq") for e in blocked[:5]],
        ))

    def _check_idle_spin(self, rep: DiagnosticReport) -> None:
        """工具调用很多但产出极少 → "空转"。

        阈值的取法：调用 ≥ 12 次 **且** 改动文件 0 个。
        只对"跑了很多但什么都没改"报警，不对正常的探索报警 ——
        阈值定得太低会让每条轨迹都报，那等于没报。
        """
        n = self.stats.get("tool_calls", 0)
        if n < 12:
            return
        if self.stats.get("files_changed", 0) > 0 or self.succeeded_writes():
            return
        rep.signals.append(Signal(
            code="idle_spin", severity="warn",
            detail=f"工具调用 {n} 次但没有任何文件改动，疑似空转。",
            evidence=[f"用过的工具：{', '.join(sorted({e['tool'] for e in self.execs}))}"],
        ))


# ══════════════════════════════════════════════════════════════════
# 对外入口
# ══════════════════════════════════════════════════════════════════
def analyze_trace(run_dir: str) -> DiagnosticReport:
    """诊断一个运行的轨迹。"""
    analyzer = TraceAnalyzer(load_events(run_dir),
                             task_text=load_task_text(run_dir))
    return analyzer.analyze(run_dir)


def analyze_workspace(workspace: str,
                      only_flawed: bool = False) -> Dict[str, Any]:
    """诊断一个工作区下的全部运行（供 CI / 回归统计）。

    为什么需要批量：单次诊断只能回答"这一次怎么样"。
    要回答"**这次改动让整体变好还是变坏**"就必须跨运行比较 ——
    这正是 G-4 说的"trace → eval 闭环"。
    """
    root = os.path.join(os.path.realpath(workspace), ".crucible", "runs")
    if not os.path.isdir(root):
        return {"runs": 0, "verdicts": {}, "reports": []}

    reports: List[DiagnosticReport] = []
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if not os.path.isfile(os.path.join(d, "events.jsonl")):
            continue
        reports.append(analyze_trace(d))

    verdicts = Counter(r.verdict for r in reports)
    by_code: Counter = Counter()
    for r in reports:
        for s in r.signals:
            by_code[s.code] += 1

    out: Dict[str, Any] = {
        "runs": len(reports),
        "verdicts": dict(verdicts),
        "signal_counts": dict(by_code),
    }
    if only_flawed:
        out["reports"] = [r.to_json() for r in reports if r.verdict == "flawed"]
    else:
        out["reports"] = [r.to_json() for r in reports]
    return out


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════
#     python harness/evaluate.py <run目录>
#     python harness/evaluate.py --workspace <工作区>
#     python harness/evaluate.py --workspace . --flawed-only
#
# 退出码：0 = 未发现过程缺陷；1 = 存在明确缺陷；2 = 用法错误。
# 之所以给退出码，是为了能直接接进 CI ——
# "trace → eval 闭环"的价值就在于**每次改动都能被自动度量**，
# 需要人手动跑的检查等于没有检查。
def _cli(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="诊断 Crucible 运行轨迹（纯规则启发式，不调模型）")
    ap.add_argument("run_dir", nargs="?", help="单个运行目录")
    ap.add_argument("--workspace", help="批量诊断该工作区下的全部运行")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--flawed-only", action="store_true",
                    help="批量模式下只列出存在明确缺陷的运行")
    args = ap.parse_args(argv)

    if args.workspace:
        out = analyze_workspace(args.workspace, only_flawed=args.flawed_only)
        if args.json:
            import json as _json
            print(_json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(f"共诊断 {out['runs']} 个运行：{out['verdicts']}")
            if out.get("signal_counts"):
                print("信号分布：")
                for code, n in sorted(out["signal_counts"].items(),
                                      key=lambda x: -x[1]):
                    print(f"  {code}: {n}")
            flawed = [r for r in out["reports"] if r["verdict"] == "flawed"]
            if flawed:
                print(f"\n存在明确缺陷的运行 {len(flawed)} 个：")
                for r in flawed[:10]:
                    codes = [s["code"] for s in r["signals"]
                             if s["severity"] == "fail"]
                    print(f"  ✗ {os.path.basename(r['run_dir'])}：{codes}")
        return 1 if flawed else 0

    if not args.run_dir:
        ap.print_help()
        return 2
    if not os.path.isdir(args.run_dir):
        print(f"目录不存在：{args.run_dir}", file=__import__("sys").stderr)
        return 2

    rep = analyze_trace(args.run_dir)
    if args.json:
        import json as _json
        print(_json.dumps(rep.to_json(), ensure_ascii=False, indent=2))
    else:
        print(rep.to_text())
    return 1 if rep.verdict == "flawed" else 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_cli())
