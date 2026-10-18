# Harness 后端架构

> **权威依据**：`深入理解 AI Agent`（bojieli，Apache-2.0）—— 见 `docs/book-notes.md` 摘录。
> **工程参考**：OpenAI Codex CLI（Rust，Apache-2.0 开源）的真实模块划分。
>
> 本文是 `harness/` 的设计基线。**每一个设计决策都标注来源**：
> `[书 §x.y]` = 来自 AI Agent 书；`[Codex]` = 来自 Codex 源码结构；`[本项目]` = 我们的取舍。

---

## 一、总公式与边界

书的第一章给出权威定义：

```
Agent = Model + Harness
Harness = 上下文管理 + 工具接口 + 约束 + 验证 + 纠正
Agent ↔ Environment
Harness 不是"模型之外的一切"，而是「Agent 边界内、模型之外」的运行与治理层。
```

**边界纪律**（书 §1「Harness 工程」）：
- 属于 Harness：工具定义、调用适配器、沙箱的**权限与重置机制**、上下文装配、事件流。
- 属于 Environment：沙箱内随行动变化的**文件与进程**、外部数据库、网页、用户。
- 部署位置不决定归属 —— 即便仿真环境与 Agent 同进程，它仍是 Environment。

`[本项目]` 落到目录：`harness/` 是 Harness；被操作的**工作目录**是 Environment。

---

## 二、主循环：ReAct + 五功能闭环

书的骨架（§1）：

```python
observation = Environment.observe()
trajectory = [observation]
while true:
    actions = Model(Harness.build_context(trajectory))
    if len(actions) == 0: break
    allowed_actions = Harness.constrain(actions)
    observation = Environment.apply(allowed_actions)
    if not Harness.verify(Environment):
        observation = Harness.correct(Environment)
    trajectory.append(allowed_actions, observation)
```

`[本项目]` 的实现映射：

| 书中环节 | 本项目模块 |
| --- | --- |
| `Harness.build_context` | `context.py` |
| `Model` | `model.py`（Provider 抽象，可离线 stub） |
| `Harness.constrain` | `policy.py` |
| `Environment.apply` | `tools/*` 执行器 |
| `Harness.verify` | `verify.py` |
| `Harness.correct` | `correct.py` |
| `trajectory` | `session.py` + `protocol.py` |
| 事件流 | `protocol.py`（SSE 推给前端） |

**终止条件**（书 §5「终止」）：最大迭代轮数、会话预算上限、连续失败超阈值升级人工。

---

## 三、上下文工程：三条铁律

书 §2 讲的 KV Cache 纪律，是后端**最容易做错、且错了代价最大**的一块。三条铁律：

1. **系统提示词与工具定义一旦确定就不要改**。任何改动（哪怕一个空格）都会使首个不同 token
   及其后的缓存失效；改动越靠前，代价越大。
2. **动态信息永远追加到末尾** —— 时间戳、环境状态、用户偏好等，作为**新消息**追加，
   绝不修改已有的系统提示词。
3. **使用标准 API 消息格式，不要自行拼接成 `USER: ... ASSISTANT: ...`**。

书 §2 列出的**五种错误模式**（我们必须在代码里主动防住）：

| 错误模式 | 后果 | 我们的防线 |
| --- | --- | --- |
| 动态系统提示词（注入时间戳/余额） | 全量缓存失效 | 静态前缀冻结，环境信息走尾部状态栏 |
| 动态用户配置 | 同上 | 同上 |
| 工具定义动态排序 | 从首个变动的工具起失效 | 工具**按注册顺序**输出，禁止重排 |
| 滑动窗口裁历史 | 前缀破坏 + 丢失关键工具结果 | **禁止**；用 append-only + 结构化压缩 |
| 文本格式化消息 | 偏离训练分布，重复调用/忽略结果 | 严格 role/content 结构 |

书 §2 还给了**缓存作为架构约束**的四条实践（Claude Code 的教训）：
- 提示词的结构**由缓存边界决定**，动态元素必须放在边界之后（否则 N 个二值条件产生 2^N 个缓存键）。
- 子 Agent 与父 Agent **字节级对齐**才能命中缓存。
- 工具结果的替换串**在首次出现时冻结**，会话重启后用完全相同的串。
- **改前缀不只是多花钱，还可能丢掉推理**（preserved thinking 把思考签名绑在前缀上，
  前缀一改，该 thinking block 及其后全部失效）。

