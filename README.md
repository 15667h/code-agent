# code-agent

对标 Codex 的综合 agent。当前进度：**前端 IDE 工作台原型（纯静态，假数据驱动）**。

## 目录

| 路径 | 说明 |
| --- | --- |
| `index.html` | 工作台主页面 |
| `styles/tokens.css` | 设计令牌（色彩 / 字体 / 空间 / 圆角 / 动效） |
| `styles/app.css` | 布局与组件样式 |
| `scripts/mock-data.js` | 演示数据（文件树 / 正文 / 事件流 / diff / 会话） |
| `scripts/app.js` | 交互逻辑 |
| `docs/codex-architecture.md` | **Codex 架构原理调研**（复刻基线） |
| `docs/design-plan.md` | 设计计划（方向 / 令牌 / 布局 / 自审） |

## 打开方式

无构建、无依赖，直接双击 `index.html` 即可（`file://` 可用）。

若要用探针做几何/交互测量，需要本地 HTTP（`file://` 下 iframe 是跨源，测不了）：

```bash
python -m http.server 8712 --bind 127.0.0.1
# 浏览器打开 http://127.0.0.1:8712/index.html
```

## 前端对标要点（对应 Codex 架构）

- **四栏 IDE**：活动栏 / 侧栏 / 编辑器+终端 / Agent 面板
- **事件流**：逐事件流式渲染 `reasoning` · `tool_call` · `tool_result` · `patch` · `response_complete`
- **审批闸门**（本页记忆点）：沙箱 × 审批的**正交双轴**，受门控动作在事件流里原地凝固
- **计划视图**：逐步推进（不是「一次性完美计划」）
- **自证检查**：对应 Codex 的 Ralph Loop / Evaluator 模型
- **沙箱模式选择器**：`read-only` / `workspace-write` / `danger-full-access`
- **可中断**：Codex 的已知短板，本项目作为差异化能力

## 设计基线

- 美学家族：**工业仪表盘**（黄铜主色 + 冷蓝炭底），刻意避开 AI 紫、酸性绿、VS Code 蓝
- 字体：**IBM Plex Sans**（UI）+ **IBM Plex Mono**（代码），避开 Inter / Roboto
- 无障碍：`prefers-reduced-motion`、可见焦点环、语义标签、`aria-*`、≥40px 热区

## 开发环境

- Node.js：`>=22`
- Python：`>=3.12`

## 已验证

- 语法：`node --check` 全通过
- 结构：行号 = 正文行数 = diff 行数（三者在 320–1600px 全部一致）
- 几何：11 档宽度（320/375/480/700/768/900/1024/1100/1280/1440/1600）横向溢出 **0**
- 交互：闸门批准/拒绝、标签开合、五个侧栏视图、状态栏切换、终端开合、上下文菜单、发送指令 —— 全部通过，**JS 错误 0**
