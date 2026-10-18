# code-agent

对标 Codex 的综合 agent。当前进度：**前端 IDE 工作台 + 后端 harness（可跑通）**。

架构基线来自两处权威依据：

- **《AI Agent Book》**（`docs/book-notes.md` 是提炼出的纪律清单）——核心公式
  `Agent = LLM + 上下文 + 工具`，而 **Harness = 上下文管理 + 工具接口 + 约束 + 验证 + 纠正**。
- **Codex 的实现**（`docs/codex-architecture.md`、`docs/harness-architecture.md`）——
  架构参考与逐条对照。
- **2026 前沿论文**（`docs/harness-research-2026.md`）——ETCLOVG 七层综述、
  三 harness 架构收敛研究、证据携带终止（ECT）、压缩悬崖、CapScope 能力作用域、
  Self-Harness / AHE 自我进化。配套差距诊断见 `docs/harness-gap-diagnosis.md`。

## 目录

| 路径 | 说明 |
| --- | --- |
| `index.html` | 工作台主页面 |
| `styles/tokens.css` | 设计令牌（色彩 / 字体 / 空间 / 圆角 / 动效） |
| `styles/app.css` | 布局与组件样式 |
| `styles/ui-kit.css` | 交互原语样式（Toast / 弹窗 / 右键菜单 / 命令面板 / Tooltip） |
| `scripts/mock-data.js` | 演示数据（后端不在时的离线原型用） |
| `scripts/ui-kit.js` | 交互原语层（焦点陷阱、Toast、确认框、**表单对话框**、右键菜单、命令面板、无障碍播报） |
| `scripts/harness-bridge.js` | **桥接层**：harness 事件翻译 + 模型接入配置读写 |
| `scripts/app.js` | 业务与交互逻辑 |
| `harness/protocol.py` | 中立轨迹格式 + 事件类型（`seq` 单调不变式） |
| `harness/context.py` | 静态前缀冻结 + append-only + `prefix_hash()` 断言 |
| `harness/policy.py` | 沙箱 × 审批 **9 格判定表** + 危险动作专项检查 |
| `harness/tools/` | `registry` / `router` / `fs` / `search` / `patch`(V4A) / `shell` |
| `harness/verify.py` | 只读结构化字段的验证 + 编辑后即时 lint + **目标证据绑定**（三态：satisfied/pending/unmatched） |
| `harness/preflight.py` | **完成前检查**：收尾前对照任务规格核查「要求的动作是否真做过」（LangChain 实测 +13.7pp） |
| `harness/correct.py` | 四级故障分类 + 熔断 + 重复指纹 + 看门狗 + 死亡螺旋防护 |
| `harness/model.py` | **模型接入**：OpenAI 兼容端点（三字段）+ 连通性自检 + 离线 stub |
| `harness/loop.py` | ReAct 主循环 + 五功能闭环 + 事件发射 |
| `harness/server.py` | 零依赖 HTTP + SSE，同源托管前端 |
| `harness/retention.py` | **类型化保留**：约束 verbatim 永不进摘要器（防压缩悬崖） |
| `harness/test_harness.py` | **207 项测试**，每项标注书中出处 |
| `docs/book-notes.md` | 《AI Agent Book》Harness 纪律提炼 |
| `docs/harness-research-2026.md` | **2026 前沿调研**：ETCLOVG 七层、架构收敛、ECT、压缩悬崖、CapScope、Self-Harness、AHE |
| `docs/harness-gap-diagnosis.md` | **差距诊断**：逐层读代码，含两个「空壳」实证 |
| `docs/codex-architecture.md` | Codex 架构原理调研 |
| `docs/harness-architecture.md` | 后端架构设计（含逐条对照与待验证清单） |
| `docs/design-plan.md` | 前端设计计划 |

## 运行

### 方式一：只看前端原型（后端不在）

无构建、无依赖，直接双击 `index.html` 即可（`file://` 可用）。
桥接层探测不到后端时会**静默降级**，页面用 mock 数据演示，不白屏。

### 方式二：跑完整链路（推荐）

```bash
python harness/server.py --port 8787
# 打开 http://127.0.0.1:8787/index.html
# 顶栏 chip 变绿即表示已接上 harness
```

服务同源托管前端 —— 不需要 CORS，消除一整类问题（书 §1 的边界治理）。

## 接入任意模型（三个字段）

模型接入**只有一条路径：OpenAI 兼容端点**。点左下角 **设置 → 模型接入**，
填三项即可：

| 字段 | 示例 | 说明 |
| --- | --- | --- |
| 接口地址 | `https://api.deepseek.com/v1` | 末尾带不带 `/chat/completions` 都行，会自动补 |
| API Key | `sk-…` | 只存在服务端 |
| 模型名称 | `deepseek-chat` | 该端点上可用的模型 id |

