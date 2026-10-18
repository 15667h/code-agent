"""意图判据的**单一来源**。

## 为什么必须抽出来

审计发现同一个概念在四个模块里各有一份副本，而且**内容已经漂移**：

| 判据 | 原先的副本 | 实测差异 |
|---|---|---|
| 写意图词 | `preflight` 41 个 / `verify` 22 个 | **互相缺词**：verify 缺 `实现/修复/refactor/fix` 等 20 个；preflight 缺 `保存/补丁/输出到` 等 7 个 |
| 验证命令 | `preflight` / `capability` | 逐字重复（26 个），只是没人保证它们一直一致 |
| 只读意图 | `preflight` / `capability` | capability 多 4 个（`不要写入`/`不要删`/`don't write`…） |
| 路径正则 | `preflight` / `verify` / `capability` | 三份逐字相同 |

后果不是"代码难看"，而是**同一轨迹会得到矛盾结论**：
任务「修好这个 bug」，preflight 判为写任务（`修好` 在它的表里），
而 verify 的 `_WRITE_INTENT` 里没有 `修好`/`修复`/`implement`/`fix` ——
于是"目标确实要求改动"这件事在两处得出不同答案。
本项目已经因为这类漂移踩过（evaluate 与 preflight 判据不一致导致 10 个假警报），
这次是在审计里主动找出来的。

## 判据该宽还是该窄

**宽**。理由是两条判据的误判代价不对称：
  · 判成"写任务"而其实不是 → 多问模型一句 / 多记一条提示（可见、可恢复）
  · 判成"不是写任务"而其实是 → 闸门放行一个假完成（不可见、后果严重）

所以取并集，并把每条的来源标出来，便于将来有人质疑时能追溯。

## 为什么不做成"运行时合并再比对"

可以写一个函数每次求并集，但那会让"用哪张表"变成隐式的。
显式列出一份、其余模块导入，才是可控的 —— 只有一处需要维护。
"""

from __future__ import annotations

import re
from typing import List

# ══════════════════════════════════════════════════════════════════
# 写意图：任务要求改动工作区的动词
# ══════════════════════════════════════════════════════════════════
# 来源合并：
#   · preflight._WRITE_INTENT（41 个，含单字「改」——中文里最核心的改动动词）
#   · verify._WRITE_INTENT（补 `保存` / `补丁` / `输出到` / `save` / `produce`）
#   · capability 与 certificate 用它判断"任务是否要求写"
WRITE_INTENT = (
    # 中文动词
    "实现", "新建", "创建", "添加", "修改", "改动", "重构", "修复", "修好",
    "删除", "写一个", "写个", "生成", "补全", "替换", "重命名", "移动", "加上",
    # 单字与常见变体。别漏这些 —— 中文里「改」是最核心的动词，
    # 而实测最常见的说法就是「把 X 改成 Y」这种形态。
    # 早期版本只收了「修改/改动」，导致「改成」「改 a.py」全部漏检。
    "改", "换成", "变成", "去掉", "干掉", "注释掉", "加上去",
    # 来自 verify 的补充（它原本单独维护，与上一组互相缺词）
    "写入", "保存", "补丁", "输出到",
    # 英文
    "implement", "create", "add", "modify", "fix", "refactor", "write",
    "delete", "remove", "rename", "generate", "build", "change", "update",
    "replace", "patch", "save", "produce", "edit",
)

# ══════════════════════════════════════════════════════════════════
# 只读意图：用户明确要求"不要改动"
# ══════════════════════════════════════════════════════════════════
# 取 preflight 与 capability 两份的并集
READONLY_INTENT = (
    "不要改", "别改", "不要动", "别动", "只读", "不要修改", "别修改",
    "不要创建", "不要新建", "不用改", "无需改",
    # 来自 capability 的补充
    "不要写入", "不要删",
    "don't modify", "do not modify", "don't change", "read only", "readonly",
    "no changes", "without modifying",
    "do not write", "don't write",
)

