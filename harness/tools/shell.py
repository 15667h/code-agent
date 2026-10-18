"""shell —— 受约束的命令执行。

## 状态持久化（书 §5「命令执行环境的状态持久化」）

书里说得很具体：

> 许多操作依赖环境状态：切换目录、激活虚拟环境、设置环境变量、启动后台服务。
> 如果每次命令都在全新 shell 中执行，这些状态都会丢失 ——
> 例如 Agent 刚用 `cd` 切到项目目录，下一条命令又回到了根目录，
> 不得不反复做同样的目录切换。
>
> 因此应维护一个**持久化的终端会话**，每次命令都在这个共享终端中执行，
> 保留工作目录、环境变量和会话状态。当然，也应保留启动隔离终端的能力
> 以支持并行任务，但**持久化会话应是默认模式**。

本模块用 `subprocess.Popen` 起一个常驻 shell 进程，通过 stdin/stdout 交互。
为了让"命令边界"可靠可判，我们用**哨兵行**（sentinel）标记每条命令的结束 ——
这是标准做法，也是纯标准库条件下唯一可行的方案（没有办法拿到真正的 PTY
在所有平台上都可用）。

## 超时与看门狗

书 §5 把「输出触顶」「命令挂死」列为 API/工具层故障。本模块：
  · `timeout_ms` 参数（对齐 Codex 的默认 120s）
  · 超时后**杀掉子进程**并把超时作为结构化错误回传，而不是无限等待
"""

from __future__ import annotations

import os
import queue
import re
import shlex
import subprocess
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from ..policy import Action, ActionKind, infer_network_needed, infer_side_effect
from ..protocol import ERR_TOOL, ToolResult
from .registry import Tool, ToolRegistry

DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 600_000
MAX_COMMAND_LEN = 8_000

# cmd.exe 的提示符标记。设为唯一字符串，便于从输出里精确剥离提示符回显，
# 而不是靠猜测"哪一行像提示符"（那会误删真实输出）。
PROMPT_MARK = "__CRUCIBLE_PS__"


