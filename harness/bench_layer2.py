"""基准第 2 层：真实仓库上的行为观察。

## 这一层测什么、不测什么

**不测**：正确性 / Pass@1。
算它要跑官方 `fail_to_pass` 测试套件，需要每题一个 Docker 镜像
（`docker_image` 字段指向 `ghcr.io/scaleapi/...`）。本机到 ghcr.io 不通，
而且 SWE-bench Pro 的题平均 170 行改动、5.1 个文件，一次完整求解
在本地要几十分钟。报一个"看起来像通过率"的数字比不报更糟。

**测**：在**真实仓库的真实代码**上，harness 的行为是否可信。具体四项：

| 指标 | 为什么重要 |
|---|---|
| `found_target` | agent 有没有读到你点名的文件（不是瞎改） |
| `touched_named` | 写操作有没有落在**该改的文件**上 |
| `ran_verification` | 有没有跑过验证（而不是"我看了一下就改了"） |
| `gate_behavior` | 闸门放行/拦截是否符合预期 |

## 为什么这一层是"弱证据"

它依赖模型能力，同一套 harness 换模型结果就变。
所以结论只用于**观察**，不用于宣称"我们比 X 强"。
第 1 层才是强证据 —— 那一层不依赖模型。

## 用法

    python harness/bench_layer2.py --n 2        # 跑 2 道真实题
    python harness/bench_layer2.py --n 1 --json
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.intent import is_probable_file  # noqa: E402

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         ".workbuddy", "bench")
REPOS_DIR = os.path.join(CACHE_DIR, "repos")

# ## 为什么脚本**不删** checkout 出来的仓库
#
# 实测踩到：脚本原本在每次跑完后 `shutil.rmtree(ws)` 清理，结果被环境的
# **安全删除护栏**拦下 —— 一个 NodeBB 仓库有 4679 个文件，超过护栏阈值，
# 需要交互确认，脚本因此直接失败退出（13 分钟的运行为此白跑）。
#
# 那条护栏是对的：它保护的是用户数据，脚本不该绕过它。
# 所以改成本地策略：
#   · **复用**已 checkout 的仓库（同题再跑就不必重新 fetch，省几分钟）
#   · `prepare_repo` 里用 `git reset --hard <sha>` + `git clean -fd`
#     把工作区恢复到 base_commit —— 这是 git 自己的清理，不碰用户目录
#   · 真要删整个缓存，由用户显式跑 `--clean`（这时他预期就是删）
#
# 这也顺带修了一个真实缺陷：原实现每次跑都重新 fetch，
# 而 benchmark 常要反复跑同一批题对比 —— 那是几分钟 × N 的浪费。


# ══════════════════════════════════════════════════════════════════
# 仓库准备
# ══════════════════════════════════════════════════════════════════
def _run(cmd: List[str], cwd: Optional[str] = None, timeout: int = 300):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)


def prepare_repo(task: Dict[str, Any], workspace: str) -> Dict[str, Any]:
    """把题目对应的仓库 checkout 到 `base_commit`。

    ## 为什么可以这样做（网络现实）

    本机到 `github.com` 直连不通，但 `codeload.github.com` /
    `git fetch` 的 smart-http 路径是通的（实测 clone NodeBB 成功）。
    所以走 `git init` + `fetch <sha>` + `checkout`，而不是整仓 clone ——
    后者对 ansible 那种仓库要几分钟，前者只取需要的那个 commit。

    ## 失败时就如实失败

    网络不稳是常态。拿不到代码就返回 `ok=False` 并说明原因，
    绝不拿一个空目录冒充"仓库已就绪" —— 那会让后续所有指标失真。
    """
    repo = task.get("repo") or ""
    sha = task.get("base_commit") or ""
    os.makedirs(workspace, exist_ok=True)
    if not repo or not sha:
        return {"ok": False, "reason": "题目缺 repo 或 base_commit"}

    git_dir = os.path.join(workspace, ".git")
    if not os.path.isdir(git_dir):
        r = _run(["git", "init", "-q"], cwd=workspace)
        if r.returncode != 0:
            return {"ok": False, "reason": f"git init 失败：{r.stderr[:200]}"}
        _run(["git", "remote", "add", "origin", f"https://github.com/{repo}.git"],
             cwd=workspace)

    # 只取目标 commit（浅取，省时间与带宽）
    r = _run(["git", "fetch", "--depth", "1", "origin", sha],
             cwd=workspace, timeout=420)
    if r.returncode != 0:
        return {"ok": False,
                "reason": f"fetch {sha[:8]} 失败：{(r.stderr or r.stdout)[:300]}"}
    r = _run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=workspace, timeout=180)
    if r.returncode != 0:
        return {"ok": False, "reason": f"checkout 失败：{r.stderr[:300]}"}

    # 若这次是复用已有 checkout，工作区可能被上一轮 agent 改脏。
    # 用 git 自己的复位（不碰用户目录、不触发安全删除护栏）。
    _run(["git", "reset", "--hard", "-q", sha], cwd=workspace, timeout=180)
    _run(["git", "clean", "-fdq"], cwd=workspace, timeout=180)
    n_files = sum(len(fs) for _, _, fs in os.walk(workspace)
                  if ".git" not in _)
    return {"ok": True, "files": n_files, "sha": sha[:8]}


# ══════════════════════════════════════════════════════════════════
# 运行一次真实 agent
# ══════════════════════════════════════════════════════════════════
@dataclass
class RunMetrics:
    task_id: str
    repo: str
    status: str = ""
    iterations: int = 0
    tool_calls: int = 0
    found_target: bool = False
    touched_named: bool = False
    ran_verification: bool = False
    gate_blocks: int = 0
    cert_ok: Optional[bool] = None
    cert_codes: List[str] = field(default_factory=list)
    read_files: List[str] = field(default_factory=list)
    written_files: List[str] = field(default_factory=list)
    duration_s: float = 0.0
    note: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id, "repo": self.repo, "status": self.status,
            "iterations": self.iterations, "tool_calls": self.tool_calls,
            "found_target": self.found_target,
            "touched_named": self.touched_named,
            "ran_verification": self.ran_verification,
            "gate_blocks": self.gate_blocks,
            "cert_ok": self.cert_ok, "cert_codes": self.cert_codes,
            "read_files": self.read_files[:10],
            "written_files": self.written_files[:10],
            "duration_s": round(self.duration_s, 1),
            "note": self.note,
        }


def build_task_prompt(task: Dict[str, Any], named: List[str]) -> str:
    """把真实题目转成给 agent 的任务文本。

    给的是 `problem_statement` + `requirements`（都是官方字段），
    **不给 patch**（那是答案），也不给 `test_patch`（那泄露验收方式）。
    """
    ps = (task.get("problem_statement") or "").strip()
    req = (task.get("requirements") or "").strip()
    parts = []
    if ps:
        parts.append(ps[:1500])
    if req:
        parts.append("## 验收要求\n" + req[:1500])
    if named:
        parts.append("## 相关文件\n" + "、".join(named[:4]))
    parts.append("请在这个仓库里定位问题并修复。改完请运行能验证你改动的检查。")
    return "\n\n".join(parts)


def run_one(task: Dict[str, Any], *, model, sandbox_mode, max_iter: int = 20,
            timeout_s: int = 600) -> RunMetrics:
    """在一道真实题上跑一次 harness，记录行为指标。"""
    from harness.context import StaticPrefix
    from harness.loop import AgentLoop, LoopConfig, build_default_registry
    from harness.policy import ApprovalPolicy, Policy
    from harness.protocol import EventType
    from harness.session import Session
    from harness.server import DEFAULT_SYSTEM_PROMPT
    from harness.bench_layer1 import expected_touched_files, files_in_requirements

    tid = task.get("instance_id") or "?"
    named = [f for f in expected_touched_files(task) if is_probable_file(f)]
    m = RunMetrics(task_id=tid, repo=task.get("repo") or "")

    ws = os.path.join(REPOS_DIR, tid[:60].replace("/", "_").replace(":", "_"))
    # 不再 rmtree（见文件开头说明）：复用已有 checkout，靠 git 复位到 base_commit
    prep = prepare_repo(task, ws)
    if not prep.get("ok"):
        m.note = f"仓库准备失败：{prep.get('reason')}"
        return m

    reg, shell = build_default_registry(ws)
    prefix = StaticPrefix(DEFAULT_SYSTEM_PROMPT, reg.specs())
    sess = Session(ws, getattr(model, "name", "m") or "m", "openai-compatible",
                   sandbox_mode.value, "on-request")
    pol = Policy(sandbox_mode, ApprovalPolicy.ON_REQUEST, ws)
    loop = AgentLoop(model, reg, prefix, pol, sess,
                     LoopConfig(max_iterations=max_iter),
                     approval_handler=lambda r: "approved")
    prompt = build_task_prompt(task, named)

    box: Dict[str, Any] = {}
    def go():
        try:
            box["r"] = loop.run(prompt)
        except Exception as e:      # noqa: BLE001
            box["e"] = f"{type(e).__name__}: {e}"
    t0 = time.time()
    th = threading.Thread(target=go)
    th.start()
    th.join(timeout=timeout_s)
    m.duration_s = time.time() - t0
    if th.is_alive():
        m.note = f"超时（>{timeout_s}s）"
    if "e" in box:
        m.note = (m.note + " | " if m.note else "") + f"异常：{box['e']}"

    r = box.get("r")
    if r is not None:
        m.status = r.status
    m.iterations = loop._iterations
    m.tool_calls = loop._tool_calls

    # ── 行为指标：全部来自结构化事实 ──
    reads, writes = [], []
    for e in loop.facts.executions:
        if not e.get("ok"):
            continue
        tool = e.get("tool")
        if tool == "read_file":
            p = str((e.get("args") or {}).get("path") or "")
            if p:
                reads.append(p)
        for p in (e.get("files_changed") or []):
            if p not in writes:
                writes.append(p)
    m.read_files, m.written_files = reads, writes

    from harness.intent import path_covers
    m.found_target = any(path_covers(n, p) for n in named for p in reads)
    m.touched_named = any(path_covers(n, p) for n in named for p in writes)

    from harness.intent import is_verify_command
    m.ran_verification = any(
        e.get("tool") == "shell" and e.get("ok")
        and is_verify_command(str((e.get("args") or {}).get("command") or ""))
        for e in loop.facts.executions)

    m.gate_blocks = sum(
        1 for e in loop.bus.events()
        if e.type == EventType.ERROR
        and e.data.get("cls") in ("preflight_incomplete",
                                  "certificate_rejected",
                                  "goal_unmet"))
    cert = getattr(loop, "_certificate", None)
    if cert is not None:
        m.cert_ok = cert.ok
        m.cert_codes = cert.reason_codes()

    shell.reset()
    # **保留**仓库（见文件开头说明）：下次跑同一题直接复用，省一次 fetch。
    # 工作区脏了没关系 —— 下次 prepare_repo 会用 git reset --hard 复位。
    return m


def run_layer2(tasks: List[Dict[str, Any]], *, n: int = 2,
               timeout_s: int = 600) -> Dict[str, Any]:
    from harness.model import HttpModel, resolve_endpoint
    from harness.policy import SandboxMode

    ep = resolve_endpoint(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if not ep.configured:
        return {"layer": 2, "ok": False,
                "error": "模型端点未配置，无法跑第 2 层（行为层需要真实模型）",
                "how_to": "在 .crucible/config.json 配置 base_url / api_key / model"}

    model = HttpModel(ep)
    results: List[RunMetrics] = []
    for t in tasks[:n]:
        results.append(run_one(t, model=model,
                               sandbox_mode=SandboxMode.WORKSPACE_WRITE,
                               timeout_s=timeout_s))

    done = [m for m in results if m.status]
    return {
        "layer": 2,
        "name": "真实仓库行为观察",
        "model": ep.model,
        "attempted": len(results),
        "completed": len(done),
        "results": [m.to_json() for m in results],
        "evidence": "弱（依赖模型能力，同一 harness 换模型结果会变）",
        "not_measured": "正确性 / Pass@1 —— 需官方 Docker 测试套件",
        "indicators": {
            "found_target": f"{sum(1 for m in done if m.found_target)}/{len(done)}",
            "touched_named": f"{sum(1 for m in done if m.touched_named)}/{len(done)}",
            "ran_verification": f"{sum(1 for m in done if m.ran_verification)}/{len(done)}",
            "gate_blocked": f"{sum(1 for m in done if m.gate_blocks)}/{len(done)}",
        },
    }


def render(res: Dict[str, Any]) -> str:
    if res.get("error"):
        return f"第 2 层无法运行：{res['error']}\n  {res.get('how_to', '')}"
    lines = [f"第 {res['layer']} 层 · {res['name']}（模型 {res['model']}）",
             f"  尝试 {res['attempted']} 道 · 完成 {res['completed']} 道",
             f"  证据强度：{res['evidence']}",
             f"  **未测量**：{res['not_measured']}", ""]
    lines.append("  指标汇总（分母是完成的题数）：")
    for k, v in res["indicators"].items():
        lines.append(f"    {k:18} {v}")
    lines.append("")
    for r in res["results"]:
        lines.append(f"  ── {r['repo']} · {r['task_id'][:46]}")
        lines.append(f"     状态 {r['status'] or '未完成'} · 轮次 {r['iterations']} · "
                     f"工具 {r['tool_calls']} · {r['duration_s']}s")
        lines.append(f"     读到目标文件 {r['found_target']} · "
                     f"写入目标文件 {r['touched_named']} · "
                     f"跑过验证 {r['ran_verification']} · "
                     f"闸门拦截 {r['gate_blocks']}")
        if r["read_files"]:
            lines.append(f"     读过：{', '.join(r['read_files'][:4])}")
        if r["written_files"]:
            lines.append(f"     写过：{', '.join(r['written_files'][:4])}")
        if r["cert_ok"] is not None:
            lines.append(f"     证书：{'签发' if r['cert_ok'] else '拒签 ' + str(r['cert_codes'])}")
        if r["note"]:
            lines.append(f"     备注：{r['note']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    from harness.bench_swe_pro import load_tasks
    ap = argparse.ArgumentParser(description="SWE-bench Pro 第 2 层：真实仓库行为观察")
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--config", default="hard")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--clean", action="store_true",
                    help="删除整个仓库缓存目录（显式确认才做；脚本平时不删）")
    args = ap.parse_args(argv)

    if args.clean:
        if not os.path.isdir(REPOS_DIR):
            print("缓存目录不存在，无需清理")
            return 0
        n = sum(len(fs) for _, _, fs in os.walk(REPOS_DIR))
        print(f"将要删除 {REPOS_DIR}（约 {n} 个文件）")
        print("这是**显式**清理请求 —— 只有你主动加 --clean 时才会走到这里。")
        shutil.rmtree(REPOS_DIR, ignore_errors=True)
        print("已清理")
        return 0

    tasks = load_tasks(args.config)
    res = run_layer2(tasks, n=args.n, timeout_s=args.timeout)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(render(res))
    return 0 if res.get("completed") else 1


if __name__ == "__main__":
    sys.exit(main())
