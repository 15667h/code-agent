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
import re
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
    seq: Optional[int] = None       # 证据来自轨迹第几条消息（可回溯到 rollout）


# ══════════════════════════════════════════════════════════════════
# 条件 → 可观测信号
# ══════════════════════════════════════════════════════════════════
# 设计要点：**不解析模型的话**，而是把条件文本翻译成「什么样的工具结果
# 才算这条条件的证据」。这一步是确定性的、可复现的、可单元测试的 ——
# 与 verify.py 开头那条铁律一致：只看结构化数据。
#
# 为什么不用 LLM 判官：判官要读模型输出，而模型输出可被提示注入操纵
# （书 §1）。更要紧的是 —— 一个恒真的判官比没有判官更坏，它会把
# "看起来完成了" 固化成 "已完成"。

_TOOL_NAMES = ("read_file", "list_dir", "glob", "grep", "apply_patch", "shell")

# 中文动词 → 工具名。**刻意不用模糊语义**，只做明确的词面映射；
# 认不出任何信号的条件下文会被标为「无法绑定证据」，如实报告而不是猜。
_VERB_TO_TOOL = (
    ("apply_patch", ("修改", "改动", "写入", "新建", "创建", "生成", "补丁",
                     "删除", "重命名", "重构", "实现", "edit", "modify",
                     "create", "write", "add", "patch", "delete", "implement")),
    ("read_file",   ("读取", "读一下", "查看内容", "看内容", "打开文件",
                     "read", "open file", "cat")),
    ("grep",        ("搜索内容", "内容搜索", "检索", "内含", "grep")),
    ("glob",        ("按文件名", "文件名匹配", "查找文件", "找到文件", "glob")),
    ("list_dir",    ("列出", "列举", "目录结构", "项目结构", "list", "ls")),
    ("shell",       ("运行", "执行命令", "跑测试", "构建", "编译", "安装",
                     "run", "execute", "build", "test", "install", "npm",
                     "pytest", "git ")),
)

# 读类工具：它们出现在 actual_paths 里只说明"碰过这个路径"，**不代表产生了它**。
# 路径证据必须来自写操作，否则 `read_file a.txt` 会让"创建 a.txt"误判为满足。
_READ_TOOLS = frozenset({"read_file", "list_dir", "glob", "grep"})

# 「写」类工具：真的会产生/改变文件的动作。条件要求"创建/修改 X"时，
# 任何一个写工具命中 X 都算证据 —— 因为**结果**才是判据。
# 实测教训：模型经常用 `shell`（`printf > notes/hello.txt`）而不是 apply_patch
# 来建文件。若只认 apply_patch，闸门就会在文件确实已落盘时仍报"未满足"，
# 也就是闸门自己在说假话 —— 那比没有闸门更坏。
_WRITE_TOOLS = frozenset({"apply_patch", "shell"})

# 条件文本里出现这些词 = 该条件要求的是**写**类动作
_WRITE_INTENT = ("创建", "新建", "写入", "生成", "修改", "改动", "删除", "重命名",
                 "保存", "添加", "补丁", "输出到",
                 "create", "write", "generate", "modify", "edit", "delete",
                 "add", "save", "patch", "produce")

# 可以被判定为「已执行」的命令名（出现在条件里即视为要求实际跑一次）
_COMMAND_TOKENS = ("pytest", "unittest", "npm", "node", "python", "pip",
                   "git", "make", "cargo", "go test", "mvn", "gradle",
                   "npm run", "py_compile")

# 路径样 token：带扩展名，或含 `/`。刻意保守，不把普通英文单词当路径。
_PATH_RE = re.compile(
    r"(?:[A-Za-z0-9_.\-]*/)+[A-Za-z0-9_.\-*]+"          # 含斜杠的路径
    r"|[A-Za-z0-9_\-*]+\.[A-Za-z0-9]{1,8}\b"            # 带扩展名的文件名
)


@dataclass
class GoalSignal:
    """一条条件翻译出的可观测信号。"""

    tool: Optional[str] = None
    paths: List[str] = field(default_factory=list)
    command: Optional[str] = None
    # 条件要求的是"产生/改变文件"类动作（而不是仅仅触碰某个路径）
    wants_write: bool = False

    @property
    def has_signal(self) -> bool:
        """有没有任何可用于绑定证据的信号。没有 = 无法自动判定。"""
        return bool(self.tool or self.paths or self.command)


def _extract_signals(text: str) -> GoalSignal:
    """把一条自然语言条件翻译成可观测信号。

    只做**词面级**翻译，不做语义推理 —— 认不出就留空，
    由上层如实报告「无法绑定证据」，而不是猜一个。
    """
    low = (text or "").lower()
    sig = GoalSignal()

    # 1. 路径
    for m in _PATH_RE.finditer(text or ""):
        tok = m.group(0).strip("`《》\"'(),;，。")
        if tok and tok not in sig.paths:
            sig.paths.append(tok)

    # 2. 命令
    for c in _COMMAND_TOKENS:
        if c in low:
            sig.command = c
            break

    # 3. 动词 → 工具
    for tool, verbs in _VERB_TO_TOOL:
        if any(v in low for v in verbs):
            sig.tool = tool
            break

    # 4. 显式写出工具名（最可靠的信号，优先级最高）
    for name in _TOOL_NAMES:
        if name in low:
            sig.tool = name
            break

    # 5. 只有命令没有工具 → 命令必走 shell
    if sig.command and not sig.tool:
        sig.tool = "shell"

    # 6. 写意图
    sig.wants_write = any(w in low for w in _WRITE_INTENT)

    return sig


