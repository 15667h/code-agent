"""零依赖 HTTP + SSE 服务。

## 为什么用标准库而不是框架

对齐本项目的两项纪律：
  1. 书 §1 强调 Harness 是「Agent 边界内、模型之外」的治理层。
     治理层引入 Web 框架只会扩大攻击面与依赖面。
  2. 前身项目（代码审查 agent）已验证过「标准库 HTTP + SSE + 同源托管前端」
     这条路可行，且不需要 CORS —— 服务同源托管前端是消除一整类问题的做法。

## 事件流

书 §5 与 Codex 的做法一致：**事件应区分类型，而不是把所有内容混成字符串**。
本服务把 EventBus 里的事件原样按 SSE 推送，前端已按同一套类型渲染，
因此前后端协议是同一份定义（protocol.py 的 EventType）。
"""

from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs, quote

# 既支持 `python -m harness.server`，也支持 `python harness/server.py`。
# 后者没有父包，相对导入会失败，所以这里补上包路径再统一用绝对导入。
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from harness.chain import verify_all, verify_chain
    from harness.context import DEFAULT_SYSTEM_PROMPT, StaticPrefix
    from harness.loop import AgentLoop, LoopConfig, build_default_registry
    from harness.model import (ENV_API_KEY, ENV_BASE_URL, ENV_MODEL, EndpointConfig,
                               HttpModel, ScriptedModel, clear_config, config_path,
                               error_reply, load_config, mask_key, normalize_base_url,
                               resolve_endpoint, save_config, tool_call_reply, final_reply)
    from harness.policy import ApprovalPolicy, Policy, SandboxMode
    from harness.protocol import Event, EventBus, EventType
    from harness.session import Session, list_runs
    from harness.tools.shell import ShellTool
    from harness.index import (build_index, load_index, save_index,
                               search_index, stale_report)
else:
    from .chain import verify_all, verify_chain
    from .context import DEFAULT_SYSTEM_PROMPT, StaticPrefix
    from .loop import AgentLoop, LoopConfig, build_default_registry
    from .model import (ENV_API_KEY, ENV_BASE_URL, ENV_MODEL, EndpointConfig,
                        HttpModel, ScriptedModel, clear_config, config_path,
                        error_reply, load_config, mask_key, normalize_base_url,
                        resolve_endpoint, save_config, tool_call_reply, final_reply)
    from .policy import ApprovalPolicy, Policy, SandboxMode
    from .protocol import Event, EventBus, EventType
    from .session import Session, list_runs
    from .tools.shell import ShellTool
    from .index import (build_index, load_index, save_index,
                        search_index, stale_report)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ══════════════════════════════════════════════════════════════════
# 会话注册表（进程内）
# ══════════════════════════════════════════════════════════════════
class RunState:
    """一次运行的可变状态，供 SSE 订阅与审批回灌。"""

    def __init__(self, run_id: str, workspace: str) -> None:
        self.run_id = run_id
        self.workspace = workspace
        self.queue: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        self.loop: Optional[AgentLoop] = None
        self.session: Optional[Session] = None
        self.result: Optional[Dict[str, Any]] = None
        self.thread: Optional[threading.Thread] = None
        self.started = time.time()
        self.error: Optional[str] = None
        # 这次运行实际用的是哪个端点（供前端回显"真的在调谁"）
        self.endpoint: Optional[EndpointConfig] = None

        # ── 审批闸门的阻塞/唤醒机制 ──
        # 设计：工具调用线程在 await_approval() 上**阻塞**，直到
        # /api/run/<id>/approve 端点回灌决定。
        # 为什么不用"返回 False 让循环继续、之后再补执行"：
        #   那样会把同一次调用拆成两次执行路径，极易造成重复副作用。
        #   书 §5 明确要求「若半截输出里已有工具因流式提前执行，
        #   续写前按调用指纹去重，避免重复副作用」—— 与其事后去重，
        #   不如让它在原地等，这条路径上根本没有第二次执行。
        self._gate_cv = threading.Condition()
        self._pending_decision: Optional[Dict[str, Any]] = None
        # 审批等待上限。可被 create_run 覆盖：无人在场的批处理场景应设小值，
        # 否则运行会白等满整个超时才失败。
        self.approval_timeout_s: float = 300.0

    def on_event(self, ev: Event) -> None:
        self.queue.put(ev.to_json())

    # ---------- 审批：阻塞等待 / 外部唤醒 ----------
    def await_approval(self, req: Any, timeout_s: Optional[float] = None) -> Optional[str]:
        """工具线程在这里阻塞，等外部（HTTP）给出决定。

        返回 "approved" / "rejected"。
        **超时按"拒绝"返回**（不是 None）：None 在 router 里的语义是
        「挂起，等外部后续决定」——那是给**非阻塞**处理器用的。
        一个阻塞的处理器等到超时，就必须给出终态，否则运行会永久挂着，
        既占着线程，又对外表现为永远"正在运行"。
        故障安全方向是拒绝：没人批，就不做。
        """
        if timeout_s is None:
            timeout_s = self.approval_timeout_s
        deadline = time.time() + timeout_s
        with self._gate_cv:
            self._pending_decision = None
            while self._pending_decision is None:
                remain = deadline - time.time()
                if remain <= 0:
                    break
                self._gate_cv.wait(min(remain, 1.0))
            dec = self._pending_decision
            self._pending_decision = None
            if dec is None:
                # 超时：显式发事件，让前端与落盘记录都看得到"等超时了"这件事，
                # 而不是让它静默消失。
                # **必须走 bus** —— 直接塞 queue 会绕过 seq 分配，
                # 而 seq=0 会被前端的去重逻辑当成陈旧事件丢掉。
                if self.session is not None:
                    self.session.bus.emit(
                        EventType.ERROR, error_layer="control",
                        cls="approval_timeout",
                        reason=f"审批等待超过 {int(timeout_s)}s 仍无决定，已按拒绝处理（故障安全）。")
                return "rejected"
            return dec.get("decision")

    def deliver_approval(self, decision: str, remember: bool = False) -> None:
        with self._gate_cv:
            self._pending_decision = {"decision": decision, "remember": remember}
            self._gate_cv.notify_all()


RUNS: Dict[str, RunState] = {}
RUNS_LOCK = threading.Lock()

# 代码索引缓存：按工作区各存一份。
# 为什么放进程内：建索引是纯计算（实测 53 个文件 0.17s），而它
# **与某次运行无关** —— 它描述的是「当前工作区长什么样」。
# 落盘缓存仍然写（供冷启动用），但内存态优先，
# 否则每次查询都要重读 ~700KB JSON。
_INDEX_CACHE: Dict[str, Any] = {}


# ══════════════════════════════════════════════════════════════════
# 运行装配
# ══════════════════════════════════════════════════════════════════
def create_run(workspace: str, prompt: str,
               sandbox: str = "workspace-write",
               approval: str = "on-request",
               model_name: Optional[str] = None,
               endpoint: Optional[Dict[str, Any]] = None,
               offline_script: Optional[List[str]] = None,
               goal_conditions: Optional[List[str]] = None,
               approval_timeout_s: float = 300.0,
               config: Optional[LoopConfig] = None) -> RunState:
    """装配一次运行。

    **模型接入只有一条路径**：`EndpointConfig`（接口地址 / API Key / 模型名）。
    配置来源优先级：请求里显式带 > 落盘的 `.crucible/config.json` > 空（走离线脚本）。

    `offline_script` 是离线脚本（形如 ["shell:git status", "final:完成"]），
    让前端在没有 key 时也能看到**真实驱动**的事件流，
    而不是前端自己伪造的假事件 —— 这点很重要：
    书 §1 的消融实验专门警告过「上下文残缺时典型的失败不是报错退出，
    而是一个看上去毫无破绽的答案」。
    """
    workspace = os.path.realpath(workspace)
    run_id = uuid.uuid4().hex[:12]
    st = RunState(run_id, workspace)
    st.approval_timeout_s = max(1.0, float(approval_timeout_s))

    ep = resolve_endpoint(workspace, endpoint)
    sbx = SandboxMode(sandbox)
    ap = ApprovalPolicy(approval)

    # 模型选择规则（单一判据，不猜）：
    #   显式给了 offline_script → 用脚本（测试与演示要确定性）
    #   端点三个字段齐备      → 用真实模型
    #   否则                  → 离线脚本（并让 UI 明确显示"未配置"）
    use_scripted = offline_script is not None or not ep.configured
    if use_scripted:
        model = _build_scripted(offline_script)
    else:
        model = HttpModel(ep, model_name or ep.model)

    # run_id 一并落盘：进程重启后仍能用它找到运行目录并核验事件链。
    # 不传的话，`RUNS` 一清空，外部拿着 run_id 就再也找不到对应目录了。
    session = Session(workspace, model.name, ep.name, sbx.value, ap.value,
                      run_id=run_id)

    registry, shell = build_default_registry(workspace)

    prefix = StaticPrefix(DEFAULT_SYSTEM_PROMPT, registry.specs())

    policy = Policy(sbx, ap, workspace)

    loop = AgentLoop(
        model=model, registry=registry, prefix=prefix, policy=policy,
        session=session, config=config, goal_conditions=goal_conditions,
        approval_handler=lambda req: st.await_approval(req),
    )
    # 让状态栏能反映 shell 的真实 cwd
    loop.router.shell_cwd = shell.shell.current_cwd()

    st.loop = loop
    st.session = session
    st.endpoint = ep
    session.bus.subscribe(st.on_event)

    with RUNS_LOCK:
        RUNS[run_id] = st
    return st


