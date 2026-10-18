"""离线核验完成证书 —— 只读、零依赖。

用途与 `verify_chain.py` 一致：**审计者只有一份 run 目录，不该需要信任
产生它的进程**。拿到 `certificate.json` + `events.jsonl` 就能自己重放一遍。

## 核验的是什么

    python harness/verify_certificate.py <run目录>

1. **证书自身指纹**：`digest` 是否与按内容重算的一致（证书没被改过）
2. **证据引用**：每条声明引用的 seq 是否真的存在于 `events.jsonl`
3. **执行状态**：那条事件的 `ok` 是否为 true（失败的操作不是证据）
4. **产物一致**：事件里记的 `files_changed` 与证书写的是否一致
5. **范围**：证据产物是否落在工作区内

## 它**不**核验什么

不核验产物在语义上是否正确（`add` 是否真的返回 `a+b`）、
程序是否真的能跑、任务意图是否被满足。
这与论文的范围声明一致：ECT 认证的是"在声明的假设下、记录轨迹中的支持"，
不是外部真理性。报告里会把这句话带出来，避免被当成正确性证明。

退出码：0 = 通过；1 = 未通过；2 = 用法/文件问题。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# 脚本直跑时项目根必须先进 sys.path，否则 `from harness...` 找不到包。
# 这是本项目第三次踩这个坑（evaluate.py / server.py / 这里），
# 记在 CLAUDE 里那条纪律：**入口脚本先把根塞进 sys.path，再用绝对导入**。
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.certificate import Certificate, verify_certificate  # noqa: E402


def _load(run_dir: str):
    cert_path = os.path.join(run_dir, "certificate.json")
    ev_path = os.path.join(run_dir, "events.jsonl")
    if not os.path.isfile(cert_path):
        return None, None, f"找不到 {cert_path}"
    if not os.path.isfile(ev_path):
        return None, None, f"找不到 {ev_path}"
    try:
        with open(cert_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return None, None, f"读 certificate.json 失败：{e}"

    raw = payload.get("certificate") or {}
    cert = Certificate(
        task_digest=raw.get("task_digest", ""),
        ledger_digest=raw.get("ledger_digest", ""),
        status=raw.get("status", ""),
        digest=raw.get("digest", ""),
    )
    # 从 JSON 还原声明与证据。**不从 `ok` 字段还原判定** ——
    # 那个字段只用于展示，核验会重新算。
    from harness.certificate import Claim, Evidence
    for c in raw.get("claims") or []:
        cert.claims.append(Claim(
            kind=c.get("kind", ""), subject=c.get("subject", ""),
            evidence=[Evidence(seq=int(e.get("seq") or 0),
                               tool=e.get("tool", ""),
                               ok=bool(e.get("ok")),
                               exit_code=e.get("exit_code"),
                               files_changed=list(e.get("files_changed") or []))
                      for e in (c.get("evidence") or [])]))

    events = []
    with open(ev_path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                events.append(json.loads(ln))
            except json.JSONDecodeError:
                continue          # 坏行跳过：一次崩溃的运行恰恰最该被核验
    return cert, events, None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="离线核验完成证书（只读、不需要信任原进程）")
    ap.add_argument("run_dir", help="运行目录（含 certificate.json 与 events.jsonl）")
    ap.add_argument("--workspace",
                    help="工作区根目录，用于范围检查（默认取运行目录所在工作区）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.run_dir):
        print(f"目录不存在：{args.run_dir}", file=sys.stderr)
        return 2

    cert, events, err = _load(args.run_dir)
    if err:
        print(err, file=sys.stderr)
        return 2

    # 工作区：run 目录通常位于 <ws>/.crucible/runs/<id>
    ws = args.workspace
    if ws is None:
        probe = os.path.realpath(args.run_dir)
        for _ in range(3):
            probe = os.path.dirname(probe)
        ws = probe if os.path.isdir(probe) else ""

    result = verify_certificate(cert, events, ws)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        mark = "✓ 通过" if result["ok"] else "✗ 未通过"
        print(f"完成证书核验：{mark}")
        print(f"  检查声明数：{result['checked']}")
        print(f"  证书指纹：{'一致' if result['cert_digest_ok'] else '**不一致（证书被改过）**'}")
        if result["build_problems"]:
            print("  签发时即存在的问题：")
            for p in result["build_problems"]:
                print(f"    · [{p['code']}] {p['detail']}")
        if result["replay_problems"]:
            print("  重放发现的问题：")
            for p in result["replay_problems"]:
                print(f"    · [{p['code']}] {p['detail']}")
        if result["ok"]:
            print("  证书里的每条声明都能在事件流里找到成功的证据。")
        print()
        print(f"  范围：{result['scope']}")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
