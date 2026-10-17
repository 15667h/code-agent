/* ============================================================
   假数据：文件树 / 代码 / 事件流 / diff / 会话
   全部为演示数据。命名刻意避开 John Doe / Acme 这类占位词。

   代码正文采用「按行」结构：
     FILE_BODIES[path] = [ 行, 行, ... ]
     行 = [ [角色, 文本], [角色, 文本], ... ]
   角色：kw 关键字 / ty 类型 / st 字符串 / nm 数字 / cm 注释 / fn 函数 / pl 标点
   —— 空数组表示空行。

   这样行数、行号、diff 三者都由同一份数据推导，不可能互相漂移。
   ============================================================ */

const WORKSPACE = {
  repo: "crucible-core",
  branch: "feat/stream-reconnect",
  ahead: 3,
  behind: 0,
  worktree: "crucible-core/.worktrees/stream-reconnect",
  indexStatus: "已索引 · 1,284 文件 · 18.6 万 token",
};

/* ---------- 代码正文（按行）---------- */
const FILE_BODIES = {

  "src/transport/reconnect.ts": [
    [["cm", "// 断线重连：指数退避 + 抖动，同时保证事件不丢序。"]],
    [["cm", "// 关键约束：重连期间到达的 tool_result 必须仍按原 seq 回填。"]],
    [],
    [["kw", "import"], ["pl", " { "], ["ty", "EventFrame"], ["pl", ", "], ["ty", "SessionCursor"], ["pl", " } "], ["kw", "from"], ["st", " \"../protocol.ts\""], ["pl", ";"]],
    [],
    [["kw", "const"], ["pl", " "], ["fn", "BASE_DELAY_MS"], ["pl", " = "], ["nm", "250"], ["pl", ";"]],
    [["kw", "const"], ["pl", " "], ["fn", "MAX_DELAY_MS"], ["pl", " = "], ["nm", "8000"], ["pl", ";"]],
    [["kw", "const"], ["pl", " "], ["fn", "JITTER_RATIO"], ["pl", " = "], ["nm", "0.25"], ["pl", ";"]],
    [],
    [["kw", "export interface"], ["pl", " "], ["ty", "ReconnectPolicy"], ["pl", " {"]],
    [["pl", "  "], ["fn", "attempt"], ["pl", ": "], ["ty", "number"], ["pl", ";"]],
    [["pl", "  "], ["fn", "delay"], ["pl", ": "], ["ty", "number"], ["pl", ";"]],
    [["pl", "  "], ["fn", "resumeFrom"], ["pl", ": "], ["ty", "SessionCursor"], ["pl", ";"]],
    [["pl", "}"]],
    [],
    [["cm", "/**"]],
    [["cm", " * 退避序列。抖动必须作用在「已算出的延迟」上，"]],
    [["cm", " * 而不是作用在 attempt 上 —— 否则两个并发会话会同步重试。"]],
    [["cm", " */"]],
    [["kw", "export function"], ["pl", " "], ["fn", "nextDelay"], ["pl", "("], ["fn", "attempt"], ["pl", ": "], ["ty", "number"], ["pl", "): "], ["ty", "number"], ["pl", " {"]],
    [["pl", "  "], ["kw", "const"], ["pl", " "], ["fn", "cap"], ["pl", " = "], ["fn", "Math"], ["pl", "."], ["fn", "min"], ["pl", "("], ["fn", "BASE_DELAY_MS"], ["pl", " * "], ["nm", "2"], ["pl", " ** "], ["fn", "attempt"], ["pl", ", "], ["fn", "MAX_DELAY_MS"], ["pl", ");"]],
    [["pl", "  "], ["kw", "const"], ["pl", " "], ["fn", "jit"], ["pl", " = "], ["fn", "cap"], ["pl", " * "], ["fn", "JITTER_RATIO"], ["pl", ";"]],
    [["pl", "  "], ["kw", "return"], ["pl", " "], ["fn", "cap"], ["pl", " - "], ["fn", "jit"], ["pl", " + "], ["fn", "Math"], ["pl", "."], ["fn", "random"], ["pl", "() * "], ["nm", "2"], ["pl", " * "], ["fn", "jit"], ["pl", ";"]],
    [["pl", "}"]],
    [],
    [["cm", "// 只有还没收到 response_complete 的帧才值得重试。"]],
    [["kw", "export function"], ["pl", " "], ["fn", "shouldRetry"], ["pl", "("], ["fn", "frame"], ["pl", ": "], ["ty", "EventFrame"], ["pl", "): "], ["ty", "boolean"], ["pl", " {"]],
    [["pl", "  "], ["kw", "if"], ["pl", " ("], ["fn", "frame"], ["pl", "."], ["fn", "kind"], ["pl", " === "], ["st", "\"response_complete\""], ["pl", ") "], ["kw", "return"], ["pl", " "], ["kw", "false"], ["pl", ";"]],
    [["pl", "  "], ["kw", "return"], ["pl", " "], ["fn", "frame"], ["pl", "."], ["fn", "retryable"], ["pl", " ?? "], ["kw", "true"], ["pl", ";"]],
    [["pl", "}"]],
    [],
    [["cm", "/**"]],
    [["cm", " * 会话游标。重连必须从上次被确认的 seq 续起，"]],
    [["cm", " * 否则重放窗口内的 tool_result 会被重复回填。"]],
    [["cm", " */"]],
    [["kw", "export function"], ["pl", " "], ["fn", "resumeCursor"], ["pl", "("], ["fn", "last"], ["pl", ": "], ["ty", "SessionCursor"], ["pl", ", "], ["fn", "saw"], ["pl", ": "], ["ty", "EventFrame"], ["pl", "[]): "], ["ty", "SessionCursor"], ["pl", " {"]],
    [["pl", "  "], ["kw", "const"], ["pl", " "], ["fn", "acked"], ["pl", " = "], ["fn", "saw"], ["pl", "."], ["fn", "filter"], ["pl", "("], ["fn", "f"], ["pl", " => "], ["fn", "f"], ["pl", "."], ["fn", "acked"], ["pl", ");"]],
    [["pl", "  "], ["kw", "if"], ["pl", " ("], ["fn", "acked"], ["pl", "."], ["fn", "length"], ["pl", " === "], ["nm", "0"], ["pl", ") "], ["kw", "return"], ["pl", " "], ["fn", "last"], ["pl", ";"]],
    [["pl", "  "], ["kw", "return"], ["pl", " "], ["fn", "Math"], ["pl", "."], ["fn", "max"], ["pl", "("], ["fn", "last"], ["pl", ", ..."], ["fn", "acked"], ["pl", "."], ["fn", "map"], ["pl", "("], ["fn", "f"], ["pl", " => "], ["fn", "f"], ["pl", "."], ["fn", "seq"], ["pl", "));"]],
    [["pl", "}"]],
  ],

  "src/transport/sse-client.ts": [
    [["cm", "// SSE 客户端：逐事件解析，事件类型标记驱动流式 UI。"]],
    [],
    [["kw", "import"], ["pl", " { "], ["ty", "EventFrame"], ["pl", ", "], ["ty", "EVENT_KINDS"], ["pl", " } "], ["kw", "from"], ["st", " \"../protocol.ts\""], ["pl", ";"]],
    [["kw", "import"], ["pl", " { "], ["fn", "nextDelay"], ["pl", ", "], ["fn", "shouldRetry"], ["pl", ", "], ["fn", "resumeCursor"], ["pl", " } "], ["kw", "from"], ["st", " \"./reconnect.ts\""], ["pl", ";"]],
    [],
    [["kw", "type"], ["pl", " "], ["ty", "EventKind"], ["pl", " = ("], ["kw", "typeof"], ["pl", " "], ["fn", "EVENT_KINDS"], ["pl", ")[number];"]],
    [],
    [["kw", "export class"], ["pl", " "], ["ty", "SseClient"], ["pl", " {"]],
    [["pl", "  "], ["kw", "private"], ["pl", " "], ["fn", "cursor"], ["pl", ": "], ["ty", "number"], ["pl", " = "], ["nm", "0"], ["pl", ";"]],
    [["pl", "  "], ["kw", "private"], ["pl", " "], ["fn", "attempt"], ["pl", ": "], ["ty", "number"], ["pl", " = "], ["nm", "0"], ["pl", ";"]],
    [["pl", "  "], ["kw", "private"], ["pl", " "], ["fn", "subscribers"], ["pl", " = "], ["kw", "new"], ["pl", " "], ["ty", "Map"], ["pl", "<"], ["ty", "EventKind"], ["pl", ", "], ["ty", "Set"], ["pl", "<("], ["fn", "f"], ["pl", ": "], ["ty", "EventFrame"], ["pl", ") => "], ["ty", "void"], ["pl", ">>();"]],
    [],
    [["pl", "  "], ["fn", "on"], ["pl", "("], ["fn", "kind"], ["pl", ": "], ["ty", "EventKind"], ["pl", ", "], ["fn", "fn"], ["pl", ": ("], ["fn", "f"], ["pl", ": "], ["ty", "EventFrame"], ["pl", ") => "], ["ty", "void"], ["pl", "): "], ["ty", "() => void"], ["pl", " {"]],
    [["pl", "    "], ["kw", "const"], ["pl", " "], ["fn", "set"], ["pl", " = "], ["kw", "this"], ["pl", "."], ["fn", "subscribers"], ["pl", "."], ["fn", "get"], ["pl", "("], ["fn", "kind"], ["pl", ") ?? "], ["kw", "new"], ["pl", " "], ["ty", "Set"], ["pl", "();"]],
    [["pl", "    "], ["fn", "set"], ["pl", "."], ["fn", "add"], ["pl", "("], ["fn", "fn"], ["pl", ");"]],
    [["pl", "    "], ["kw", "this"], ["pl", "."], ["fn", "subscribers"], ["pl", "."], ["fn", "set"], ["pl", "("], ["fn", "kind"], ["pl", ", "], ["fn", "set"], ["pl", ");"]],
    [["pl", "    "], ["kw", "return"], ["pl", " () => "], ["fn", "set"], ["pl", "."], ["fn", "delete"], ["pl", "("], ["fn", "fn"], ["pl", ");"]],
    [["pl", "  }"]],
    [],
    [["pl", "  "], ["cm", "// 收到一帧：按类型分发给订阅者。"]],
    [["pl", "  "], ["fn", "dispatch"], ["pl", "("], ["fn", "frame"], ["pl", ": "], ["ty", "EventFrame"], ["pl", "): "], ["ty", "void"], ["pl", " {"]],
    [["pl", "    "], ["kw", "if"], ["pl", " ("], ["fn", "frame"], ["pl", "."], ["fn", "seq"], ["pl", " <= "], ["kw", "this"], ["pl", "."], ["fn", "cursor"], ["pl", ") "], ["kw", "return"], ["pl", ";"], ["cm", "  // 去重"]],
    [["pl", "    "], ["kw", "this"], ["pl", "."], ["fn", "cursor"], ["pl", " = "], ["fn", "frame"], ["pl", "."], ["fn", "seq"], ["pl", ";"]],
    [["pl", "    "], ["kw", "this"], ["pl", "."], ["fn", "attempt"], ["pl", " = "], ["nm", "0"], ["pl", ";"]],
    [["pl", "    "], ["kw", "const"], ["pl", " "], ["fn", "set"], ["pl", " = "], ["kw", "this"], ["pl", "."], ["fn", "subscribers"], ["pl", "."], ["fn", "get"], ["pl", "("], ["fn", "frame"], ["pl", "."], ["fn", "kind"], ["pl", ");"]],
    [["pl", "    "], ["fn", "set"], ["pl", "?.forEach(("], ["fn", "fn"], ["pl", ") => "], ["fn", "fn"], ["pl", "("], ["fn", "frame"], ["pl", "));"]],
    [["pl", "  }"]],
    [],
    [["pl", "  "], ["kw", "async"], ["pl", " "], ["fn", "resume"], ["pl", "(): "], ["ty", "Promise"], ["pl", "<"], ["ty", "void"], ["pl", "> {"]],
    [["pl", "    "], ["kw", "const"], ["pl", " "], ["fn", "delay"], ["pl", " = "], ["fn", "nextDelay"], ["pl", "("], ["kw", "this"], ["pl", "."], ["fn", "attempt"], ["pl", "++);"]],
    [["pl", "    "], ["kw", "await"], ["pl", " "], ["fn", "sleep"], ["pl", "("], ["fn", "delay"], ["pl", ");"]],
    [["pl", "    "], ["kw", "const"], ["pl", " "], ["fn", "from"], ["pl", " = "], ["fn", "resumeCursor"], ["pl", "("], ["kw", "this"], ["pl", "."], ["fn", "cursor"], ["pl", ", "], ["kw", "this"], ["pl", "."], ["fn", "pending"], ["pl", ");"]],
    [["pl", "    "], ["kw", "await"], ["pl", " "], ["kw", "this"], ["pl", "."], ["fn", "open"], ["pl", "("], ["fn", "from"], ["pl", ");"]],
    [["pl", "  }"]],
    [["pl", "}"]],
  ],

  "harness/approval.py": [
    [["cm", "# 审批闸门：沙箱模式 × 审批策略 正交判定。"]],
    [["cm", "# 两把旋钮互相独立 —— 沙箱决定「能碰什么」，策略决定「什么时候问」。"]],
    [],
    [["kw", "from"], ["pl", " "], ["ty", "enum"], ["pl", " "], ["kw", "import"], ["pl", " "], ["ty", "Enum"]], 
    [["kw", "from"], ["pl", " "], ["ty", "dataclasses"], ["pl", " "], ["kw", "import"], ["pl", " "], ["ty", "dataclass"]],
    [],
    [],
    [["kw", "class"], ["pl", " "], ["ty", "SandboxMode"], ["pl", "("], ["ty", "Enum"], ["pl", "):"]],
    [["pl", "    "], ["fn", "READ_ONLY"], ["pl", " = "], ["st", "\"read-only\""]],
    [["pl", "    "], ["fn", "WORKSPACE_WRITE"], ["pl", " = "], ["st", "\"workspace-write\""]],
    [["pl", "    "], ["fn", "FULL_ACCESS"], ["pl", " = "], ["st", "\"danger-full-access\""]],
    [],
    [],
    [["kw", "class"], ["pl", " "], ["ty", "ApprovalPolicy"], ["pl", "("], ["ty", "Enum"], ["pl", "):"]],
    [["pl", "    "], ["fn", "UNTRUSTED"], ["pl", " = "], ["st", "\"untrusted\""]],
    [["pl", "    "], ["fn", "ON_REQUEST"], ["pl", " = "], ["st", "\"on-request\""]],
    [["pl", "    "], ["fn", "NEVER"], ["pl", " = "], ["st", "\"never\""]],
    [],
    [],
    [["kw", "class"], ["pl", " "], ["ty", "Action"], ["pl", ":"]],
    [["pl", "    "], ["fn", "needs_network"], ["pl", ": "], ["ty", "bool"], ["pl", " = "], ["kw", "False"]],
    [["pl", "    "], ["fn", "escapes_worktree"], ["pl", ": "], ["ty", "bool"], ["pl", " = "], ["kw", "False"]],
    [["pl", "    "], ["fn", "writes"], ["pl", ": "], ["ty", "bool"], ["pl", " = "], ["kw", "False"]],
    [],
    [],
    [["kw", "def"], ["pl", " "], ["fn", "decide"], ["pl", "("], ["fn", "action"], ["pl", ": "], ["ty", "Action"], ["pl", ", "], ["fn", "sbx"], ["pl", ": "], ["ty", "SandboxMode"], ["pl", ", "], ["fn", "pol"], ["pl", ": "], ["ty", "ApprovalPolicy"], ["pl", ") -> "], ["ty", "str"], ["pl", ":"]],
    [["pl", "    "], ["st", "\"\"\"判定三态：run / ask / refuse。\"\"\""]],
    [["pl", "    "], ["kw", "if"], ["pl", " "], ["fn", "sbx"], ["pl", " "], ["kw", "is"], ["pl", " "], ["ty", "SandboxMode"], ["pl", "."], ["fn", "FULL_ACCESS"], ["pl", ":"]],
    [["pl", "        "], ["kw", "return"], ["pl", " "], ["st", "\"run\""], ["cm", "  # 无沙箱即无门控"]],
    [["pl", "    "], ["kw", "if"], ["pl", " "], ["fn", "action"], ["pl", "."], ["fn", "needs_network"], ["pl", " "], ["kw", "or"], ["pl", " "], ["fn", "action"], ["pl", "."], ["fn", "escapes_worktree"], ["pl", ":"]],
    [["pl", "        "], ["kw", "return"], ["pl", " "], ["st", "\"refuse\""], ["pl", " "], ["kw", "if"], ["pl", " "], ["fn", "pol"], ["pl", " "], ["kw", "is"], ["pl", " "], ["ty", "ApprovalPolicy"], ["pl", "."], ["fn", "NEVER"], ["pl", " "], ["kw", "else"], ["pl", " "], ["st", "\"ask\""]],
    [["pl", "    "], ["kw", "if"], ["pl", " "], ["fn", "pol"], ["pl", " "], ["kw", "is"], ["pl", " "], ["ty", "ApprovalPolicy"], ["pl", "."], ["fn", "UNTRUSTED"], ["pl", ":"]],
    [["pl", "        "], ["kw", "return"], ["pl", " "], ["st", "\"ask\""]],
    [["pl", "    "], ["kw", "return"], ["pl", " "], ["st", "\"run\""]],
  ],
};

