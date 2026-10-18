"""SWE-bench Pro 抽样基准。

## 这个脚本能做什么、不能做什么（先说清楚，避免被误读成"跑分")

**能**：
  · 从 HuggingFace 官方数据集服务取**真实题目**（problem_statement / requirements /
    patch / fail_to_pass / base_commit），来源是 `ScaleAI/SWE-bench_Pro`，
    公开集 731 题（这里用 `hard` 子集 51 题）
  · fetch 到真实仓库的 base_commit，拿到**真实代码**
  · 在这些真实代码上跑 harness，观察它的行为与判定

**不能**：
  · 跑该仓库的**完整测试套件**。SWE-bench Pro 官方评测需要每题的 Docker 镜像
    （`docker_image` 字段，`ghcr.io/scaleapi/...`）。本机网络到 ghcr.io 不通，
    且镜像体积与启动成本都不适合在这里跑。
  · 因此这里**不报告 Pass@1**。没有跑官方 test_patch 就算不出通过率 ——
    报一个"看起来像通过率"的数字比不报更糟（那正是 BenchJack 里
    "返回 {} 就过"的形态）。

## 那我们测什么

分两层，各自诚实标注证据强度：

### 第 1 层：判定正确性（强证据）

用真实题目的 `requirements` 与真实代码，构造**该被拦住的输入**，
看 harness 的闸门拦不拦得住。具体三类：

  A. **假完成**：模型只说"我改好了"，一个文件都没动
     → `preflight` / `certificate` 必须拒
  B. **改错位置**：动了文件但改的不是 requirements 要求的地方
     → 目标/证书层能否发现"证据不支撑声明"
  C. **真完成**：正确改动 + 真实跑过验证命令的（用题目自己的测试文件路径）
     → 必须**放行**（不误拦）

这一类是**强证据**：输入来自公开基准，判据是确定性的，
结论可复现，且不依赖模型能力。

### 第 2 层：行为观察（弱证据，仅作参考）

让真实模型在真实仓库上尝试解一道题，记录过程指标：
工具调用数、是否读到了关键文件、是否跑过验证、闸门拦截情况。
**不评判对不对**（没有官方测试就跑不了正确答案），只看"过程是否像样"。

## 为什么不用 SWE-bench Verified

文档已记录：OpenAI 于 2026-02 停用该基准，审计发现其失败任务里
至少 59.4% 的测试用例本身有缺陷。用一个已知有问题的基准测出来的数字，
说服力还不如没有。

## 用法

    python harness/bench_swe_pro.py --fetch          # 只抓数据集到本地缓存
    python harness/bench_swe_pro.py --layer1         # 跑第 1 层判定测试
    python harness/bench_swe_pro.py --layer1 --n 10  # 抽样 10 题
    python harness/bench_swe_pro.py --list           # 看缓存的题目摘要
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

# 脚本直跑时把项目根塞进 sys.path（本项目第四次踩这个坑后的标准做法）
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATASET = "ScaleAI/SWE-bench_Pro"
SERVER = "https://datasets-server.huggingface.co"
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         ".workbuddy", "bench")


def _get_json(url: str, timeout: int = 40):
    req = urllib.request.Request(url, headers={"User-Agent": "crucible-bench/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_rows(config: str = "hard", length: int = 60) -> list:
    """从官方数据集服务取题目。

    为什么用 `datasets-server` 而不是直接下 parquet：
      · parquet 的 `resolve` 链接会被镜像 308 重定向回 huggingface.co，
        而本机到 huggingface.co 不通（实测 502）
      · datasets-server 是独立域名，直接返回 JSON 行，无需解析 parquet，
        也不需要 `datasets`/`pyarrow` 这类重依赖

    分页取（服务端单次上限通常 100）—— 这里按 length 一次拿够，
    拿不满就如实返回，不假装"全量"。
    """
    q = urllib.parse.urlencode({
        "dataset": DATASET, "config": config, "split": "test",
        "offset": 0, "length": min(length, 100),
    })
    data = _get_json(f"{SERVER}/rows?{q}")
    rows = [r["row"] for r in (data.get("rows") or [])]
    total = data.get("num_rows_total")
    return rows, total


def cache_tasks(config: str = "hard", length: int = 60) -> str:
    rows, total = fetch_rows(config, length)
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"swe_pro_{config}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"config": config, "total_available": total,
                   "fetched": len(rows), "tasks": rows},
                  f, ensure_ascii=False, indent=2)
    return path


def load_tasks(config: str = "hard") -> list:
    path = os.path.join(CACHE_DIR, f"swe_pro_{config}.json")
    if not os.path.isfile(path):
        cache_tasks(config)
    with open(path, encoding="utf-8") as f:
        return json.load(f)["tasks"]


def summarize(tasks: list) -> None:
    print(f"已缓存 {len(tasks)} 题")
    print(f"{'repo':28} {'lang':6} {'改动行':>6} {'声明要求':>8}  instance")
    for t in tasks:
        patch = t.get("patch") or ""
        added = sum(1 for ln in patch.splitlines()
                    if ln.startswith("+") and not ln.startswith("+++"))
        req = t.get("requirements") or ""
        # requirements 是给人看的清单，用行首 "- " 数条目
        n_req = sum(1 for ln in req.splitlines() if ln.strip().startswith("-"))
        print(f"{t.get('repo',''):28} {t.get('repo_language',''):6} "
              f"{added:>6} {n_req:>8}  {t.get('instance_id','')[:50]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SWE-bench Pro 抽样基准")
    ap.add_argument("--config", default="hard", choices=["hard", "default", "v1"])
    ap.add_argument("--fetch", action="store_true", help="抓数据集到本地缓存")
    ap.add_argument("--list", action="store_true", help="列出缓存的题目")
    ap.add_argument("--layer1", action="store_true", help="跑判定正确性测试")
    ap.add_argument("--n", type=int, default=6, help="抽样题数")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.fetch:
        p = cache_tasks(args.config)
        tasks = load_tasks(args.config)
        print(f"已写入 {p}")
        summarize(tasks)
        return 0

    if args.list:
        summarize(load_tasks(args.config))
        return 0

    if args.layer1:
        from harness.bench_layer1 import run_layer1
        res = run_layer1(tasks=load_tasks(args.config)[:args.n])
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            from harness.bench_layer1 import render
            print(render(res))
        return 0 if res["trustworthy"] else 1

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
