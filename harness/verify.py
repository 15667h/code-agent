"""验证层。

## 铁律：只看结构化数据

书 §1 的职责表里，Verify 一栏写着：

> 自动判断操作结果的对错；**安全检查只看结构化数据（如工具返回的 JSON 字段），
> 而不看模型自由生成的文本，因为后者可能已被提示注入操纵**。

这条纪律决定了本模块的接口形状：**所有判定函数的输入都是 `ToolResult`
的结构化字段与文件系统事实，绝不解析 `result.text`**。
如果把模型的输出文本当判据，提示注入就直接赢了 ——
攻击者只需让模型在输出里写「一切正常」。

## 提议者—审核者

书 §1 的五个设计模式之首：产出与评判由**不共享上下文**的两个角色承担，
评判方看到的是**产物本身**，而不是产出方的推理过程。前提是**自审不可靠**。

本模块即"审核者"：它不读模型的 reasoning，只看落盘后的客观事实。
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from .protocol import ERR_TOOL, ToolResult


# ══════════════════════════════════════════════════════════════════
# 检查项
# ══════════════════════════════════════════════════════════════════
@dataclass
class Check:
    """一条验证规则。

    `fn` 接收 `VerifyContext`，返回 `(ok, 说明)`。
    **约定：fn 只允许读取 ctx 中的结构化字段与文件系统事实。**
    """

    name: str
    fn: Callable[["VerifyContext"], "tuple[bool, str]"]
    severity: str = "error"      # error | warn


@dataclass
class VerifyContext:
    """验证上下文 —— 全部字段都是结构化或客观事实。"""

    workspace: str
    tool: str
    args: Dict[str, Any]
    result: ToolResult
    # 客观事实（由 Harness 采集，而非模型声明）
    files_exist: Dict[str, bool] = field(default_factory=dict)
    baseline_hashes: Dict[str, str] = field(default_factory=dict)


@dataclass
class VerifyReport:
    ok: bool
    items: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {"ok": self.ok, "items": self.items}


class Verifier:
    def __init__(self, workspace: str) -> None:
        self.workspace = os.path.realpath(workspace)
        self._checks: List[Check] = []
        self._register_builtin()

    # ---------- 注册 ----------
    def add(self, check: Check) -> None:
        self._checks.append(check)

    def _register_builtin(self) -> None:
        # 1. 工具自报成功，但结构化字段矛盾 → 不通过
        self.add(Check("结构一致性", _check_structural_consistency))
        # 2. 声明改动的文件必须真的存在于磁盘
        self.add(Check("改动已落盘", _check_files_written))
        # 3. 声明的路径必须都在工作区内（越界即使工具没报错也算失败）
        self.add(Check("无越界写入", _check_no_escape))
        # 4. 无副作用的工具声明了 files_changed → 矛盾
        self.add(Check("副作用声明一致", _check_side_effect_declared, severity="warn"))

    # ---------- 运行 ----------
    def run(self, ctx: VerifyContext) -> VerifyReport:
        items: List[Dict[str, Any]] = []
        ok = True
        for c in self._checks:
            try:
                passed, why = c.fn(ctx)
            except Exception as e:
                passed, why = False, f"检查项执行异常：{type(e).__name__}: {e}"
            items.append({"name": c.name, "ok": passed, "detail": why,
                          "severity": c.severity})
            if not passed and c.severity == "error":
                ok = False
        return VerifyReport(ok=ok, items=items)

    def prepare(self, tool: str, args: Dict[str, Any], result: ToolResult) -> VerifyContext:
        files_exist = {}
        for p in result.files_changed:
            full = os.path.join(self.workspace, p)
            files_exist[p] = os.path.exists(full)
        return VerifyContext(workspace=self.workspace, tool=tool, args=args,
                             result=result, files_exist=files_exist)


# ══════════════════════════════════════════════════════════════════
# 内置检查
# ══════════════════════════════════════════════════════════════════
def _check_structural_consistency(ctx: VerifyContext) -> "tuple[bool, str]":
    r = ctx.result
    if r.ok and r.exit_code is not None and r.exit_code != 0:
        return False, f"工具报告 ok=true，但 exit_code={r.exit_code}，两者矛盾。"
    if r.ok and r.error_layer:
        return False, f"工具报告 ok=true，但同时带有 error_layer={r.error_layer}。"
    if not r.ok and not r.error_layer:
        return False, "工具报告失败但没有给出 error_layer，无法定位故障层。"
    return True, "结构化字段自洽。"


def _check_files_written(ctx: VerifyContext) -> "tuple[bool, str]":
    if ctx.tool not in ("apply_patch",):
        return True, "不适用。"      # 只对编辑类工具检查
    missing = [p for p, ex in ctx.files_exist.items() if not ex]
    if missing:
        return False, f"工具声明修改了 {missing}，但这些文件在磁盘上不存在。"
    return True, f"声明的 {len(ctx.files_exist)} 个改动文件均存在。"


def _check_no_escape(ctx: VerifyContext) -> "tuple[bool, str]":
    ws = ctx.workspace
    for p in ctx.result.files_changed:
        full = os.path.realpath(os.path.join(ws, p))
        if full != ws and not full.startswith(ws + os.sep):
            return False, f"声明的改动路径 {p} 越出工作区。"
    return True, "全部改动均在工作区内。"


def _check_side_effect_declared(ctx: VerifyContext) -> "tuple[bool, str]":
    r = ctx.result
    if not r.side_effect and r.files_changed:
        return False, "工具声明无副作用，却报告了文件改动，声明不一致。"
    return True, "副作用声明与改动报告一致。"


# ══════════════════════════════════════════════════════════════════
# 即时反馈：编辑后自动跑检查（书 §5「即时语法反馈机制」）
# ══════════════════════════════════════════════════════════════════
# 书原话：
# > Agent 修改代码后，不应等到用户明确要求测试时才检查语法。
# > 更高效的做法是：文件写入操作一完成，工具层就自动运行相应的 linter
# > 或语法检查器，将检查结果作为工具返回值的一部分呈现给 Agent。
# > 就像程序员在 IDE 中打错一个括号，编辑器立刻画红线提醒一样。

LINTER_BY_EXT: Dict[str, List[str]] = {
    ".py": ["python", "-m", "py_compile"],           # 标准库自带，零依赖
    ".json": ["python", "-c", "import json,sys;json.load(open(sys.argv[1],encoding='utf-8'))"],
}


def quick_lint(paths: Sequence[str], workspace: str,
               timeout_s: int = 20) -> List[Dict[str, Any]]:
    """对刚落盘的文件做**廉价**语法检查。

    刻意选 `py_compile` 而不是 flake8/mypy：即时反馈的价值在于**快**，
    慢的检查会让每一轮工具调用都卡住。重检查留给显式的 `shell pytest`。

    书 §5 的「边界」纪律也适用：工具要说明自己**做不到什么**。
    所以这里只做语法级检查，不能代替类型检查与单元测试，
    返回结果里会明确写清这一点。
    """
    out: List[Dict[str, Any]] = []
    ws = os.path.realpath(workspace)
    for rel in paths:
        full = os.path.join(ws, rel)
        if not os.path.isfile(full):
            continue
        ext = os.path.splitext(rel)[1].lower()
        cmd = LINTER_BY_EXT.get(ext)
        if not cmd:
            continue
        try:
            p = subprocess.run(cmd + [full], cwd=ws, capture_output=True,
                               text=True, timeout=timeout_s)
            out.append({
                "path": rel,
                "ok": p.returncode == 0,
                "exit_code": p.returncode,
                # 只保留前若干行 —— 语法错误的要点总在前面
                "stderr": (p.stderr or "")[:2000],
                "checker": cmd[1],
            })
        except (subprocess.TimeoutExpired, OSError) as e:
            out.append({"path": rel, "ok": False, "exit_code": None,
                        "stderr": f"检查器执行失败：{e}", "checker": cmd[1]})
    return out


def lint_note(lint_results: Sequence[Dict[str, Any]]) -> str:
    """把 lint 结果渲染成给模型的简短反馈。

    Codex 的做法是把 lint 结果作为**工具返回值的一部分**（见其
    `Write` 工具描述里的「自动 lint」）。这里照做，并说明检查的局限。
    """
    if not lint_results:
        return ""
    bad = [r for r in lint_results if not r["ok"]]
    if not bad:
        names = ", ".join(r["path"] for r in lint_results)
        return f"\n\n[即时检查] 语法通过（{names}）。注意：这只检查语法，不代表类型与测试都通过。"
    lines = ["\n\n[即时检查] 发现语法问题，请先修好再继续："]
    for r in bad:
        lines.append(f"  · {r['path']}（{r['checker']} 退出码 {r['exit_code']}）")
        if r["stderr"]:
            for ln in r["stderr"].strip().splitlines()[:12]:
                lines.append(f"      {ln}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# 目标达成判定（对齐 Codex 的 /goal 与 Evaluator）
# ══════════════════════════════════════════════════════════════════
@dataclass
class GoalCheck:
    """一条「达标条件」及其**证据**。

    书 §5 反复强调一件事：**「给出了回答」不等于「完成了任务」**，
    并把「测试通过而非代码写完」定义为完成标准。
    因此每条条件的达成都必须挂一份可复核的证据，
    而不是让模型声明一句「已完成」。
    """

    text: str
    satisfied: bool
    evidence: str = ""


class GoalVerifier:
    """目标自证。对应 Codex `/goal` 的 Evaluator 角色。

    **它只判断证据是否充分，不参与实际工作** —— 这正是书 §1
    「提议者—审核者」的要求：评判方与产出方不共享上下文，判断才客观。
    """

    def __init__(self, conditions: List[str]) -> None:
        self.conditions = list(conditions)
        self._results: List[GoalCheck] = []

    def record(self, text: str, satisfied: bool, evidence: str) -> None:
        self._results.append(GoalCheck(text, satisfied, evidence))

    def report(self) -> Dict[str, Any]:
        done = sum(1 for r in self._results if r.satisfied)
        return {
            "total": len(self.conditions),
            "checked": len(self._results),
            "satisfied": done,
            "all_met": bool(self.conditions) and done >= len(self.conditions),
            "items": [{"text": r.text, "ok": r.satisfied, "evidence": r.evidence}
                      for r in self._results],
        }

    def unmet(self) -> List[str]:
        met = {r.text for r in self._results if r.satisfied}
        return [c for c in self.conditions if c not in met]