`[本项目]` 的落地：
- `ContextBuilder` 持有一个**冻结的 static_prefix**（system + tools），只在会话创建时构建一次，
  之后任何写入都走 `append()`。
- 提供 `prefix_hash()` 与 `assert_stable()` —— **把「前缀不变」变成可断言的测试**，
  而不只是一句口头纪律。
- 压缩（`compact()`）**只作用于轨迹中段**，且必须是**追加一条摘要消息**，
  绝不原地删改旧消息（书 §2 的 append-only 纪律）。

---

## 四、工具接口（对齐 Codex）

### 4.1 管线：ToolRegistry + ToolRouter

`[Codex]` Codex 的 Rust core 维护 `ToolRegistry` 与 `ToolRouter`，所有工具调用走同一条管线：

```
模型发出 tool call → ToolRouter → 审批检查 → 沙箱选择 → 允许/拒绝 → 执行 → 结果回模型
```

`[本项目]`：

```
tools/registry.py   —— 注册表：声明、schema、按注册顺序输出
tools/router.py     —— 路由：策略判定 → 沙箱选择 → 执行 → 事件
```

### 4.2 工具清单

书 §5 给了搜索与编辑工具的完整对比。`[Codex]` 的真实工具面：

| 工具 | Codex 对应 | 我们的实现 |
| --- | --- | --- |
| `read_file` | `read_file` | 按行号范围读取，**返回内容带行号前缀**（书 §5） |
| `list_dir` | `list_dir` | 目录列举 |
| `glob` | `glob_file_search` | 文件名模式匹配 |
| `grep` | `rg` | **纯 Python 正则搜索**，带行号/上下文行/类型过滤 |
| `apply_patch` | `apply_patch` | **V4A 补丁格式**，写前校验唯一性 |
| `shell` | `shell_command` | 受沙箱约束，带 `timeout_ms` |

**为什么 `read_file` 必须带行号**（书 §5）：模型可精确引用「第 42 行」，减少歧义，
使后续编辑更可靠。这是低成本高回报的一条。

### 4.3 编辑原语：apply_patch / V4A

`[Codex]` Codex 的 `apply_patch` 用自定义 **V4A 补丁格式**（`UpdateFileChunk` + `Hunk`），
1,444 行解析器**边流式接收边解析边校验，补丁对不上原文件就拒绝落盘**。

```
*** Begin Patch
*** Add File: path/to/new_file.rs
+line one
*** Update File: path/to/existing.rs
@@ context line @@
-old line
+new line
*** Delete File: path/to/obsolete.rs
*** End Patch
```

两个好处（`[Codex]`）：**省 token**（改十行不必重写千行）；**可审计可回滚**（每次编辑是结构化补丁）。

书 §4 列了五种编辑方案并做了对比。`[Codex]` 与 Claude Code 都用「旧字符串 → 新字符串」，
而 Codex 进一步升级成了 V4A 结构化补丁。`[本项目]` 采用 V4A 的子集
（Add / Update / Delete 三种操作 + `@@` 上下文定位），并保留**唯一性校验**：
上下文匹配不唯一就拒绝，绝不猜。

### 4.4 工具描述与保真

书 §4 的三条硬纪律，我们照抄进工具 schema 文档：

1. **说清「什么时候用」，而不只是「能做什么」**。
2. **明确边界**（做不到什么）。书原话：*大多数工具调用失败的根因不是模型不知道工具能做什么，
   而是不知道工具不能做什么。*
3. **参数用具体例子代替抽象规范**。

书 §4 的**保真性**原则我们作为硬约束：**禁止静默输入转换、禁止静默参数注入**。
模型感知的世界与工具操作的世界之间不能存在系统性偏差。需要规范化时必须
**在描述里说明 + 在返回里告知**。

---

## 五、约束：沙箱 × 审批 正交双轴

`[Codex]` 两把旋钮互相独立：

| 沙箱模式 | 文件系统 | 网络 |
| --- | --- | --- |
| `read-only` | 只读 | 受限 |
| `workspace-write` | 工作区内可写 | 默认受限 |
| `danger-full-access` | 全开 | 全开，**无审批** |

| 审批策略 | 行为 |
| --- | --- |
| `untrusted` | 每条有后果的命令都要批准 |
| `on-request` | 沙箱内自动放行，越界才问（默认） |
| `never` | 全自动 |