def _build_scripted(spec: Optional[List[str]]):
    """把简写的脚本字符串翻译成 ModelReply 序列。

    语法（`|` 后是参数）：
        read:<path>            读文件
        search:<pattern>       grep 搜索
        shell:<command>        运行命令
        patch:<path>           新建一个文件
        glob:<pattern>         按文件名查找
        list:<path>            列目录
        call:<工具名>:<JSON>   任意工具（参数直接给 JSON）
        error:<status>:<msg>   注入一个 HTTP 错误
        final:<text>           最终回答

    **未知动词一律抛错，而不是悄悄降级成"最终回答"。**
    这里踩过坑：早期版本把无法识别的动词当成 final 文本，
    结果 `list_dir:.` 被当成了回答，工具一个都没调，
    而事件流看起来"正常完成" —— 正是书 §1 消融实验警告的那种
    「看上去毫无破绽的失败」。解析错误必须立刻可见。
    """
    script = []
    # 收到非字符串项时**给出格式说明**，而不是让它在 partition 上抛
    # AttributeError —— 实测踩到：传成 `[{"tool_calls": [...]}]`（看起来
    # 很合理）会得到 "dict object has no attribute 'partition'"，
    # 完全看不出该用什么格式。**指错方向/不给方向比报错本身更糟。**
    for i, item in enumerate(spec or []):
        if not isinstance(item, str):
            raise ValueError(
                f"offline_script 的第 {i + 1} 项是 {type(item).__name__}，"
                "但这里只接受**字符串**。正确格式："
                '["call:shell:{\\"command\\":\\"git status\\"}", "final:完成"]。'
                "可用动词见 _parse_offline_script 的文档。")
    for item in (spec or []):
        kind, _, rest = item.partition(":")
        kind = kind.strip()
        rest = rest.strip()

        if kind == "read":
            script.append(tool_call_reply("read_file", {"path": rest},
                                          f"先读 {rest} 看当前内容。"))
        elif kind == "list":
            script.append(tool_call_reply("list_dir", {"path": rest or "."},
                                          f"列出 {rest or '.'} 的内容。"))
        elif kind == "search":
            script.append(tool_call_reply("grep", {"pattern": rest, "limit": 40},
                                          f"搜索 {rest} 定位相关代码。"))
        elif kind == "glob":
            script.append(tool_call_reply("glob", {"pattern": rest},
                                          f"按模式 {rest} 找文件。"))
        elif kind == "shell":
            script.append(tool_call_reply("shell", {"command": rest},
                                          f"运行命令：{rest}"))
        elif kind == "patch":
            patch = (f"*** Begin Patch\n*** Add File: {rest}\n"
                     f"+# 由 agent 创建\n*** End Patch")
            script.append(tool_call_reply("apply_patch", {"patch": patch},
                                          f"新建 {rest}。"))
        elif kind == "call":
            name, _, args_raw = rest.partition(":")
            try:
                args = json.loads(args_raw) if args_raw.strip() else {}
            except json.JSONDecodeError as e:
                raise ValueError(f"offline_script 的 `call:{name}` 参数不是合法 JSON：{e}")
            script.append(tool_call_reply(name.strip(), args, f"调用 {name.strip()}。"))
        elif kind == "error":
            code, _, msg = rest.partition(":")
            script.append(error_reply(int(code or 429), msg or "injected"))
        elif kind == "final":
            script.append(final_reply(rest))
        else:
            raise ValueError(
                f"offline_script 含未知动词 `{kind}`（原始项：{item!r}）。"
                "可用：read / list / search / glob / shell / patch / call / error / final。"
                "注意不要把工具名直接当动词用 —— 要调用任意工具请写 `call:<工具名>:<JSON>`。"
            )
    if not script:
        script = [
            tool_call_reply("list_dir", {"path": "."}, "先看看工作区里有什么。"),
            final_reply("这是离线演示：我已列出工作区内容。配置 API key 后可跑真实模型。"),
        ]
    return ScriptedModel(script)


def start_run(st: RunState, prompt: str) -> None:
    def worker():
        try:
            res = st.loop.run(prompt)
            st.result = res.to_json()
        except Exception as e:
            st.error = f"{type(e).__name__}: {e}"
        finally:
            st.queue.put(None)      # 关闭 SSE 流

    st.thread = threading.Thread(target=worker, daemon=True)
    st.thread.start()


