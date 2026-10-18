"""哈希链记录：让轨迹**可被外部核验未被篡改**。

## 为什么这是我们的差异化点

arXiv 2608.23953（*What Makes a Good Harness*，2026-08）在对比三个成熟 harness
（Claude Code / Codex / OpenHands）后指出：

> 三者的共同缺失是**外部可验证性** —— 没有任何一个提供防篡改的执行记录。
> 这在 provenance 敏感领域（审计、合规、法务、训练数据溯源）是刚需，
> 并且被作者点名为 **harness 的下一个分化轴**。

我们的 `events.jsonl` 已经解决了"顺序"，但没解决"完整性"：
它是一份普通追加文件，`seq` 有单调保证，**但任何一行都可以被编辑而不留痕迹**。
一份可以被无声改写的记录，在审计场景里等于没有。

## 做法：每条事件带前一条的指纹

    第 1 条: prev = GENESIS(全零)
    第 n 条: prev = hash(n-1)，hash = SHA256(prev + 规范化序列化(本条))

于是改任意一条，从那一条起**全部 hash 对不上**；删一条，后面的 prev 断裂；
插一条，同样断裂。这不需要密钥、不需要外部服务，任何人离线就能核验。

## 三条纪律

1. **只覆盖"语义内容"，不覆盖"落盘字节"。** 哈希算的是规范化 JSON
   （`sort_keys=True`、无多余空格）。否则换行符、缩进、键顺序的差异都会
   制造假警报 —— 而假警报会让核验机制退化成噪音。
   这与本项目在 GoalVerifier / preflight 上学到的教训是同一条：
   **判定器必须先能在正确时通过。**

2. **链头单独存文件**（`chain.head`）。整条链每次都重新算太慢；
   核验时用链头做快速断言，再按需全量重算。

3. **绝不因为写链失败而影响主循环。** 记录完整性与 agent 能不能干活是两件事，
   后者更重要。写失败只计数，供上层检查（与 `_persist_failures` 同样的处理）。

## 为什么不用 HMAC / 签名

签名解决的是"**谁**写的"，哈希链解决的是"**有没有被改过**"。
后者不需要密钥就能外部核验 —— 第三方拿着 `events.jsonl` 就能自证，
而签名要求第三方信任密钥持有者。对我们的场景（agent 行为审计、
训练数据溯源），可离线自证更有价值。
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Tuple


# 创世指纹：链的起点。用全零而非空串，是为了让"第一条的 prev 是空的"
# 这种结构性错误也表现为明确的错值，而不是恰好通过。
GENESIS = "0" * 64

CHAIN_FILE = "chain.head"
EVENTS_FILE = "events.jsonl"


# ══════════════════════════════════════════════════════════════════
# 规范化：哈希的输入
# ══════════════════════════════════════════════════════════════════
def canonical_bytes(record: Dict[str, Any]) -> bytes:
    """把一条记录变成**规范**字节串。

    刻意只取链关心的字段，而不是整个 dict —— 落盘时可能被追加了
    展示用的字段（如前端补的 `ts_ms`），那些不该进哈希：
    否则一次无害的展示层改动会让整条历史变成"被篡改"。

    保留的字段是**语义**字段：事件类型、序号、时间、回合、内容。
    """
    payload = {
        "type": record.get("type"),
        "seq": record.get("seq"),
        "ts": record.get("ts"),
        "turn_id": record.get("turn_id"),
        "data": record.get("data"),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def compute_hash(prev_hash: str, record: Dict[str, Any]) -> str:
    """hash = SHA256(prev_hash || 本条规范字节)。

    把 prev 也纳入哈希输入，是链式的关键 —— 只哈希本条内容的话，
    调整两条记录的顺序将不可检出。
    """
    h = hashlib.sha256()
    h.update(prev_hash.encode("ascii"))
    h.update(canonical_bytes(record))
    return h.hexdigest()


# ══════════════════════════════════════════════════════════════════
# 追加端
# ══════════════════════════════════════════════════════════════════
class HashChain:
    """一次运行的事件哈希链。

    与 Session 的关系：Session 负责**写文件**，本类负责**算指纹**。
    分开的理由是职责单一 —— 链的正确性可以脱离文件 IO 单独测试，
    这是它作为"外部可核验"机制的前提。
    """

    def __init__(self, run_dir: str, enabled: bool = True) -> None:
        self.run_dir = run_dir
        self.enabled = enabled
        self._prev = GENESIS
        self._count = 0
        self.errors = 0

    # ---------- 状态 ----------
    @property
    def head(self) -> str:
        return self._prev

    @property
    def count(self) -> int:
        return self._count

    # ---------- 追加 ----------
    def next_hash(self, record: Dict[str, Any]) -> str:
        """算出下一条的 hash 并推进链。

        注意这里**只推进内存状态**，不写文件 —— 调用方（Session）
        会把 hash 一起写进那行 JSON，保持"一条事件一次写入"。
        分成两次写会制造一个"事件已落盘但链头还没落盘"的窗口，
        崩溃时那段窗口无法核验。
        """
        h = compute_hash(self._prev, record)
        self._prev = h
        self._count += 1
        return h

    def write_head(self) -> None:
        """把链头单独落盘（供快速核验）。

        失败不影响主循环 —— 记录完整性与 agent 干活是两件事。
        """
        if not self.enabled:
            return
        try:
            path = os.path.join(self.run_dir, CHAIN_FILE)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"head": self._prev, "count": self._count,
                           "genesis": GENESIS}, f, ensure_ascii=False)
            os.replace(tmp, path)      # 原子替换：绝不会留下半个文件
        except OSError:
            self.errors += 1


# ══════════════════════════════════════════════════════════════════
# 核验端（**外部可用**：不依赖本项目的任何运行时状态）
# ══════════════════════════════════════════════════════════════════
def verify_chain(run_dir: str) -> Dict[str, Any]:
    """离线核验一条链。

    返回结构化结果，而不是抛异常 —— 调用方可能是审计脚本、
    CI job、或者另一个语言实现的核验器（只需照抄这套哈希规则）。

    检查三类破坏：
      · **改写**：某条内容变了 → 该条 hash 对不上
      · **删除**：中间少一条 → 后继的 prev 接不上
      · **插入/重排**：seq 不连续，或 prev 指向不存在的 hash

    另一类必须单独查的是 **seq 本身**：即使 hash 链自洽，
    seq 有空洞也说明文件被编辑过（因为 hash 只覆盖内容，
    正常追加不可能产生空洞）。

    ## 最要紧的一条：**无链 ≠ 被篡改**

    在链功能上线之前产生的运行**根本没有回 hash 字段**。
    若把它们一律判为"被篡改"，就会制造大量假警报 ——
    而假警报会让核验机制彻底失去可信度（本项目在 GoalVerifier 上吃过一次）。

    所以这里先判断"**这次运行到底有没有链**"：
      · 首条事件带回 hash/prev_hash → 有链，正常核验
      · 完全没有任何链字段        → 明确标为 `unchained`，
        如实说明"这次运行早于链功能，无法核验完整性"
      · 只有部分事件带链（>0 且 <全部）→ 这**是真的异常**：
        说明有人往带链的运行里插了不带链的记录，或删掉了链字段。
    """
    events_path = os.path.join(run_dir, EVENTS_FILE)
    result: Dict[str, Any] = {
        "run_dir": run_dir,
        "ok": False,
        "checked": 0,
        "problems": [],
        "head": GENESIS,
        "stored_head": None,
        "unchained": False,
    }

    if not os.path.isfile(events_path):
        result["problems"].append({"kind": "missing_events_file",
                                   "detail": f"找不到 {events_path}"})
        return result

    # ── 先扫一遍：这次运行有没有链？──
    # 这一步必须先做，因为后面的核验逻辑假设"事件里有 hash"。
    total_lines = 0
    chained_lines = 0
    with open(events_path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            total_lines += 1
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if rec.get("hash") or rec.get("prev_hash"):
                chained_lines += 1

    if total_lines == 0:
        result["ok"] = True
        result["note"] = "事件文件为空（运行可能还没产生任何事件）。"
        return result

    if chained_lines == 0:
        # 旧运行：明确标注，不制造假警报
        result["unchained"] = True
        result["checked"] = total_lines
        result["ok"] = True          # 「无法核验」不等于「被篡改」
        result["note"] = (
            f"这次运行（{total_lines} 条事件）产生于链功能上线之前，"
            "事件里没有任何 hash 字段，**无法核验完整性**。"
            "这不表示它被篡改过 —— 只表示没有可比对的锚点。"
        )
        return result

    if chained_lines != total_lines:
        # 部分带链 = 真异常，继续往下走让具体问题暴露出来
        result["problems"].append({
            "kind": "partial_chain",
            "detail": f"共 {total_lines} 条事件，其中只有 {chained_lines} 条带 hash。"
                      "混有未带链的记录说明事件文件被编辑过"
                      "（正常追加的每一行都会带 hash）。",
        })

    prev = GENESIS
    n = 0
    bad: List[Dict[str, Any]] = list(result["problems"])

    with open(events_path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError as e:
                bad.append({"kind": "unparsable_line", "line": lineno,
                            "detail": str(e)})
                continue

            n += 1
            seq = rec.get("seq")

            # 1. seq 必须连续（正常追加的唯一形态）
            if seq != n:
                bad.append({"kind": "seq_gap", "line": lineno,
                            "detail": f"第 {lineno} 行的 seq={seq}，应为 {n}。"})

            # 2. prev 必须指向上一条的 hash
            claimed_prev = rec.get("prev_hash")
            if claimed_prev != prev:
                bad.append({
                    "kind": "prev_mismatch", "line": lineno, "seq": seq,
                    "detail": "该条声称的前驱指纹与上一条的实际指纹不符 —— "
                              "说明在它之前有内容被增删或改写。",
                    "claimed": claimed_prev, "expected": prev,
                })

            # 3. 本条 hash 必须真的由 (prev, 内容) 算出
            claimed_hash = rec.get("hash")
            actual = compute_hash(prev, rec)
            if claimed_hash != actual:
                bad.append({
                    "kind": "content_tampered", "line": lineno, "seq": seq,
                    "detail": "该条的内容指纹与按内容重算的结果不符 —— "
                              "这条记录本身被改过。",
                    "claimed": claimed_hash, "recomputed": actual,
                })

            # 链继续推进（用**算出的**值，不用声称的值 ——
            # 否则一条被改的记录可以靠伪造 hash 蒙过后续检查）
            prev = actual
            result["head"] = prev

    result["checked"] = n

    # 4. 与落盘的链头比对
    head_path = os.path.join(run_dir, CHAIN_FILE)
    if os.path.isfile(head_path):
        try:
            with open(head_path, "r", encoding="utf-8") as f:
                stored = json.load(f)
            result["stored_head"] = stored.get("head")
            if stored.get("head") != prev:
                bad.append({
                    "kind": "head_mismatch",
                    "detail": "chain.head 记录的最终指纹与从事件重算的不符 —— "
                              "事件文件在链头写入之后又被改过。",
                    "stored": stored.get("head"), "recomputed": prev,
                })
            if stored.get("count") not in (None, n):
                bad.append({
                    "kind": "count_mismatch",
                    "detail": f"chain.head 记录 {stored.get('count')} 条，"
                              f"实际 {n} 条。",
                })
        except (OSError, json.JSONDecodeError) as e:
            bad.append({"kind": "bad_head_file", "detail": str(e)})
    else:
        # 没有链头不是错误 —— 可能是运行中途崩溃（那时本就没有链头）。
        # 但不能静默：审计者需要知道"这次没有可比的锚点"。
        result["note"] = "没有 chain.head，只做了链内自洽性检查。"

    result["problems"] = bad
    result["ok"] = not bad
    return result


def verify_all(runs_root: str) -> Dict[str, Any]:
    """核验一个工作区下的全部运行（供 CI / 审计脚本批量使用）。"""
    if not os.path.isdir(runs_root):
        return {"runs": 0, "ok": 0, "failed": [], "note": "runs 目录不存在"}
    dirs = sorted(d for d in os.listdir(runs_root)
                  if os.path.isdir(os.path.join(runs_root, d)))
    failed: List[Dict[str, Any]] = []
    ok_n = 0
    unchecked: List[str] = []
    for d in dirs:
        full = os.path.join(runs_root, d)
        if not os.path.isfile(os.path.join(full, EVENTS_FILE)):
            continue
        r = verify_chain(full)
        if r["ok"]:
            ok_n += 1
        else:
            failed.append({"run": d, "problems": r["problems"]})
        if r.get("note"):
            unchecked.append(d)
    return {
        "runs": ok_n + len(failed),
        "ok": ok_n,
        "failed": failed,
        "without_anchor": unchecked,
    }