`[Codex]` 的实现细节：`ExecPolicy::prompt_is_rejected_by_policy()` 判定，
拒绝则直接回错，需审批则发 `ExecApprovalRequestEvent`；`apply_patch` 另走
`assess_patch_safety()`（补丁是否完全落在可写工作区内 / 是否碰禁改路径 / 是否符合当前沙箱权限）。

`[本项目]` 的判定表（`policy.py`）：

```
                     read-only   workspace-write   danger-full-access
read (工作区内)          run          run                run
write (工作区内)         refuse       run/ask*           run
write (工作区外)         refuse       ask/refuse**       run
shell (无副作用)         ask          run                run
shell (有副作用)         ask          ask                run
network                  refuse       ask                run
```

- `*` 由审批策略决定：`untrusted` → ask；`on-request` → run；`never` → run
- `**` `on-request` 下越界仍要 ask；`never` 下拒绝（never 不等于无限制）

**判据**（书 §1 的故障安全默认值）：**所有能力默认关闭，必须显式开放**。

书 §5 补充的**过程约束**（不只是结果）：`rm -rf`、删除生产数据、覆盖未读文件这类
危险动作要有专门检查与审批 —— 因为**即使结果正确，用错误方法达成也不行**。

---

## 六、验证与纠正

### 6.1 验证（书 §5「反馈信号」）

书强调 **安全检查只看结构化数据**，不看模型自由生成的文本 ——
因为后者可能已被提示注入操纵。

`[本项目]` 的验收基线：
- **`verify` 只读结构化字段**（exit code、diff 是否落在工作区、补丁是否匹配唯一）。
- 编辑后自动跑 linter/语法检查，**把结果作为工具返回值的一部分**（书 §5 的「即时反馈机制」）。
- 计划步骤的达成判定由**证据**驱动（对应 `/goal` 的 Evaluator）。

### 6.2 纠正：分级恢复（书 §5「恢复」）

按对用户的透明程度**逐级升级**，能用低级别解决就不升级：

1. **静默重试** —— 指数退避 + 随机抖动；尊重服务端等待提示；
   **区分前台与后台调用**（后台失败直接放弃，否则"重试放大"挤占主链路配额）。
2. **降级与接续** —— 输出触顶则提升上限重发，再不够则追加元指令从断点续写。
3. **暴露给用户** —— 全部自动手段用尽后才呈现，**并附上已尝试过的恢复动作**。

### 6.3 故障分类学（书 §5）

四层：**API 层**（429/超时/截断）、**工具层**（幻觉调用/参数畸形/反复同一错误）、
**上下文层**（窗口溢出/压缩失败/轨迹结构损坏）、**控制流层**（死循环/死亡螺旋）。

检测两件事：
- **可重试性** —— 可重试（限流/抖动）才重试；不可重试（参数不合法/权限不足）必须改变输入。
- **模式** —— **重复调用指纹**（对「工具名 + 参数」做哈希，相同指纹反复出现 = 无进展循环）；
  **连续失败计数**（每条恢复路径独立计数）。

### 6.4 终止与熔断（书 §5）

**每条恢复路径都要有上限**，阈值来自生产数据而非拍脑袋。书给的实例：
Claude Code 的压缩熔断是「连续 3 次」—— 因为实测出现过单会话连续失败三千余次、
逾千会话 50 次以上连续失败，3 次是「绝大多数故障已恢复」与「继续重试基本无望」的拐点。

**死亡螺旋**防护两条：错误路径上**禁用一切会再次调用模型的副作用逻辑**；递归深度计数器。

### 6.5 活性与完整性监控（书 §5）

- 流式连接最危险的失败不是断开，而是**静默卡死** → 需要独立的**空闲看门狗**
  （超时无新输出即判定卡死，杀掉挂起的流并重试）。原则：**每个长连接都需要活性信号**。
- **完整性监控**：工具调用缺少配对的结果消息时，在注入上下文前**自动修复配对**，
  而不是把结构异常抛给模型。

---

## 七、事件流协议

`[Codex]` 事件流是多种入口共享语义的关键。事件应区分类型，而不是把所有内容混成字符串。

`[本项目]` 的事件类型（与前端已实现的渲染一一对应）：

| 事件 | 关键字段 | 前端动作 |
| --- | --- | --- |
| `turn.started` | turn_id、时间 | 创建运行视图 |
| `reasoning` | seq、文本片段 | 增量显示 |
| `tool.requested` | call_id、工具、参数 | **策略校验 / 展示审批** |
| `approval.required` | 风险、确切动作、暂缓事件数 | 凝固成审批闸门 |
| `tool.output` | call_id、stdout/stderr、状态 | 日志、截断与持久化 |
| `file.changed` | 路径、diff、基准版本 | 审查界面更新 |
| `check` | 目标、证据、达成与否 | 自证卡片 |
| `turn.completed` | 结果、用量、终态 | 关闭流并保存摘要 |
| `error` | 层（api/tool/context/control）、可重试性 | 分级呈现 |

