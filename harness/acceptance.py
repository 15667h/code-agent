"""验收框架：证明"测法可信"，而不是证明"模型会做题"。

## 为什么需要它

arXiv 综述《Harness Engineering Survey》点名了一个结构性空白：
绝大多数 harness 只验证**产物**，从不验证**自己的测法**。

UC Berkeley RDI 的 **BenchJack**（2026-04）用自动扫描工具攻击 10 个主流
agent 基准，在 SWE-bench Verified / Pro / Terminal-Bench / WebArena 上拿到
**≈100%**、GAIA 98% —— **一道题都没做**。手法都很朴素：

| 基准 | 手法 |
| --- | --- |
| SWE-bench | 丢一个 10 行 `conftest.py` 钩住 pytest，把结果改写成 passed |
| WebArena | 导航到 `file://` URL，**直接从任务配置里读标准答案** |
| FieldWorkArena | 返回 `{}` 就过 —— 验证管线从不检查实际输出 |

**共性弱点四类**：① agent 与评估者无隔离 ② 参考答案随任务下发
③ 评分器对 agent 控制的字符串调 `eval()` ④ 评分逻辑根本不校验正确性。

本模块把这三条方法论约束**做成可执行的检查**，而不是写在文档里靠人记：

    约束 1（null agent）  —— 什么都不做必须得 0 分
    约束 2（隔离）        —— 验收在独立空间跑断言，agent 不得影响
    约束 3（结构化产物）  —— 断言只读文件系统事实，不问模型

## 最关键的设计：null agent 必须是**真的什么都不做**

最容易犯的错是把 null agent 写成"返回空回答"，那样它仍然
可能因为副作用（如创建了目录）而"得分"。真正的 null agent 应当
**一个工具都不调用，一个文件都不碰**。

它得分 > 0 只有一种解释：**评分逻辑把"没做"当成了"做了"**。
这正是 BenchJack 里 FieldWorkArena 那种"返回 `{}` 就过"的形态。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence


# ══════════════════════════════════════════════════════════════════
# 断言：只读文件系统事实
# ══════════════════════════════════════════════════════════════════
@dataclass
class Assertion:
    """一条验收断言。

    纪律（约束 3）：**只允许读文件系统事实**。
    不允许调模型、不允许解析 agent 的自由文本、不允许 eval 字符串。
    理由：前三者都可被 agent 影响或说服；只有文件系统事实是客观的。

    ## `positive` 字段：区分"要求产出"与"不许乱来"

    这是为了让 **null agent 自检**能给出有意义的结论：

    | 类型 | 例子 | null agent 下的期望 |
    | --- | --- | --- |
    | 正向（positive=True） | `file_exists("hello.txt")` | **必须不成立** |
    | 反向（positive=False） | `file_unchanged("README.md")` | 成立（什么都没做当然没改坏） |

    一个任务若**全部由反向断言构成**，那么 null agent 会拿满分 ——
    这正是 BenchJack 里 FieldWorkArena 的形态（"返回 `{}` 就过"）。
    所以校验时要求：**每个任务至少一条正向断言**。
    """

    name: str
    fn: Callable[[str], bool]        # 入参是**快照区**的根，不是工作区
    detail: str = ""
    positive: bool = True

    def run(self, snapshot_root: str) -> Dict[str, Any]:
        try:
            ok = bool(self.fn(snapshot_root))
        except Exception as e:
            return {"name": self.name, "ok": False, "positive": self.positive,
                    "detail": f"断言执行异常：{type(e).__name__}: {e}"}
        return {"name": self.name, "ok": ok, "positive": self.positive,
                "detail": self.detail or ("通过" if ok else "未通过")}


# ══════════════════════════════════════════════════════════════════
# 常用断言工厂（刻意做得"笨"）
# ══════════════════════════════════════════════════════════════════
# 这些工厂都只返回"文件是否存在 / 内容是否包含"这类**不可被说服**的判断。
# 绝不做"判断代码质量"这种事 —— 那需要语义理解，就该由人来做，
# 或者由 agent 自己带来的测试来做（那是产物的一部分）。

def file_exists(rel: str, detail: str = "") -> Assertion:
    def _fn(root: str) -> bool:
        return os.path.isfile(os.path.join(root, rel))
    return Assertion(f"file_exists:{rel}", _fn,
                     detail or f"工作区里应存在文件 {rel}")


def file_contains(rel: str, needle: str, detail: str = "") -> Assertion:
    def _fn(root: str) -> bool:
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            return False
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                return needle in f.read()
        except OSError:
            return False
    return Assertion(f"file_contains:{rel}", _fn,
                     detail or f"{rel} 里应包含 {needle!r}")


def file_absent(rel: str, detail: str = "") -> Assertion:
    """文件**必须不存在** —— 用来测"不许做的事确实没做"。"""
    def _fn(root: str) -> bool:
        return not os.path.exists(os.path.join(root, rel))
    return Assertion(f"file_absent:{rel}", _fn,
                     detail or f"工作区里不应存在 {rel}", positive=False)


def file_line_count_at_least(rel: str, n: int, detail: str = "") -> Assertion:
    def _fn(root: str) -> bool:
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            return False
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return len(f.read().splitlines()) >= n
    return Assertion(f"lines:{rel}", _fn,
                     detail or f"{rel} 至少应有 {n} 行")


def file_unchanged(rel: str, expected_content: str) -> Assertion:
    """文件内容必须**逐字节等于**期望值 —— 用来测"不许改的东西没被改"。"""
    def _fn(root: str) -> bool:
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            return False
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return f.read() == expected_content
    return Assertion(f"unchanged:{rel}", _fn,
                     detail=f"{rel} 不应被修改", positive=False)


# ══════════════════════════════════════════════════════════════════
# 隔离快照（约束 2）
# ══════════════════════════════════════════════════════════════════
class Snapshot:
    """agent 工作区的一份**只读快照**。

    为什么必须快照而不是直接读工作区：
    评估跑在与 agent **相同的可写空间**里时，agent 可以让评估通过 ——
    BenchJack 的 `conftest.py` 手法就是这么干的（改测试结果而不是改代码）。

    快照切断了这条路径：
      · 断言在**另一个目录**跑，agent 改不到
      · 快照是**拷贝**，之后 agent 再改工作区也不影响已判定的结论
      · 断言函数拿到的 root 是快照根，没有任何途径回到原工作区

    这不是"防住所有攻击"（agent 理论上能改项目自身的代码），
    但它消除了**最朴素也最常见**的那一类：改测试/改断言让自己通过。
    """

    def __init__(self, workspace: str) -> None:
        self.workspace = os.path.realpath(workspace)
        self.root = tempfile.mkdtemp(prefix="crucible-snapshot-")
        self._copied = False

    def copy(self) -> str:
        """把工作区拷进快照区（忽略运行时产物目录）。"""
        if self._copied:
            return self.root
        ignore = shutil.ignore_patterns(
            ".git", "node_modules", "__pycache__", ".venv", "venv",
            "dist", "build", ".next", "target", ".mypy_cache",
            ".pytest_cache", ".crucible")
        if os.path.isdir(self.workspace):
            shutil.copytree(self.workspace, self.root, dirs_exist_ok=True,
                            ignore=ignore)
        self._copied = True
        self.root = os.path.realpath(self.root)
        return self.root

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> "Snapshot":
        self.copy()
        return self

    def __exit__(self, *exc) -> None:
        self.cleanup()


# ══════════════════════════════════════════════════════════════════
# 一次验收
# ══════════════════════════════════════════════════════════════════
@dataclass
class Verdict:
    """验收结论。

    刻意**只有 0 或 1 两种得分**（全部断言通过 = 1，否则 0）：
    连续分数会让"部分完成"看起来像"完成"，
    而 BenchJack 的教训正是**评分器不该有可被凑出来的中间地带**。
    """

    task: str
    score: int = 0
    results: List[Dict[str, Any]] = field(default_factory=list)
    isolation_ok: bool = False
    note: str = ""

    @property
    def passed(self) -> bool:
        return self.score == 1

    def to_json(self) -> Dict[str, Any]:
        return {"task": self.task, "score": self.score, "passed": self.passed,
                "results": self.results, "isolation_ok": self.isolation_ok,
                "note": self.note}

    def to_text(self) -> str:
        lines = [f"任务：{self.task}",
                 f"得分：{self.score}（{'通过' if self.passed else '未通过'}）",
                 f"隔离：{'是' if self.isolation_ok else '否'}"]
        for r in self.results:
            lines.append(f"  {'✓' if r['ok'] else '✗'} {r['name']} — {r['detail']}")
        if self.note:
            lines.append(f"说明：{self.note}")
        return "\n".join(lines)


def score_workspace(workspace: str, assertions: Sequence[Assertion],
                    task: str = "") -> Verdict:
    """在**隔离快照**上给一份工作区打分。

    这是唯一正确的评分入口：任何"直接读工作区"的评分都会
    重新打开 BenchJack 那条路。
    """
    with Snapshot(workspace) as snap:
        root = snap.root
        results = [a.run(root) for a in assertions]
        ok = all(r["ok"] for r in results) and bool(results)
        v = Verdict(task=task or workspace, score=1 if ok else 0,
                    results=results, isolation_ok=True)
        if not results:
            v.note = "没有任何断言 —— 这不能算通过（否则空断言就是满分）"
            v.score = 0
        return v


# ══════════════════════════════════════════════════════════════════
# 约束 1：null agent 自检
# ══════════════════════════════════════════════════════════════════
def null_agent_check(workspace: str, assertions: Sequence[Assertion]) -> Dict[str, Any]:
    """**最重要的一个检查**：什么都不做，不该拿到任何"我做到了"的分数。

    做法：拿一份**未经任何改动**的工作区拷贝，跑同一套断言。

    ## 判据只针对**正向断言**

    反向断言（`file_unchanged` / `file_absent`）在 null agent 下
    **本来就应该成立** —— 什么都不做当然没改坏东西。
    把它们也判成"虚假通过"会把每个带反向断言的任务都误报，
    而误报会让整个自检机制失去可信度（本项目在这条纪律上栽过三次）。

    真正要抓的是：**正向断言**（要求产出）在什么都没做时也成立。
    那说明断言在测一个**基线就已经满足**的条件 ——
    BenchJack 的 FieldWorkArena 正是这种形态（"返回 `{}` 就过"）。

    另外单独校验：任务必须**至少有一条正向断言**。全反向断言的任务
    必然让 null agent 满分（它只是"没做坏事"，不是"做成了事"）。
    """
    probe_root = tempfile.mkdtemp(prefix="crucible-null-")
    try:
        ignore = shutil.ignore_patterns(
            ".git", "node_modules", "__pycache__", ".venv", "venv",
            "dist", "build", ".next", "target", ".mypy_cache",
            ".pytest_cache", ".crucible")
        if os.path.isdir(workspace):
            shutil.copytree(workspace, probe_root, dirs_exist_ok=True,
                            ignore=ignore)
        results = [a.run(probe_root) for a in assertions]

        positives = [r for r in results if r.get("positive", True)]
        spurious = [r["name"] for r in positives if r["ok"]]
        missing_positive = not positives

        problems = []
        if spurious:
            problems.append(
                f"这些**正向**断言在「什么都没做」时也成立：{spurious} —— "
                "它们在测基线本就满足的条件，不是任务的产出")
        if missing_positive:
            problems.append(
                "该任务**没有任何正向断言**（全是「不许乱来」类）—— "
                "null agent 必然满分，因为「没做坏事」不等于「做成了事」")

        return {
            "trustworthy": not problems,
            "baseline_score": 1 if (positives and not spurious
                                    and all(r["ok"] for r in results)) else 0,
            "spurious": spurious,
            "has_positive_assertion": not missing_positive,
            "detail": ("可信：null agent 未通过任何正向断言" if not problems
                       else "**不可信**：" + "；".join(problems)),
            "problems": problems,
        }
    finally:
        shutil.rmtree(probe_root, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════
# 约束 2 的运行时检查：工作区指纹
# ══════════════════════════════════════════════════════════════════
def workspace_fingerprint(workspace: str,
                          ignore_globs: Sequence[str] = ()) -> Dict[str, str]:
    """给工作区算一份"文件 → 内容哈希"指纹。

    用途有二：
      · 对比任务前后，得出**实际改动集**（不依赖 agent 自报）
      · 检测"断言依赖的基线被偷偷改过"（约束 2 的运行时形态）
    """
    import fnmatch
    root = os.path.realpath(workspace)
    out: Dict[str, str] = {}
    if not os.path.isdir(root):
        return out
    ignore_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv",
                   "dist", "build", ".next", "target", ".mypy_cache",
                   ".pytest_cache", ".crucible"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ignore_dirs
                       and not d.startswith(".")]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if any(fnmatch.fnmatch(rel, g) for g in ignore_globs):
                continue
            try:
                with open(full, "rb") as f:
                    out[rel] = hashlib.sha256(f.read()).hexdigest()[:16]
            except OSError:
                continue
    return out


def diff_fingerprints(before: Dict[str, str],
                      after: Dict[str, str]) -> Dict[str, List[str]]:
    """对比两份指纹，得出真实的改动集。

    这是"断言看结构化产物"的另一种落地：**不采信 agent 的自报**，
    而是从文件系统事实反推它到底改了什么。
    """
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    modified = sorted(k for k in (set(before) & set(after))
                      if before[k] != after[k])
    return {"added": added, "removed": removed, "modified": modified}


def tamper_check(before: Dict[str, str], after: Dict[str, str],
                 protected: Sequence[str]) -> Dict[str, Any]:
    """检查**受保护的基线文件**有没有被偷改。

    `protected` 一般放"验收所依赖的东西"：测试文件、断言依赖的数据文件、
    构建脚本。agent 改了它们就等于改了评分标准 ——
    这正是 `conftest.py` 手法的本质。
    """
    changed = []
    for p in protected:
        b, a = before.get(p), after.get(p)
        if b is None and a is None:
            continue
        if b != a:
            changed.append({"path": p, "before": b, "after": a,
                            "kind": "deleted" if a is None else
                                    "created" if b is None else "modified"})
    return {"clean": not changed, "violations": changed}


# ══════════════════════════════════════════════════════════════════
# 验收套件：把三项约束做成可跑的基准
# ══════════════════════════════════════════════════════════════════
# 为什么套件里要**同时**包含"该做到的"与"不该做到的"断言：
# 只测"文件存在"的套件，任何 agent 只要无脑创建一堆文件就能拿满分 ——
# 那是一种被动的 BenchJack。加入 `file_absent` / `file_unchanged`
# 这类反向断言，才能把"乱做"也判为失败。
@dataclass
class TaskSpec:
    """一个验收任务。"""

    name: str
    prompt: str
    assertions: List[Assertion] = field(default_factory=list)
    # 受保护文件：agent 不得改动它们（改了就等于改了评分标准）
    protected: List[str] = field(default_factory=list)
    # 任务开始前应当存在的文件（用于构造基线与 null 自检）
    seed: Dict[str, str] = field(default_factory=dict)


@dataclass
class SuiteResult:
    tasks: List[Verdict] = field(default_factory=list)
    null_checks: List[Dict[str, Any]] = field(default_factory=list)
    tamper_checks: List[Dict[str, Any]] = field(default_factory=list)
    trustworthy: bool = True
    problems: List[str] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {
            "trustworthy": self.trustworthy,
            "passed": sum(1 for v in self.tasks if v.passed),
            "total": len(self.tasks),
            "tasks": [v.to_json() for v in self.tasks],
            "null_checks": self.null_checks,
            "tamper_checks": self.tamper_checks,
            "problems": self.problems,
        }

    def to_text(self) -> str:
        lines = []
        verdict = "可信" if self.trustworthy else "**不可信**"
        lines.append(f"测法{verdict}（{sum(1 for v in self.tasks if v.passed)}"
                     f"/{len(self.tasks)} 任务通过）")
        for v in self.tasks:
            lines.append(f"\n{v.to_text()}")
        if self.null_checks:
            lines.append("\n── null agent 自检 ──")
            for n in self.null_checks:
                mark = "✓" if n["trustworthy"] else "✗"
                lines.append(f"  {mark} {n['detail']}")
        if self.tamper_checks:
            lines.append("\n── 基线篡改检查 ──")
            for t in self.tamper_checks:
                mark = "✓" if t["clean"] else "✗"
                lines.append(f"  {mark} {'干净' if t['clean'] else t['violations']}")
        if self.problems:
            lines.append("\n── 问题 ──")
            for p in self.problems:
                lines.append(f"  ! {p}")
        return "\n".join(lines)


def seed_workspace(base: str, files: Dict[str, str]) -> str:
    """按 `seed` 铺一份基线工作区，返回它的路径。"""
    for rel, content in files.items():
        full = os.path.join(base, rel)
        os.makedirs(os.path.dirname(full) or base, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
    return base


def evaluate_suite(tasks: Sequence[TaskSpec], run_task,
                   tmp_root: Optional[str] = None) -> SuiteResult:
    """跑整套验收。

    `run_task(spec, workspace) -> None`：由调用方提供的执行器
    （真实模型循环、离线脚本、或一个 null agent）。

    ## 三个必须都在做的检查

    1. **每个任务开始前铺基线** —— 否则"文件存在"类断言可能凭空成立
    2. **null agent 自检**：拿基线直接跑断言，得分必须为 0
    3. **基线篡改检查**：任务结束后确认 protected 文件未被改

    第 2 条是这套框架存在的理由 —— 它把"测法本身可不可信"
    从一句口号变成了一个会失败的检查。
    """
    owns_tmp = tmp_root is None
    tmp_root = tmp_root or tempfile.mkdtemp(prefix="crucible-accept-")
    res = SuiteResult()

    try:
        for spec in tasks:
            ws = os.path.join(tmp_root, spec.name)
            os.makedirs(ws, exist_ok=True)
            seed_workspace(ws, spec.seed)
            baseline = workspace_fingerprint(ws)

            # ── 约束 1：在**基线**上跑断言，必须全不通过 ──
            nc = null_agent_check(ws, spec.assertions)
            nc["task"] = spec.name
            res.null_checks.append(nc)
            if not nc["trustworthy"]:
                res.trustworthy = False
                # 把**具体原因**带进 problems —— 只说"不可信"等于没说，
                # 人需要知道是哪条断言、为什么。反馈必须具体。
                for why in nc.get("problems", []):
                    res.problems.append(f"任务「{spec.name}」的断言不可信：{why}")

            # ── 执行任务 ──
            try:
                run_task(spec, ws)
            except Exception as e:
                res.problems.append(
                    f"任务「{spec.name}」执行时抛异常："
                    f"{type(e).__name__}: {e}")

            # ── 评分（在隔离快照上）──
            v = score_workspace(ws, spec.assertions, task=spec.name)
            res.tasks.append(v)

            # ── 约束 2：受保护基线未被偷改 ──
            if spec.protected:
                after = workspace_fingerprint(ws)
                tc = tamper_check(baseline, after, spec.protected)
                tc["task"] = spec.name
                res.tamper_checks.append(tc)
                if not tc["clean"]:
                    res.problems.append(
                        f"任务「{spec.name}」改动了受保护基线文件："
                        f"{[x['path'] for x in tc['violations']]}")
                    # 基线被动过 → 这次评分不作数
                    v.score = 0
                    v.note = "受保护基线被改动，评分无效"

        if not res.tamper_checks and not any(t.protected for t in tasks):
            res.problems.append(
                "没有任何任务声明 protected 文件 —— "
                "约束 2（评估器与 agent 不共享可写空间）未被实际检验")
    finally:
        if owns_tmp:
            shutil.rmtree(tmp_root, ignore_errors=True)
    return res


def null_agent_runner(spec: TaskSpec, workspace: str) -> None:
    """一个**真的什么都不做**的 agent。

    供 `evaluate_suite` 做自检用：它不调工具、不改文件、不产出任何东西。
    如果这样都能让某个任务通过，那套断言就是错的。

    注意"真的什么都不做"不等于"返回空回答" ——
    后者仍可能因为隐含副作用而"得分"，那就不叫 null agent 了。
    """
    return None