def _path_match(pattern_token: str, actual: str) -> bool:
    """条件里的路径 token 是否覆盖了某个实际路径。

    用 `fnmatch` 语义，并额外做 basename 兜底 —— 条件常写 `index.html`
    而结果是 `snake/index.html`，这两者指的是同一个文件。
    """
    import fnmatch
    p = (pattern_token or "").replace("\\", "/").strip()
    a = (actual or "").replace("\\", "/").strip()
    if not p or not a:
        return False
    if p == a:
        return True
    if fnmatch.fnmatch(a, p) or fnmatch.fnmatch(a, "*/" + p.lstrip("/")):
        return True
    if "/" not in p and (a == p or a.endswith("/" + p)):
        return True
    return False


class GoalVerifier:
    """目标自证。对应 Codex `/goal` 的 Evaluator 角色。

    **它只判断证据是否充分，不参与实际工作** —— 这正是书 §1
    「提议者—审核者」的要求：评判方与产出方不共享上下文，判断才客观。

    ## 判定语义（刻意保守，三类状态）

    | 状态 | 含义 |
    | --- | --- |
    | satisfied | 挂上了**确定性匹配**到的工具结果证据 |
    | pending   | 条件可绑定，但本次会话还没出现对应证据 |
    | unmatched | 条件里没有任何可识别信号，**无法自动判定**（如实暴露，不猜） |

    `all_met` 的含义是「所有条件都挂上了证据」，而不是「模型说完成了」。

    ## 为什么不用 LLM 当判官

    验证层的第一条铁律是只看结构化数据。让模型来判断自己的任务完成度，
    等于把审核者并进提议者 —— 而书 §1 明确写了「前提是自审不可靠」。
    这里是确定性匹配器：`apply_patch` 真的写了 `index.html`，
    就只能证明「有文件被写入」，**不能**证明「写进去的内容是对的」。
    所以证据文本会如实写清这一点（见 `_evidence_text`）。
    """

    def __init__(self, conditions: List[str]) -> None:
        self.conditions = list(conditions)
        self._signals: List[GoalSignal] = [_extract_signals(c) for c in self.conditions]
        self._results: List[GoalCheck] = []
        # 每条条件的累计覆盖：已证据化的路径 / 已命中的工具
        self._covered: List[set] = [set() for _ in self.conditions]
        self._tool_hits: List[set] = [set() for _ in self.conditions]
        self._cmd_hits: List[set] = [set() for _ in self.conditions]
        self._evidence: List[List[str]] = [[] for _ in self.conditions]
        # 被**外部显式登记**为已满足的条件索引。
        # 必须与信号判定分开存：否则无信号条件（"文案读起来自然"）会被
        # `_is_satisfied` 的 `has_signal` 早退吞掉，逃生口形同虚设。
        self._external: set = set()

    # ---------- 观察（唯一的自动入口） ----------
    def observe(self, tool: str, args: Dict[str, Any], result: ToolResult,
                seq: Optional[int] = None) -> List[str]:
        """喂进一次工具执行结果，尝试把它挂到各条条件上。

        返回**本次刚刚变成 satisfied 的条件文本**（供循环发事件）。

        纪律：
          · 只接受 `result.ok is True` 的结果 —— 失败的工具输出不是证据。
          · 只做确定性匹配，匹配不上就什么都不做（宁可 pending）。
        """
        if not self.conditions or not result.ok:
            return []

        actual_paths = self._paths_of(args, result)
        newly: List[str] = []

        for i, sig in enumerate(self._signals):
            if not sig.has_signal:
                continue
            before = self._is_satisfied(i)

            # ── 证据门控 ──
            #
            # 目标是"产生/改变文件"（wants_write）时：
            #   · 必须来自**写类工具**（apply_patch / shell）
            #   · 读类工具即使报出同一路径也**不算** —— 否则 `read_file a.txt`
            #     会把"创建 a.txt"误判为满足（一个真实的假阳性通道）
            #   · 不要求恰好是 apply_patch：实测模型常用 `shell` 直接重定向写文件，
            #     若死认工具名，闸门会在文件确实落盘时仍报未满足（假阴性）
            #
            # 目标只是"看到/读到某文件"时：读类与写类都算证据。
            if sig.wants_write and tool not in _WRITE_TOOLS:
                continue
            if tool in _READ_TOOLS and sig.wants_write:
                continue

            if sig.paths:
                for want in sig.paths:
                    for got in actual_paths:
                        if _path_match(want, got):
                            self._covered[i].add(want)
                            self._note(i, f"{tool} 落盘 {got}")
            if sig.command and tool == "shell":
                cmd_text = str(args.get("command") or "")
                if sig.command.lower() in cmd_text.lower():
                    self._cmd_hits[i].add(sig.command)
                    self._note(i, f"shell 执行 `{cmd_text[:80]}`"
                                 + ("（退出码 0）" if result.exit_code in (0, None) else
                                    f"（退出码 {result.exit_code}）"))
            if sig.tool and not sig.paths and not sig.command:
                self._tool_hits[i].add(tool)
                self._note(i, f"{tool} 成功执行")

            if not before and self._is_satisfied(i):
                newly.append(self.conditions[i])

        return newly

    def _paths_of(self, args: Dict[str, Any], result: ToolResult) -> List[str]:
        out: List[str] = []
        for p in (result.files_changed or []):
            if p:
                out.append(str(p))
        for key in ("path",):
            v = args.get(key)
            if isinstance(v, str) and v.strip() and v.strip() != ".":
                out.append(v.strip())
        meta = result.meta or {}
        v = meta.get("path")
        if isinstance(v, str) and v.strip() and v.strip() != ".":
            out.append(v.strip())
        # glob 结果：文件名清单本身也算"找到了这些文件"的佐证
        if result.meta and isinstance(result.meta.get("matches"), list):
            out.extend(str(x) for x in result.meta["matches"])
        return out

    def _note(self, i: int, text: str) -> None:
        if text not in self._evidence[i]:
            self._evidence[i].append(text)

    def _is_satisfied(self, i: int) -> bool:
        """纯函数式判定：结构化累计量是否已经足够。"""
        # 外部显式登记优先 —— 它是人工/评审 UI 的覆盖入口
        if i in self._external:
            return True
        sig = self._signals[i]
        if not sig.has_signal:
            return False
        if sig.paths:
            return all(w in self._covered[i] for w in sig.paths)
        if sig.command:
            return sig.command in self._cmd_hits[i]
        if sig.tool:
            return bool(self._tool_hits[i])
        return False

    # ---------- 手工登记（保留的逃生口） ----------
    def record(self, text: str, satisfied: bool, evidence: str,
               seq: Optional[int] = None) -> None:
        """人工/外部显式登记一条判定结果。

        保留这个入口是为了让上层（未来的评审 UI、测试夹具）能够显式
        覆盖自动判定；自动路径走 `observe()`。

        **无信号的条件只能靠这个入口达成** —— 比如"文案读起来自然"
        这类主观条件，机器无法自动判定，必须有人显式登记。
        """
        self._results.append(GoalCheck(text, satisfied, evidence, seq))
        if not satisfied:
            return
        for i, c in enumerate(self.conditions):
            if c == text:
                self._evidence[i] = [evidence or "（外部登记）"]
                self._external.add(i)

    # ---------- 报告 ----------
    def _status(self, i: int) -> str:
        if self._is_satisfied(i):
            return "satisfied"
        if not self._signals[i].has_signal:
            return "unmatched"
        return "pending"

    def report(self) -> Dict[str, Any]:
        items: List[Dict[str, Any]] = []
        satisfied = pending = unmatched = 0
        for i, c in enumerate(self.conditions):
            st = self._status(i)
            if st == "satisfied":
                satisfied += 1
            elif st == "pending":
                pending += 1
            else:
                unmatched += 1
            items.append({
                "text": c,
                "ok": st == "satisfied",
                "status": st,
                "evidence": self._evidence_text(i) if st == "satisfied" else "",
            })
        # 外部显式登记的（不走信号匹配）也计入
        for r in self._results:
            if r.satisfied and not any(it["text"] == r.text for it in items):
                satisfied += 1
                items.append({"text": r.text, "ok": True, "status": "satisfied",
                              "evidence": r.evidence, "external": True})

        return {
            "total": len(self.conditions),
            "checked": satisfied,
            "satisfied": satisfied,
            "pending": pending,
            "unmatched": unmatched,
            # 「所有条件都挂上了证据」—— 而不是「模型说完成了」
            "all_met": bool(self.conditions) and satisfied >= len(self.conditions),
            "items": items,
            "basis": ("确定性证据绑定：只认工具结果的结构化字段"
                      "（落盘路径 / 退出码 / ok），不读模型文本。"
                      "证据只说明「这个动作发生了」，不构成对内容正确性的判断。"),
        }

    def _evidence_text(self, i: int) -> str:
        notes = self._evidence[i]
        return "；".join(notes[:4]) if notes else "（无）"

    def unmet(self) -> List[str]:
        return [c for i, c in enumerate(self.conditions) if self._status(i) != "satisfied"]

    def unmatched(self) -> List[str]:
        """条件里没有任何可识别信号 —— 需要人工审阅或改写条件。"""
        return [c for i, c in enumerate(self.conditions)
                if self._status(i) == "unmatched"]