**协议纪律**：每个事件都带 `seq`（单调递增），前端可据此做去重与重连补偿。

---

## 八、中立轨迹格式（书 §5「接管」）

书 §5 给出一条关键设计原则：

> **轨迹不该按任何一家的接口格式存储，而应保存为一份中立格式。**
> 每段思考拆成**可移植的文字**与**不可移植的凭证**两部分；
> 工具调用只记录**名称与参数**，标识符在渲染成具体请求时按目标厂商重新生成。

理由：**跨厂商接管能带走的是文字，带不走的是凭证**。
各家对凭证的要求不一致，方案必须**按最严格的一端设计**，并为满足不了的情形准备退路
（把历史工具调用改写成文字叙述）。

`[本项目]` 的 `Message` 结构：

```python
Message = {
  "role": "user" | "assistant" | "tool" | "system",
  "content": str,                    # 可移植的文字
  "reasoning": str | None,           # 可移植的思考文字
  "reasoning_signature": str | None, # 不可移植的凭证（切换时丢弃）
  "tool_calls": [{name, args}],      # 只记名称与参数，id 渲染时再生成
  "tool_call_id": str | None,        # 渲染时按目标厂商重新生成
  "seq": int,                        # 追加序号，便于完整性校验
}
```

中立格式的额外价值（书 §5）：第七章的**评估重放**、第八章的**训练样本构造**、
第九章的**经验提取**，依赖的都是同一份产物。

---

## 九、贯穿全书的五个设计模式（书 §1）

我们逐个落地，并标明在哪个模块：

| 模式 | 定义 | 本项目的落点 |
| --- | --- | --- |
| **提议者—审核者** | 产出与评判由**不共享上下文**的两个角色承担，评判方只看产物 | `verify.py`：验证器只看结构化结果，不看模型推理 |
| **渐进式披露** | 先给可检索的目录，再按需加载细节 | MCP 工具默认只暴露索引，需要时加载完整 schema |
| **只增不改** | 状态以追加演进，已写下的不再回头改 | `context.py` 的 append-only + `prefix_hash()` 断言 |
| **边界集 + 保留集** | 修改需同时在「应当改变」和「不应当影响」两批样本上验证 | `tests/`：改动须同时过回归集与边界集 |
| **最小 diff + 可回滚** | 每次修改尽量小、带来源、可单独回滚 | `apply_patch` 的 V4A + rollout 落盘 |

---

## 十、模块清单（`harness/`）

```
harness/
  __init__.py
  protocol.py     中立轨迹格式 + 事件类型（八、七）
  context.py      静态前缀冻结 + append-only + prefix_hash（三）
  policy.py       沙箱 × 审批 正交双轴 + 过程约束（五）
  verify.py       结构化验证 + 即时反馈（六.1）
  correct.py      分级恢复 + 熔断 + 指纹检测（六.2~6.4）
  session.py      会话、轨迹持久化、rollout 落盘
  model.py        Provider 抽象（可离线 stub，便于无 key 自测）
  tools/
    __init__.py
    registry.py   ToolRegistry：注册顺序即输出顺序（四.1）
    router.py     ToolRouter：策略 → 沙箱 → 执行 → 事件（四.1）
    fs.py         read_file（带行号）/ list_dir / glob
    search.py     纯 Python 正则搜索
    patch.py      apply_patch / V4A（四.3）
    shell.py      受约束的命令执行
  loop.py         ReAct 主循环 + 事件发射
  server.py       HTTP + SSE，零依赖
```

**零依赖纪律**：只用 Python 标准库（对齐前身项目的 harness 慢方案，
也让 `python harness/server.py` 即可跑起来）。

---

## 十一、待验证清单

实现完成后必须实测并通过。**全部 11 项已通过**（`python -m harness.test_harness`，共 118 项测试）：

