"""文件读取与探索工具。

书 §5「上下文的精细化管理」给了两条具体要求，本模块逐条落实：

  1. **文件读取不应总是读全文。** 对大型文件，工具应支持按行号范围读取片段。
  2. **返回内容时附加行号标注，每行代码都以实际行号作为前缀。**
     书原话：「模型可精确引用『在 `src/main.py` 的第 42 行』，减少歧义
     并使后续编辑操作更可靠。」

另外，本模块实现了书 §5 搜索工具对比中的两类：
  · read_file  —— 读取已知位置
  · list_dir   —— 探索项目结构的第一步
  · glob       —— **文件名模式匹配**（只看路径不看内容，比内容搜索快得多）
"""

from __future__ import annotations

import fnmatch
import os
import posixpath
import re
from typing import Any, Dict, List, Optional

from ..errors import tool_error
from ..policy import Action, ActionKind
from ..protocol import ToolResult, truncate_output
from .registry import Tool, ToolRegistry


# ══════════════════════════════════════════════════════════════════
# 路径安全
# ══════════════════════════════════════════════════════════════════
def resolve_within(workspace: str, rel: str) -> str:
    """把相对路径解析成工作区内的绝对路径。

    **路径越界在这里就被拒绝**，不依赖 policy 层。理由：这是工具实现方的
    结构性保证（受信任代码），而不是需要模型配合的约定。
    书 §5 的安全一节把「路径越界」列为沙箱要防的第一件事。
    """
    ws = os.path.realpath(workspace)
    p = rel.strip()
    if not p:
        raise ValueError("路径为空")
    if os.path.isabs(p):
        raise ValueError(f"不接受绝对路径：{rel}（请给出相对于工作区的路径）")
    full = os.path.realpath(os.path.join(ws, p))
    if full != ws and not full.startswith(ws + os.sep):
        raise ValueError(f"路径越出工作区：{rel}")
    return full


def to_rel(workspace: str, full: str) -> str:
    ws = os.path.realpath(workspace)
    if os.path.realpath(full) == ws:
        return "."
    return os.path.relpath(full, ws).replace(os.sep, "/")


# ══════════════════════════════════════════════════════════════════
# read_file
# ══════════════════════════════════════════════════════════════════
MAX_READ_LINES = 2000


def read_file(workspace: str, args: Dict[str, Any]) -> ToolResult:
    """读取文件，**每行带真实行号前缀**。"""
    rel = args.get("path", "")
    try:
        full = resolve_within(workspace, rel)
    except ValueError as e:
        return tool_error("read_file", "bad_path", str(e))

    if not os.path.exists(full):
        return tool_error("read_file", "not_found", f"文件不存在：{rel}")
    if os.path.isdir(full):
        return tool_error("read_file", "is_dir", f"{rel} 是一个目录，不是文件。")

    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError as e:
        return tool_error("read_file", "io", f"打开 {rel} 失败：{e}")

    start = int(args.get("start_line") or 1)
    end = args.get("end_line")
    start = max(1, start)
    end = int(end) if end else min(len(lines), start + MAX_READ_LINES - 1)
    end = min(end, len(lines))

    if start > len(lines):
        return tool_error("read_file", "bad_range",
                          f"起始行 {start} 超出文件范围（该文件共 {len(lines)} 行）。")

    sel = lines[start - 1: end]
    # 行号宽度按最大行号对齐，模型看着更整齐、也更容易精确引用
    width = len(str(end))
    body = "\n".join(f"{str(i).rjust(width)}| {ln}" for i, ln in enumerate(sel, start=start))
    header = f"# {rel}  ({len(lines)} 行，显示 {start}-{end})"

    return ToolResult(
        ok=True,
        text=header + "\n" + body,
        side_effect=False,
        meta={"path": rel, "total_lines": len(lines), "start": start, "end": end},
    )


def describe_read(args: Dict[str, Any]) -> Action:
    return Action(kind=ActionKind.READ, paths=[], side_effect=False,
                  summary=f"read_file {args.get('path', '')}")