/* ---------- 文件树 ---------- */
/* 行号一律取自 FILE_BODIES，避免手写数字与正文对不上 */
function _ln(path, fallback) {
  const b = FILE_BODIES[path];
  return b ? b.length : fallback;
}

const FILE_TREE = [
  {
    name: "src", kind: "dir", open: true, children: [
      {
        name: "transport", kind: "dir", open: true, children: [          { name: "sse-client.ts", kind: "file", status: "M", lines: _ln("src/transport/sse-client.ts", 0), path: "src/transport/sse-client.ts" },
          { name: "reconnect.ts", kind: "file", status: "A", lines: _ln("src/transport/reconnect.ts", 0), path: "src/transport/reconnect.ts" },
        ]
      },
      { name: "protocol.ts", kind: "file", status: "M", lines: 342, path: "src/protocol.ts" },
      { name: "session.ts", kind: "file", status: "", lines: 508, path: "src/session.ts" },
      { name: "index.ts", kind: "file", status: "", lines: 41, path: "src/index.ts" },
    ]
  },
  {
    name: "harness", kind: "dir", open: false, children: [
      { name: "loop.py", kind: "file", status: "", lines: 388, path: "harness/loop.py" },
      { name: "sandbox.py", kind: "file", status: "", lines: 256, path: "harness/sandbox.py" },
      { name: "approval.py", kind: "file", status: "M", lines: _ln("harness/approval.py", 0), path: "harness/approval.py" },
    ]
  },
  {
    name: "tests", kind: "dir", open: false, children: [
      { name: "e2e_reconnect.py", kind: "file", status: "A", lines: 210, path: "tests/e2e_reconnect.py" },
    ]
  },
  { name: "AGENTS.md", kind: "file", status: "", lines: 68, path: "AGENTS.md" },
  { name: "package.json", kind: "file", status: "", lines: 52, path: "package.json" },
  { name: ".codexignore", kind: "file", status: "", lines: 14, path: ".codexignore" },
];

