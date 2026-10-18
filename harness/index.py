"""代码索引 —— 符号与词表倒排（**不是语义索引**）。

## 先说清楚这个索引**是什么**、**不是什么**

这是本项目一贯的做法：能力的边界要写在代码里，而不是靠用户猜。

**是**：
- **符号索引**：用正则从源码里提出函数 / 类 / 方法 / 常量定义及其行号。
  这让"找 `_run_completion_gate` 定义在哪"变成一次查表，而不是全库扫描。
- **词表倒排**：把每个文件的标识符（去重后）建成 `词 → 文件集`。
  这让"哪些文件提到了 `certificate`"变成一次集合查询。
- **文件清单**：路径、大小、行数、语言猜测。

**不是**：
- **不是语义索引**：没有向量、没有 embedding。搜"处理完成判定的那段逻辑"
  搜不到东西 —— 它只懂字面。
- **不是调用图**：知道 `foo` 定义在哪，但不知道谁调用了 `foo`。
  跨文件调用关系需要语法分析（AST）与跨文件解析，那是另一个量级的工作。
- **不保证跨语言正确**：符号提取用的是**按语言分组的正则**，
  不是真正的 parser。故意写得很保守（宁可少提取，不乱提取），
  因为"漏掉一个符号"只是找不到，而"错提一个"会让人以为某处有这个定义。

## 与 grep 的分工（为什么不用 grep 顶替）

grep 是**逐文件**匹配，复杂度 O(文件数 × 文件大小)。
索引是**一次建好、多次查询**。在 140 个文件的工作区上差别不大，
但这个差别会随规模放大；更重要的是**查询形状不同**：
grep 回答"哪一行含这个字符串"，索引回答"哪个文件定义了这个符号"。

## 为什么不做增量

增量索引需要文件监听 + 失效传播，而"索引过期"是最坏的一种状态：
它会让查询结果**看起来正确但实际是旧的**。这里用**显式的陈旧检测**
（记录建索引时的文件大小与 mtime，查询时比对），而不是悄悄增量 ——
对不上就明确报"索引已过期，请重建"，不返回可能过期的结果。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ══════════════════════════════════════════════════════════════════
# 语言识别
# ══════════════════════════════════════════════════════════════════
# 只认少数几种、且**只认扩展名**。不做内容嗅探（shebang 之类的启发式
# 会在无扩展名文件上乱猜，而猜错语言会让符号提取全军覆没）。
_LANG_BY_EXT = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".jsx": "jsx",
    ".json": "json", ".md": "markdown", ".css": "css",
    ".html": "html", ".htm": "html",
    ".yml": "yaml", ".yaml": "yaml", ".toml": "toml",
    ".sh": "shell", ".bat": "batch", ".ps1": "powershell",
    ".java": "java", ".go": "go", ".rs": "rust",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
    ".rb": "ruby", ".php": "php", ".sql": "sql",
}

# 不索引的目录。这里有**两类**，理由不同，值得分开写：
#
# 1. 构建产物 / 依赖（node_modules / dist / __pycache__ …）
#    —— 它们不是源码，索引它们会淹没真正的代码。
# 2. **本工具自己的运行时目录**（.crucible / .workbuddy）
#    —— 这不是"源码"，而是 harness 的落盘记录与开发探针产物。
#
# 第 2 类实测踩得很惨：早期只排除了 `.crucible` 而漏了 `.workbuddy`，
# 于是 3000 个文件的配额被探针产物吃满（里面 2956 个是 JSON），
# 索引 34MB、`harness/*.py` 一个都没进去、符号提取全部落空。
# **症状看起来像"索引功能坏了"，实际是索引范围没界定。**
# 这说明"排除清单"必须包含自己产生的东西 —— 否则工具会索引自己。
_SKIP_DIRS = {".git", "node_modules", ".next", "dist", "build", ".venv",
              "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
              ".idea", ".vscode",
              ".crucible", ".workbuddy", ".cache", "coverage",
              ".tox", ".eggs", "site-packages"}

_TEXT_EXT = set(_LANG_BY_EXT)


def lang_of(path: str) -> str:
    return _LANG_BY_EXT.get(os.path.splitext(path)[1].lower(), "")


# ══════════════════════════════════════════════════════════════════
# 符号提取：按语言分组的保守正则
# ══════════════════════════════════════════════════════════════════
# 设计原则：**宁可漏，不可错**。
# 错提一个符号会让"这个定义在这里"变成假信息，而漏一个只是找不到。
# 所以只匹配**顶层缩进明确**的形态，且要求行首（可含缩进）就是定义关键字。
@dataclass
class SymbolRule:
    """一条符号提取规则。

    `group` 指明**名字在第几个捕获组** —— 这一点必须显式写出来。
    早期实现靠"这条规则是不是列表第 4 项"来判断，那是**依赖列表下标**：
    往规则表里插一条新规则就会让判据错位，而症状是"名字提取成了缩进"，
    很难联想到是下标问题。
    """
    kind: str
    rx: "re.Pattern[str]"
    group: int = 2          # 默认：第 1 组是缩进，第 2 组是名字


_PY_RULES = [
    SymbolRule("class", re.compile(r"^(\s*)class\s+([A-Za-z_]\w*)")),
    SymbolRule("def", re.compile(r"^(\s*)def\s+([A-Za-z_]\w*)")),
    SymbolRule("def", re.compile(r"^(\s*)async\s+def\s+([A-Za-z_]\w*)")),
    # 模块级常量：全大写 = 约定俗成的常量形态
    SymbolRule("const", re.compile(r"^([A-Z_][A-Z0-9_]{2,})\s*(?::[^=]+)?="), group=1),
]

_JS_RULES = [
    SymbolRule("class", re.compile(r"^(\s*)class\s+([A-Za-z_$]\w*)")),
    SymbolRule("function", re.compile(r"^(\s*)function\s+([A-Za-z_$]\w*)")),
    SymbolRule("function", re.compile(r"^(\s*)(?:const|let|var)\s+([A-Za-z_$]\w*)\s*=\s*(?:async\s*)?(?:function|\()")),
    SymbolRule("const", re.compile(r"^(\s*)(?:const|let|var)\s+([A-Z_][A-Z0-9_]{2,})\s*=")),
    # 对象方法简写（`foo() {`）—— 只在缩进 ≥2 时算，避免把普通调用行当定义
    SymbolRule("method", re.compile(r"^(\s{2,})([A-Za-z_$]\w*)\s*\([^)]*\)\s*\{")),
]

_GO_RULES = [
    SymbolRule("func", re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"), group=1),
    SymbolRule("type", re.compile(r"^type\s+([A-Za-z_]\w*)"), group=1),
]

_RUST_RULES = [
    SymbolRule("fn", re.compile(r"^(\s*)(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)")),
    SymbolRule("struct", re.compile(r"^(\s*)(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_]\w*)")),
]

_JAVA_RULES = [
    SymbolRule("class", re.compile(r"^(\s*)(?:public|private|protected|abstract|final|\s)*\s*(?:class|interface|enum)\s+([A-Za-z_]\w*)")),
    SymbolRule("method", re.compile(r"^(\s{2,})(?:public|private|protected|static|final|synchronized|\s)*\s*[A-Za-z_<>\[\],\s]+\s+([A-Za-z_]\w*)\s*\(")),
]

_SHELL_RULES = [
    SymbolRule("func", re.compile(r"^(?:function\s+)?([A-Za-z_]\w*)\s*\(\s*\)\s*\{"), group=1),
]

_RULES_BY_LANG: Dict[str, List[SymbolRule]] = {
    "python": _PY_RULES,
    "javascript": _JS_RULES,
    "typescript": _JS_RULES,
    "tsx": _JS_RULES,
    "jsx": _JS_RULES,
    "go": _GO_RULES,
    "rust": _RUST_RULES,
    "java": _JAVA_RULES,
    "shell": _SHELL_RULES,
}

# 标识符词表：只取"像标识符"的串。排除过于常见的关键字与单字符，
# 否则倒排表会退化成"每个文件都命中"。
_IDENT_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
_STOP_IDENTS = {
    # 各语言关键字/内置：出现频次高且无区分度
    "self", "none", "true", "false", "this", "return", "import",
    "from", "class", "def", "function", "const", "let", "var", "async",
    "await", "with", "while", "else", "elif", "try", "except", "finally",
    "raise", "print", "string", "number", "boolean", "object", "array",
    "value", "values", "items", "index", "length", "result", "results",
    "data", "name", "names", "text", "path", "paths", "file", "files",
    "test", "tests", "type", "types", "json", "html", "http", "https",
    "span", "div", "style", "para", "args", "kwargs", "cls", "assert",
    "lambda", "global", "nonlocal", "yield", "pass", "break", "continue",
    # 英文虚词（出现在注释与字符串里，会污染倒排表）
    "the", "and", "not", "but", "its", "you", "your", "this", "that",
    "for", "are", "was", "were", "which", "when", "then", "than",
}


# ══════════════════════════════════════════════════════════════════
# 索引
# ══════════════════════════════════════════════════════════════════
@dataclass
class Symbol:
    name: str
    kind: str
    path: str
    line: int
    indent: int = 0

    def to_json(self) -> Dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "path": self.path,
                "line": self.line}


@dataclass
class FileEntry:
    path: str
    size: int
    mtime: float
    lines: int
    lang: str
    symbols: List[Symbol] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {"path": self.path, "size": self.size, "lines": self.lines,
                "lang": self.lang, "symbols": [s.to_json() for s in self.symbols]}


@dataclass
class CodeIndex:
    """一份建好的索引。

    `stamp` 记录建索引时刻的文件指纹（路径 → (size, mtime)）——
    查询前比对它，对不上就报"已过期"而不是返回可能过期的结果。
    """

    workspace: str = ""
    built_at: float = 0.0
    files: Dict[str, FileEntry] = field(default_factory=dict)
    symbols: Dict[str, List[Symbol]] = field(default_factory=dict)
    words: Dict[str, List[str]] = field(default_factory=dict)
    stamp: Dict[str, Tuple[int, float]] = field(default_factory=dict)
    truncated: bool = False
    errors: List[str] = field(default_factory=list)

    def to_json(self, with_symbols: bool = True) -> Dict[str, Any]:
        langs: Dict[str, int] = {}
        for fe in self.files.values():
            key = fe.lang or "(未知)"
            langs[key] = langs.get(key, 0) + 1
        top = sorted(langs.items(), key=lambda kv: -kv[1])[:10]
        return {
            "workspace": self.workspace,
            "built_at": self.built_at,
            "files": len(self.files),
            "symbols": sum(len(v) for v in self.symbols.values()),
            "unique_symbols": len(self.symbols),
            "words": len(self.words),
            "languages": dict(top),
            "truncated": self.truncated,
            "errors": self.errors[:10],
            "files_detail": ([fe.to_json() for fe in self.files.values()]
                             if with_symbols else None),
        }


def _read_text(path: str, limit: int = 1024 * 1024) -> Optional[str]:
    """读文本文件。与 `_read_workspace_file` 同一套编码策略。"""
    try:
        with open(path, "rb") as f:
            raw = f.read(limit)
    except OSError:
        return None
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def extract_symbols(text: str, path: str, lang: str) -> List[Symbol]:
    """从一份源码里提取符号。

    **只扫描行首形态**，不做多行拼接 —— 跨行定义（如装饰器包裹的函数）
    仍能被 `def` 行命中，而把多行拼起来会引入"这个符号属于哪个文件"
    的歧义（拼接窗口跨了文件边界就会错）。
    """
    rules = _RULES_BY_LANG.get(lang)
    if not rules:
        return []
    out: List[Symbol] = []
    seen = set()
    for i, line in enumerate(text.split("\n"), start=1):
        if len(line) > 400:      # 超长行（压缩过的 js）跳过，避免正则回溯
            continue
        for rule in rules:
            m = rule.rx.match(line)
            if not m:
                continue
            groups = m.groups()
            gi = rule.group - 1
            name = groups[gi] if 0 <= gi < len(groups) else ""
            name = (name or "").strip()
            if not name or len(name) < 2:
                continue
            indent = len(groups[0]) if groups and groups[0] and groups[0].strip() == "" else 0
            key = (name, i)
            if key in seen:
                continue
            seen.add(key)
            out.append(Symbol(name=name, kind=rule.kind, path=path, line=i,
                              indent=indent))
            break                # 一行只认第一个匹配，避免同一行多个规则重复计
    return out


def build_index(workspace: str, max_files: int = 8000,
                max_lines_per_file: int = 20000) -> CodeIndex:
    """扫描工作区建索引。

    两步：① 收集文件清单与指纹；② 逐文件提取符号与词表。

    为什么分开两步：第一步很快（只 stat），第二步要读内容。
    分开后可以在第一步之后就知道"要读哪些文件"，也让指纹在第二步
    开始前就固定下来 —— 否则建索引期间的改动会让指纹与内容不一致。
    """
    ws = os.path.realpath(workspace)
    idx = CodeIndex(workspace=ws, built_at=time.time())

    # ① 文件清单 + 指纹
    candidates: List[str] = []
    for root, subdirs, filenames in os.walk(ws):
        subdirs[:] = [d for d in subdirs if d not in _SKIP_DIRS]
        for fn in filenames:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, ws).replace("\\", "/")
            ext = os.path.splitext(fn)[1].lower()
            if ext not in _TEXT_EXT and ext != "":
                continue             # 非文本类型不入索引
            try:
                st = os.stat(full)
            except OSError:
                continue
            idx.stamp[rel] = (st.st_size, st.st_mtime)
            candidates.append(rel)
            if len(candidates) >= max_files:
                idx.truncated = True
                break
        if idx.truncated:
            break

    # ② 逐文件提取
    for rel in sorted(candidates):
        full = os.path.join(ws, rel)
        text = _read_text(full)
        if text is None:
            idx.errors.append(f"read failed: {rel}")
            continue
        lines = text.split("\n")
        if len(lines) > max_lines_per_file:
            text = "\n".join(lines[:max_lines_per_file])
            idx.errors.append(f"truncated: {rel}（超过 {max_lines_per_file} 行）")
            lines = text.split("\n")

        lang = lang_of(rel)
        syms = extract_symbols(text, rel, lang)
        idx.files[rel] = FileEntry(path=rel, size=os.path.getsize(full),
                                   mtime=idx.stamp[rel][1], lines=len(lines),
                                   lang=lang, symbols=syms)
        for s in syms:
            idx.symbols.setdefault(s.name, []).append(s)

        # 词表：去重后的标识符集合
        words = {w for w in _IDENT_RX.findall(text) if w.lower() not in _STOP_IDENTS}
        for w in words:
            idx.words.setdefault(w, []).append(rel)

    return idx


# ══════════════════════════════════════════════════════════════════
# 陈旧检测
# ══════════════════════════════════════════════════════════════════
def stale_report(idx: CodeIndex, workspace: str) -> Dict[str, Any]:
    """比对指纹，报告索引有多旧。

    ## 为什么要"报差距"而不是"自动重建"

    自动重建看起来更省事，但它会让**查询结果悄悄变化** ——
    用户同一分钟里查两次，第二次结果不同，而他不知道是因为索引刚被重建。
    显式报出"哪些文件变了"，用户自己决定要不要重建。

    只比对 size 与 mtime：这是**廉价且足够**的判据。
    更严的判据（内容哈希）要对每个文件做一次全读，在建索引时已经读过一次了，
    再读一次等于把成本翻倍 —— 而它换来的是"检测到 mtime 精度导致的漏报"，
    那种情况在实际使用中很少见（通常 mtime 精度是毫秒级）。
    """
    ws = os.path.realpath(workspace)
    if ws != idx.workspace:
        return {"stale": True, "reason": "workspace_changed",
                "detail": f"索引建自 {idx.workspace}，现在的工作区是 {ws}。"}
    added, changed, removed = [], [], []
    current = set()
    for rel, (size, mtime) in idx.stamp.items():
        full = os.path.join(ws, rel)
        try:
            st = os.stat(full)
        except OSError:
            removed.append(rel)
            continue
        current.add(rel)
        if st.st_size != size or abs(st.st_mtime - mtime) > 1e-6:
            changed.append(rel)
    # 新出现的文件：扫一层目录即可，不做全量（全量等于重建）
    for root, subdirs, filenames in os.walk(ws):
        subdirs[:] = [d for d in subdirs if d not in _SKIP_DIRS]
        for fn in filenames:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, ws).replace("\\", "/")
            if rel not in current and os.path.splitext(fn)[1].lower() in _TEXT_EXT:
                added.append(rel)
    stale = bool(added or changed or removed)
    return {
        "stale": stale,
        "added": added[:20],
        "changed": changed[:20],
        "removed": removed[:20],
        "counts": {"added": len(added), "changed": len(changed),
                   "removed": len(removed)},
        "indexed_at": idx.built_at,
        "detail": ("索引已过期：工作区有变化。查询结果可能不含最新文件 —— "
                   "请重建索引。" if stale else "索引与工作区一致。"),
    }


# ══════════════════════════════════════════════════════════════════
# 查询
# ══════════════════════════════════════════════════════════════════
def query_symbols(idx: CodeIndex, name: str, exact: bool = False,
                  limit: int = 100) -> List[Symbol]:
    """按符号名查询。

    `exact=False` 时做**前缀匹配**（不是子串）—— 子串匹配会让 `get`
    命中 `target`、`budget`、`forget` 等几十个无关符号，
    而"找定义"这个场景下前缀是更符合直觉的判据。
    """
    low = (name or "").lower()
    if not low:
        return []
    out: List[Symbol] = []
    if exact:
        out = list(idx.symbols.get(name, []))
    else:
        for sym_name, syms in idx.symbols.items():
            if sym_name.lower().startswith(low):
                out.extend(syms)
            if len(out) >= limit * 2:
                break
    out.sort(key=lambda s: (s.path, s.line))
    return out[:limit]


def query_files(idx: CodeIndex, word: str, limit: int = 100) -> List[str]:
    """按标识符查文件（倒排表查询）。"""
    return sorted(idx.words.get(word, []))[:limit]


def search_index(idx: CodeIndex, q: str, limit: int = 60) -> Dict[str, Any]:
    """统一查询入口：先当符号查，再当普通词查。

    返回**分开的两组**而不是混成一个列表：
    "这个文件里定义了这个符号"与"这个文件里提到了这个词"是
    两种不同强度的证据，混在一起会让人以为搜索结果都是"定义"。
    """
    syms = query_symbols(idx, q, limit=limit)
    files = query_files(idx, q, limit=limit)
    if not files and syms:
        files = sorted({s.path for s in syms})
    return {
        "query": q,
        "symbols": [s.to_json() for s in syms],
        "files": files,
        "note": "symbols 是**定义**位置（强证据）；files 是**提到**该词的文件"
                "（弱证据）。两者性质不同，不要混用。",
    }


# ══════════════════════════════════════════════════════════════════
# 落盘 / 载入
# ══════════════════════════════════════════════════════════════════
def save_index(idx: CodeIndex, path: str) -> None:
    payload = {
        "workspace": idx.workspace,
        "built_at": idx.built_at,
        "truncated": idx.truncated,
        "errors": idx.errors[:50],
        "files": {k: v.to_json() for k, v in idx.files.items()},
        "symbols": {k: [s.to_json() for s in v] for k, v in idx.symbols.items()},
        "words": idx.words,
        "stamp": {k: [v[0], v[1]] for k, v in idx.stamp.items()},
    }
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def load_index(path: str) -> Optional[CodeIndex]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    idx = CodeIndex(workspace=payload.get("workspace", ""),
                    built_at=payload.get("built_at", 0.0),
                    truncated=payload.get("truncated", False),
                    errors=payload.get("errors", []))
    for rel, fe in (payload.get("files") or {}).items():
        syms = [Symbol(name=s["name"], kind=s["kind"], path=s["path"],
                       line=s["line"]) for s in (fe.get("symbols") or [])]
        idx.files[rel] = FileEntry(path=rel, size=fe.get("size", 0),
                                   mtime=fe.get("mtime", 0.0),
                                   lines=fe.get("lines", 0),
                                   lang=fe.get("lang", ""), symbols=syms)
    for name, syms in (payload.get("symbols") or {}).items():
        idx.symbols[name] = [Symbol(name=s["name"], kind=s["kind"],
                                    path=s["path"], line=s["line"]) for s in syms]
    idx.words = payload.get("words") or {}
    idx.stamp = {k: (v[0], v[1]) for k, v in (payload.get("stamp") or {}).items()}
    return idx
