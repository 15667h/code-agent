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
    def _find_run_dir(self, run_id: str) -> Optional[str]:
        """按 run_id 找出它的落盘目录。

        为什么需要它：`RUNS` 是内存态，进程重启就没了。但哈希链的价值恰恰是
        **不依赖产生它的那个进程还活着** —— 只要 run 目录还在，任何人都能核验。
        所以核验端点必须能在没有 RunState 的情况下工作。

        匹配顺序：meta.json 里的 run_id → 目录名 → session_id。
        最先查 run_id 是因为那才是**对外**的标识（session_id 是内部落盘目录名，
        两者独立生成，实测过拿 run_id 去匹配目录名匹配不上）。
        """
        if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
            return None              # 拒绝路径穿越
        root = os.path.join(os.path.realpath(default_workspace()),
                            ".crucible", "runs")
        if not os.path.isdir(root):
            return None

        metas: List[tuple] = []
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
                metas.append((m.get("created_at") or 0, d))
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
        if path == "/api/tools":
            return self._json(200, _tool_manifest())
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
            run_dir = self._find_run_dir(rid)
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
            st = RUNS.get(rid)
            run_dir = (st.session.run_dir if (st and st.session)
                       else self._find_run_dir(rid))
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
                run_dir = self._find_run_dir(rid)
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
            run_dir = self._find_run_dir(rid)
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
                              "这**不等于**「没有越权」，而是「无法判断」。",
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
                run_dir = self._find_run_dir(rid)
            if run_dir is None:
                return self._json(404, {"error": "run not found"})
            root = os.path.join(run_dir, "subagents")
            if not os.path.isdir(root):
                return self._json(404, {
                    "error": "no subagent records",
                    "detail": "这次运行没有派出任何子代理。"
                              "这**不等于**「子代理都失败了」，而是「没有记录」。",
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
                run_dir = self._find_run_dir(rid)
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
                              "这**不等于**「任务未完成」。",
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

        if path.startswith("/api/run/") and path.endswith("/rollback"):
            rid = path.split("/")[3]
            st = RUNS.get(rid)
            cs = None
            if st is not None and st.session is not None:
                cs = st.session.changesets
            else:
                # 进程重启后 RUNS 清空 —— 但变更集在盘上，
                # 回滚**不依赖产生它的进程还活着**（这正是它要解决的问题）。
                run_dir = self._find_run_dir(rid)
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
