"""子代理与上下文隔离（G-9）。

依据三份材料：

1. **《Role Separation Is Not Verification》**（本项目的核心依据）

   | 配置 | HumanEval pass@1 | 测试用例准确率 |
   | --- | --- | --- |
   | 单代理（代码与测试同一对话） | 71.3% | 61.0% |
   | 多代理，但**测试设计者看不到代码** | 79.9% | **87.8%** |

   真正起作用的**不是"分成两个 agent"，而是上下文隔离**。原作者的话：
   > "在同一对话里紧跟代码生成的测试，会被代码影响而产生偏见，失去客观性与多样性。"

   → **没有上下文隔离的角色分离，只是换了个标签，不是架构改变。**

2. **latent entanglement**（共享模型问题）：跨 6 个模型族的 18 个 LLM 实测，
   行为依赖程度与"过度认可（倾向于验证错误输出）"的相关性 ρ = 0.64–0.71。
   越相关（同族）的模型，越容易在同一个错误答案上达成一致。
   → 最好用**不同模型**做验证（本项目有三个字段可配端点，换端点成本极低）。

3. **检查点摘要会朝上游结论方向压缩**（2500+ 对话、25 万次推理实测）：
   摘要不是中性压缩，下一个 agent 不是从原始证据开始，而是从**被塑形过的证据**开始。
   → 验证者**不能读生成者的推理摘要**。

## 本模块的立足点：把三条约束变成"结构上做不到违反"

这三条如果用"约定"实现（写进 docstring 请大家遵守），迟早会有人为了方便
传一个 `context` 字符串进去，于是隔离就没了 —— 而且**没有任何报错**，
只是效果悄悄退回到 61.0%。这正是本项目反复踩的那类"看上去正常的失败"。

所以：

1. `SubAgentSpec` **只有 `task` 一个内容字段**。**没有任何字段可以承载上游推理**
   —— 不是"不建议传"，是**没有地方可传**。
2. `SubAgentResult` 把模型自己的话放在 `claim` 里，并**明确标注它不是证据**；
   结构化产物放 `artifacts`。两者物理分开，调用方想混都混不起来。
3. `build_verification_prompt()` 的**签名里没有 `claim` 参数**
   —— 它不可能把生成者的话读进去。
4. 隔离是**双向记录**的：`IsolationRecord` 不只记"给了什么"，还记
   **"刻意没给什么、为什么"**。隔离不可见 = 无法审计 = 等于没做。

## 与能力账本（G-5）的关系

`LedgerBook` 按 `grantee` 分账，子代理拿到**自己独立的 ceiling**。
这不是顺手做的：CapScope 第 4 点要求"子代理的权限不自动传导"，
而它的实现方式恰恰就是"每个 agent 各持一份 ceiling"。
本模块的 `spec.name` 就是那个 `grantee`。
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .capability import Ceiling, CapabilityLedger, derive_ceiling
from .preflight import TraceFacts

# ══════════════════════════════════════════════════════════════════
# 规格：**唯一**的入向通道
# ══════════════════════════════════════════════════════════════════
@dataclass
class SubAgentSpec:
    """交给子代理的**全部**信息。

    ## 为什么这里刻意极简

    这个类的字段清单本身就是隔离策略的实现。每加一个字符串字段，
    就多一条把上游推理漏进去的通道；而漏进去**不会报错**，
    只会让上下文隔离失效（实测收益 87.8% → 61.0%）。

    所以：
    - 有 `task`（任务规格）—— 必须给
    - 有 `name`/`role`/`max_iterations`/`sandbox`（配置）—— 结构性参数
    - **没有** `context`、`history`、`parent_messages`、`summary`、
      `previous_reasoning`、`notes` 这类字段 —— 不是遗漏，是设计。

    如果将来确实需要给子代理看某个文件的内容，
    走 `read_paths`（**显式的、可审计的清单**），不要加一个自由文本字段。
    """

    name: str
    task: str
    role: str = "worker"                  # worker | verifier
    # 子代理可以读的路径清单。**显式白名单** —— 而不是"把整个上下文给它"。
    # 为什么是白名单而不是黑名单：黑名单会随上游变化而失效
    # （上游新增一种消息类型，隔离就漏了），白名单不会。
    read_paths: List[str] = field(default_factory=list)
    max_iterations: int = 8
    # 沙箱模式。子代理默认可以比父级更严 —— 让它读得少、写得少。
    sandbox: Optional[str] = None
    # 是否允许子代理写工作区。默认**不允许**：
    # 验证者尤其不该有写权限（否则它就能改产物让自己通过 ——
    # 那正是 BenchJack 的 conftest.py 手法）。
    allow_write: bool = False
    # 期望的模型（None = 用父级的）。文档建议验证者换模型以避开 latent entanglement。
    model_hint: Optional[str] = None
    # 期望的端点 base_url（None = 用父级的）。用于"换一个厂家做验证"。
    endpoint_hint: Optional[str] = None


# ══════════════════════════════════════════════════════════════════
# 隔离记录：双向
# ══════════════════════════════════════════════════════════════════
@dataclass
class IsolationRecord:
    """本次隔离**给了什么 / 刻意没给什么**。

    只记"给了什么"是不够的：那样无法回答"隔离到底做了没有"。
    反过来说明它为什么值得存在：本项目已验证过"不可观察的机制等于没有"
    （哈希链、能力账本都花了额外功夫做落盘，就是这个理由）。

    所以 `withheld` 是必需的，而且每条都带 `reason`。
    """

    agent_id: str
    given: List[str] = field(default_factory=list)
    withheld: List[str] = field(default_factory=list)
    # 上下文里实际的消息条数（用于断言"只有规格"）
    context_messages: int = 0
    # 父级上下文的 messages 是否**一条都没进去**
    parent_context_leaked: bool = False

    def to_json(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "given": self.given,
            "withheld": self.withheld,
            "context_messages": self.context_messages,
            "parent_context_leaked": self.parent_context_leaked,
        }

    def to_text(self) -> str:
        lines = [f"子代理 {self.agent_id} 的上下文隔离："]
        lines.append(f"  · 收到 {self.context_messages} 条消息")
        for g in self.given:
            lines.append(f"  · 给了：{g}")
        for w in self.withheld:
            lines.append(f"  · **刻意没给**：{w}")
        if self.parent_context_leaked:
            lines.append("  · ✗ 父级上下文泄漏！隔离失效。")
        return "\n".join(lines)


# 必须在隔离记录里出现的"刻意没给"清单。
# 这份清单是**硬编码**的 —— 它表达的是"隔离的定义"，
# 不该随调用方的写法漂移。少了任何一条都说明隔离不完整。
WITHHELD_TEMPLATE = (
    ("父级的推理与思考文本（reasoning）",
     "检查点摘要会朝上游结论方向压缩；读了它，验证者就从被塑形过的证据开始。"),
    ("父级与上游的工具输出原文",
     "工具输出是间接提示注入的载体之一（CapScope 的立论）。"),
    ("上游全部对话历史（messages）",
     "《Role Separation Is Not Verification》的核心结论：收益来自隔离本身。"),
    ("生成者自己的最终回答（claim）",
     "验证者读原始产物，不读生成者的自我陈述。"),
    ("父级的压缩摘要（compaction summary）",
     "摘要不是中性压缩，会把下游 agent 的结论引向生成方的结论。"),
)


# ══════════════════════════════════════════════════════════════════
# 结果：产物与"声明"物理分开
# ══════════════════════════════════════════════════════════════════
@dataclass
class SubAgentResult:
    """子代理跑完后的返回。

    ## 关键设计：`artifacts` 与 `claim` 分开

    上游拿到这个对象时，第一眼应该看到**产物**（客观、可复核），
    而不是模型说了什么。所以字段顺序、命名、文档都朝这个方向压。

    `claim` 字段的文档里明确写着"这不是证据" —— 不是为了提醒人，
    而是为了让任何读代码的人第一眼就知道它的地位。
    """

    agent_id: str
    status: str
    # ── 客观产物（唯一可作为证据的部分）──
    artifacts: Dict[str, Any] = field(default_factory=dict)
    # ── 生成者自己的话。**它不是证据。** ──
    claim: str = ""
    claim_is_evidence: bool = False       # 恒 False，写出来是为了让人看见
    # ── 隔离记录 ──
    isolation: Optional[IsolationRecord] = None
    # ── 子代理自己的能力账本（不共享父级的） ──
    capability: Dict[str, Any] = field(default_factory=dict)
    # ── 模型独立性评估 ──
    model_independence: Dict[str, Any] = field(default_factory=dict)
    iterations: int = 0
    tool_calls: int = 0
    run_dir: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "status": self.status,
            "artifacts": self.artifacts,
            "claim": self.claim,
            "claim_is_evidence": self.claim_is_evidence,
            "isolation": self.isolation.to_json() if self.isolation else None,
            "capability": self.capability,
            "model_independence": self.model_independence,
            "iterations": self.iterations,
            "tool_calls": self.tool_calls,
            "run_dir": self.run_dir,
        }

    def to_text(self) -> str:
        lines = [f"── 子代理 {self.agent_id}（role={self.status}）──"]
        a = self.artifacts
        lines.append(f"  轮次 {self.iterations} · 工具调用 {self.tool_calls}")
        if a.get("files_changed"):
            lines.append(f"  落盘：{', '.join(a['files_changed'][:8])}")
        if a.get("commands"):
            lines.append("  命令（含退出码）：")
            for c in a["commands"][:6]:
                lines.append(f"    · [{c.get('exit_code')}] {c.get('command', '')[:90]}")
        if a.get("tool_sequence"):
            lines.append(f"  工具序列：{' → '.join(a['tool_sequence'])}")
        if self.isolation:
            lines.append(self.isolation.to_text().replace("\n", "\n  "))
        if self.claim:
            lines.append(f"  **生成者的自述（非证据）**：{self.claim[:160]}")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# 模型独立性：只报事实，不替用户下结论
# ══════════════════════════════════════════════════════════════════
# 这只是**名字前缀的启发式**，它不能真的判断两个模型是否同族
# （不同厂家的同名模型、同一厂家的不同代次都会让它出错）。
# 所以函数返回三种结论，`unknown` 是常态而不是失败 ——
# 宁可说"不知道"，也不要给一个看起来确定其实猜出来的判断。
_FAMILY_HINTS = (
    ("deepseek", ("deepseek",)),
    ("gpt/openai", ("gpt", "o1", "o3", "o4", "codex")),
    ("claude", ("claude", "sonnet", "opus", "haiku")),
    ("gemini", ("gemini", "palm")),
    ("qwen", ("qwen", "tongyi")),
    ("llama", ("llama", "meta-llama")),
    ("glm", ("glm", "chatglm", "zhipu")),
    ("ernie", ("ernie", "wenxin")),
    ("mistral", ("mistral", "mixtral")),
)


def _family_of(model: str) -> Optional[str]:
    low = (model or "").lower()
    if not low:
        return None
    for fam, keys in _FAMILY_HINTS:
        if any(k in low for k in keys):
            return fam
    return None


def model_independence(generator: str, verifier: str,
                       gen_endpoint: str = "", ver_endpoint: str = "") -> Dict[str, Any]:
    """评估生成者与验证者的独立性。

    **这是报告，不是门禁。** 不阻断的理由：用户可能只有一个端点可用，
    阻断会让整个功能不可用；而"无法换模型"这件事本身是重要事实，
    应当被如实说出，而不是变成一次失败。

    ## 判据取向：端点不同**不足以**说明独立

    实测踩到的坑：早期版本把"端点不同"当作独立性的**充分条件**，
    于是两个从未听说过的模型名 + 两个不同端点 → 判"低风险"。
    但同一个模型完全可以通过两个中转站暴露 —— 端点不同
    只是佐证，不是证明。

    所以判据的主轴是**模型本身是否可能相关**，端点只做佐证：

    | 情形 | verdict | 风险 |
    | --- | --- | --- |
    | 模型名相同 | `same_model` | 高 |
    | 名字前缀判为同族 | `same_family` | 中高 |
    | 名字前缀判为不同族 + 端点也不同 | `distinct_family` | 低 |
    | 名字前缀判为不同族，同端点 | `distinct_family` | 中 |
    | **名字前缀无法判定** | `uncertain` | 中 |

    最后一档是关键：**不知道就说不知道**。给一个猜出来的"低风险"
    比给"不确定"更坏 —— 它会让一次弱验证被当成强验证接受。
    """
    g = (generator or "").strip()
    v = (verifier or "").strip()
    ge = (gen_endpoint or "").strip().lower()
    ve = (ver_endpoint or "").strip().lower()
    same_ep = bool(ge and ve and ge == ve)

    fam_g, fam_v = _family_of(g), _family_of(v)
    if fam_g is None or fam_v is None:
        fam_rel = "unknown"
    elif fam_g == fam_v:
        fam_rel = "same"
    else:
        fam_rel = "distinct"

    if g.lower() == v.lower() and g:
        verdict, risk = "same_model", "高"
    elif fam_rel == "same":
        verdict, risk = "same_family", "中高"
    elif fam_rel == "distinct":
        verdict, risk = "distinct_family", ("低" if not same_ep else "中")
    else:
        # 名字前缀认不出来 —— 可能是新模型、私有命名、或者中转站改了名。
        # 这时**没有任何依据**说它们独立，所以不报"低"。
        verdict, risk = "uncertain", "中"

    notes = {
        "same_model": "**同一模型**：文档实测的 latent entanglement 场景"
                      "（行为依赖与「过度认可」相关性 ρ=0.64–0.71）。"
                      "本次验证的独立性较弱，结论请按此折扣。",
        "same_family": "模型名不同但**名字前缀指向同一族**。"
                       "同族模型共享训练数据与对齐流程，仍存在相关失败模式。",
        "distinct_family": "名字前缀指向不同模型族。"
                           + ("端点也不同，独立性较好。" if not same_ep
                              else "但端点相同 —— 若该端点是中转，实际后端可能仍是同一模型。"),
        "uncertain": "**无法判定**这两个模型是否同族（名字前缀认不出来）。"
                     "这不等于它们独立 —— 请勿把本次验证当作强验证。",
    }

    return {
        "verdict": verdict,
        "risk": risk,
        "family_check": fam_rel,
        "generator_model": g,
        "verifier_model": v,
        "same_endpoint": same_ep,
        "note": notes[verdict],
        "caveat": "family_check 只按模型名前缀做启发式，**不能真正判定同族**。"
                  "uncertain 表示「无法判定」，不表示「相互独立」。",
    }


# ══════════════════════════════════════════════════════════════════
# 隔离上下文
# ══════════════════════════════════════════════════════════════════
def build_isolated_context(prefix, spec: SubAgentSpec,
                           workspace: str) -> tuple:
    """为子代理建一个**全新**的上下文，只装规格。

    返回 `(context_builder, isolation_record)`。

    ## 为什么不接受父级的 ContextBuilder

    签名里没有这个参数，是刻意的。若允许传入，函数就得**遍历父级轨迹**
    去挑哪些能带 —— 而"挑"这件事永远是漏的：上游加一种新消息类型，
    隔离就漏一个洞，且**不报错**。

    全新上下文只有一种风险：少给了必要信息（表现为子代理做得不好，可见）；
    复用父级上下文的风险是**多给了不该给的**（表现为隔离失效，不可见）。
    两个风险不对称，所以选前者。
    """
    from .context import ContextBuilder

    ctx = ContextBuilder(prefix)

    # 只装规格。这一条 append 就是全部入向信息。
    ctx.append_user(spec.task)

    rec = IsolationRecord(agent_id=spec.name)
    rec.given.append(f"任务规格（{len(spec.task)} 字符）")
    rec.given.append("同一套工具定义（工具能力不因隔离而减少）")
    if spec.read_paths:
        listed = []
        for rel in spec.read_paths:
            full = os.path.join(workspace, rel)
            state = "存在" if os.path.isfile(full) else "不存在"
            listed.append(f"{rel}（{state}）")
        rec.given.append("显式允许读取：" + "、".join(listed))
    for what, why in WITHHELD_TEMPLATE:
        rec.withheld.append(f"{what} —— {why}")
    rec.context_messages = len(ctx)
    rec.parent_context_leaked = False

    return ctx, rec


# ══════════════════════════════════════════════════════════════════
# 验证者提示词：签名的形状就是隔离
# ══════════════════════════════════════════════════════════════════
def build_verification_prompt(spec: SubAgentSpec, artifacts: Dict[str, Any]) -> str:
    """构造验证者的提示词。

    ## 这个函数的签名是隔离策略的一部分

    只有 `spec`（原始规格）与 `artifacts`（**原始产物**）。
    **没有 `claim` 参数** —— 它不可能读到生成者的话。
    这不是"约定不要传"，是"没有地方可传"。

    为什么非这样不可：文档 7.3 实测（2500+ 对话、25 万次推理）
    摘要会朝上一个 agent 的结论方向压缩。若验证者先读了
    "我改好了，测试通过"这句话，它的判断就被引到了那个方向上 ——
    而且**它自己不会知道**。

    ## 产物里给什么

    只给客观事实：改了哪些文件、跑了哪些命令、退出码多少、工具调用序列。
    这些都能被独立复核（文件在磁盘上、退出码在事件流里）。

    ## 为什么要额外说"去读文件"

    实测踩到的**设计缺口**：第一版只给产物清单，结果验证者
    （真实的 deepseek-v4.1-flash）一次工具都没调用，直接回
    "calc.py 的内容不可见，无法确认 add 是否已修好"。
    它的推理没错（清单确实不含内容），但它的**正确做法应该是去读**——
    它只读沙箱、有 read_file 工具，读文件是被期待的。

    「隔离」不等于「只能看摘要」。摘要只是定位线索，
    真凭据在磁盘上。不把这句话写清楚，验证者就会停在"看不出来"上，
    而那是**假阴性**：明明能核实，却报"无法判断"。
    """
    lines = [
        "你是这次任务的**独立验证者**。",
        "",
        "你没有看到生成者的推理过程、对话历史或它的自我陈述 —— 这是刻意的。",
        "你只有下面两项事实：原始任务规格，和它的客观产物。",
        "请只依据这两项判断，**不要推测生成者「本来想做什么」**。",
        "",
        "## 原始任务规格",
        spec.task,
        "",
        "## 客观产物（可独立复核）",
    ]
    files = artifacts.get("files_changed") or []
    if files:
        lines.append(f"落盘的文件（{len(files)} 个）：")
        for f in files[:20]:
            lines.append(f"  · {f}")
    else:
        lines.append("落盘的文件：**无**")
    cmds = artifacts.get("commands") or []
    if cmds:
        lines.append(f"执行过的命令（{len(cmds)} 条，含退出码）：")
        for c in cmds[:20]:
            lines.append(f"  · [{c.get('exit_code')}] {(c.get('command') or '')[:140]}")
    else:
        lines.append("执行过的命令：无")
    seq = artifacts.get("tool_sequence") or []
    lines.append(f"工具调用序列：{' → '.join(seq) if seq else '（无）'}")
    if artifacts.get("gate_blocked"):
        lines.append(f"收尾前闸门拦截次数：{artifacts['gate_blocked']}")
    lines.append(f"运行结束状态：{artifacts.get('status')}")
    lines.extend([
        "",
        "## 要求",
        "1. 逐条对照规格：**要求的动作是否真的发生了**？依据必须是你核实过的事实——",
        "   清单本身，或你亲自读到的文件内容。",
        "2. **清单里没有的，不要直接判「没做」**，先去读确认；读不到才算没做。",
        "   反过来也成立：**清单里有的，也不要直接判「做了」**——"
        "文件在列表里只说明它被写过，不说明写对了。",
        "3. 指出产物无法支持判断的部分（例如「是否美观」这类无法从产物得出的事项）。",
        "4. 最后给一个结论：满足 / 不满足 / 无法判断（选一个，不要含糊）",
        "   ",
        "## 关于「产物清单不够用」时该怎么做（重要）",
        "",
        "上面只是**产物清单**（哪些文件动了、哪些命令跑了、退出码多少），"
        "不是文件内容。",
        "如果你需要核实文件内容，**请直接调用 `read_file` 去读**——"
        "你是只读沙箱，读文件是允许的，也是被期待的。",
        "",
        "**不要**因为清单里没有内容就停在「无法确认」。"
        "「无法确认」只适用于**读了之后仍然无法判断**的情况。",
        "",
        "具体来说，下面这些都必须去读、而不是靠猜：",
        "· `apply_patch` 改了某文件 → 读它，看改动是否真的落在该落的地方",
        "· 建了一个测试文件 → 读它，看断言是否真能捕获原来的 bug",
        "· 退出码 0 → 读测试文件，判断这个 0 是不是因为断言太弱/没有断言",
        "",
        "一个把测试写得毫无断言、却仍返回 0 的用例，是最典型的"
        "「测试通过但功能没做对」—— 只有读内容才能发现它。",
    ])
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# 产物提取：只从结构化事实里取
# ══════════════════════════════════════════════════════════════════
def extract_artifacts(facts: TraceFacts, status: str,
                      gate_blocked: int = 0) -> Dict[str, Any]:
    """从 `TraceFacts`（结构化事实）提取可复核的产物。

    **不解析模型文本。** 与 preflight / capability / verify 同一条纪律：
    模型文本可能已被提示注入操纵，不能作为证据来源。
    """
    files = facts.wrote_paths()
    cmds: List[Dict[str, Any]] = []
    for e in facts.executions:
        if e.get("tool") != "shell":
            continue
        cmd = str((e.get("args") or {}).get("command") or "")
        cmds.append({
            "command": cmd,
            "exit_code": e.get("exit_code"),
            "ok": bool(e.get("ok")),
        })
    return {
        "status": status,
        "files_changed": files,
        "commands": cmds,
        "tool_sequence": [e.get("tool") for e in facts.executions],
        "tool_count": len(facts.executions),
        "gate_blocked": gate_blocked,
    }


# ══════════════════════════════════════════════════════════════════
# 运行器
# ══════════════════════════════════════════════════════════════════
def run_subagent(spec: SubAgentSpec, *, workspace: str,
                 model,
                 sandbox: "SandboxMode",
                 run_dir: Optional[str] = None,
                 approval_handler=None,
                 loop_config=None) -> SubAgentResult:
    """跑一个隔离的子代理。

    ## 为什么签名里**没有**父级的前缀与上下文

    父级的 `StaticPrefix` 是按父级角色写的系统提示，子代理要的是
    **按自己角色写**的那一份（`_role_system_prompt`）。把父级前缀当参数传进来，
    等于给了它一条"顺手复用"的路 —— 而那条路复用的不只是提示词，
    还包括提示词里可能夹带的上游任务描述与结论。

    `parent_ctx` 就更不该有：本模块的全部意义就是不给它上下文。

    所以参数只有"环境"（workspace / model / sandbox / 落盘位置），
    没有"内容"。内容的唯一入向通道是 `spec`。
    """
    from .context import StaticPrefix
    from .loop import AgentLoop, LoopConfig, build_default_registry
    from .policy import ApprovalPolicy, Policy, SandboxMode
    from .session import Session

    ws = os.path.realpath(workspace)
    reg, shell = build_default_registry(ws)

    # 子代理的系统提示：在共享工具定义的前提下，只替换角色描述。
    # 工具定义必须与父级**逐字节一致**吗？不必 —— 但工具**顺序**必须一致
    # （本项目铁律：注册顺序即输出顺序）。用同一份 specs 最省心。
    role_prompt = _role_system_prompt(spec)
    prefix = StaticPrefix(role_prompt, reg.specs())

    ctx, rec = build_isolated_context(prefix, spec, ws)

    # 沙箱：子代理默认**不比父级宽**。spec 显式指定时以 spec 为准，
    # 但不能越过父级的允许范围 —— 子代理不该有父级没有的权限。
    want_sbx = sandbox
    if spec.sandbox:
        try:
            want_sbx = SandboxMode(spec.sandbox)
        except ValueError:
            want_sbx = sandbox
    # 只允许**收紧**，不允许放宽
    rank = {SandboxMode.READ_ONLY: 0, SandboxMode.WORKSPACE_WRITE: 1,
            SandboxMode.FULL_ACCESS: 2}
    sbx = want_sbx if rank[want_sbx] <= rank[sandbox] else sandbox
    if not spec.allow_write and rank[sbx] >= rank[SandboxMode.WORKSPACE_WRITE]:
        # 不写工作区 → 收紧到只读。这一条对**验证者**尤其重要：
        # 有写权限的验证者可以改产物让自己通过（BenchJack 的 conftest.py 手法）。
        sbx = SandboxMode.READ_ONLY

    sub_run_dir = None
    if run_dir:
        sub_run_dir = os.path.join(run_dir, "subagents", spec.name)

    sess = Session(ws, getattr(model, "name", "sub") or "sub", "openai-compatible",
                   sbx.value, ApprovalPolicy.ON_REQUEST.value, run_dir=sub_run_dir)

    pol = Policy(sbx, ApprovalPolicy.ON_REQUEST, ws)
    cfg = loop_config or LoopConfig(max_iterations=spec.max_iterations)

    # 子代理**不跑完成前检查**（preflight）。
    # 实测踩到：不关它的话，验证者会在收尾前收到一条
    # 「任务要求对工作区做改动，但整个会话里没有任何写操作，请去补做」——
    # 那条消息假设接收者就是执行者，而验证者的职责恰恰是**不代工**。
    #
    # 更根本的理由：preflight 的反馈是**父级任务规格**驱动的，
    # 把它注入子代理上下文，等于把上游的意图描述又带进了本该隔离的上下文。
    # 隔离要覆盖的正是"上游想让下游做什么"。
    cfg.preflight_enabled = False

    loop = AgentLoop(model, reg, prefix, pol, sess, cfg,
                     approval_handler=approval_handler)

    # ── 能力上限：独立授予 ──
    # grantee 用子代理的 name —— 这是 CapScope "权限不自动传导"的落点。
    ceiling = derive_ceiling(spec.task, sbx, readonly_hint=not spec.allow_write,
                             grantee=spec.name)
    ledger = loop.ledgers.open(ceiling)
    loop.ledger = ledger
    loop.router.ledger = ledger

    # ── 子上下文替换：把 loop 自己的 ContextBuilder 换成隔离的那一个 ──
    # 为什么必须整体替换而不是"清空再填"：清空再填会留下忘记清的可能。
    # 换成一个**从没装过父级消息**的对象，泄漏在物理上不可能。
    loop.context = ctx
    sess.attach_context(ctx, reg.names())

    t0 = time.time()
    result = loop.run(spec.task)
    dur = round(time.time() - t0, 2)

    # 断言隔离真的成立：loop 的上下文里不该有父级消息
    rec.context_messages = len(loop.context)
    rec.parent_context_leaked = rec.context_messages > 1 + _expected_extra(spec)
    if rec.parent_context_leaked:
        raise AssertionError(
            f"子代理 {spec.name} 的上下文里有 {rec.context_messages} 条消息，"
            f"超出规格应有的条数 —— 隔离失效。")

    artifacts = extract_artifacts(loop.facts, result.status)
    artifacts["duration_s"] = dur

    sub = SubAgentResult(
        agent_id=spec.name,
        status=result.status,
        artifacts=artifacts,
        claim=result.answer or "",
        claim_is_evidence=False,
        isolation=rec,
        capability=ledger.report()["summary"],
        run_dir=sess.run_dir,
        iterations=result.iterations,
        tool_calls=result.tool_calls,
    )

    # 把结果发到**子代理自己的**事件流（不是父级的）。
    #
    # 为什么需要这一步：子代理的循环会发 turn.completed，但那里面
    # 只有"结束状态"，没有"产物"。父级/UI 需要的是能一眼核对的产物。
    # 落在子代理自己的 stream 里，父级要转发就转发、要只看汇总就看汇总 ——
    # 而**绝不会**因为"想让父级看见"就把它塞进父级的上下文。
    try:
        sess.bus.emit("subagent.completed",
                      agent_id=spec.name, role=spec.role,
                      status=result.status,
                      artifacts=artifacts,
                      claim=result.answer or "",
                      claim_is_evidence=False,
                      isolation=rec.to_json(),
                      capability=ledger.report()["summary"],
                      iterations=result.iterations,
                      tool_calls=result.tool_calls)
    except Exception:
        # 事件发不出去不该影响返回值（书 §5：错误路径禁用副作用逻辑）
        pass

    shell.reset()
    return sub


def _expected_extra(spec: SubAgentSpec) -> int:
    """规格之外**允许**存在的消息条数（状态栏等追加式消息）。

    状态栏是循环自己追加的（`inject_status`），它不含上游信息，
    因此不算泄漏。这里给一个宽松上限，正是因为它是追加式的、
    条数不固定；但它**绝不可能是上游消息**（上游消息只能从
    `build_isolated_context` 进去，而那里只 append 了一次）。
    """
    return 64


def _role_system_prompt(spec: SubAgentSpec) -> str:
    """按角色生成系统提示。

    角色提示只描述**这次角色该怎么做**，不含任何上游信息 ——
    它本身也要过隔离这道关。
    """
    common = (
        "你是一个被隔离的子代理。你没有上游的对话历史 —— 这是刻意的：\n"
        "让你独立判断，而不是顺着已有的结论走。\n"
        "你只看到交给你的任务规格。请仅依据你**自己实际观察到的**事实作答，\n"
        "不要假设上游已经做了任何事。\n"
    )
    if spec.role == "verifier":
        return common + (
            "\n你的角色是**验证者**。你的职责是判断任务是否真的完成了，"
            "依据只能是客观产物（文件是否在磁盘上、命令退出码是否为 0）。\n"
            "你读不到生成者的推理或自我陈述；若你拿到了它们，说明隔离出了问题，"
            "应当明确说出来。\n"
            "产物里没有的，一律算「没有做」 —— 不要因为任务看起来简单就推断已完成。\n"
        )
    return common + (
        "\n你的角色是**执行者**。完成交给你的任务，并在最后说明你实际做了什么、"
        "依据是什么（落盘路径 / 退出码）。\n"
    )


# ══════════════════════════════════════════════════════════════════
# 编排：一次「生成 → 隔离验证」的组合
# ══════════════════════════════════════════════════════════════════
@dataclass
class VerificationOutcome:
    """一次隔离验证的结论。

    这里刻意**不合成一个"可信度分数"**：分数会让人停止追问
    "为什么是这个值"，而 code + evidence 会让人去复核
    （与 evaluate.py 同一条纪律，来自 BenchJack 的教训）。
    """

    generator: Optional[SubAgentResult]
    verifier: Optional[SubAgentResult]
    verdict_text: str = ""
    model_independence: Dict[str, Any] = field(default_factory=dict)
    # 验证者被授予的能力上限（应当比生成者更严）
    verifier_ceiling: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        return {
            "generator": self.generator.to_json() if self.generator else None,
            "verifier": self.verifier.to_json() if self.verifier else None,
            "verdict_text": self.verdict_text,
            "model_independence": self.model_independence,
            "verifier_ceiling": self.verifier_ceiling,
        }

    def to_text(self) -> str:
        lines = []
        if self.generator:
            lines.append(self.generator.to_text())
        if self.verifier:
            lines.append("")
            lines.append(self.verifier.to_text())
            lines.append("")
            lines.append(f"验证结论（验证者自述）：{self.verdict_text[:400]}")
        mi = self.model_independence
        lines.append("")
        lines.append(f"模型独立性：{mi.get('verdict')}（风险 {mi.get('risk')}）"
                     f" —— {mi.get('note', '')}")
        if self.verifier_ceiling:
            c = self.verifier_ceiling
            lines.append(f"验证者的上限：可写={c.get('allows_write_workspace')} "
                         f"任务只读={c.get('readonly_task')}")
        return "\n".join(lines)