**为什么只做 OpenAI 兼容一种**：为每家写适配器是**永不收敛**的工作。而
`/chat/completions` 已经是事实标准 —— 国内主流厂家（DeepSeek / 通义 / Kimi /
智谱 / 硅基流动…）与几乎所有中转站都提供兼容端点。所以接入面收敛成三个字段，
**换任何厂家或中转，代码一行不动**。

表单里有**测试连接**：填完先验一次再保存，避免「保存成功但根本用不了」。
失败诊断会指向真正的原因，而不是丢一个 HTTP 码：

| 情况 | 提示 |
| --- | --- |
| Key 错 | API Key 无效或已过期 |
| 模型名不存在 | **模型名称**不存在 —— 该端点没有这个模型 id |
| 地址错 | **接口地址**不对 —— 注意末尾要不要带 `/v1` |
| 网络不通 | 连不上 `<url>`，请检查地址与网络 |

> 404 有两种完全不同的含义，判据来自响应正文而不是状态码 ——
> **指错方向比不给提示更糟**，用户会去改一个本来正确的字段。

配置落在 `<workspace>/.crucible/config.json`（含明文 key，**已在 `.gitignore`**，
文件权限收紧到 `0600`）。也可用环境变量 `CRUCIBLE_BASE_URL` /
`CRUCIBLE_API_KEY` / `CRUCIBLE_MODEL` 注入（容器/CI 不想落盘时）。

接口层面：

```bash
GET  /api/config          # 读配置（api_key 只回掩码）
POST /api/config          # 存配置；api_key 不传=沿用，传空串=清除
POST /api/config/test     # 连通性自检，返回可读诊断
POST /api/config/clear    # 清除
```

未配置时运行**自动走离线脚本**，不会直接失败 —— 前端也看得到真实驱动的事件流
（不是前端伪造的假事件）：

```bash
curl -X POST http://127.0.0.1:8787/api/run \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"看看工作区","offline_script":["list:.","read:src/a.py","final:完成"]}'
```


## 后端对标要点

- **前缀冻结**：`StaticPrefix` 冻结系统提示与工具定义，`assert_prefix_stable()` 每次调用前自检
  —— 保住 KV-cache 命中率。动态信息（状态栏）只**追加到末尾**，绝不插入前缀。
- **append-only 轨迹**：模型可见的记录一律追加，不做历史重排（否则击穿缓存前缀）。
- **工具顺序固定**：注册顺序即输出顺序，`specs()` 字节级一致。
- **中立轨迹格式**：`tool_calls` 只记 `name`/`args`，不可移植的凭证（思考签名）单独存。
- **沙箱 × 审批正交双轴**：`read-only`/`workspace-write`/`danger-full-access` ×
  `untrusted`/`on-request`/`never`，9 格判定表；危险动作（`rm -rf` 等）即使策略允许也强制征询，
  且**不接受"批准并记住"**。
- **审批闸门是阻塞式的**：工具线程原地等待外部决定，而非"先拒绝再补执行"
  —— 后者会给同一次调用开出两条执行路径，制造重复副作用。超时**故障安全为拒绝**。
- **apply_patch 用 V4A 格式**：补丁必须唯一匹配，越界即拒绝。
- **工具输出上限**：超限截断（保留前 60% / 后 40%），全文落盘供按需查看。
- **验证只看结构化字段**：不看模型自由生成的文本（可能已被提示注入操纵）。
- **四级故障分类**：API / 工具 / 上下文 / 控制流；每层恢复路径独立计数与上限。
- **重复调用指纹**：`工具名 + 参数` 哈希，相同指纹反复出现 = 无进展循环。
- **看门狗**：长连接最危险的失败是静默卡死，需独立活性信号。
- **事件唯一性**：一次执行 = 一条 `tool.output` 事件（有测试锁住）。

## 前端对标要点（对应 Codex 架构）

- **四栏 IDE**：活动栏 / 侧栏 / 编辑器+终端 / Agent 面板
- **事件流**：逐事件流式渲染 `reasoning` · `tool_call` · `tool_output` · `file_changed` · `check`
- **审批闸门**（本页记忆点）：沙箱 × 审批的**正交双轴**，受门控动作在事件流里原地凝固
- **计划视图**：逐步推进（不是「一次性完美计划」）
- **自证检查**：对应 Codex 的 Ralph Loop / Evaluator 模型
- **沙箱模式选择器**：`read-only` / `workspace-write` / `danger-full-access`

## 交互细节（大厂级操作）

**通用**
- `Ctrl+K` 命令面板：模糊检索命令 / 文件 / 会话 / 斜杠命令，带分组与空状态
- `?` 快捷键总览（5 组 23 条）
- Toast 全部带进度条、悬停暂停、可撤销、可关闭；破坏性操作一律走确认框或带撤销
- 右键菜单：标签、文件、会话、状态栏各项都有自己的上下文菜单，全程键盘可达
- 统一 Tooltip：只在文本被截断时才出现（`data-tip="auto"`）
- 焦点管理：浮层打开即入焦、关闭即归还，模态内 Tab 循环不逃逸

