"""apply_patch —— V4A 补丁格式（对齐 Codex）。

## 为什么用结构化补丁而不是「重写整个文件」

Codex 的 `apply_patch` 用自定义 V4A 格式（`UpdateFileChunk` + `Hunk`，
解析器在 `codex-rs/apply-patch/src/lib.rs`），好处有两条：

  1. **省 token** —— 改十行不必重写一千行。（书 §5）
  2. **可审计、可回滚** —— 每次编辑是一张结构化补丁，能连同会话一起落盘重放。

书 §5 的「最小 diff + 可回滚」设计模式正是这条：**每次修改尽量小、带来源、
可单独回滚**，它让归因成为可能。

## 格式

    *** Begin Patch
    *** Add File: src/new.ts
    +export const X = 1;
    *** Update File: src/old.ts
    @@ export function nextDelay(attempt: number): number {
    -  const cap = BASE * 2 ** attempt;
    +  const cap = Math.min(BASE * 2 ** attempt, MAX);
    *** Delete File: src/gone.ts
    *** End Patch

## 唯一性纪律（本实现的安全核心）

书 §4 的**参数保真性**原则要求工具不做静默修正：
> 模型感知到的世界与工具操作的世界之间，不能存在系统性的偏差。

对应到补丁，就是：**上下文匹配不唯一时，拒绝执行，绝不猜。**
Codex 的解析器「补丁对不上原文件就拒绝落盘」也是同一条。

因此 `_locate()` 只在**恰好一处匹配**时返回位置；0 处或 ≥2 处都返回失败，
并把**具体的歧义信息**（匹配到几处、都在哪几行）作为结构化错误反馈给模型，
让模型补更多上下文重来 —— 而不是替它做决定。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..policy import Action, ActionKind
from ..protocol import ToolResult
from .fs import resolve_within, to_rel
from .registry import Tool, ToolRegistry

BEGIN = "*** Begin Patch"
END = "*** End Patch"
ADD = "*** Add File: "
UPDATE = "*** Update File: "
DELETE = "*** Delete File: "
MOVE_TO = "*** Move to: "
HUNK = "@@"


# ══════════════════════════════════════════════════════════════════
# 解析
# ══════════════════════════════════════════════════════════════════
@dataclass
class Hunk:
    """一个修改块：可选的上下文锚点 + 若干增删行。"""
    anchor: Optional[str] = None          # @@ 后面的定位文本
    old: List[str] = field(default_factory=list)
    new: List[str] = field(default_factory=list)


@dataclass
class FileOp:
    kind: str                             # add | update | delete
    path: str
    lines: List[str] = field(default_factory=list)     # add 用
    hunks: List[Hunk] = field(default_factory=list)    # update 用
    move_to: Optional[str] = None


class PatchParseError(Exception):
    pass


def parse_patch(text: str) -> List[FileOp]:
    """解析 V4A 补丁。

    刻意写得严格：格式错误必须**明确报错并指出行号**，
    因为模型需要精确的错误信息才能自行纠正（书 §5「反馈越结构化越好」）。
    """
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")

    # 允许补丁前后有说明文字，但 Begin/End 必须存在且成对
    try:
        i = next(k for k, ln in enumerate(lines) if ln.strip() == BEGIN)
    except StopIteration:
        raise PatchParseError(f"缺少 `{BEGIN}` 起始标记。补丁必须以该行开始。")

    try:
        j = next(k for k in range(len(lines) - 1, i, -1) if lines[k].strip() == END)
    except StopIteration:
        raise PatchParseError(f"缺少 `{END}` 结束标记。")

    body = lines[i + 1: j]
    ops: List[FileOp] = []
    cur: Optional[FileOp] = None
    cur_hunk: Optional[Hunk] = None
    seen_content = False

    for ln_no, ln in enumerate(body, start=i + 2):
        if ln.startswith(ADD):
            cur = FileOp(kind="add", path=ln[len(ADD):].strip())
            ops.append(cur)
            cur_hunk = None
            seen_content = False
            continue
        if ln.startswith(UPDATE):
            cur = FileOp(kind="update", path=ln[len(UPDATE):].strip())
            ops.append(cur)
            cur_hunk = None
            seen_content = False
            continue
        if ln.startswith(DELETE):
            cur = FileOp(kind="delete", path=ln[len(DELETE):].strip())
            ops.append(cur)
            cur_hunk = None
            seen_content = False
            continue
        if ln.startswith(MOVE_TO):
            if cur is None or cur.kind != "update":
                raise PatchParseError(f"第 {ln_no} 行：`{MOVE_TO}` 只能紧跟在 Update File 之后。")
            cur.move_to = ln[len(MOVE_TO):].strip()
            continue

        if ln.startswith(HUNK):
            if cur is None or cur.kind != "update":
                raise PatchParseError(f"第 {ln_no} 行：`{HUNK}` 只能出现在 Update File 段落内。")
            anchor = ln[len(HUNK):].strip() or None
            cur_hunk = Hunk(anchor=anchor)
            cur.hunks.append(cur_hunk)
            seen_content = True
            continue

        if cur is None:
            if ln.strip() == "":
                continue
            raise PatchParseError(
                f"第 {ln_no} 行：内容出现在任何文件段之前。"
                f"每个内容行都必须属于某个 `{ADD}` / `{UPDATE}` / `{DELETE}` 段。"
            )

        if ln.startswith("+"):
            seen_content = True
            if cur.kind == "add":
                cur.lines.append(ln[1:])
            elif cur.kind == "update":
                if cur_hunk is None:
                    cur_hunk = Hunk()
                    cur.hunks.append(cur_hunk)
                cur_hunk.new.append(ln[1:])
            else:
                raise PatchParseError(f"第 {ln_no} 行：Delete 段内不允许 `+` 行。")
            continue

        if ln.startswith("-"):
            seen_content = True
            if cur.kind != "update":
                raise PatchParseError(f"第 {ln_no} 行：只有 Update 段允许 `-` 行。")
            if cur_hunk is None:
                cur_hunk = Hunk()
                cur.hunks.append(cur_hunk)
            cur_hunk.old.append(ln[1:])
            continue

        if ln.startswith(" "):
            seen_content = True
            if cur.kind != "update":
                raise PatchParseError(f"第 {ln_no} 行：只有 Update 段允许上下文行。")
            if cur_hunk is None:
                cur_hunk = Hunk()
                cur.hunks.append(cur_hunk)
            # 上下文行同时属于 old 与 new
            cur_hunk.old.append(ln[1:])
            cur_hunk.new.append(ln[1:])
            continue

        if ln.strip() == "":
            if cur.kind == "add":
                cur.lines.append("")
            elif cur.kind == "update":
                if cur_hunk is None:
                    cur_hunk = Hunk()
                    cur.hunks.append(cur_hunk)
                cur_hunk.old.append("")
                cur_hunk.new.append("")
            continue

        raise PatchParseError(
            f"第 {ln_no} 行：无法识别的行 `{ln[:60]}`。"
            "内容行必须以 `+`（新增）、`-`（删除）或空格（上下文）开头。"
        )

    if not ops:
        raise PatchParseError("补丁里没有任何文件操作（Add / Update / Delete）。")
    for op in ops:
        if op.kind in ("add",) and not seen_content and not op.lines:
            raise PatchParseError(f"`{ADD}{op.path}` 之后没有任何 `+` 行，空文件请显式写一行 `+`。")
    return ops


# ══════════════════════════════════════════════════════════════════
# 定位：唯一性纪律的实现
# ══════════════════════════════════════════════════════════════════
def _locate(haystack: List[str], needle: List[str], anchor: Optional[str],
            from_index: int = 0) -> Tuple[Optional[int], str]:
    """在 haystack 中定位 needle。

    返回 (起始下标 或 None, 说明文本)。

    **这是安全核心**：只有恰好一处匹配才返回下标。
    0 处 → 报「找不到」；≥2 处 → 报「歧义」并列出所有位置，
    让模型补更多上下文，而不是由工具替它猜一处。
    """
    if not needle:
        return None, "修改块里没有任何行（既没有上下文行，也没有 +/- 行）。"

    n = len(needle)
    hits: List[int] = []
    for i in range(from_index, len(haystack) - n + 1):
        if haystack[i:i + n] == needle:
            hits.append(i)
            if len(hits) > 1:
                # 已发现歧义，无需继续扫（但为了给出完整位置列表，继续扫到上限）
                if len(hits) > 8:
                    break

    if len(hits) == 1:
        # 若给了锚点，额外校验锚点确实在附近（防止长文件里匹配到无关同文段）
        if anchor:
            a_idx = _find_anchor(haystack, anchor, from_index)
            if a_idx is not None and abs(a_idx - hits[0]) > 50:
                return None, (
                    f"匹配到的位置（第 {hits[0] + 1} 行）与锚点 `{anchor[:40]}` "
                    f"（第 {a_idx + 1} 行）相距过远，可能定位到了错误的段落。请把锚点写得更贴近待改内容。"
                )
        return hits[0], ""

    if not hits:
        preview = "\n".join(f"    {ln}" for ln in needle[:8])
        return None, (
            "找不到要修改的原文。请先用 read_file 读取该区域确认**当前实际内容**"
            "（注意行号前缀不属于文件内容本身），再据此重写补丁。\n"
            f"你给出的原文开头是：\n{preview}"
        )

    where = "、".join(f"第 {h + 1} 行" for h in hits[:8])
    more = "（还有更多）" if len(hits) > 8 else ""
    return None, (
        f"要修改的原文在文件中出现了 {len(hits)} 次（{where}{more}），无法确定改哪一处。"
        "请在 `@@` 后加上更贴近的定位文本，或在上下文中多带几行**前后文**来消除歧义。"
        "工具不会替你猜位置。"
    )


def _find_anchor(haystack: List[str], anchor: str, from_index: int) -> Optional[int]:
    for i in range(from_index, len(haystack)):
        if anchor in haystack[i]:
            return i
    return None


# ══════════════════════════════════════════════════════════════════
# 应用
# ══════════════════════════════════════════════════════════════════
def apply_patch(workspace: str, args: Dict[str, Any], dry_run: bool = False) -> ToolResult:
    text = args.get("patch", "")
    if not text.strip():
        return ToolResult.error("patch 不能为空。", retryable=False, kind="bad_args")

    try:
        ops = parse_patch(text)
    except PatchParseError as e:
        # 解析错误明确回传，模型可自行修正
        return ToolResult.error(f"补丁格式有误：{e}", retryable=False, kind="parse_error")

    # ── 先全部校验，再统一落盘（原子性：一个补丁要么全成，要么全不成）──
    planned: List[Tuple[str, str, List[str]]] = []     # (rel, full, 新内容)
    changed: List[str] = []
    diffs: List[str] = []

    for op in ops:
        try:
            full = resolve_within(workspace, op.path)
        except ValueError as e:
            return ToolResult.error(f"`{op.path}`：{e}", retryable=False, kind="bad_path")
        rel = to_rel(workspace, full)

        if op.kind == "add":
            if os.path.exists(full):
                return ToolResult.error(
                    f"`{rel}` 已存在，Add File 只用于新建文件。若想修改它，请改用 Update File。",
                    retryable=False, kind="exists",
                )
            planned.append((rel, full, list(op.lines)))
            changed.append(rel)
            diffs.append(f"[新增] {rel}  (+{len(op.lines)})")
            continue

        if op.kind == "delete":
            if not os.path.exists(full):
                return ToolResult.error(f"`{rel}` 不存在，无法删除。",
                                        retryable=False, kind="not_found")
            planned.append((rel, full, None))
            changed.append(rel)
            diffs.append(f"[删除] {rel}")
            continue

        # update
        if not os.path.exists(full):
            return ToolResult.error(
                f"`{rel}` 不存在，无法修改。若想新建文件，请改用 Add File。",
                retryable=False, kind="not_found",
            )
        try:
            with open(full, "r", encoding="utf-8") as f:
                lines = f.read().split("\n")
        except OSError as e:
            return ToolResult.error(f"读取 `{rel}` 失败：{e}", retryable=False, kind="io")

        cursor = 0
        for hi, hk in enumerate(op.hunks, start=1):
            if not hk.old and not hk.new:
                return ToolResult.error(
                    f"`{rel}` 的第 {hi} 个修改块是空的。", retryable=False, kind="empty_hunk")

            idx, why = _locate(lines, hk.old, hk.anchor, cursor)
            if idx is None:
                return ToolResult.error(
                    f"`{rel}` 第 {hi} 个修改块无法应用：{why}",
                    retryable=False, kind="no_unique_match", path=rel, hunk=hi,
                )
            lines[idx: idx + len(hk.old)] = hk.new
            cursor = idx + len(hk.new)

        planned.append((rel, full, lines))
        adds = sum(len(h.new) for h in op.hunks)
        dels = sum(len(h.old) for h in op.hunks)
        changed.append(rel)
        diffs.append(f"[修改] {rel}  (+{adds} −{dels})")
        if op.move_to:
            try:
                dst = resolve_within(workspace, op.move_to)
            except ValueError as e:
                return ToolResult.error(f"`{op.move_to}`：{e}", retryable=False, kind="bad_path")
            planned[-1] = (rel, full, lines)
            changed.append(to_rel(workspace, dst))

    if dry_run:
        return ToolResult(ok=True, text="[预演] 补丁可应用：\n" + "\n".join(diffs),
                          side_effect=False, files_changed=[])

    # ── 落盘 ──
    written: List[str] = []
    for rel, full, content in planned:
        try:
            if content is None:
                os.remove(full)
            else:
                os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
                with open(full, "w", encoding="utf-8") as f:
                    f.write("\n".join(content))
            written.append(rel)
        except OSError as e:
            # 部分失败：已写的如实报告，避免"以为成功了"的错觉（保真原则）
            return ToolResult.error(
                f"写入 `{rel}` 失败：{e}。已成功写入：{written or '无'}。"
                "注意补丁是逐文件应用的，已写入的文件不会自动回滚，请用 git 检查状态。",
                layer="tool", retryable=False, kind="io",
                files_changed=list(written),
            )

    return ToolResult(
        ok=True,
        text="补丁已应用：\n" + "\n".join(diffs),
        side_effect=True,
        files_changed=written,
        meta={"diff": "\n".join(diffs), "diffs": diffs},
    )


def describe_patch(args: Dict[str, Any]) -> Action:
    """把补丁翻译成结构化 Action。

    注意这里**只做路径提取，不做内容解释** —— 从补丁头解析出会碰哪些文件，
    交给 policy 判定（工作区内/外、是否命中禁改清单）。
    这是「结构化数据供安全检查」的落地：policy 不看补丁的正文语义，
    只看它声明要碰哪些路径。
    """
    text = args.get("patch", "")
    paths: List[str] = []
    for op in (text or "").split("\n"):
        for tag in (ADD, UPDATE, DELETE, MOVE_TO):
            if op.startswith(tag):
                paths.append(op[len(tag):].strip())
    return Action(
        kind=ActionKind.WRITE,
        paths=[p for p in paths if p],
        side_effect=True,
        summary="apply_patch " + ", ".join(paths[:3]),
    )


def register_patch_tools(reg: ToolRegistry, workspace: str) -> None:
    reg.register(Tool(
        name="apply_patch",
        description=(
            "用 V4A 结构化补丁修改文件。支持三种操作：Add File / Update File / Delete File。\n"
            "什么时候用：任何对工作区文件的修改都应走这个工具 —— 它只传输改动部分，"
            "比起重写整个文件更省 token，也留下可审计、可回滚的补丁记录。\n"
            "格式：\n"
            f"  {BEGIN}\n"
            f"  {ADD}src/new.ts\n"
            "  +export const X = 1;\n"
            f"  {UPDATE}src/old.ts\n"
            "  @@ export function f() {      ← 可选锚点，帮助定位\n"
            "  -  const a = 1;\n"
            "  +  const a = 2;\n"
            f"  {DELETE}src/gone.ts\n"
            f"  {END}\n"
            "内容行必须以 `+`（新增）、`-`（删除）或空格（上下文）开头。\n"
            "边界：\n"
            "· **上下文必须能唯一定位**。文件里有多处相同文本时，工具会**拒绝执行**"
            "并告诉你匹配到几处、在哪几行 —— 请补更多前后文或写更贴切的 `@@` 锚点，"
            "不要在同一个补丁上反复重试。\n"
            "· 补丁是**原子**的：任一文件无法应用，则整个补丁都不落盘。\n"
            "· 只接受工作区内的相对路径；命中禁改清单（.git/、node_modules/、.env、*.pem 等）会被拒绝。\n"
            "· 改文件前请先用 read_file 确认当前内容。"
        ),
        properties={
            "patch": {"type": "string", "description": f"完整的 V4A 补丁文本，以 `{BEGIN}` 开始、`{END}` 结束"},
        },
        required=["patch"],
        examples=[
            {"patch": f"{BEGIN}\n{ADD}src/util.ts\n+export const TAU = 6.283;\n{END}"},
            {"patch": f"{BEGIN}\n{UPDATE}src/util.ts\n@@ export const TAU\n-export const TAU = 6.283;\n+export const TAU = 6.2831853;\n{END}"},
        ],
        handler=lambda a: apply_patch(workspace, a),
        describe_action=describe_patch,
        supports_parallel=False,      # 默认否 —— 补丁之间会互相影响行号
        category="执行工具",
    ))
