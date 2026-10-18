"""变更集记录与回滚：改坏了能还原，且不靠 git。

## 为什么不能只依赖 git

文档点出的那个陷阱很关键：

> `git checkout` 在 `workspace-write` 下**是被允许的** ——
> 这既是恢复手段，也是**破坏手段**。

也就是说 agent 既能用 git 恢复，也能用 git 把工作区砸得更烂。
而且更根本的问题是：
  · 不是每个工作区都有 git（我们实测过：临时工作区里 `git status` 直接失败）
  · git 只跟踪**已提交**的历史，未跟踪的新文件与未提交的改动不在它的保护范围内
  · git 不知道"哪一次工具的哪一步干的" —— 无法按 seq 回滚到某个中间态

所以恢复能力必须**自己做**，而不是把责任推给外部工具。

## 前沿依据（arXiv 2604.28138 Crab + arXiv 2605.22781 DeltaBox）

两条关键结论直接决定了本模块的形状：

1. **「>75% 的轮次不产生与恢复相关的状态」**
   → 所以**不做每轮全量快照**。只在文件**确实变了**时才记录。
   全量快照会让每轮都付一次拷贝代价，而那 75% 的轮次是白付的。

2. **「只复制变化」（DeltaBox：检查点 14ms / 回滚 5ms）**
   → 所以存的是**变更集**（改前的旧内容 + 改后的新内容），不是整目录副本。

Crab 用 eBPF 做系统级分类，我们用不了也不必要：
**agent 只会通过工具改文件**（这是受信任代码的唯一写入口），
所以把插桩点放在工具管线上就够，覆盖面接近 100%。

## 三条设计纪律

1. **只在文件真的变了时记录。** 哈希比对先行，内容相同就跳过 ——
   这是"75% 的轮次不该付代价"的直接落地。

2. **记录必须可独立还原。** 存的是**旧内容的完整拷贝**（不是 diff），
   因为回滚时没有可靠的基础版本可用来重放 diff。
   代价是空间，但被"只在变更时记录"抵消了。

3. **回滚是显式动作，且可指定到某个 seq。**
   不做"自动回滚" —— 那会让 agent 的失败变成静默的状态跳变，
   比改坏更难排查。

## 存储布局

    <run_dir>/changesets/
      index.jsonl          每次变更一行（append-only）
      blobs/<hash>         旧内容（按内容哈希去重，同内容只存一份）

用**内容哈希**做 blob 文件名而不是序号：同一份旧内容被多次覆盖时只存一份，
且天然幂等（重复写入同一 hash 无副作用）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


CHANGESET_DIR = "changesets"
INDEX_FILE = "index.jsonl"
BLOB_DIR = "blobs"

# 快照时跳过的目录（运行时产物、依赖、VCS）
_SNAP_IGNORE = {".git", "node_modules", "__pycache__", ".venv", "venv",
                "dist", "build", ".next", "target", ".mypy_cache",
                ".pytest_cache", ".crucible"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ══════════════════════════════════════════════════════════════════
# 记录
# ══════════════════════════════════════════════════════════════════
@dataclass
class Change:
    """一个文件的**一次**变更。

    `before` 为 None 表示该文件此前不存在（本次是新建）；
    `after` 为 None 表示本次删除了它。
    """

    path: str                      # 相对工作区的路径
    seq: int                       # 对应的事件 seq（用于"回滚到第 N 步"）
    tool: str
    before_hash: Optional[str]
    after_hash: Optional[str]
    before_exists: bool
    after_exists: bool
    # 字节数，便于报告"这次回滚会动多少数据"
    bytes_before: int = 0
    bytes_after: int = 0

    def to_json(self) -> Dict[str, Any]:
        return {
            "path": self.path, "seq": self.seq, "tool": self.tool,
            "before_hash": self.before_hash, "after_hash": self.after_hash,
            "before_exists": self.before_exists, "after_exists": self.after_exists,
            "bytes_before": self.bytes_before, "bytes_after": self.bytes_after,
        }


class ChangeSetStore:
    """变更集的落盘与回滚。

    与 Session 的关系：Session 管事件落盘，本类管**文件内容**落盘。
    分开的理由是它有独立的生命周期 —— 回滚可能在进程重启之后才发生，
    那时 Session 早就没了，但变更集还在盘上（这正是它要解决的问题）。
    """

    def __init__(self, run_dir: str, workspace: str,
                 enabled: bool = True) -> None:
        self.run_dir = run_dir
        self.workspace = os.path.realpath(workspace)
        self.enabled = enabled
        self.dir = os.path.join(run_dir, CHANGESET_DIR)
        self._index_path = os.path.join(self.dir, INDEX_FILE)
        self._blob_dir = os.path.join(self.dir, BLOB_DIR)
        self.errors = 0
        if enabled:
            try:
                os.makedirs(self._blob_dir, exist_ok=True)
            except OSError:
                self.errors += 1

    # ---------- 路径安全 ----------
    def _resolve(self, rel: str) -> Optional[str]:
        """把相对路径解析到工作区内；越界返回 None（与 fs.resolve_within 同判据）。"""
        p = (rel or "").strip().replace("\\", "/")
        if not p or os.path.isabs(p):
            return None
        full = os.path.realpath(os.path.join(self.workspace, p))
        if full != self.workspace and not full.startswith(self.workspace + os.sep):
            return None
        return full

    def _rel(self, full: str) -> str:
        return os.path.relpath(full, self.workspace).replace(os.sep, "/")

    # ---------- blob ----------
    def _blob_path(self, h: str) -> str:
        return os.path.join(self._blob_dir, h)

    def _read_file(self, full: str) -> Optional[str]:
        """读文件内容。二进制/读失败一律返回 None —— 我们不假装能还原它。

        为什么不对二进制做处理：回滚二进制需要存原始字节，
        而我们的判断依赖"能不能解码成文本"。**如实拒绝**比假装支持更安全 ——
        如果这里静默存了乱码，回滚会把文件写成损坏内容，比不回滚更糟。
        """
        try:
            with open(full, "rb") as f:
                raw = f.read()
        except OSError:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def _store_blob(self, text: str) -> Optional[str]:
        """存一份旧内容。

        **二进制写入是必须的**：文本模式（`open(p, "w")`）在 Windows 上
        会把 `\\n` 翻译成 `\\r\\n`，于是存进去的就不是原内容了 ——
        实测症状是回滚后文件多出 `\\r` 或重复换行，逐字节比对失败。

        回滚的唯一价值是**逐字节还原**；做不到逐字节，那就不叫还原，
        叫"换了个内容的文件"。项目对工具参数有「保真性」纪律，
        这里同理。
        """
        h = _sha(text)
        p = self._blob_path(h)
        if os.path.exists(p):
            return h                    # 去重：同内容只存一份
        try:
            with open(p, "wb") as f:
                f.write(text.encode("utf-8"))
        except OSError:
            self.errors += 1
            return None
        return h

    # ---------- 记录 ----------
    def capture(self, paths: Sequence[str], tool: str, seq: int,
                before: Optional[Dict[str, Optional[str]]] = None,
                before_was_full_snapshot: bool = False) -> List[Change]:
        """记录一批文件的变更。

        `before` 是**执行前**读到的内容快照（`{rel: 内容 or None}`）。
        为什么要求调用方先读：文件一旦被写，旧内容就没了 ——
        这是唯一能拿到旧内容的时机，无法事后补救。

        `before_was_full_snapshot=True` 表示 `before` 是一份**完整的工作区快照**
        （而不是只读了预判路径）。此时"不在快照里"的文件可以确定为
        **当时不存在**，按新建处理。
        这一点很关键：`shell` 写文件无法预知路径，工具事后报出的
        `files_changed` 里必然包含新文件 —— 若因为"快照里没有"就跳过，
        等于漏记了最常见的 shell 建文件场景（实测踩到，且是漏记而非报错，
        非常隐蔽）。

        而 `before_was_full_snapshot=False`（只预读了几个路径）时，
        不在里面的文件**无法判断**当时是否存在 —— 那种情况下
        如实跳过，不猜。

        返回真正发生了变化的 Change 列表（内容相同的会被跳过）。
        这里刻意**不抛异常**：记录失败不该影响 agent 干活
        （与 `_persist_event` 同一取向），但会计数供上层检查。
        """
        if not self.enabled:
            return []
        out: List[Change] = []
        for rel in paths:
            try:
                if before is None:
                    before_text = ...
                elif rel in before:
                    before_text = before[rel]
                elif before_was_full_snapshot:
                    # 完整快照里没有它 → 当时不存在（新建）
                    before_text = None
                else:
                    before_text = ...
                if before_text is ...:
                    # 调用方没给旧内容、也无法推断 → 如实跳过，不猜
                    continue
                full = self._resolve(rel)
                if full is None:
                    continue
                after_text = self._read_file(full) if os.path.exists(full) else None
                before_exists = before_text is not None
                after_exists = after_text is not None

                # ── 关键：内容相同就跳过 ──
                # arXiv 在先：>75% 的轮次不产生与恢复相关的状态。
                # 每次都记 = 那 75% 白付代价。
                if before_exists == after_exists and before_text == after_text:
                    continue

                bh = self._store_blob(before_text) if before_text is not None else None
                ah = _sha(after_text) if after_text is not None else None
                ch = Change(
                    path=rel, seq=seq, tool=tool,
                    before_hash=bh, after_hash=ah,
                    before_exists=before_exists, after_exists=after_exists,
                    bytes_before=len(before_text or ""), bytes_after=len(after_text or ""),
                )
                with open(self._index_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(ch.to_json(), ensure_ascii=False) + "\n")
                out.append(ch)
            except OSError:
                self.errors += 1
        return out

    def capture_before(self, paths: Sequence[str]) -> Dict[str, Optional[str]]:
        """在执行**写操作之前**读取这些文件的当前内容。

        这是 `capture()` 的配套前置动作。返回 `{rel: 内容 or None}`，
        None 表示文件此刻不存在（用于区分"新建"与"修改"）。
        """
        out: Dict[str, Optional[str]] = {}
        for rel in paths:
            full = self._resolve(rel)
            if full is None:
                continue
            out[rel] = self._read_file(full) if os.path.exists(full) else None
        return out

    # ---------- 不可预知路径时的兜底 ----------
    # shell 这类工具无法预知会改哪些文件。但"读取旧内容"只有**执行前**
    # 这一个时机 —— 执行后再读，拿到的已经是新内容了。
    # 所以必须在执行前拍一份内容快照。
    #
    # 代价控制（arXiv 2604.28138："**>75% 的轮次不产生与恢复相关的状态**"）：
    #   · 只在工具声明 `side_effect=True` 时才调用本方法
    #     → 全部只读命令（ls/grep/git status…）零代价
    #   · 只拍**文本文件**且小于 `max_bytes` 的
    #     → 二进制与超大文件不拍，如实标记"无法还原"
    #   · 有文件数上限，防止在巨型仓库里失控
    # 这三条让"该付代价的那 25%"才付代价，其余不动。
    SNAP_MAX_BYTES = 256 * 1024
    SNAP_MAX_FILES = 2000

    def snapshot_workspace_content(self) -> Dict[str, Optional[str]]:
        """执行前拍一份"相对路径 → 内容"的快照（用于无法预知路径的工具）。

        返回的内容可直接作为 `capture(..., before=...)` 的入参：
        未出现在返回值里的文件就是"当时不存在"。
        """
        out: Dict[str, Optional[str]] = {}
        root = self.workspace
        if not os.path.isdir(root):
            return out
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in _SNAP_IGNORE and not d.startswith(".")]
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(full) > self.SNAP_MAX_BYTES:
                        continue
                except OSError:
                    continue
                text = self._read_file(full)
                if text is None:
                    continue            # 二进制 / 读失败 → 不假装能还原
                out[self._rel(full)] = text
                if len(out) >= self.SNAP_MAX_FILES:
                    return out
        return out

    # ---------- 读取 ----------
    def history(self) -> List[Change]:
        """按写入顺序返回全部变更（append-only，天然有序）。"""
        if not os.path.isfile(self._index_path):
            return []
        out: List[Change] = []
        with open(self._index_path, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = json.loads(ln)
                except json.JSONDecodeError:
                    continue                # 损坏的行跳过，保留其余（同 session 取向）
                out.append(Change(
                    path=d.get("path", ""), seq=int(d.get("seq") or 0),
                    tool=d.get("tool", ""),
                    before_hash=d.get("before_hash"),
                    after_hash=d.get("after_hash"),
                    before_exists=bool(d.get("before_exists")),
                    after_exists=bool(d.get("after_exists")),
                    bytes_before=int(d.get("bytes_before") or 0),
                    bytes_after=int(d.get("bytes_after") or 0),
                ))
        return out

    def summary(self) -> Dict[str, Any]:
        h = self.history()
        return {
            "enabled": self.enabled,
            "changes": len(h),
            "files": len({c.path for c in h}),
            "errors": self.errors,
            "bytes_available": sum(c.bytes_before for c in h if c.before_hash),
        }

    # ---------- 回滚 ----------
    def rollback(self, to_seq: Optional[int] = None,
                 dry_run: bool = False) -> Dict[str, Any]:
        """把工作区还原到某个 seq 之前的状态。

        `to_seq=None` 表示**撤销全部**变更。
        语义是"撤销 seq >= to_seq 的那些变更"（即回到 `to_seq` 那一刻之前）。

        为什么从**后往前**逐个还原：后面的变更可能建立在前面的基础上
        （文件 A 被改两次），倒序还原才能让最早的 `before_hash` 正确落位。
        正序还原会把后面的旧内容盖掉前面的正确状态。

        **不自动回滚**：本方法只被显式调用。自动回滚会让 agent 的失败
        变成静默的状态跳变，比"改坏了"更难排查。
        """
        all_changes = self.history()
        if to_seq is None:
            targets = all_changes
        else:
            targets = [c for c in all_changes if c.seq >= to_seq]

        if not targets:
            return {"ok": True, "restored": [], "skipped": [],
                    "note": "没有需要回滚的变更。"}

        # 按 seq 倒序、同 seq 内按记录顺序倒序 —— 保证后发生的先还原
        targets = list(reversed(targets))

        restored: List[str] = []
        skipped: List[Dict[str, str]] = []

        for c in targets:
            full = self._resolve(c.path)
            if full is None:
                skipped.append({"path": c.path, "reason": "路径越界或不可解析"})
                continue
            try:
                if not c.before_exists:
                    # 这次是**新建** → 回滚意味着删掉它
                    if os.path.exists(full):
                        if dry_run:
                            restored.append(c.path + "（将删除）")
                        else:
                            os.remove(full)
                            restored.append(c.path + "（已删除）")
                    continue
                # 恢复旧内容
                if not c.before_hash:
                    skipped.append({"path": c.path,
                                    "reason": "没有存下旧内容，无法还原"})
                    continue
                blob = self._blob_path(c.before_hash)
                if not os.path.isfile(blob):
                    skipped.append({"path": c.path,
                                    "reason": f"内容块缺失（{c.before_hash[:8]}）"})
                    continue
                # 二进制读写：文本模式会在 Windows 上把 \n 翻成 \r\n，
                # 于是"还原"出来的内容与原文不一致（实测踩到）。
                with open(blob, "rb") as f:
                    raw = f.read()
                if dry_run:
                    restored.append(c.path)
                else:
                    os.makedirs(os.path.dirname(full) or self.workspace,
                                exist_ok=True)
                    with open(full, "wb") as f:
                        f.write(raw)
                    restored.append(c.path)
            except OSError as e:
                skipped.append({"path": c.path, "reason": str(e)})

        # 去重但保序（同一文件被撤多次，报告里只出现一次更可读）
        seen = set()
        uniq = [p for p in restored if not (p in seen or seen.add(p))]
        return {
            "ok": not skipped,
            "dry_run": dry_run,
            "restored": uniq,
            "skipped": skipped,
            "rolled_back_changes": len(targets),
        }

    def discard(self) -> None:
        """删掉全部变更集（工作区被外部重置、记录已无用时的清理入口）。"""
        shutil.rmtree(self.dir, ignore_errors=True)