# ══════════════════════════════════════════════════════════════════
# 验证类命令：只有明确是验证/构建动作才算，不做语义猜测
# ══════════════════════════════════════════════════════════════════
VERIFY_CMDS = (
    "pytest", "unittest", "py_compile", "npm test", "npm run test",
    "npm run build", "npm run lint", "yarn test", "cargo test",
    "cargo build", "go test", "go build", "mvn test", "mvn package",
    "gradle test", "make", "make test", "tsc", "eslint", "flake8",
    "ruff", "mypy", "jest", "vitest", "dotnet test", "dotnet build",
)

# ══════════════════════════════════════════════════════════════════
# 路径样 token
# ══════════════════════════════════════════════════════════════════
# 三处原本逐字相同，现在只留一份。
# 刻意宽松：`a.py` / `src/a.py` / `*.ts` 都该被认出来 ——
# 这里的用途是"从任务文本里提取可能的文件名"，不是校验路径合法性。
PATH_RE = re.compile(
    r"(?:[A-Za-z0-9_.\-]*/)+[A-Za-z0-9_.\-*]+"
    r"|[A-Za-z0-9_\-*]+\.[A-Za-z0-9]{1,8}\b"
)

# 常见源码/文本后缀。
#
# ## 为什么需要这张表（SWE-bench Pro 基准跑出来的真实缺陷）
#
# `PATH_RE` 的第二支 `[A-Za-z0-9_\-*]+\.[A-Za-z0-9]{1,8}` 会把
# **点分模块名**与**MIME 类型**也当成文件名：
#   · `ansible.module_utils.urls`（Python 模块路径）
#   · `text/plain`（不匹配，含 /，走第一支 → 被当成路径）
# 于是一个说"从 `ansible.module_utils.urls` 导入"的任务，
# preflight 会认为"你点名了文件 `module_utils.urls` 却没动它" → **误拦**。
#
# 实测：6 道真实题里有 1 道因此被误拦（honest 用例）。
# 这不是基准的问题 —— 真实任务里同样常见（"改 lib/foo/bar.py 里从
# `pkg.sub.mod` 导入的那个函数"）。
#
# 判据：扩展名必须在**已知源码后缀**里。不认识就当模块名，
# 不猜。宁可漏掉一个真文件（退回"没点名"，只是少一道检查），
# 也不要把模块名当成文件（会产生误拦）。
CODE_EXTS = frozenset("""
py pyi pyx js jsx ts tsx mjs cjs vue svelte astro
go rs java kt kts scala rb php c h cc cpp cxx hpp hh cs swift m mm
sh bash zsh fish ps1 bat cmd
json yaml yml toml ini cfg conf env xml html htm css scss sass less styl
md markdown rst txt adoc sql proto graphql gql
lock gradle cmake mk patch diff tpl j2 jinja
""".split())

# MIME 类型与常见"像路径但不是文件"的形态
_NON_FILE_PREFIXES = ("text/", "application/", "image/", "audio/", "video/",
                      "multipart/", "font/", "model/")


def is_probable_file(token: str) -> bool:
    """这个 token 看起来是**仓库里的文件**，还是模块名 / MIME / 目录？

    `intent` 之外的两个消费者都关心这件事：
      · `preflight.named_path_untouched` —— 只对真文件要求"必须被触碰"
      · `certificate` 的产物比对 —— 只对真文件做一致性检查
    所以判据放在这里，不各自实现。
    """
    tok = (token or "").strip()
    if not tok:
        return False
    low = tok.lower()
    if low.startswith("/"):
        return False                      # 绝对路径 → 系统路径，不是仓库文件
    if any(low.startswith(p) for p in _NON_FILE_PREFIXES):
        return False                      # MIME 类型
    base = low.rsplit("/", 1)[-1]
    if "." not in base:
        return False                      # 无扩展名 → 目录名或标识符
    ext = base.rsplit(".", 1)[-1]
    return ext in CODE_EXTS