class PersistentShell:
    """一个常驻的 shell 会话。

    `cwd` 与 `env` 在命令之间**保持** —— 这正是书里要求的默认行为。
    """

    def __init__(self, workspace: str) -> None:
        self.workspace = os.path.realpath(workspace)
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self.cwd = self.workspace
        self._extra_env: Dict[str, str] = {}
        # 读线程 + 队列：**必须有这一步**。
        # 直接在调用线程里 readline() 会永久阻塞，导致 timeout 的 deadline
        # 检查永远执行不到（实测：`sleep 10` 配 800ms 超时反而成功了）。
        self._lineq: "queue.Queue[Optional[str]]" = queue.Queue()
        self._reader: Optional[threading.Thread] = None

    # ---------- 生命周期 ----------
    def _spawn(self) -> None:
        env = os.environ.copy()
        env.update(self._extra_env)
        env["CRUCIBLE_WORKSPACE"] = self.workspace
        # 让输出不带颜色转义，避免污染上下文
        env["TERM"] = "dumb"
        env["NO_COLOR"] = "1"
        env["GIT_PAGER"] = "cat"
        env["PAGER"] = "cat"

        # 平台差异必须显式处理，不能指望"同一套 shell 语义"：
        #  - POSIX: /bin/sh，UTF-8，哨兵用 printf
        #  - Windows: cmd.exe，**控制台默认代码页是 936(GBK) 而非 UTF-8**，
        #    用 utf-8 解码会把中文路径全变成替换字符（真实踩过的坑）。
        #    同时 cmd 默认回显输入，必须 `@echo off`，否则哨兵/命令回显
        #    会混进输出，并让哨兵识别错位。
        if os.name == "nt":
            # cmd.exe 的启动：
            #  - /q  关闭回显（echo off）
            #  - 编码必须匹配**控制台当前代码页**。简体中文 Windows 默认 936(GBK)，
            #    之前硬用 utf-8 解码，中文路径全变替换字符 —— 这是实测踩到的坑。
            #  - 不用 /u（UTF-16 管道），实测会让 readline 卡住。
            self._proc = subprocess.Popen(
                ["cmd.exe", "/q"],
                cwd=self.cwd, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True, encoding=self._console_encoding(), errors="replace",
                bufsize=1,
            )
        else:
            self._proc = subprocess.Popen(
                ["/bin/sh"],
                cwd=self.cwd, env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )

        self._drain_startup()

    # ---------- 读线程 ----------
    def _start_reader(self) -> None:
        """起一个后台线程把 stdout 逐行推进队列。

        为什么不让调用线程直接 readline：
        阻塞的 readline 会让 `time.time() > deadline` 这个判断永远执行不到，
        超时机制就是假的。用线程 + 队列后，主流程可以 `get(timeout=...)`
        真正做到到点就返回。
        """
        proc = self._proc
        if not proc or not proc.stdout:
            return
        # 换新的队列，避免上一轮的残留行串到这一轮
        self._lineq = queue.Queue()
        q = self._lineq
        out = proc.stdout

        def pump():
            try:
                for line in out:
                    q.put(line)
            except Exception:
                pass
            finally:
                q.put(None)   # 流结束

        t = threading.Thread(target=pump, name="crucible-shell-reader", daemon=True)
        t.start()
        self._reader = t

    def _read_line(self, timeout_s: float) -> "tuple[Optional[str], bool]":
        """从队列取一行。

        返回 (line, eof)。超时返回 (None, False) —— 由调用方判定超时。
        """
        try:
            line = self._lineq.get(timeout=max(0.01, timeout_s))
        except queue.Empty:
            return None, False
        if line is None:
            return None, True
        return line, False

    def _drain_startup(self, timeout_s: float = 5.0) -> None:
        """吃掉 shell 的启动横幅，并把提示符改成可剥离的唯一标记。

        Windows 的 cmd.exe 一起来就打印版权横幅；且管道模式下会把
        「提示符 + 命令」回显进输出流（实测：`echo hello` 的返回值里
        混着 `C:\\...\\ws>hello`）。两件事必须一起处理，否则
        (a) 横幅污染第一条命令的返回，(b) 提示符混进每一条输出。

        做法：把 PROMPT 设成唯一标记再发哨兵，等哨兵回来就说明已就绪。
        之后 `run()` 按标记剥离前缀 —— 这样既不丢掉真实输出
        （`>hello` 里的 `hello` 会被保留），也不会误删正常文本。
        """
        proc = self._proc
        if not proc or not proc.stdin or not proc.stdout:
            return
        sentinel = f"__CRUCIBLE_INIT_{uuid.uuid4().hex[:10]}__"
        try:
            if os.name == "nt":
                # $G 会被 cmd 展开成 ">"，于是提示符固定为 PROMPT_MARK + ">"
                proc.stdin.write(
                    f"@echo off\r\n"
                    f"@prompt {PROMPT_MARK}$G\r\n"
                    f"@echo {sentinel}\r\n")
            else:
                proc.stdin.write(f"printf '%s\\n' '{sentinel}'\n")
            proc.stdin.flush()
        except (OSError, ValueError):
            return

        self._start_reader()
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            line, eof = self._read_line(deadline - time.time())
            if eof:
                break
            if line is None:
                break
            if sentinel in line:
                break

    @staticmethod
    def _strip_prompt(line: str) -> str:
        """剥离 cmd.exe 的提示符前缀。

        提示符已固定为 `PROMPT_MARK + ">"`，所以匹配很窄、不会误伤：
        只有以该标记开头的行才被剥离，且**只剥前缀**，保留后面的真实输出。
        """
        idx = line.find(PROMPT_MARK)
        if idx < 0:
            return line
        # 标记之后紧跟一个 ">"（cmd 的 $G），一起剥掉
        after = line[idx + len(PROMPT_MARK):]
        if after.startswith(">"):
            after = after[1:]
        return after

    @staticmethod
    def _console_encoding() -> str:
        """Windows 控制台代码页对应的 Python 编码名。

        取当前控制台输出代码页。**不能**用 `subprocess.run(["chcp"])` ——
        `chcp` 是 cmd.exe 的内建命令，没有同名可执行文件，直接 exec 必然
        抛 FileNotFoundError（于是静默退回 utf-8，中文路径就全乱了）。
        这里走 Win32 API，拿不到才退回 gbk。
        """
        if os.name != "nt":
            return "utf-8"
        try:
            import ctypes
            cp = ctypes.windll.kernel32.GetConsoleOutputCP()
            if cp == 65001:
                return "utf-8"
            if cp == 936:
                return "gbk"
            if cp == 950:
                return "big5"
            # 其它代码页：优先让 Python 自己查别名，查不到退回 gbk
            try:
                return "cp%d" % cp
            except Exception:
                return "gbk"
        except Exception:
            return "gbk"

    def ensure(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            self._spawn()

    def reset(self) -> None:
        """重置会话（沙箱重置机制属于 Harness，见书 §1 的归属辨析）。"""
        self.kill()
        self.cwd = self.workspace
        self._extra_env = {}

    def kill(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.kill()
            except OSError:
                pass
        self._proc = None
        # 读线程会因流关闭而自行退出；队列里的残留行也一并清掉，
        # 避免下一轮 _spawn 前读到上一次的陈旧输出。
        try:
            while True:
                self._lineq.get_nowait()
        except queue.Empty:
            pass
        self._reader = None

    # ---------- 执行 ----------
    def run(self, command: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> ToolResult:
        with self._lock:
            self.ensure()
            assert self._proc and self._proc.stdin and self._proc.stdout

            sentinel = f"__CRUCIBLE_END_{uuid.uuid4().hex[:12]}__"

            # ── 执行前后快照 ──
            # 为什么需要它：`shell` 是最常用的写文件方式（`printf > f.txt`、
            # 生成器、构建），但它原本**不上报改动了哪些文件**，于是
            # 目标闸门拿不到路径证据，会在文件确实落盘时仍判"未满足"。
            #
            # 为什么不解析命令文本：policy 层的铁律是"安全检查只看结构化数据"，
            # 从命令字符串里正则抠路径既不准（重定向、变量、管道）又开了一个
            # 注入口子。用**文件系统事实**（mtime 变化）得到的才是真的发生了什么。
            #
            # 代价控制：只扫工作区、跳过忽略目录、限深度与文件数；
            # 任何异常都退化为空集合，绝不影响命令本身执行。
            before = self._snapshot()

            # 用哨兵包住命令：哨兵里嵌 pwd 与退出码，一次取回全部状态。
            #
            # Windows 的两个坑（都是实测踩到的）：
            #  1. `%ERRORLEVEL%` 在**同一行**展开的是上一行的值。
            #     必须让它单独成行，且紧跟在命令之后。
            #  2. cmd 会把 `%CD%` 展开成含空格路径时不加引号，
            #     这里用 `cd` 单独回显更稳，不依赖变量展开。
            if os.name == "nt":
                script = (
                    f"@echo off\r\n"
                    f"{command}\r\n"
                    f"@echo {sentinel}:%ERRORLEVEL%:%CD%\r\n"
                )
            else:
                script = (
                    f"{command}\n"
                    f"__crucible_rc=$?\n"
                    f"printf '%s:%s:%s\\n' '{sentinel}' \"$__crucible_rc\" \"$PWD\"\n"
                )

            try:
                self._proc.stdin.write(script)
                self._proc.stdin.flush()
            except (OSError, ValueError) as e:
                self.kill()
                return ToolResult.error(
                    f"终端会话已失效，无法写入命令：{e}。会话已重置，请重试。",
                    layer=ERR_TOOL, retryable=True, kind="shell_dead",
                )

            out: List[str] = []
            rc: Optional[int] = None
            deadline = time.time() + max(1, timeout_ms) / 1000.0
            timed_out = False

            # 逐行读，遇到哨兵即停（哨兵行不进入输出）
            if self._reader is None or not self._reader.is_alive():
                self._start_reader()
            while True:
                remain = deadline - time.time()
                if remain <= 0:
                    timed_out = True
                    break
                line, eof = self._read_line(remain)
                if eof:
                    # 进程 EOF → shell 已退出
                    self.kill()
                    break
                if line is None:
                    # 队列在剩余时间内没数据 → 超时
                    timed_out = True
                    break
                if sentinel in line:
                    m = re.search(re.escape(sentinel) + r":(-?\d+)(?::(.*))?", line.strip())
                    if m:
                        rc = int(m.group(1))
                        new_cwd = (m.group(2) or "").strip()
                        if new_cwd and os.path.isdir(new_cwd):
                            self.cwd = new_cwd
                    break
                if os.name == "nt":
                    # cmd 会回显「提示符 + 命令」；剥掉提示符前缀，保留真实输出
                    line = self._strip_prompt(line)
                    if line in ("", "\n", "\r\n"):
                        continue
                out.append(line.rstrip("\n"))

            if timed_out:
                self.kill()
                return ToolResult.error(
                    f"命令超过 {timeout_ms} ms 未结束，已被终止。\n"
                    "如果这是长时任务，请拆成更小的步骤；如果命令在等待输入，"
                    "请改用非交互形式（加 -y / --yes / </dev/null 等）。",
                    layer=ERR_TOOL, retryable=False, kind="timeout",
                )

            text = "\n".join(out)
            if rc is None:
                text += "\n（命令未返回退出码，终端会话可能已中断。）"
                return ToolResult(ok=False, text=text, exit_code=None,
                                  side_effect=True, error_layer=ERR_TOOL,
                                  retryable=True, meta={"cwd": self.cwd})

            changed, probe_note = self._diff_snapshot(before)
            return ToolResult(
                ok=(rc == 0),
                text=text,
                exit_code=rc,
                side_effect=True,
                files_changed=changed,
                meta={"cwd": self.cwd, "command": command,
                      "changed_probe": probe_note},
            )

    # ---------- 改动探测 ----------
    _SNAP_IGNORE = {".git", "node_modules", "__pycache__", ".venv", "venv",
                    "dist", "build", ".next", "target", ".mypy_cache",
                    ".pytest_cache", ".crucible"}
    _SNAP_MAX_FILES = 4000
    _SNAP_MAX_DEPTH = 6

    def _snapshot(self) -> Optional[Dict[str, float]]:
        """记录工作区文件 → mtime。超限或出错返回 None（退化为不探测）。"""
        root = self.workspace
        if not root or not os.path.isdir(root):
            return None
        snap: Dict[str, float] = {}
        root_depth = root.rstrip(os.sep).count(os.sep)
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames
                               if d not in self._SNAP_IGNORE and not d.startswith(".")]
                if dirpath.count(os.sep) - root_depth >= self._SNAP_MAX_DEPTH:
                    dirnames[:] = []
                for fn in filenames:
                    full = os.path.join(dirpath, fn)
                    try:
                        snap[full] = os.stat(full).st_mtime_ns
                    except OSError:
                        continue
                    if len(snap) >= self._SNAP_MAX_FILES:
                        return snap
        except OSError:
            return None
        return snap

    def _diff_snapshot(self, before: Optional[Dict[str, float]]) -> "tuple[List[str], str]":
        """对比出新增/修改的文件（相对工作区路径）。

        返回 (改动清单, 说明)。说明会写进 meta，让"为什么这条命令算改了文件"
        这件事可追查 —— 闸门的判据必须是可复核的。
        """
        if before is None:
            return [], "未探测（工作区不可用或快照失败）"
        after = self._snapshot()
        if after is None:
            return [], "未探测（快照失败）"
        changed: List[str] = []
        for full, mt in after.items():
            old = before.get(full)
            if old is None or old != mt:
                try:
                    rel = os.path.relpath(full, self.workspace).replace(os.sep, "/")
                except ValueError:
                    continue
                changed.append(rel)
        # 删除也是一种改动
        for full in before:
            if full not in after:
                try:
                    rel = os.path.relpath(full, self.workspace).replace(os.sep, "/")
                except ValueError:
                    continue
                changed.append(rel)
        changed.sort()
        if len(changed) > 200:
            return changed[:200], f"探测到 {len(changed)} 个改动，仅保留前 200 个"
        return changed, f"探测到 {len(changed)} 个改动"

    # ---------- 环境变量 ----------
    def set_env(self, key: str, value: str) -> None:
        """设置会话级环境变量。下一条命令起生效，**不重启 shell**。"""
        self._extra_env[key] = value
        with self._lock:
            if self._proc and self._proc.stdin and self._proc.poll() is None:
                try:
                    if os.name == "nt":
                        self._proc.stdin.write(f"set {key}={value}\r\n")
                    else:
                        self._proc.stdin.write(f"export {key}={shlex.quote(value)}\n")
                    self._proc.stdin.flush()
                except (OSError, ValueError):
                    pass

    def current_cwd(self) -> str:
        """返回会话当前目录（通常是 `cd` 之后的位置）。"""
        return self.cwd


# ══════════════════════════════════════════════════════════════════
# 工具实现
# ══════════════════════════════════════════════════════════════════
class ShellTool:
    """把持久化 shell 包成工具。持有会话实例，跨调用保持状态。"""

    def __init__(self, workspace: str) -> None:
        self.shell = PersistentShell(workspace)

    def __call__(self, args: Dict[str, Any]) -> ToolResult:
        cmd = args.get("command", "")
        if not isinstance(cmd, str) or not cmd.strip():
            return ToolResult.error("command 不能为空。", retryable=False, kind="bad_args")
        if len(cmd) > MAX_COMMAND_LEN:
            return ToolResult.error(
                f"命令过长（{len(cmd)} 字符，上限 {MAX_COMMAND_LEN}）。请拆成多条命令。",
                retryable=False, kind="too_long",
            )
        t = int(args.get("timeout_ms") or DEFAULT_TIMEOUT_MS)
        t = max(1000, min(t, MAX_TIMEOUT_MS))
        return self.shell.run(cmd, timeout_ms=t)

    def reset(self) -> None:
        self.shell.reset()


def describe_shell(args: Dict[str, Any]) -> Action:
    """把 shell 命令翻译成结构化 Action。

    这是本设计里最需要小心的一个函数：**判定依据必须是结构化的推断结果
    （side_effect / needs_network），而不是命令文本的"语义理解"**。
    书 §1 的纪律：安全检查只看结构化数据，因为命令文本可能已被提示注入操纵。

    因此这里做的是**保守推断**（`infer_side_effect` 只在明确只读命令上返回 False），
    推断结果进入 Action 的布尔字段，由 policy 的 9 格表判定。
    命令原文只用于危险模式匹配（`Policy.check_dangerous`），
    **永远不能成为放行的理由**。
    """
    cmd = args.get("command", "") or ""
    return Action(
        kind=ActionKind.SHELL,
        command=cmd,
        side_effect=infer_side_effect(cmd),
        needs_network=infer_network_needed(cmd),
        summary=" ".join(cmd.split())[:160],
    )


def register_shell_tools(reg: ToolRegistry, workspace: str, shell: ShellTool) -> None:
    reg.register(Tool(
        name="shell",
        description=(
            "在**持久化**的终端会话里运行命令。会话的工作目录与环境变量在命令之间保持 —— "
            "`cd` 之后的下一条命令会停在新目录，`export` 也会保留。\n"
            "什么时候用：运行测试、构建、安装、git 操作，以及没有专用工具覆盖的场景。\n"
            "优先规则：**存在专用工具时优先用专用工具** —— 读文件用 read_file（带行号）、"
            "搜内容用 grep、找文件用 glob、改文件用 apply_patch。"
            "专用工具返回结构化结果与更清晰的错误，shell 只是兜底出口。\n"
            "边界与约束：\n"
            "· 命令受**沙箱模式与审批策略**双重约束，越界动作会被拒绝或要求确认。\n"
            "· 危险动作（rm -rf、强制推送、无 WHERE 的 DELETE、管道执行远程脚本等）"
            "**即使在最宽松的配置下也需要人工确认**，不会静默放行。\n"
            "· 有副作用的复合命令（含 `|` `>` `&` `;` 等）会被保守地判为需要审批。\n"
            "· 默认 120 秒超时，超时后进程被终止。长任务请拆分。\n"
            "· 不要用交互式命令（vim、less、read 等），也不要用会等待输入的命令。"
        ),
        properties={
            "command": {"type": "string", "description": "要执行的命令，例如 `pytest -q tests/`"},
            "timeout_ms": {"type": "integer", "description": "超时毫秒数，默认 120000，上限 600000"},
        },
        required=["command"],
        examples=[
            {"command": "pytest -q tests/test_x.py"},
            {"command": "git status --short"},
            {"command": "npm run build", "timeout_ms": 300000},
        ],
        handler=shell,
        describe_action=describe_shell,
        supports_parallel=False,      # 共享一个会话，不能并发
        category="执行工具",
    ))