/* ---------- 工作区改动清单（源码管理视图用）---------- */
const WORKSPACE_CHANGES = [
  { path: "src/transport/reconnect.ts", st: "A", a: _ln("src/transport/reconnect.ts", 0), d: 0, openable: true },
  { path: "src/transport/sse-client.ts", st: "M", a: 19, d: 3, openable: true },
  { path: "harness/approval.py", st: "M", a: 26, d: 21, openable: true },
  { path: "tests/e2e_reconnect.py", st: "A", a: 210, d: 0, openable: false },
];

/* ---------- 已打开的标签 ---------- */
/* 简短文件名代表未命名的临时标签，没有伪造的行数 */
const OPEN_TABS = [
  { path: "src/transport/reconnect.ts", dirty: true },
  { path: "src/transport/sse-client.ts", dirty: false },
  { path: "harness/approval.py", dirty: false },
  { path: "src/protocol.ts", dirty: false },
];

/* ---------- 补丁描述 ---------- */
/* diff 不手写，由 FILE_BODIES 在运行时生成 —— 正文改了 diff 自动跟着改 */
const PATCHES = {
  "src/transport/reconnect.ts": {
    isNew: true,
    verified: "npx tsc --noEmit → 0 错误",
  },
};

/* ---------- 事件流：agent loop 的真实事件序列 ---------- */
/* 事件类型对应 Codex 的事件标记协议：reasoning / tool_call / tool_result / response_complete */
const EVENT_STREAM = [
  {
    id: "e1", kind: "reasoning", state: "done", dur: "0.4s",
    text: "重连逻辑要在「事件不丢序」这个约束下工作。先确认现有 SSE 客户端如何处理 cursor，再决定退避放在哪一层。",
  },
  {
    id: "e2", kind: "tool", tool: "code_search", state: "done", dur: "1.1s",
    title: "rg \"cursor\" -n src/",
    meta: [{ k: "命中", v: "7" }, { k: "文件", v: "3" }],
    output: [
      { t: "plain", text: "src/transport/sse-client.ts:9:  private cursor: number = 0;" },
      { t: "plain", text: "src/transport/sse-client.ts:23:    this.cursor = frame.seq;" },
      { t: "plain", text: "src/protocol.ts:88:  export type SessionCursor = number;" },
      { t: "plain", text: "harness/loop.py:141:    cursor = session.cursor" },
      { t: "dim", text: "7 命中 · 3 文件 · 0.94s" },
    ],
  },
  {
    id: "e3", kind: "tool", tool: "read", state: "done", dur: "0.6s",
    title: "读取 src/transport/sse-client.ts",
    meta: [{ k: "行", v: "34" }, { k: "tokens", v: "612" }],
    output: [
      { t: "plain", text: "// SSE 客户端：逐事件解析，事件类型标记驱动流式 UI。" },
      { t: "dim", text: "… 截断至 10,000 token 上限以下" },
    ],
  },
  {
    id: "e4", kind: "reasoning", state: "done", dur: "1.2s",
    text: "客户端只记 cursor 不持久化，也没处理「重放窗口内重复回填」。退避若直接作用在 attempt 上，两个并发会话会同步重试——把抖动作用在已算出的延迟上是更稳的做法。",
  },
  {
    id: "e5", kind: "patch", state: "done", dur: "0.9s",
    file: "src/transport/reconnect.ts", patch: "src/transport/reconnect.ts",
  },

  /* ---- 闸门：这是本页的记忆点 ---- */
  {
    id: "e6", kind: "gate", state: "pending",
    action: "shell", detail: "npm test -- --run tests/e2e_reconnect.py",
    risk: "工作区内命令",
    reason: "要验证新增的退避序列是否让重连测试通过。命令会读 .env.test 并起本地 mock server。",
    deferred: 4,
  },
];