**文件树** —— 完整键盘导航（`↑↓→← Home/End Enter F2`）、双击内联重命名

**标签** —— 中键关闭、拖拽重排、脏标签关闭前确认、溢出渐隐 + 更多下拉 + 计数

**终端** —— 真实输入行、`↑↓` 命令历史、模拟回显、`Ctrl+L` 清屏、可拖拽调高

**输入坞** —— `@` 文件补全、`/` 斜杠命令菜单

**状态与持久化** —— 主题跟随系统；`localStorage` 记住主题 / 面板 / 侧栏宽 / 终端高 / 视图 / 筛选；
首屏骨架屏；滚动渐隐；侧栏可拖拽调宽

## 设计基线

- 美学家族：**工业仪表盘**（黄铜主色 + 冷蓝炭底），刻意避开 AI 紫、酸性绿、VS Code 蓝
- 字体：**IBM Plex Sans**（UI）+ **IBM Plex Mono**（代码），避开 Inter / Roboto
- 无障碍：`prefers-reduced-motion`、可见焦点环、语义标签、`aria-*`、≥40px 热区

## 开发环境

- Node.js：`>=22`（仅用于语法校验）
- Python：`>=3.12`（后端**零第三方依赖**，只用标准库）

## 已验证

**后端**（`python -m harness.test_harness`）
- **207 项测试全部通过**，覆盖架构文档第十一节的待验证清单与实测抓到的缺陷
- 关键不变式：前缀稳定性、append-only、工具顺序固定、9 格策略、补丁唯一性/越界、
  重复指纹熔断、输出上限、配对完整性、事件 `seq` 单调、闸门挂起与回灌、事件唯一性、shell 跨平台
- 模型接入：地址归一化（中转站四种写法）、api_key 绝不回传明文、掩码可核对、
  配置落盘权限 0600、损坏配置不炸、key 留空=沿用、404 区分「地址错」与「模型名错」

**端到端**
- 离线运行：`status=completed`、事件 `seq` 连续、`tool.requested` 与 `tool.output` 一一对应、
  读取结果带行号、中立格式只含 `name/args`、危险命令被识别并拒绝 —— 全通过
- 闸门：挂起 → 幽灵 `call_id` 被拒（404）→ 回灌批准**恰好执行一次** → 拒绝路径不执行 —— 全通过
- 模型接入（打本地 OpenAI 兼容桩）：三字段保存与落盘、自检路径与鉴权头、
  四种失败诊断各自指向真因、`200 + error` 形态被识别、key 沿用/清除、清除配置、
  未配置时自动走离线脚本 —— 全通过
- **目标闸门（真实模型 deepseek-v4.1-flash）**：文件落盘 → `check/scope=goal newly_met`
  → `all_met:true` → `completed`；反向用例（要求创建但指示不动手）正确报 `all_met:false`
- **完成前检查（真实模型）**：
  · 误拦防线：正常任务 4 轮完成，`preflight blocks: 0`，理由「任务完成。」
  · 拦住假完成：明示"只回答完成即可" → 拦下 `no_write_attempt` + `named_path_untouched`，
    最终理由**如实列出缺口与依据**，不谎报完成
  · 逼出真验证：要求「跑通测试」→ 模型自主建 `calc.py` + `test_calc.py`，
    跑 unittest 拿到退出码 0，干净完成

**前端**（CDP 探针，真实视口）
- 12 档宽度（320/375/480/700/768/860/900/1024/1100/1280/1440/1600）横向溢出**全为 0**
- 交互探针 15 项全过，JS 错误 **0**：文件树渲染、事件流、状态栏、行号、
  输入后按钮启用、提交后清空、三态 `check` 卡片视觉区分

> 窄屏必须用 CDP `Emulation.setDeviceMetricsOverride`：`--window-size` 有最小宽度
> （320/375/480 会被夹到 500）；在页面里改 `documentElement.style.width` 会造出
> **随宽度线性增长的假溢出**（实测 1600 档虚报 859px）。
- 浏览器：前端接上 harness 后真实渲染 live 事件卡片；模型接入对话框三字段、
  实时地址预览、测试连接、必填校验、保存后状态栏同步、重开回填与掩码、焦点陷阱
  —— 全通过，**JS 错误 0**

**前端**
- 语法：`node --check` 四个脚本全通过；CSS 花括号配平；HTML 标签配平
- 结构：行号 = 正文行数 = diff 行数
- 几何：多档宽度（320→1600）页面横向溢出 **全部为 0**，无被裁控件，无未命名按钮
- 交互：命令面板、Toast、确认框、右键菜单、快捷键面板、事件筛选、闸门、终端历史、
  `@` 与 `/` 补全、五视图切换、侧栏拖拽、持久化 —— 全部通过