- [x] **前缀稳定性**：连续 N 轮后 `prefix_hash()` 不变（对应书 §2 铁律 1）
- [x] **append-only**：轨迹只增不改（对应书 §2 纪律）
- [x] **工具顺序固定**：多次构建上下文，工具定义序列字节级一致（对应 §2 错误模式 3）
- [x] **策略判定表**：9 个格子（3 沙箱 × 3 审批）逐个断言
- [x] **apply_patch**：上下文不唯一时必须拒绝（防猜）
- [x] **补丁越界**：落到工作区外的补丁必须被拒
- [x] **重复调用指纹**：同指纹重复 N 次触发熔断
- [x] **工具输出上限**：超限截断 + 全文落盘 + 返回可查看路径
- [x] **配对完整性**：缺 `tool` 结果时自动补配对而不是抛异常
- [x] **事件 seq 单调**：无空洞、无回退
- [x] **审批闸门**：`ask` 时事件流挂起，批准后继续，拒绝后不执行

### 11.1 实测过程中抓到的缺陷（已修，并已写测试锁住）

这些是实现阶段**跑起来才暴露**的问题，不是设计阶段能预见的。逐条记录，
因为每一条都对应一类容易被"看起来正常"掩盖的失败（书 §1 消融实验的警告）。

| # | 缺陷 | 为什么危险 | 修法 |
| --- | --- | --- | --- |
| 1 | **审批闸门是"先拒绝再补执行"** | 同一次调用有两条执行路径 → 重复副作用窗口 | 改为**阻塞式**：工具线程原地等待外部决定，超时故障安全为拒绝 |
| 2 | **同一次工具结果被广播两次** | router 发一条，loop 又发一条 `persisted=True`（无 `call_id`）→ 前端无法合并，渲染成两张卡片，**看起来像执行了两次** | 去掉 loop 的冗余 emit，事件唯一性加测试锁住 |
| 3 | **工作区内相对路径被判为"越界"** | `apply_patch` 给的相对路径按**进程 CWD** 解析，导致**每一次补丁都被误判为越界拒绝** | Action 的路径统一按 workspace 归一后再判定 |
| 4 | **glob 匹配的 basename 兜底完全错** | `fnmatch("a.py", ".git")` 会命中 → 任意文件都可能被"禁改清单"误伤 | 改为段感知匹配器（`**/.git/**` 只匹配该目录下的路径） |
| 5 | **shell 超时机制是假的** | 直接在调用线程 `readline()` 会永久阻塞，`deadline` 判断永远执行不到（`sleep 10` 配 800ms 超时竟然成功了） | 改读线程 + 队列，主流程 `get(timeout=...)` 真正到点就返回 |
| 6 | **Windows 上 shell 输出全乱码** | 用 UTF-8 解码控制台（代码页 936/GBK）→ 中文路径全变替换字符 | `GetConsoleOutputCP()` 探测代码页并匹配编码；`chcp` 是内建命令，直接 exec 必然失败 |
| 7 | **启动横幅与提示符混进命令输出** | `echo hello` 的返回值带着一段版权声明和 `C:\...\ws>hello` | 启动时发同步哨兵吃掉横幅 + 固定 `PROMPT` 标记后按标记剥离前缀 |
| 8 | **`%ERRORLEVEL%` 同行展开取错值** | cmd 在同一行展开的是**上一行**的退出码 → 退出码判定不可信 | 让 `@echo ...%ERRORLEVEL%` 单独成行且紧跟命令 |
| 9 | **cwd 不再被跟踪** | 修 #7 时顺手删了哨兵里的 `%CD%`，导致跨命令的 cwd 状态丢失 | 把 `%CD%` 加回哨兵 |
| 10 | **`.agentpanel` 网格 6 个子元素只写了 5 条轨道**（前端） | `1fr` 落到 `.streambar` 上、真正的 `.stream` 被压成内容高度 → 滚轮失效 | 补第 6 条轨道 |
| 11 | **终端高度用内联 `grid-template-rows` 记录**（前端） | 内联样式会盖掉 `.is-term-collapsed` 类规则 → 「拖过一次之后折叠就失效」 | 改用 `--terminal-h` 变量 |
| 12 | **文件树 `li` 建了却从不 `appendChild`**（前端） | 文件树永远渲染 0 项，只看截图很可能漏掉 | 补 append，并用 DOM 探针而非肉眼校验 |
| 13 | **运行产物被误纳入版本库** | 探针与 `.crucible/runs/` 是"跑一次就有一份"的一次性数据 | `.gitignore` 里精确忽略（保留目录本身，只忽略运行目录） |

另：全量 grep 确认 `harness/` 内**没有** `eval` / `exec` / `shell=True` / `pickle.load` 这类危险写法。
