"""约束层：沙箱 × 审批 的正交双轴判定。

## 为什么是「正交双轴」

Codex 的设计把两件事彻底分开（`codex-rs/core/src/exec_policy.rs`、
`protocol/src/models.rs` 的 `PermissionProfile`）：

  · **沙箱模式**决定「能碰什么」（文件系统 / 网络的物理可达范围）
  · **审批策略**决定「什么时候问人」（交互节奏）

两把旋钮独立，因此是 3 × 3 = 9 种组合，而不是一条 3 档的刻度。

Codex 的真实取值：
    沙箱：read-only / workspace-write / danger-full-access
    审批：untrusted   / on-request     / never

## 判定表（本项目）

    action \\ sandbox        read-only   workspace-write   danger-full-access
    read   (工作区内)          run          run                run
    write  (工作区内)          refuse       ★1                 run
    write  (工作区外)          refuse       ★2                 run
    shell  (无副作用)          ★3           run                run
    shell  (有副作用)          ★3           ★1                 run
    network                    refuse       ★2                 run

    ★1 由审批策略决定：untrusted→ask / on-request→run / never→run
    ★2 由审批策略决定：untrusted→ask / on-request→ask / never→refuse
    ★3 由审批策略决定：untrusted→ask / on-request→ask / never→ask

★2、★3 的 `never` 档刻意不放开 —— 书 §1 的「故障安全默认值：所有能力默认关闭，
必须显式开放」。`never` 只表示「不再打扰你」，**不等于无限制**。

## 过程约束（书 §5）

> 修复数据库故障时直接把库删了重建，"修复"确实生效，但数据没了；
> 修复编译错误时把代码全删重写，编译确实通过，但实现没了。
> 因此要对 `rm -rf`、删除生产数据、覆盖未读文件这类危险动作设置专门的检查与审批
> —— **约束的是动作，而非仅仅是结果**。

`check_dangerous()` 就是这条原则的落地：它独立于沙箱判定，
在任何沙箱/审批组合下都会对危险动作升格处理（至少在 `ask` 之上）。
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence


# ══════════════════════════════════════════════════════════════════
# 枚举
# ══════════════════════════════════════════════════════════════════
class SandboxMode(str, Enum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    FULL_ACCESS = "danger-full-access"


class ApprovalPolicy(str, Enum):
    UNTRUSTED = "untrusted"        # 每条有后果的命令都要批准
    ON_REQUEST = "on-request"      # 沙箱内自动放行，越界才问（默认）
    NEVER = "never"                # 全自动，不打断


class Decision(str, Enum):
    RUN = "run"
    ASK = "ask"
    REFUSE = "refuse"


class ActionKind(str, Enum):
    READ = "read"
    WRITE = "write"
    SHELL = "shell"
    NETWORK = "network"


# ══════════════════════════════════════════════════════════════════
# 动作描述
# ══════════════════════════════════════════════════════════════════
@dataclass
class Action:
    """一次被判定动作的结构化描述。

    **全部字段都是结构化的** —— 书 §1 明确要求「安全检查只看结构化数据，
    不看模型自由生成的文本，因为后者可能已被提示注入操纵」。
    因此本类刻意不提供「把命令原文交给判定器」的入口：
    命令文本只用于**危险模式匹配**，它永远不能是「允许执行」的理由。
    """

    kind: ActionKind
    tool: str = ""
    # 受影响路径（已解析为绝对路径）
    paths: List[str] = field(default_factory=list)
    # 副作用标记：写操作、有状态命令
    side_effect: bool = False
    # 是否需要网络
    needs_network: bool = False
    # 原始命令（仅用于危险模式匹配，不作为放行依据）
    command: str = ""
    # 供 UI 展示的动作摘要
    summary: str = ""

    def target_kind(self, workspace: str) -> str:
        """把路径归一到「工作区内 / 工作区外」。

        所有路径都在工作区内才算 inside；只要有一条越界就算 outside。

        **相对路径必须相对 workspace 解析，而不是相对进程 CWD。**
        这里踩过坑：工具层（apply_patch / read_file 等）产出的是工作区内的
        相对路径，若直接用 os.path.realpath(p)，会按进程当前目录解析，
        于是一个本来在工作区内的路径被误判为"越界"，
        导致所有补丁写入都被拒绝。
        """
        if not self.paths:
            return "inside"
        ws = os.path.realpath(workspace)
        for p in self.paths:
            rp = os.path.realpath(p) if os.path.isabs(p) else os.path.realpath(os.path.join(ws, p))
            if rp != ws and not rp.startswith(ws + os.sep):
                return "outside"
        return "inside"


# ══════════════════════════════════════════════════════════════════
# 结果
# ══════════════════════════════════════════════════════════════════
@dataclass
class PolicyResult:
    decision: Decision
    reason: str
    # 升格来源：normal（沙箱×审批）/ danger（危险动作专项）
    escalated_by: str = "normal"
    risk: str = "low"          # 供审批闸门展示
    matched_rule: str = ""     # 命中的危险模式（可审计）

    def to_json(self) -> Dict[str, object]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "escalated_by": self.escalated_by,
            "risk": self.risk,
            "matched_rule": self.matched_rule,
        }


# ══════════════════════════════════════════════════════════════════
# 危险动作模式（过程约束）
# ══════════════════════════════════════════════════════════════════
# 每条 = (正则, 风险等级, 人类可读的原因)
DANGEROUS_PATTERNS: List[tuple] = [
    # 递归/强制删除
    (r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+", "critical", "递归或强制删除（rm -rf / -f）"),
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\b", "critical", "递归删除目录"),
    (r"\bsudo\s+rm\b", "critical", "以超级用户删除文件"),
    # 通配符大范围删除
    (r"\b(rm|del)\b[^\n]*\s\*(\s|$)", "critical", "通配符批量删除"),
    # 权限/属主被改
    (r"\bchmod\s+(-R\s+)?[0-7]*7[0-7]*\b", "high", "放宽文件权限"),
    (r"\bchown\s+-R\b", "high", "递归修改属主"),
    # 磁盘/文件系统
    (r"\b(mkfs|fdisk|parted)\b", "critical", "操作文件系统"),
    (r"\bdd\b[^\n]*\bof=/dev/", "critical", "直接写块设备"),
    # 覆盖式重定向到系统路径
    (r">\s*/(etc|usr|bin|sbin|boot|sys|proc)/", "critical", "重定向覆盖系统路径"),
    # 数据库破坏
    (r"\b(DROP\s+(TABLE|DATABASE|SCHEMA))\b", "critical", "结构性删除数据库对象"),
    (r"\bTRUNCATE\s+TABLE\b", "critical", "清空表数据"),
    (r"\bDELETE\s+FROM\b(?![\s\S]*\bWHERE\b)", "critical", "无 WHERE 的全表删除"),
    # 版本控制破坏
    (r"\bgit\s+push\b[^\n]*(\s--force\b|\s-f\b)", "critical", "强制推送（可覆盖远端历史）"),
    (r"\bgit\s+reset\s+--hard\b", "high", "硬重置（丢弃未提交改动）"),
    (r"\bgit\s+clean\s+-[a-zA-Z]*[fdx][a-zA-Z]*\b", "high", "清理未跟踪文件"),
    (r"\bgit\s+(checkout|restore)\s+--\s+\S", "high", "检出覆盖本地修改"),
    # 凭据/密钥
    (r"\b(cat|type|head|tail|less|more)\b[^\n]*(\.env|\.pem|id_rsa|\.aws/credentials|\.ssh/)",
     "critical", "读取凭据文件"),
    (r"curl[^\n]*(-d|--data)[^\n]*(TOKEN|KEY|SECRET|PASSWORD)", "critical", "可能外传凭据"),
    # 供应链：安装/执行远程代码
    (r"\b(curl|wget)\b[^\n]*\|\s*(ba)?sh\b", "critical", "管道执行远程脚本"),
    (r"\b(pip|npm)\s+(install|-i)\b[^\n]*--(force|ignore-scripts)", "high", "绕过依赖安全检查"),
    # 进程/服务
    (r"\b(kill|pkill|killall)\b[^\n]*\s(-9\s+)?1(\s|$)", "critical", "杀死 init 进程"),
    (r"\bsystemctl\s+(stop|disable)\b", "high", "停止系统服务"),
    # 系统关机
    (r"\b(shutdown|reboot|halt|poweroff)\b", "critical", "关机或重启"),
]

_COMPILED_DANGER = [(re.compile(p, re.IGNORECASE), lvl, why) for p, lvl, why in DANGEROUS_PATTERNS]


# 网络访问迹象（用于推断 needs_network）
NETWORK_PATTERNS = [
    re.compile(r"\b(curl|wget|nc|ncat|telnet|ssh|scp|rsync)\b", re.IGNORECASE),
    re.compile(r"\b(pip|npm|yarn|pnpm|apt|brew|go|cargo)\s+(install|add|get|update)\b", re.IGNORECASE),
    re.compile(r"\bgit\s+(clone|fetch|pull|push)\b", re.IGNORECASE),
    re.compile(r"\bhttps?://", re.IGNORECASE),
]


# ══════════════════════════════════════════════════════════════════
# 禁改清单的 glob 匹配
# ══════════════════════════════════════════════════════════════════
# 刻意不用 fnmatch 的朴素写法。原因是踩过坑：
# `fnmatch("a.py", ".git")` 会返回 True（fnmatch 是纯字符串匹配，
# 不认路径语义），若再退化成 basename 比较，会让**任何文件**都命中 `.git` 这类模式，
# 于是整条禁改清单变成"拒绝一切写入"。
# 这里的实现按路径段匹配：`**/.git/**` 要求路径里**确实存在一个名为 .git 的段**。
def _glob_hits(pattern: str, rel: str) -> bool:
    """判断相对路径 rel 是否命中禁改模式 pattern（路径段语义）。"""
    pat = pattern.replace("\\", "/").strip("/")
    path = rel.replace("\\", "/").strip("/")
    if not pat:
        return False
    pseg = [s for s in pat.split("/") if s not in ("", ".")]
    sseg = [s for s in path.split("/") if s not in ("", ".")]

    return _match_segs(pseg, sseg)


def _match_segs(pseg: List[str], sseg: List[str]) -> bool:
    """路径段的递归匹配，支持 `**` 跨任意层数。"""
    if not pseg:
        return not sseg
    head = pseg[0]

    if head == "**":
        # `**` 可匹配 0 层或多层
        if _match_segs(pseg[1:], sseg):
            return True
        for i in range(len(sseg)):
            if _match_segs(pseg[1:], sseg[i + 1:]):
                return True
        return False

    if not sseg:
        return False
    if head == "*":
        return _match_segs(pseg[1:], sseg[1:])
    # 普通段（可能带 * 通配，如 .env.*）
    if fnmatch.fnmatch(sseg[0], head):
        return _match_segs(pseg[1:], sseg[1:])
    return False


# ══════════════════════════════════════════════════════════════════
# 判定引擎
# ══════════════════════════════════════════════════════════════════
class Policy:
    """沙箱 × 审批 双轴判定。

    对应 Codex 的 `ExecPolicy::prompt_is_rejected_by_policy()` 与
    `assess_patch_safety()`。
    """

    def __init__(self, sandbox: SandboxMode, approval: ApprovalPolicy,
                 workspace: str,
                 extra_deny_globs: Optional[Sequence[str]] = None) -> None:
        self.sandbox = sandbox
        self.approval = approval
        self.workspace = os.path.realpath(workspace)
        # 额外禁改路径。书 §3 的 AGENTS.md 实践里常见「不要改动 migrations/ 目录」
        # 这类显式禁区 —— 那是「指导」，这里把它变成「做不了」。
        # 书 §5 原则 1：约束优先于指导 —— 能用代码强制的就不要用文档建议。
        self.deny_globs: List[str] = list(extra_deny_globs or [
            "**/.git/**", "**/node_modules/**", "**/.env", "**/.env.*",
            "**/*.pem", "**/id_rsa", "**/.ssh/**",
        ])
        self._decisions: List[Dict[str, object]] = []

    # ---------- 主入口 ----------
    def decide(self, action: Action) -> PolicyResult:
        """判定一个动作。"""
        # ── 第一关：危险模式（过程约束，独立于沙箱）──
        danger = self.check_dangerous(action)
        if danger is not None:
            self._record(action, danger)
            return danger

        # ── 第二关：禁改路径 ──
        deny = self._check_deny_globs(action)
        if deny is not None:
            self._record(action, deny)
            return deny

        # ── 第三关：沙箱 × 审批 ──
        res = self._by_sandbox(action)
        self._record(action, res)
        return res

    # ---------- 危险模式 ----------
    def check_dangerous(self, action: Action) -> Optional[PolicyResult]:
        """危险动作专项检查。

        书 §5：约束的是**动作**，而非仅仅是结果。
        因此这里不关心动作是否"最终有效"，只关心它是否属于破坏性捷径。

        升格规则：命中的，**至少 ask**；critical 级在 `never` 档下也要 ask。
        （从不静默放行 —— 这正是「故障安全默认值」的含义。）
        """
        if action.kind not in (ActionKind.SHELL, ActionKind.WRITE, ActionKind.NETWORK):
            return None
        text = action.command or action.summary
        if not text:
            return None

        for rx, level, why in _COMPILED_DANGER:
            if rx.search(text):
                return PolicyResult(
                    decision=Decision.ASK,
                    reason=f"危险动作：{why}。即使当前策略允许自动执行，也需要人工确认。",
                    escalated_by="danger",
                    risk=level,
                    matched_rule=why,
                )
        return None

    # ---------- 禁改路径 ----------
    def _check_deny_globs(self, action: Action) -> Optional[PolicyResult]:
        if action.kind == ActionKind.READ or not action.paths:
            return None
        for p in action.paths:
            rel = self._rel(p)
            for g in self.deny_globs:
                if _glob_hits(g, rel):
                    return PolicyResult(
                        decision=Decision.REFUSE,
                        reason=f"路径 {rel} 命中禁改清单（{g}）。这是硬约束，不是建议。",
                        escalated_by="deny_glob",
                        risk="high",
                        matched_rule=g,
                    )
        return None

    # ---------- 沙箱 × 审批 判定表 ----------
    def _by_sandbox(self, action: Action) -> PolicyResult:
        sbx, ap = self.sandbox, self.approval
        inside = action.target_kind(self.workspace) == "inside"

        # danger-full-access：无沙箱即无门控（但危险模式已在第一关拦过）
        if sbx is SandboxMode.FULL_ACCESS:
            return PolicyResult(Decision.RUN, "完全访问模式：不施加沙箱限制。", risk="high")

        # ── read-only ──
        if sbx is SandboxMode.READ_ONLY:
            if action.kind == ActionKind.READ:
                return PolicyResult(Decision.RUN, "只读模式：读取工作区内容。", risk="low")
            if action.kind == ActionKind.WRITE:
                return PolicyResult(Decision.REFUSE, "只读模式下不允许写入。", risk="medium")
            if action.kind == ActionKind.NETWORK:
                return PolicyResult(Decision.REFUSE, "只读模式下不允许网络访问。", risk="medium")
            # shell
            if not action.side_effect:
                # ★3：untrusted / on-request / never 一律 ask
                return PolicyResult(
                    Decision.ASK,
                    "只读模式下运行命令属于越界动作，需要确认。" if ap is not ApprovalPolicy.NEVER
                    else "只读模式下即使策略为 never，运行命令仍需确认（沙箱优先）。",
                    risk="medium",
                )
            return PolicyResult(Decision.REFUSE, "只读模式下不允许有副作用的命令。", risk="high")

        # ── workspace-write ──
        if sbx is SandboxMode.WORKSPACE_WRITE:
            if action.kind == ActionKind.READ:
                return PolicyResult(Decision.RUN, "工作区写入模式：读取工作区内容。", risk="low")

            if action.kind == ActionKind.WRITE:
                if inside:
                    # ★1
                    if ap is ApprovalPolicy.UNTRUSTED:
                        return PolicyResult(Decision.ASK, "untrusted 策略：每次写入都需要确认。", risk="low")
                    return PolicyResult(Decision.RUN, "工作区内写入，沙箱内自动放行。", risk="low")
                # ★2 越界
                if ap is ApprovalPolicy.NEVER:
                    return PolicyResult(Decision.REFUSE, "越出工作区的写入在 never 策略下被拒绝"
                                        "（never 表示不再打扰，不等于无限制）。", risk="high")
                return PolicyResult(Decision.ASK, "写入工作区以外的路径，需要确认。", risk="high")

            if action.kind == ActionKind.NETWORK:
                # ★2
                if ap is ApprovalPolicy.NEVER:
                    return PolicyResult(Decision.REFUSE, "网络访问在 never 策略下被拒绝。", risk="medium")
                return PolicyResult(Decision.ASK, "网络访问超出沙箱范围，需要确认。", risk="medium")

            # shell
            if not action.side_effect:
                return PolicyResult(Decision.RUN, "工作区内无副作用的命令，沙箱内自动放行。", risk="low")
            # ★1：有副作用的命令
            if ap is ApprovalPolicy.UNTRUSTED:
                return PolicyResult(Decision.ASK, "untrusted 策略：有副作用的命令需要确认。", risk="medium")
            if not inside:
                if ap is ApprovalPolicy.NEVER:
                    return PolicyResult(Decision.REFUSE, "越界命令在 never 策略下被拒绝。", risk="high")
                return PolicyResult(Decision.ASK, "命令可能影响工作区以外的路径，需要确认。", risk="high")
            return PolicyResult(Decision.RUN, "工作区内命令，沙箱内自动放行。", risk="low")

        # 未知组合：故障安全，拒绝
        return PolicyResult(Decision.REFUSE, f"未识别的沙箱模式 {sbx}，按故障安全原则拒绝。", risk="high")

    # ---------- 审计 ----------
    def _record(self, action: Action, res: PolicyResult) -> None:
        self._decisions.append({
            "tool": action.tool,
            "kind": action.kind.value,
            "summary": action.summary or action.command,
            "decision": res.decision.value,
            "reason": res.reason,
            "escalated_by": res.escalated_by,
            "risk": res.risk,
        })

    def audit_log(self) -> List[Dict[str, object]]:
        """判定审计。每一次判定都留痕 —— 可审计是 Harness 的基本要求。"""
        return list(self._decisions)

    def _rel(self, path: str) -> str:
        rp = os.path.realpath(path)
        if rp == self.workspace:
            return "."
        if rp.startswith(self.workspace + os.sep):
            return os.path.relpath(rp, self.workspace).replace(os.sep, "/")
        return rp.replace(os.sep, "/")


# ══════════════════════════════════════════════════════════════════
# 便捷工厂
# ══════════════════════════════════════════════════════════════════
def infer_network_needed(command: str) -> bool:
    return any(rx.search(command) for rx in NETWORK_PATTERNS)


def infer_side_effect(command: str) -> bool:
    """推断命令是否有副作用。

    保守取向：**只有明确属于只读的命令才判为无副作用**。
    书 §1 的「故障安全默认值」要求如此 —— 宁可多问一次，也不静默放行。
    """
    c = command.strip()
    if not c:
        return True
    # 管道/重定向/后台/串联，一律视为有副作用（复合命令里任一段可能有副作用）
    if re.search(r"[|&;<>]|\$\(|`", c):
        return True
    readonly_cmds = {
        "ls", "pwd", "cat", "head", "tail", "wc", "file", "stat", "du", "df",
        "echo", "printf", "date", "whoami", "id", "env", "printenv", "which",
        "type", "command", "man", "help", "less", "more", "find", "tree",
        "grep", "rg", "ag", "sed", "awk", "sort", "uniq", "cut", "tr", "diff",
        "cmp", "md5sum", "sha256sum", "basename", "dirname", "realpath",
        "true", "false", "test", "expr", "seq", "jq", "yq",
    }
    first = c.split()[0].split("/")[-1]
    if first in readonly_cmds:
        # git 单独处理：status/log/diff/show 是只读，其余不是
        return False
    if first == "git":
        sub = c.split()[1] if len(c.split()) > 1 else ""
        return sub not in {"status", "log", "diff", "show", "branch", "remote", "describe", "rev-parse", "ls-files", "blame"}
    if first in {"python", "python3", "node", "ruby", "perl"}:
        # 解释器一律视为有副作用（可能执行任意代码）
        return True
    return True