# ══════════════════════════════════════════════════════════════════
# list_dir
# ══════════════════════════════════════════════════════════════════
def list_dir(workspace: str, args: Dict[str, Any]) -> ToolResult:
    rel = args.get("path") or "."
    try:
        full = resolve_within(workspace, rel)
    except ValueError as e:
        return tool_error("list_dir", "bad_path", str(e))

    if not os.path.isdir(full):
        return tool_error("list_dir", "not_dir", f"{rel} 不是目录。")

    show_hidden = bool(args.get("show_hidden", False))
    try:
        entries = sorted(os.scandir(full), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError as e:
        return tool_error("list_dir", "io", f"无法列出目录 {rel}：{e}")

    lines = []
    n_dir = n_file = 0
    for e in entries:
        if not show_hidden and e.name.startswith("."):
            continue
        if e.is_dir():
            lines.append(f"{e.name}/")
            n_dir += 1
        else:
            try:
                size = e.stat().st_size
            except OSError:
                size = -1
            lines.append(f"{e.name}  ({size} B)" if size >= 0 else e.name)
            n_file += 1

    text = f"# {rel}  ({n_dir} 目录, {n_file} 文件)\n" + "\n".join(lines) if lines else f"# {rel}  (空目录)"
    return ToolResult(ok=True, text=text, side_effect=False,
                      meta={"path": rel, "dirs": n_dir, "files": n_file})


def describe_list(args: Dict[str, Any]) -> Action:
    return Action(kind=ActionKind.READ, paths=[], side_effect=False,
                  summary=f"list_dir {args.get('path') or '.'}")


# ══════════════════════════════════════════════════════════════════
# glob —— 文件名模式匹配
# ══════════════════════════════════════════════════════════════════
DEFAULT_IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
                       "dist", "build", ".next", "target", ".mypy_cache",
                       ".pytest_cache", "artifacts"}


def glob_files(workspace: str, args: Dict[str, Any]) -> ToolResult:
    """按文件名模式查找。

    书 §4 的工具描述纪律要求写清边界 —— 本工具的边界是：
    **只匹配文件名/路径，不搜索文件内容**。搜内容请用 grep。
    书原话：*大多数工具调用失败的根因不是模型不知道工具能做什么，
    而是不知道工具不能做什么。*
    """
    pattern = args.get("pattern", "").strip()
    if not pattern:
        return tool_error("glob", "bad_glob", "pattern 参数为空。")

    try:
        root = resolve_within(workspace, args.get("path") or ".")
    except ValueError as e:
        return tool_error("glob", "bad_path", str(e))

    limit = int(args.get("limit") or 200)
    pat = pattern.replace("\\", "/")
    hits: List[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_IGNORE_DIRS and not d.startswith(".")]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = to_rel(workspace, full)
            if _match_glob(pat, rel):
                hits.append(rel)
                if len(hits) >= limit:
                    break
        if len(hits) >= limit:
            break

    hits.sort()
    if not hits:
        return ToolResult(
            ok=True,
            text=f"没有文件匹配 `{pattern}`。（本工具只匹配文件名与路径，不搜索文件内容；"
                 "搜内容请用 grep。）",
            side_effect=False, meta={"count": 0, "matches": []},
        )
    more = "" if len(hits) < limit else f"\n… 已达上限 {limit}，结果可能不完整"
    return ToolResult(ok=True, text="\n".join(hits) + more, side_effect=False,
                      meta={"count": len(hits), "matches": list(hits)})


def _match_glob(pat: str, rel: str) -> bool:
    """支持 `**/` 跨目录匹配的 glob。

    fnmatch 的 `*` 会跨越 `/`，所以对 `**/*.py` 这类模式要特判。
    """
    if pat.startswith("**/"):
        tail = pat[3:]
        if fnmatch.fnmatch(rel, tail):
            return True
        # 任意深度
        parts = rel.split("/")
        for i in range(len(parts)):
            if fnmatch.fnmatch("/".join(parts[i:]), tail):
                return True
        return False
    if pat.endswith("/**"):
        prefix = pat[:-3]
        return rel == prefix.rstrip("/") or rel.startswith(prefix.rstrip("/") + "/")
    return fnmatch.fnmatch(rel, pat)


def describe_glob(args: Dict[str, Any]) -> Action:
    return Action(kind=ActionKind.READ, paths=[], side_effect=False,
                  summary=f"glob {args.get('pattern', '')}")


