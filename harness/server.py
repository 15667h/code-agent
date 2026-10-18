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
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

# 既支持 `python -m harness.server`，也支持 `python harness/server.py`。
# 后者没有父包，相对导入会失败，所以这里补上包路径再统一用绝对导入。
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from harness.context import DEFAULT_SYSTEM_PROMPT, StaticPrefix
    from harness.loop import AgentLoop, LoopConfig, build_default_registry
    from harness.model import (ENV_API_KEY, ENV_BASE_URL, ENV_MODEL, EndpointConfig,
                               HttpModel, ScriptedModel, clear_config, config_path,
                               error_reply, load_config, mask_key, normalize_base_url,
                               resolve_endpoint, save_config, tool_call_reply, final_reply)
    from harness.policy import ApprovalPolicy, Policy, SandboxMode
    from harness.protocol import Event, EventBus, EventType
    from harness.session import Session, list_runs
else:
    from .context import DEFAULT_SYSTEM_PROMPT, StaticPrefix
    from .loop import AgentLoop, LoopConfig, build_default_registry
    from .model import (ENV_API_KEY, ENV_BASE_URL, ENV_MODEL, EndpointConfig,
                        HttpModel, ScriptedModel, clear_config, config_path,
                        error_reply, load_config, mask_key, normalize_base_url,
                        resolve_endpoint, save_config, tool_call_reply, final_reply)
    from .policy import ApprovalPolicy, Policy, SandboxMode
    from .protocol import Event, EventBus, EventType
    from .session import Session, list_runs

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

    session = Session(workspace, model.name, ep.name, sbx.value, ap.value)

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
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query)

        if path == "/api/health":
            return self._json(200, {"ok": True, "runs": len(RUNS),
                                    "pid": os.getpid()})
        if path == "/api/runs":
            ws = (q.get("workspace") or [os.getcwd()])[0]
            return self._json(200, {"runs": list_runs(ws)})
        if path == "/api/tools":
            return self._json(200, _tool_manifest())
        if path == "/api/config":
            ws = (q.get("workspace") or [os.getcwd()])[0]
            ep = load_config(ws)
            return self._json(200, {
                "endpoint": ep.to_public(),
                "config_path": config_path(ws),
                # 这三个环境变量可以作为替代来源（容器/CI 场景不想落盘明文 key）
                "env_vars": [ENV_BASE_URL, ENV_API_KEY, ENV_MODEL],
            })
        if path.startswith("/api/run/") and path.endswith("/result"):
            rid = path.split("/")[3]
            st = RUNS.get(rid)
            if st is None:
                return self._json(404, {"error": "run not found"})
            return self._json(200, {
                "run_id": rid,
                "result": st.result,
                "error": st.error,
                "summary": st.session.summary() if st.session else None,
            })
        if path.startswith("/api/run/") and path.endswith("/events"):
            return self._sse(path.split("/")[3])

        # 静态：根路径给前端
        if path in ("/", "/index.html"):
            return self._static("index.html")
        return self._static(path)

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
        body = self._read_body()

        if path == "/api/run":
            ws = body.get("workspace") or os.getcwd()
            prompt = (body.get("prompt") or "").strip()
            if not prompt:
                return self._json(400, {"error": "prompt 不能为空"})
            cfg = LoopConfig(
                max_iterations=int(body.get("max_iterations") or 25),
                lint_after_edit=bool(body.get("lint_after_edit", True)),
                inject_status=bool(body.get("inject_status", True)),
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

        # ── 模型接入配置 ──
        # 只有三个字段。GET 返回掩码后的形态（**绝不回传明文 key**），
        # POST 保存，/test 用当前配置做一次真实连通性自检。
        if path == "/api/config":
            ws = body.get("workspace") or os.getcwd()
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
            ws = body.get("workspace") or os.getcwd()
            # 优先用请求里带的（用户可能还没保存就想先试），否则用落盘的
            ep = resolve_endpoint(ws, body.get("endpoint"))
            model = HttpModel(ep)
            report = model.selfcheck(timeout_s=float(body.get("timeout_s") or 20.0))
            report["endpoint"] = ep.to_public()
            return self._json(200 if report.get("ok") else 200, report)

        if path == "/api/config/clear":
            ws = body.get("workspace") or os.getcwd()
            removed = clear_config(ws)
            return self._json(200, {"ok": True, "removed": removed,
                                    "endpoint": EndpointConfig().to_public()})

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
        ws = os.getcwd()
        reg, _ = build_default_registry(ws)
        return {"order": reg.names(), "categories": reg.categories(),
                "specs": reg.specs()}
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════
# 启动
# ══════════════════════════════════════════════════════════════════
def serve(host: str = "127.0.0.1", port: int = 8787) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    # flush=True：后台/管道运行时 stdout 默认块缓冲，不刷新就看不到启动信息
    print(f"Crucible harness 已启动：http://{host}:{port}/", flush=True)
    print(f"  工作台前端：http://{host}:{port}/index.html", flush=True)
    print(f"  工具清单：  http://{host}:{port}/api/tools", flush=True)
    print(f"  离线演示：  POST /api/run  "
          f'{{"prompt":"...", "offline_script":["search:TODO","shell:git status"]}}',
          flush=True)
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
    args = ap.parse_args()
    serve(args.host, args.port)
