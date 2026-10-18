"""代码内容搜索。

书 §5「Coding Agent 中的搜索工具」给出了四种方式的对比，并明确了一条路线选择：

> 以 Claude Code 为代表的终端型 Agent **刻意不建嵌入索引**，纯靠 agentic 的
> grep + glob 现场检索 —— 这样既不必维护随代码演化而不断陈旧的索引，
> 也省掉了一整套索引基础设施。Cursor 这类 IDE 型工具早期走相反路线，
> **目前 Cursor 等 IDE 也改用 grep + glob 现场检索了**。

本项目跟随这条主流路线：**纯 Python 实现的正则内容搜索，不建索引、不依赖 rg/grep**。
理由：
  · 不引入索引陈旧问题（索引随代码演化失效，且需要额外基础设施）
  · 零外部依赖（对齐本项目「只用标准库」的纪律）
  · 屏蔽平台差异（书 §4 指出 grep/find 在 Mac/Win/Linux 语法不同，
    专用工具能给出更清晰的行号反馈）
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from ..errors import tool_error
from ..policy import Action, ActionKind
from ..protocol import ToolResult
from .fs import DEFAULT_IGNORE_DIRS, resolve_within, to_rel
from .registry import Tool, ToolRegistry

MAX_HITS = 500
MAX_FILE_BYTES = 4 * 1024 * 1024     # 跳过超大文件，避免拖垮搜索


def grep(workspace: str, args: Dict[str, Any]) -> ToolResult:
    pattern = args.get("pattern", "")
    if not pattern:
        return tool_error("grep", "bad_args", "pattern 参数为空。")

    flags = 0
    if args.get("case_insensitive"):
        flags |= re.IGNORECASE
    if args.get("multiline"):
        flags |= re.DOTALL
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        # 书 §5：错误变成模型的输入，且要**附上输入约束提示**
        return tool_error("grep", "bad_regex",
                          f"正则表达式无法编译：{e}")

    try:
        root = resolve_within(workspace, args.get("path") or ".")
    except ValueError as e:
        return tool_error("grep", "bad_path", str(e))

    glob_pat = args.get("glob") or ""          # 只搜匹配的文件
    ctx_before = int(args.get("before") or 0)
    ctx_after = int(args.get("after") or 0)
    limit = int(args.get("limit") or MAX_HITS)

    files = _collect(root, workspace, glob_pat)
    hits: List[str] = []
    n_files = 0
    hit_files: List[str] = []          # 结构化：命中的文件清单（供证据绑定）

    for full in files:
        try:
            if os.path.getsize(full) > MAX_FILE_BYTES:
                continue
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            continue

        matched_here = False
        if args.get("multiline"):
            text = "\n".join(lines)
            for m in rx.finditer(text):
                ln = text[:m.start()].count("\n") + 1
                if not matched_here:
                    hits.append(f"\n{to_rel(workspace, full)}")
                    matched_here = True
                snippet = m.group(0).replace("\n", "\\n")[:200]
                hits.append(f"{ln}: {snippet}")
                if len(hits) >= limit:
                    break
        else:
            for i, ln in enumerate(lines, start=1):
                if rx.search(ln):
                    if not matched_here:
                        hits.append(f"\n{to_rel(workspace, full)}")
                        matched_here = True
                    if ctx_before or ctx_after:
                        lo = max(1, i - ctx_before)
                        hi = min(len(lines), i + ctx_after)
                        for j in range(lo, hi + 1):
                            mark = ":" if j == i else "-"
                            hits.append(f"{j}{mark} {lines[j - 1]}")
                    else:
                        hits.append(f"{i}: {ln}")
                    if len(hits) >= limit:
                        break
        if matched_here:
            n_files += 1
            hit_files.append(to_rel(workspace, full))
        if len(hits) >= limit:
            break

    if not hits:
        return ToolResult(
            ok=True,
            text=f"没有匹配 `{pattern}`。\n"
                 "（本工具只搜索文件**内容**；如果是要按文件名找文件，请用 glob。）",
            side_effect=False, meta={"hits": 0, "files": 0, "matches": []},
        )

    head = f"# grep `{pattern}` —— {len(hits)} 处命中，涉及 {n_files} 个文件"
    tail = "" if len(hits) < limit else f"\n… 已达上限 {limit}，可缩小范围或加 glob 过滤"
    return ToolResult(ok=True, text=head + "\n" + "\n".join(hits) + tail,
                      side_effect=False,
                      meta={"hits": len(hits), "files": n_files,
                            "matches": hit_files})


def _collect(root: str, workspace: str, glob_pat: str) -> List[str]:
    out: List[str] = []
    if os.path.isfile(root):
        return [root]
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in DEFAULT_IGNORE_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("."):
                continue
            full = os.path.join(dirpath, fn)
            if glob_pat:
                from .fs import _match_glob
                if not _match_glob(glob_pat, to_rel(workspace, full)):
                    continue
            out.append(full)
    out.sort()
    return out


def describe_grep(args: Dict[str, Any]) -> Action:
    return Action(kind=ActionKind.READ, paths=[], side_effect=False,
                  summary=f"grep {args.get('pattern', '')}")


def register_search_tools(reg: ToolRegistry, workspace: str) -> None:
    reg.register(Tool(
        name="grep",
        description=(
            "用正则表达式搜索工作区内文件的**内容**，返回 `文件路径` 与 `行号: 内容`。\n"
            "什么时候用：要找某个函数名、变量名、错误消息、字符串字面量在哪出现时。"
            "支持完整 Python 正则语法。\n"
            "边界：**只搜索文本内容，不匹配文件名**；"
            "自动跳过 .git/node_modules 等目录与二进制/超大文件；"
            "一次最多返回 500 处命中，可用 glob 参数限定只搜某类文件。\n"
            "**不要用它来做这些**：\n"
            "· 想按**文件名**找文件 → 用 glob。grep 会逐个打开并扫描文件内容，慢得多，"
            "而且它从来不匹配文件名\n"
            "· 只想看某个已知文件的内容 → 用 read_file。grep 只给匹配行，"
            "没有行号上下文之外的完整内容\n"
            "· pattern 里想用通配符 `*` → grep 用的是 **Python 正则**，"
            "`*` 是「前一个字符重复」而不是「任意字符」；要跨任意字符用 `.*`，"
            "只做字面量匹配就把特殊字符转义\n"
            "· 目标文件很大（>4MB）→ grep 会跳过它，改用 read_file 分段读"
        ),
        properties={
            "pattern": {"type": "string", "description": "正则表达式，例如 `def handle_.*|class \\w+Handler`"},
            "path": {"type": "string", "description": "搜索起点，省略表示工作区根目录"},
            "glob": {"type": "string", "description": "只搜索匹配该模式的文件，例如 `**/*.py`"},
            "case_insensitive": {"type": "boolean", "description": "是否忽略大小写，默认 false"},
            "multiline": {"type": "boolean", "description": "是否跨行匹配，默认 false"},
            "before": {"type": "integer", "description": "命中行之前额外显示几行上下文"},
            "after": {"type": "integer", "description": "命中行之后额外显示几行上下文"},
            "limit": {"type": "integer", "description": "最多返回多少处命中，默认 500"},
        },
        required=["pattern"],
        examples=[
            {"pattern": "def nextDelay", "glob": "**/*.ts"},
            {"pattern": "TODO|FIXME", "case_insensitive": True, "limit": 50},
            {"pattern": "class \\w+Handler", "glob": "**/*.py", "after": 3},
        ],
        handler=lambda a: grep(workspace, a),
        describe_action=describe_grep,
        supports_parallel=True,
        category="搜索工具",
    ))
