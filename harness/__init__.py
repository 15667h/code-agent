"""Crucible Harness —— 对标 Codex 的 agent 运行与治理层。

权威依据：《深入理解 AI Agent》（bojieli/ai-agent-book）
    Agent = Model + Harness
    Harness = 上下文管理 + 工具接口 + 约束 + 验证 + 纠正

工程参考：OpenAI Codex CLI 的模块划分
    ToolRegistry + ToolRouter 管线 / ExecPolicy / apply_patch(V4A) / 事件流

设计文档：
    docs/harness-architecture.md   架构决策（每个决策都标注来源）
    docs/book-notes.md             书中要点摘录

目录：
    protocol.py   中立轨迹格式 + 事件协议（书 §5 中立即接管）
    context.py    静态前缀冻结 + append-only + prefix_hash 断言（书 §2 三铁律）
    policy.py     沙箱 × 审批 9 格判定 + 过程约束（书 §1 §5）
    verify.py     结构化验证 + 即时 lint + 目标自证（书 §1 §5）
    correct.py    故障分类 + 指纹检测 + 熔断 + 看门狗（书 §5）
    session.py    会话与 rollout 落盘（可重放、可审计）
    model.py      OpenAI 兼容端点接入（三字段：地址 / Key / 模型）+ 离线 stub
    loop.py       ReAct 主循环 + 五功能闭环
    tools/        注册表、路由、文件、搜索、补丁、shell
    server.py     零依赖 HTTP + SSE
"""

__all__ = ["protocol", "context", "policy", "verify", "correct",
           "session", "model", "loop", "tools"]
