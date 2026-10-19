"""模型接入：**单一 OpenAI 兼容端点**。

## 为什么只做 OpenAI 兼容一种

本项目要接「任意的模型厂家和任意的中转接口」。与其为每家写适配器
（那是**永不收敛**的工作：厂家越多，适配层越大，而每一层都是一处 bug 源），
不如承认一个事实：**OpenAI 的 `/chat/completions` 已经是事实标准**。
国内主流厂家（DeepSeek / 通义 / Kimi / 智谱 / 硅基流动…）与几乎所有中转站
都提供兼容端点。所以接入面收敛成**三个字段**：

    接口地址 base_url    例如 https://api.deepseek.com/v1
    API Key              Bearer token
    模型名称 model        例如 deepseek-chat

换任何厂家/中转，改这三个字段即可，**代码一行不动**。

## 地址归一化（中转站的现实）

中转站的地址写法极不统一，常见四种：
- `https://x.com/v1`                    → 追加 `/chat/completions`
- `https://x.com/v1/`                   → 同上（去尾斜杠）
- `https://x.com/v1/chat/completions`   → **已经完整**，直接用
- `https://x.com`                       → 有的中转要 `/v1`，有的不要

`normalize_base_url()` 处理前三种（确定性的）。第四种**不猜**：
不擅自补 `/v1` —— 猜错会产生 404，而 404 的报错信息通常指向「模型不存在」
或「路径错误」，反而把真正的原因藏起来。宁可让用户看到一次直白的 404，
也不要猜一个可能对也可能错的路径。（书 §1 讲「不要制造看上去正常的失败」。）

## 离线模型仍然保留

`ScriptedModel` 让整套 Harness 能在**没有 key** 的情况下被完整测试 ——
否则「前缀稳定性」「9 格策略」「闸门挂起」这些不变式就只能靠人工碰运气。
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
# 端点配置（三个字段）
# ══════════════════════════════════════════════════════════════════
@dataclass
class EndpointConfig:
    """一次模型接入所需的**全部**信息 —— 刻意只有这三个字段。

    `api_key` 属于机密：落盘时单独处理（见 `save_config`），
    对外返回时一律打码（见 `mask_key`），**绝不原样回传**。
    """

    base_url: str = ""
    api_key: str = ""
    model: str = ""
    # 历史遗留的思考回传标记（部分国产模型要求），默认关。
    # 保留它是因为这确实是一个**协议差异**，而不是厂商私有适配。
    requires_reasoning_replay: bool = False

    # ---------- 派生 ----------
    @property
    def chat_url(self) -> str:
        """归一化后的 `/chat/completions` 完整地址。"""
        return normalize_base_url(self.base_url)

    @property
    def configured(self) -> bool:
        """三个字段是否齐备。缺任何一个都不算配置好。"""
        return bool(self.base_url.strip() and self.api_key.strip() and self.model.strip())

    @property
    def name(self) -> str:
        """给 session.meta 用的 provider 标识。

        从 base_url 里取主机名，而不是让用户额外填一个「厂家名」——
        这样三个字段就是真的只有三个。
        """
        try:
            from urllib.parse import urlparse
            host = urlparse(self.base_url).hostname or "custom"
            return host
        except Exception:
            return "custom"

    def to_public(self) -> Dict[str, Any]:
        """对外（前端 / API）的安全形态：**api_key 只给掩码**。"""
        return {
            "base_url": self.base_url,
            "model": self.model,
            "api_key_masked": mask_key(self.api_key),
            "has_key": bool(self.api_key.strip()),
            "configured": self.configured,
            "chat_url": self.chat_url if self.base_url.strip() else "",
        }

    def to_disk(self) -> Dict[str, Any]:
        """落盘形态。含明文 key —— 所以这个文件必须不进版本库。"""
        return {
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.model,
            "requires_reasoning_replay": self.requires_reasoning_replay,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "EndpointConfig":
        d = d or {}
        return cls(
            base_url=str(d.get("base_url") or "").strip(),
            api_key=str(d.get("api_key") or "").strip(),
            model=str(d.get("model") or "").strip(),
            requires_reasoning_replay=bool(d.get("requires_reasoning_replay", False)),
        )


def mask_key(key: str) -> str:
    """把 key 打成 `sk-a***xyz` 形状：够用户核对是哪一个，又不泄漏。

    刻意**不做成整体全掩码** —— 那样用户无法分辨"我保存的是哪个 key"，
    于是每次换 key 都要重新粘一遍。掩码的价值就在于"能认出"。
    对短 key 也保留首尾各 1 位（而不是直接全遮），同样是为了可核对。
    """
    k = (key or "").strip()
    if not k:
        return ""
    if len(k) <= 4:
        # 太短：只留第一位，剩下全遮 —— 仍能核对"是不是这个开头"
        return k[0] + "•" * (len(k) - 1)
    if len(k) <= 8:
        return k[:1] + "•" * (len(k) - 2) + k[-1:]
    return k[:4] + "•" * 6 + k[-4:]


def normalize_base_url(raw: str) -> str:
    """把用户填的地址归一成完整的 `/chat/completions` URL。

    只做**确定性**的转换，不猜：
      1. 去首尾空白与尾部斜杠
      2. 已经以 `/chat/completions` 结尾 → 原样使用
      3. 其余 → 追加 `/chat/completions`

    **不自动补 `/v1`**：见模块文档里的说明 —— 猜错会制造
    「看起来像模型不存在」的误导性报错。
    """
    u = (raw or "").strip().rstrip("/")
    if not u:
        return ""
    if u.endswith("/chat/completions"):
        return u
    return u + "/chat/completions"


# ══════════════════════════════════════════════════════════════════
# 配置落盘
# ══════════════════════════════════════════════════════════════════
# 存放位置：<workspace>/.crucible/config.json
# **必须在 .gitignore 里**（含明文 key），见仓库根的 .gitignore。
CONFIG_REL = os.path.join(".crucible", "config.json")
ENV_BASE_URL = "CRUCIBLE_BASE_URL"
ENV_API_KEY = "CRUCIBLE_API_KEY"
ENV_MODEL = "CRUCIBLE_MODEL"


def config_path(workspace: str) -> str:
    return os.path.join(os.path.realpath(workspace), CONFIG_REL)


def load_config(workspace: str) -> EndpointConfig:
    """读配置。优先级：环境变量 > 落盘文件 > 空。

    环境变量优先是为了支持容器/CI 场景（不想在磁盘上留明文 key 时，
    可以用 env 注入）。两条路径都存在时，**env 里出现的那一项覆盖文件**，
    未出现的项仍从文件取 —— 这样不必为了改一个字段而重写整份配置。
    """
    cfg = EndpointConfig()
    p = config_path(workspace)
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                cfg = EndpointConfig.from_dict(json.load(f))
        except (OSError, json.JSONDecodeError, TypeError):
            # 配置损坏不该炸掉整个 harness；退回默认（stub）即可。
            # 但也**不能静默** —— 上层会把 configured=False 反映到 UI 上。
            cfg = EndpointConfig()
    if os.environ.get(ENV_BASE_URL):
        cfg.base_url = os.environ[ENV_BASE_URL].strip()
    if os.environ.get(ENV_API_KEY):
        cfg.api_key = os.environ[ENV_API_KEY].strip()
    if os.environ.get(ENV_MODEL):
        cfg.model = os.environ[ENV_MODEL].strip()
    return cfg


def save_config(workspace: str, cfg: EndpointConfig) -> str:
    """写配置，返回落盘路径。

    目录权限收紧到 0o700 / 文件 0o600（POSIX 上生效）——
    文件里有明文 key，不该让同机其他用户读到。
    """
    p = config_path(workspace)
    d = os.path.dirname(p)
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg.to_disk(), f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


def clear_config(workspace: str) -> bool:
    p = config_path(workspace)
    if os.path.isfile(p):
        try:
            os.remove(p)
            return True
        except OSError:
            return False
    return False


def resolve_endpoint(workspace: str,
                     override: Optional[Dict[str, Any]] = None) -> EndpointConfig:
    """决定这次运行用哪个端点。

    顺序：请求里显式带的 > 落盘的 > 空（空即走离线脚本）。
    **不猜、不回退到"某个有 key 的厂家"** —— 配置来源必须单一且可见，
    否则「为什么它突然在调另一个厂家的接口」会变成无法排查的问题。
    """
    base = load_config(workspace)
    if not override:
        return base
    # 请求里只带部分字段时，未带的沿用落盘值（与 load_config 的合并语义一致）
    merged = EndpointConfig.from_dict({
        "base_url": override.get("base_url") or base.base_url,
        "api_key": override.get("api_key") or base.api_key,
        "model": override.get("model") or base.model,
        "requires_reasoning_replay": override.get(
            "requires_reasoning_replay", base.requires_reasoning_replay),
    })
    return merged



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
    # 这批工具调用是否来自**文本协议**（而非原生 tool_calls）。
    # loop 据此判断端点是否支持原生调用，从而决定要不要继续提醒格式。
    tool_calls_were_text: bool = False

    def to_message(self) -> Message:
        return Message.assistant(
            content=self.content,
            reasoning=self.reasoning,
            tool_calls=self.tool_calls,
            signature=self.reasoning_signature,
            origin_model=self.model,
        )


# ══════════════════════════════════════════════════════════════════
# 文本工具调用解析（不支持原生 function calling 的端点）
# ══════════════════════════════════════════════════════════════════
# 格式：<tool_call>{"name": "read_file", "arguments": {"path": "a.py"}}</tool_call>
#
# 为什么用这个格式而不是"自己发明一套"：
#   ReAct 原始论文用的就是 `Action: <name> / Action Input: <json>`，
#   而 <tool_call> 标签是社区（Qwen / Hermes 等）已验证的既有约定，
#   模型在预训练里见过，**不需要额外教学**就能稳定输出。
#   自创格式则要花大量提示词去教，且换个模型就失效。
_TEXT_CALL_RE = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL | re.IGNORECASE,
)
# 容错：有的模型会漏掉闭合标签或写成 ```json 围栏
_TEXT_CALL_LOOSE_RE = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*(?:</tool_call>|$)",
    re.DOTALL | re.IGNORECASE,
)
# ── DeepSeek 系模型的特殊标记 `<｜｜DSML｜｜ invoke name="x">` ──
# 实测：同一个模型在没走原生 tool_calls 时，会在 **两种格式之间漂移** ——
# 一会儿输出 <tool_call>{"name":...}，一会儿输出 <｜｜DSML｜｜ invoke>。
# 更糟的是它会把两种混着写，导致前面那个正则只截到半截 JSON，
# 解析失败后正文原样回灌，下一轮上下文就更乱。
# 所以两种格式都要认，并且 **DSML 优先**（它的结构更明确，不易误切）。
_DSML_INVOKE_RE = re.compile(
    r"<\s*\uff5c\uff5c\s*DSML\s*\uff5c\uff5c\s*invoke\s+name\s*=\s*\"([^\"]+)\"\s*>(.*?)"
    r"(?:</\s*\uff5c\uff5c\s*DSML\s*\uff5c\uff5c\s*invoke\s*>|$)",
    re.DOTALL,
)
_DSML_PARAM_RE = re.compile(
    r"<\s*\uff5c\uff5c\s*DSML\s*\uff5c\uff5c\s*parameter\s+name\s*=\s*\"([^\"]+)\"[^>]*>(.*?)"
    r"(?:</\s*\uff5c\uff5c\s*DSML\s*\uff5c\uff5c\s*parameter\s*>|$)",
    re.DOTALL,
)

# ── DSML 标记 + JSON 负载（第三种漂移形态）──
# 实测踩到：同一模型还会输出
#
#     <｜｜DSML｜｜call>
#     {"name": "grep", "arguments": {"pattern": "compute_magic_number"}}
#     </tool_call>
#
# 也就是**用 DSML 的标签包 JSON 的负载**（两种格式杂交）。
# 当时的后果很严重：解析器认不出它 → 这次工具调用被**静默丢弃** →
# 循环把它当成"最终回答"收尾，事件流里 `tool_calls=0`，
# 而模型其实做对了（它确实想调 grep）。
#
# 这不是"模型选错了工具"，而是 **harness 把正确的动作丢了** ——
# 属于书 §1 警告的「看上去毫无破绽的失败」：回答里残留着半截标签，
# 但运行状态是 completed，不细看根本发现不了。
#
# 判据刻意**窄**：必须是 DSML 标签紧跟着一个 JSON 对象。
# 不能放宽成"正文里出现 DSML 就找 JSON" —— 那会误吞模型在解释 DSML 时的正文。
_DSML_JSON_CALL_RE = re.compile(
    r"<\s*\uff5c\uff5c\s*DSML\s*\uff5c\uff5c\s*(?:call|tool_calls?)\s*>"
    r"\s*(?:\n\s*)?(\{)",
    re.IGNORECASE,
)

# 工具调用标签的各种写法。实测出现过的：`<tool_call>`、`<call>`。
# 不包含 `invoke` —— 那是 DSML 的标签，上面已经先处理并整块移除了，
# 把它放进来只会重复消费。
_CALL_TAG_RE = re.compile(
    r"<\s*(?:tool[_\s]?calls?|function[_\s]?calls?|tool_use|call)\s*[^<>]{0,300}>",
    re.IGNORECASE,
)
_CALL_CLOSE_RE = re.compile(
    r"</\s*(?:tool[_\s]?calls?|function[_\s]?calls?|tool_use|call)\s*>",
    re.IGNORECASE,
)


def extract_text_tool_calls(content: str) -> "tuple[List[ToolCall], str]":
    """从正文里抽出文本形式的工具调用。

    同时支持两种格式（实测同一模型会在两者之间漂移，甚至混写）：

    1. `DSML`：`<｜｜DSML｜｜invoke name="x"><｜｜DSML｜｜parameter name="k">v</...>`
    2. 通用：`<tool_call>{"name": "x", "arguments": {...}}</tool_call>`

    **DSML 优先**：它的结构更明确（name 与 parameter 分开），
    不会像 JSON 正则那样被"混写"截成半截。
    DSML 先扫并**整块移除**，再对剩余文本扫 `<tool_call>`。

    返回 `(calls, stripped)`。解析失败的块刻意**保留在正文里**，
    让模型看见自己写坏的原文，下一轮才有机会纠正
    （书 §5：工具层错误不终止调用，把错误变成模型的输入）。
    """
    if not content:
        return [], content

    calls: List[ToolCall] = []
    stripped = content

    # ── 1. DSML 格式 ──
    if "DSML" in stripped:
        for m in _DSML_INVOKE_RE.finditer(stripped):
            name = (m.group(1) or "").strip()
            body = m.group(2) or ""
            if not name:
                continue
            args: Dict[str, Any] = {}
            for pm in _DSML_PARAM_RE.finditer(body):
                key = (pm.group(1) or "").strip()
                raw = (pm.group(2) or "").strip()
                if not key:
                    continue
                try:
                    args[key] = json.loads(raw)
                except json.JSONDecodeError:
                    # 参数值可能不是 JSON（比如裸字符串 / 命令文本）
                    args[key] = raw
            # DSML 常把整个参数对象塞进一个名为 arguments 的 parameter，
            # 形成 {"arguments": {...}} 的多余一层。展平它 ——
            # 否则 router 看到的参数名会是 arguments，而不是 path / command。
            if len(args) == 1 and isinstance(args.get("arguments"), dict):
                args = args["arguments"]
            calls.append(ToolCall(name, args))
            stripped = stripped.replace(m.group(0), "")

    # ── 1b. DSML 标记 + JSON 负载（杂交形态）──
    # 用**花括号配平扫描**取 JSON，不用非贪婪正则 —— 理由同下面的
    # `<tool_call>` 分支：混写时正则会被截断成半截 JSON。
    if "DSML" in stripped:
        pos = 0
        while True:
            m = _DSML_JSON_CALL_RE.search(stripped, pos)
            if not m:
                break
            brace = m.start(1)
            end = _match_brace(stripped, brace)
            if end < 0:
                # JSON 没配平 → 解析不了，**保留原文**让模型看见自己写坏了什么
                pos = m.end()
                continue
            # 注意 `+ 1`：`_match_brace` 返回的是**闭括号的下标**，
            # 不是"结束位置"。漏掉它会少收一个字符，JSON 必然解析失败
            # （实测踩到：payload 变成 `...{"pattern":"x"}`，
            # 原本应是 `...{"pattern":"x"}}`）。
            payload = stripped[brace:end + 1]
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                pos = end
                continue
            if not isinstance(obj, dict):
                pos = end
                continue
            name = str(obj.get("name") or obj.get("tool") or "").strip()
            if not name:
                pos = end
                continue
            raw_args = obj.get("arguments")
            if raw_args is None:
                raw_args = obj.get("parameters") or obj.get("args") or {}
            if isinstance(raw_args, str):
                try:
                    raw_args = json.loads(raw_args)
                except json.JSONDecodeError:
                    raw_args = {}
            if not isinstance(raw_args, dict):
                raw_args = {}
            calls.append(ToolCall(name, raw_args))
            # 整块移除：从 DSML 标签起点到 JSON 结束（同样要 + 1）
            stripped = stripped[:m.start()] + stripped[end + 1:]
            pos = 0        # 位置已变，重新扫一遍

    # ── 2. 通用 `<工具标签>{json}` 格式 ──
    # 关键点：**不能**用 `\{.*?\}` 去做这件事 ——
    # 模型常把两种格式混写：`<tool_call>{...}` 后面紧跟残缺的
    # `</｜｜DSML｜｜ parameter>` 而没有 `</tool_call>`，
    # 非贪婪正则会一路吞到文本结尾，JSON 解析必然失败，
    # 于是"看起来解析过了"其实什么都没解析出来。
    # 改成**花括号配平扫描**：从 `{` 起数括号，配平即止。
    #
    # ## 标签名不能只认 `<tool_call>`
    #
    # 实测（2026-10-19）：模型在最后一步输出了 `<call>` 而不是 `<tool_call>`。
    # 解析器不认 → 整块被当成**正文** → 循环把它视为最终回答收尾 →
    # 运行状态 `completed`，回答停在「接下来做真正的运行时测试：<call>」。
    # 任务其实没做完，但事件流看起来是正常结束 —— 又一种「看上去毫无破绽的失败」。
    #
    # 放宽标签名，但**判据仍然窄**：标签后必须紧跟一个配平的 JSON 对象，
    # 且对象里必须有非空的 `name`。缺任何一条都不认，绝不把普通正文吞掉。
    m2 = _CALL_TAG_RE.search(stripped)
    if m2:
        consumed: List[str] = []
        pos = 0
        while True:
            m2 = _CALL_TAG_RE.search(stripped, pos)
            if not m2:
                break
            gt = m2.end() - 1                      # 标签的 `>` 位置
            i = m2.start()
            brace = stripped.find("{", gt)
            if brace < 0:
                break
            end = _match_brace(stripped, brace)
            if end < 0:
                pos = gt + 1
                continue
            raw = stripped[brace:end + 1]
            obj = None
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                obj = None
            if isinstance(obj, dict):
                name = obj.get("name") or obj.get("tool") or ""
                args = obj.get("arguments", obj.get("args", {}))
                if isinstance(name, str) and name.strip():
                    if not isinstance(args, dict):
                        args = {"__malformed__": args}
                    calls.append(ToolCall(name.strip(), args))
                    # 闭合标签有就一起吃掉，没有也不影响
                    tail = _CALL_CLOSE_RE.search(stripped, end)
                    stop = tail.end() if (tail and tail.start() - end < 40) else end + 1
                    consumed.append(stripped[i:stop])
                    pos = stop
                    continue
            pos = end + 1
        for chunk in consumed:
            stripped = stripped.replace(chunk, "")

    # ── 3. 清掉残标签 ──
    # 只清**不可能是普通英文正文**的标签名。裸 `<call>` 刻意不清：
    # 它是常见词，模型完全可能在正文里讨论它；识别失败时保留原文，
    # 下一轮模型才看得见自己写坏了什么（与「坏块保留」同一条纪律）。
    stripped = _DSML_tag_strip(stripped)
    stripped = _CALL_CLOSE_RE.sub("", stripped)
    stripped = re.sub(
        r"<\s*(?:tool[_\s]?calls?|function[_\s]?calls?|tool_use)\s*[^<>]{0,300}>",
        "", stripped, flags=re.IGNORECASE)
    return calls, stripped.strip()


def _match_brace(text: str, start: int) -> int:
    """从 `text[start] == '{'` 起做花括号配平，返回匹配的 `}` 下标；失败 -1。

    必须跳过字符串内部的花括号（含转义），否则
    `{"cmd": "echo }"}` 会在字符串里的 `}` 处被截断。
    """
    if start >= len(text) or text[start] != "{":
        return -1
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _DSML_tag_strip(text: str) -> str:
    """清掉 DSML 的标记（calls / invoke / parameter 的开闭标签）。

    这些是**标记**不是内容：留着会污染下一轮上下文，
    让模型误以为上一轮"已经调用过了"。

    注意 `｜｜DSML｜｜` 与标签名之间**可能有空格**（实测
    `</｜｜DSML｜｜ parameter>` 就有），所以用 `\\s*` 而不是直接相邻。
    """
    return re.sub(
        r"</?\s*\uff5c\uff5c\s*DSML\s*\uff5c\uff5c\s*(calls|invoke|parameter)[^>]*>",
        "", text).strip()


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
    """走 OpenAI 兼容的 `/chat/completions`。

    刻意只依赖标准库（urllib），与本项目「零依赖」的纪律一致。
    解析出的内容立刻转成**中立形态**（ModelReply → Message），
    不把厂商私有结构带出这一层 —— 这正是书 §5 要求的：
    轨迹不该按任何一家的接口格式存储。

    只认三个字段（base_url / api_key / model），因此**换厂家和中转不需要改代码**。
    """

    def __init__(self, endpoint: EndpointConfig,
                 model: Optional[str] = None,
                 timeout_s: float = 120.0,
                 text_protocol_only: bool = True) -> None:
        self.endpoint = endpoint
        self.model_name = (model or endpoint.model or "").strip()
        self.timeout_s = timeout_s
        # 是否用文本协议表达工具调用（**默认是**）。
        #
        # 为什么默认走文本协议而不是"先试原生、失败再降级"：
        # 端点一旦在某轮返回过文本块，历史里就留下了「assistant 文本块 + 用户消息形态的结果」，
        # 而如果请求里同时还声明着 `tools`，上游会判定
        #   `tool calls and tool results do not match` 并 400。
        # 实测（deepseek-v4.1-flash）**第一轮看起来完全正常、第二轮才炸**，
        # 这种"延迟失败"最难排查。
        #
        # 所以这里选**一开始就自洽**：不声明 tools，全程文本协议。
        # 代价是丧失原生 function calling 的强约束，但换来的是行为确定性 ——
        # 对一个"要能接任意中转"的系统，确定性比理论上的最优更重要。
        # 需要原生调用的端点可显式传 text_protocol_only=False。
        self.text_protocol_only = text_protocol_only

    @property
    def name(self) -> str:
        return self.model_name or "unconfigured"

    # ---------- 调用 ----------
    def complete(self, request: Dict[str, Any], timeout_s: float = 120.0) -> ModelReply:
        if not self.endpoint.configured:
            missing = [k for k, v in (("接口地址", self.endpoint.base_url),
                                      ("API Key", self.endpoint.api_key),
                                      ("模型名称", self.model_name)) if not str(v).strip()]
            return error_reply(
                401, f"模型未配置完整，缺少：{'、'.join(missing)}。"
                     "请在「模型接入」里填写这三个字段。", retryable=False)

        payload = {
            "model": self.model_name,
            "messages": request.get("messages", []),
            "temperature": request.get("temperature", 0.2),
        }

        # ── 是否声明 tools ──
        # 文本协议模式下**不能**声明 tools：上游会认为"你既然声明了工具，
        # 那工具调用就该走原生 tool_calls"，于是拿不到对应的 tool_calls 时报
        #   `tool calls and tool results do not match`
        # 并直接 400（实测 deepseek-v4.1-flash 就是这个行为）。
        # 所以一旦判定该端点不支持原生调用，就不再传 tools，让模型专心用文本协议。
        if request.get("tools") and not self.text_protocol_only:
            payload["tools"] = request["tools"]
            payload["tool_choice"] = "auto"
        if request.get("max_tokens"):
            payload["max_tokens"] = request["max_tokens"]

        return self._post(payload, timeout_s or self.timeout_s)

    def _post(self, payload: Dict[str, Any], timeout_s: float) -> ModelReply:
        """发一次请求并把 HTTP/网络错误转成结构化错误回复。

        错误分层在这里就已经决定（书 §5）：只有 429/5xx 才是「可重试」，
        401/403/404 是**配置问题**，重试不会有任何帮助 —— 把它们标成可重试
        只会让用户白等若干轮退避。
        """
        url = self.endpoint.chat_url
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.endpoint.api_key}",
            "User-Agent": "crucible-harness/1.0",
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
            # 4xx 里只有 408/409/429 值得重试，其余都是配置或请求问题
            retryable = e.code in (408, 409, 429) or e.code >= 500
            return error_reply(e.code, self._explain_http(e.code, detail), retryable=retryable)
        except urllib.error.URLError as e:
            return error_reply(
                503, f"连不上 {url}：{e.reason}。"
                     "请检查接口地址、网络与是否需要代理。", retryable=True)
        except TimeoutError:
            return error_reply(503, f"请求 {url} 超时（{timeout_s}s）。", retryable=True)
        except OSError as e:
            return error_reply(503, f"网络错误：{e}", retryable=True)
        except json.JSONDecodeError as e:
            return error_reply(502, f"响应不是合法 JSON：{e}。"
                                    "该地址可能不是 OpenAI 兼容端点。", retryable=True)

        return self._parse(body)

    @staticmethod
    def _explain_http(code: int, detail: str) -> str:
        """把状态码 + **响应正文**合起来翻译成人能直接照做的提示。

        这一层的价值在于：用户填错东西时，原始报文（`(404) Not Found`）
        通常不会告诉他是**哪一项**填错了。而 404 恰好有两种完全不同的含义：

            {"error":{"message":"model not found"}}    → 模型名错
            {"error":{"message":"unknown path /v1/..."}} → 地址错

        指错方向比不给提示更糟 —— 用户会去改一个本来正确的字段。
        所以这里必须读 detail，而不是只看状态码。
        """
        d = (detail or "")
        low = d.lower()

        # 先从正文里找明确的判据（比状态码更具体）
        if code == 404:
            if "model" in low and ("not found" in low or "不存在" in low
                                   or "unknown model" in low or "no such model" in low):
                hint = "**模型名称**不存在 —— 该端点没有这个模型 id。"
            elif "path" in low or "route" in low or "endpoint" in low or "url" in low:
                hint = "**接口地址**不对 —— 注意末尾要不要带 /v1。"
            else:
                hint = ("地址或模型名可能不对：404。请先核对接口地址末尾要不要带 /v1，"
                        "再确认模型名称在该端点上存在。")
        elif code in (401,):
            hint = "API Key 无效或已过期。"
        elif code == 403:
            hint = "API Key 没有调用该模型的权限（或该 Key 被限制了模型范围）。"
        elif code == 400:
            # 400 常见于「模型名写了但该端点不认识」或参数不合规
            if "model" in low:
                hint = "**模型名称**不被该端点接受。"
            else:
                hint = "请求被端点拒绝（常见原因是模型名称不对，或该端点不支持 tools 参数）。"
        elif code == 422:
            hint = "请求参数不被该端点接受（常见原因是模型名称不存在）。"
        elif code == 429:
            hint = "触发限流或余额不足。"
        else:
            hint = ""

        parts = []
        if hint:
            parts.append(hint)
        if d:
            parts.append(d[:400])
        return " ".join(parts) or f"HTTP {code}"

    def _parse(self, body: Dict[str, Any]) -> ModelReply:
        choices = body.get("choices") or []
        if not choices:
            # 有的中转在出错时返回 200 + {"error": {...}}，必须识别出来，
            # 否则会被当成「模型没说话」，变成一次静默的失败。
            err = body.get("error")
            if err:
                msg = err.get("message") if isinstance(err, dict) else str(err)
                return error_reply(502, f"端点返回错误：{msg}", retryable=False)
            return error_reply(502, f"响应缺少 choices：{str(body)[:300]}", retryable=True)
        msg = choices[0].get("message") or {}
        finish = choices[0].get("finish_reason", "")

        calls: List[ToolCall] = []
        used_text_protocol = False
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

        content = msg.get("content") or ""
        reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""

        # ── 文本工具调用降级路径（关键兼容层）──
        # 有些 OpenAI 兼容端点**不实现原生 function calling**：即使请求里带了
        # tools、即使 tool_choice="required"，返回的 message 里也没有 tool_calls。
        #
        # 实测 deepseek-v4.1-flash 的行为更特别：工具意图被写进 reasoning_content，
        # 而 **content 是 None**。所以只扫正文是不够的 —— 必须**正文与思考都扫**。
        #
        # 若不处理，后果是**极隐蔽的失败**：循环看到"没有 tool_calls"就判定为
        # 最终回答，任务在第一轮就"成功完成"了，事件流看起来毫无破绽。
        # 正是书 §1 警告的那种"看上去正常的失败"。
        #
        # 剥掉块是必须的：留着它会被当作用户可见的回答回灌进轨迹，污染下一轮上下文。
        if not calls:
            for source in ("content", "reasoning"):
                probe = content if source == "content" else reasoning
                if not probe:
                    continue
                found, stripped = extract_text_tool_calls(probe)
                if found:
                    calls = found
                    used_text_protocol = True
                    # 自适应切换：观察到文本协议说明该端点不走原生 function calling，
                    # 此后不再声明 tools（否则上游会 400）。
                    self.text_protocol_only = True
                    if source == "content":
                        content = stripped
                    else:
                        reasoning = stripped
                    # finish_reason 也要修正：端点报 "stop"，但对我们的语义是
                    # tool_calls。不改的话轨迹里会留下自相矛盾的记录。
                    finish = "tool_calls"
                    break

        return ModelReply(
            content=content,
            reasoning=reasoning,
            reasoning_signature=msg.get("reasoning_signature"),
            tool_calls=calls,
            model=body.get("model") or self.model_name,
            usage=body.get("usage") or {},
            finish_reason=finish,
            tool_calls_were_text=used_text_protocol,
        )

    # ---------- 连通性自检 ----------
    def selfcheck(self, timeout_s: float = 20.0) -> Dict[str, Any]:
        """用当前配置发一次**最小**请求，验证三个字段是否真的能用。

        为什么不做「单独 HEAD 一下地址」：地址不通和模型不存在是两码事，
        而用户真正关心的是「我填的这三项能不能跑」。所以直接发真实请求。
        只发 1 个 token、不带工具、不带上下文，代价可忽略。

        返回给前端一个**可读的判定**，而不是让用户自己解读 HTTP 码。
        """
        if not self.endpoint.configured:
            missing = [k for k, v in (("接口地址", self.endpoint.base_url),
                                      ("API Key", self.endpoint.api_key),
                                      ("模型名称", self.model_name)) if not str(v).strip()]
            return {"ok": False, "stage": "validate",
                    "message": f"还有字段没填：{'、'.join(missing)}。",
                    "url": self.endpoint.chat_url if self.endpoint.base_url else ""}

        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "temperature": 0,
        }
        t0 = time.time()
        reply = self._post(payload, timeout_s)
        took = round(time.time() - t0, 2)

        if reply.error_layer is not None:
            return {"ok": False, "stage": self._stage_of(reply),
                    "message": reply.raw_error or "调用失败",
                    "url": self.endpoint.chat_url, "elapsed_s": took}
        return {"ok": True, "stage": "ok",
                "message": f"连通。端点回包模型名：{reply.model or self.model_name}",
                "url": self.endpoint.chat_url,
                "model": reply.model or self.model_name,
                "elapsed_s": took}

    @staticmethod
    def _stage_of(reply: ModelReply) -> str:
        """把失败归到哪个环节 —— 前端据此给出不同的引导语。

        判据用**已经翻译过的正文**（`_explain_http` 的产物），
        因此能区分「404 = 地址错」与「404 = 模型名错」这两种完全不同的情况。
        归错环节会把人引向错误的修改方向，比不给提示更糟。
        """
        raw = reply.raw_error or ""
        if (raw.startswith("连不上") or "超时" in raw or "网络错误" in raw
                or "连接" in raw):
            return "network"
        if "API Key" in raw or "权限" in raw:
            return "auth"
        # 正文里点名了是哪一项，就按那名归
        if "模型名称" in raw and "不存在" in raw:
            return "model"
        if "模型名称" in raw and "不被" in raw:
            return "model"
        if "接口地址" in raw and "不对" in raw:
            return "url"
        if "地址或模型名" in raw:
            return "url_or_model"
        if "模型名称不存在" in raw:
            return "model"
        if "422" in raw:
            return "model"
        if "404" in raw:
            return "url_or_model"
        return "unknown"