/* 闸门之后被暂缓的事件（点「批准」后才注入） */
const DEFERRED_AFTER_GATE = [
  {
    id: "e7", kind: "tool", tool: "shell", state: "done", dur: "4.8s",
    title: "npm test -- --run tests/e2e_reconnect.py",
    meta: [{ k: "通过", v: "12" }, { k: "失败", v: "0" }, { k: "耗时", v: "4.8s" }],
    output: [
      { t: "pass", text: "✓ 抖动作用在延迟而非 attempt (2 ms)" },
      { t: "pass", text: "✓ 指数上限收敛到 8s (0 ms)" },
      { t: "pass", text: "✓ 重连期间 tool_result 保持原 seq (11 ms)" },
      { t: "pass", text: "✓ response_complete 不触发重试 (1 ms)" },
      { t: "dim", text: "12 passed (4.82s)" },
    ],
  },
  {
    id: "e8", kind: "reasoning", state: "done", dur: "0.8s",
    text: "测试全绿。sse-client 的 on() 还缺一个退订路径，但那个不属于本次范围。",
  },
  {
    id: "e9", kind: "check", state: "done", dur: "0.3s",
    title: "自证 / 目标达成检查",
    model: "evaluator",
    items: [
      { ok: true, text: "退避序列已实现且抖动位置正确" },
      { ok: true, text: "重连不丢事件序 —— 有测试覆盖" },
      { ok: true, text: "typecheck 0 错误" },
    ],
  },
];