# ══════════════════════════════════════════════════════════════════
# HTTP 处理
# ══════════════════════════════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):
    server_version = "Crucible/0.1"

    def log_message(self, fmt, *args):      # 降噪
        pass

    # ---------- 工具 ----------
    def _find_run_dir(self, run_id: str,
                      workspace: Optional[str] = None) -> Optional[str]:
        """按 run_id 找出它的落盘目录。

        为什么需要它：`RUNS` 是内存态，进程重启就没了。但哈希链的价值恰恰是
        **不依赖产生它的那个进程还活着** —— 只要 run 目录还在，任何人都能核验。
        所以核验端点必须能在没有 RunState 的情况下工作。

        匹配顺序：meta.json 里的 run_id → 目录名 → session_id。
        最先查 run_id 是因为那才是**对外**的标识（session_id 是内部落盘目录名，
        两者独立生成，实测过拿 run_id 去匹配目录名匹配不上）。

        ## `workspace` 参数是后补的，补它的原因值得记

        实测踩到：前端从 `/api/runs` 列出 13 个 run，点进去有一半报 404，
        而且**看不出为什么**。根因是这里把根目录硬编码成 `default_workspace()`，
        而 `/api/runs` 是**按传入的 workspace** 列的 —— 两个端点用了不同的
        工作区口径，于是"列得出来、点不进去"。

        「列表里有、详情取不到」是最坏的一种不一致：用户会以为是 run 坏了。
        所以详情端点现在也接受 workspace（与 `/api/runs` 同口径），
        由调用方把列表里那一项的工作区带回来。不带时退回默认工作区
        （保持旧行为，不破坏既有调用）。
        """
        if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
            return None              # 拒绝路径穿越

        # 先查内存态：本次进程自己跑出来的 run 一定有 RunState，
        # 直接用它记录的 run_dir 最准确（不受工作区口径影响）。
        st = RUNS.get(run_id)
        if st is not None and st.session is not None:
            return st.session.run_dir

        roots = []
        if workspace:
            roots.append(os.path.join(os.path.realpath(workspace),
                                      ".crucible", "runs"))
        roots.append(os.path.join(os.path.realpath(default_workspace()),
                                  ".crucible", "runs"))

        for root in roots:
            if not os.path.isdir(root):
                continue
            for name in os.listdir(root):
                d = os.path.join(root, name)
                if not os.path.isdir(d):
                    continue
                if name == run_id or name.startswith(run_id):
                    return d
                mp = os.path.join(d, "meta.json")
                if os.path.isfile(mp):
                    try:
                        with open(mp, "r", encoding="utf-8") as f:
                            m = json.load(f)
                    except (OSError, json.JSONDecodeError):
                        continue
                    if m.get("run_id") == run_id or m.get("session_id") == run_id:
                        return d
        return None

    def _resolve_run_dir(self, rid: str, q: Dict[str, List[str]]) -> Optional[str]:
        """详情端点的统一解析：内存态 → 指定工作区 → 默认工作区。

        所有 `/api/run/<id>/*` 端点都走这一个入口，避免"某些端点支持
        workspace、某些不支持"这种最难排查的参差。
        """
        ws = (q.get("workspace") or [None])[0]
        return self._find_run_dir(rid, ws)

    def _replay_result(self, rid: str, run_dir: str) -> Dict[str, Any]:
        """从落盘事件重建"运行结果"。

        ## 为什么必须有它

        `/result` 原本只认内存态，**历史 run 取不到结果与用量** ——
        进程一重启就 404。而 usage（"这次花了多少 token"）恰恰只在这里。
        与 `/events` 曾是同一个坑：**同一类缺陷出现在两个端点，
        说明上次修的时候只修了眼前那一个。**

        ## 重建原则：只读结构化字段

        `status` / `iterations` / `tool_calls` / `reason` / `usage` 全部来自
        `turn.completed` 事件的 `data`（结构化），**不解析任何模型文本**。
        这与 verify / preflight / capability / certificate 是同一条纪律。

        取**最后一个** `turn.completed`：一次会话可能有多次（重试/多轮），
        最终状态是最后一次。
        """
        ev_path = os.path.join(run_dir, "events.jsonl")
        if not os.path.isfile(ev_path):
            return {"error": "no events recorded",
                    "detail": "这个 run 目录里没有 events.jsonl，无法重建结果。"}

        final: Optional[Dict[str, Any]] = None
        counts: Dict[str, int] = {}
        usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        usage_seen = False
        files_changed: List[str] = []
        errors = 0

        with open(ev_path, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    e = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                t = e.get("type")
                counts[t] = counts.get(t, 0) + 1
                d = e.get("data") or {}
                if t == "turn.completed":
                    # 用 last 而不是累积 —— reason/status 是"最终状态"语义
                    final = d
                    u = d.get("usage") or {}
                    if u:
                        usage_seen = True
                        for k in usage_total:
                            try:
                                usage_total[k] += int(u.get(k) or 0)
                            except (TypeError, ValueError):
                                pass
                if t == "tool.output":
                    for p in (d.get("files_changed") or []):
                        if p not in files_changed:
                            files_changed.append(p)
                if t == "error":
                    errors += 1

        final = final or {}
        return {
            "run_id": rid,
            "mode": "replay",
            "run_dir": run_dir,
            "summary": {
                "session_id": rid,
                "run_dir": run_dir,
                "events": sum(counts.values()),
                "by_type": counts,
                # 链状态一并给出：审计信息不该藏在需要额外调用的地方
                # （与 session.summary 保持一致的口径）
                "chain_head": self._chain_head(run_dir),
            },
            "result": {
                "status": final.get("status") or "",
                "reason": final.get("reason") or "",
                "answer": final.get("answer") or "",
                "iterations": final.get("iterations"),
                "tool_calls": final.get("tool_calls"),
                # usage 为 0 时给 None 而不是三个 0 ——
                # "没记录用量"与"用量确实是零"不是一回事，
                # 报成 0 会让人以为这次调用不花钱。
                "usage": usage_total if usage_seen else None,
                "files_changed": files_changed,
                "errors": errors,
            },
        }

    @staticmethod
    def _chain_head(run_dir: str) -> Optional[str]:
        p = os.path.join(run_dir, "chain.head")
        if not os.path.isfile(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip() or None
        except OSError:
            return None

    def _json(self, code: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> Dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _static(self, rel: str) -> None:
        """同源托管前端 —— 消除 CORS（前身项目的既有做法）。"""
        p = os.path.normpath(os.path.join(ROOT, rel.lstrip("/")))
        if not p.startswith(ROOT) or not os.path.isfile(p):
            self.send_error(404, "not found")
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".md": "text/markdown; charset=utf-8",
        }.get(os.path.splitext(p)[1].lower(), "application/octet-stream")
        with open(p, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------- 路由 ----------
    def do_GET(self):
        # 兜住一切未预期的异常。
        # 为什么必须有：没有它时，路由里任何一处抛错都会**静默产生空响应**，
        # 客户端只看到 "Expecting value: line 1 column 1" —— 一个不含任何
        # 诊断信息的症状。实测排查这个花了很久，而真因只是一条 KeyError。
        # 错误必须可见：把异常类型与消息如实返回。
        try:
            return self._route_get()
        except Exception as e:      # noqa: BLE001 —— 路由兜底就是要捕一切
            import traceback
            try:
                self._json(500, {
                    "error": f"{type(e).__name__}: {e}",
                    "path": self.path,
                    "traceback": traceback.format_exc()[-1200:],
                })
            except Exception:
                pass

    def _route_get(self):
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query)

        if path == "/api/health":
            return self._json(200, {"ok": True, "runs": len(RUNS),
                                    "pid": os.getpid()})
        if path == "/api/runs":
            ws = (q.get("workspace") or [default_workspace()])[0]
            return self._json(200, {"runs": list_runs(ws)})

        if path == "/api/runs/all":
            # 跨工作区聚合。为什么需要它：
            # `/api/runs` 只列**一个**工作区的 run，而 session 目录是
            # 按工作区分散的（每次以不同 workspace 启动就多一份）。
            # 前端要给出"这个服务一共做过什么"的全局视图，就必须有人聚合。
            # 顺带把每个 run 的**工作区**带回去 —— 详情端点需要它来定位，
            # 否则就会出现"列表里有、点进去 404"（本项目刚踩过这个坑）。
            roots: List[str] = []
            # 显式传入的工作区优先，其次进程默认工作区。
            # 另外把工作区**下层**也扫一层 —— 真实运行过的工作区（例如
            # 演示/临时工作区）是嵌套的：`<root>/.crucible/<name>/.crucible/runs`。
            # 不扫这一层的结果是"跑过但列不出来"，而列表里少东西
            # 比列表里多东西更难被发现。
            #
            # 判据必须落在 `<候选>/.crucible/runs` 上，而不是 `<候选>/runs` ——
            # 后者会把 `<root>/.crucible/runs` 自身当成一个"工作区"，
            # 而它不是（它是 run 目录的容器）。
            for w in ([q.get("workspace") or []] +
                      [[default_workspace()]]):
                if w and w[0]:
                    roots.append(os.path.realpath(w[0]))
            for base in list(dict.fromkeys(roots)):
                sub = os.path.join(base, ".crucible")
                if not os.path.isdir(sub):
                    continue
                for name in os.listdir(sub):
                    cand = os.path.join(sub, name)
                    if os.path.isdir(os.path.join(cand, ".crucible", "runs")):
                        roots.append(cand)
            seen: Dict[str, Any] = {}
            for ws_dir in dict.fromkeys(roots):
                for m in list_runs(ws_dir):
                    key = m.get("run_id") or m.get("session_id") or m.get("run_dir")
                    if key in seen:
                        continue
                    m = dict(m)
                    m["workspace_dir"] = ws_dir
                    seen[key] = m
            runs = sorted(seen.values(), key=lambda x: x.get("created_at") or 0,
                          reverse=True)
            return self._json(200, {"runs": runs, "count": len(runs),
                                    "sources": list(dict.fromkeys(roots))})

        if path == "/api/tools":
            return self._json(200, _tool_manifest())

        # ── 工作区只读访问（前端 explorer / editor 用真实数据）──
        # 刻意不用 `/api/run/...` 前缀：它们与"某次运行"无关，
        # 而是"当前工作区现在长什么样"。
        if path == "/api/workspace/tree":
            ws = (q.get("workspace") or [default_workspace()])[0]
            rel = (q.get("path") or [""])[0]
            try:
                depth = int((q.get("depth") or ["1"])[0])
            except ValueError:
                depth = 1
            return self._json(200, _list_tree(ws, rel, depth, max_entries=400))

        if path == "/api/workspace/file":
            ws = (q.get("workspace") or [default_workspace()])[0]
            rel = (q.get("path") or [""])[0]
            try:
                limit = int((q.get("max_bytes") or [str(512 * 1024)])[0])
            except ValueError:
                limit = 512 * 1024
            limit = max(1024, min(limit, 4 * 1024 * 1024))
            return self._json(200, _read_workspace_file(ws, rel, limit))

        if path == "/api/workspace/terminal":
            # **受白名单限制的只读命令执行**。
            #
            # ## 为什么不是"开放任意 shell"
            # 这是浏览器发起的请求，不是 agent 的动作 —— 但它会真的执行命令。
            # 开放任意命令等于把 harness 变成一个远程 shell：任何能打开
            # 这个页面的人（本机任何进程、任何浏览器扩展）都能执行任意代码。
            # harness 的沙箱与审批闸门保护的是 **agent**，不保护这条路径。
            #
            # 所以这里用**白名单 + 元字符拒绝**双重限制：
            #   1. 首词必须在白名单里（ls/cat/git status/…）
            #   2. 命令里不得出现 shell 元字符（| & ; > < ` $ ( ) 换行）
            # 两道都过才执行。**拒绝时给原因**，不静默失败。
            ws = (q.get("workspace") or [default_workspace()])[0]
            cmd = (q.get("cmd") or [""])[0]
            return self._json(200, _run_readonly_command(ws, cmd))

        if path == "/api/workspace/git-status":
            ws = (q.get("workspace") or [default_workspace()])[0]
            return self._json(200, _workspace_git_status(ws))

        if path == "/api/workspace/index":
            # 代码索引（符号 + 词表倒排）。
            #
            # 内存态优先（同一个工作区只建一次），否则读落盘缓存；
            # 都没有就**按需建**并落盘 —— 前端首次点"索引"时不该等太久，
            # 而 53 个文件的索引实测只要 0.17s。
            ws = os.path.realpath((q.get("workspace") or [default_workspace()])[0])
            rebuild = (q.get("rebuild") or ["0"])[0] in ("1", "true", "yes")
            idx = _INDEX_CACHE.get(ws)
            if idx is None or rebuild:
                cache_path = os.path.join(ws, ".crucible", "index.json")
                if not rebuild:
                    idx = load_index(cache_path)
                    if idx is not None:
                        _INDEX_CACHE[ws] = idx
                if idx is None or rebuild:
                    idx = build_index(ws)
                    _INDEX_CACHE[ws] = idx
                    try:
                        save_index(idx, cache_path)
                    except OSError as e:
                        # 落盘失败不致命，但**要说出来** —— 否则下次
                        # 冷启动会重新建索引，而用户以为缓存是好的。
                        idx.errors.append(f"落盘失败：{e}")
            return self._json(200, {
                **idx.to_json(with_symbols=False),
                "cache": "memory" if ws in _INDEX_CACHE else "disk",
                "limit_note": "这是「符号索引 + 词表倒排」，不是语义索引："
                              "搜「处理完成判定的逻辑」这类意图搜不到。"
                              "也不含跨文件调用关系（谁调用了谁）。",
            })

        if path == "/api/workspace/index/query":
            ws = os.path.realpath((q.get("workspace") or [default_workspace()])[0])
            text = (q.get("q") or [""])[0]
            if not text.strip():
                return self._json(400, {"error": "empty query",
                                        "detail": "查询词不能为空。"})
            idx = _INDEX_CACHE.get(ws)
            if idx is None:
                cache_path = os.path.join(ws, ".crucible", "index.json")
                idx = load_index(cache_path) or build_index(ws)
                _INDEX_CACHE[ws] = idx
            # 陈旧时**照常返回结果，但必须带上过期标记** ——
            # 直接拒绝会让用户以为"查不了"，而实际是"结果可能不全"。
            stale = stale_report(idx, ws)
            result = search_index(idx, text)
            result["stale"] = stale["stale"]
            result["stale_detail"] = stale["detail"] if stale["stale"] else ""
            result["indexed_at"] = idx.built_at
            result["file_count"] = len(idx.files)
            return self._json(200, result)

        # ── 本机目录浏览（供"选择工作区"）──
        # 这个端点**与 workspace 无关**：它就是要去看工作区之外的地方。
        # 只读、只列目录，见 _fs_list 的说明。
        if path == "/api/fs/list":
            p = (q.get("path") or [""])[0]
            return self._json(200, _fs_list(p))

        if path == "/api/workspace/info":
            # 工作区概要：前端状态栏与索引提示需要它。
            # 只报**能可靠得到**的几个量（路径、文件数、是否有 .git）。
            ws = (q.get("workspace") or [default_workspace()])[0]
            return self._json(200, _workspace_info(ws))

        if path == "/api/workspace/search":
            # 工作区文本搜索。
            #
            # **刻意复用 agent 用的 grep 工具**，而不是另写一套实现：
            # 两套实现迟早会漂移，而漂移会让用户看到"搜索结果与 agent
            # 看到的不一样" —— 那是很难解释的一种不一致。
            ws = (q.get("workspace") or [default_workspace()])[0]
            pat = (q.get("q") or [""])[0]
            if not pat.strip():
                return self._json(400, {
                    "error": "empty query",
                    "detail": "搜索词不能为空。",
                })
            return self._json(200, _workspace_search(ws, pat, q))
        if path == "/api/config":
            ws = (q.get("workspace") or [default_workspace()])[0]
            ep = load_config(ws)
            return self._json(200, {
                "endpoint": ep.to_public(),
                "config_path": config_path(ws),
                # 这三个环境变量可以作为替代来源（容器/CI 场景不想落盘明文 key）
                "env_vars": [ENV_BASE_URL, ENV_API_KEY, ENV_MODEL],
            })
        if path.startswith("/api/run/") and path.endswith("/result"):
            # 与 /events 同一处理：内存态优先，否则回落到落盘。
            #
            # 实测踩到：`/result` 原本**只认内存态**，于是历史 run 取不到
            # 运行结果与用量 —— 而 usage 恰恰是"这次花了多少"的唯一来源。
            # 这与 /events 曾是同一个坑（同一类缺陷的第二处）：
            # **同一个 bug 出现在两个端点，说明修的时候只修了眼前那一个。**
            # 所以这次把两者都改成"内存态 → 落盘"的统一形态。
            rid = path.split("/")[3]
            st = RUNS.get(rid)
            if st is not None:
                return self._json(200, {
                    "run_id": rid,
                    "mode": "live",
                    "result": st.result,
                    "error": st.error,
                    "summary": st.session.summary() if st.session else None,
                })
            run_dir = self._resolve_run_dir(rid, q)
            if run_dir is None:
                return self._json(404, {
                    "error": "run not found",
                    "detail": "既不在本次进程的运行表里，也没能在落盘目录中找到。"
                              "若这是另一个工作区跑出来的，请带上 workspace 参数。",
                })
            return self._json(200, self._replay_result(rid, run_dir))

        if path.startswith("/api/run/") and path.endswith("/events"):
            # 两种形态，靠**运行是否还在内存里**区分：
            #   · 在跑 / 刚跑完（RUNS 有）→ SSE 实时流，前端 EventSource 订阅
            #   · 历史 run（进程已重启）→ **一次性回放**落盘事件
            #
            # 为什么必须支持第二种：历史 run 的事件流原本**看不到** ——
            # `/events` 只认内存态，进程一重启就全部 404。
            # 而事件流是这类 agent 最该被回看的东西（哪一步做了什么），
            # 只留 summary 等于把最有价值的证据锁起来。
            rid = path.split("/")[3]
            if rid in RUNS:
                return self._sse(rid)
            run_dir = self._resolve_run_dir(rid, q)
            if run_dir is None:
                return self._json(404, {
                    "error": "run not found",
                    "detail": "既不在本次进程的运行表里，也没能在落盘目录中找到。"
                              "若这是另一个工作区跑出来的，请带上 workspace 参数。",
                })
            ev_path = os.path.join(run_dir, "events.jsonl")
            if not os.path.isfile(ev_path):
                return self._json(404, {
                    "error": "no events recorded",
                    "detail": "这个 run 目录里没有 events.jsonl。",
                })
            events = []
            with open(ev_path, "r", encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        events.append(json.loads(ln))
                    except json.JSONDecodeError:
                        # 坏行跳过而不是整份失败：一次崩溃的运行**恰恰**是最该
                        # 被回看的那次，因为它尾部可能就缺记录。
                        continue
            return self._json(200, {
                "run_id": rid,
                "mode": "replay",      # 前端据此显示"历史回放"而不是"实时"
                "run_dir": run_dir,
                "count": len(events),
                "events": events,
            })

        # ── 事件链核验（防篡改可核验记录）──
        # 这是"外部可核验"的落点：任何人拿到这份 JSON 就能离线重算，
        # 不需要信任本进程、也不需要密钥。
        if path.startswith("/api/run/") and path.endswith("/verify-chain"):
            rid = path.split("/")[3]
            st = RUNS.get(rid)
            if st is not None and st.session is not None:
                return self._json(200, st.session.verify_events())
            # 进程可能已重启（RUNS 是内存态）→ 退回按目录核验，
            # 只要 run 目录还在就仍可核验 —— 这正是哈希链的价值：
            # **不依赖产生它的那个进程还活着**。
            # 注意用**顶部**导入的 verify_chain：局部 `from .chain import`
            # 在脚本直跑模式（`python harness/server.py`）下会
            # ImportError: attempted relative import with no known parent package。
            run_dir = self._resolve_run_dir(rid, q)
            if run_dir is None:
                return self._json(404, {"error": "run not found"})
            return self._json(200, verify_chain(run_dir))

        if path == "/api/verify-chains":
            ws = (q.get("workspace") or [default_workspace()])[0]
            runs_root = os.path.join(os.path.realpath(ws), ".crucible", "runs")
            return self._json(200, verify_all(runs_root))

        # ── 轨迹诊断（trace → eval 闭环）──
        if path.startswith("/api/run/") and path.endswith("/diagnose"):
            rid = path.split("/")[3]
            rs = RUNS.get(rid)
            run_dir = (rs.session.run_dir if (rs and rs.session)
                       else self._resolve_run_dir(rid, q))
            if run_dir is None or not os.path.isdir(run_dir):
                return self._json(404, {"error": "run not found"})
            from harness.evaluate import analyze_trace
            return self._json(200, analyze_trace(run_dir).to_json())

        if path == "/api/diagnose":
            ws = (q.get("workspace") or [default_workspace()])[0]
            from harness.evaluate import analyze_workspace
            flawed_only = (q.get("flawed_only") or ["0"])[0] in ("1", "true", "yes")
            return self._json(200, analyze_workspace(ws, only_flawed=flawed_only))

        # ── 变更集（改坏了能还原）──
        if path.startswith("/api/run/") and path.endswith("/changesets"):
            rid = path.split("/")[3]
            st = RUNS.get(rid)
            if st is not None and st.session is not None:
                cs = st.session.changesets
            else:
                run_dir = self._resolve_run_dir(rid, q)
                if run_dir is None:
                    return self._json(404, {"error": "run not found"})
                from harness.changeset import ChangeSetStore
                cs = ChangeSetStore(run_dir,
                                    default_workspace(), enabled=False)
            return self._json(200, {
                "summary": cs.summary(),
                "history": [c.to_json() for c in cs.history()],
            })

        # ── 能力账本（G-5，CapScope）──
        # 为什么需要这个端点：能力账本的用途是**审计**——
        # "这次会话里有没有出现过任务之外的能力"。
        # 只留在内存里的话，进程一重启就无从查证。
        # 所以先读内存态（最新），读不到再读落盘（进程已重启）。
        if path.startswith("/api/run/") and path.endswith("/capability"):
            rid = path.split("/")[3]
            st = RUNS.get(rid)
            if st is not None and getattr(st, "loop", None) is not None \
                    and st.loop.ledger is not None:
                return self._json(200, st.loop.ledger.report())
            run_dir = self._resolve_run_dir(rid, q)
            if run_dir is None:
                return self._json(404, {"error": "run not found"})
            cap_path = os.path.join(run_dir, "capability.json")
            if not os.path.isfile(cap_path):
                # 不存在 ≠ 空账本。必须区分：前者是"没记录"，
                # 后者是"记录了且没有越界"。混为一谈会让人以为一切正常。
                return self._json(404, {
                    "error": "no capability record",
                    "detail": "这次运行没有能力账本记录"
                              "（会话可能在 capability_ledger 关闭时运行）。"
                              "这不等于「没有越权」，而是「无法判断」。",
                })
            with open(cap_path, "r", encoding="utf-8") as f:
                return self._json(200, json.load(f))

        # ── 子代理列表（G-9）──
        # 只读**落盘**，不读内存态：子代理的 run 目录是持久事实，
        # 而它存在的意义恰恰是"父级进程可能早就没了，结果仍要能查"。
        # 顺带列出每个子代理的隔离记录 —— 隔离不可见就等于没做。
        if path.startswith("/api/run/") and path.endswith("/subagents"):
            rid = path.split("/")[3]
            run_dir = None
            st = RUNS.get(rid)
            if st is not None and st.session is not None:
                run_dir = st.session.run_dir
            if run_dir is None:
                run_dir = self._resolve_run_dir(rid, q)
            if run_dir is None:
                return self._json(404, {"error": "run not found"})
            root = os.path.join(run_dir, "subagents")
            if not os.path.isdir(root):
                return self._json(404, {
                    "error": "no subagent records",
                    "detail": "这次运行没有派出任何子代理。"
                              "这不等于「子代理都失败了」，而是「没有记录」。",
                })
            out = []
            for name in sorted(os.listdir(root)):
                d = os.path.join(root, name)
                if not os.path.isdir(d):
                    continue
                item: Dict[str, Any] = {"agent_id": name, "run_dir": d}
                for fn in ("capability.json", "meta.json"):
                    fp = os.path.join(d, fn)
                    if os.path.isfile(fp):
                        try:
                            with open(fp, "r", encoding="utf-8") as f:
                                item[fn.replace(".json", "")] = json.load(f)
                        except (OSError, json.JSONDecodeError):
                            item[fn.replace(".json", "")] = None
                out.append(item)
            return self._json(200, {"subagents": out, "count": len(out)})

        # ── 完成证书（ECT）──
        # 读落盘而不是内存态：证书的用途是**事后复核**，
        # 而复核者不该需要"产生它的进程还活着"。
        # 内存态优先只是为了拿到最新的一次（可能刚签发还没刷盘）。
        if path.startswith("/api/run/") and path.endswith("/certificate"):
            rid = path.split("/")[3]
            run_dir = None
            st = RUNS.get(rid)
            if st is not None and st.session is not None:
                run_dir = st.session.run_dir
            if run_dir is None:
                run_dir = self._resolve_run_dir(rid, q)
            if run_dir is None:
                return self._json(404, {"error": "run not found"})
            cert_path = os.path.join(run_dir, "certificate.json")
            if not os.path.isfile(cert_path):
                # 三种可能，都必须说清楚，不能混成"没有证书"：
                #   · 运行还没收尾（正常）
                #   · 证书功能被关掉
                #   · 运行在那个功能上线前
                return self._json(404, {
                    "error": "no certificate",
                    "detail": "这次运行没有证书记录：可能尚未收尾、"
                              "或运行时 certificate 被关闭。"
                              "这不等于「任务未完成」。",
                })
            with open(cert_path, "r", encoding="utf-8") as f:
                payload = json.load(f)

            # 顺带跑一次独立重放 —— 只回"证书说它没问题"是不够的，
            # 必须让调用方能自己确认"证据真的在事件流里"。
            try:
                from harness.certificate import Certificate as _Cert, Claim as _Claim, \
                    Evidence as _Ev, verify_certificate
                raw = payload.get("certificate") or {}
                cert = _Cert(
                    task_digest=raw.get("task_digest", ""),
                    ledger_digest=raw.get("ledger_digest", ""),
                    status=raw.get("status", ""),
                    digest=raw.get("digest", ""))
                for c in raw.get("claims") or []:
                    cert.claims.append(_Claim(
                        kind=c.get("kind", ""), subject=c.get("subject", ""),
                        evidence=[_Ev(seq=int(e.get("seq") or 0),
                                      tool=e.get("tool", ""),
                                      ok=bool(e.get("ok")),
                                      exit_code=e.get("exit_code"),
                                      files_changed=list(e.get("files_changed") or []))
                                  for e in (c.get("evidence") or [])]))
                events = []
                ev_path = os.path.join(run_dir, "events.jsonl")
                if os.path.isfile(ev_path):
                    with open(ev_path, "r", encoding="utf-8") as ef:
                        for ln in ef:
                            ln = ln.strip()
                            if not ln:
                                continue
                            try:
                                events.append(json.loads(ln))
                            except json.JSONDecodeError:
                                continue
                ws_guess = os.path.dirname(os.path.dirname(os.path.dirname(run_dir)))
                payload["verification"] = verify_certificate(cert, events, ws_guess)
            except Exception as e:      # noqa: BLE001
                # 重放失败要**说出来**，不能静默当成"没问题"。
                payload["verification"] = {
                    "ok": False,
                    "error": f"重放失败：{type(e).__name__}: {e}",
                    "detail": "无法确认证据是否成立，请勿把这份证书当作通过。",
                }
            return self._json(200, payload)

        # 静态：根路径给前端
        if path in ("/", "/index.html"):
            return self._static("index.html")
        return self._static(path)

    def do_POST(self):
        # 与 do_GET 同样的兜底：任何异常都必须变成可见的错误响应，
        # 不能是空响应（空响应的症状里不含任何诊断信息）。
        try:
            return self._route_post()
        except Exception as e:      # noqa: BLE001
            import traceback
            try:
                self._json(500, {
                    "error": f"{type(e).__name__}: {e}",
                    "path": self.path,
                    "traceback": traceback.format_exc()[-1200:],
                })
            except Exception:
                pass

    def _route_post(self):
        u = urlparse(self.path)
        path = u.path
        body = self._read_body()

        if path == "/api/run":
            ws = body.get("workspace") or default_workspace()
            prompt = (body.get("prompt") or "").strip()
            if not prompt:
                return self._json(400, {"error": "prompt 不能为空"})
            cfg = LoopConfig(
                max_iterations=int(body.get("max_iterations") or 25),
                lint_after_edit=bool(body.get("lint_after_edit", True)),
                inject_status=bool(body.get("inject_status", True)),
                # 完成前检查默认开启（LangChain 实测 +13.7pp）。
                # 留出关闭入口，便于排查"是不是这道闸门拦的"。
                preflight_enabled=bool(body.get("preflight_enabled", True)),
                preflight_max_rounds=int(body.get("preflight_max_rounds") or 2),
                capability_ledger=bool(body.get("capability_ledger", True)),
            )
            try:
                st = create_run(
                    workspace=ws, prompt=prompt,
                    sandbox=body.get("sandbox") or "workspace-write",
                    approval=body.get("approval") or "on-request",
                    model_name=body.get("model"),
                    endpoint=body.get("endpoint"),
                    offline_script=body.get("offline_script"),
                    goal_conditions=body.get("goal_conditions"),
                    approval_timeout_s=float(body.get("approval_timeout_s") or 300.0),
                    config=cfg,
                )
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            start_run(st, prompt)
            return self._json(200, {"run_id": st.run_id,
                                    "run_dir": st.session.run_dir,
                                    "prefix_hash": st.session.meta.prefix_hash,
                                    "tool_order": st.session.meta.tool_order,
                                    "model": st.session.meta.model,
                                    "provider": st.session.meta.provider,
                                    "live": bool(st.endpoint and st.endpoint.configured)})

        # ── 工作区写操作（只有"新建"，且只建空的）──
        # POST 而不是 GET：它会改变磁盘状态。用 GET 的话，一个预取、
        # 一次误刷新、一个 <img src> 都能触发建文件。
        if path == "/api/workspace/create":
            ws = body.get("workspace") or default_workspace()
            # 在系统目录里"新建一个空目录"听起来无害，但这是把工作区
            # 引到系统路径上的第一步。在入口就拒，比事后再解释便宜。
            blocked = _dangerous_workspace(ws)
            if blocked:
                return self._json(400, {"ok": False, "error": "dangerous_workspace",
                                        "detail": blocked})
            r = _create_workspace_entry(ws, body.get("path") or "",
                                        body.get("kind") or "file")
            # 拒绝用 400 + 原因码：前端能把"为什么不行"直接摆到字段下面
            return self._json(200 if r.get("ok") else 400, r)

        # ── 模型接入配置 ──
        # 只有三个字段。GET 返回掩码后的形态（**绝不回传明文 key**），
        # POST 保存，/test 用当前配置做一次真实连通性自检。
        if path == "/api/config":
            ws = body.get("workspace") or default_workspace()
            incoming = body.get("endpoint") or {}
            current = load_config(ws)
            # api_key 语义：传了新值就替换；传空字符串显式清除；
            # **不传**则沿用已有 —— 否则前端每次保存都要重填 key。
            merged = EndpointConfig(
                base_url=incoming.get("base_url", current.base_url) or "",
                api_key=(incoming["api_key"] if "api_key" in incoming else current.api_key) or "",
                model=incoming.get("model", current.model) or "",
                requires_reasoning_replay=bool(
                    incoming.get("requires_reasoning_replay", current.requires_reasoning_replay)),
            )
            p = save_config(ws, merged)
            return self._json(200, {"ok": True, "path": p, "endpoint": merged.to_public()})

        if path == "/api/config/test":
            ws = body.get("workspace") or default_workspace()
            # 优先用请求里带的（用户可能还没保存就想先试），否则用落盘的
            ep = resolve_endpoint(ws, body.get("endpoint"))
            model = HttpModel(ep)
            report = model.selfcheck(timeout_s=float(body.get("timeout_s") or 20.0))
            report["endpoint"] = ep.to_public()
            return self._json(200 if report.get("ok") else 200, report)

        if path == "/api/config/clear":
            ws = body.get("workspace") or default_workspace()
            removed = clear_config(ws)
            return self._json(200, {"ok": True, "removed": removed,
                                    "endpoint": EndpointConfig().to_public()})

        # ── 把一份模型配置沿用到另一个工作区 ──
        # 为什么需要它：配置存在 `<workspace>/.crucible/config.json`，
        # 所以"换工作区"必然等于"这个新目录还没配过模型"。用户刚配好就去开新项目，
        # 一进去看到"未配置"，第一反应是"我的 key 丢了"。
        #
        # 刻意**不做自动沿用**：自动的话，用户在新项目里以为用的是 A 模型，
        # 其实沿用了上个项目的 B —— 那正是「配置来源必须单一且可见」要避免的。
        # 这里只在用户显式点"沿用"时复制，并且**只接受同一台机器上的路径**。
        if path == "/api/config/copy":
            src = os.path.realpath(body.get("from") or "")
            dst = os.path.realpath(body.get("to") or "")
            if not src or not dst or src == dst:
                return self._json(400, {"ok": False, "error": "bad_args",
                                        "detail": "需要两个不同的工作区路径。"})
            cfg = load_config(src)
            if not cfg.configured:
                return self._json(200, {
                    "ok": False, "error": "source_not_configured",
                    "detail": "来源工作区没有可沿用的配置（%s）。"
                              % config_path(src)})
            target = config_path(dst)
            try:
                # 目标必须是**已存在**的目录：不替用户把整个目录树建出来，
                # 否则拼错一个路径就等于在别处悄悄留下一份带明文 key 的文件。
                if not os.path.isdir(dst):
                    raise OSError("目标目录不存在：%s" % dst)
                save_config(dst, cfg)
            except OSError as e:
                return self._json(200, {"ok": False, "error": "write_failed",
                                        "detail": str(e)})
            return self._json(200, {"ok": True, "config_path": target,
                                    "endpoint": load_config(dst).to_public()})

        if path.startswith("/api/run/") and path.endswith("/rollback"):
            rid = path.split("/")[3]
            st = RUNS.get(rid)
            cs = None
            if st is not None and st.session is not None:
                cs = st.session.changesets
            else:
                # 进程重启后 RUNS 清空 —— 但变更集在盘上，
                # 回滚**不依赖产生它的进程还活着**（这正是它要解决的问题）。
                run_dir = self._resolve_run_dir(rid, q)
                if run_dir is None:
                    return self._json(404, {"error": "run not found"})
                ws = body.get("workspace") or default_workspace()
                from harness.changeset import ChangeSetStore
                cs = ChangeSetStore(run_dir, ws, enabled=False)
            if cs is None:
                return self._json(404, {"error": "no changeset store for that run"})

            to_seq = body.get("to_seq")
            try:
                to_seq = int(to_seq) if to_seq is not None else None
            except (TypeError, ValueError):
                return self._json(400, {"error": "to_seq 必须是整数"})
            dry = bool(body.get("dry_run", False))
            result = cs.rollback(to_seq=to_seq, dry_run=dry)
            code = 200 if result.get("ok") else 207   # 207 = 部分成功，如实表达
            return self._json(code, result)

        if path.startswith("/api/run/") and path.endswith("/approve"):
            rid = path.split("/")[3]
            st = RUNS.get(rid)
            if st is None:
                return self._json(404, {"error": "run not found"})
            call_id = body.get("call_id") or ""
            decision = body.get("decision") or "rejected"
            remember = bool(body.get("remember", False))

            # 该 call_id 必须真的是待决的那一个 —— 否则回灌的是幽灵审批。
            req = st.loop.router.pending.get(call_id)
            if req is None:
                return self._json(404, {
                    "error": "no pending approval for that call_id",
                    "hint": "审批可能已超时、已被拒绝，或该调用根本没进入闸门。",
                })

            # 先把决定写进 router（记录 remember、清理 pending），
            # 再唤醒被阻塞的工具线程 —— 顺序不能反，否则线程会读到空 pending。
            st.loop.router.resolve_approval(call_id, decision, remember)
            st.deliver_approval(decision, remember)
            return self._json(200, {"ok": True, "decision": decision,
                                    "call_id": call_id})

        return self._json(404, {"error": "not found"})

    # ---------- SSE ----------
    def _sse(self, rid: str) -> None:
        st = RUNS.get(rid)
        if st is None:
            return self._json(404, {"error": "run not found"})

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        # 回放已有事件（重连补偿）—— 书 §5：事件流要能重连补偿
        seen = 0
        try:
            while True:
                try:
                    item = st.queue.get(timeout=15)
                except queue.Empty:
                    # 心跳：既是保活，也是「活性信号」（书 §5 的看门狗原则）
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                if item is None:
                    self.wfile.write(b"event: end\ndata: {}\n\n")
                    self.wfile.flush()
                    return
                seen += 1
                payload = json.dumps(item, ensure_ascii=False)
                self.wfile.write(f"id: {item.get('seq', seen)}\n".encode())
                self.wfile.write(f"event: {item.get('type', 'message')}\n".encode())
                self.wfile.write(f"data: {payload}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def _tool_manifest() -> Dict[str, Any]:
    """给前端一份工具清单（按注册顺序），前端可据此渲染工具面板。"""
    try:
        ws = default_workspace()
        reg, _ = build_default_registry(ws)
        return {"order": reg.names(), "categories": reg.categories(),
                "specs": reg.specs()}
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════
# 工作区只读访问（文件树 / 读文件）
# ══════════════════════════════════════════════════════════════════
# 前端展示"真实工作区"需要这两个端点。设计要点：
#
# 1. **这是只读接口，不经过 policy/沙箱**。理由：它不是 agent 的动作，
#    是**用户在看自己的工作区**。把它塞进 agent 的工具链会混淆
#    "谁在动这个文件"这件事 —— 而审批闸门的存在意义正是回答这个问题。
#    但正因为它绕过了 policy，**路径校验必须自己做，且做得更严**。
# 2. **必须防穿越**：任何 `..`、绝对路径、符号链接逃逸都要挡住。
#    这里用 realpath 后做前缀比对，而不是字符串前缀匹配 ——
#    后者会被 `..` 绕过（`/ws/../etc` 的字符串前缀仍是 `/ws`）。
# 3. **列目录时跳过噪声**，但要**如实告诉前端跳了什么**：
#    静默省略会让人以为"这个仓库就这么点东西"。

_SKIP_DIRS = {".git", "node_modules", ".next", "dist", "build", ".venv",
              "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
              ".idea", ".vscode", ".DS_Store"}
_TEXT_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".md", ".txt",
             ".css", ".html", ".htm", ".yml", ".yaml", ".toml", ".ini",
             ".cfg", ".sh", ".bat", ".ps1", ".sql", ".java", ".go", ".rs",
             ".c", ".h", ".cpp", ".hpp", ".rb", ".php", ".xml", ".csv"}


def _safe_join(workspace: str, rel: str) -> Optional[str]:
    """把相对路径安全地拼到工作区下。

    返回 None 表示**拒绝**。拒绝的情形一律视为"不存在"，
    并且**不区分**"逃逸被拒"与"文件不存在" —— 否则这个端点就变成了
    "探测工作区之外有什么"的工具（存在性本身就是信息）。
    """
    if not rel:
        return os.path.realpath(workspace)
    if os.path.isabs(rel):
        return None
    # 拒绝任何显式向上跳的路径段（realpath 之后还会再校验一次，
    # 这里是早退，让拒绝更明显）
    parts = [p for p in re.split(r"[\\/]+", rel) if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    ws = os.path.realpath(workspace)
    full = os.path.realpath(os.path.join(ws, *parts))
    # realpath 之后必须仍在工作区内 —— 这一条防的是符号链接逃逸：
    # 字符串前缀匹配挡不住指向外面的软链。
    if full != ws and not full.startswith(ws + os.sep):
        return None
    return full


def _list_tree(workspace: str, rel: str, depth: int, max_entries: int) -> Dict[str, Any]:
    """列一层目录（前端按需展开，不做全量递归）。

    返回当前层的直接子项。为什么只列一层：
    全量递归在大型仓库上会变成一次扫描上千文件的操作，
    而前端只需要"用户展开的那个目录"。
    """
    target = _safe_join(workspace, rel)
    if target is None or not os.path.isdir(target):
        return {"error": "not a directory", "path": rel}

    entries: List[Dict[str, Any]] = []
    skipped: List[str] = []
    try:
        names = sorted(os.listdir(target), key=lambda s: (not os.path.isdir(
            os.path.join(target, s)), s.lower()))
    except OSError as e:
        return {"error": f"listdir failed: {e}", "path": rel}

    for name in names:
        full = os.path.join(target, name)
        if name in _SKIP_DIRS:
            skipped.append(name)
            continue
        try:
            is_dir = os.path.isdir(full)
            size = 0 if is_dir else os.path.getsize(full)
        except OSError:
            continue
        child_rel = (rel + "/" + name).lstrip("/")
        # 只标"能不能当文本打开"，不读内容 —— 读内容是另一个端点的活。
        # 无扩展名的文件（.gitignore / Makefile / LICENSE）**也算文本候选**：
        # 实测踩到，早期只按扩展名判 → .gitignore 被判成二进制，
        # 而它恰恰是最常被打开来看的文件之一。
        ext = "" if is_dir else os.path.splitext(name)[1].lower()
        entries.append({
            "name": name,
            "path": child_rel,
            "kind": "dir" if is_dir else "file",
            "size": size,
            "ext": ext,
            "text": is_dir or ext in _TEXT_EXT or ext == "",
        })
        if len(entries) >= max_entries:
            break

    return {
        "path": rel,
        "entries": entries,
        "truncated": len(entries) >= max_entries,
        # **跳过的目录要如实报出来**：静默省略会让人以为仓库就这么点东西
        "skipped": skipped,
        "depth": depth,
    }


def _read_workspace_file(workspace: str, rel: str, max_bytes: int) -> Dict[str, Any]:
    """读一个工作区内的文本文件。

    行号在**服务端**加，不在前端加 —— 因为行号必须与实际内容用同一次
    读取算出来；前端自己 split 会在"正好卡在截断处"时错位。
    """
    target = _safe_join(workspace, rel)
    if target is None or not os.path.isfile(target):
        return {"error": "file not found", "path": rel}
    try:
        size = os.path.getsize(target)
        with open(target, "rb") as f:
            raw = f.read(max_bytes)
    except OSError as e:
        return {"error": f"read failed: {e}", "path": rel}

    truncated = size > len(raw)
    # 解码：优先 UTF-8，失败退回本地代码页（Windows 上中文源码常见 GBK）。
    # 直接 `errors="replace"` 也行，但那会把内容变成一堆问号，
    # 而"这个文件不是 UTF-8"本身是有用的信息。
    enc = "utf-8"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gbk")
            enc = "gbk"
        except UnicodeDecodeError:
            text = raw.decode("utf-8", "replace")
            enc = "utf-8(replace)"

    lines = text.split("\n")
    return {
        "path": rel,
        "size": size,
        "encoding": enc,
        "truncated": truncated,
        "line_count": len(lines),
        "lines": lines,
    }


# ── 新建文件 / 新建目录 ──
#
# 这是本仓库**唯一**能给工作区写东西的浏览器接口，所以能力刻意压到最小：
#   · 只能建"空的" —— 写内容归 agent 的工具链（走沙箱 + 审批）
#   · 不能覆盖 —— 已存在一律拒绝，不提供 force
#   · 不能删除、不能重命名
# 最坏情况是在工作区里多一个空文件 / 空目录，而不是代码被改写。
#
# 这不是洁癖。这条路径绕过了 policy（理由同上面的只读接口：它是用户在
# 操作自己的工作区，不是 agent 的动作），所以"能做的"必须比"想做的"更少 ——
# 一旦允许写内容，它就变成了绕过审批闸门的后门。

_BAD_NAME_CHARS = '<>:"|?*'
# Windows 的保留设备名。连 CON.txt 这种带扩展名的写法也不合法，
# 所以按"点号前的部分"比对。
_RESERVED_NAMES = ({"con", "prn", "aux", "nul"}
                   | {"com%d" % i for i in range(1, 10)}
                   | {"lpt%d" % i for i in range(1, 10)})

_NEW_KIND_CN = {"file": "文件", "dir": "目录"}


def _create_workspace_entry(workspace: str, rel: str, kind: str) -> Dict[str, Any]:
    """在工作区内新建一个空文件或空目录。

    返回 `{"ok": True, ...}`，或 `{"ok": False, "error": <码>, "detail": <人话>}`。

    **错误必须说清是哪一步不行**。「创建失败」这种话对用户没有任何用处 ——
    是名字非法？还是上级目录不存在？还是已经有了？三种情况的下一步动作完全不同。
    """
    def bad(code: str, detail: str) -> Dict[str, Any]:
        return {"ok": False, "error": code, "detail": detail, "path": rel}

    if kind not in _NEW_KIND_CN:
        return bad("bad_kind", "kind 只能是 file 或 dir。")

    rel = (rel or "").strip()
    if not rel:
        return bad("empty_name", "名字不能为空。")
    if os.path.isabs(rel) or rel.startswith(("/", "\\")):
        return bad("absolute_path", "只接受工作区内的相对路径，不能从根目录开始。")

    parts = [p for p in re.split(r"[\\/]+", rel) if p not in ("", ".")]
    if not parts:
        return bad("empty_name", "名字不能为空。")
    if any(p == ".." for p in parts):
        return bad("traversal", "路径里不能有 ..（不能建到工作区外面去）。")

    name = parts[-1]
    if any(ch in _BAD_NAME_CHARS for ch in name):
        return bad("bad_name",
                   '名字里不能有这些字符：< > : " | ? *')
    if any(ord(ch) < 32 for ch in name):
        return bad("bad_name", "名字里不能有控制字符（换行、制表符等）。")
    if name != name.rstrip(" ."):
        # 这一条不是为了好看：系统会**悄悄**去掉结尾的空格与点，
        # 建出来的名字和用户输入的不一致，而用户以为自己建对了。
        return bad("bad_name",
                   "名字结尾不能是空格或点。系统会把它悄悄去掉，"
                   "建出来的名字就和你输入的不一样了。")
    if name.split(".")[0].lower() in _RESERVED_NAMES:
        return bad("reserved_name",
                   "「%s」是系统保留的设备名（CON / PRN / AUX / NUL / COM1…），"
                   "这类名字建不出来。换一个。" % name)

    target = _safe_join(workspace, "/".join(parts))
    if target is None:
        return bad("outside_workspace",
                   "这个路径会落到工作区外面（或经过一个指向外面的符号链接）。已拒绝。")

    parent = os.path.dirname(target)
    if not os.path.isdir(parent):
        # 不自动 makedirs 上级：那会让"路径写错"变成"悄悄建了一串目录"，
        # 用户以为建在 A 处，实际建在 B 处。
        return bad("no_parent",
                   "上一级目录不存在。先在文件树里选中一个已存在的目录再新建。")

    if os.path.exists(target):
        # 已存在就**不动它**。省掉这条，"新建"就会静默变成"覆盖"。
        what = "目录" if os.path.isdir(target) else "文件"
        return bad("already_exists", "这里已经有一个同名%s了。" % what)

    try:
        if kind == "dir":
            os.mkdir(target)
        else:
            # 'x' 模式：存在即失败。用它而不是先 exists 再写，
            # 是为了不留"检查与写入之间"的竞态窗口。
            with open(target, "x", encoding="utf-8"):
                pass
    except FileExistsError:
        return bad("already_exists", "已经有同名项了（刚刚被别的进程建了）。")
    except OSError as e:
        return bad("io_error", "系统拒绝了这个操作：%s" % e)

    return {
        "ok": True,
        "path": "/".join(parts),
        "kind": kind,
        "name": name,
        "parent": "/".join(parts[:-1]),
        "detail": "已创建%s：%s" % (_NEW_KIND_CN[kind], "/".join(parts)),
    }


def _windows_drives() -> List[str]:
    """枚举盘符。

    用 `GetLogicalDrives` 而不是逐个 `os.path.exists("A:\\")`：
    后者在有不存在的软驱时会让整个请求卡几秒（访问硬件）。
    """
    if os.name != "nt":
        return ["/"]
    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        return [chr(ord("A") + i) + ":\\" for i in range(26) if mask & (1 << i)]
    except (AttributeError, OSError):
        # 拿不到就退回逐个探测 —— 慢一点，但不会让整个选择器不可用
        return [chr(ord("A") + i) + ":\\"
                for i in range(26) if os.path.isdir(chr(ord("A") + i) + ":\\")]


# 目录里有这些东西，基本可以认定"这是一个项目"（而不是一个随便的文件夹）。
_PROJECT_MARKERS = (".git", "package.json", "pyproject.toml", "setup.py",
                    "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
                    "CMakeLists.txt", "AGENTS.md", "README.md")


def _shortcuts() -> List[Dict[str, str]]:
    """选择器里的"快捷位置"。只列真实存在的。"""
    out: List[Dict[str, str]] = []
    home = os.path.expanduser("~")
    cands = [("主目录", home)]
    for label, sub in (("桌面", "Desktop"), ("文档", "Documents"),
                       ("下载", "Downloads")):
        cands.append((label, os.path.join(home, sub)))
    for label, p in cands:
        if p and os.path.isdir(p):
            out.append({"name": label, "path": os.path.realpath(p)})
    return out


def _fs_list(path: str = "", max_entries: int = 800) -> Dict[str, Any]:
    """列本机的一个目录，供"选择工作区"使用。

    ## 为什么需要它

    之前工作区只能由 `--workspace` 或 URL 的 `?workspace=` 指定 ——
    也就是**只能从命令行切**。要换项目就得重启服务。这不是"限制得比较保守"，
    是缺了一块功能：用户没法新建项目再在里面干活。

    ## 能力刻意压到最小

    · **只读**：列目录，不建、不删、不改。要建空目录走
      `/api/workspace/create`（那一个端点管写，方便单独审查）。
    · **只列目录**，不列文件：这个端点的用途是"选一个工作区根"，
      列出文件名既没用又多泄露。
    · 不跟随符号链接判目录（`follow_symlinks=False`），避免软链环。

    ## 错误要能继续走

    目录不存在 / 没权限时**仍然返回 parent 与 drives** ——
    让选择器能退回去。只回一个 400 的话，前端会卡在一个走不出去的界面里。
    """
    drives = _windows_drives()
    raw = (path or "").strip().strip('"').strip("'")
    if not raw:
        raw = os.path.expanduser("~")
    # 用户可能手输了一个反斜杠结尾或重复分隔符；realpath 会把它们归一。
    # 注意 realpath 在路径不存在时**不报错**，只做字符串归一 —— 所以后面必须再判存在。
    target = os.path.realpath(os.path.expandvars(os.path.expanduser(raw)))

    out: Dict[str, Any] = {
        "path": target,
        # parent 为 None 表示已经在最顶层（盘符根 / 根目录），前端据此禁用"上一级"
        "parent": None,
        "drives": drives,
        "shortcuts": _shortcuts(),
        "entries": [],
        "truncated": False,
        "skipped_hidden": 0,
        # 默认不可用；只有成功列出来、且不是危险目录时才置 True。
        # 提前放默认值是为了让所有错误分支的返回形状**一致** ——
        # 前端读 undefined 会把"不能选"当成"能选"。
        "usable": False,
        "blocked_reason": None,
    }

    parent = os.path.dirname(target)
    if parent and parent != target:
        out["parent"] = parent

    if not os.path.exists(target):
        out["error"] = "not_found"
        out["detail"] = "这个路径不存在。"
        return out
    if not os.path.isdir(target):
        out["error"] = "not_a_dir"
        out["detail"] = "这是一个文件，不是目录。请选它所在的目录。"
        return out

    try:
        entries = []
        hidden = 0
        with os.scandir(target) as it:
            for e in it:
                try:
                    if not e.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                name = e.name
                # **只跳过点开头的隐藏目录**（.git / .venv / .crucible）：
                # 它们既不能当工作区根，进去也什么都没有。
                #
                # 刻意**不**按 _SKIP_DIRS 过滤 node_modules / dist / build ——
                # 那是索引扫描的规则，不是浏览的规则。用户在这里是主动看磁盘，
                # 藏掉目录只会让他觉得"这个文件夹是空的"。
                if name.startswith("."):
                    hidden += 1
                    continue
                full = os.path.join(target, name)
                marker = next((m for m in _PROJECT_MARKERS
                               if os.path.exists(os.path.join(full, m))), "")
                entries.append({
                    "name": name,
                    "path": full,
                    "project": marker,
                    "has_git": os.path.isdir(os.path.join(full, ".git")),
                })
                if len(entries) >= max_entries:
                    out["truncated"] = True
                    break
    except PermissionError:
        out["error"] = "permission_denied"
        out["detail"] = "没有权限读取这个目录。"
        return out
    except OSError as e:
        out["error"] = "listdir_failed"
        out["detail"] = "读不了这个目录：%s" % e
        return out

    entries.sort(key=lambda x: x["name"].lower())
    out["entries"] = entries
    out["count"] = len(entries)
    # 跳过的隐藏目录要报出个数 —— 静默省略会让人以为磁盘上就这么多东西
    out["skipped_hidden"] = hidden
    # 是否可写要如实报：不可写的目录当工作区，agent 一动手就全失败，
    # 与其让它之后报一堆写失败，不如现在就显示出来。
    out["writable"] = os.access(target, os.W_OK)
    out["has_git"] = os.path.isdir(os.path.join(target, ".git"))
    out["project"] = next((m for m in _PROJECT_MARKERS
                           if os.path.exists(os.path.join(target, m))), "")
    # 根目录不算项目。允许选中它，但要提示后果：agent 会在这个巨型目录里
    # 建索引、跑 grep —— 慢到不可用，而且 .crucible 会落在系统盘根上。
    out["is_root"] = os.path.dirname(target) == target
    # 能不能当工作区，**在浏览时就说**，不要等用户点"选这个"才报错。
    blocked = _dangerous_workspace(target)
    out["usable"] = blocked is None
    out["blocked_reason"] = blocked
    return out


def _dangerous_workspace(path: str) -> Optional[str]:
    """返回"不该当工作区"的理由，能当就返回 None。

    这不是权限控制（那是 policy 的事），而是**免得出事之后再解释**：
    把 C:\\Windows 设成工作区，agent 的写操作会直接改系统文件。
    """
    p = os.path.realpath(path).rstrip("\\/").lower()
    if not p:
        return None
    if os.name != "nt":
        for bad in ("/etc", "/usr", "/bin", "/sbin", "/boot", "/dev", "/proc"):
            if p == bad or p.startswith(bad + "/"):
                return "系统目录不能作为工作区：%s" % bad
        return None
    if len(p) == 2 and p[1] == ":":           # c: 这种盘符根
        return "盘符根目录不能作为工作区 —— 索引与搜索会扫全盘。" \
               "请选盘里的一个具体目录。"
    for bad in ("c:\\windows", "c:\\program files", "c:\\program files (x86)",
                "c:\\programdata", "c:\\$recycle.bin"):
        if p == bad or p.startswith(bad + "\\"):
            return "系统目录不能作为工作区：%s。agent 会在工作区里写文件，" \
                   "改这里会破坏系统。" % bad
    return None


# 终端白名单：只允许**只读、只读工作区、不改变系统状态**的命令。
# 分两类：
#   · 直接放行（无参数或参数已被本函数校验过）
#   · 需要额外校验参数（如 `git` 只允许 status/log/diff/branch）
_TERM_ALLOWED = {
    "ls", "dir", "cat", "type", "head", "tail", "wc", "pwd", "cd",
    "find", "grep", "rg", "tree",
    "python", "python3", "node", "npm", "pip",  # 仅 --version 形态
    "echo", "date", "whoami", "hostname",
    "git", "wc",
    "help", "clear",
}
# 元字符一律拒绝。它们是把"一条只读命令"变成"任意命令链"的唯一手段，
# 所以挡住元字符比逐个白名单命令更根本。
_TERM_FORBIDDEN = set("|&;><`$()\n\r\t")
# python/node/npm 这类解释器只允许"查版本" ——
# 允许 `python -c "..."` 等于开放任意代码执行。
_VERSION_ONLY = {"python", "python3", "node", "npm", "pip"}


def _run_readonly_command(workspace: str, cmd: str) -> Dict[str, Any]:
    """在受控白名单下执行一条只读命令。

    ## 为什么需要这么严

    这是**浏览器发起的**命令执行，不是 agent 的动作。harness 的沙箱与审批
    闸门保护的是 agent，管不到这条路径；而开放任意命令会把服务变成
    一个远程 shell（本机任何进程、任何浏览器扩展都能用）。

    三道限制（缺一不可）：
      1. **元字符拒绝** —— `| & ; > < ` $ ( )` 与换行
      2. **首词白名单** —— 不在名单里直接拒
      3. **参数形态校验** —— 解释器类只允许 `--version`；`git` 只允许
         status/log/diff/branch/show；`find`/`grep` 禁 `-exec`/`-delete`

    ## 拒绝时给原因

    和这个项目一贯的做法一致：不静默失败。"为什么不让跑"本身是有用的信息
    （用户会知道这是只读终端，而不是终端坏了）。
    """
    ws = os.path.realpath(workspace)
    raw = (cmd or "").strip()
    if not raw:
        return {"ok": False, "code": "empty", "detail": "命令为空。",
                "workspace": ws}

    if any(ch in _TERM_FORBIDDEN for ch in raw):
        bad = sorted({ch for ch in raw if ch in _TERM_FORBIDDEN})
        return {"ok": False, "code": "metachar", "workspace": ws,
                "detail": "命令包含被禁止的 shell 元字符："
                          + " ".join(repr(c) for c in bad)
                          + "。这个终端是只读的，不支持管道、重定向与命令串联。"}

    parts = raw.split()
    head = parts[0].lower()
    if head not in _TERM_ALLOWED:
        return {"ok": False, "code": "not_allowed", "workspace": ws,
                "detail": f"`{head}` 不在只读白名单里。"
                          "允许的命令：" + "、".join(sorted(_TERM_ALLOWED))}

    args = parts[1:]
    low_args = [a.lower() for a in args]

    if head in _VERSION_ONLY:
        if not any(a in ("--version", "-v", "-V") for a in args):
            return {"ok": False, "code": "not_allowed", "workspace": ws,
                    "detail": f"`{head}` 只允许查版本（--version）——"
                              "允许执行任意代码会把这个只读终端变成代码执行入口。"}

    if head == "git":
        sub = low_args[0] if low_args else ""
        if sub not in ("status", "log", "diff", "branch", "show", "rev-parse"):
            return {"ok": False, "code": "not_allowed", "workspace": ws,
                    "detail": "`git` 只允许 status / log / diff / branch / show / rev-parse。"
                              "写操作（commit/checkout/reset/…）不在这里做。"}

    for bad_arg in ("-exec", "-execdir", "-delete", "-ok", "rm", "-fdelete"):
        if bad_arg in low_args:
            return {"ok": False, "code": "not_allowed", "workspace": ws,
                    "detail": f"参数 `{bad_arg}` 会改变文件系统，已拒绝。"}

    # ── 读文件内容的命令：**不经 shell，直接走读文件函数** ──
    #
    # 这是实测踩出来的修法。原因值得写清楚：
    # Windows 上 shell 的输出按**控制台代码页（GBK/936）**解码，而且用了
    # `errors="replace"` —— 读 UTF-8 文件时字节流被 GBK 解错，无法解码的
    # 字节变成 U+FFFD 替换字符。**信息在这一步就永久丢了**，
    # 事后 `encode("gbk").decode("utf-8")` 也救不回来（实测确实救不回来）。
    #
    # 所以不能事后转码，只能**不走那条会损坏数据的路径**：
    # `cat`/`head`/`tail` 本质就是"读文件内容"，而
    # `_read_workspace_file` 本来就按 UTF-8 → GBK 的顺序试解码，是对的。
    # 委托给它，等于让两条路径共用同一个正确的实现。
    #
    # 判据：首词在白名单里且带一个文件参数。`head -n 20 f` 这类参数
    # 也要能处理，所以先剥掉以 `-` 开头的选项。
    if head in ("cat", "type", "head", "tail"):
        # 剥掉选项**及其数值**：`head -n 3 file` 里的 `3` 是选项参数，
        # 不是文件。实测踩到：早期只跳过 `-` 开头的项，于是 `3` 被当成
        # 第二个文件参数，报"一次只允许读一个文件" —— 把一条合法命令
        # 拒掉，而且理由完全指错方向。
        file_args = []
        skip_next = False
        for a in args:
            if skip_next:
                skip_next = False
                continue
            if a in ("-n", "--lines"):
                skip_next = True
                continue
            if a.startswith("-"):
                continue          # 其它选项（-v / --number 等）直接忽略
            file_args.append(a)

        if len(file_args) == 1:
            limit = 64 * 1024
            n = 20
            if head in ("head", "tail"):
                for i, a in enumerate(args):
                    if a in ("-n", "--lines") and i + 1 < len(args):
                        try:
                            n = max(1, int(args[i + 1]))
                        except ValueError:
                            n = 20
                    elif a.startswith("-") and a.lstrip("-").isdigit():
                        n = max(1, int(a.lstrip("-")))
                limit = max(64 * 1024, n * 8192)
            fread = _read_workspace_file(ws, file_args[0], limit)
            if fread.get("error"):
                return {"ok": False, "code": "read_failed", "workspace": ws,
                        "detail": fread["error"], "command": raw}
            lines = fread.get("lines") or []
            if head == "tail":
                lines = lines[-n:]
            elif head == "head":
                lines = lines[:n]
            return {
                "ok": True, "workspace": ws, "command": raw,
                "exit_code": 0,
                "text": "\n".join(lines),
                "encoding": fread.get("encoding"),
                "truncated": bool(fread.get("truncated")),
                "restricted": True,
                # 说明这条命令是**直接读文件**完成的，没经过 shell ——
                # 否则用户看到输出与 shell 行为不一致会困惑
                "via": "read_file（后端直读，保证编码正确）",
            }
        if len(file_args) > 1:
            return {"ok": False, "code": "not_allowed", "workspace": ws,
                    "detail": f"{head} 一次只允许读一个文件。"}

    shell = ShellTool(ws)
    try:
        res = shell({"command": raw, "timeout_ms": 15000})
        text = res.text
        return {
            "ok": bool(res.ok),
            "workspace": ws,
            "command": raw,
            "exit_code": res.exit_code,
            "text": text,
            "truncated": res.truncated,
            # shell 路径的输出按控制台代码页解码（对 dir/git status 这类正确）
            "encoding": "控制台代码页（shell 输出）",
            "restricted": True,
        }
    except Exception as e:      # noqa: BLE001
        return {"ok": False, "code": "exec_failed", "workspace": ws,
                "detail": f"{type(e).__name__}: {e}"}
    finally:
        try:
            shell.reset()
        except Exception:
            pass


def _workspace_search(workspace: str, pattern: str, q: Dict[str, List[str]]) -> Dict[str, Any]:
    """在工作区内按内容搜索。

    ## 复用 agent 的 grep 工具，而不是另写一套

    两套实现迟早会漂移，而漂移的后果是"用户搜到的结果与 agent 看到的
    不一样" —— 那是最难解释的一类不一致。所以这里直接调注册表里的 grep。

    返回结构里带 `engine` 字段，明确说明是谁搜的；将来换实现时
    前端能据此分辨结果来源（而不是以为搜索算法悄悄变了）。
    """
    ws = os.path.realpath(workspace)
    if not os.path.isdir(ws):
        return {"error": "workspace not found", "workspace": ws}

    try:
        limit = int((q.get("limit") or ["200"])[0])
    except ValueError:
        limit = 200
    limit = max(1, min(limit, 2000))

    reg, shell = build_default_registry(ws)
    try:
        tool = reg.tool("grep")
        if tool is None:
            return {"error": "grep tool unavailable",
                    "detail": "当前注册表里没有 grep 工具，无法搜索。"}
        # 直接调 handler（不经过 router）—— 因为 router 会走审批闸门，
        # 而**用户在前端点搜索不该被审批拦下**：这不是 agent 的动作。
        # 这也正是这个端点是只读接口的原因（见上方说明）。
        res = tool.handler({"pattern": pattern, "limit": limit})
    except Exception as e:      # noqa: BLE001
        return {"error": f"search failed: {type(e).__name__}: {e}"}
    finally:
        try:
            shell.reset()
        except Exception:
            pass

    # grep 工具的返回是带行号的文本。这里**不解析文本**——
    # 解析会引入一类只在特定格式下才错的 bug。
    # 直接把原文交给前端，由前端按行渲染（前端已经有一套同样的行渲染）。
    return {
        "query": pattern,
        "workspace": ws,
        "engine": "grep（与 agent 用的是同一个工具）",
        "ok": bool(getattr(res, "ok", False)),
        "lines": [l for l in (getattr(res, "text", "") or "").split("\n") if l.strip()],
        "truncated": bool(getattr(res, "truncated", False)),
    }


def _workspace_git_status(workspace: str) -> Dict[str, Any]:
    """工作区 git 改动统计。

    ## 这是状态栏"诊断"的**真实来源**

    原来状态栏写着"N 错误 / M 警告"，那是编的 —— harness 不做 lint 与类型检查，
    所以那两个数字没有任何来源。但**"工作区改了多少东西"是真实可得的**，
    而且对 agent 类界面更有用（agent 刚改了什么一眼可见）。

    所以这个端点返回 diff 统计而不是"诊断"，前端据此把标签从"错误/警告"
    改成"改动"，并明确说明没有诊断来源。

    ## 判据全部来自 git 本身，不猜

    用 `git status --porcelain` 拿状态码（这是机器可读格式，不像
    `git status` 的人类输出那样需要解析），用 `git diff --numstat` 拿增删行数。
    **不解析人类可读输出** —— 那会随 git 版本与语言环境变化。

    非 git 工作区如实返回 `is_repo: false`，不报 0 个改动
    （"不在仓库里"与"没有改动"不是一回事）。
    """
    ws = os.path.realpath(workspace)
    if not os.path.isdir(ws):
        return {"error": "workspace not found", "workspace": ws}
    if not os.path.isdir(os.path.join(ws, ".git")):
        return {"is_repo": False, "workspace": ws,
                "detail": "这个工作区不是 git 仓库，因此没有改动统计。"
                          "这不等于「没有改动」。"}

    shell = ShellTool(ws)
    try:
        # 分支：用 rev-parse 拿短名（比解析 `git status` 第一行可靠）
        br = shell({"command": "git rev-parse --abbrev-ref HEAD", "timeout_ms": 8000})
        branch = (br.text or "").strip().splitlines()
        branch = branch[0].strip() if branch else ""

        # 状态：--porcelain 是**机器可读**格式（每行两字母状态码 + 路径）
        st = shell({"command": "git status --porcelain", "timeout_ms": 12000})
        rows = [l for l in (st.text or "").splitlines() if l.strip()]
        staged, unstaged, untracked, renamed = [], [], [], []
        for line in rows:
            if len(line) < 4:
                continue
            code, path = line[:2], line[3:].strip()
            if code == "??":
                untracked.append(path)
            else:
                if code[0] not in (" ", "?"):
                    staged.append(path)
                if len(code) > 1 and code[1] not in (" ", "?"):
                    unstaged.append(path)
                if "R" in code:
                    renamed.append(path)

        # 增删行数：--numstat 给 "added<TAB>deleted<TAB>path"，同样机器可读
        num = shell({"command": "git diff --numstat HEAD", "timeout_ms": 12000})
        added = deleted = 0
        files_with_diff = 0
        for line in (num.text or "").splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            try:
                added += int(parts[0])
                deleted += int(parts[1])
            except ValueError:
                continue        # 二进制文件的增删是 "-"，跳过而不是当 0 累加
            files_with_diff += 1

        return {
            "is_repo": True,
            "workspace": ws,
            "branch": branch,
            "changed": len(rows),
            "staged": len(staged),
            "unstaged": len(unstaged),
            "untracked": len(untracked),
            "renamed": len(renamed),
            "added_lines": added,
            "deleted_lines": deleted,
            "files_with_diff": files_with_diff,
            "paths": [l[3:].strip() for l in rows[:50]],
            # 明确声明这不是"诊断"
            "note": "这是 git 改动统计，不是 lint/类型检查结果。"
                    "harness 不做代码诊断。",
        }
    except Exception as e:      # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}", "workspace": ws}
    finally:
        try:
            shell.reset()
        except Exception:
            pass


def _workspace_info(workspace: str) -> Dict[str, Any]:
    """工作区概要。

    ## 只报**能可靠得到**的量

    文件计数用**有限深度**扫描（默认 4 层、上限 20000）：
    拒不设上限会让一个巨大的 node_modules 把端点拖成超时。

    ## 索引状态必须如实报（这里曾是一句过期的话）

    早期这里写死 `indexed: False` + 「当前 harness 没有代码索引」。
    后来 `harness/index.py` 建好了，这句就成了**与同一屏上的状态栏
    自相矛盾**的假话（底栏说"没有代码索引"，状态栏说"1336 符号 · 53 文件"）。
    说"没有"和说"有"一样都是断言，过期了同样要改。现在按实际状态报：
    内存索引 → 落盘缓存 → 都没有才是"尚未建立"。
    """
    ws = os.path.realpath(workspace)
    if not os.path.isdir(ws):
        return {"error": "workspace not found", "workspace": ws}

    files = 0
    dirs = 0
    by_ext: Dict[str, int] = {}
    truncated = False
    max_files = 20000

    for root, subdirs, filenames in os.walk(ws):
        rel = os.path.relpath(root, ws)
        if rel.count(os.sep) >= 4:
            subdirs[:] = []
            continue
        # 原地过滤噪声目录，避免走进 node_modules 这类巨型目录
        subdirs[:] = [d for d in subdirs if d not in _SKIP_DIRS and d != ".crucible"]
        dirs += len(subdirs)
        for fn in filenames:
            files += 1
            ext = os.path.splitext(fn)[1].lower() or "(无扩展名)"
            by_ext[ext] = by_ext.get(ext, 0) + 1
            if files >= max_files:
                truncated = True
                break
        if truncated:
            break

    git_head = None
    head_path = os.path.join(ws, ".git", "HEAD")
    if os.path.isfile(head_path):
        try:
            with open(head_path, "r", encoding="utf-8") as f:
                ref = f.read().strip()
            git_head = ref.replace("ref: refs/heads/", "") or ref
        except OSError:
            git_head = None

    top = sorted(by_ext.items(), key=lambda kv: -kv[1])[:8]

    # ── 索引状态：按实际状态报，不再写死「没有」──
    # 只在**已经建过**的前提下读缓存：info 是页面首屏就会调的端点，
    # 在这里 build_index 会让"看个文件数"变成一次全量扫描。
    idx = _INDEX_CACHE.get(ws)
    if idx is None:
        cache_path = os.path.join(ws, ".crucible", "index.json")
        if os.path.isfile(cache_path):
            idx = load_index(cache_path)
            if idx is not None:
                _INDEX_CACHE[ws] = idx            # 读到了就留着，别反复解析
    if idx is not None:
        n_files = len(idx.files)
        n_syms = sum(len(v) for v in idx.symbols.values())   # 定义**处数**
        n_names = len(idx.symbols)                           # 不同**名字**数
        indexed = n_files > 0
        # 带上构建时间：数字与时间都可核对，读者才不用无条件相信这句话。
        # 索引**会过期**（改文件不重建），所以"有索引"不等于"索引是最新的"——
        # 过期判定归 /api/workspace/index/query 的 stale 字段，这里不重复下结论。
        try:
            built = time.strftime("%m-%d %H:%M", time.localtime(idx.built_at))
        except (ValueError, OSError):
            built = ""
        # 处数与名字数是**两个不同的量**，都写"符号 N 个"会让同屏两个数字打架
        # （状态栏报的是名字数），所以这里把两者都标清楚。
        index_note = (f"符号索引：{n_names} 个符号名 · {n_syms} 处定义 · {n_files} 个文件"
                      + (f"（构建于 {built}）" if built else "")
                      + "。这是「符号索引 + 词表倒排」，不是语义索引"
                      "（搜不到「处理完成判定的逻辑」这类意图，也不含调用关系）。")
        index_built_at = idx.built_at
    else:
        indexed = False
        index_built_at = 0.0
        index_note = ("尚未建立代码索引；首次搜索或点『重建索引』会自动建立。"
                      "未建立时搜索走 grep 工具（逐文件匹配，无索引无语义）。")

    return {
        "workspace": ws,
        "files": files,
        "dirs": dirs,
        "truncated": truncated,
        "by_ext": dict(top),
        "branch": git_head,
        "has_git": os.path.isdir(os.path.join(ws, ".git")),
        "indexed": indexed,
        "index_built_at": index_built_at,
        "index_note": index_note,
        "scan": {"max_depth": 4, "max_files": max_files,
                 "skipped_dirs": sorted(_SKIP_DIRS)},
    }


# ══════════════════════════════════════════════════════════════════
# 启动
# ══════════════════════════════════════════════════════════════════
def default_workspace() -> str:
    """API 未显式指定 workspace 时用哪个。

    优先级：启动参数写入的环境变量 > 当前进程目录。
    刻意不做"猜一个最近的 git 仓库"之类的事 —— 工作区必须**显式**，
    否则 agent 可能在错误的目录里动手。
    """
    return os.path.realpath(os.environ.get("CRUCIBLE_DEFAULT_WORKSPACE") or os.getcwd())


def serve(host: str = "127.0.0.1", port: int = 8787,
          workspace: Optional[str] = None) -> None:
    """起服务。

    `workspace` 指定默认工作区（API 未显式传时会用它）。
    这样启动时 `--workspace /path/to/proj` 就能把整个工作台绑到那个目录，
    前端也能通过 `?workspace=` 覆盖 —— 两者一致，避免"服务在 A、前端在 B"。
    """
    if workspace:
        os.environ["CRUCIBLE_DEFAULT_WORKSPACE"] = os.path.realpath(workspace)
    httpd = ThreadingHTTPServer((host, port), Handler)
    ws = default_workspace()
    # flush=True：后台/管道运行时 stdout 默认块缓冲，不刷新就看不到启动信息
    print(f"Crucible harness 已启动：http://{host}:{port}/", flush=True)
    print(f"  工作台：    http://{host}:{port}/index.html"
          f"?workspace={quote(ws)}", flush=True)
    print(f"  默认工作区：{ws}", flush=True)
    print(f"  工具清单：  http://{host}:{port}/api/tools", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Crucible harness HTTP 服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--workspace", default=None,
                    help="默认工作区（绝对路径）。API 未显式传 workspace 时使用。")
    args = ap.parse_args()
    serve(args.host, args.port, args.workspace)