# ══════════════════════════════════════════════════════════════════
# 注册
# ══════════════════════════════════════════════════════════════════
def register_fs_tools(reg: ToolRegistry, workspace: str) -> None:
    reg.register(Tool(
        name="read_file",
        description=(
            "读取工作区内某个文件的内容。**返回的每一行都带真实行号前缀**"
            "（形如 `  42| code`），可直接用于引用与补丁定位。\n"
            "什么时候用：需要查看已知路径的文件内容时。\n"
            "边界：只读文件内容，不改动任何东西；只接受工作区内的**相对路径**，"
            "不接受绝对路径；一次最多返回 2000 行，大文件请用 start_line/end_line 分段读。\n"
            "**不要用它来做这些**：\n"
            "· 想确认某个路径下有哪些文件 → 用 list_dir（read_file 只能读文件，传目录会报错）\n"
            "· 知道文件名特征但不知道在哪 → 用 glob（比逐个目录试快得多）\n"
            "· 想找某个函数/字符串出现在哪些文件 → 用 grep（read_file 只读一个文件，"
            "要开几十个文件才能达到同样效果）\n"
            "· 想确认文件是否存在 → 直接 read_file 会报「文件不存在」并给你下一步，"
            "或用 glob 按名字查"
        ),
        properties={
            "path": {"type": "string", "description": "相对于工作区的路径，例如 `src/main.py`"},
            "start_line": {"type": "integer", "description": "起始行号（从 1 开始，含）。省略则从第 1 行开始"},
            "end_line": {"type": "integer", "description": "结束行号（含）。省略则到文件末尾或满 2000 行为止"},
        },
        required=["path"],
        examples=[
            {"path": "src/main.py"},
            {"path": "src/main.py", "start_line": 100, "end_line": 150},
        ],
        handler=lambda a: read_file(workspace, a),
        describe_action=describe_read,
        supports_parallel=True,     # 纯读，可并发
        category="读取工具",
    ))

    reg.register(Tool(
        name="list_dir",
        description=(
            "列出目录内容，目录名以 `/` 结尾并标注文件大小。\n"
            "什么时候用：探索项目结构、确认某个目录下有什么文件时，"
            "这是了解陌生代码库的第一步。\n"
            "边界：只列当前层，**不递归**；默认隐藏点开头的文件"
            "（需要时给 show_hidden=true）；不读取文件内容。\n"
            "**不要用它来做这些**：\n"
            "· 想看文件里写了什么 → 用 read_file（list_dir 只给名字和大小）\n"
            "· 想找深层嵌套的文件 → 用 glob `**/*.py`（list_dir 只列一层，"
            "逐层手工展开很慢且容易漏）\n"
            "· 想按内容找东西 → 用 grep"
        ),
        properties={
            "path": {"type": "string", "description": "相对路径，省略或 `.` 表示工作区根目录"},
            "show_hidden": {"type": "boolean", "description": "是否显示点开头的隐藏项，默认 false"},
        },
        examples=[{"path": "src"}, {"path": "."}],
        handler=lambda a: list_dir(workspace, a),
        describe_action=describe_list,
        supports_parallel=True,
        category="读取工具",
    ))

    reg.register(Tool(
        name="glob",
        description=(
            "按**文件名/路径模式**查找文件，支持 `**` 跨目录，例如 `**/*.py`、"
            "`src/**/test_*.py`。\n"
            "什么时候用：知道文件名特征（后缀、前缀、命名约定）但不知道具体位置时。"
            "比内容搜索快得多，因为它不打开文件。\n"
            "边界：**只匹配文件名与路径，完全不搜索文件内容** —— 自动跳过 .git/node_modules 等目录。\n"
            "**不要用它来做这些**：\n"
            "· 想按内容找代码（函数名、字符串、报错信息）→ 用 grep —— "
            "glob 只匹配文件名，`*.ts` 里的内容它一点也看不见\n"
            "· 想看某个已知路径的内容 → 用 read_file（glob 只给路径，不给内容）\n"
            "· 想确认某个具体路径存不存在 → 直接 read_file，它会告诉你\n"
            "· 模式写成了正则（如 `.*\\.py`）→ glob 用的是 shell 通配符，"
            "没有 `.*` 语义；跨目录用 `**`，例如 `**/*.py`"
        ),
        properties={
            "pattern": {"type": "string", "description": "glob 模式，例如 `**/*.py` 或 `src/**/*.ts`"},
            "path": {"type": "string", "description": "搜索起点，省略表示工作区根目录"},
            "limit": {"type": "integer", "description": "最多返回多少条，默认 200"},
        },
        required=["pattern"],
        examples=[{"pattern": "**/*.py"}, {"pattern": "src/**/*.ts", "limit": 50}],
        handler=lambda a: glob_files(workspace, a),
        describe_action=describe_glob,
        supports_parallel=True,
        category="搜索工具",
    ))