/* ---------- 会话历史 ---------- */
const SESSIONS = [
  {
    id: "s1", active: true, title: "SSE 断线重连实现",
    branch: "feat/stream-reconnect", time: "刚刚", files: 3, add: 79, del: 3,
    model: "gpt-5.4-codex", turns: 9, tokens: "38.2k",
  },
  {
    id: "s2", title: "审批策略空值兜底",
    branch: "fix/approval-null", time: "1 小时前", files: 2, add: 11, del: 6,
    model: "gpt-5.4-codex", turns: 5, tokens: "12.7k",
  },
  {
    id: "s3", title: "worktree 隔离清理",
    branch: "chore/worktree-gc", time: "昨天", files: 7, add: 63, del: 41,
    model: "gpt-5.4-mini", turns: 14, tokens: "51.9k",
  },
  {
    id: "s4", title: "协议事件类型对齐",
    branch: "refactor/event-kinds", time: "2 天前", files: 4, add: 38, del: 52,
    model: "gpt-5.4-codex", turns: 11, tokens: "44.1k",
  },
];

/* ---------- 计划视图 ---------- */
const PLAN = {
  goal: "让 SSE 客户端在断线后自动重连，且重连期间不丢失事件顺序。",
  steps: [
    { id: "p1", state: "done", text: "定位现有 cursor 处理方式", evidence: "rg 命中 7 处" },
    { id: "p2", state: "done", text: "实现指数退避 + 抖动", evidence: "reconnect.ts · 新增 40 行" },
    { id: "p3", state: "active", text: "补重连端到端测试" },
    { id: "p4", state: "todo", text: "跑整体回归" },
  ],
};