# ══════════════════════════════════════════════════════════════════
# 查询函数（判据的**唯一**使用方式）
# ══════════════════════════════════════════════════════════════════
def wants_write(text: str) -> bool:
    """这段文本**字面上**是否出现"要改动工作区"的动词。

    ## 这是一个**低层**判据，不是最终结论

    它只做词面命中，**不排除提问形态、也不排除"不要改"**。
    所以「这个项目改用的是什么框架？」会返回 True（含「改」），
    「不要修改任何文件」也会返回 True（含「修改」）—— 这是设计如此。

    要问"这条任务是不是写任务"，请用 `preflight.looks_like_edit_task()`：
    它在调用本函数之后，还会用 `_QUESTION_SHAPE` 排掉提问、
    用 `wants_readonly()` 排掉"明确要求别改"。

    为什么坚持分层而不是把护栏塞进来：那两道护栏各自有明确语义，
    塞进来会让本函数既难解释又难单独测试；而且 verify / capability
    需要的是"这个词出现了没有"这种更粗的信号，不是最终判定。
    """
    low = (text or "").lower()
    return any(k in low for k in WRITE_INTENT)


def wants_readonly(text: str) -> bool:
    """这段文本是否明确要求"不要改动"。"""
    low = (text or "").lower()
    return any(k in low for k in READONLY_INTENT)


def is_verify_command(command: str) -> bool:
    """这条命令是否属于验证/构建类。"""
    low = (command or "").lower()
    return bool(low) and any(v in low for v in VERIFY_CMDS)


def mentioned_paths(text: str) -> List[str]:
    """从文本里提取路径样 token（去重、保序）。

    去重保序是刻意的：调用方常直接把它们展示给人看，
    重复的路径会让输出变长而没有信息增量。

    **不做 `is_probable_file` 过滤** —— 有些调用方需要看到所有
    路径样 token（比如只做展示）。要"只要真文件"的，自己再过一道
    `is_probable_file`（`preflight` 与 `certificate` 都这么做）。
    """
    out: List[str] = []
    for m in PATH_RE.finditer(text or ""):
        tok = m.group(0).strip("`《》\"'(),;，。：:！!？?")
        if tok and tok not in out:
            out.append(tok)
    return out


def mentioned_files(text: str) -> List[str]:
    """只要**看起来是仓库文件**的路径。

    这是 `preflight` / `certificate` 该用的那个：它们要基于
    "任务点名了哪些文件"做判定，模块名与 MIME 混进来会造成误拦。
    """
    return [p for p in mentioned_paths(text) if is_probable_file(p)]


def path_covers(pattern: str, actual: str) -> bool:
    """写入路径 `actual` 是否覆盖了任务点名的路径 `pattern`。

    ## 为什么必须容忍"名字不完全相等"

    实测踩到：任务说改 `a.py`，agent 写的是 `src/a.py` ——
    同一个文件，只是 prompt 里省略了目录。用严格相等比较会判成
    "写的不是该写的地方"，把一个正确完成拦掉。

    但要克制：**只允许 basename 兜底**（`a.py` ↔ `src/a.py`），
    不允许模糊匹配（`user.py` 不该匹配 `user_repository.py`）——
    后者会让 `wrong_target` 用例的检查失效。

    从 `preflight._covers` 原样迁来：两处必须用同一套匹配规则，
    否则"preflight 认可、证书不认可"这种矛盾结论迟早出现。
    """
    import fnmatch
    p = (pattern or "").replace("\\", "/").strip()
    a = (actual or "").replace("\\", "/").strip()
    if not p or not a:
        return False
    if p == a:
        return True
    if fnmatch.fnmatch(a, p) or fnmatch.fnmatch(a, "*/" + p.lstrip("/")):
        return True
    if "/" not in p and (a == p or a.endswith("/" + p)):
        return True
    return False
