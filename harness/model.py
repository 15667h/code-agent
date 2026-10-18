"""Provider 抽象与离线模型。

## 为什么要有一个离线模型

本项目的 Harness 需要能在**没有 API key** 的情况下被完整测试 ——
否则「前缀稳定性」「9 格策略判定」「审批闸门挂起」这些关键不变式
就只能靠人工在真模型上碰运气验证。

因此 `ScriptedModel` 提供确定性的、可复现的响应序列：
它按预设脚本回放 tool_calls 与文本，让整条 Harness 管线可离线跑通。

这套「先给确定性的假模型，再换真模型」的思路，与书里
配套代码的做法一致（`agentbook/providers` 把 provider 抽象成纯数据 + 查找，
解析策略单独放在 `resolution.py`）。

## 中立格式的出口

`render_for()` 在 protocol.py 的 Message 上，因此换厂商时
**凭证一律丢弃、标识符重新生成**（书 §5）。本模块只负责调用与解析，
不持有任何厂商私有结构。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .protocol import ERR_API, Message, ROLE_ASSISTANT, ToolCall, ToolResult


# ══════════════════════════════════════════════════════════════════
# Provider 声明
# ══════════════════════════════════════════════════════════════════
@dataclass
class Provider:
    """一个提供商的声明。**纯数据** —— 不含解析策略（对齐书里的分层）。"""

    name: str
    base_url: str
    default_model: str
    key_vars: tuple = ()
    base_url_var: Optional[str] = None
    # 该厂商是否要求回传历史思考（书 §2 提到的 DeepSeek V4 / Kimi K2 / GLM-5 协议）
    requires_reasoning_replay: bool = False

    def api_key(self) -> Optional[str]:
        for v in self.key_vars:
            if os.environ.get(v):
                return os.environ[v]
        return None

    def get_base_url(self) -> str:
        if self.base_url_var and os.environ.get(self.base_url_var):
            return os.environ[self.base_url_var]
        return self.base_url


PROVIDERS: Dict[str, Provider] = {
    "openai": Provider(
        name="openai",
        base_url="https://api.openai.com/v1",
        default_model="gpt-5.4-codex",
        key_vars=("OPENAI_API_KEY",),
        base_url_var="OPENAI_BASE_URL",
    ),
    "deepseek": Provider(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        key_vars=("DEEPSEEK_API_KEY",),
        base_url_var="DEEPSEEK_BASE_URL",
    ),
    "dashscope": Provider(
        name="dashscope",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen3.7-plus",
        key_vars=("DASHSCOPE_API_KEY",),
        base_url_var="DASHSCOPE_BASE_URL",
    ),
    "kimi": Provider(
        name="kimi",
        base_url="https://api.moonshot.cn/v1",
        default_model="kimi-k3",
        key_vars=("MOONSHOT_API_KEY", "KIMI_API_KEY"),
        base_url_var="KIMI_BASE_URL",
        requires_reasoning_replay=True,
    ),
    "siliconflow": Provider(
        name="siliconflow",
        base_url="https://api.siliconflow.cn/v1",
        default_model="Qwen/Qwen3.5-397B-A17B",
        key_vars=("SILICONFLOW_API_KEY",),
    ),
    "stub": Provider(
        name="stub",
        base_url="",
        default_model="scripted",
    ),
}


def resolve_provider(name: Optional[str] = None) -> Provider:
    """挑选 provider。

    顺序：显式指定 → 有 key 的第一个 → stub。
    「有 key」是唯一的判据 —— **不猜、不回退到有副作用的路径**。
    """
    if name:
        p = PROVIDERS.get(name)
        if p is None:
            raise ValueError(f"未知 provider：{name}。可用：{', '.join(PROVIDERS)}")
        return p
    env_pref = os.environ.get("CRUCIBLE_PROVIDER")
    if env_pref and env_pref in PROVIDERS:
        return PROVIDERS[env_pref]
    for p in PROVIDERS.values():
        if p.name == "stub":
            continue
        if p.api_key():
            return p
    return PROVIDERS["stub"]


# ══════════════════════════════════════════════════════════════════
# 模型返回
# ══════════════════════════════════════════════════════════════════
@dataclass
class ModelReply:
    """一次模型调用的结果（中立形态）。"""

    content: str = ""
    reasoning: Optional[str] = None
    reasoning_signature: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    model: str = ""
    # 结构化用量（不靠解析文本）
    usage: Dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""
    raw_error: Optional[str] = None
    error_layer: Optional[str] = None
    retryable: Optional[bool] = None

    def to_message(self) -> Message:
        return Message.assistant(
            content=self.content,
            reasoning=self.reasoning,
            tool_calls=self.tool_calls,
            signature=self.reasoning_signature,
            origin_model=self.model,
        )


# ══════════════════════════════════════════════════════════════════
# 接口
# ══════════════════════════════════════════════════════════════════
class BaseModel:
    def complete(self, request: Dict[str, Any], timeout_s: float = 120.0) -> ModelReply:
        raise NotImplementedError

    @property
    def name(self) -> str:
        return "base"


# ══════════════════════════════════════════════════════════════════
# 离线脚本模型
# ══════════════════════════════════════════════════════════════════
class ScriptedModel(BaseModel):
    """确定性的离线模型。

    脚本是一个 `ModelReply` 列表，按调用次数依次回放。
    用完后返回一个纯文本的收尾回复，保证循环能正常终止。

    `on_call` 钩子可注入副作用，用于测试中触发故障（比如第 2 次调用返回 429），
    这是验证熔断与重试路径的唯一可靠方法。
    """

    def __init__(self, script: Optional[List[ModelReply]] = None,
                 on_call: Optional[Callable[[int, Dict[str, Any]], Optional[ModelReply]]] = None,
                 final_text: str = "我已经完成了你能离线验证的部分。") -> None:
        self.script = list(script or [])
        self.on_call = on_call
        self.final_text = final_text
        self.calls: List[Dict[str, Any]] = []
        self._i = 0

    def complete(self, request: Dict[str, Any], timeout_s: float = 120.0) -> ModelReply:
        self.calls.append(request)
        idx = self._i
        self._i += 1

        if self.on_call is not None:
            injected = self.on_call(idx, request)
            if injected is not None:
                return injected

        if idx < len(self.script):
            r = self.script[idx]
            r.model = r.model or "scripted"
            return r
        return ModelReply(content=self.final_text, model="scripted", finish_reason="stop")

    @property
    def name(self) -> str:
        return "scripted"


def tool_call_reply(name: str, args: Dict[str, Any], reasoning: str = "") -> ModelReply:
    """便捷构造：一次「只调用工具、不给最终回答」的回复。

    书 §2 指出这种形态很常见：*Agent 决定调用工具时通常只有
    `reasoning` + `tool_calls`*。
    """
    return ModelReply(reasoning=reasoning or f"需要调用 {name}。",
                      tool_calls=[ToolCall(name, args)],
                      finish_reason="tool_calls")


def final_reply(text: str, reasoning: str = "") -> ModelReply:
    return ModelReply(content=text, reasoning=reasoning or "可以给出结论了。",
                      finish_reason="stop")


def error_reply(status: int, body: str = "", retryable: Optional[bool] = None,
                layer: str = ERR_API) -> ModelReply:
    """构造一个错误回复（用于测试故障路径）。"""
    return ModelReply(
        raw_error=f"HTTP {status}: {body}",
        error_layer=layer,
        retryable=retryable if retryable is not None else status in (429, 500, 502, 503, 504),
    )


# ══════════════════════════════════════════════════════════════════
# 真实 HTTP 模型（OpenAI 兼容端口）
# ══════════════════════════════════════════════════════════════════
class HttpModel(BaseModel):
    """走 OpenAI 兼容的 /chat/completions。

    刻意只依赖标准库（urllib），与本项目「零依赖」的纪律一致。
    解析出的内容立刻转成**中立形态**（ModelReply → Message），
    不把厂商私有结构带出这一层 —— 这正是书 §5 要求的：
    轨迹不该按任何一家的接口格式存储。
    """

    def __init__(self, provider: Provider, model: Optional[str] = None) -> None:
        self.provider = provider
        self.model_name = model or provider.default_model

    @property
    def name(self) -> str:
        return self.model_name

    def complete(self, request: Dict[str, Any], timeout_s: float = 120.0) -> ModelReply:
        key = self.provider.api_key()
        if not key:
            return error_reply(401, f"缺少 API key（{', '.join(self.provider.key_vars)}）",
                               retryable=False)

        payload = {
            "model": self.model_name,
            "messages": request.get("messages", []),
            "temperature": request.get("temperature", 0.2),
        }
        if request.get("tools"):
            payload["tools"] = request["tools"]
            payload["tool_choice"] = "auto"
        if request.get("max_tokens"):
            payload["max_tokens"] = request["max_tokens"]

        url = self.provider.get_base_url().rstrip("/") + "/chat/completions"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        })

        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:800]
            except Exception:
                pass
            return error_reply(e.code, detail)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return error_reply(503, f"连接失败：{e}", retryable=True)
        except json.JSONDecodeError as e:
            return error_reply(502, f"响应不是合法 JSON：{e}", retryable=True)

        return self._parse(body)

    def _parse(self, body: Dict[str, Any]) -> ModelReply:
        choices = body.get("choices") or []
        if not choices:
            return error_reply(502, f"响应缺少 choices：{str(body)[:300]}", retryable=True)
        msg = choices[0].get("message") or {}
        finish = choices[0].get("finish_reason", "")

        calls: List[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args_raw = fn.get("arguments") or "{}"
            if isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw) if args_raw.strip() else {}
                except json.JSONDecodeError:
                    # 参数畸形：保留原文，由 router 报结构化错误让模型自行纠正
                    args = {"__malformed__": args_raw}
            else:
                args = args_raw
            calls.append(ToolCall(fn.get("name", ""), args))

        return ModelReply(
            content=msg.get("content") or "",
            reasoning=msg.get("reasoning_content") or msg.get("reasoning"),
            reasoning_signature=msg.get("reasoning_signature"),
            tool_calls=calls,
            model=body.get("model") or self.model_name,
            usage=body.get("usage") or {},
            finish_reason=finish,
        )