/* ---------- 终端 ---------- */
const TERMINAL_LINES = [
  { t: "cmd", text: "$ git status --short" },
  { t: "out", text: " M src/transport/sse-client.ts" },
  { t: "out-hi", text: "?? src/transport/reconnect.ts" },
  { t: "out-hi", text: "?? tests/e2e_reconnect.py" },
  { t: "out", text: " M harness/approval.py" },
  { t: "cmd", text: "$ npx tsc --noEmit" },
  { t: "dim", text: "(无输出 = 通过)" },
];

/* ---------- 自证状态（Ralph Loop 的 Evaluator）---------- */
const VERIFY = {
  model: "gpt-5.4-mini (evaluator)",
  checked: 3, total: 3,
};

/* ---------- 活动栏 ---------- */
const ACTIVITY = [
  { id: "explorer", icon: "files", label: "资源管理器" },
  { id: "search", icon: "search", label: "搜索" },
  { id: "scm", icon: "branch", label: "源码管理", badge: 4 },
  { id: "sessions", icon: "history", label: "会话历史" },
  { id: "mcp", icon: "plug", label: "MCP 工具" },
];

/* ---------- MCP 工具面板 ---------- */
const MCP_TOOLS = [
  { name: "fs", desc: "文件读写", enabled: true },
  { name: "git", desc: "版本操作", enabled: true },
  { name: "postgres", desc: "数据库查询", enabled: false },
  { name: "playwright", desc: "浏览器自动化", enabled: false },
];

/* ---------- 状态栏 ---------- */
const STATUSBAR = {
  model: "gpt-5.4-codex",
  tokenUsed: 38.2, tokenCap: 200,
  sandbox: "workspace-write",
  approval: "on-request",
  index: "已索引",
  diagnostics: { errors: 0, warnings: 2 },
  encoding: "UTF-8",
  indent: "2 spaces",
};
