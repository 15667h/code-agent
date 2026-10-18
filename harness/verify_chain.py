"""离线核验事件链 —— 不需要信任产生它的进程，也不需要密钥。

## 用法

    python verify_chain.py <run目录>
    python verify_chain.py --workspace <工作区>       # 批量核验全部运行
    python verify_chain.py --json <run目录>           # 机器可读输出

## 退出码

    0  全部核验通过（或"无链可核"）
    1  发现篡改 / 链被破坏
    2  用法错误或路径不存在

## 为什么单独给一个脚本

哈希链的价值全在"**外部可核验**"：审计者、CI job、甚至别的语言实现的
核验器都应该能独立完成这件事。藏在一个必须起服务才能调的 HTTP 端点后面，
等于把"外部可核验"降级成"你得先信任我的运行时"。

这个脚本只依赖 Python 标准库，且**只读** —— 它不改动任何文件。
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def _load_chain_module():
    """把 harness.chain 载进来。

    两种场景都要支持：
      · 在项目仓库里跑（能找到 harness 包）
      · 只把 chain.py 拷出去单独用（脱离仓库）
    后者对本功能的意义更大 —— 审计者往往只拿到一份记录，没有整个项目。
    所以这里先试包导入，失败则按路径直接加载同目录的 chain.py。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    try:
        from harness.chain import verify_chain, verify_all, EVENTS_FILE
        return verify_chain, verify_all, EVENTS_FILE
    except ImportError:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "crucible_chain", os.path.join(here, "chain.py"))
        if spec is None or spec.loader is None:
            raise
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.verify_chain, mod.verify_all, mod.EVENTS_FILE


def _render_one(r: dict, verbose: bool) -> str:
    lines = []
    status = "通过" if r["ok"] else "**未通过**"
    if r.get("unchained"):
        status = "无链可核（不判定为篡改）"
    lines.append(f"运行目录：{r['run_dir']}")
    lines.append(f"结论：{status}    已核验 {r['checked']} 条")
    if r.get("head") and r["head"] != "0" * 64:
        lines.append(f"链头指纹：{r['head'][:32]}…")
    if r.get("stored_head"):
        same = "一致" if r["stored_head"] == r["head"] else "**不一致**"
        lines.append(f"落盘锚点：{same}")
    if r.get("note"):
        lines.append(f"说明：{r['note']}")
    if r["problems"]:
        lines.append(f"发现问题 {len(r['problems'])} 条：")
        for i, p in enumerate(r["problems"], start=1):
            if not verbose and i > 8:
                lines.append(f"  … 另有 {len(r['problems']) - 8} 条（加 --verbose 全看）")
                break
            where = f"（第 {p['line']} 行）" if p.get("line") else ""
            lines.append(f"  {i}. [{p['kind']}]{where} {p.get('detail', '')}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="离线核验 Crucible 运行的事件哈希链（只读，不改动任何文件）")
    ap.add_argument("run_dir", nargs="?", help="单个运行目录")
    ap.add_argument("--workspace", help="批量核验该工作区下的全部运行")
    ap.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    ap.add_argument("--verbose", action="store_true", help="列出全部问题")
    args = ap.parse_args(argv)

    try:
        verify_chain, verify_all, EVENTS_FILE = _load_chain_module()
    except ImportError as e:
        print(f"无法加载核验模块：{e}", file=sys.stderr)
        return 2

    if args.workspace:
        runs_root = os.path.join(os.path.realpath(args.workspace),
                                 ".crucible", "runs")
        if not os.path.isdir(runs_root):
            print(f"找不到运行目录：{runs_root}", file=sys.stderr)
            return 2
        result = verify_all(runs_root)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"共核验 {result['runs']} 个运行，"
                  f"通过 {result['ok']} 个，未通过 {len(result['failed'])} 个。")
            for f in result["failed"]:
                print(f"  ✗ {f['run']}：{len(f['problems'])} 个问题")
            if result.get("without_anchor"):
                print(f"  注意：{len(result['without_anchor'])} 个运行没有链头锚点，"
                      "只做了链内自洽检查。")
        return 0 if not result["failed"] else 1

    if not args.run_dir:
        ap.print_help()
        return 2

    if not os.path.isdir(args.run_dir):
        print(f"目录不存在：{args.run_dir}", file=sys.stderr)
        return 2
    if not os.path.isfile(os.path.join(args.run_dir, EVENTS_FILE)):
        print(f"该目录里没有 {EVENTS_FILE}", file=sys.stderr)
        return 2

    r = verify_chain(args.run_dir)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(_render_one(r, args.verbose))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
