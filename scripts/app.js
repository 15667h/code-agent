/* ============================================================
   Crucible — 交互逻辑
   纯 ES5+ 兼容写法，普通 script 加载（file:// 下可直接打开）。
   数据来自 mock-data.js 中的全局变量。
   ============================================================ */
(function () {
  "use strict";

  /* ---------- 小工具 ---------- */
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function svg(paths, cls) {
    var s = '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"' + (cls ? ' class="' + cls + '"' : '') + '>';
    s += paths;
    s += "</svg>";
    var wrap = document.createElement("span");
    wrap.innerHTML = s;
    return wrap.firstChild;
  }

  /* 图标路径集 */
  var ICONS = {
    file: '<path d="M4 1.5h5l3 3v10H4z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/><path d="M9 1.5v3h3" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
    dirOpen: '<path d="M2 4h4l1.2 1.5H14v8.5H2z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
    dirClosed: '<path d="M2 4h4l1.2 1.5H14v8.5H2z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
    check: '<path d="M3.5 8.5l3 3 6-6.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    chevron: '<path d="M5 7l3 3 3-3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
    x: '<path d="M4.5 4.5l7 7M11.5 4.5l-7 7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
    warning: '<path d="M8 2.5l6 11H2z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M8 6.5v3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><circle cx="8" cy="11.4" r="0.8" fill="currentColor"/>',
    /* 「无法判定」专用：问号。与 warning 的三角刻意区分 ——
       不确定不是坏消息，画成警示三角等于把未知说成了失败。 */
    help: '<circle cx="8" cy="8" r="6.1" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M6.2 6.3c0-1 .8-1.7 1.8-1.7s1.8.7 1.8 1.6c0 1.4-1.8 1.5-1.8 3.1" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><circle cx="8" cy="11.6" r="0.8" fill="currentColor"/>',
    pause: '<rect x="4" y="3.5" width="2.6" height="9" rx="0.8" fill="currentColor"/><rect x="9.4" y="3.5" width="2.6" height="9" rx="0.8" fill="currentColor"/>',
    shield: '<path d="M8 1.8l5 1.8v4.6c0 3-2 5.3-5 6.2-3-.9-5-3.2-5-6.2V3.6z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>',
    terminal: '<path d="M3.5 5l3 3-3 3M9 11h4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>',
    search: '<circle cx="7" cy="7" r="4" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M10 10l3.5 3.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>',
    edit: '<path d="M11.2 2.6l2.2 2.2-8.2 8.2H3v-2.2z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
    book: '<path d="M3 3h4.5c.8 0 1.5.7 1.5 1.5V13c0-.6-.7-1-1.5-1H3zM13 3H8.5C7.7 3 7 3.7 7 4.5V13c0-.6.7-1 1.5-1H13z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
    clock: '<circle cx="8" cy="8" r="5.8" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M8 4.6V8l2.4 1.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
    plus: '<path d="M8 3.5v9M3.5 8h9" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
    play: '<path d="M5.5 3.5l7 4.5-7 4.5z" fill="currentColor"/>',
    plug: '<path d="M6 2.5v3M10 2.5v3M4.5 5.5h7v2a3.5 3.5 0 0 1-7 0z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/><path d="M8 11v2.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
    trash: '<path d="M3 4.5h10M6.5 4.5V2.8h3v1.7M4.5 4.5l.7 8.7h5.6l.7-8.7" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>',
  };

  var TOOL_ICON = {
    code_search: ICONS.search,
    read: ICONS.book,
    shell: ICONS.terminal,
    apply_patch: ICONS.edit,
  };

  /* ---------- URL 参数 ----------
     支持 `?workspace=<绝对路径>` 指定工作区 —— 这让同一个工作台可以直接
     指向任意目录，而不用改任何代码。也支持 `?prompt=<文本>` 与 `?autorun=1`
     用于演示：打开页面即自动发起一次运行，便于"亲眼看到"整条链路。
     刻意从 URL 读而不是从 localStorage 读：URL 是**显式**的，
     适合"我这次要在这个目录上干活"这种一次性意图。 */
  /* URL 上的 `offline` 是字符串，而后端要的是**字符串数组**。
     直接传字符串会被逐字符迭代 —— 每个字符被当成一个「动词」，
     于是必然报未知动词，看起来像后端坏了。
     这里显式解析 JSON；解析不了就当没有，绝不把坏参数塞给后端。 */
  function parseOfflineParam(raw) {
    if (!raw) return null;
    if (Array.isArray(raw)) return raw;
    try {
      var v = JSON.parse(raw);
      return Array.isArray(v) ? v : null;
    } catch (e) {
      console.warn("[offline] 不是合法 JSON 数组，已忽略：", raw);
      return null;
    }
  }

  var urlParams = (function () {
    var out = {};
    try {
      (window.location.search || "").replace(/^\?/, "").split("&").forEach(function (kv) {
        if (!kv) return;
        var i = kv.indexOf("=");
        var k = decodeURIComponent(i < 0 ? kv : kv.slice(0, i));
        var v = i < 0 ? "" : decodeURIComponent(kv.slice(i + 1).replace(/\+/g, " "));
        if (k) out[k] = v;
      });
    } catch (e) { /* 参数坏了不该让页面崩 */ }
    return out;
  })();

  /* ---------- 持久化：只存真正影响下次打开的东西 ---------- */
  var STORE_KEY = "crucible.ui.v1";
  var persisted = (function () {
    try {
      return JSON.parse(localStorage.getItem(STORE_KEY) || "{}") || {};
    } catch (e) { return {}; }
  })();
  function save() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        theme: persisted.theme,
        railW: persisted.railW,
        railCollapsed: persisted.railCollapsed,
        agentHidden: persisted.agentHidden,
        termH: persisted.termH,
        activeView: persisted.activeView,
        filter: persisted.filter,
        // 上次用的工作区。URL 里有 ?workspace= 时仍然以 URL 为准（显式优先），
        // 这里只是"上次我在哪干活"的记忆，用来免去每次重新选一遍。
        workspace: persisted.workspace,
      }));
    } catch (e) { /* 隐私模式写不进去就算了，不该因此崩掉 */ }
  }

  /* ---------- 状态 ---------- */
  var state = {
    // 初始标签页：**空**。
    // 原来预置了 mock 的四个文件标签（reconnect.ts / sse-client.ts / …），
    // 那些文件在真实工作区里不存在 —— 编辑器会显示"无法打开"，
    // 而标签栏还挂着它们，看起来像"项目里有这几个文件"。
    // 真实场景下标签来自用户点开的文件，所以初值必须是空的。
    activeTab: null,
    tabs: [],
    railView: persisted.activeView || "explorer",
    gateResolved: null,      // null | "approved" | "rejected"
    planOpen: true,
    termCollapsed: false,
    railDrawer: window.matchMedia("(max-width: 860px)").matches,
    agentDrawer: window.matchMedia("(max-width: 1024px)").matches,
    ctxRefs: [],
    filter: persisted.filter || "all",
    focusIdx: 0,             // 文件树的键盘游标
    renaming: null,          // 正在内联重命名的路径
    endpoint: null,          // 当前模型接入配置（公开形态，不含明文 key）
    // 只从 URL 取。曾经写成 `urlParams.workspace || state.workspace || null`，
    // 那是在 state 自己的初始化表达式里引用 state —— `var` 提升让此刻的 state
    // 是 undefined，于是抛 TypeError，**整个 app.js 从这一行起全部不执行**。
    // 表现为「页面看着正常、所有交互都是死的」，极难定位。
    //
    // 优先级：URL（显式，这次就要在这个目录干活）> 上次用的 > 空（服务端默认）。
    // **不再需要靠重启服务换工作区** —— 界面里可以直接选任意目录。
    workspace: urlParams.workspace || persisted.workspace || null,

    // ── 后端数据（本次对接新增）──
    // 全部初始化为 null 而不是空数组：`null` 表示"还没查"，
    // `[]` 表示"查了，确实没有"。混用会让人把"没加载"读成"没有数据"，
    // 而这两种状态在前端要给完全不同的提示。
    runs: null,              // 运行列表（跨工作区）
    runsError: null,         // 列表查询失败的原因（与"空列表"分开）
    detailRun: null,         // 当前展开详情的 run id
    detail: null,            // 该 run 的明细（诊断/证书/能力/…）
    detailLoading: false,
    detailError: null,
    tools: null,             // 工具清单（mcp 视图）
    chainSummary: null,      // 批量哈希链核验

    // ── 事件流模式 ──
    // `stream` 只有三种值，**不允许第四种**：
    //   "mock"   —— 后端未连接，展示原型演示数据
    //   "live"   —— 正在跑一次真实任务（实时事件流）
    //   "replay" —— 回放某条历史 run 的落盘事件
    // 为什么不用布尔量：需要区分"实时"与"历史"，
    // 而这两者的混淆是最危险的（会把已结束的运行看成正在跑）。
    streamMode: "mock",
    replayRunId: null,
    replayDetail: null,      // { runId, model, status, count, workspace }
    // ── 工作区真实文件（explorer / editor）──
    // `treeCache` 按目录缓存已列出的子项：展开一个目录只请求一次，
    // 收起再展开不重复请求。用 `null` 表示"正在加载"而不是 `{}` ——
    // 后者会被读成"这个目录是空的"。
    treeCache: {},
    treeError: null,
    fileCache: {},           // path -> { lines, encoding, line_count, truncated }
    fileError: null,
    wsInfo: null,            // 工作区概要（状态栏 / 索引提示）
    wsInfoError: null,
    searchQuery: "",         // 搜索词
    searchHits: undefined,   // undefined=没搜过 / null=搜索中 / {..}=结果
    searchError: null,
    searchMode: "index",     // "index" | "grep" —— 两种搜索**性质不同**，必须显式区分
    indexInfo: null,         // 索引概要（文件数 / 符号数 / 陈旧状态）
    indexError: null,
    gitStatus: null,         // 工作区 git 改动（状态栏诊断的真实来源）
    gitError: null,

    // ── 用量 ──
    usage: null,
    usageSource: null,       // "live" | "replay"
  };

  /* 把外层可见的"重绘钩子"放在 state 旁边。
     为什么需要它：`updateMeter` / `renderStatusbar` 依赖 DOM 且定义在
     `init()` 内部（那里才保证 DOM 已就绪），但 `Backend.loadUsage()` 是
     外层函数、拿不到它们。用 `var` 声明 + 在 init 里赋值，
     既保住了 init 里的 DOM 时序，外层也能安全调用（未就绪时是空操作）。
     替代方案是把这几个函数提到外层 —— 那样它们在 init 之前就可能被调到，
     而那时 DOM 还没准备好。**时序问题不能用作用域调整来绕过。** */
  var uiHooks = {};

  // 未保存改动的标记表。初值**必须是空的** ——
  // 原来预置了 `{"src/transport/reconnect.ts": true}`，那是一个 mock 文件，
  // 会让一个不存在的文件在标签上永远显示"未保存"圆点。
  // 真实场景下"有未保存改动"只可能来自用户在编辑器里的操作。
  var dirtyFile = {};

  /* 斜杠命令：**只列真实可用的**。
     原来这份清单来自 mock-data.js，里面有 /plan、/mcp、/goal、/resume ——
     这些在 harness 里**都不存在**（没有计划、没有 MCP 服务管理、
     没有会话续接端点）。把它们摆在"Agent 命令"分组里，
     用户敲下去只会得到一句"模型看不懂的文本"。
     现在只保留真有对应行为的，并在描述里写清它做什么。 */
  var SLASH_COMMANDS = [
    { cmd: "/clear", desc: "清空事件流（仅前端视图，不影响已落盘的记录）" },
    { cmd: "/status", desc: "查看上次运行的真实 token 用量" },
    { cmd: "/sessions", desc: "切到「会话历史」，可回放任一次运行" },
    { cmd: "/trace", desc: "切到「执行轨迹」，看实际做过的每一步" },
    { cmd: "/search", desc: "切到「搜索」，在工作区里按内容查找" },
  ];

  /* 执行一条界面命令。**不在输入框里插文本** ——
     插文本的旧行为会让用户以为命令发给了模型（实际上模型只是读到
     一段看不懂的文字）。这里的每条命令都映射到一个真实的界面动作。 */
  function runSlashCommand(cmd) {
    var name = String(cmd || "").replace(/^\//, "");
    if (name === "clear") {
      if (state.streamMode === "replay") { Backend.exitReplay(); }
      else { liveVMs = []; }
      renderStream();
      updateStreamStat();
      UI.toast({ tone: "ok", title: "已清空事件流视图",
                 desc: "只清前端显示；已落盘的记录不受影响。", duration: 3200 });
      return;
    }
    if (name === "status") { showTokenDetail(); return; }
    if (name === "sessions") { goView("sessions"); return; }
    if (name === "trace") { if (typeof setPlanOpen === "function") setPlanOpen(true); return; }
    if (name === "search") { goView("search"); return; }
    UI.toast({ tone: "warning", title: "未知命令 /" + name,
               desc: "可用命令见命令面板（Ctrl+K）。", duration: 3600 });
  }



  /* ---------- 已装好的 DOM 引用 ---------- */
  var D = {};
  function cacheDom() {
    D.shell = $(".shell");
    D.rail = $("#rail");
    D.railBody = $("#rail-body");
    D.tabbar = $("#tabbar");
    D.tabbarScroll = $("#tabbar-scroll");
    D.stream = $("#stream");
    D.termScroll = $("#terminal-fade");
    D.termBody = $("#terminal-body");
    D.termInput = $("#term-input");
    D.editorScroll = $("#editor-scroll");
    D.gutter = $("#editor-gutter");
    D.mine = $("#mention");
  }

  /* ---------- 主题：跟随系统偏好，同时允许手动覆盖 ----------
     ## 图标必须与动作一致
     旧版顶栏那个主题按钮画的是一个「+」—— 于是它被当成"新建"；
     而活动栏底部的"设置"图标是圆+八条射线，看着就是亮度图标，
     点下去却是模型接入。**图标语义错位比没有图标更糟**：功能明明存在，
     用户却找不到，还会认为"点了没用"。
     现在：主题在活动栏底部，图标随当前主题切换（深色显示太阳=切到浅色），
     并且 tooltip 里明说当前是什么、点一下会变成什么。 */
  var ICON_SUN = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    + '<circle cx="12" cy="12" r="4.1" fill="none" stroke="currentColor" stroke-width="1.7"/>'
    + '<path d="M12 2.6v2.2M12 19.2v2.2M2.6 12h2.2M19.2 12h2.2'
    + 'M5.4 5.4l1.6 1.6M17 17l1.6 1.6M18.6 5.4L17 7M7 17l-1.6 1.6"'
    + ' stroke="currentColor" stroke-width="1.7" stroke-linecap="round" fill="none"/></svg>';
  var ICON_MOON = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    + '<path d="M20 14.2A8.2 8.2 0 0 1 9.8 4a8.6 8.6 0 1 0 10.2 10.2z"'
    + ' fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>';

  function themeLabel(t) { return t === "light" ? "浅色" : "深色"; }

  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    var mc = document.querySelector('meta[name="theme-color"]');
    if (mc) mc.setAttribute("content", t === "light" ? "#f2f0eb" : "#10141a");
    var b = $("#btn-theme");
    if (b) {
      // 图标画的是**将要切到**的那一侧：深色时显示太阳。
      var toLight = t !== "light";
      b.innerHTML = toLight ? ICON_SUN : ICON_MOON;
      var label = (toLight ? "切换到浅色主题" : "切换到深色主题")
        + "（当前：" + themeLabel(t) + "）";
      b.setAttribute("aria-label", label);
      b.setAttribute("data-tip", label);
      b.title = label;
    }
    // 设置页里的主题单选项要跟着同步（两处入口、同一份状态）
    $$(".settings__theme-btn").forEach(function (x) {
      var on = x.dataset.theme === t;
      x.classList.toggle("is-on", on);
      x.setAttribute("aria-checked", on ? "true" : "false");
    });
  }
  function initTheme() {
    var sys = window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    applyTheme(persisted.theme || sys);
    // 用户没手动选过时，跟随系统变化
    var mq = window.matchMedia("(prefers-color-scheme: light)");
    var on = function (e) { if (!persisted.theme) applyTheme(e.matches ? "light" : "dark"); };
    if (mq.addEventListener) mq.addEventListener("change", on);
    else if (mq.addListener) mq.addListener(on);
  }

  /* ---------- 滚动渐隐：让「还有内容」可见 ---------- */
  function bindFade(scrollEl, boxEl) {
    if (!scrollEl || !boxEl) return;
    function upd() {
      var atTop = scrollEl.scrollTop <= 2;
      var atBottom = scrollEl.scrollTop + scrollEl.clientHeight >= scrollEl.scrollHeight - 2;
      boxEl.setAttribute("data-fade-top", atTop ? "false" : "true");
      boxEl.setAttribute("data-fade-bottom", (atBottom || scrollEl.scrollHeight <= scrollEl.clientHeight) ? "false" : "true");
    }
    scrollEl.addEventListener("scroll", upd, { passive: true });
    window.addEventListener("resize", upd);
    upd();
    return upd;
  }
  var refreshStreamFade = null;
  var refreshTermFade = null;

  /* ---------- 复制按钮建造器 ---------- */
  function copyBtn(getText, label, tip) {
    var b = el("button", "copybtn");
    b.type = "button";
    b.setAttribute("aria-label", tip || ("复制" + (label ? " " + label : "")));
    b.setAttribute("data-tip", "复制" + (label ? " " + label : ""));
    b.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><rect x="5.5" y="5.5" width="7.5" height="7.5" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M10.5 5.5V4.2A1.2 1.2 0 0 0 9.3 3H4.2A1.2 1.2 0 0 0 3 4.2v5.1a1.2 1.2 0 0 0 1.2 1.2h1.3" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>';
    b.addEventListener("click", function (e) {
      e.stopPropagation();
      e.preventDefault();
      var t = typeof getText === "function" ? getText() : getText;
      UI.copy(t, label);
    });
    return b;
  }

  /* ============================================================
     一、侧栏
     ============================================================ */
  var RAIL_TITLES = {
    explorer: "资源管理器",
    search: "搜索",
    scm: "源码管理",
    sessions: "会话历史",
    mcp: "MCP 工具",
  };

  var treeState = {};  // 目录开合

  function countFiles(nodes) {
    var n = 0;
    (nodes || []).forEach(function (x) {
      if (x.kind === "file") n++;
      else n += countFiles(x.children);
    });
    return n;
  }

  function renderTree(nodes, depth, container) {
    var ul = el("ul", depth === 0 ? "filetree" : "");
    nodes.forEach(function (node) {
      var li = el("li");
      if (node.kind === "dir") {
        var key = depth + ":" + node.name + ":" + (container.dataset.path || "");
        // 默认展开到 transport 一层：让有正文的文件一眼可见
        if (!(key in treeState)) {
          treeState[key] = node.open === true || depth === 0 ||
            (depth === 1 && node.name === "transport");
        }
        var open = treeState[key];

        var head = el("button", "fileitem");
        head.type = "button";
        head.setAttribute("aria-expanded", open ? "true" : "false");
        if (depth) { head.style.paddingLeft = (16 + depth * 12) + "px"; }

        var chv = svg(ICONS.chevron, "fileitem__icon");
        chv.style.transform = open ? "none" : "rotate(-90deg)";
        chv.style.transition = "transform 140ms cubic-bezier(0.25,1,0.5,1)";
        head.appendChild(chv);

        var nm = el("span", "fileitem__name", node.name);
        head.appendChild(nm);

        var cnt = el("span", "fileitem__lines", String(countFiles(node.children)));
        head.appendChild(cnt);

        head.addEventListener("click", function () {
          treeState[key] = !treeState[key];
          renderRail();
        });
        li.appendChild(head);

        if (open) li.appendChild(renderTree(node.children, depth + 1, container));
      } else {
        var b = el("button", "fileitem");
        b.type = "button";
        b.setAttribute("role", "treeitem");
        b.setAttribute("aria-level", String(depth + 1));
        if (depth) { b.style.paddingLeft = (16 + depth * 12) + "px"; }
        if (state.activeTab === node.path) b.classList.add("is-on");
        b.setAttribute("data-path", node.path || "");

        b.appendChild(svg(ICONS.file, "fileitem__icon"));
        var nmEl = el("span", "fileitem__name truncate", node.name);
        b.appendChild(nmEl);
        if (node.status) {
          var s = el("span", "fileitem__status st-" + node.status, node.status);
          s.setAttribute("data-tip", node.status === "M" ? "已修改" : node.status === "A" ? "新增" : "已删除");
          b.appendChild(s);
        }
        b.appendChild(el("span", "fileitem__lines", String(node.lines)));
        b.setAttribute("data-tip", node.path || node.name);

        // 正在重命名：把名字换成输入框
        if (state.renaming === node.path && node.path) {
          var inp = el("input", "inlineedit");
          inp.type = "text";
          inp.value = node.name;
          inp.setAttribute("aria-label", "重命名 " + node.name);
          inp.setAttribute("spellcheck", "false");
          nmEl.replaceWith(inp);
          setTimeout(function () {
            inp.focus();
            // 只选中主文件名，不含扩展名 —— 和主流编辑器一致
            var dot = inp.value.lastIndexOf(".");
            inp.setSelectionRange(0, dot > 0 ? dot : inp.value.length);
          }, 0);

          var finish = function (commit) {
            if (state.renaming !== node.path) return;
            state.renaming = null;
            var v = inp.value.trim();
            if (commit && v && v !== node.name) {
              var dir = node.path.split("/").slice(0, -1).join("/");
              node.name = v;
              node.path = dir ? dir + "/" + v : v;
              UI.toast({ tone: "ok", title: "已重命名为 " + v, duration: 3000 });
            }
            renderRail();
          };
          inp.addEventListener("keydown", function (ev) {
            ev.stopPropagation();       // 别让树的键盘导航抢走按键
            if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
            if (ev.key === "Escape") { ev.preventDefault(); finish(false); }
          });
          inp.addEventListener("blur", function () { finish(true); });
          inp.addEventListener("click", function (ev) { ev.stopPropagation(); });
        }

        b.addEventListener("click", function () {
          if (node.path) openTab(node.path);
          openRailIfMobile();
        });
        b.addEventListener("dblclick", function (ev) {
          ev.preventDefault();
          if (node.path) { state.renaming = node.path; renderRail(); }
        });
        b.addEventListener("contextmenu", function (ev) {
          ev.preventDefault();
          fileMenu(ev.clientX, ev.clientY, node);
        });
        li.appendChild(b);
      }
      ul.appendChild(li);
    });
    return ul;
  }

  /* 文件右键菜单 */
  function fileMenu(x, y, node) {
    var isFile = node.kind === "file";
    var p = node.path;
    UI.contextMenu(x, y, [
      { label: "打开", icon: ICONS.file, disabled: !isFile, onClick: function () { if (p) openTab(p); } },
      { label: "在新标签打开", icon: ICONS.plus, disabled: !isFile, onClick: function () { if (p) openTab(p); } },
      { sep: true },
      { label: "重命名", icon: ICONS.edit, hint: "双击", onClick: function () {
          if (!p) return;
          state.renaming = p;
          renderRail();
        } },
      { label: "复制路径", icon: ICONS.file, disabled: !p, onClick: function () { if (p) UI.copy(p, "路径"); } },
      { label: "复制文件名", icon: ICONS.file, onClick: function () { UI.copy(node.name, "文件名"); } },
      { sep: true },
      { label: "加入 agent 上下文", icon: ICONS.book, disabled: !isFile, onClick: function () {
          if (p && state.ctxRefs.indexOf(p) === -1) {
            state.ctxRefs.push(p);
            renderCtxRefs();
            UI.toast({ tone: "info", title: "已加入上下文", desc: p, duration: 3000 });
          }
        } },
      { label: "删除", icon: ICONS.x, danger: true, disabled: !isFile, onClick: function () {
          if (!p) return;
          UI.confirm({
            title: "删除这个文件？",
            desc: "「" + node.name + "」将从工作区移除。已跟踪的改动仍可从 git 恢复，未跟踪的则无法找回。",
            confirmLabel: "删除",
            cancelLabel: "取消",
            tone: "danger",
            detail: p,
          }).then(function (ok) {
            if (!ok) return;
            var t = state.tabs.filter(function (x2) { return x2.path === p; })[0];
            if (t) closeTab(p, true);
            UI.closeMenu();
            UI.toast({
              tone: "warn", title: "已删除 " + node.name, duration: 7000,
              desc: "这是演示环境，文件未真正删除。",
              actions: [{ label: "撤销", primary: true, onClick: function () { UI.toast({ tone: "ok", title: "已撤销", duration: 2200 }); } }],
            });
          });
        } },
    ]);
  }

  /* ---------- 文件树的键盘导航（roving tabindex）---------- */
  function treeItems() {
    return $$(".fileitem[role='treeitem'], .fileitem[aria-expanded]", D.railBody)
      .filter(function (n) { return n.offsetParent !== null; });
  }

  function moveTreeFocus(dir) {
    var items = treeItems();
    if (!items.length) return;
    var cur = items.indexOf(document.activeElement);
    var next = cur < 0 ? 0 : Math.max(0, Math.min(items.length - 1, cur + dir));
    items.forEach(function (n, i) { n.tabIndex = i === next ? 0 : -1; });
    items[next].focus();
  }

  function onTreeKey(e) {
    var n = e.target.closest(".fileitem");
    if (!n || e.target.tagName === "INPUT") return;
    var items = treeItems();
    var cur = items.indexOf(n);

    switch (e.key) {
      case "ArrowDown": e.preventDefault(); moveTreeFocus(1); break;
      case "ArrowUp": e.preventDefault(); moveTreeFocus(-1); break;
      case "Home": e.preventDefault();
        if (items.length) { items.forEach(function (x, i) { x.tabIndex = i === 0 ? 0 : -1; }); items[0].focus(); }
        break;
      case "End": e.preventDefault();
        if (items.length) {
          items.forEach(function (x, i) { x.tabIndex = i === items.length - 1 ? 0 : -1; });
          items[items.length - 1].focus();
        }
        break;
      case "ArrowRight":
        // 是折叠的目录就展开，否则进入第一项
        if (n.getAttribute("aria-expanded") === "false") { e.preventDefault(); n.click(); }
        else if (n.getAttribute("aria-expanded") === "true") {
          e.preventDefault();
          var sub = items[cur + 1];
          if (sub && (sub.style.paddingLeft || "").replace("px", "") > (n.style.paddingLeft || "0").replace("px", "")) {
            items.forEach(function (x, i) { x.tabIndex = i === cur + 1 ? 0 : -1; });
            sub.focus();
          }
        }
        break;
      case "ArrowLeft":
        if (n.getAttribute("aria-expanded") === "true") { e.preventDefault(); n.click(); }
        else if (cur > 0 && n.style.paddingLeft) {
          // 回到最近的浅一层项
          e.preventDefault();
          var myDepth = (parseInt(n.style.paddingLeft, 10) || 16);
          for (var i = cur - 1; i >= 0; i--) {
            var d2 = (parseInt(items[i].style.paddingLeft, 10) || 16);
            if (d2 < myDepth) {
              items.forEach(function (x, k) { x.tabIndex = k === i ? 0 : -1; });
              items[i].focus();
              break;
            }
          }
        }
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        n.click();
        break;
      case "F2":
        e.preventDefault();
        if (n.getAttribute("data-path")) {
          state.renaming = n.getAttribute("data-path");
          renderRail();
        }
        break;
      case "Delete":
        e.preventDefault();
        n.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, clientX: n.getBoundingClientRect().left + 40, clientY: n.getBoundingClientRect().top + 12 }));
        break;
    }
  }

  /* ============================================================
     后端数据接入（harness）
     ============================================================
     设计取舍：
     1. **纯真实数据，不做 mock 兜底**。后端不在线时显示"未连接 + 原因"，
        不退回假数据。理由是假数据会让"对接坏了"看起来像"有数据" ——
        那正是最难发现的失败。
     2. **失败要区分三种**：未连接（无后端）/ 查询失败（HTTP 错误）/
        确实为空。三者给不同文案。混成一句"暂无数据"会把后端 500
        伪装成"这个项目没跑过任务"。
     3. **列表项必须带 workspace**。详情端点靠它定位（后端刚补的能力），
        丢掉它就退化成"列表里有、点进去 404"。
     ============================================================ */
  var Backend = {
    available: function () {
      return !!(window.Harness && window.Harness.isLive());
    },

    /* 拉运行列表。成功后缓存到 state.runs 并触发一次侧栏重绘。 */
    loadRuns: function () {
      if (!this.available()) {
        state.runs = null;
        state.runsError = "not_connected";
        return Promise.resolve(null);
      }
      var self = this;
      return window.Harness.listRunsAll(state.workspace || null)
        .then(function (payload) {
          state.runs = window.Harness.toRunCards(payload);
          state.runsError = null;
          renderRail();
          return state.runs;
        })
        .catch(function (e) {
          // 保留原始信息：`runsError` 是对象不是布尔，
          // 因为"哪个端点、什么码"决定了用户该做什么。
          state.runs = null;
          state.runsError = { kind: "http", message: String(e && e.message || e) };
          renderRail();
          return null;
        });
    },

    /* 拉一个 run 的全部明细。**并发拉而不是串行** ——
       六个端点互相独立，串行只会让用户多等六倍。 */
    loadDetail: function (runId, workspace) {
      if (!this.available()) return Promise.resolve(null);
      state.detailLoading = true;
      state.detailError = null;
      var H = window.Harness;
      var wrap = function (p) {
        // 把 404 也变成"可用但带说明"的数据，而不是 reject ——
        // 后端对"没记录"和"查不到"都返回 404 + 说明，
        // 直接 reject 会把那份说明丢掉（而说明才是有用的部分）。
        return p.then(function (v) { return { ok: true, data: v }; })
                .catch(function (e) { return { ok: false, error: String(e && e.message || e) }; });
      };
      return Promise.all([
        wrap(H.runDiagnose(runId, workspace)),
        wrap(H.runCertificate(runId, workspace)),
        wrap(H.runCapability(runId, workspace)),
        wrap(H.runChangesets(runId, workspace)),
        wrap(H.runSubagents(runId, workspace)),
        wrap(H.runVerifyChain(runId, workspace)),
      ]).then(function (rs) {
        state.detail = {
          runId: runId,
          workspace: workspace,
          diagnose: rs[0],
          certificate: rs[1],
          capability: rs[2],
          changesets: rs[3],
          subagents: rs[4],
          chain: rs[5],
        };
        state.detailLoading = false;
        renderRail();
        return state.detail;
      });
    },

    loadTools: function () {
      if (!this.available()) return Promise.resolve(null);
      return window.Harness.toolManifest()
        .then(function (p) { state.tools = window.Harness.toToolGroups(p); renderRail(); return state.tools; })
        .catch(function () { state.tools = null; });
    },

    loadChainSummary: function () {
      if (!this.available()) return Promise.resolve(null);
      return window.Harness.verifyChains(state.workspace || null)
        .then(function (p) { state.chainSummary = window.Harness.toChainVerify(p); return state.chainSummary; })
        .catch(function () { state.chainSummary = null; });
    },

    /* 回放一条历史 run 的事件流。
       与"实时运行"共用同一套渲染（buildEvent），
       但**换掉 context 的时间基准**（见桥接层 resetReplay）——
       否则时间列会显示成 1970 年附近的某个时刻。 */
    replayRun: function (runId, workspace, meta) {
      if (!this.available()) return Promise.resolve(null);
      state.streamMode = "replay";
      state.replayRunId = runId;
      state.replayVMs = null;
      state.replayError = null;
      state.replayDetail = { runId: runId, workspace: workspace, model: (meta || {}).model || "",
                             status: (meta || {}).status || "", count: 0 };
      renderStream();
      updateStreamStat();
      return window.Harness.runReplayEvents(runId, workspace)
        .then(function (r) {
          state.replayVMs = r.viewModels;
          state.replayDetail.count = r.payload.count;
          renderStream();
          updateStreamStat();
          return r;
        })
        .catch(function (e) {
          // 回放失败必须**退出回放态并说明原因** ——
          // 停在一个空的事件流上会被读成"这条 run 没有事件"。
          state.replayVMs = null;
          state.replayError = String(e && e.message || e);
          renderStream();
          updateStreamStat();
          return null;
        });
    },

    exitReplay: function () {
      state.streamMode = Backend.available() ? "live" : "mock";
      state.replayRunId = null;
      state.replayVMs = null;
      state.replayError = null;
      state.replayDetail = null;
      renderStream();
      updateStreamStat();
      UI.announce("已退出历史回放");
    },

    /* ---------- 工作区真实文件 ---------- */
    /* 工作区可能被 agent 改动过 → 丢弃缓存，下次读的是磁盘上最新的。
       为什么不"精确失效被改的文件"：agent 可能改了任意路径，
       而我们只能从事件里看到一部分（shell 的改动不总是带 files_changed）。
       全丢一遍的代价是重新请求，比"显示旧内容"安全得多。 */
    invalidateWorkspace: function () {
      state.treeCache = {};
      state.fileCache = {};
      Backend.loadWorkspaceInfo();
      if (state.railView === "explorer") Backend.loadTree("");
      if (state.activeTab && Backend.available()) Backend.loadFile(state.activeTab);
    },

    loadTree: function (path) {
      if (!this.available()) return Promise.resolve(null);
      var key = path || "";
      if (state.treeCache[key] !== undefined) {
        return Promise.resolve(state.treeCache[key]);
      }
      state.treeCache[key] = null;      // 加载中
      renderRail();
      return window.Harness.workspaceTree(state.workspace, key)
        .then(function (r) {
          state.treeCache[key] = r;
          state.treeError = null;
          // 父目录的结果也要刷新 —— 展开子目录后父级的"已加载"标记仍有效，
          // 但如果没有这一步，重绘时会看不到新内容。
          renderRail();
          return r;
        })
        .catch(function (e) {
          state.treeCache[key] = { error: String(e && e.message || e) };
          state.treeError = String(e && e.message || e);
          renderRail();
          return null;
        });
    },

    loadFile: function (path) {
      if (!this.available()) return Promise.resolve(null);
      if (state.fileCache[path]) return Promise.resolve(state.fileCache[path]);
      // 先占位，避免重复请求
      state.fileCache[path] = null;
      return window.Harness.workspaceFile(state.workspace, path)
        .then(function (r) {
          if (r && r.error) {
            state.fileCache[path] = { error: r.error, path: path };
          } else {
            state.fileCache[path] = r;
          }
          state.fileError = null;
          renderEditor();
          return r;
        })
        .catch(function (e) {
          // **失败不写占位**：写下 `{error}` 后再次打开会一直命中错误缓存，
          // 用户重试也没用（因为缓存判定的是"有没有值"，不是"值是否有效"）。
          delete state.fileCache[path];
          state.fileError = String(e && e.message || e);
          renderEditor();
          UI.toast({ tone: "danger", title: "打开文件失败",
                     desc: state.fileError, duration: 5000 });
          return null;
        });
    },

    loadGitStatus: function () {
      if (!this.available()) return Promise.resolve(null);
      return window.Harness.workspaceGitStatus(state.workspace)
        .then(function (r) {
          state.gitStatus = (r && !r.error) ? r : null;
          state.gitError = (r && r.error) ? r.error : null;
          if (uiHooks.renderStatusMetrics) uiHooks.renderStatusMetrics();
          return state.gitStatus;
        })
        .catch(function (e) {
          state.gitStatus = null;
          state.gitError = String(e && e.message || e);
          if (uiHooks.renderStatusMetrics) uiHooks.renderStatusMetrics();
          return null;
        });
    },

    loadIndex: function (rebuild) {
      if (!this.available()) return Promise.resolve(null);
      return window.Harness.workspaceIndex(state.workspace, rebuild)
        .then(function (r) {
          state.indexInfo = (r && !r.error) ? r : null;
          state.indexError = (r && r.error) ? r.error : null;
          if (uiHooks.renderStatusMetrics) uiHooks.renderStatusMetrics();
          if (state.railView === "explorer") renderRail();
          return state.indexInfo;
        })
        .catch(function (e) {
          state.indexInfo = null;
          state.indexError = String(e && e.message || e);
          if (uiHooks.renderStatusMetrics) uiHooks.renderStatusMetrics();
          return null;
        });
    },

    /* 索引查询（符号 + 文件）。**与 grep 是两种不同的搜索**，
       所以走**两个不同的函数**，各自的结果形状也不同 ——
       混成一个入口会让人以为它们是一回事。 */
    indexSearch: function (q) {
      if (!this.available()) return Promise.resolve(null);
      return window.Harness.indexQuery(state.workspace, q)
        .then(function (r) { return r; })
        .catch(function (e) { return { error: String(e && e.message || e) }; });
    },

    /* ---------- 本机目录浏览（选择工作区用）----------
       失败时**返回形状与成功一致**（entries/drives/shortcuts 都在），
       只是多一个 error 字段。理由：选择器是用户唯一能换工作区的地方，
       它拿到 undefined 就会画出空白列表并让用户以为"这台机器没有目录"。 */
    fsList: function (path) {
      if (!this.available()) {
        return Promise.resolve({
          error: "offline", detail: "后端未连接，浏览不了本机目录。",
          entries: [], drives: [], shortcuts: [], usable: false,
        });
      }
      return window.Harness.fsList(path).catch(function (e) {
        return {
          error: "network", detail: String(e && e.message || e),
          entries: [], drives: [], shortcuts: [], usable: false,
        };
      });
    },

    /* 把模型配置沿用到新工作区。**只在用户显式点"沿用"时调** ——
       自动沿用会让用户以为在新项目里用的是 A 模型，其实沿用了上个项目的 B。 */
    copyConfig: function (from, to) {
      if (!this.available()) {
        return Promise.resolve({ ok: false, error: "offline", detail: "后端未连接。" });
      }
      return window.Harness.copyConfig(from, to);
    },

    /* 在**指定目录**下建空文件/空目录。
       与 startCreate 走同一个后端端点（`/api/workspace/create`）——
       写磁盘的入口只许有一个，否则迟早出现"一个入口校验了、另一个没有"。
       选择器用它建"新项目文件夹"。 */
    createEntry: function (dir, path, kind) {
      if (!this.available()) {
        return Promise.resolve({
          ok: false, status: 0,
          body: { error: "offline", detail: "后端未连接，建不了目录。" },
        });
      }
      return window.Harness.workspaceCreate(dir, path, kind);
    },

    loadWorkspaceInfo: function () {
      if (!this.available()) return Promise.resolve(null);
      return window.Harness.workspaceInfo(state.workspace)
        .then(function (r) {
          state.wsInfo = r && !r.error ? r : null;
          state.wsInfoError = r && r.error ? r.error : null;
          if (uiHooks.renderStatusbar) uiHooks.renderStatusbar();
          if (uiHooks.renderBranchChip) uiHooks.renderBranchChip();
          return state.wsInfo;
        })
        .catch(function (e) {
          state.wsInfo = null;
          state.wsInfoError = String(e && e.message || e);
          return null;
        });
    },

    /* ---------- 用量 ---------- */
    loadUsage: function (runId, workspace, source) {
      if (!this.available()) return Promise.resolve(null);
      return window.Harness.runResult(runId, workspace)
        .then(function (r) {
          var u = window.Harness.toUsage(r && r.result);
          state.usage = u;
          state.usageSource = source || "unknown";
          if (uiHooks.updateMeter) uiHooks.updateMeter();
          return u;
        })
        .catch(function () {
          state.usage = null;
          state.usageSource = null;
          if (uiHooks.updateMeter) uiHooks.updateMeter();
          return null;
        });
    },
  };

  /* 事件流统计条：把"当前看的是什么"写清楚。
     实时 / 回放 / 演示三态在这一行里必须能分辨 ——
     它是用户判断"agent 是否在动"的第一入口。

     过滤信息也在这里拼：**只保留一处格式化**。
     早期 applyFilter 自己写了一遍，于是模式前缀会被它覆盖掉
     （回放标识刚显示出来就被替换掉）。 */
  function updateStreamStat(shown) {
    var st = $("#stream-stat");
    if (!st) return;
    var nodes = $$(".ev", D.stream).length;
    var total = nodes;
    var f = state.filter;
    var filt = (f === "all") ? "" : "（筛选中 " + (shown == null ? total : shown) + "/" + total + "）";

    var prefix;
    if (state.streamMode === "replay") {
      var d = state.replayDetail || {};
      prefix = "历史回放" + (d.status ? " · " + d.status : "") + " · ";
    } else if (state.streamMode === "live") {
      prefix = "实时 · ";
    } else {
      prefix = "演示数据 · ";
    }
    st.textContent = prefix + total + " 个事件" + filt
      + " · " + state.tabs.length + " 个文件已打开";
  }

  /* 一条"数据来源"提示条。
     存在它的唯一理由是：**让"读不到"和"没有"在视觉上分开**。
     没有它的话，后端挂了会被读成"这个项目很干净"。 */
  function dataStateBar() {
    var bar = el("div", "backendbar");
    if (!Backend.available()) {
      bar.className += " backendbar--off";
      bar.appendChild(el("span", "backendbar__dot"));
      bar.appendChild(el("span", null, "后端未连接 · 启动 `python harness/server.py` 后刷新"));
      return bar;
    }
    if (state.runsError) {
      bar.className += " backendbar--err";
      bar.appendChild(el("span", "backendbar__dot"));
      bar.appendChild(el("span", null, "运行列表读取失败：" + state.runsError.message));
      return bar;
    }
    bar.className += " backendbar--on";
    bar.appendChild(el("span", "backendbar__dot"));
    bar.appendChild(el("span", null, "已连接 harness · " +
      (state.runs ? state.runs.length + " 次运行记录" : "加载中…")));
    return bar;
  }

  /* 详情里的一块小卡：标题 + 若干行。 */
  function detailBlock(title, tone, rows) {
    var b = el("div", "dblock" + (tone ? " dblock--" + tone : ""));
    b.appendChild(el("div", "dblock__head", title));
    var list = el("div", "dblock__body");
    (rows || []).forEach(function (r) {
      var line = el("div", "dline" + (r.ok === false ? " is-bad" : (r.warn ? " is-warn" : "")));
      if (r.k) line.appendChild(el("span", "dline__k", r.k));
      line.appendChild(el("span", "dline__v", r.v));
      list.appendChild(line);
    });
    b.appendChild(list);
    return b;
  }

  /* 渲染当前展开 run 的明细。 */
  function renderRunDetail(host) {
    var det = state.detail;
    if (state.detailLoading) {
      host.appendChild(el("div", "dloading", "读取明细…"));
      return;
    }
    if (!det) return;

    // ── 回放入口（放在最前，因为这是最常用的动作）──
    var runCard = null;
    (state.runs || []).forEach(function (r) {
      if (r.id === det.runId) runCard = r;
    });
    var act = el("div", "dblock");
    act.appendChild(el("div", "dblock__head", "事件流"));
    var actBody = el("div", "dblock__body");
    var btn = el("button", "dbtn dbtn--play", "▶ 回放这条运行的完整事件流");
    btn.type = "button";
    btn.addEventListener("click", function () {
      Backend.replayRun(det.runId, det.workspace, {
        model: runCard && runCard.model,
        status: det.diagnose && det.diagnose.ok
          ? (window.Harness.toDiagnoseCards(det.diagnose.data).stats || {}).status : "",
      });
      UI.announce("正在回放运行 " + det.runId);
    });
    actBody.appendChild(btn);
    actBody.appendChild(el("div", "dnote",
      "回放的是落盘事件，不是实时运行 —— 事件流顶部会标明「历史回放」。"));
    act.appendChild(actBody);
    host.appendChild(act);

    // ── 诊断（过程缺陷）──
    var dg = det.diagnose;
    if (dg && dg.ok) {
      var t = window.Harness.toDiagnoseCards(dg.data);
      var rows = [];
      if (t.stats) {
        rows.push({ k: "事件", v: String(t.stats.events) });
        rows.push({ k: "工具调用", v: String(t.stats.toolCalls) + "（执行 " + t.stats.executed + "）" });
        rows.push({ k: "迭代", v: String(t.stats.iterations) });
        rows.push({ k: "结束状态", v: String(t.stats.status) });
      }
      t.reasons.forEach(function (r) { rows.push({ ok: false, k: r.code, v: r.text }); });
      if (!t.reasons.length) {
        rows.push({ ok: true, k: "结论", v: "本份轨迹没有发现过程缺陷信号" });
      }
      host.appendChild(detailBlock(
        "轨迹诊断 · " + (t.verdict === "flawed" ? "发现问题" : "无问题"),
        t.verdict === "flawed" ? "bad" : null, rows));
      if (t.disclaimer) {
        host.appendChild(el("p", "dnote", t.disclaimer));
      }
    } else if (dg) {
      host.appendChild(detailBlock("轨迹诊断", "muted",
        [{ warn: true, k: "不可用", v: dg.error }]));
    }

    // ── 完成证书（ECT）──
    var ct = det.certificate;
    if (ct && ct.ok) {
      var c = window.Harness.toCertificate(ct.data);
      if (c && c.available) {
        var rows2 = [];
        c.claims.forEach(function (x) {
          rows2.push({
            ok: true, k: x.kind,
            v: x.subject + "  ← seq " + x.evidenceSeq.join(", "),
          });
        });
        c.problems.forEach(function (p) {
          rows2.push({ ok: false, k: p.code, v: p.detail + (p.remedy ? " → " + p.remedy : "") });
        });
        if (!rows2.length) rows2.push({ k: "声明", v: "无（只读任务或未收尾）" });
        rows2.push({
          ok: c.replayOk !== false, k: "独立重放",
          v: c.replayOk === false ? "未通过：" + (c.replayCodes || []).join(", ") : "通过",
        });
        host.appendChild(detailBlock(
          "完成证书 · " + (c.signed ? "已签发" : "未签发"),
          c.signed ? null : "bad", rows2));
        // 边界说明必须跟着证书一起出现，否则它会被读成"正确性证明"
        if (c.scope) host.appendChild(el("p", "dnote", c.scope));
      } else {
        host.appendChild(detailBlock("完成证书", "muted",
          [{ warn: true, k: "不可用", v: (c && c.detail) || "无记录" }]));
      }
    } else if (ct) {
      host.appendChild(detailBlock("完成证书", "muted", [{ warn: true, k: "读取失败", v: ct.error }]));
    }

    // ── 能力账本（CapScope）──
    var cp = det.capability;
    if (cp && cp.ok) {
      var cap = window.Harness.toCapability(cp.data);
      if (cap && cap.available) {
        var rows3 = [
          { ok: cap.outOfScope === 0, k: "越界次数",
            v: cap.outOfScope + " / " + cap.total + " 次调用" },
          { k: "分账主体", v: cap.grantee || "（未记录）" },
          { k: "任务只读", v: cap.readonlyTask ? "是" : "否" },
        ];
        Object.keys(cap.byCode || {}).forEach(function (code) {
          rows3.push({ ok: false, k: code, v: String(cap.byCode[code]) + " 次" });
        });
        host.appendChild(detailBlock("能力账本", cap.outOfScope ? "bad" : null, rows3));
      } else {
        host.appendChild(detailBlock("能力账本", "muted",
          [{ warn: true, k: "不可用", v: (cap && cap.detail) || "无记录" }]));
      }
    } else if (cp) {
      host.appendChild(detailBlock("能力账本", "muted", [{ warn: true, k: "读取失败", v: cp.error }]));
    }

    // ── 变更集（能不能还原）──
    var ch = det.changesets;
    if (ch && ch.ok) {
      var cs = window.Harness.toChangesets(ch.data);
      if (cs.summary && cs.summary.changes > 0) {
        var rows4 = [
          { k: "变更数", v: String(cs.summary.changes) },
          { k: "涉及文件", v: String(cs.summary.files) },
        ];
        cs.history.slice(0, 8).forEach(function (h) {
          rows4.push({
            ok: true, k: "seq " + (h.seq == null ? "?" : h.seq),
            v: (h.paths || []).join(", ") || h.tool,
          });
        });
        var blk = detailBlock("变更集 · 可回滚", null, rows4);
        // 逐条回滚按钮：回滚的价值只在**能回到具体某一步之前**（后端文档原话）
        cs.history.slice(0, 8).forEach(function (h) {
          if (!h.changeId) return;
          var b = el("button", "dbtn", "回滚到 seq " + h.seq + " 之前");
          b.type = "button";
          b.addEventListener("click", function () {
            if (!Backend.available()) return;
            UI.confirm({
              title: "回滚这次改动？",
              desc: "会把 " + ((h.paths || []).join("、") || h.tool)
                  + " 还原到该步之前的状态。",
              confirmLabel: "回滚", tone: "danger",
            }).then(function (ok) {
              if (!ok) return;
              window.Harness.rollback(state.detail.runId, h.changeId, state.detail.workspace)
                .then(function () {
                  UI.toast({ tone: "ok", title: "已回滚", desc: "seq " + h.seq + " 之前的改动已还原。" });
                  Backend.loadDetail(state.detail.runId, state.detail.workspace);
                })
                .catch(function (e) {
                  UI.toast({ tone: "danger", title: "回滚失败", desc: String(e && e.message || e) });
                });
            });
          });
          blk.querySelector(".dblock__body").appendChild(b);
        });
        host.appendChild(blk);
      } else {
        host.appendChild(detailBlock("变更集", "muted",
          [{ k: "状态", v: "本次运行没有可回滚的改动" }]));
      }
    }

    // ── 子代理（隔离验证）──
    var sb = det.subagents;
    if (sb && sb.ok) {
      var s2 = window.Harness.toSubagents(sb.data);
      if (s2 && s2.available && s2.count > 0) {
        var rows5 = s2.agents.map(function (a) {
          return { ok: true, k: a.id, v: "有独立能力账本记录" };
        });
        rows5.push({ warn: true, k: "说明",
          v: "子代理上下文与父级隔离：只给规格，不给上游推理" });
        host.appendChild(detailBlock("子代理 · " + s2.count + " 个", null, rows5));
      } else {
        host.appendChild(detailBlock("子代理", "muted",
          [{ k: "状态", v: (s2 && s2.detail) || "本次运行没有派发子代理" }]));
      }
    }

    // ── 哈希链（防篡改）──
    var ck = det.chain;
    if (ck && ck.ok) {
      var c2 = window.Harness.toChainVerify(ck.data);
      if (c2 && c2.available) {
        host.appendChild(detailBlock("事件哈希链 · " + (c2.ok ? "完整" : "异常"),
          c2.ok ? null : "bad", [
            { ok: c2.ok, k: "核验", v: c2.ok ? "链完整、未被篡改" : "发现问题" },
            { warn: true, k: "外部锚点", v: c2.withoutAnchor && c2.withoutAnchor.indexOf(state.detail.runId) >= 0
              ? "无（仅无法防止整链重写，不代表被改过）" : "有" },
          ]));
      }
    }
  }

  /* ---------- 真实文件树（按需展开）----------
     与 mock 版的 FILE_TREE 渲染最大的不同：
     **只有列出来的那一层才在 DOM 里**。展开一个目录才请求它一次。
     这不是优化，是必须的 —— 一个真实仓库全量递归会把侧栏塞进几千个节点。

     三态（加载中 / 失败 / 有数据）都要能分辨：
     `treeCache[path] === undefined` 还没查、`=== null` 正在查、
     `{error}` 查失败、有 `entries` 才是数据。 */
  function renderRealTree(path, depth) {
    var wrap = el("div", "filetree" + (depth ? " filetree--sub" : ""));

    var cached = state.treeCache[path];
    if (cached === undefined) {
      // 首次进入：先显示加载占位，再异步取
      wrap.appendChild(el("div", "dloading", "读取目录…"));
      Backend.loadTree(path);
      return wrap;
    }
    if (cached === null) {
      wrap.appendChild(el("div", "dloading", "读取目录…"));
      return wrap;
    }
    if (cached && cached.error) {
      var e = el("div", "treeerr");
      e.appendChild(el("span", null, "读取失败：" + cached.error));
      var retry = el("button", "dbtn", "重试");
      retry.type = "button";
      retry.addEventListener("click", function () {
        delete state.treeCache[path];
        renderRail();
      });
      e.appendChild(retry);
      wrap.appendChild(e);
      return wrap;
    }

    var entries = (cached && cached.entries) || [];
    if (!entries.length) {
      wrap.appendChild(el("div", "dloading", "（空目录）"));
      return wrap;
    }

    entries.forEach(function (en) {
      var isDir = en.kind === "dir";
      var row = el("div", "fileitem" + (isDir ? " fileitem--dir" : ""));
      row.setAttribute("data-path", en.path);
      row.appendChild(svg(isDir ? ICONS.dirClosed : ICONS.file, "fileitem__icon"));
      row.appendChild(el("span", "fileitem__name truncate", en.name));
      if (!isDir && en.size) {
        row.appendChild(el("span", "fileitem__size tabular", UI.fmtBytes(en.size)));
      }
      row.setAttribute("data-tip", en.path);

      if (isDir) {
        var openKey = "open:" + en.path;
        var opened = !!treeState[openKey];
        row.classList.toggle("is-open", opened);
        row.querySelector(".fileitem__icon").innerHTML =
          opened ? ICONS.dirOpen : ICONS.dirClosed;
        row.addEventListener("click", function () {
          treeState[openKey] = !treeState[openKey];
          if (treeState[openKey]) Backend.loadTree(en.path);
          renderRail();
        });
        /* 目录也要有右键菜单 —— 原来只有文件行有。
           「右键目录 → 在此新建」是这类工具里最常用的新建路径，
           只能在顶栏的「+」里新建是一件很别扭的事。 */
        row.addEventListener("contextmenu", function (ev) {
          ev.preventDefault();
          UI.contextMenu(ev.clientX, ev.clientY, [
            { label: opened ? "收起此目录" : "展开此目录", icon: ICONS.chevron, onClick: function () {
                treeState[openKey] = !treeState[openKey];
                if (treeState[openKey]) Backend.loadTree(en.path);
                renderRail();
              } },
            { sep: true },
            { label: "在此新建文件", icon: ICONS.plus, onClick: function () {
                if (uiHooks.startCreate) uiHooks.startCreate("file", en.path);
              } },
            { label: "在此新建目录", icon: ICONS.dirClosed, onClick: function () {
                if (uiHooks.startCreate) uiHooks.startCreate("dir", en.path);
              } },
            { sep: true },
            { label: "复制路径", icon: ICONS.file, onClick: function () { UI.copy(en.path, "路径"); } },
          ]);
        });
        wrap.appendChild(row);
        if (opened) wrap.appendChild(renderRealTree(en.path, depth + 1));
      } else {
        row.addEventListener("click", function () {
          if (en.text) {
            openTab(en.path);
          } else {
            UI.toast({ tone: "info", title: "这是二进制文件",
                       desc: en.path + " 不按文本打开。", duration: 3600 });
          }
        });
        row.addEventListener("contextmenu", function (ev) {
          ev.preventDefault();
          // 「在此新建」用文件所在的目录 —— 右键一个文件时，"此"就是它旁边
          var dirOf = function (p) {
            return p.indexOf("/") >= 0 ? p.slice(0, p.lastIndexOf("/")) : "";
          };
          UI.contextMenu(ev.clientX, ev.clientY, [
            { label: "打开", icon: ICONS.file, onClick: function () { openTab(en.path); } },
            { label: "在此新建文件", icon: ICONS.plus, onClick: function () {
                if (uiHooks.startCreate) uiHooks.startCreate("file", dirOf(en.path));
              } },
            { label: "在此新建目录", icon: ICONS.dirClosed, onClick: function () {
                if (uiHooks.startCreate) uiHooks.startCreate("dir", dirOf(en.path));
              } },
            { sep: true },
            { label: "复制路径", icon: ICONS.file, onClick: function () { UI.copy(en.path, "路径"); } },
            { label: "复制文件名", icon: ICONS.file, onClick: function () { UI.copy(en.name, "文件名"); } },
            { sep: true },
            { label: "加入 agent 上下文", icon: ICONS.book, onClick: function () {
                addCtxRef(en.path);
                UI.toast({ tone: "ok", title: "已加入上下文", desc: en.path, duration: 2800 });
              } },
          ]);
        });
        wrap.appendChild(row);
      }
    });

    // 目录被截断 / 有目录被跳过时要说出来，否则会被读成"这里只有这些"
    if (cached && cached.truncated) {
      wrap.appendChild(el("div", "dnote", "目录内容超过单次上限，只列出了前 "
        + entries.length + " 项。"));
    }
    if (cached && (cached.skipped || []).length) {
      wrap.appendChild(el("div", "dnote",
        "已跳过：" + cached.skipped.join("、") + "（构建产物/依赖目录）"));
    }
    return wrap;
  }

  function renderRail() {
    var body = $("#rail-body");
    var title = $("#rail-title");
    title.textContent = RAIL_TITLES[state.railView] || "";
    body.innerHTML = "";

    if (state.railView === "explorer") {
      body.appendChild(dataStateBar());

      if (!Backend.available()) {
        body.appendChild(emptyHint("后端未连接",
          "资源管理器读取的是工作区磁盘上的真实内容。启动后端后即可看到。"));
        return;
      }

      var wsName = String(state.workspace || "").split(/[\\/]/).filter(Boolean).pop() || "工作区";
      var sect = el("div", "sect");
      var head = el("button", "sect__head");
      head.type = "button";
      head.setAttribute("aria-expanded", "true");
      head.appendChild(svg(ICONS.chevron, "sect__chev"));
      head.appendChild(el("span", null, wsName));
      // 分支来自真实的 .git/HEAD（不是编的）
      head.appendChild(el("span", "sect__tail",
        state.wsInfo && state.wsInfo.branch ? "· " + state.wsInfo.branch : ""));
      var listWrap = el("div");
      head.addEventListener("click", function () {
        var open = head.getAttribute("aria-expanded") === "true";
        head.setAttribute("aria-expanded", open ? "false" : "true");
        listWrap.hidden = open;
      });
      sect.appendChild(head);
      sect.appendChild(listWrap);
      listWrap.appendChild(renderRealTree("", 0));
      body.appendChild(sect);

      body.appendChild(buildIndexNote());
      return;
    }

    if (state.railView === "search") {
      // ## 两种搜索，**必须显式区分**
      //
      // · **索引**（符号索引 + 词表倒排）：回答"哪个文件**定义**了这个符号"。
      //   快（一次查表），但只懂字面，且索引可能是旧的。
      // · **内容**（grep）：回答"哪一行含这个字符串"。慢（逐文件扫），
      //   但永远是**当前磁盘状态**。
      //
      // 混成一个入口是最坏的做法：用户不知道结果为什么时快时慢、
      // 也可能不知道为什么"刚才搜到的这一行现在没了"（索引过期）。
      // 所以用一个明确的两态切换，并在说明里写清各自的能力边界。
      var wrap = el("div", "railinput");
      wrap.appendChild(svg(ICONS.search));
      var inp = el("input");
      inp.type = "search";
      inp.placeholder = state.searchMode === "index"
        ? "搜索符号（函数/类/常量名）…" : "在工作区内容里搜索…";
      inp.setAttribute("aria-label", "在工作区内搜索");
      inp.value = state.searchQuery || "";
      wrap.appendChild(inp);
      body.appendChild(wrap);

      // 模式切换
      var seg = el("div", "searchmode");
      [["index", "符号索引", "查定义，快；只懂字面，索引可能过期"],
       ["grep", "内容 grep", "查每一行，慢；永远是当前磁盘状态"]].forEach(function (m) {
        var on = state.searchMode === m[0];
        var b = el("button", "searchmode__btn" + (on ? " is-on" : ""));
        b.type = "button";
        b.setAttribute("aria-pressed", on ? "true" : "false");
        b.setAttribute("data-tip", m[2]);
        b.textContent = m[1];
        b.addEventListener("click", function () {
          state.searchMode = m[0];
          state.searchHits = undefined;      // 换模式后旧结果无效
          renderRail();
          if (state.searchQuery) doSearch(state.searchQuery);
        });
        seg.appendChild(b);
      });
      body.appendChild(seg);

      if (!Backend.available()) {
        body.appendChild(emptyHint("后端未连接",
          "两种搜索都由后端执行：索引是符号表，grep 复用 agent 的工具。"));
        return;
      }

      var doSearch = function (q) {
        q = String(q || "").trim();
        state.searchQuery = q;
        if (!q) {
          state.searchHits = null;
          state.searchError = null;
          renderRail();
          return;
        }
        state.searchHits = null;
        state.searchError = null;
        renderRail();

        var p = state.searchMode === "index"
          ? Backend.indexSearch(q)
          : window.Harness.workspaceSearch(state.workspace, q);
        p.then(function (r) {
          if (r && r.error) {
            state.searchHits = { error: r.error, detail: r.detail || "" };
          } else {
            state.searchHits = r;
          }
          renderRail();
          var ni = $("#rail-body input[type=search]");
          if (ni) { ni.focus(); ni.setSelectionRange(ni.value.length, ni.value.length); }
        }).catch(function (e) {
          state.searchError = String(e && e.message || e);
          state.searchHits = null;
          renderRail();
        });
      };

      var timer = null;
      inp.addEventListener("input", function () {
        if (timer) clearTimeout(timer);
        var v = inp.value;
        // 索引模式可以更灵敏（查表很快）；grep 慢，多等一会儿再发
        timer = setTimeout(function () { doSearch(v); },
                           state.searchMode === "index" ? 200 : 380);
      });
      inp.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { if (timer) clearTimeout(timer); doSearch(inp.value); }
      });

      if (!state.searchQuery) {
        body.appendChild(emptyHint(
          state.searchMode === "index" ? "输入符号名开始查定义" : "输入关键词开始搜内容",
          state.searchMode === "index"
            ? "索引是「符号索引 + 词表倒排」，不是语义索引：它只懂字面。"
              + "搜「处理完成判定的那段逻辑」这类意图搜不到。"
            : "grep 逐文件匹配内容，无索引、无语义理解，但结果永远是当前磁盘状态。"));
        return;
      }
      if (state.searchError) {
        body.appendChild(emptyHint("搜索失败", state.searchError));
        return;
      }
      if (state.searchHits === null) {
        body.appendChild(el("div", "dloading", "搜索中…"));
        return;
      }
      if (state.searchHits && state.searchHits.error) {
        body.appendChild(emptyHint("搜索未执行", state.searchHits.error
          + (state.searchHits.detail ? " —— " + state.searchHits.detail : "")));
        return;
      }

      // ── 索引模式的结果 ──
      if (state.searchMode === "index") {
        var syms = (state.searchHits && state.searchHits.symbols) || [];
        var files = (state.searchHits && state.searchHits.files) || [];

        // 过期要显眼 —— 过期索引的结果**看起来正常但可能是旧的**
        if (state.searchHits && state.searchHits.stale) {
          var sb = el("div", "stalebar");
          sb.appendChild(el("span", null, "索引已过期"));
          sb.appendChild(el("span", "stalebar__detail",
            (state.searchHits.stale_detail || "") + "（结果可能不含最新改动）"));
          var rb = el("button", "dbtn", "重建索引");
          rb.type = "button";
          rb.addEventListener("click", function () {
            UI.toast({ tone: "info", title: "正在重建索引…", duration: 2000 });
            Backend.loadIndex(true).then(function () { doSearch(state.searchQuery); });
          });
          sb.appendChild(rb);
          body.appendChild(sb);
        }

        var h1 = el("div", "sect__head");
        h1.appendChild(el("span", null, "定义（强证据） · " + syms.length + " 处"));
        body.appendChild(h1);
        if (!syms.length) {
          body.appendChild(emptyHint("没有符号命中",
            "索引里没有以「" + state.searchQuery + "」开头的符号定义。"
            + "换内容 grep 模式可以搜到「提到过这个词」的位置。"));
        } else {
          syms.forEach(function (sm) {
            var it = el("button", "fileitem fileitem--sym");
            it.type = "button";
            it.appendChild(svg(ICONS[sm.kind === "class" ? "book" : "edit"], "fileitem__icon"));
            var box = el("div");
            box.style.minWidth = "0";
            box.style.flex = "1 1 auto";
            var l1 = el("div", "symname", sm.name);
            var l2 = el("div", "sympath", sm.kind + " · " + sm.path + ":" + sm.line);
            box.appendChild(l1);
            box.appendChild(l2);
            it.appendChild(box);
            it.addEventListener("click", function () { openTab(sm.path); });
            body.appendChild(it);
          });
        }

        var h2 = el("div", "sect__head sect__head--sub");
        h2.appendChild(el("span", null, "提到该词的文件（弱证据） · " + files.length));
        body.appendChild(h2);
        files.slice(0, 40).forEach(function (f) {
          var it2 = el("button", "fileitem");
          it2.type = "button";
          it2.appendChild(svg(ICONS.file, "fileitem__icon"));
          it2.appendChild(el("span", "fileitem__name truncate", f));
          it2.addEventListener("click", function () { openTab(f); });
          body.appendChild(it2);
        });

        var note1 = el("div", "dnote");
        note1.textContent = "「定义」与「提到」是两种强度的证据，不要混用。"
          + "索引不含跨文件调用关系（谁调用了谁）。";
        body.appendChild(note1);
        return;
      }

      // ── grep 模式的结果 ──
      var lines = (state.searchHits && state.searchHits.lines) || [];
      var hr = el("div", "sect__head");
      hr.appendChild(el("span", null, state.searchQuery + " · " + lines.length + " 行"));
      if (state.searchHits) {
        hr.appendChild(el("span", "sect__tail", state.searchHits.truncated ? "已截断" : ""));
      }
      body.appendChild(hr);
      if (!lines.length) {
        body.appendChild(emptyHint("没有命中",
          "关键词 " + state.searchQuery + " 在工作区里没有匹配到内容。"));
        return;
      }
      lines.forEach(function (ln) {
        var it = el("div", "fileitem");
        it.style.alignItems = "flex-start";
        it.style.paddingTop = "5px";
        it.style.paddingBottom = "5px";
        it.appendChild(svg(ICONS.file, "fileitem__icon"));
        var box = el("div");
        box.style.minWidth = "0";
        box.style.flex = "1 1 auto";
        var l2 = el("div", null, ln);
        l2.className = "srcline";
        box.appendChild(l2);
        it.appendChild(box);
        body.appendChild(it);
      });
      var note = el("div", "dnote");
      note.textContent = (state.searchHits && state.searchHits.engine
        ? "引擎：" + state.searchHits.engine : "")
        + " —— 逐文件文本匹配，无索引、无语义理解。";
      body.appendChild(note);
      return;
    }

    if (state.railView === "scm") {
      // 源码管理视图 = **当前工作区被 agent 改过的文件**。
      // 真实来源是变更集（ChangeSetStore），它按 run 记录每次写操作 ——
      // 这才是"这次 agent 动了什么"的准确答案。
      // mock 的"4 个改动 +40 −12"是编的，删掉。
      body.appendChild(dataStateBar());

      if (!Backend.available()) {
        body.appendChild(emptyHint("后端未连接",
          "工作区改动来自 harness 的变更集记录。启动后端后即可看到。"));
        return;
      }
      if (!state.runs) {
        body.appendChild(el("div", "dloading", "读取运行记录…"));
        Backend.loadRuns();
        return;
      }
      if (!state.runs.length) {
        body.appendChild(emptyHint("还没有运行记录",
          "变更集是按运行记录的。跑过一次任务后，这里会列出它改过的文件。"));
        return;
      }

      var sess = el("div", "sect");
      var sh = el("button", "sect__head");
      sh.type = "button";
      sh.setAttribute("aria-expanded", "true");
      sh.appendChild(svg(ICONS.chevron, "sect__chev"));
      sh.appendChild(el("span", null, "工作区改动"));
      sh.appendChild(el("span", "sect__tail", state.runs.length + " 次运行"));
      sess.appendChild(sh);

      // 列出最近几次运行；每次可以展开看它改了哪些文件。
      // 这里**不做全量聚合**：变更集是 per-run 的，
      // 而"总共改了什么"需要跨 run 求并集 —— 那会掩盖"哪次改的"，
      // 而后者才是回滚时要的信息。
      state.runs.slice(0, 12).forEach(function (r) {
        var row = el("div", "scmrun");
        var top = el("div", "scmrun__top");
        top.appendChild(el("span", "scmrun__id tabular", r.id));
        top.appendChild(el("span", "scmrun__model truncate", r.model || ""));
        top.appendChild(el("span", "scmrun__time", r.tsMin == null ? "" : UI.fmtRel(r.tsMin)));
        row.appendChild(top);
        var act = el("button", "dbtn", "查看改动");
        act.type = "button";
        act.addEventListener("click", function () {
          window.Harness.runChangesets(r.id, r.workspace).then(function (p) {
            var cs = window.Harness.toChangesets(p);
            if (!cs.summary || cs.summary.changes === 0) {
              UI.toast({ tone: "info", title: "这次运行没有可回滚的改动",
                         desc: "r.id = " + r.id, duration: 4000 });
              return;
            }
            UI.toast({
              tone: "info", duration: 6000,
              title: r.id + " · " + cs.summary.changes + " 处改动 / "
                + cs.summary.files + " 个文件",
              desc: cs.history.slice(0, 5).map(function (h) {
                return "seq " + (h.seq == null ? "?" : h.seq) + " "
                  + (h.paths || []).join("、");
              }).join("\n"),
            });
          }).catch(function (e) {
            UI.toast({ tone: "danger", title: "读取变更集失败",
                       desc: String(e && e.message || e), duration: 5000 });
          });
        });
        row.appendChild(act);
        sess.appendChild(row);
      });
      body.appendChild(sess);

      var wt = el("div");
      wt.className = "dnote";
      wt.textContent = "变更集按每次运行独立记录。回滚入口在「会话历史」里选中那条运行后的明细中。";
      body.appendChild(wt);
      return;
    }

    if (state.railView === "sessions") {
      // 纯真实数据。三种状态分开处理：
      //   未连接 / 查询失败 / 有数据（含 0 条）
      // 合成一句"暂无数据"会让后端故障伪装成"这个项目没跑过任务"。
      body.appendChild(dataStateBar());

      if (!Backend.available()) {
        body.appendChild(emptyHint("后端未连接",
          "会话历史来自 harness 的落盘记录。启动后端后刷新页面即可看到真实运行。"));
        return;
      }
      if (state.runsError) {
        body.appendChild(emptyHint("读取失败",
          state.runsError.message + " —— 这不等于「没有运行记录」。"));
        return;
      }
      if (state.runs === null) {
        body.appendChild(el("div", "dloading", "读取运行记录…"));
        Backend.loadRuns();
        return;
      }
      if (!state.runs.length) {
        body.appendChild(emptyHint("还没有运行记录",
          "在后端连接的状态下，从下方输入框发一条指令就会产生第一条记录。"));
        return;
      }

      var s = el("div", "sect");
      var sh = el("div", "sect__head");
      sh.appendChild(el("span", null, "运行记录"));
      sh.appendChild(el("span", "sect__tail", state.runs.length + " 次 · 按时间倒序"));
      s.appendChild(sh);

      state.runs.forEach(function (x) {
        var isOpen = state.detailRun === x.id;
        var b = el("button", "sess" + (isOpen ? " is-on" : ""));
        b.type = "button";
        b.setAttribute("data-tip", x.workspace);
        var top = el("div", "sess__top");
        top.appendChild(el("span", "sess__title truncate", x.model || "（未知模型）"));
        top.appendChild(el("span", "sess__time", x.tsMin == null ? "" : UI.fmtRel(x.tsMin)));
        b.appendChild(top);

        var sub = el("div", "sess__sub");
        // 工作区只显示末尾两段 —— 完整路径太长且会挤掉真正有用的信息，
        // 但**不能省略**它：用户要能分辨"这是哪个目录里的运行"。
        var shortWs = String(x.workspace || "").split(/[\\/]/).slice(-2).join("/");
        sub.innerHTML =
          '<span class="sess__branch truncate">' + esc(shortWs) + "</span>" +
          '<span class="sess__stat">' + esc(x.sandbox || "") + "</span>" +
          '<span class="sess__stat sess__turns">' + esc(x.approval || "") + "</span>";
        b.appendChild(sub);

        b.addEventListener("click", function () {
          if (state.detailRun === x.id) {
            state.detailRun = null;
            state.detail = null;
            renderRail();
            return;
          }
          state.detailRun = x.id;
          state.detail = null;
          state.detailLoading = true;
          renderRail();
          Backend.loadDetail(x.id, x.workspace);
          UI.announce("已打开运行 " + x.id + " 的明细");
        });

        s.appendChild(b);

        // 展开时插入明细块
        if (isOpen) {
          var host = el("div", "sessdetail");
          s.appendChild(host);
          renderRunDetail(host);
        }

        b.addEventListener("contextmenu", function (ev) {
          ev.preventDefault();
          UI.contextMenu(ev.clientX, ev.clientY, [
            { label: "复制 run id", icon: ICONS.file, onClick: function () { UI.copy(x.id, "run id"); } },
            { label: "复制工作区路径", icon: ICONS.file, onClick: function () { UI.copy(x.workspace, "工作区路径"); } },
            { label: "复制前缀指纹", icon: ICONS.file, onClick: function () { UI.copy(x.prefixHash, "前缀指纹"); } },
            { sep: true },
            { label: "刷新这一条", icon: ICONS.clock, onClick: function () {
                state.detailRun = x.id;
                Backend.loadDetail(x.id, x.workspace);
              } },
          ]);
        });
      });
      body.appendChild(s);
      return;
    }

    if (state.railView === "mcp") {
      // 这个视图展示**后端真实注册的工具**，不是 MCP 服务开关 ——
      // 名称沿用 MCP 是因为活动栏图标已定，但内容是 harness 的工具清单。
      // 刻意不保留"启用开关"：工具是否可用由 policy/沙箱决定，
      // 前端放一个开关会让人以为它能改变权限，而它不能。
      body.appendChild(dataStateBar());

      if (!Backend.available()) {
        body.appendChild(emptyHint("后端未连接",
          "工具清单由 harness 提供。启动后端后即可看到真实注册的工具与它们的描述。"));
        return;
      }
      if (state.tools === null) {
        body.appendChild(el("div", "dloading", "读取工具清单…"));
        Backend.loadTools();
        return;
      }
      if (!state.tools.length) {
        body.appendChild(emptyHint("没有已注册的工具", "后端返回了空清单。"));
        return;
      }

      var m = el("div", "sect");
      var mh = el("div", "sect__head");
      var nTools = state.tools.reduce(function (n, g) { return n + g.tools.length; }, 0);
      mh.appendChild(el("span", null, "已注册工具"));
      mh.appendChild(el("span", "sect__tail", nTools + " 个 · " + state.tools.length + " 类"));
      m.appendChild(mh);

      state.tools.forEach(function (g) {
        m.appendChild(el("div", "mcpgroup__name", g.name));
        g.tools.forEach(function (t) {
          var row = el("div", "mcpitem");
          var meta = el("div", "mcpitem__meta");
          meta.appendChild(el("span", "mcpitem__name", t.name));
          var desc = String(t.desc || "");
          // 只显示第一句：完整描述里还有"什么时候用 / 边界 / 负例"，
          // 这里是列表，展开阅读交给 tooltip。
          var first = desc.split(/[。\n]/)[0];
          meta.appendChild(el("span", "mcpitem__desc", first + (desc.length > first.length ? "…" : "")));
          row.appendChild(meta);
          row.setAttribute("data-tip", desc);
          m.appendChild(row);
        });
      });
      body.appendChild(m);

      var note = el("div");
      note.style.cssText = "padding:8px 16px;font-size:10px;color:var(--text-faint);line-height:1.6;text-wrap:pretty";
      note.textContent = "工具顺序固定（注册顺序即输出顺序），这是保证提示词前缀可缓存的前提。"
        + "描述里包含「什么时候用」「边界」「替代工具」，模型据此选择。";
      body.appendChild(note);
      return;
    }
  }

  /* 资源管理器底部的提示块。
     原来显示的是 mock 的"已索引 · 1,284 文件 · 18.6 万 token" ——
     那个数字**根本不存在**：这个 harness 没有代码索引。
     现在只显示能核实的量，并把"没有索引"如实说出来。为什么重要：
     编一个索引状态会让人以为搜索是走索引的，从而误判搜索结果的可信度。 */
  function buildIndexNote() {
    var d = el("div");
    d.className = "idxnote";
    if (!Backend.available()) {
      d.textContent = "后端未连接，无法读取工作区信息。";
      return d;
    }
    var info = state.wsInfo;
    if (!info) {
      d.textContent = state.wsInfoError
        ? "读取工作区信息失败：" + state.wsInfoError
        : "正在读取工作区…";
      Backend.loadWorkspaceInfo();
      return d;
    }
    var parts = [
      info.files + " 个文件 · " + info.dirs + " 个目录",
      info.branch ? "分支 " + info.branch : "不在 git 仓库中",
    ];
    if (info.truncated) {
      parts.push("（扫描已截断：超过 " + info.scan.max_files + " 个文件）");
    }
    d.textContent = parts.join(" · ") + "\n" + info.index_note;
    return d;
  }

  /* 转义 HTML。替换表的键有个**实打实的 bug 曾在这里**：
     早期写成 `"<<"`，于是 `<` 根本不会被转义 ——
     而 `<` 恰恰是最需要转义的字符（它是标签起始）。
     这个 bug 在正常数据下看不出来（很少出现裸 `<`），
     但一旦路径或模型名里带它，就会破坏 DOM 结构。 */
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c] || c;
    });
  }

  /* 空状态提示：复用已有的 `.empty` 视觉，避免同一语义出现两套样式。
     为什么值得单独一个函数：前端有七八处"这里没东西"，
     而**没东西的原因**往往比"没东西"本身重要得多
     （未连接 / 读取失败 / 确实为空）。统一成一个函数，
     就不容易出现"某处忘了说原因"。 */
  function emptyHint(title, desc) {
    var b = el("div", "empty");
    b.appendChild(el("p", "empty__title", title));
    if (desc) b.appendChild(el("p", "empty__hint", desc));
    return b;
  }

  /* ============================================================
     二、标签栏 + 编辑器
     ============================================================ */
  function openTab(path) {
    var exists = state.tabs.some(function (t) { return t.path === path; });
    if (!exists) state.tabs.push({ path: path, dirty: !!dirtyFile[path] });
    state.activeTab = path;
    renderTabs();
    renderEditor();
    if (state.railView === "explorer") renderRail();
  }

  /* 关闭标签：脏文件先确认；破坏性操作用模态而不是静默丢弃 */
  function closeTab(path, force) {
    var i = state.tabs.findIndex(function (t) { return t.path === path; });
    if (i < 0) return;
    var tab = state.tabs[i];

    if (tab.dirty && !force) {
      UI.confirm({
        title: "放弃未保存的改动？",
        desc: "「" + path.split("/").pop() + "」有未保存的改动。关闭后这些改动会丢失，本操作无法撤销。",
        confirmLabel: "放弃并关闭",
        cancelLabel: "保留",
        tone: "danger",
        detail: path,
      }).then(function (ok) {
        if (ok) closeTab(path, true);
      });
      return;
    }

    state.tabs.splice(i, 1);
    if (state.activeTab === path) {
      var next = state.tabs[Math.min(i, state.tabs.length - 1)];
      state.activeTab = next ? next.path : null;
    }
    renderTabs();
    renderEditor();
    UI.announce("已关闭 " + path.split("/").pop());
  }

  function closeOthers(keep) {
    state.tabs = state.tabs.filter(function (t) { return t.path === keep || t.dirty; });
    if (!state.tabs.some(function (t) { return t.path === keep; })) openTab(keep);
    state.activeTab = keep;
    renderTabs();
    renderEditor();
    UI.toast({ tone: "info", title: "已关闭其他标签", desc: "有未保存改动的标签被保留。", duration: 3200 });
  }

  function cycleTab(dir) {
    if (state.tabs.length < 2) return;
    var i = state.tabs.findIndex(function (t) { return t.path === state.activeTab; });
    var n = (i + dir + state.tabs.length) % state.tabs.length;
    state.activeTab = state.tabs[n].path;
    renderTabs();
    renderEditor();
    UI.announce(state.tabs[n].path.split("/").pop());
  }

  function renderTabs() {
    var bar = D.tabbarScroll;
    bar.innerHTML = "";

    if (!state.tabs.length) {
      var e = el("span");
      e.style.cssText = "padding:0 14px;display:flex;align-items:center;font-size:11px;color:var(--text-faint)";
      e.textContent = "没有打开的文件";
      bar.appendChild(e);
      updateTabOverflow();
      return;
    }

    state.tabs.forEach(function (t, idx) {
      var name = t.path.split("/").pop();
      var b = el("button", "tab" + (t.path === state.activeTab ? " is-on" : ""));
      b.type = "button";
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", t.path === state.activeTab ? "true" : "false");
      b.tabIndex = t.path === state.activeTab ? 0 : -1;
      b.setAttribute("data-path", t.path);

      var ic = svg(ICONS.file, "tab__icon");
      b.appendChild(ic);

      var nm = el("span", "tab__name truncate");
      nm.textContent = name;
      b.appendChild(nm);

      if (t.dirty) {
        var dot = el("span", "tab__dirty");
        dot.setAttribute("aria-label", "有未保存改动");
        dot.setAttribute("data-tip", "有未保存改动");
        b.appendChild(dot);
      }

      var c = el("button", "tab__close");
      c.type = "button";
      c.setAttribute("aria-label", "关闭 " + name);
      c.setAttribute("data-tip", "关闭（中键亦可）");
      c.appendChild(svg(ICONS.x));
      c.addEventListener("click", function (ev) {
        ev.stopPropagation();
        closeTab(t.path);
      });
      b.appendChild(c);

      // 路径过长时，悬停给出完整路径；否则给最近两级
      var shortPath = t.path.split("/").slice(-2).join("/");
      b.setAttribute("data-tip", t.path.length > 20 ? t.path : shortPath);

      b.addEventListener("click", function () {
        state.activeTab = t.path;
        renderTabs();
        renderEditor();
      });

      // 中键关闭 —— 编辑器里几乎人人都会试这个手势
      b.addEventListener("auxclick", function (ev) {
        if (ev.button === 1) { ev.preventDefault(); closeTab(t.path); }
      });

      // 双击标签区空白处新建：这里不做，避免误触。右键给菜单
      b.addEventListener("contextmenu", function (ev) {
        ev.preventDefault();
        tabMenu(ev.clientX, ev.clientY, t, idx);
      });

      // 拖拽重排
      b.draggable = true;
      b.addEventListener("dragstart", function (ev) {
        ev.dataTransfer.setData("text/plain", String(idx));
        ev.dataTransfer.effectAllowed = "move";
        document.body.classList.add("is-dragging");
      });
      b.addEventListener("dragend", function () {
        document.body.classList.remove("is-dragging");
        $$(".tab", D.tabbarScroll).forEach(function (n) {
          n.classList.remove("tab--drop-before", "tab--drop-after");
        });
      });
      b.addEventListener("dragover", function (ev) {
        ev.preventDefault();
        var r = b.getBoundingClientRect();
        var before = ev.clientX < r.left + r.width / 2;
        b.classList.toggle("tab--drop-before", before);
        b.classList.toggle("tab--drop-after", !before);
      });
      b.addEventListener("dragleave", function () {
        b.classList.remove("tab--drop-before", "tab--drop-after");
      });
      b.addEventListener("drop", function (ev) {
        ev.preventDefault();
        var from = parseInt(ev.dataTransfer.getData("text/plain"), 10);
        if (isNaN(from) || from === idx) return;
        var r = b.getBoundingClientRect();
        var to = ev.clientX < r.left + r.width / 2 ? idx : idx + 1;
        var moved = state.tabs.splice(from, 1)[0];
        if (from < to) to--;
        state.tabs.splice(to, 0, moved);
        renderTabs();
      });

      bar.appendChild(b);
    });

    // 激活的标签始终保持可见
    var active = bar.querySelector(".tab.is-on");
    if (active && active.scrollIntoView) {
      active.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
    updateTabOverflow();
  }

  function tabMenu(x, y, t, idx) {
    var name = t.path.split("/").pop();
    UI.contextMenu(x, y, [
      { label: "关闭", icon: ICONS.x, hint: "Ctrl+W", onClick: function () { closeTab(t.path); } },
      { label: "关闭其他", icon: ICONS.x, onClick: function () { closeOthers(t.path); } },
      { sep: true },
      { label: "复制文件名", icon: ICONS.file, onClick: function () { UI.copy(name, "文件名"); } },
      { label: "复制相对路径", icon: ICONS.file, onClick: function () { UI.copy(t.path, "路径"); } },
      { sep: true },
      { label: t.dirty ? "保存" : "已保存", icon: ICONS.check, disabled: !t.dirty, onClick: function () { markSaved(t.path); } },
      { label: "在资源管理器中显示", icon: ICONS.dirClosed, onClick: function () {
          // 走 goView 而不是自己抄一遍按钮状态：
          // 抄的那份会漏掉"侧栏被折叠时要展开"，于是点了没反应。
          goView("explorer");
        } },
    ]);
  }

  function markSaved(path) {
    dirtyFile[path] = false;
    var t = state.tabs.filter(function (x) { return x.path === path; })[0];
    if (t) t.dirty = false;
    renderTabs();
    UI.toast({ tone: "ok", title: "已保存 " + path.split("/").pop(), duration: 2600 });
  }

  /* 标签栏溢出：左/右渐隐 + 「更多」入口 + 计数 */
  function updateTabOverflow() {
    var el2 = D.tabbarScroll;
    var more = $("#tabbar-more");
    var cnt = $("#tabbar-count");
    if (!el2) return;
    var canL = el2.scrollLeft > 2;
    var canR = el2.scrollLeft + el2.clientWidth < el2.scrollWidth - 2;
    D.tabbar.setAttribute("data-overflow-left", canL ? "true" : "false");
    D.tabbar.setAttribute("data-overflow-right", canR ? "true" : "false");

    var n = state.tabs.length;
    if (more) more.hidden = n < 2;
    if (cnt) {
      cnt.hidden = !(canL || canR) || n < 2;
      cnt.textContent = n + " 个";
    }
  }

  /* 轻量语法着色：按「行 → 片段」渲染，行号与行数由同一份数据推导 */
  function escHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function renderCodeLines(lines) {
    return lines.map(function (segs) {
      if (!segs || !segs.length) return "";
      return segs.map(function (sg) {
        if (!sg[0]) return escHtml(sg[1]);
        return '<span class="t-' + sg[0] + '">' + escHtml(sg[1]) + "</span>";
      }).join("");
    }).join("\n");
  }

  function gutterFor(n) {
    var out = [];
    for (var i = 1; i <= n; i++) out.push(String(i));
    return out.join("\n");
  }


  function renderEditor() {
    var pre = $("#editor-code");
    var code = pre.firstChild;
    var gutter = $("#editor-gutter");
    var empty = $("#editor-empty");

    if (!state.activeTab) {
      pre.hidden = true;
      gutter.hidden = true;
      empty.hidden = false;
      gutter.textContent = "";
      return;
    }
    pre.hidden = false;
    gutter.hidden = false;
    empty.hidden = true;

    var path = state.activeTab;

    // 后端未连接 → 明确说明来源，而不是显示不存在的文件内容
    if (!Backend.available()) {
      code.innerHTML = '<span class="t-cm">'
        + escHtml("// 后端未连接，无法读取工作区文件。\n// 启动 `python harness/server.py` 后刷新即可。")
        + "</span>";
      gutter.textContent = "";
      return;
    }

    var f = state.fileCache[path];
    if (f === undefined || f === null) {
      // 首次打开：显示加载态并异步取真实内容
      code.innerHTML = '<span class="t-cm">' + escHtml("// 读取中…") + "</span>";
      gutter.textContent = "";
      Backend.loadFile(path);
      return;
    }
    if (f.error) {
      code.innerHTML = '<span class="t-cm">'
        + escHtml("// 无法打开：" + f.error + "\n// 这不等于「文件是空的」。")
        + "</span>";
      gutter.textContent = "";
      return;
    }

    var lines = f.lines || [];
    if (lines.length) {
      // 真实文本：**不做语法着色**。着色需要语言解析器，
      // 而瞎猜关键字会把普通文本染成花花绿绿 —— 那比不着色更难看懂。
      // 只在行尾如实标出编码与截断情况。
      code.innerHTML = lines.map(function (l) {
        return '<span class="t-plain">' + (l === "" ? "&nbsp;" : escHtml(l)) + "</span>";
      }).join("\n");
      // 行号直接来自服务端（它读了多少行就是多少行）——
      // 前端自己 split 会在"正好卡在截断处"时错位
      gutter.textContent = gutterFor(lines.length);
      $("#editor-scroll").scrollTop = 0;

      var meta = el("div", "filemeta");
      meta.textContent = f.path + " · " + UI.fmtBytes(f.size) + " · "
        + f.line_count + " 行 · " + f.encoding
        + (f.truncated ? " · 已截断（只显示前 " + lines.length + " 行）" : "");
      var oldMeta = $("#editor-scroll .filemeta");
      if (oldMeta) oldMeta.remove();
      $("#editor-scroll").appendChild(meta);
      return;
    }

    // 空文件：如实说是空的（不伪造占位正文）
    code.innerHTML = '<span class="t-cm">' + escHtml("// （空文件）") + "</span>";
    gutter.textContent = "";
  }

  /* ============================================================
     三、终端
     ============================================================ */
  /* ============================================================
     三、终端 —— 带命令历史与真实回显
     ============================================================ */
  // 终端：**真实只读命令**。
  // 原来这里是纯 mock（假 zsh、编好的输出、连"command not found"都是写死的）。
  // 现在走后端 /api/workspace/terminal —— 它有白名单 + 元字符拒绝两道限制。
  var termLines = [];
  var termHistory = [];
  var termHistIdx = -1;
  var termBusy = false;

  function termAppend(l) {
    termLines.push(l);
    var body = D.termBody;
    var s = el("span", "tl-" + (l.t === "cmd" ? "cmd" : l.t === "out-hi" ? "out-hi" : l.t === "dim" ? "dim" : l.t === "pass" ? "pass" : l.t === "fail" ? "fail" : "out"));
    s.textContent = l.text + "\n";
    body.appendChild(s);
    D.termScroll.scrollTop = D.termScroll.scrollHeight;
    if (refreshTermFade) refreshTermFade();
  }

  function renderTerminal() {
    D.termBody.innerHTML = "";
    termLines.forEach(function (l) {
      var cls = l.t === "cmd" ? "cmd" : l.t === "out-hi" ? "out-hi" : l.t === "dim" ? "dim" : l.t === "pass" ? "pass" : l.t === "fail" ? "fail" : "out";
      var s = el("span", "tl-" + cls);
      s.textContent = l.text + "\n";
      D.termBody.appendChild(s);
    });
    D.termScroll.scrollTop = D.termScroll.scrollHeight;
    if (refreshTermFade) refreshTermFade();
  }

  function runCommand(raw) {
    var cmd = raw.trim();
    if (!cmd) return;

    termHistory.push(cmd);
    termHistIdx = termHistory.length;
    termAppend({ t: "cmd", text: "$ " + cmd });

    // 本地命令：先给即时反馈，再模拟输出
    if (cmd === "clear" || cmd === "cls") {
      termLines = [];
      renderTerminal();
      return;
    }
    if (cmd === "help") {
      termAppend({ t: "dim", text: "这是受控只读终端：允许 ls/cat/head/tail/grep/find/wc/"
        + "git status 等只读命令；禁止管道、重定向、命令串联等 shell 元字符。" });
      return;
    }

    if (!Backend.available()) {
      termAppend({ t: "fail", text: "后端未连接，命令无法执行。" });
      termAppend({ t: "dim", text: "这个终端由 harness 执行真实命令，不是前端模拟的。" });
      return;
    }

    termBusy = true;
    termAppend({ t: "dim", text: "…执行中" });
    window.Harness.workspaceTerminal(state.workspace, cmd)
      .then(function (r) {
        termBusy = false;
        // 去掉那行"执行中"占位
        for (var i = termLines.length - 1; i >= 0; i--) {
          if (termLines[i].text === "…执行中") { termLines.splice(i, 1); break; }
        }
        if (!r) { renderTerminal(); termAppend({ t: "fail", text: "请求失败。" }); return; }
        if (r.ok === false && r.code) {
          // 被白名单 / 元字符拒绝：**把原因显示出来**。
          // 静默失败会让人以为终端坏了，而不是"这条命令不允许"。
          renderTerminal();
          termAppend({ t: "fail", text: "已拒绝：" + (r.detail || r.code) });
          return;
        }
        renderTerminal();
        var txt = (r.text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
        txt.split("\n").forEach(function (line, i, arr) {
          if (i === arr.length - 1 && !line) return;
          termAppend({ t: r.ok ? "out" : "fail", text: line });
        });
        if (typeof r.exit_code === "number" && r.exit_code !== 0) {
          termAppend({ t: "dim", text: "退出码 " + r.exit_code });
        }
      })
      .catch(function (e) {
        termBusy = false;
        renderTerminal();
        termAppend({ t: "fail", text: "执行失败：" + String(e && e.message || e) });
      });
  }

  function termHistoryMove(dir) {
    if (!termHistory.length) return;
    termHistIdx = Math.max(0, Math.min(termHistory.length, termHistIdx + dir));
    D.termInput.value = termHistIdx >= termHistory.length ? "" : termHistory[termHistIdx];
    // 光标落到末尾，符合 shell 的习惯
    var v = D.termInput.value;
    D.termInput.setSelectionRange(v.length, v.length);
  }

  /* ============================================================
     四、Agent 面板：计划 / 事件流 / 闸门
     ============================================================ */
  /* 执行计划视图。
     ## 这里有个必须说清楚的边界
     **harness 没有"计划"概念** —— 它不做 Plan DAG、不预告步骤，
     而是 ReAct 单循环（走一步看一步）。所以这个面板**不能接真实计划**，
     因为它不存在。编一份假的计划比留空更坏：它会让人以为 agent
     真按某个计划在走，从而误判"它偏离计划了没有"。

     能做的是接**真实的执行轨迹**：每次工具调用就是一步，
     状态来自它的 ok/失败。这是真实数据，只是它的语义是
     "已经做了什么"，不是"打算做什么"。面板标题与文案都据此改写。 */
  function renderPlan() {
    var list = $("#plan-list");
    var goal = $("#plan-goal");
    var count = $("#plan-count");
    var card = $("#plan-view");
    list.innerHTML = "";
    if (!card) return;

    var vms = state.streamMode === "replay" ? (state.replayVMs || []) : liveVMs;
    var steps = vms.filter(function (v) {
      return v.kind === "tool" || v.kind === "gate";
    });

    if (state.streamMode === "mock" || !steps.length) {
      // 没有真实轨迹时的说明态。标题也改掉 —— 写"执行计划"会误导。
      var label = $("#plan-view .planv__label");
      if (label) label.textContent = "执行轨迹";
      goal.textContent = Backend.available()
        ? "还没有可显示的步骤。发一条指令、或回放一条历史运行后，"
          + "这里会按时间列出 agent 实际做过的每一步。"
        : "后端未连接。这是演示占位 —— 真实轨迹来自 harness 的事件流。";
      if (count) count.textContent = "0/0";
      return;
    }

    var label2 = $("#plan-view .planv__label");
    if (label2) label2.textContent = "执行轨迹";
    goal.textContent = state.streamMode === "replay"
      ? "历史运行 · " + String(state.replayDetail && state.replayDetail.runId || "")
      : "本次运行 · 按时间顺序";

    steps.forEach(function (v, i) {
      var st = v.state === "running" ? "active"
             : (v.kind === "gate" ? "todo" : "done");
      var li = el("li", "pstep pstep--" + st);
      var mark = el("span", "pstep__mark");
      if (st === "done") mark.appendChild(svg(ICONS.check, null));
      else mark.textContent = String(i + 1);
      li.appendChild(mark);

      var box = el("div");
      box.appendChild(el("span", "pstep__text",
        v.kind === "gate" ? "闸门 · " + (v.action || "") : (v.title || v.tool || "")));
      // 依据：真实工具的耗时 / 闸门的风险等级
      var ev = v.kind === "gate" ? ("风险 " + (v.risk || "?"))
             : ((v.output && v.output.length)
                 ? (v.state === "done" ? "已返回结果" : "执行中")
                 : "");
      if (v.dur) ev += (ev ? " · " : "") + v.dur;
      if (ev) box.appendChild(el("span", "pstep__ev", ev));
      li.appendChild(box);
      list.appendChild(li);
    });

    var done = steps.filter(function (v) { return v.state === "done"; }).length;
    if (count) count.textContent = done + "/" + steps.length;
  }

  function renderStream() {
    var stream = D.stream;
    stream.innerHTML = "";

    // ── 按模式分派 ──
    // 三种模式互斥，且**都在这里显式写出来** —— 漏掉一种会退化成
    // "什么都不画"，而那看起来像"没有事件"，不像"代码有分支没接上"。
    if (state.streamMode === "replay") {
      renderReplayStream(stream);
      applyFilter();
      if (refreshStreamFade) refreshStreamFade();
      return;
    }
    if (state.streamMode === "live") {
      // 实时模式下由 rerenderLive() 负责（它在 SSE 回调里被反复调用），
      // 这里主动画一次只是为了切模式时立刻有内容。
      rerenderLive();
      applyFilter();
      if (refreshStreamFade) refreshStreamFade();
      return;
    }

    // 事件流没有任何 mock 兜底（按"纯真实数据"要求）：
    // 没有真实事件时显示怎么产生事件，而不是铺一屏假事件。
    // 假事件最坏的地方在于它**看起来像真跑过一次**。
    stream.appendChild(emptyHint(
      Backend.available() ? "还没有事件" : "后端未连接",
      Backend.available()
        ? "在下方输入框发一条指令即可看到真实事件流；"
          + "或在左侧「会话历史」里选一条历史运行点「回放」。"
        : "事件流来自 harness。启动后端后发一条指令即可看到真实事件。"));

    applyFilter();
    if (refreshStreamFade) refreshStreamFade();
  }

  /* ---------- 历史回放渲染 ----------
     放在事件流顶部的一条"回放标识"。**这条不能省**：
     没有它，用户会把一份已经跑完的历史轨迹看成"agent 正在跑"。
     而 agent 类的界面里，"现在到底有没有在动"是最要紧的一件事。 */
  function replayBanner() {
    var d = state.replayDetail || {};
    var b = el("div", "replaybar");
    var dot = el("span", "replaybar__dot");
    b.appendChild(dot);
    var txt = el("span", "replaybar__text");
    txt.textContent = "历史回放 · " + (d.model || "未知模型")
      + " · " + (d.count || 0) + " 个事件"
      + (d.status ? " · 结束状态 " + d.status : "")
      + " · 已结束，不会继续产生事件";
    b.appendChild(txt);
    var btn = el("button", "replaybar__btn", "退出回放");
    btn.type = "button";
    btn.addEventListener("click", function () {
      Backend.exitReplay();
    });
    b.appendChild(btn);
    return b;
  }

  function renderReplayStream(stream) {
    stream.appendChild(replayBanner());

    // 回放失败要说清原因，而不是留一个空事件流 ——
    // 空的会被读成"这条运行没有事件"，而实际是"读不到"。
    if (state.replayError) {
      stream.appendChild(emptyHint("回放失败", state.replayError
        + " —— 这不等于「这条运行没有事件」。"));
      return;
    }

    var vms = state.replayVMs;
    if (vms === null) {
      stream.appendChild(el("div", "dloading", "读取落盘事件…"));
      return;
    }
    if (!vms.length) {
      stream.appendChild(emptyHint("这条运行没有可显示的事件",
        "落盘事件为空，或全部属于未识别类型。"));
      return;
    }
    var frag = document.createDocumentFragment();
    vms.forEach(function (vm, i) {
      var node = buildEvent(vm, "replay" + (i + 1));
      if (node) frag.appendChild(node);
    });
    stream.appendChild(frag);
  }

  /* ---------- 事件过滤 ---------- */
  function evMatchesFilter(node, f) {
    if (f === "all") return true;
    if (f === "tools") return node.classList.contains("ev--tool") || node.classList.contains("ev--patch");
    if (f === "gate") return node.classList.contains("ev--gate");
    return true;
  }

  function applyFilter() {
    var f = state.filter;
    var nodes = $$(".ev", D.stream);
    var shown = 0;
    nodes.forEach(function (n) {
      var keep = evMatchesFilter(n, f);
      n.setAttribute("data-hidden", keep ? "false" : "true");
      if (keep) shown++;
    });

    // 空结果给出路，而不是留一片空白
    var none = $(".stream__none", D.stream);
    if (!shown) {
      if (!none) {
        none = el("div", "stream__none");
        none.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="10" cy="10" r="6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M14.5 14.5L19 19" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
        none.appendChild(el("p", null, "当前筛选下没有事件"));
        none.appendChild(el("p", null, "切回「全部」即可看到完整事件流。"));
        D.stream.appendChild(none);
      }
      none.hidden = false;
    } else if (none) {
      none.hidden = true;
    }

    var total = nodes.length;
    updateStreamStat(shown);

    ["all", "tools", "gate"].forEach(function (k) {
      var b = $("#btn-filter-" + k);
      if (b) {
        b.setAttribute("aria-pressed", state.filter === k ? "true" : "false");
      }
    });
  }

  function setFilter(f) {
    state.filter = f;
    persisted.filter = f;
    save();
    applyFilter();
    if (refreshStreamFade) refreshStreamFade();
    var label = f === "all" ? "全部事件" : f === "tools" ? "仅工具调用" : "仅审批闸门";
    UI.announce("筛选：" + label);
  }

  /* 自证条。
     ## 三态必须守住（这是本项目反复强调的那条）
     mock 版写死"自证检查通过 · 4/4"——**它永远说通过**，
     而那正是这个项目花了一整轮去消灭的东西（假成功报告）。
     现在按真实数据分三态：
       · 有检查事件   → 显示"通过 N 条 / 有 M 条未满足"
       · 没有检查事件 → 显示"本次运行没有目标条件"，**不写"通过"**
       · 后端未连接   → 显示演示态并明确标注
     关键：**不许在没有任何证据时显示"通过"**。 */
  function showVerify() {
    var v = $("#verify-bar");
    if (!v) return;
    v.innerHTML = "";
    var dot = el("span", "verify__dot");
    dot.setAttribute("aria-hidden", "true");
    v.appendChild(dot);

    var vms = state.streamMode === "replay" ? (state.replayVMs || []) : liveVMs;
    // 只统计**真实的**自证信号：check 事件里的 target/goal 检查
    var checks = vms.filter(function (x) { return x.kind === "check" && x.scope; });
    var errs = vms.filter(function (x) {
      return x.kind === "check" && (x.items || []).some(function (it) { return it.ok === false; });
    });

    if (state.streamMode === "mock") {
      dot.className = "verify__dot";
      v.appendChild(el("span", "verify__label", "演示数据 · 无真实自证"));
      v.appendChild(el("span", "verify__spacer"));
      v.appendChild(el("span", "verify__model", "未连接"));
      v.hidden = false;
      return;
    }

    if (!checks.length && !errs.length) {
      dot.className = "verify__dot";
      v.appendChild(el("span", "verify__label",
        "本次运行没有自证检查记录 —— 这不等于「检查通过」"));
      v.appendChild(el("span", "verify__spacer"));
      v.appendChild(el("span", "verify__model",
        state.streamMode === "replay" ? "历史回放" : "实时"));
      v.hidden = false;
      return;
    }

    var bad = errs.length;
    dot.className = "verify__dot" + (bad ? " verify__dot--warn" : "");
    v.appendChild(el("span", "verify__label",
      bad ? ("自证检查有 " + bad + " 项未通过 · 共 " + checks.length + " 项记录")
          : ("自证检查通过 · " + checks.length + " 项记录")));
    v.appendChild(el("span", "verify__spacer"));
    v.appendChild(el("span", "verify__model",
      state.streamMode === "replay" ? "历史回放" : "实时"));
    v.hidden = false;
  }

  function buildEvent(ev, idx) {
    var node;
    switch (ev.kind) {
      case "reasoning": node = buildReasoning(ev); break;
      case "tool": node = buildTool(ev); break;
      case "patch": node = buildPatch(ev); break;
      case "gate": node = buildGate(ev); break;
      case "check": node = buildCheck(ev); break;
      default:
        // 未知 kind 一律不渲染。绝不产出空白卡片 ——
        // 一张空的白卡片比不渲染更容易被误判成"渲染成功"。
        return null;
    }
    node.setAttribute("data-ev-idx", String(idx == null ? 0 : idx));
    return node;
  }

  function buildReasoning(ev) {
    var d = el("div", "ev ev--reasoning");
    var head = el("div", "ev__head");
    head.appendChild(el("span", "ev__kind", "推理"));
    if (ev.dur) head.appendChild(el("span", "ev__dur", "耗时 " + ev.dur));
    var t = el("span", "ev__time");
    t.textContent = fmtClock(ev.tsOffset);
    head.appendChild(t);
    var sp = el("span", "ev__spacer");
    head.appendChild(sp);
    d.appendChild(head);
    d.appendChild(el("p", "ev__text", ev.text));
    return d;
  }

  /* 事件时间：用会话起始时间偏移，避免每秒都在跳 */
  function fmtClock(offsetMs) {
    if (offsetMs == null) return "";
    var base = sessionStart || Date.now();
    var t = new Date(base + offsetMs);
    try {
      return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(t);
    } catch (e) {
      return String(t.getHours()).padStart(2, "0") + ":" + String(t.getMinutes()).padStart(2, "0");
    }
  }
  var sessionStart = null;

  function buildTool(ev) {
    var wrap = el("div", "ev ev--tool");
    var card = el("div", "tool");
    var head = el("button", "tool__head");
    head.type = "button";
    head.setAttribute("aria-expanded", "false");

    var badge = el("span", "tool__badge");
    var ico = TOOL_ICON[ev.tool];
    if (ico) {
      var ic = svg(ico, "tool__ico");
      badge.appendChild(ic);
    }
    badge.appendChild(document.createTextNode(ev.tool));
    head.appendChild(badge);

    head.appendChild(el("span", "tool__title", ev.title));

    if (ev.state === "running") {
      var sp = el("span", "ev__spin");
      sp.setAttribute("aria-hidden", "true");
      head.appendChild(sp);
    } else if (ev.dur) {
      head.appendChild(el("span", "ev__dur", ev.dur));
    }
    head.appendChild(svg(ICONS.chevron, "tool__chev"));
    card.appendChild(head);

    // 元信息行：和复制按钮同处一行，避免把按钮嵌进 <button> 里
    var metaline = el("div", "tool__metaline");
    var meta = el("div", "tool__meta");
    (ev.meta || []).forEach(function (m) {
      var s = el("span", "meta");
      s.appendChild(el("span", "meta__k", m.k));
      s.appendChild(el("span", "meta__v", m.v));
      meta.appendChild(s);
    });
    metaline.appendChild(meta);
    metaline.appendChild(copyBtn(function () {
      return (ev.output || []).map(function (l) { return l.text; }).join("\n") || ev.title;
    }, "输出", "复制输出"));
    card.appendChild(metaline);

    var bodyEl = el("div", "tool__body");
    bodyEl.hidden = true;
    if (ev.output) {
      var ol = el("pre", "outlines copyscope");
      ev.output.forEach(function (l) {
        var cls = l.t === "pass" ? "out-pass" : l.t === "fail" ? "out-fail" : l.t === "dim" ? "out-dim" : "out-plain";
        var s = el("span", cls);
        s.textContent = l.text + "\n";
        ol.appendChild(s);
      });
      bodyEl.appendChild(ol);
    } else {
      var p = el("div", "outlines out-plain copyscope");
      p.textContent = ev.title;
      bodyEl.appendChild(p);
    }
    card.appendChild(bodyEl);

    var expanded = false;
    head.addEventListener("click", function () {
      expanded = !expanded;
      head.setAttribute("aria-expanded", expanded ? "true" : "false");
      bodyEl.hidden = !expanded;
      metaline.hidden = expanded;
    });

    wrap.appendChild(card);
    return wrap;
  }

  /* 把后端给的 unified diff 文本解析成前端 diff 行。
     harness 的 apply_patch 返回的是标准 @@ 头 +  +/-/空格 前缀，
     和前端已有的 rows 结构只差一层转换 —— 不做推断，只做映射。 */
  function parseUnifiedDiff(text) {
    if (!text || typeof text !== "string") return null;
    var out = [];
    var lines = text.split("\n");
    for (var i = 0; i < lines.length; i++) {
      var l = lines[i];
      if (l === "" && i === lines.length - 1) continue;
      if (/^diff --git |^index |^--- |^\+\+\+ /.test(l)) continue;
      if (/^@@/.test(l)) {
        out.push({ t: "meta", text: l });
        continue;
      }
      if (l.charAt(0) === "+") out.push({ t: "add", text: l.slice(1) });
      else if (l.charAt(0) === "-") out.push({ t: "del", text: l.slice(1) });
      else if (l.charAt(0) === " ") out.push({ t: "ctx", text: l.slice(1) });
      else out.push({ t: "ctx", text: l });
    }
    return out.length ? out : null;
  }

  function buildPatch(ev) {
    var wrap = el("div", "ev ev--tool");
    var card = el("div", "tool");

    var head = el("button", "tool__head");
    head.type = "button";
    head.setAttribute("aria-expanded", "true");
    var badge = el("span", "tool__badge");
    badge.appendChild(svg(ICONS.edit, "tool__ico"));
    badge.appendChild(document.createTextNode("apply_patch"));
    head.appendChild(badge);
    head.appendChild(el("span", "tool__title", ev.file));
    if (ev.dur) head.appendChild(el("span", "ev__dur", ev.dur));
    head.appendChild(svg(ICONS.chevron, "tool__chev"));
    card.appendChild(head);

    var bodyEl = el("div", "tool__body");

    // 只用**真实** diff。mock 的 PATCHES 回退已删除 ——
    // 没有 diff 时显示"未提供 diff"比编一段更像补丁的文本安全得多：
    // 编出来的 diff 会被当成"agent 真的这样改的"。
    var rows = ev.diffText ? parseUnifiedDiff(ev.diffText) : null;

    if (rows) {
      // 统计增删
      var adds = 0, dels = 0;
      rows.forEach(function (r) { if (r.t === "add") adds++; if (r.t === "del") dels++; });

      var diff = el("div", "diff");
      var dh = el("div", "diff__head");
      // "新增文件"的判断改用**真实 diff 形状**：全是 add 且没有 del。
      // 依赖 mock 的 meta.isNew 会在真实运行时永远不显示这个标记。
      if (adds > 0 && dels === 0 && !rows.some(function (r) { return r.t === "ctx"; })) {
        dh.appendChild(el("span", "diff__badge", "新增"));
      }
      dh.appendChild(el("span", "diff__file", ev.file));
      var nums = el("span", "diff__nums");
      nums.innerHTML = '<span class="diff__add">+' + adds + "</span>" +
        (dels ? ' <span class="diff__del">−' + dels + "</span>" : "");
      dh.appendChild(nums);

      var dacts = el("div", "diff__acts");
      dacts.appendChild(copyBtn(function () {
        return rows.map(function (l) {
          return (l.t === "add" ? "+" : l.t === "del" ? "-" : " ") + l.text;
        }).join("\n");
      }, "补丁", "复制补丁"));
      dh.appendChild(dacts);
      diff.appendChild(dh);

      var dbody = el("div", "diff__body");
      var nums2 = el("div", "diff__nums2");
      var lines = el("div", "diff__lines");
      var leftNo = 0;
      rows.forEach(function (l) {
        if (l.t === "add") leftNo++;
        var lft = l.t === "add" ? leftNo : "";
        var rgt = l.n && l.n[1] != null ? l.n[1] : "";
        nums2.appendChild(document.createTextNode(pad(lft, 3) + " " + pad(rgt, 3) + "\n"));
        var ln = el("span", "dline dline--" + (l.t === "add" ? "add" : l.t === "del" ? "del" : l.t === "meta" ? "meta" : "ctx"));
        ln.textContent = (l.t === "add" ? "+" : l.t === "del" ? "−" : " ") + l.text + "\n";
        lines.appendChild(ln);
      });
      dbody.appendChild(nums2);
      dbody.appendChild(lines);
      diff.appendChild(dbody);
      bodyEl.appendChild(diff);
    } else {
      // 没有 diff 时如实说明。**不编一段补丁** ——
      // 编出来的 diff 会被读成"agent 真的这样改的"。
      var nd = el("div", "outlines out-dim copyscope");
      nd.textContent = ev.file
        ? "这个文件被改动了，但事件里没有携带 diff 正文。"
        : "这次工具调用没有可显示的 diff。";
      bodyEl.appendChild(nd);
    }

    card.appendChild(bodyEl);

    var expanded = true;
    head.addEventListener("click", function () {
      expanded = !expanded;
      head.setAttribute("aria-expanded", expanded ? "true" : "false");
      bodyEl.hidden = !expanded;
    });

    wrap.appendChild(card);
    return wrap;
  }

  function pad(n, w) {
    var s = n === "" || n == null ? "" : String(n);
    while (s.length < w) s = " " + s;
    return s;
  }

  /* ---- 审批闸门：本页的记忆点 ---- */
  function buildGate(ev) {
    var wrap = el("div", "ev ev--gate");

    if (state.gateResolved) {
      // 已决态：降为历史条目
      var decided = el("div", "gate is-approved" + (state.gateResolved === "rejected" ? " is-rejected" : ""));
      var row = el("div", "gate__decided gate__decided--" + (state.gateResolved === "approved" ? "yes" : "no"));
      row.appendChild(svg(state.gateResolved === "approved" ? ICONS.check : ICONS.pause));
      var txt = state.gateResolved === "approved"
        ? "已批准 · " + ev.detail
        : "已拒绝 · " + ev.detail;
      row.appendChild(el("span", null, txt));
      row.appendChild(copyBtn(function () { return ev.detail; }, "命令", "复制命令"));
      decided.appendChild(row);
      wrap.appendChild(decided);
      return wrap;
    }

    var gate = el("div", "gate");
    var head = el("div", "gate__head");
    head.appendChild(svg(ICONS.shield, "gate__ico"));
    // **回放模式下的闸门不可操作**：那条 run 早就结束了，
    // 它的闸门当时决过或超时过。给一个能点的"批准"会让人以为
    // 可以改变历史结果 —— 而它只会静默失败（后端没有这次调用）。
    // 所以这里显式标成"历史记录 · 不可操作"。
    var replaying = state.streamMode === "replay";
    head.appendChild(el("span", "gate__label",
      replaying ? "审批闸门 · 历史记录（已结束，不可操作）" : "审批闸门 · 等待你的决定"));
    head.appendChild(el("span", "gate__risk", ev.risk));
    gate.appendChild(head);

    var bodyEl = el("div", "gate__body");

    var act = el("div", "gate__action copyscope");
    act.appendChild(el("span", "gate__tool", ev.action));
    act.appendChild(el("span", "gate__cmd", ev.detail));
    act.appendChild(copyBtn(function () { return ev.detail; }, "命令", "复制命令"));
    bodyEl.appendChild(act);

    bodyEl.appendChild(el("p", "gate__reason", ev.reason));

    if (replaying) {
      gate.appendChild(bodyEl);
      wrap.appendChild(gate);
      return wrap;
    }

    var acts = el("div", "gate__acts");

    var btnYes = el("button", "btn btn--primary");
    btnYes.type = "button";
    btnYes.appendChild(svg(ICONS.check));
    btnYes.appendChild(el("span", null, "批准"));
    btnYes.addEventListener("click", function () { resolveGate("approved", ev); });

    var btnAlways = el("button", "btn");
    btnAlways.type = "button";
    btnAlways.appendChild(el("span", null, "批准，并在本会话记住"));
    btnAlways.addEventListener("click", function () {
      // live 模式下后端才有"会话记忆"的真状态；本地反映只是让用户看得见
      ev._remember = true;
      var ap = $("[data-approval]");
      if (ap) ap.textContent = "never";
      resolveGate("approved", ev);
    });

    var btnNo = el("button", "btn btn--danger");
    btnNo.type = "button";
    btnNo.appendChild(el("span", null, "拒绝"));
    btnNo.addEventListener("click", function () { resolveGate("rejected", ev); });

    acts.appendChild(btnYes);
    acts.appendChild(btnAlways);
    acts.appendChild(btnNo);
    bodyEl.appendChild(acts);

    var deferred = el("div", "gate__deferred");
    deferred.appendChild(svg(ICONS.clock));
    var b = el("b", null, String(ev.deferred));
    deferred.appendChild(document.createTextNode("后续 "));
    deferred.appendChild(b);
    deferred.appendChild(document.createTextNode(" 个事件已暂缓 · 批准后继续"));
    bodyEl.appendChild(deferred);

    gate.appendChild(bodyEl);
    wrap.appendChild(gate);
    return wrap;
  }

  function resolveGate(decision, ev) {
    // live 模式：把决定真正回灌给后端，而不是只改前端状态。
    if (window.Harness && window.Harness.isLive() && ev && ev._callId) {
      window.Harness.approve(ev._callId, decision, decision === "approved" && !!ev._remember)
        .then(function (ok) {
          if (!ok) {
            UI.toast({
              tone: "warning", title: "后端未接受这次决定",
              desc: "该审批可能已超时或已被自动放行，状态未改变。",
              duration: 5000,
            });
          }
        });
    }
    state.gateResolved = decision;
    cancelAnimationFrame(gateRaf);
    renderStream();

    // 原来这里会改 mock 的 PLAN 步骤状态 —— 已删除：
    // harness 没有计划概念，改一份假计划来"反映"决定是纯粹的表演。
    // 决定本身已经通过 /approve 回灌给后端，真实状态在事件流里。
  }

  var gateRaf = 0;

  function buildCheck(ev) {
    var d = el("div", "ev ev--check");
    var card = el("div", "checkcard");
    // 只要有一项不确定，整块头部就不能用"通过"的语气
    var anyWarn = (ev.items || []).some(function (it) { return it.warn; });
    var allOk = (ev.items || []).length > 0 && (ev.items || []).every(function (it) { return it.ok; });
    if (anyWarn) card.classList.add("checkcard--warn");
    var head = el("div", "checkcard__head");
    // 三种状态三种图标：通过=对勾，失败=警告，不确定=问号。
    // 以前只分"对勾/警告"两种，"无法自动判定"会被画成"失败"，
    // 那是把不确定性说成了确定的坏消息。
    head.appendChild(svg(anyWarn ? ICONS.warning : ICONS.check));
    head.appendChild(el("span", "checkcard__title", ev.title));
    head.appendChild(el("span", "checkcard__model", ev.model));
    card.appendChild(head);

    var ul = el("ul", "checklist");
    (ev.items || []).forEach(function (it) {
      var li = el("li");
      li.appendChild(svg(it.ok ? ICONS.check : (it.warn ? ICONS.help : ICONS.warning)));
      if (it.warn) li.classList.add("checklist__item--warn");
      li.appendChild(el("span", null, it.text));
      ul.appendChild(li);
    });
    card.appendChild(ul);
    d.appendChild(card);
    return d;
  }

  /* ============================================================
     五、输入坞
     ============================================================ */
  function renderCtxRefs() {
    var box = $("#dock-ctx");
    box.innerHTML = "";
    if (!state.ctxRefs.length) { box.hidden = true; return; }
    box.hidden = false;
    state.ctxRefs.forEach(function (r, i) {
      var chip = el("span", "ctxref");
      chip.appendChild(document.createTextNode("@" + r));
      var b = el("button");
      b.type = "button";
      b.setAttribute("aria-label", "移除上下文 " + r);
      b.appendChild(svg(ICONS.x));
      b.addEventListener("click", function () {
        state.ctxRefs.splice(i, 1);
        renderCtxRefs();
      });
      chip.appendChild(b);
      box.appendChild(chip);
    });
  }

  /* 上下文浮层 */
  var popOpen = false;
  function buildCtxPop() {
    var pop = $("#ctxpop");
    pop.innerHTML = "";
    var lbl = el("span", "ctxpop__label", "加入上下文");
    pop.appendChild(lbl);

    // 候选文件来自**真实文件树缓存**（`allFiles()`）。
    // 原来递归 mock 的 FILE_TREE —— 候选里全是工作区里不存在的路径，
    // 选了之后 `@` 引用的是一个幽灵文件。
    var files = allFiles().filter(function (p) {
      // 排除明显的非源码文件（md/json 噪音太多，用户很少要引用它们）
      return !/\.(md|json)$/.test(p);
    });
    if (!files.length) {
      var tip = el("div", "mention__tip",
        Backend.available()
          ? "还没有列出任何文件。先在左侧资源管理器展开目录，再回来添加引用。"
          : "后端未连接，无法列出工作区文件。");
      pop.appendChild(tip);
      pop.hidden = false;
      return;
    }

    files.slice(0, 6).forEach(function (f) {
      var it = el("button", "ctxpop__item");
      it.type = "button";
      it.setAttribute("role", "menuitem");
      it.appendChild(svg(ICONS.file));
      it.appendChild(el("span", "ctxpop__name", f));
      it.addEventListener("click", function () {
        if (state.ctxRefs.indexOf(f) === -1) state.ctxRefs.push(f);
        renderCtxRefs();
        closePop();
      });
      pop.appendChild(it);
    });

    var div = el("div");
    div.style.cssText = "height:1px;background:var(--border);margin:6px 4px";
    pop.appendChild(div);

    var auto = el("button", "ctxpop__item");
    auto.type = "button";
    auto.setAttribute("role", "menuitemcheckbox");
    auto.setAttribute("aria-checked", "false");
    auto.appendChild(svg(ICONS.plus));
    auto.appendChild(el("span", "ctxpop__name", "自动纳入已打开的文件"));
    auto.appendChild(el("span", "ctxpop__switch", "关"));
    var on = false;
    auto.addEventListener("click", function () {
      on = !on;
      auto.setAttribute("aria-checked", on ? "true" : "false");
      auto.lastChild.textContent = on ? "开" : "关";
    });
    pop.appendChild(auto);
  }

  function openPop(anchor) {
    var pop = $("#ctxpop");
    buildCtxPop();
    pop.hidden = false;
    var r = anchor.getBoundingClientRect();
    var top = Math.max(8, r.top - pop.offsetHeight - 8);
    pop.style.left = Math.min(r.left, window.innerWidth - pop.offsetWidth - 10) + "px";
    pop.style.top = top + "px";
    popOpen = true;
    $("#btn-dock-ctx").setAttribute("aria-expanded", "true");
  }
  function closePop() {
    $("#ctxpop").hidden = true;
    popOpen = false;
    var b = $("#btn-dock-ctx");
    if (b) b.setAttribute("aria-expanded", "false");
  }

  /* 发送：往事件流里追加用户消息 + 一次上下文装配
     —— 若已连上 harness 后端，这里改走真实运行（见 harness-bridge.js）。 */
  function sendPrompt(text) {
    if (!text.trim()) return;
    var stream = D.stream;

    var u = el("div", "ev ev--msg");
    u.style.paddingLeft = "0";
    var ub = el("div");
    ub.style.cssText = "padding:8px 12px;background:var(--brass-a12);border-left:2px solid var(--brass);border-radius:0 6px 6px 0;font-size:12px;color:var(--text);text-wrap:pretty";
    ub.textContent = text;
    u.appendChild(ub);
    stream.appendChild(u);
    stream.scrollTop = stream.scrollHeight;
    applyFilter();
    UI.announce("已发送指令");

    if (window.Harness && window.Harness.isLive()) {
      return sendToHarness(text);
    }

    var think = el("div", "ev ev--reasoning");
    var th = el("div", "ev__head");
    var sp = el("span", "ev__spin");
    sp.setAttribute("aria-hidden", "true");
    th.appendChild(sp);
    th.appendChild(el("span", "ev__kind", "正在装配上下文"));
    think.appendChild(th);
    think.appendChild(el("p", "ev__text", "读取 AGENTS.md、当前分支改动与已打开的文件…"));
    stream.appendChild(think);
    stream.scrollTop = stream.scrollHeight;
    if (refreshStreamFade) refreshStreamFade();
    applyFilter();

    // 演示：1.6s 后替换为「思考完成」
    setTimeout(function () {
      think.innerHTML = "";
      var h2 = el("div", "ev__head");
      h2.appendChild(el("span", "ev__kind", "推理"));
      h2.appendChild(el("span", "ev__dur", "耗时 0.9s"));
      think.appendChild(h2);
      think.appendChild(el("p", "ev__text", "已装配 " + (state.ctxRefs.length ? state.ctxRefs.length + " 个显式引用 + " : "") + "当前分支的 4 处改动。这只是前端演示；接上真实 harness 后，这里会流入模型的实际事件流。"));
      stream.scrollTop = stream.scrollHeight;
      if (refreshStreamFade) refreshStreamFade();
      applyFilter();
    }, 1600);
  }

  /* ---------- 接到 harness：真实运行 ---------- */
  function sendToHarness(text) {
    var pending = el("div", "ev ev--reasoning");
    var ph = el("div", "ev__head");
    var psp = el("span", "ev__spin");
    psp.setAttribute("aria-hidden", "true");
    ph.appendChild(psp);
    ph.appendChild(el("span", "ev__kind", "连接 harness"));
    pending.appendChild(ph);
    pending.appendChild(el("p", "ev__text", "正在发起运行…"));
    D.stream.appendChild(pending);
    D.stream.scrollTop = D.stream.scrollHeight;

    window.Harness.run(text, {
      sandbox: $("[data-sandbox]") ? $("[data-sandbox]").textContent : "workspace-write",
      approval: $("[data-approval]") ? $("[data-approval]").textContent : "on-request",
      workspace: state.workspace,          // null 时由服务端默认工作区决定
      offline_script: parseOfflineParam(urlParams.offline) || persisted.offlineScript,
    }).then(function (info) {
      pending.remove();
      // 这一轮跑起来了 → 徽标要反映"现在是第几轮"
      if (window.refreshThreadBadge) window.refreshThreadBadge();
      UI.toast({
        tone: "info", title: "已连接 harness",
        desc: "run_id " + info.run_id + " · 前缀哈希 " + (info.prefix_hash || "—"),
        duration: 4000,
      });
    }).catch(function (err) {
      pending.remove();
      var bad = el("div", "ev ev--check");
      var c = el("div", "checkcard");
      var hh = el("div", "checkcard__head");
      hh.appendChild(svg(ICONS.warning));
      hh.appendChild(el("span", "checkcard__title", "harness 拒绝这次运行"));
      hh.appendChild(el("span", "checkcard__model", "server"));
      c.appendChild(hh);
      var ul = el("ul", "checklist");
      var li = el("li");
      li.appendChild(svg(ICONS.warning));
      li.appendChild(el("span", null, String((err && err.message) || err)));
      ul.appendChild(li);
      c.appendChild(ul);
      bad.appendChild(c);
      D.stream.appendChild(bad);
      UI.toast({
        tone: "danger", title: "运行未启动",
        desc: String((err && err.message) || err), duration: 6000,
      });
    });
  }

  /* ============================================================
     @ 文件引用 / 斜杠命令 补全
     ============================================================ */
  var mentionItems = [], mentionIdx = 0, mentionMode = null, mentionQuery = "";

  /* `@` 补全的候选文件 —— 来自**已加载的真实文件树缓存**。
     原来递归 mock 的 FILE_TREE，于是补全出来的路径在真实工作区里都不存在。
     现在只收集已经列出来过的目录（用户在树里展开过的那部分）：
     这是有意的 —— 全量扫描工作区会在大仓库上卡住输入体验，
     而"补全你正在看的目录"本来就是更贴合直觉的行为。
     缓存里没有的目录不会出现在候选里，但补全框会提示这一点。 */
  function allFiles() {
    var out = [];
    Object.keys(state.treeCache || {}).forEach(function (dir) {
      var node = state.treeCache[dir];
      if (!node || !node.entries) return;
      node.entries.forEach(function (en) {
        if (en.kind === "file") out.push(en.path);
      });
    });
    // 去重 + 排序（同名字段来自不同目录时顺序稳定，便于键盘选择）
    out.sort();
    return out.filter(function (p, i) { return i === 0 || p !== out[i - 1]; });
  }

  function showMention(mode, query, caret) {
    mentionMode = mode;
    mentionQuery = query;
    var box = D.mine;
    box.innerHTML = "";

    // 文件池为空时给出**原因**，而不是空框 ——
    // 空框会被读成"这个工作区没有文件"。
    if (mode !== "cmd" && !allFiles().length) {
      var tip = el("div", "mention__tip",
        Backend.available()
          ? "还没有列出任何文件。先在左侧资源管理器展开一个目录，再回来用 @ 引用。"
          : "后端未连接，无法列出工作区文件。");
      box.appendChild(tip);
      box.hidden = false;
      return;
    }

    var items;
    if (mode === "slash") {
      box.appendChild(el("span", "mention__label", "命令"));
      items = SLASH_COMMANDS.filter(function (c) {
        return !query || c.cmd.slice(1).toLowerCase().indexOf(query.toLowerCase()) === 0;
      }).map(function (c) {
        return { key: c.cmd, desc: c.desc, run: function () { insertMention(c.cmd + " "); } };
      });
    } else {
      var files = allFiles();
      var scored = files.map(function (f) {
        var base = f.split("/").pop();
        var s = -1;
        if (!query) s = 1;
        else if (base.toLowerCase().indexOf(query.toLowerCase()) === 0) s = 100;
        else if (base.toLowerCase().indexOf(query.toLowerCase()) > -1) s = 60;
        else if (f.toLowerCase().indexOf(query.toLowerCase()) > -1) s = 20;
        return { f: f, s: s };
      }).filter(function (o) { return o.s > 0; }).sort(function (a, b) { return b.s - a.s; });

      box.appendChild(el("span", "mention__label", query ? "匹配的文件" : "工作区文件"));
      items = scored.slice(0, 20).map(function (o) {
        return { key: o.f, desc: "", run: function () { insertMention("@" + o.f + " "); } };
      });
    }

    if (!items.length) { hideMention(); return; }
    mentionItems = items;
    mentionIdx = 0;

    items.forEach(function (it, i) {
      var b = el("button", "mention__item" + (i === 0 ? " is-on" : ""));
      b.type = "button";
      b.setAttribute("role", "option");
      b.setAttribute("aria-selected", i === 0 ? "true" : "false");
      b.appendChild(svg(mode === "slash" ? ICONS.terminal : ICONS.file));
      b.appendChild(el("span", "mention__name", it.key));
      if (it.desc) b.appendChild(el("span", "mention__desc", it.desc));
      b.addEventListener("mousemove", function () { setMentionIdx(i); });
      b.addEventListener("mousedown", function (e) { e.preventDefault(); it.run(); });
      box.appendChild(b);
    });

    // 定位到输入框上方
    var wrap = $(".dock__wrap");
    var r = wrap.getBoundingClientRect();
    box.hidden = false;
    var top = r.top - box.offsetHeight - 6;
    if (top < 6) top = r.bottom + 6;
    box.style.left = Math.max(6, Math.min(r.left, window.innerWidth - box.offsetWidth - 10)) + "px";
    box.style.top = top + "px";
  }

  function setMentionIdx(i) {
    var btns = $$(".mention__item", D.mine);
    if (!btns.length) return;
    if (btns[mentionIdx]) {
      btns[mentionIdx].classList.remove("is-on");
      btns[mentionIdx].setAttribute("aria-selected", "false");
    }
    mentionIdx = (i + btns.length) % btns.length;
    btns[mentionIdx].classList.add("is-on");
    btns[mentionIdx].setAttribute("aria-selected", "true");
    btns[mentionIdx].scrollIntoView({ block: "nearest" });
  }

  function hideMention() {
    D.mine.hidden = true;
    mentionMode = null;
    mentionItems = [];
  }

  function insertMention(text) {
    var ta = $("#dock-input");
    var caret = ta.selectionStart;
    var v = ta.value;
    // 回退掉触发符（@ 或 /）以及已输入的查询串
    var back = v.slice(0, caret);
    var trigger = mentionMode === "slash" ? back.lastIndexOf("/") : back.lastIndexOf("@");
    if (trigger < 0) trigger = caret;
    ta.value = v.slice(0, trigger) + text + v.slice(caret);
    var pos = trigger + text.length;
    ta.setSelectionRange(pos, pos);
    ta.focus();
    hideMention();
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  }

  /* 根据输入内容决定要不要弹补全 */
  function syncMention() {
    var ta = $("#dock-input");
    var caret = ta.selectionStart;
    var back = ta.value.slice(0, caret);

    // 只在行首或空白后紧跟 / 时，才认作斜杠命令
    var slash = /(^|\s)\/([\w-]*)$/.exec(back);
    if (slash) { showMention("slash", slash[2], caret); return; }

    // @ 引用：允许路径里带 / 和 . 和 -
    var at = /(^|\s)@([\w./-]*)$/.exec(back);
    if (at) { showMention("file", at[2], caret); return; }

    hideMention();
  }

  /* ============================================================
     六、事件绑定
     ============================================================ */
  function bind() {
    // ---------- 活动栏切换 ----------
    /* ## 点活动栏图标的行为（曾经是坏的）
       原来的判定只有一句「窄屏才 openRail，且当前已选中就跳过」：
         · 宽屏下点图标**永远不展开侧栏** —— 一旦侧栏被折叠，
           唯一的"展开"控件（侧栏头部那个 ‹）就跟着侧栏一起变成 0 宽，
           **用户再也回不去**。实测：折叠后点遍所有图标，rail 宽度始终 1px。
         · 窄屏下点**当前已选中的图标**什么也不发生（already=true 直接跳过），
           于是"关了抽屉之后点同一个图标打不开"。
       现在按 VS Code 的语义：点当前视图 = 开/关，点其它视图 = 切换并展开。 */
    $$(".actbar__btn[data-view]").forEach(function (b) {
      b.addEventListener("click", function () {
        var v = b.dataset.view;
        var sameView = state.railView === v;
        setActiveViewButton(v);
        state.railView = v;
        persisted.activeView = v;
        save();
        state.focusIdx = 0;
        renderRail();

        if (state.railDrawer) {
          // 窄屏：侧栏是抽屉。点当前视图 → 开/关；点别的 → 切换并保持打开
          if (sameView) toggleRailDrawer();
          else openRail();
        } else if (sameView) {
          // 宽屏：点当前视图 = 折叠/展开（这是折叠后唯一能恢复的入口）
          toggleRailCollapsed();
          return;
        } else {
          // 宽屏：切到别的视图时，如果侧栏是折叠的，顺手展开 ——
          // 点了图标却什么都没出现是最容易让人以为"功能坏了"的形态
          expandRail();
        }
        UI.announce((sameView ? "" : (RAIL_TITLES[v] || v))
          + (state.railDrawer
              ? (D.shell.classList.contains("is-rail-open") ? " · 侧栏已打开" : " · 侧栏已收起")
              : (isRailCollapsed() ? " · 侧栏已折叠" : "")));
      });
    });

    // ---------- 侧栏折叠（记忆状态） ----------
    $("#btn-collapse-rail").addEventListener("click", function () {
      toggleRailCollapsed();
    });

    // ---------- 侧栏宽度拖拽 ----------
    (function initRailResize() {
      var h = $("#rail-resize");
      if (!h) return;
      // 指针按下即拖，不需要先点准 1px 的边
      h.addEventListener("pointerdown", function (e) {
        if (state.railDrawer) return;
        e.preventDefault();
        h.classList.add("is-active");
        document.body.classList.add("is-col-resizing");
        var startW = D.rail.getBoundingClientRect().width;
        try { h.setPointerCapture(e.pointerId); } catch (err) { /* 忽略 */ }

        function mv(ev) {
          var w = Math.max(180, Math.min(520, startW + (ev.clientX - e.clientX)));
          document.documentElement.style.setProperty("--rail-w", w + "px");
        }
        function up() {
          h.classList.remove("is-active");
          document.body.classList.remove("is-col-resizing");
          h.removeEventListener("pointermove", mv);
          h.removeEventListener("pointerup", up);
          h.removeEventListener("pointercancel", up);
          persisted.railW = Math.round(D.rail.getBoundingClientRect().width);
          save();
        }
        h.addEventListener("pointermove", mv);
        h.addEventListener("pointerup", up);
        h.addEventListener("pointercancel", up);
      });
      // 双击复位
      h.addEventListener("dblclick", function () {
        document.documentElement.style.removeProperty("--rail-w");
        persisted.railW = null;
        save();
        UI.toast({ tone: "info", title: "侧栏宽度已复位", duration: 2200 });
      });
    })();

    // ---------- 侧栏「更多」 ----------
    $("#btn-rail-more").addEventListener("click", function (e) {
      e.stopPropagation();
      var r = this.getBoundingClientRect();
      this.setAttribute("aria-expanded", "true");
      var view = state.railView;
      var items = view === "explorer" ? [
        { label: "全部展开", icon: ICONS.plus, onClick: function () { expandAll(true); } },
        { label: "全部折叠", icon: ICONS.chevron, onClick: function () { expandAll(false); } },
        { sep: true },
        { label: "刷新索引", icon: ICONS.play, onClick: refreshIndex },
        { label: "在路径中查找…", icon: ICONS.search, hint: "Ctrl+Shift+F", onClick: function () { goView("search"); } },
      ] : view === "scm" ? [
        // 这两个动作在真实后端**没有对应能力**，所以不提供按钮：
        //   · "暂存"    —— harness 不管 git 暂存区
        //   · "丢弃全部" —— 变更集只支持"回滚到某一步之前"，没有全丢
        // 放一个点了没用的按钮比不放更坏：用户会以为操作生效了。
        { label: "怎么回滚", icon: ICONS.book, onClick: function () {
            UI.toast({
              tone: "info", duration: 8000,
              title: "回滚入口在「会话历史」",
              desc: "选中一条运行 → 展开明细 → 「变更集 · 可回滚」里逐条回滚到某一步之前。"
                + "\nharness 不支持「丢弃全部改动」，也不管 git 暂存区。",
            });
          } },
      ] : [
        { label: "刷新", icon: ICONS.play, onClick: refreshIndex },
      ];
      UI.contextMenu(r.left - 120, r.bottom + 4, items);
      var self = this;
      setTimeout(function () { self.setAttribute("aria-expanded", "false"); }, 400);
    });

    // ---------- 账户菜单 ----------
    $("#btn-acct").addEventListener("click", function (e) {
      e.stopPropagation();
      var r = this.getBoundingClientRect();
      UI.contextMenu(r.left - 140, r.top - 130, [
        { label: "模型接入", icon: ICONS.plug, onClick: openModelSettings },
        { label: "键盘快捷键", icon: ICONS.terminal, hint: "?", onClick: openShortcuts },
        { label: "切换主题", icon: ICONS.edit, onClick: toggleTheme },
        { sep: true },
        { label: "退出登录", icon: ICONS.x, danger: true, onClick: function () {
            UI.confirm({ title: "退出登录？", desc: "当前会话的上下文会保留在本地，下次打开自动恢复。", confirmLabel: "退出", cancelLabel: "取消", tone: "danger" })
              .then(function (ok) {
                if (ok) UI.toast({ tone: "info", title: "已退出登录", desc: "演示环境，未真正退出。", duration: 3000 });
              });
          } },
      ]);
    });

    // ---------- 设置（活动栏底部）----------
    var btnSettings = $("#btn-settings");
    if (btnSettings) {
      // 设置是**一页**，不是只有模型接入。默认落在哪个分区按状态决定：
      // 还没配模型 → 直接进"模型接入"（这是最可能的意图）；已配好 → 外观。
      btnSettings.addEventListener("click", function () {
        openSettings(state.endpoint && state.endpoint.configured ? "appearance" : "model");
      });
      // 右键给一个"清除配置"的口子 —— 不放在主表单里，避免误触，
      // 但必须存在：否则用户想换 key / 换端点时只能手动删文件。
      btnSettings.addEventListener("contextmenu", function (e) {
        e.preventDefault();
        var r = this.getBoundingClientRect();
        UI.contextMenu(r.right + 6, r.bottom - 120, [
          { label: "模型接入…", icon: ICONS.plug, onClick: openModelSettings },
          { sep: true },
          { label: "清除模型配置", icon: ICONS.trash, danger: true, onClick: function () {
              UI.confirm({
                title: "清除模型配置？",
                desc: "会删除服务端保存的接口地址、API Key 与模型名称。清除后运行将回到离线脚本模式。",
                confirmLabel: "清除", tone: "danger",
              }).then(function (ok) {
                if (!ok) return;
                window.Harness.clearConfig(state.workspace).then(function (r2) {
                  applyEndpoint(r2 && r2.endpoint);
                  UI.toast({ tone: "info", title: "已清除模型配置",
                             desc: r2 && r2.removed ? "配置已删除。" : "本来就没有配置文件。",
                             duration: 4000 });
                });
              });
            } },
        ]);
      });
    }

    // ---------- 主题 ----------
    function toggleTheme() {
      var cur = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
      persisted.theme = cur;
      save();
      applyTheme(cur);
      UI.announce(cur === "light" ? "已切到浅色主题" : "已切到深色主题");
    }
    $("#btn-theme").addEventListener("click", toggleTheme);

    // ---------- 命令面板 ----------
    function openPalette() {
      var cmds = [];

      cmds.push({ label: "切换明暗主题", sub: "当前：" + (document.documentElement.getAttribute("data-theme") === "light" ? "浅色" : "深色"),
        group: "外观", icon: ICONS.edit, onRun: toggleTheme });
      // 设置是一个**页面**（外观 / 模型接入 / 工作区），所以给它一条独立命令 ——
      // 只靠活动栏底部那个图标，用户得先猜到它是设置。
      cmds.push({ label: "打开设置…", sub: "外观 · 模型接入 · 工作区",
        group: "外观", icon: ICONS.plug,
        onRun: function () {
          openSettings(state.endpoint && state.endpoint.configured ? "appearance" : "model");
        } });
      // 换工作区是一个高频动作（开新项目就要做一次），
      // 所以给它一条**顶层**命令，而不是让人先进设置再找。
      cmds.push({ label: "打开文件夹…", sub: "切换工作区（当前：" + workspaceName(
          state.workspace || (state.wsInfo && state.wsInfo.workspace) || "服务端默认") + "）",
        group: "工作区", icon: ICONS.dirOpen,
        onRun: function () { openFolderPicker(); } });

      cmds.push({ label: "键盘快捷键总览", sub: "查看全部快捷键", group: "帮助", icon: ICONS.terminal, keys: ["?"], onRun: openShortcuts });

      cmds.push({ label: "显示 / 隐藏 Agent 面板", group: "视图", icon: ICONS.play, keys: ["Ctrl", "B"],
        onRun: function () { setAgentOpen(!agentIsOpen()); } });
      cmds.push({ label: "显示 / 隐藏终端", group: "视图", icon: ICONS.terminal, keys: ["Ctrl", "`"],
        onRun: function () { toggleTerminal(); } });
      cmds.push({ label: "折叠 / 展开侧栏", group: "视图", icon: ICONS.dirClosed,
        onRun: function () { toggleRailCollapsed(); } });

      ["explorer", "search", "scm", "sessions", "mcp"].forEach(function (v) {
        cmds.push({ label: "打开" + RAIL_TITLES[v], group: "侧栏", icon: ICONS.file,
          onRun: function () { goView(v); } });
      });

      // 打开的文件
      state.tabs.forEach(function (t) {
        cmds.push({ label: "打开 " + t.path.split("/").pop(), sub: t.path, group: "已打开的文件", icon: ICONS.file,
          onRun: function () { openTab(t.path); } });
      });

      // 工作区文件
      allFiles().forEach(function (f) {
        cmds.push({ label: f.split("/").pop(), sub: f, group: "工作区文件", icon: ICONS.file,
          onRun: function () { openTab(f); } });
      });

      // 会话（真实运行记录）
      (state.runs || []).slice(0, 8).forEach(function (r) {
        cmds.push({
          label: r.model || r.id, sub: r.id + (r.tsMin == null ? "" : " · " + UI.fmtRel(r.tsMin)),
          group: "运行记录", icon: ICONS.clock,
          onRun: function () {
            goView("sessions");
            // 直接回放这条 —— 比"切过去然后自己找"少两步
            Backend.replayRun(r.id, r.workspace, { model: r.model });
            UI.toast({ tone: "info", title: "正在回放 " + r.id, duration: 3000 });
          },
        });
      });

      // 斜杠命令
      SLASH_COMMANDS.forEach(function (c) {
        cmds.push({
          label: c.cmd, sub: c.desc, group: "界面命令", icon: ICONS.terminal,
          // **真正执行**，而不是把文本插进输入框。
          // 插文本的旧行为会让用户以为 `/status` 是发给模型的一条指令 ——
          // 而模型看它只是一段普通文字，什么都不会发生。
          onRun: function () { runSlashCommand(c.cmd); },
        });
      });

      // 动作
      cmds.push({ label: "刷新代码库索引", group: "操作", icon: ICONS.play, onRun: refreshIndex });
      cmds.push({ label: "保存当前文件", group: "操作", icon: ICONS.check, keys: ["Ctrl", "S"],
        onRun: function () { if (state.activeTab) markSaved(state.activeTab); } });
      cmds.push({ label: "复制当前文件路径", group: "操作", icon: ICONS.file,
        onRun: function () { if (state.activeTab) UI.copy(state.activeTab, "路径"); else UI.toast({ tone: "warn", title: "没有打开的文件", duration: 2600 }); } });
      cmds.push({ label: "关闭所有标签", group: "操作", icon: ICONS.x,
        onRun: function () {
          var dirty = state.tabs.filter(function (t) { return t.dirty; });
          if (dirty.length) {
            UI.confirm({ title: "有文件未保存", desc: "有 " + dirty.length + " 个文件带未保存改动。继续会丢弃它们。",
              confirmLabel: "全部关闭", cancelLabel: "取消", tone: "danger",
              detail: dirty.map(function (t) { return t.path; }).join("\n") })
              .then(function (ok) {
                if (!ok) return;
                state.tabs = [];
                state.activeTab = null;
                renderTabs();
                renderEditor();
                UI.toast({ tone: "info", title: "已关闭全部标签", duration: 2600 });
              });
          } else {
            state.tabs = [];
            state.activeTab = null;
            renderTabs();
            renderEditor();
            UI.toast({ tone: "info", title: "已关闭全部标签", duration: 2600 });
          }
        } });

      UI.commandPalette(cmds);
    }
    $("#btn-cmdk").addEventListener("click", openPalette);

    // ---------- 快捷键总览 ----------
    function openShortcuts() {
      var back = el("div", "paletteback");
      var sheet = el("div", "sheet");
      sheet.setAttribute("role", "dialog");
      sheet.setAttribute("aria-modal", "true");
      sheet.setAttribute("aria-label", "键盘快捷键");

      var head = el("div", "sheet__head");
      var h2 = el("h2", "sheet__title", "键盘快捷键");
      head.appendChild(h2);
      var x = el("button", "iconbtn iconbtn--sm");
      x.type = "button";
      x.setAttribute("aria-label", "关闭");
      x.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="M6 6l8 8M14 6l-8 8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>';
      head.appendChild(x);
      sheet.appendChild(head);

      var bd = el("div", "sheet__body");
      SHORTCUTS.forEach(function (g) {
        var sec = el("div", "sheet__group");
        sec.appendChild(el("h3", null, g.group));
        g.items.forEach(function (it) {
          var row = el("div", "sheet__row");
          row.appendChild(el("span", null, it.desc));
          var ks = el("span", "sheet__keys");
          it.keys.forEach(function (k) { ks.appendChild(el("kbd", null, k)); });
          row.appendChild(ks);
          sec.appendChild(row);
        });
        bd.appendChild(sec);
      });
      sheet.appendChild(bd);
      back.appendChild(sheet);
      document.body.appendChild(back);
      UI.lockScroll(true);

      var rel = UI.trapFocus(sheet);
      x.focus();
      function close() {
        rel();
        UI.lockScroll(false);
        if (back.parentNode) back.parentNode.removeChild(back);
        document.removeEventListener("keydown", onKey);
      }
      function onKey(e) {
        if (e.key === "Escape") { e.preventDefault(); close(); }
      }
      x.addEventListener("click", close);
      back.addEventListener("mousedown", function (e) { if (e.target === back) close(); });
      document.addEventListener("keydown", onKey);
    }
    $("#btn-help").addEventListener("click", openShortcuts);

    // ---------- 对话模式 ----------
    $$(".segctl__btn").forEach(function (b) {
      b.addEventListener("click", function () {
        $$(".segctl__btn").forEach(function (x) {
          x.classList.remove("is-on");
          x.setAttribute("aria-pressed", "false");
        });
        b.classList.add("is-on");
        b.setAttribute("aria-pressed", "true");
        var m = b.dataset.mode;
        var sb = $("[data-sandbox]");
        if (m === "full") {
          sb.textContent = "danger-full-access";
          $(".statusitem__dot--sandbox").style.background = "var(--vermilion)";
          UI.announce("已切到全权模式：无沙箱、无审批");
          UI.toast({ tone: "warn", title: "已切到全权模式", desc: "网络与工作区外访问不再询问。仅建议在一次性容器里使用。", duration: 6000 });
        } else if (m === "chat") {
          sb.textContent = "read-only";
          $(".statusitem__dot--sandbox").style.background = "var(--slate-blue)";
          UI.announce("已切到对话模式：只读");
        } else {
          sb.textContent = "workspace-write";
          $(".statusitem__dot--sandbox").style.background = "var(--amber)";
          UI.announce("已切到 Agent 模式：工作区内可写");
        }
      });
    });

    // ---------- 状态栏：沙箱 / 审批 / 模型 / 索引 / 诊断 ----------
    var SBX = ["read-only", "workspace-write", "danger-full-access"];
    $("#btn-sandbox").addEventListener("click", function () {
      var cur = $("[data-sandbox]").textContent;
      var nxt = SBX[(SBX.indexOf(cur) + 1) % SBX.length];
      $("[data-sandbox]").textContent = nxt;
      $(".statusitem__dot--sandbox").style.background =
        nxt === "read-only" ? "var(--slate-blue)" : nxt === "workspace-write" ? "var(--amber)" : "var(--vermilion)";
      UI.announce("沙箱模式：" + nxt);
      if (nxt === "danger-full-access") {
        UI.toast({ tone: "warn", title: "沙箱已完全打开", desc: "agent 将不再受工作区约束，也不再请求审批。", duration: 6000 });
      }
    });
    var AP = ["untrusted", "on-request", "never"];
    $("#btn-approval").addEventListener("click", function () {
      var cur = $("[data-approval]").textContent;
      var nxt = AP[(AP.indexOf(cur) + 1) % AP.length];
      $("[data-approval]").textContent = nxt;
      UI.announce("审批策略：" + nxt);
      UI.toast({ tone: "info", title: "审批策略：" + nxt, desc: APR_DESC[nxt], duration: 4000 });
    });
    var APR_DESC = {
      untrusted: "每条有后果的命令都要你点头。",
      "on-request": "沙箱内自动放行，越界才问你。",
      never: "全自动，不再打断你。",
    };
    /* 状态栏的模型按钮。原来这里弹的是一个**编造的模型清单**
       ——列出的模型页面上根本不存在，点任何一个只会弹一条 toast。
       现在它指向真正的设置页；"查看 token 消耗"仍然保留（那是真实数据）。 */
    $("#btn-model").addEventListener("click", function (e) {
      var r = this.getBoundingClientRect();
      UI.contextMenu(r.left, r.top - 88, [
        { label: state.endpoint && state.endpoint.configured
            ? ("当前：" + state.endpoint.model)
            : "尚未接入模型",
          icon: ICONS.plug, disabled: true },
        { sep: true },
        { label: "模型接入设置…", icon: ICONS.plug, onClick: function () { openSettings("model"); } },
        { label: "查看 token 消耗", icon: ICONS.clock, onClick: showTokenDetail },
      ]);
    });
    var agentModelBtn = $("#btn-agent-model");
    if (agentModelBtn) {
      agentModelBtn.addEventListener("click", function () { openSettings("model"); });
    }
    $("#btn-index").addEventListener("click", function (e) {
      var r = this.getBoundingClientRect();
      UI.contextMenu(r.left, r.top - 84, [
        { label: "重新索引工作区", icon: ICONS.play, onClick: refreshIndex },
        { label: "工作区详情", icon: ICONS.book, onClick: function () {
            var i = state.wsInfo;
            if (!i) {
              UI.toast({ tone: "warning", title: "还没读到工作区信息",
                         desc: state.wsInfoError || "正在读取或后端未连接。", duration: 5000 });
              return;
            }
            UI.toast({
              tone: "info", duration: 8000,
              title: i.files + " 个文件 · " + i.dirs + " 个目录",
              desc: i.workspace + "\n" + i.index_note,
            });
          } },
      ]);
    });
    /* 状态栏"改动"入口 —— 从**真实 git status** 读，不是诊断。
       原来这里列的是编造的 lint 警告（"reconnect.ts:22 未使用的常量"），
       而那个文件根本不存在。harness 不做 lint/类型检查，
       但**"工作区改了多少东西"是真实可得的**，而且对 agent 类界面更有用。 */
    $("#btn-diag").addEventListener("click", function (e) {
      var r = this.getBoundingClientRect();
      var items = [];
      var g = state.gitStatus;
      if (!Backend.available()) {
        items.push({ label: "后端未连接", icon: ICONS.help, disabled: true });
      } else if (state.gitError) {
        items.push({ label: "读取失败：" + state.gitError, icon: ICONS.warning, disabled: true });
      } else if (!g) {
        items.push({ label: "正在读取工作区改动…", icon: ICONS.help, disabled: true });
        Backend.loadGitStatus();
      } else if (!g.is_repo) {
        items.push({ label: "不在 git 仓库中", icon: ICONS.help, disabled: true });
        items.push({ label: "因此没有改动统计（这不等于没有改动）", icon: ICONS.help, disabled: true });
      } else {
        items.push({ label: g.changed + " 个文件有改动", icon: ICONS.edit, disabled: true });
        if (g.staged) items.push({ label: "已暂存 " + g.staged + " 个", icon: ICONS.check, disabled: true });
        if (g.unstaged) items.push({ label: "未暂存 " + g.unstaged + " 个", icon: ICONS.warning, disabled: true });
        if (g.untracked) items.push({ label: "未跟踪 " + g.untracked + " 个", icon: ICONS.plus, disabled: true });
        items.push({ label: "+" + g.added_lines + " / −" + g.deleted_lines + " 行", icon: ICONS.edit, disabled: true });
        items.push({ sep: true });
        (g.paths || []).slice(0, 8).forEach(function (pth) {
          items.push({
            label: pth, icon: ICONS.file,
            onClick: function () { if (/\.[a-z0-9]+$/i.test(pth)) openTab(pth); },
          });
        });
        if ((g.paths || []).length > 8) {
          items.push({ label: "还有 " + (g.paths.length - 8) + " 个…", disabled: true });
        }
        items.push({ sep: true });
        items.push({ label: "刷新", icon: ICONS.clock, onClick: function () { Backend.loadGitStatus(); } });
      }
      items.push({ label: "harness 不做 lint/类型检查（无诊断来源）", icon: ICONS.help, disabled: true });
      UI.contextMenu(r.left - 120, r.top - 200, items);
    });
    $("#btn-meter").addEventListener("click", showTokenDetail);

    function showTokenDetail() {
      var u = state.usage;
      if (!state.usageSource) {
        UI.toast({
          tone: "info",
          title: "还没有用量数据",
          desc: "发一次真实运行、或在「会话历史」里回放一条记录后，这里会显示该次运行的 token 用量。",
          duration: 6000,
        });
        return;
      }
      if (!u || !u.available) {
        // **不编数字**：没有记录就说没有记录。
        // 显示一个编出来的 "38.2k / 200k" 会让人以为真花了那些钱。
        UI.toast({
          tone: "warning",
          title: "这次运行没有用量记录",
          desc: (u && u.note) || "端点未返回 usage。这不等于「用量为零」。",
          duration: 6000,
        });
        return;
      }
      UI.toast({
        tone: "info",
        title: "上次运行用量 " + u.fmt + " token",
        desc: u.parts + " · 来源：" + (state.usageSource === "live" ? "实时运行" : "历史回放")
          + "\n这是单次运行的计量，不是上下文窗口占用。",
        duration: 8000,
      });
    }

    /* 把用量反映到输入坞的进度条上。
       ## 分母问题（这里曾经是编的）
       mock 时显示 "38.2k / 200k"，那个 200k 是假的上限。
       现在**只用真实数字**：
         · 有用量 → 显示总量，进度条按"相对该次运行的输入量"画满，
           并在 title 里说清它不是窗口占用
         · 没用量 → 显示"未记录"，进度条归零
       编一个上限比不显示更坏：用户会据此判断"还能不能再加东西"。 */
    function updateMeter() {
      var bar = $("#btn-meter");
      if (!bar) return;
      var fill = bar.querySelector(".dock__meterfill");
      var label = bar.querySelector(".tabular");
      var prog = bar.querySelector(".dock__meterbar");
      var u = state.usage;
      if (u && u.available) {
        if (label) label.textContent = u.fmt + " tok";
        if (prog) prog.setAttribute("aria-valuenow", String(u.total));
        // 进度条按输入量相对总用量画（输入通常占大头）——
        // 只是一个"这次运行里输入占多少"的可视化，不是窗口占用率
        var pct = u.total ? Math.round(u.prompt / u.total * 100) : 0;
        if (fill) fill.style.width = Math.max(3, pct) + "%";
        bar.setAttribute("data-tip",
          "上次运行用量：" + u.parts + "（来源：" + state.usageSource + "）");
      } else {
        if (label) label.textContent = "未记录用量";
        if (prog) prog.setAttribute("aria-valuenow", "0");
        if (fill) fill.style.width = "0%";
        bar.setAttribute("data-tip",
          state.usageSource ? "这次运行没有用量记录" : "还没有运行过；发一条指令后会显示真实用量");
      }
    }

    /* 重建代码索引。
       ## 这个 harness 之前**真的没有索引**，现在有（本轮补上）
       它是**符号索引 + 词表倒排**（函数/类/常量定义 + 词→文件），
       不是语义索引。索引的边界写在 incident 说明里，前端也照实展示 ——
       编一个"已索引 · 18.6 万 token"比没有索引更坏。 */
    function refreshIndex() {
      if (!Backend.available()) {
        UI.toast({ tone: "warning", title: "后端未连接",
                   desc: "索引由 harness 建立。", duration: 5000 });
        return;
      }
      UI.toast({ tone: "info", title: "正在重建索引…", duration: 2000 });
      Backend.loadIndex(true).then(function (ix) {
        if (!ix) {
          UI.toast({ tone: "danger", title: "建立索引失败",
                     desc: state.indexError || "未知原因", duration: 5000 });
          return;
        }
        UI.toast({
          tone: "ok", duration: 6000,
          title: "索引已重建：" + ix.unique_symbols + " 个符号 · " + ix.files + " 个文件",
          desc: "这是「符号索引 + 词表倒排」，不是语义索引（只懂字面）。"
            + "语言分布：" + Object.keys(ix.languages || {}).join("、"),
        });
      });
    }

    /* 状态栏左侧：**当前工作区**。
       在这之前状态栏里根本没有这个信息 —— 只有分支名。用户看不出
       "agent 到底会在哪个目录上动手"，而切换工作区又只能靠重启服务。
       有 state.workspace 就用它；没有就用服务端回报的路径 ——
       "还不知道"和"没有"是两件事，不能都显示成"工作区"。 */
    function renderWorkspaceChip() {
      var label = $("#status-workspace");
      var btn = $("#btn-workspace");
      if (!label || !btn) return;
      var p = state.workspace || (state.wsInfo && state.wsInfo.workspace) || "";
      if (!p) {
        label.textContent = Backend.available() ? "读取中…" : "未连接";
        btn.setAttribute("data-tip", Backend.available()
          ? "正在读取服务端的工作区路径" : "后端未连接，读不到工作区");
        return;
      }
      label.textContent = workspaceName(p);
      btn.setAttribute("data-tip", p + "\n点击切换工作区目录");
    }

    /* 状态栏：把真实工作区信息写回去。
       mock 版写着"已索引 · 1,284 文件 · 18.6 万 token" —— 全是编的。
       现在只显示能核实的量（文件数、分支），没有的就说没有。 */
    function renderStatusbar() {
      // 工作区分两处显示（状态栏 + 资源管理器标题）。wsInfo 回来时
      // 两处都要更新 —— 只更新一处会出现"状态栏说 A、侧栏说 B"。
      renderWorkspaceChip();
      var idx = $("#btn-index");
      if (!idx) return;
      var dot = idx.querySelector(".statusitem__dot");
      var info = state.wsInfo;
      if (!Backend.available()) {
        idx.setAttribute("data-tip", "后端未连接，无法读取工作区");
        if (dot) dot.className = "statusitem__dot";
        idx.lastChild.textContent = "未连接";
        return;
      }
      if (!info) {
        idx.setAttribute("data-tip", state.wsInfoError || "读取工作区中…");
        idx.lastChild.textContent = state.wsInfoError ? "读取失败" : "读取中";
        return;
      }
      // 明确写"未索引"而不是"已索引" —— 后端确实没有索引
      idx.lastChild.textContent = (info.truncated ? "≥" : "") + info.files + " 文件";
      idx.setAttribute("data-tip",
        info.workspace + "\n" + info.files + " 个文件 · " + info.dirs + " 个目录"
        + (info.branch ? "\n分支 " + info.branch : "")
        + "\n" + info.index_note);
      if (dot) dot.className = "statusitem__dot statusitem__dot--ok";
    }

    /* 状态栏右侧：索引徽标与改动统计。
       ## 为什么单独一个函数、而不是塞进 renderBranchChip
       它俩职责不同：renderBranchChip 管**顶栏**（仓库名/分支/终端标题），
       这里管**状态栏**。混在一起的后果实测到了：loadGitStatus() 只调
       renderStatusbar，而填充代码写在了 renderBranchChip 里 —— 于是
       状态栏永远显示"读取中"。**函数名与职责不符，接线就会漏。**
       拆开后，每个加载器调自己那个渲染函数，一眼能看出对不对。 */
    function renderStatusMetrics() {
      // **必须自己取一次 info** —— 这段代码是从 renderBranchChip 里拆出来的，
      // 而那边有个 `var info = state.wsInfo;` 在函数作用域里。
      // 拆出来后它会变成**未声明的自由变量**，运行时抛
      // `ReferenceError: info is not defined`，而整个 app 的初始化因此中断
      // （症状是"页面看着在，但所有数据都是读取中"）。
      // 拆分时最容易漏的就是这类**隐式依赖外层局部变量**的引用。
      var info = state.wsInfo;
      var il = $("#status-index-label");
      if (il) {
        // 有索引时优先报索引里的文件数（更准），否则退回目录扫描的计数
        var ix = state.indexInfo;
        il.textContent = ix
          ? (ix.unique_symbols + " 符号 · " + ix.files + " 文件")
          : (info ? ((info.truncated ? "≥" : "") + info.files + " 文件")
                  : (Backend.available() ? "读取中" : "未连接"));
      }
      // 改动统计（真实的 git 状态，不是诊断）
      var sc = $("#status-changes");
      if (sc) {
        var gg = state.gitStatus;
        if (!Backend.available()) {
          sc.textContent = "未连接";
        } else if (state.gitError) {
          sc.textContent = "读取失败";
        } else if (!gg) {
          sc.textContent = "读取中";
        } else if (!gg.is_repo) {
          sc.textContent = "非 git 仓库";
        } else if (gg.changed === 0) {
          sc.textContent = "无改动";
        } else {
          sc.textContent = gg.changed + " 改动  +" + gg.added_lines + " −" + gg.deleted_lines;
        }
        var dg = $("#btn-diag");
        if (dg) {
          dg.setAttribute("data-tip", gg && gg.is_repo
            ? ("工作区改动：" + gg.changed + " 个文件，+" + gg.added_lines
               + " / −" + gg.deleted_lines + " 行\n（git 统计，不是 lint 诊断）")
            : "工作区改动（git 统计，不是 lint 诊断）");
        }
      }
    }

    /* 把真实工作区信息填进顶栏 / 状态栏 / 终端标题。
       这些位置原本是**硬编码在 index.html 里的 mock 字样**
       （仓库名 crucible-core、分支 feat/stream-reconnect、
       终端标题 zsh — crucible-core）—— 静态 HTML 不会自动更新，
       所以要显式替换，否则页面上永远挂着一个不存在的仓库名。 */
    function renderBranchChip() {
      var info = state.wsInfo;
      var name = "";
      if (info && info.workspace) {
        var segs = String(info.workspace).split(/[\\/]/).filter(Boolean);
        name = segs.length ? segs[segs.length - 1] : info.workspace;
      }
      var repo = $("#ctx-repo");
      if (repo) repo.textContent = name || "（未连接）";
      var branch = $("#ctx-branch");
      if (branch) {
        branch.textContent = (info && info.branch) || (Backend.available() ? "—" : "未连接");
        branch.hidden = !(info && info.branch);
      }
      var sb = $("#status-branch");
      if (sb) sb.textContent = (info && info.branch) || (Backend.available() ? "—" : "未连接");
      var tt = $("#term-tab");
      if (tt) tt.textContent = name ? ("shell — " + name) : "shell";
      var tp = $("#term-prompt");
      if (tp) tp.textContent = (name || "workspace") + " $";
      var dot = $("#btn-index .statusitem__dot");
      if (dot && info) dot.className = "statusitem__dot statusitem__dot--ok";
    }

    // 挂到外层钩子：Backend 在 init 之后才会用到它们，
    // 而且只有挂上之后调用才有效 —— 未挂时是空操作，不会在 DOM 未就绪时炸。
    uiHooks.updateMeter = updateMeter;
    uiHooks.renderStatusbar = renderStatusbar;
    uiHooks.renderBranchChip = renderBranchChip;
    uiHooks.renderStatusMetrics = renderStatusMetrics;
    uiHooks.renderWorkspaceChip = renderWorkspaceChip;
    // 首屏把状态栏指标画一次（此时数据可能还没到，会显示"读取中"）
    renderStatusMetrics();
    renderWorkspaceChip();

    // ---------- 工作区切换 ----------
    // 入口一：状态栏（用户最先看到"我在哪"的地方）
    $("#btn-workspace").addEventListener("click", function () {
      openFolderPicker();
    });
    // 入口二：资源管理器里的工作区标题行。
    // `sect__head` 本身是 button（点击折叠），不能在里面再嵌一个 button
    // （button 嵌套 button 是非法 HTML，浏览器会把它拆开）。用右键菜单。
    document.addEventListener("contextmenu", function (e) {
      var head = e.target && e.target.closest && e.target.closest(".sect__head");
      if (!head) return;
      e.preventDefault();
      UI.contextMenu(e.clientX, e.clientY, [
        { label: "切换工作区…", icon: ICONS.dirOpen, onClick: function () { openFolderPicker(); } },
        { label: "复制工作区路径", icon: ICONS.file, onClick: function () {
            var p = state.workspace || (state.wsInfo && state.wsInfo.workspace) || "";
            if (p) UI.copy(p, "工作区路径");
          } },
      ]);
    });

    // ---------- 计划视图 ----------
    $("#plan-toggle").addEventListener("click", function () {
      var open = this.getAttribute("aria-expanded") === "true";
      setPlanOpen(!open);
    });
    function setPlanOpen(open) {
      $("#plan-toggle").setAttribute("aria-expanded", open ? "true" : "false");
      $("#plan-body").hidden = !open;
      UI.announce(open ? "计划已展开" : "计划已折叠");
    }

    // ---------- Agent 面板开合 ----------
    function setAgentOpen(open) {
      if (state.agentDrawer) D.shell.classList.toggle("is-agent-open", open);
      else D.shell.classList.toggle("is-agent-hidden", !open);
      var t = $("#btn-agent-toggle");
      if (t) t.setAttribute("aria-expanded", open ? "true" : "false");
      persisted.agentHidden = !open;
      save();
      UI.announce(open ? "Agent 面板已展开" : "Agent 面板已收起");
    }
    function agentIsOpen() {
      return state.agentDrawer
        ? D.shell.classList.contains("is-agent-open")
        : !D.shell.classList.contains("is-agent-hidden");
    }
    $("#btn-agent-toggle").addEventListener("click", function () { setAgentOpen(!agentIsOpen()); });
    $("#btn-agent-close").addEventListener("click", function () { setAgentOpen(false); });

    $("#btn-agent-jump").addEventListener("click", function () {
      D.stream.scrollTop = D.stream.scrollHeight;
      if (refreshStreamFade) refreshStreamFade();
      UI.announce("已跳到最新事件");
    });
    $("#btn-agent-copy").addEventListener("click", function () {
      var parts = [];
      $$(".ev", D.stream).forEach(function (n) {
        if (n.getAttribute("data-hidden") === "true") return;
        var t = n.textContent.replace(/\s+/g, " ").trim();
        if (t) parts.push(t);
      });
      UI.copy(parts.join("\n\n"), "会话记录");
    });

    // ---------- 事件过滤 ----------
    ["all", "tools", "gate"].forEach(function (k) {
      var b = $("#btn-filter-" + k);
      if (b) b.addEventListener("click", function () { setFilter(k); });
    });

    // ---------- 键盘快捷键 ----------
    document.addEventListener("keydown", function (e) {
      var mod = e.ctrlKey || e.metaKey;
      var inField = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target.tagName || "")) || e.target.isContentEditable;

      // 命令面板 / 快捷键总览：输入框里也允许（和主流编辑器一致）
      if (mod && (e.key === "k" || e.key === "K")) { e.preventDefault(); openPalette(); return; }
      if (!inField && e.key === "?" && !mod) { e.preventDefault(); openShortcuts(); return; }
      if (mod && (e.key === "b" || e.key === "B")) { e.preventDefault(); setAgentOpen(!agentIsOpen()); return; }
      if (mod && e.key === "`") { e.preventDefault(); toggleTerminal(); return; }
      if (mod && e.key === "w") { e.preventDefault(); if (state.activeTab) closeTab(state.activeTab); return; }
      if (mod && e.key === "s") {
        e.preventDefault();
        if (state.activeTab) markSaved(state.activeTab);
        else UI.toast({ tone: "warn", title: "没有打开的文件", duration: 2400 });
        return;
      }
      if (mod && e.key === "l" && (e.target === D.termInput || inField && D.termScroll.contains(document.activeElement))) {
        e.preventDefault();
        termLines = [];
        renderTerminal();
        return;
      }
      if (mod && e.key === "PageDown") { e.preventDefault(); cycleTab(1); return; }
      if (mod && e.key === "PageUp") { e.preventDefault(); cycleTab(-1); return; }
      if (mod && e.shiftKey && (e.key === "e" || e.key === "E")) {
        e.preventDefault(); goView("explorer");
        setTimeout(function () {
          var items = treeItems();
          if (items.length) { items.forEach(function (x, i) { x.tabIndex = i === 0 ? 0 : -1; }); items[0].focus(); }
        }, 60);
        return;
      }
      if (mod && e.shiftKey && (e.key === "f" || e.key === "F")) { e.preventDefault(); goView("search"); setTimeout(function () { var i = $(".railinput input"); if (i) i.focus(); }, 60); return; }

      if (e.key === "Escape") {
        if (!D.mine.hidden) { hideMention(); return; }
        if (popOpen) { closePop(); return; }
        if (state.agentDrawer) { setAgentOpen(false); return; }
        if (state.railDrawer) { closeRail(); return; }
        if (document.activeElement === D.termInput) D.termInput.blur();
        return;
      }
    });

    // ---------- 终端 ----------
    $("#btn-term-toggle").addEventListener("click", toggleTerminal);
    $("#btn-term-clear").addEventListener("click", function () {
      termLines = [];
      renderTerminal();
      UI.announce("终端已清空");
    });
    $("#btn-term-copy").addEventListener("click", function () {
      UI.copy(termLines.map(function (l) { return l.text; }).join("\n"), "终端输出");
    });
    $("#btn-term-new").addEventListener("click", function () {
      UI.toast({ tone: "info", title: "已新建终端", desc: "演示环境只有一个会话。", duration: 3000 });
    });
    $$(".termtab").forEach(function (b) {
      b.addEventListener("click", function () {
        $$(".termtab").forEach(function (x) {
          x.classList.remove("is-on");
          x.setAttribute("aria-selected", "false");
        });
        b.classList.add("is-on");
        b.setAttribute("aria-selected", "true");
        UI.announce("终端标签：" + b.textContent.trim());
      });
    });

    $("#termform").addEventListener("submit", function (e) {
      e.preventDefault();
      runCommand(D.termInput.value);
      D.termInput.value = "";
      termHistIdx = termHistory.length;
    });
    D.termInput.addEventListener("keydown", function (e) {
      if (e.key === "ArrowUp") { e.preventDefault(); termHistoryMove(-1); }
      else if (e.key === "ArrowDown") { e.preventDefault(); termHistoryMove(1); }
    });
    // 点空白处聚焦终端输入，像真终端一样
    D.termScroll.addEventListener("click", function (e) {
      if (window.getSelection() && window.getSelection().toString()) return;
      D.termInput.focus();
    });

    // ---------- 分割手柄：拖拽调终端高度 ----------
    var handle = $("#split-handle");
    var dragging = false;
    handle.addEventListener("mousedown", function (e) {
      dragging = true;
      e.preventDefault();
      document.body.style.userSelect = "none";
      document.body.style.cursor = "row-resize";
    });
    document.addEventListener("mousemove", function (e) {
      if (!dragging) return;
      var wb = $(".workbench");
      var r = wb.getBoundingClientRect();
      var h = Math.max(60, Math.min(r.bottom - e.clientY, r.height - 120));
      // 用 CSS 变量而非内联 grid-template-rows：
      // 内联样式会盖掉 .is-term-collapsed 的类规则，导致「拖过一次后折叠就失效」。
      document.documentElement.style.setProperty("--terminal-h", h + "px");
    });
    document.addEventListener("mouseup", function () {
      if (!dragging) return;
      dragging = false;
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      persisted.termH = Math.round($("#terminal").getBoundingClientRect().height);
      save();
    });
    handle.addEventListener("keydown", function (e) {
      var cur = $("#terminal").getBoundingClientRect().height;
      function setH(h) {
        document.documentElement.style.setProperty("--terminal-h", Math.max(60, h) + "px");
        persisted.termH = Math.round($("#terminal").getBoundingClientRect().height);
        save();
      }
      if (e.key === "ArrowUp") { e.preventDefault(); setH(cur + 24); }
      if (e.key === "ArrowDown") { e.preventDefault(); setH(cur - 24); }
      if (e.key === "Home") { e.preventDefault(); setH(60); }
      if (e.key === "End") { e.preventDefault(); setH(Math.round(window.innerHeight * 0.6)); }
    });

    function toggleTerminal() {
      state.termCollapsed = !state.termCollapsed;
      $(".workbench").classList.toggle("is-term-collapsed", state.termCollapsed);
      var b = $("#btn-term-toggle");
      b.setAttribute("aria-expanded", state.termCollapsed ? "false" : "true");
      b.setAttribute("aria-label", state.termCollapsed ? "展开终端" : "折叠终端");
      var chev = b.querySelector("svg path");
      if (chev) chev.setAttribute("d", state.termCollapsed ? "M5.5 12.5L10 8l4.5 4.5" : "M5.5 7.5L10 12l4.5-4.5");
      UI.announce(state.termCollapsed ? "终端已折叠" : "终端已展开");
      if (!state.termCollapsed) setTimeout(function () { D.termInput.focus(); }, 60);
    }

    // ---------- 上下文浮层 ----------
    $("#btn-dock-ctx").addEventListener("click", function (e) {
      e.stopPropagation();
      if (popOpen) closePop();
      else openPop(this);
    });
    document.addEventListener("click", function (e) {
      if (!popOpen) return;
      if ($("#ctxpop").contains(e.target) || e.target === $("#btn-dock-ctx")) return;
      closePop();
    });

    // ---------- 输入坞 ----------
    var ta = $("#dock-input");
    function autoGrow() {
      ta.style.height = "auto";
      ta.style.height = Math.min(ta.scrollHeight, 132) + "px";
    }
    ta.addEventListener("input", function () {
      autoGrow();
      $("#btn-dock-send").disabled = !ta.value.trim();
      syncMention();
    });
    ta.addEventListener("click", syncMention);
    ta.addEventListener("keydown", function (e) {
      // 补全浮层优先吃按键
      if (!D.mine.hidden && mentionItems.length) {
        if (e.key === "ArrowDown") { e.preventDefault(); setMentionIdx(mentionIdx + 1); return; }
        if (e.key === "ArrowUp") { e.preventDefault(); setMentionIdx(mentionIdx - 1); return; }
        if (e.key === "Enter" || e.key === "Tab") {
          e.preventDefault();
          mentionItems[mentionIdx].run();
          return;
        }
        if (e.key === "Escape") { e.preventDefault(); hideMention(); return; }
      }
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); return; }
      if (e.key === "Tab" && e.shiftKey) {
        e.preventDefault();
        var mode = $(".segctl__btn.is-on");
        if (mode && mode.dataset.mode === "agent") {
          $$(".segctl__btn").forEach(function (x) {
            x.classList.remove("is-on");
            x.setAttribute("aria-pressed", "false");
          });
          $('.segctl__btn[data-mode="chat"]').classList.add("is-on");
          $('.segctl__btn[data-mode="chat"]').setAttribute("aria-pressed", "true");
          UI.toast({ tone: "info", title: "已切到计划模式", desc: "先出计划、不动代码。", duration: 3000 });
        }
      }
    });
    $("#dock").addEventListener("submit", function (e) { e.preventDefault(); submit(); });
    $("#btn-dock-send").addEventListener("click", function (e) { e.preventDefault(); submit(); });
    $("#btn-dock-send").disabled = true;

    /* ---------- 「接续第 N 轮」 ---------- */
    /* 追问会自动带上上文（bridge 的 run() 带 resume_from），否则用户问
       「做完了吗」，agent 只会回答「本次会话里还没有任何任务描述」——
       尽管磁盘上躺着他刚写的文件。这是最劝退的一种失败：看起来像失忆。
       但**自动**行为必须可见：否则用户会以为每条指令都是全新开始，
       反过来又会在想开新任务时莫名其妙带上旧上下文。 */
    var threadBtn = $("#btn-thread");
    function refreshThreadBadge() {
      if (!threadBtn) return;
      var c = (window.Harness && window.Harness.conversation)
        ? window.Harness.conversation() : null;
      var on = !!(c && c.runId);
      threadBtn.hidden = !on;
      if (!on) return;
      var n = $("#thread-n");
      if (n) n.textContent = String(c.turns || 1);
    }
    if (threadBtn) {
      threadBtn.addEventListener("click", function () {
        if (!(window.Harness && window.Harness.resetConversation)) return;
        window.Harness.resetConversation();
        refreshThreadBadge();
        UI.toast({
          tone: "info", title: "已新开一段对话",
          desc: "下一条指令不会再带上之前的上下文。", duration: 3200,
        });
      });
    }
    window.refreshThreadBadge = refreshThreadBadge;

    function submit() {
      var v = ta.value;
      if (!v.trim()) return;
      sendPrompt(v);
      ta.value = "";
      autoGrow();
      $("#btn-dock-send").disabled = true;
      hideMention();
    }

    // ---------- 新建文件 / 新建目录 ----------
    /* 这两项以前只弹一句「这是演示环境」的 toast —— 功能根本不存在。
       而菜单里摆着它，用户点完就是"什么都没发生"，还会怪自己点错。
       现在真的落盘：POST /api/workspace/create。
       后端把能力压到最小（只能建空的、不能覆盖），见 server.py 的说明。 */
    function startCreate(kind, baseDir) {
      var isDir = kind === "dir";
      if (state.railView !== "explorer") goView("explorer");
      if (!Backend.available()) {
        UI.toast({
          tone: "warning", title: "后端未连接，创建不了", duration: 6000,
          desc: "新建要写磁盘，只有 harness 在跑的时候才能落盘。"
            + "先启动 `python harness/server.py`。",
        });
        return;
      }
      // 默认落在当前打开文件所在的目录 —— 用户正在看哪个文件，
      // 新建的东西大概率就该在它旁边。从目录右键进来时用那个目录。
      // 都没打开就落到根。
      var cur = state.activeTab || "";
      var base = baseDir != null ? baseDir
        : (cur.indexOf("/") >= 0 ? cur.slice(0, cur.lastIndexOf("/")) : "");
      UI.formDialog({
        title: isDir ? "新建目录" : "新建文件",
        desc: base
          ? "会建在 " + base + " 目录下；也可以改成别的路径。"
          : "会建在工作区根目录；也可以写成带层级的路径。",
        submitLabel: "创建",
        focusName: "path",
        fields: [{
          name: "path",
          label: isDir ? "目录路径" : "文件路径",
          value: base ? base + "/" : "",
          placeholder: isDir ? "例如 src/utils" : "例如 src/utils/helper.js",
          hint: "相对工作区的路径，可以带目录层级。已存在的不会被覆盖。",
          required: true,
        }],
        onSubmit: function (v, dlg) {
          var p = (v.path || "").trim();
          dlg.busy(true, "创建中…");
          return window.Harness.workspaceCreate(state.workspace, p, kind)
            .then(function (r) {
              if (!r.ok) {
                var b = r.body || {};
                dlg.busy(false);
                // 失败原因写在字段下面而不是弹 toast：
                // 用户接下来要改的正是这个字段
                dlg.setError("path", String(b.detail || b.error || "创建失败"));
                return false;
              }
              var made = r.body.path;
              /* 让新建的东西**看得见**：把父目录这一路展开。
                 只建成功却看不见，用户会以为没建成 —— 与"根本没做"同一个症状。 */
              var segs = made.split("/");
              var acc = "";
              for (var i = 0; i < segs.length - 1; i++) {
                acc = acc ? acc + "/" + segs[i] : segs[i];
                treeState["open:" + acc] = true;
              }
              treeState["open:" + (isDir ? made : (r.body.parent || ""))] = true;
              // 全丢一遍目录缓存：新条目可能落在任意一层，
              // 而缓存存的是"当时那一层的子项"。树很浅，重新请求的代价远小于
              // 显示一份不含新条目的旧列表。
              state.treeCache = {};
              Backend.loadWorkspaceInfo();
              renderRail();
              if (!isDir) {
                delete state.fileCache[made];
                openTab(made);
                Backend.loadFile(made);
              }
              UI.toast({
                tone: "ok", duration: 3600,
                title: (isDir ? "已创建目录 " : "已创建文件 ") + made,
                desc: isDir ? "" : "空文件，内容在编辑器里写。",
              });
              return true;
            })
            .catch(function (e) {
              dlg.busy(false);
              dlg.setError("path", String(e && e.message || e));
              return false;
            });
        },
      });
    }

    // 文件树的目录右键要用同一个实现（见 renderRealTree）——
    // 两处各写一份，迟早只改一处。
    uiHooks.startCreate = startCreate;

    $("#btn-newfile").addEventListener("click", function () {
      var r = D.railBody.getBoundingClientRect();
      UI.contextMenu(r.left + 24, r.top + 60, [
        { label: "新建文件", icon: ICONS.plus, onClick: function () { startCreate("file"); } },
        { label: "新建目录", icon: ICONS.dirClosed, onClick: function () { startCreate("dir"); } },
        { sep: true },
        // 原来这里的「刷新」调的是 refreshIndex（重建代码索引）——
        // 在文件树的上下文里点"刷新"，用户要的是刷新树，不是重建索引。
        { label: "刷新文件树", icon: ICONS.play, onClick: function () {
            Backend.invalidateWorkspace();
            UI.toast({ tone: "info", title: "已刷新文件树", duration: 1800 });
          } },
      ]);
    });

    // ---------- 标签栏「更多」 ----------
    $("#tabbar-more").addEventListener("click", function (e) {
      e.stopPropagation();
      var r = this.getBoundingClientRect();
      var items = state.tabs.map(function (t) {
        return {
          label: t.path.split("/").pop() + (t.dirty ? " ●" : ""),
          icon: ICONS.file,
          onClick: function () { openTab(t.path); },
        };
      });
      UI.contextMenu(r.left - 120, r.bottom + 4, items);
    });
    D.tabbarScroll.addEventListener("scroll", updateTabOverflow, { passive: true });

    // ---------- 文件树键盘导航（事件委托） ----------
    D.railBody.addEventListener("keydown", onTreeKey);

    // ---------- 遮罩 ----------
    $("#scrim").addEventListener("click", function () {
      setAgentOpen(false);
      closeRail();
    });

    // ---------- 媒体查询变化 ----------
    // 两个断点**分别监听**。合成一个 mobile 标志会让 861–1024px 这段
    // 出现"JS 认为是抽屉、CSS 认为不是"的错位（实测：那个 ‹ 按钮点了没反应）。
    function watch(mqStr, apply) {
      var mq = window.matchMedia(mqStr);
      apply(mq.matches);
      var onChange = function (m) { apply(m.matches); };
      if (mq.addEventListener) mq.addEventListener("change", onChange);
      else if (mq.addListener) mq.addListener(onChange);
    }
    watch("(max-width: 860px)", function (m) {
      state.railDrawer = m;
      // 从窄变宽时清掉抽屉态，否则宽屏会残留一个看不见的 is-rail-open
      if (!m) closeRail();
    });
    watch("(max-width: 1024px)", function (m) {
      state.agentDrawer = m;
      if (!m) D.shell.classList.remove("is-agent-open");
    });
  }

  /* ---------- 侧栏开合：一个状态，四个入口 ----------
     入口：活动栏图标、侧栏头部 ‹、命令面板、快捷键。
     **不能让某个入口只在特定宽度下有效** —— 之前那个 ‹ 在 861–1024px
     区间里点了完全没反应（因为那个标志用的是 1024、而 CSS 的抽屉
     断点用的是 860，两处断言不一致）。现在断点从 CSS 抄过来只写一次。 */
  function isRailCollapsed() {
    return D.shell.classList.contains("is-rail-collapsed");
  }
  function toggleRailCollapsed(force) {
    if (state.railDrawer) { toggleRailDrawer(force); return; }
    var collapsed = (force == null) ? !isRailCollapsed() : !force;
    D.shell.classList.toggle("is-rail-collapsed", collapsed);
    persisted.railCollapsed = collapsed;
    save();
    // 折叠后焦点若还留在侧栏里，Tab 键会走进一块 0 宽的区域（看不见却可聚焦）。
    // 把焦点交回活动栏当前视图按钮，键盘用户才有办法再展开。
    if (collapsed && D.rail && D.rail.contains(document.activeElement)) {
      var btn = $('.actbar__btn[data-view="' + state.railView + '"]');
      if (btn) btn.focus();
    }
    UI.announce(collapsed ? "侧栏已折叠（点活动栏图标展开）" : "侧栏已展开");
  }
  function expandRail() {
    if (state.railDrawer) { openRail(); return; }
    if (isRailCollapsed()) toggleRailCollapsed(true);
  }
  function toggleRailDrawer(force) {
    var open = (force == null)
      ? !D.shell.classList.contains("is-rail-open")
      : !!force;
    D.shell.classList.toggle("is-rail-open", open);
    UI.announce(open ? "侧栏已打开" : "侧栏已收起");
  }
  function setActiveViewButton(v) {
    $$(".actbar__btn[data-view]").forEach(function (x) {
      var on = x.dataset.view === v;
      x.classList.toggle("is-on", on);
      x.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function goView(v) {
    setActiveViewButton(v);
    state.railView = v;
    persisted.activeView = v;
    save();
    state.focusIdx = 0;
    renderRail();
    // 命令面板/快捷键/菜单过来的调用，语义是"打开这个视图"，
    // 所以必须是**展开**而不是切换 —— 否则在已选中该视图时按 Ctrl+Shift+E
    // 会把侧栏收起来，与"打开资源管理器"完全相反。
    expandRail();
  }

  function openRail() { D.shell.classList.add("is-rail-open"); }
  function closeRail() { D.shell.classList.remove("is-rail-open"); }
  function openRailIfMobile() { /* 窄屏点文件后保留侧栏，方便连续操作 */ }

  function expandAll(open) {
    Object.keys(treeState).forEach(function (k) {
      if (k.indexOf("0:") === 0 || k.indexOf("1:") === 0) treeState[k] = open;
    });
    renderRail();
    UI.toast({ tone: "info", title: open ? "已全部展开" : "已全部折叠", duration: 2200 });
  }

  /* ============================================================
     模型接入设置 —— 只有三个字段
     ============================================================
     设计取舍：
     1. **只有三个字段**。接口地址 / API Key / 模型名称。
        不设「厂家下拉」—— 任何厂家下拉都会过时，而这三个字段
        能接任意 OpenAI 兼容端点（含中转），换厂家不用改代码。
     2. **地址归一化实时可见**。用户填 `https://x.com/v1` 时，
        下方直接显示最终请求的完整 URL。地址写错是这类配置里
        最常见的失败，而原始报错（404/401）通常不指向原因。
     3. **测试连接是一等公民**。填完先验一次再保存，
        避免「保存成功但根本用不了」。
     4. **key 留空 = 沿用旧值**。这是关键语义 —— 否则用户每次
        改模型名都得重新粘一遍 key。
     ============================================================ */
  function normalizeBase(u) {
    var s = String(u || "").trim().replace(/\/+$/, "");
    if (!s) return "";
    if (/\/chat\/completions$/.test(s)) return s;
    return s + "/chat/completions";
  }

  /* ============================================================
     设置页（外观 / 模型接入 / 工作区 / 关于）
     ============================================================
     ## 为什么从"弹一个小表单"改成"一页设置"

     上一版只有 `openModelSettings()`：一个三字段的模态，入口是活动栏底部
     那个图标。问题是：

     1. **入口不像入口**。图标画成圆+八条射线（看着是亮度），标签只写
        "设置"，而它只做模型接入。用户点了以为要换主题，没反应就认为坏了。
     2. **主题没有真正的入口**。真正的换主题按钮在顶栏、图标是个「+」，
        被当成"新建"。
     3. **后端没连上时只剩一句 toast**，用户看不到任何可操作的东西。

     所以这里做成一个真正的设置页：左侧分栏、右侧内容。主题与模型接入
     是**同一页的两个分区**，不再靠猜图标。后端未连接时也照常打开，
     只是把"为什么现在不能保存"写在页面上，而不是弹个提示就消失。

     ## 模型接入只有三个字段

     接口地址 / API Key / 模型名称。不设「厂家下拉」—— 任何厂家下拉都会
     过时，而这三个字段能接任意 OpenAI 兼容端点（含中转）。
     几条必须保持的语义：
       · 地址归一化**实时可见**（把最终请求 URL 显示出来）
       · 测试连接是一等公民（填完先验一次）
       · key 留空 = **沿用**旧值（不是清空）
       · 只存在服务端的 .crucible/config.json，不回传明文
     ============================================================ */

  var _settingsClose = null;      // 当前打开的设置页关闭函数（防叠层）

  function normalizeBase(u) {
    var s = String(u || "").trim().replace(/\/+$/, "");
    if (!s) return "";
    if (/\/chat\/completions$/.test(s)) return s;
    return s + "/chat/completions";
  }

  function currentThemeChoice() {
    return persisted.theme || "auto";
  }

  function openSettings(tab) {
    if (_settingsClose) _settingsClose();

    var live = !!(window.Harness && window.Harness.isLive());
    var ws = state.workspace;
    var curTab = tab || "appearance";

    var back = el("div", "modalback");
    var dlg = el("div", "modal modal--settings");
    dlg.setAttribute("role", "dialog");
    dlg.setAttribute("aria-modal", "true");
    var titleId = "settings-title";
    dlg.setAttribute("aria-labelledby", titleId);

    var head = el("div", "settings__head");
    var h2 = el("h2", "modal__title", "设置");
    h2.id = titleId;
    head.appendChild(h2);
    var closeBtn = el("button", "iconbtn iconbtn--sm");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "关闭设置");
    closeBtn.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">'
      + '<path d="M6 6l8 8M14 6l-8 8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>';
    head.appendChild(closeBtn);
    dlg.appendChild(head);

    var body = el("div", "settings__body");
    var nav = el("nav", "settings__nav");
    nav.setAttribute("role", "tablist");
    nav.setAttribute("aria-label", "设置分区");
    var pane = el("div", "settings__pane");
    pane.setAttribute("role", "tabpanel");
    body.appendChild(nav);
    body.appendChild(pane);
    dlg.appendChild(body);

    var foot = el("div", "settings__foot");
    var hint = el("span", "settings__foothint",
      live ? "配置保存在服务端 .crucible/config.json（已在 .gitignore，不随仓库提交）。"
           : "后端未连接：可以先看，连上后才能保存。");
    foot.appendChild(hint);
    var doneBtn = el("button", "btn", "关闭");
    doneBtn.type = "button";
    doneBtn.addEventListener("click", function () { close(); });
    foot.appendChild(doneBtn);
    dlg.appendChild(foot);

    back.appendChild(dlg);
    document.body.appendChild(back);
    UI.lockScroll(true);
    var releaseTrap = UI.trapFocus(dlg);
    closeBtn.focus();

    var TABS = [
      ["appearance", "外观"],
      ["model", "模型接入"],
      ["workspace", "工作区"],
      ["about", "关于"],
    ];

    function setTab(name) {
      curTab = name;
      $$(".settings__navitem", nav).forEach(function (b) {
        var on = b.dataset.tab === name;
        b.classList.toggle("is-on", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
        b.tabIndex = on ? 0 : -1;
      });
      pane.innerHTML = "";
      if (name === "appearance") pane.appendChild(appearancePane());
      else if (name === "model") pane.appendChild(modelPane());
      else if (name === "workspace") pane.appendChild(workspacePane());
      else pane.appendChild(aboutPane());
    }

    TABS.forEach(function (t) {
      var b = el("button", "settings__navitem", t[1]);
      b.type = "button";
      b.dataset.tab = t[0];
      b.setAttribute("role", "tab");
      b.addEventListener("click", function () { setTab(t[0]); });
      nav.appendChild(b);
    });

    /* ---------------- 外观 ---------------- */
    function appearancePane() {
      var wrap = el("div", "settings__sect");
      wrap.appendChild(el("h3", "settings__h", "主题"));
      wrap.appendChild(el("p", "settings__p",
        "明暗随时可切。选「跟随系统」时不写死，操作系统改了这里跟着变。"));

      var group = el("div", "settings__opts");
      group.setAttribute("role", "radiogroup");
      group.setAttribute("aria-label", "主题");
      var cur = currentThemeChoice();
      [["dark", "深色"], ["light", "浅色"], ["auto", "跟随系统"]].forEach(function (o) {
        var b = el("button", "settings__opt settings__theme-btn");
        b.type = "button";
        b.dataset.theme = o[0];
        b.setAttribute("role", "radio");
        b.setAttribute("aria-checked", cur === o[0] ? "true" : "false");
        b.classList.toggle("is-on", cur === o[0]);
        // 小色块：让人不用点就知道这个选项长什么样
        var chip = el("span", "settings__swatch settings__swatch--" + o[0]);
        b.appendChild(chip);
        b.appendChild(el("span", null, o[1]));
        b.addEventListener("click", function () {
          if (o[0] === "auto") {
            persisted.theme = null;
            save();
            var sys = window.matchMedia("(prefers-color-scheme: light)").matches
              ? "light" : "dark";
            applyTheme(sys);
          } else {
            persisted.theme = o[0];
            save();
            applyTheme(o[0]);
          }
          $$(".settings__theme-btn", group).forEach(function (x) {
            var on = x.dataset.theme === o[0];
            x.classList.toggle("is-on", on);
            x.setAttribute("aria-checked", on ? "true" : "false");
          });
          UI.announce("主题：" + o[1]);
        });
        group.appendChild(b);
      });
      wrap.appendChild(group);

      var note = el("p", "settings__note");
      note.textContent = "主题选择存在浏览器本地（不占服务端配置）。";
      wrap.appendChild(note);
      return wrap;
    }

    /* ---------------- 模型接入 ---------------- */
    function modelPane() {
      var wrap = el("div", "settings__sect");
      wrap.appendChild(el("h3", "settings__h", "模型接入"));
      wrap.appendChild(el("p", "settings__p",
        "填这三项即可接任意 OpenAI 兼容端点（含中转）。不需要选厂家。"
        + "不填也能用 —— 此时运行走离线脚本，只演示流程不真的调模型。"));

      if (!live) {
        var warn = el("div", "settings__warn");
        warn.appendChild(el("strong", null, "后端未连接"));
        warn.appendChild(el("span", null,
          "模型配置由 harness 后端读写（.crucible/config.json），所以现在读不到也存不了。"));
        var retry = el("button", "btn btn--sm", "重试连接");
        retry.type = "button";
        retry.addEventListener("click", function () {
          retry.disabled = true;
          retry.textContent = "连接中…";
          window.Harness.probe().then(function (ok) {
            if (ok) {
              refreshConnectionUI();
              close();
              openSettings("model");
            } else {
              retry.disabled = false;
              retry.textContent = "重试连接";
              UI.toast({ tone: "warning", title: "仍然连不上",
                         desc: "确认后端已启动：python harness/server.py --port 8787",
                         duration: 5000 });
            }
          });
        });
        warn.appendChild(retry);
        wrap.appendChild(warn);
      }

      // 先用 state.endpoint（启动时已从服务端读过）预填，再异步刷新。
      // 不预填的话，打开这一页会先闪一下"尚未配置"——
      // 一个**已有模型在跑**的界面短暂显示"未配置"是会让人怀疑配置丢了的。
      var form = {
        base: null, key: null, model: null,
        cur: state.endpoint || {
          base_url: "", model: "", has_key: false,
          api_key_masked: "", chat_url: "", configured: false,
        },
        result: el("div", "settings__result"),
      };

      var status = el("div", "settings__status");
      wrap.appendChild(status);
      wrap.appendChild(form.result);

      form.base = textField("接口地址", "https://api.deepseek.com/v1",
        "填到 /v1 即可；末尾带不带 /chat/completions 都行，会自动补。");
      wrap.appendChild(form.base.wrap);
      form.key = textField("API Key", "sk-…",
        "只存在服务端，不回传明文。已有 key 时留空 = 沿用。", "password");
      wrap.appendChild(form.key.wrap);
      form.model = textField("模型名称", "deepseek-chat",
        "该端点上可用的模型 id，原样填即可。");
      wrap.appendChild(form.model.wrap);

      var preview = el("p", "field__hint settings__preview");
      preview.hidden = true;
      wrap.appendChild(preview);
      function refreshPreview() {
        var full = normalizeBase(form.base.input.value);
        preview.textContent = full ? ("将请求：" + full) : "";
        preview.hidden = !full;
      }
      form.base.input.addEventListener("input", refreshPreview);

      var acts = el("div", "settings__acts");
      var testBtn = el("button", "btn", "测试连接");
      testBtn.type = "button";
      var saveBtn = el("button", "btn btn--primary", "保存");
      saveBtn.type = "button";
      var clearBtn = el("button", "btn btn--danger", "清除配置");
      clearBtn.type = "button";
      acts.appendChild(testBtn);
      acts.appendChild(saveBtn);
      var spacer = el("span", "settings__spacer");
      acts.appendChild(spacer);
      acts.appendChild(clearBtn);
      wrap.appendChild(acts);

      if (!live) {
        [testBtn, saveBtn, clearBtn].forEach(function (b) { b.disabled = true; });
      }

      function paintStatus() {
        status.innerHTML = "";
        var c = form.cur;
        var dotCls = c.configured ? "is-ok" : (c.has_key ? "is-warn" : "is-off");
        var row = el("div", "settings__statusrow");
        row.appendChild(el("span", "settings__dot " + dotCls));
        row.appendChild(el("span", null, c.configured
          ? ("已配置：" + c.model + " @ " + (c.chat_url || c.base_url))
          : (c.has_key ? "不完整：有 API Key，但缺接口地址或模型名" : "尚未配置")));
        status.appendChild(row);
        if (c.has_key) {
          status.appendChild(el("div", "settings__sub",
            "已保存的 key：" + (c.api_key_masked || "••••") + "（留空即沿用）"));
        }
      }

      function fillForm() {
        form.base.input.value = form.cur.base_url || "";
        form.model.input.value = form.cur.model || "";
        form.key.input.value = "";
        form.key.input.placeholder = form.cur.has_key
          ? ("已保存：" + (form.cur.api_key_masked || "••••") + "（留空则沿用）")
          : "sk-…";
        refreshPreview();
      }

      function readForm() {
        var v = {
          base_url: (form.base.input.value || "").trim(),
          model: (form.model.input.value || "").trim(),
        };
        var k = (form.key.input.value || "").trim();
        if (k) v.api_key = k;                 // 填了 → 替换
        else if (!form.cur.has_key) v.api_key = "";   // 本来没有也没填 → 明确空
        // 有 key 且留空 → **不带该字段**，服务端沿用旧值
        return v;
      }

      if (live) {
        paintStatus();
        window.Harness.getConfig(ws).then(function (r) {
          form.cur = (r && r.endpoint) || form.cur;
          paintStatus();
          fillForm();
        }).catch(function (e) {
          form.result.innerHTML = "";
          form.result.appendChild(el("div", "settings__warn",
            "读取配置失败：" + String(e && e.message || e)));
        });
      } else {
        paintStatus();
        fillForm();
      }

      testBtn.addEventListener("click", function () {
        form.result.innerHTML = "";
        var busy = el("div", "settings__resultrow is-busy", "正在连接该端点…");
        form.result.appendChild(busy);
        testBtn.disabled = true;
        window.Harness.testConfig(ws, readForm(), 20).then(function (rep) {
          testBtn.disabled = false;
          form.result.innerHTML = "";
          var row = el("div", "settings__resultrow "
            + (rep.ok ? "is-ok" : "is-bad"));
          row.appendChild(el("span", null, rep.message || (rep.ok ? "连通" : "失败")));
          if (rep.url || rep.elapsed_s != null) {
            row.appendChild(el("span", "settings__meta",
              (rep.url || "") + (rep.elapsed_s != null ? ("　" + rep.elapsed_s + "s") : "")));
          }
          form.result.appendChild(row);
          UI.announce(rep.ok ? "连接测试通过" : "连接测试未通过");
        }).catch(function (e) {
          testBtn.disabled = false;
          form.result.innerHTML = "";
          form.result.appendChild(el("div", "settings__resultrow is-bad",
            String(e && e.message || e)));
        });
      });

      saveBtn.addEventListener("click", function () {
        if (!form.base.input.value.trim()) {
          form.base.markError("接口地址必填。没配置就留空整页不填 —— 运行会走离线脚本。");
          form.base.input.focus();
          return;
        }
        if (!form.model.input.value.trim()) {
          form.model.markError("模型名称必填。");
          form.model.input.focus();
          return;
        }
        if (!form.cur.has_key && !form.key.input.value.trim()) {
          form.key.markError("还没有 API Key，这里必须填一个。");
          form.key.input.focus();
          return;
        }
        saveBtn.disabled = true;
        saveBtn.textContent = "保存中…";
        window.Harness.saveConfig(ws, readForm()).then(function (r) {
          saveBtn.disabled = false;
          saveBtn.textContent = "保存";
          form.cur = (r && r.endpoint) || form.cur;
          applyEndpoint(form.cur);
          paintStatus();
          fillForm();
          UI.toast({
            tone: "success",
            title: "模型接入已保存",
            desc: form.cur.configured
              ? (form.cur.model + " @ " + form.cur.chat_url)
              : "配置不完整，运行时仍走离线脚本。",
            duration: 5000,
          });
        }).catch(function (e) {
          saveBtn.disabled = false;
          saveBtn.textContent = "保存";
          form.result.innerHTML = "";
          form.result.appendChild(el("div", "settings__resultrow is-bad",
            "保存失败：" + String(e && e.message || e)));
        });
      });

      clearBtn.addEventListener("click", function () {
        UI.confirm({
          title: "清除模型配置？",
          desc: "会删除服务端保存的接口地址、API Key 与模型名称。清除后运行回到离线脚本模式。",
          confirmLabel: "清除", tone: "danger",
        }).then(function (ok) {
          if (!ok) return;
          window.Harness.clearConfig(ws).then(function (r) {
            form.cur = (r && r.endpoint) || {
              base_url: "", model: "", has_key: false,
              api_key_masked: "", chat_url: "", configured: false,
            };
            applyEndpoint(form.cur);
            paintStatus();
            fillForm();
            form.result.innerHTML = "";
            UI.toast({ tone: "info", title: "已清除模型配置",
                       desc: r && r.removed ? "配置已删除。" : "本来就没有配置文件。",
                       duration: 4000 });
          }).catch(function (e) {
            UI.toast({ tone: "danger", title: "清除失败",
                       desc: String(e && e.message || e), duration: 5000 });
          });
        });
      });

      return wrap;
    }

    /* 一个带标签 / 提示 / 错误位的字段。不用 formDialog 的字段系统，
       因为设置页是**分区**的，一个 form 装不下四个分区。 */
    function textField(label, placeholder, hint, type) {
      var wrap = el("div", "field");
      var id = "set-" + label.replace(/\s+/g, "-");
      var lb = el("label", "field__label", label);
      lb.setAttribute("for", id);
      wrap.appendChild(lb);
      var box = el("div", "field__box");
      var input = document.createElement("input");
      input.className = "field__input";
      input.id = id;
      input.type = type || "text";
      input.placeholder = placeholder || "";
      input.spellcheck = false;
      input.autocomplete = type === "password" ? "new-password" : "off";
      box.appendChild(input);
      if (type === "password") {
        var eye = el("button", "field__reveal");
        eye.type = "button";
        eye.setAttribute("aria-label", "显示内容");
        eye.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">'
          + '<path d="M2 10s3-5 8-5 8 5 8 5-3 5-8 5-8-5-8-5z" fill="none" stroke="currentColor" stroke-width="1.4"/>'
          + '<circle cx="10" cy="10" r="2.2" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';
        eye.addEventListener("click", function () {
          var shown = input.type === "text";
          input.type = shown ? "password" : "text";
          eye.setAttribute("aria-label", shown ? "显示内容" : "隐藏内容");
          input.focus();
        });
        box.appendChild(eye);
      }
      wrap.appendChild(box);
      var hintEl = el("div", "field__hint", hint || "");
      if (!hint) hintEl.hidden = true;
      wrap.appendChild(hintEl);
      var errEl = el("div", "field__error");
      errEl.hidden = true;
      wrap.appendChild(errEl);
      input.addEventListener("input", function () { errEl.hidden = true; });
      return {
        wrap: wrap, input: input,
        markError: function (msg) {
          errEl.textContent = msg;
          errEl.hidden = false;
        },
      };
    }

    /* ---------------- 工作区 ---------------- */
    function workspacePane() {
      var wrap = el("div", "settings__sect");
      wrap.appendChild(el("h3", "settings__h", "工作区"));
      wrap.appendChild(el("p", "settings__p",
        "只读。这里展示的是真实状态 —— 读不到就写读不到，不留占位数字。"));

      var rows = el("dl", "settings__kv");
      function kv(k, v) {
        rows.appendChild(el("dt", null, k));
        rows.appendChild(el("dd", null, v == null || v === "" ? "—" : String(v)));
      }
      // 没带 ?workspace= 时 state.workspace 是空串，真实工作区由服务端决定。
      // 这时要显示**服务端回报的路径**（wsInfo.workspace），
      // 而不是一个"—"——"不知道"和"没有"是两件事。
      var wsPath = state.workspace
        || (state.wsInfo && state.wsInfo.workspace)
        || "(服务端默认工作区，尚未读到路径)";
      kv("路径", wsPath);
      var parts = String(wsPath).split(/[\\/]/).filter(Boolean);
      kv("名称", parts.length ? parts[parts.length - 1] : "—");

      var g = state.gitStatus;
      kv("分支", g && g.is_repo ? (g.branch || "—") : "非 git 仓库");
      kv("改动", g && g.is_repo
        ? (g.changed + " 个文件 · +" + g.added_lines + " / −" + g.deleted_lines + " 行")
        : "—");

      var ix = state.indexInfo;
      kv("代码索引", ix
        ? (ix.unique_symbols + " 个符号 · " + ix.files + " 个文件")
        : (state.indexError ? "建立失败" : "尚未建立"));
      if (ix) {
        kv("索引语言", Object.keys(ix.languages || {}).join("、") || "—");
        kv("索引边界", "符号索引 + 词表倒排，不是语义索引；不含跨文件调用关系");
      }

      kv("沙箱", (document.querySelector("[data-sandbox]") || {}).textContent || "—");
      kv("审批策略", (document.querySelector("[data-approval]") || {}).textContent || "—");
      wrap.appendChild(rows);

      var acts = el("div", "settings__acts");
      var switchBtn = el("button", "btn btn--primary", "切换目录…");
      switchBtn.type = "button";
      switchBtn.addEventListener("click", function () {
        // 选择器会叠在设置页上面 —— 所以它的 Esc 必须分层判断
        // （openFolderPicker 里有和这里一样的 backs[last] !== back 判据）。
        openFolderPicker();
      });
      acts.appendChild(switchBtn);

      var rebuild = el("button", "btn", "重建索引");
      rebuild.type = "button";
      rebuild.addEventListener("click", function () {
        if (!Backend.available()) {
          UI.toast({ tone: "warning", title: "后端未连接", duration: 4000 });
          return;
        }
        rebuild.disabled = true;
        rebuild.textContent = "重建中…";
        Backend.loadIndex(true).then(function () {
          rebuild.disabled = false;
          rebuild.textContent = "重建索引";
          setTab("workspace");
          UI.toast({ tone: "success", title: "索引已重建", duration: 3500 });
        });
      });
      acts.appendChild(rebuild);
      wrap.appendChild(acts);
      return wrap;
    }

    /* ---------------- 关于 ---------------- */
    function aboutPane() {
      var wrap = el("div", "settings__sect");
      wrap.appendChild(el("h3", "settings__h", "Crucible"));
      wrap.appendChild(el("p", "settings__p",
        "一个 harness 原型：模型只负责选动作，约束、验证与纠正都在 harness 里。"));
      var rows = el("dl", "settings__kv");
      rows.appendChild(el("dt", null, "前端"));
      rows.appendChild(el("dd", null, "静态页面，无构建步骤"));
      rows.appendChild(el("dt", null, "后端"));
      rows.appendChild(el("dd", null, live ? "已连接 · Python 标准库零依赖" : "未连接"));
      rows.appendChild(el("dt", null, "模型"));
      rows.appendChild(el("dd", null, state.endpoint && state.endpoint.configured
        ? state.endpoint.model : "未配置（运行走离线脚本）"));
      wrap.appendChild(rows);

      var acts = el("div", "settings__acts");
      var help = el("button", "btn", "键盘快捷键总览");
      help.type = "button";
      help.addEventListener("click", function () { close(); openShortcuts(); });
      acts.appendChild(help);
      wrap.appendChild(acts);
      return wrap;
    }

    function close() {
      if (_settingsClose !== close) return;
      _settingsClose = null;
      if (releaseTrap) releaseTrap();
      UI.lockScroll(false);
      document.removeEventListener("keydown", onKey, true);
      back.classList.add("modalback--out");
      setTimeout(function () {
        if (back.parentNode) back.parentNode.removeChild(back);
      }, 190);
    }
    function onKey(e) {
      if (e.key !== "Escape") return;
      // ★ 设置页上面可能还压着一个确认框（"清除模型配置？"）。
      //   这个监听是 capture 阶段的，不分层的话 Esc 会**先把设置页关掉**，
      //   而那个确认框还留在屏幕上 —— 用户按 Esc 想取消，结果整页没了。
      //   判据：只有自己是最后一个模态时才处理 Esc。
      var backs = document.querySelectorAll(".modalback");
      if (backs.length && backs[backs.length - 1] !== back) return;
      e.preventDefault();
      e.stopPropagation();
      close();
    }
    document.addEventListener("keydown", onKey, true);
    closeBtn.addEventListener("click", close);
    doneBtn.addEventListener("click", close);
    back.addEventListener("mousedown", function (e) {
      if (e.target === back) close();
    });
    _settingsClose = close;

    setTab(curTab);
  }

  /* 兼容旧调用点：这些地方只想直接落到"模型接入"这一页 */
  function openModelSettings() { openSettings("model"); }

  /* 把某个 endpoint 的公开展示形态应用到界面上 */
  function applyEndpoint(ep) {
    if (!ep) return;
    state.endpoint = ep;
    var name = ep.configured ? ep.model : "未配置";
    var title = ep.configured
      ? (name + " @ " + ep.chat_url)
      : "模型未配置 · 点此接入";

    // 状态栏那个按钮里除了模型名还有一个**状态点 span**。
    // 直接写 textContent 会把这个 span 一并抹掉 —— 于是用户一旦接入模型，
    // 指示点就永久消失，颜色也无处可改（实测踩到）。
    // 所以只替换末尾那个文本节点，保住结构。
    var btn = $("#btn-model");
    if (btn) {
      setTrailingText(btn, name);
      btn.title = title;
    }
    var panelModel = $(".agentpanel__model");
    if (panelModel) {
      panelModel.textContent = name;
      panelModel.title = title;
    }

    var dot = $(".statusitem__dot--model");
    if (dot) dot.style.background = ep.configured ? "var(--jade)" : "var(--text-faint)";
  }

  /* 替换元素末尾的文本节点，保留已有的子元素（如状态点）。
     若末尾不是文本节点（全被元素占满），追加一个。 */
  function setTrailingText(node, text) {
    var last = node.lastChild;
    if (last && last.nodeType === 3) {
      last.nodeValue = text;
    } else {
      node.appendChild(document.createTextNode(text));
    }
  }

  /* 启动时把已保存的配置反映到界面 */
  function loadEndpoint() {
    if (!window.Harness || !window.Harness.isLive()) return Promise.resolve(null);
    return window.Harness.getConfig(state.workspace).then(function (r) {
      applyEndpoint(r && r.endpoint);
      return r && r.endpoint;
    }).catch(function () { return null; });
  }

  /* ---------- 连接状态变化后重画界面 ----------
     这一页里有十余处文案是"有后端 / 没后端"两套（空状态、状态栏、
     执行轨迹说明、编辑器占位…）。它们都在**首屏**渲染过一次，
     而连通性探测是**异步**的 —— 如果连上之后不重画，就会出现
     "底部说已连接、面板说未连接"这种自相矛盾的屏幕。
     所以把"需要重画的东西"收在一个函数里，连通后调一次。
     **新增依赖连接状态的区块时，记得加到这里**（漏加的后果就是这次的 bug）。 */
  function refreshConnectionUI() {
    try {
      renderRail();
      renderTabs();
      renderEditor();
      renderPlan();
      renderStream();
      if (uiHooks.renderStatusbar) uiHooks.renderStatusbar();
      if (uiHooks.renderBranchChip) uiHooks.renderBranchChip();
      if (uiHooks.renderStatusMetrics) uiHooks.renderStatusMetrics();
      if (uiHooks.renderWorkspaceChip) uiHooks.renderWorkspaceChip();
      if (uiHooks.updateMeter) uiHooks.updateMeter();
    } catch (e) {
      // 重画失败不能连"已连接"这个事实一起吞掉 —— 至少留一条可查的痕迹
      if (window.console) console.error("连接后重画界面失败：", e);
    }
  }

  /* ============================================================
     切换工作区
     ============================================================
     在此之前工作区只能由 `--workspace` 或 URL 的 `?workspace=` 指定 ——
     也就是**只能从命令行换**。想开个新项目，得先建好目录、停服务、
     带 `--workspace` 重启。这不是"限制得比较保守"，是缺了一整块功能。

     现在后端每个端点本来就接受 `workspace` 参数（索引缓存也是按工作区
     分的），所以切换工作区**不需要重启服务**：前端换个参数、把属于旧目录
     的状态清掉、重画即可。 */

  function workspaceName(p) {
    var segs = String(p || "").split(/[\\/]/).filter(Boolean);
    return segs.length ? segs[segs.length - 1] : String(p || "");
  }

  /* 切换工作区。
     返回 true 表示真的换了（调用方据此决定是否提示），false 表示没变。
     **相同路径直接返回** —— 否则会出现"点一下工作区，标签全被关掉"这种
     莫名其妙的行为（用户只是想把选择器打开看看）。 */
  function switchWorkspace(next) {
    var p = String(next || "").trim();
    if (!p || p === state.workspace) return false;
    /* 旧工作区要回落到 wsInfo。
       `state.workspace` 为 null 的含义是"没显式指定，用服务端默认" ——
       如果只读 state.workspace，页面刚打开、还没在界面里切过的时候
       它就是 null，于是"上一份配置在哪个工作区"这个问题直接无从回答，
       沿用提示（以及一切靠 prev 判断的东西）会**静默不执行**。
       实测就是这样：切到新目录后什么都没提示，而源目录明明配着模型。 */
    var prev = state.workspace || (state.wsInfo && state.wsInfo.workspace) || "";

    state.workspace = p;
    persisted.workspace = p;
    save();

    /* URL 同步用 replaceState 而不是 pushState。
       用 push 的话用户按一次"后退"就回到旧工作区的地址，但界面状态
       （已清空的标签、已重载的树）不会跟着回退 —— 结果是
       「地址栏说 A、界面显示 B」。要么完整支持 popstate，要么不要 push。 */
    try {
      var u = new URL(window.location.href);
      u.searchParams.set("workspace", p);
      window.history.replaceState(null, "", u.toString());
    } catch (e) { /* 老浏览器 / file:// 下忽略 */ }

    /* ── 清掉一切属于旧目录的状态 ──
       不清的后果不是报错，是**显示错的内容**：旧项目的文件树、旧项目的
       标签、旧项目的改动统计，全都看起来像是新目录的。 */
    // 对话上下文也属于旧目录：换了项目还带着上一个项目的轨迹，
    // 等于让 agent 在 A 项目的历史里改 B 项目的代码。
    if (window.Harness && window.Harness.resetConversation) {
      window.Harness.resetConversation();
      if (window.refreshThreadBadge) window.refreshThreadBadge();
    }
    state.tabs = [];
    state.activeTab = null;
    dirtyFile = {};
    state.treeCache = {};
    state.fileCache = {};
    state.ctxRefs = [];
    state.wsInfo = null;
    state.wsInfoError = null;
    state.gitStatus = null;
    state.indexInfo = null;
    state.indexError = null;
    state.runs = null;
    state.runsError = null;
    state.detail = null;
    state.detailRun = null;
    state.detailError = null;
    state.replayVMs = null;
    state.replayRunId = null;
    state.replayDetail = null;
    state.streamMode = "live";
    state.gateResolved = null;
    termHistory.length = 0;
    liveVMs.length = 0;
    if (D.stream) D.stream.innerHTML = "";

    // 重画 + 按新工作区重新取一遍。顺序无关（各自 updae 自己的区块），
    // 但 loadEndpoint 要在重画之后 —— 它会把模型名写进状态栏与 Agent 面板。
    refreshConnectionUI();
    Backend.loadWorkspaceInfo();
    Backend.loadGitStatus();
    Backend.loadIndex(false);
    Backend.loadRuns();
    loadEndpoint();

    /* 配置是按工作区存的（<workspace>/.crucible/config.json），
       所以新目录必然是"还没配过"。用户刚配好就去开新项目，一进去看到
       "未配置"，第一反应是"我的 key 丢了"。

       **不自动沿用** —— 自动的话用户会以为自己在新项目里用的是 A 模型，
       其实沿用了上个项目的 B。只在旧目录确实有配置时，给一次显式选择。 */
    if (prev) suggestAdoptConfig(prev);
    return true;
  }

  function suggestAdoptConfig(prevWs) {
    if (!window.Harness || !window.Harness.isLive()) return;
    window.Harness.getConfig(state.workspace).then(function (now) {
      if (now && now.endpoint && now.endpoint.configured) return;  // 新目录自己有
      return window.Harness.getConfig(prevWs).then(function (old) {
        var ep = old && old.endpoint;
        if (!ep || !ep.configured) return;                          // 旧目录也没有，不打扰
        UI.toast({
          tone: "info",
          title: "这个目录还没有模型配置",
          desc: "配置是按工作区存的。可以把「" + workspaceName(prevWs)
                + "」那份（" + (ep.model || "已配置") + "）沿用过来。",
          duration: 10000,
          actions: [{
            label: "沿用",
            primary: true,
            onClick: function () {
              Backend.copyConfig(prevWs, state.workspace).then(function (r) {
                if (r && r.ok) {
                  applyEndpoint(r.endpoint);
                  UI.toast({ tone: "success", title: "已沿用模型配置",
                             desc: r.endpoint && r.endpoint.model || "", duration: 3200 });
                } else {
                  UI.toast({ tone: "warning", title: "没能沿用",
                             desc: (r && r.detail) || "原因未知", duration: 5200 });
                }
              });
            },
          }],
        });
      });
    }).catch(function (e) {
      /* 取配置失败不该影响切换本身，但**不能静默** ——
         这里静默过一次：整个"沿用配置"入口没出现，界面上看不出任何异常，
         只有截图对比才能发现少了一条提示。 */
      if (window.console) console.error("切换工作区后检查模型配置失败：", e);
    });
  }

  /* ============================================================
     目录选择器
     ============================================================
     为什么自己写而不用 <input type="file" webkitdirectory>：
     它只给**相对路径**，拿不到绝对路径，而工作区必须是一个绝对路径
     （后端所有端点都要 realpath）。所以只能让后端列目录。

     这个对话框是**唯一**能让用户换工作区的地方，所以几条底线：
     · 永远有退路（上一级、快捷位置、盘符、手输路径），不会卡在空目录里；
     · 不能选的目录**当场就说为什么**，不要等点了"选择"才报错；
     · Esc 分层（可能压在设置页上面）。 */
  function openFolderPicker(opts) {
    opts = opts || {};
    var startAt = opts.path || state.workspace
      || (state.wsInfo && state.wsInfo.workspace) || "";

    var back = el("div", "modalback");
    var dlg = el("div", "modal modal--picker");
    dlg.setAttribute("role", "dialog");
    dlg.setAttribute("aria-modal", "true");
    dlg.setAttribute("aria-label", "选择工作区目录");

    /* ── 头部 ── */
    var head = el("div", "picker__head");
    head.appendChild(el("h2", "picker__title", "打开文件夹"));
    var closeBtn = el("button", "iconbtn iconbtn--sm picker__close");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "关闭");
    closeBtn.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">'
      + '<path d="M5.5 5.5l9 9M14.5 5.5l-9 9" stroke="currentColor" '
      + 'stroke-width="1.7" stroke-linecap="round"/></svg>';
    head.appendChild(closeBtn);
    dlg.appendChild(head);

    /* ── 路径栏 ── */
    var bar = el("div", "picker__bar");
    var upBtn = el("button", "iconbtn iconbtn--sm");
    upBtn.type = "button";
    upBtn.setAttribute("aria-label", "上一级");
    upBtn.setAttribute("data-tip", "上一级（Backspace）");
    upBtn.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">'
      + '<path d="M10 15.5v-11M5.5 9L10 4.5 14.5 9" stroke="currentColor" '
      + 'stroke-width="1.7" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    bar.appendChild(upBtn);

    var pathInput = el("input", "picker__path");
    pathInput.type = "text";
    pathInput.spellcheck = false;
    pathInput.setAttribute("aria-label", "路径");
    pathInput.placeholder = "输入路径后回车";
    bar.appendChild(pathInput);

    var goBtn = el("button", "btn btn--sm", "转到");
    goBtn.type = "button";
    bar.appendChild(goBtn);
    dlg.appendChild(bar);

    /* 面包屑独立成行，不放在左栏。
       放在左栏时每一级各占一行（C: / Users / 34442 / Desktop / …），
       六行就把快捷位置顶下去了，而它跟路径栏表达的是同一件事。 */
    var crumbs = el("div", "picker__crumbs");
    dlg.appendChild(crumbs);

    /* ── 内容：左快捷位 / 右目录列表 ── */
    var body = el("div", "picker__body");
    var side = el("div", "picker__side");
    var list = el("div", "picker__list");
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", "目录");
    list.tabIndex = 0;
    body.appendChild(side);
    body.appendChild(list);
    dlg.appendChild(body);

    /* ── 底部 ── */
    var foot = el("div", "picker__foot");
    var stat = el("div", "picker__stat");
    foot.appendChild(stat);
    var actions = el("div", "picker__acts");

    var newBtn = el("button", "btn btn--sm", "新建文件夹");
    newBtn.type = "button";
    newBtn.setAttribute("data-tip", "在当前目录下建一个新文件夹，然后直接进去");
    actions.appendChild(newBtn);

    var cancelBtn = el("button", "btn btn--sm", "取消");
    cancelBtn.type = "button";
    actions.appendChild(cancelBtn);

    var pickBtn = el("button", "btn btn--sm btn--primary", "选择此文件夹");
    pickBtn.type = "button";
    actions.appendChild(pickBtn);

    foot.appendChild(actions);
    dlg.appendChild(foot);
    back.appendChild(dlg);

    /* ── 状态 ── */
    var data = null;        // 最近一次列目录的结果（唯一真相）
    var sel = -1;           // 列表高亮下标
    var closed = false;

    function close() {
      if (closed) return;
      closed = true;
      document.removeEventListener("keydown", onKey, true);
      if (releaseTrap) releaseTrap();
      UI.lockScroll(false);
      back.classList.add("modalback--out");
      setTimeout(function () {
        if (back.parentNode) back.parentNode.removeChild(back);
      }, 190);
    }

    function chdir(p) {
      pathInput.value = p || "";
      list.innerHTML = "";
      list.appendChild(el("div", "picker__loading", "读取中…"));
      Backend.fsList(p).then(function (d) {
        if (closed) return;
        data = d || {};
        sel = -1;
        // 用**后端归一化后的真实路径**回填输入框。
        // 不回填的话，用户会看到自己敲的或拼出来的形式（比如
        // `C:\Users\me\Desktop/newproj` 这种混用分隔符），
        // 而真正的路径不是那样 —— 地址栏与磁盘不一致最容易被误读。
        if (data.path) pathInput.value = data.path;
        renderCrumb(d);
        renderList();
        renderStat();
      });
    }

    function renderCrumb(d) {
      var host = side;
      host.innerHTML = "";

      function group(title) {
        var g = el("div", "picker__group");
        g.appendChild(el("div", "picker__grouptitle", title));
        host.appendChild(g);
        return g;
      }

      if (d && d.drives && d.drives.length) {
        var gd = group("磁盘");
        d.drives.forEach(function (dr) {
          var b = el("button", "picker__quick", String(dr).replace(/\\$/, ""));
          b.type = "button";
          b.setAttribute("data-tip", dr);
          b.addEventListener("click", function () { chdir(dr); });
          gd.appendChild(b);
        });
      }
      if (d && d.shortcuts && d.shortcuts.length) {
        var gs = group("常用位置");
        d.shortcuts.forEach(function (s) {
          var b = el("button", "picker__quick", s.name);
          b.type = "button";
          b.setAttribute("data-tip", s.path);
          b.addEventListener("click", function () { chdir(s.path); });
          gs.appendChild(b);
        });
      }
      // 面包屑：路径栏下方一行，逐级可点 —— 比"点上一级点十次"好用
      var crumbPath = (d && d.path) || pathInput.value || "";
      crumbs.innerHTML = "";
      if (crumbPath) {
        var segs = String(crumbPath).split(/[\\/]/).filter(Boolean);
        var acc = "";
        segs.forEach(function (seg, i) {
          acc += (i === 0 && /^[A-Za-z]:$/.test(seg) ? seg + "\\" : seg + "/");
          if (i > 0) crumbs.appendChild(el("span", "picker__crumbsep", "›"));
          var b = el("button", "picker__crumb", seg);
          b.type = "button";
          b.setAttribute("data-tip", acc);
          // 点击任意一级 = 跳到那一级
          (function (target) {
            b.addEventListener("click", function () { chdir(target); });
          })(acc);
          crumbs.appendChild(b);
        });
      }
    }

    function renderList() {
      list.innerHTML = "";
      if (!data) return;
      if (data.error) {
        var e = el("div", "picker__empty");
        e.appendChild(el("div", "picker__emptytitle",
          data.error === "not_found" ? "这个路径不存在"
            : data.error === "permission_denied" ? "没有权限读取这个目录"
            : data.error === "not_a_dir" ? "这是一个文件，不是目录"
            : "读不了这个目录"));
        e.appendChild(el("div", "picker__emptyhint",
          data.detail || "换一个目录试试，或者点左边的快捷位置。"));
        list.appendChild(e);
        return;
      }
      var dirs = data.entries || [];
      if (!dirs.length) {
        var em = el("div", "picker__empty");
        em.appendChild(el("div", "picker__emptytitle", "这个目录下没有子目录"));
        em.appendChild(el("div", "picker__emptyhint",
          "它本身可以当作工作区 —— 空目录也算一个项目。点「选择此文件夹」即可。"));
        list.appendChild(em);
        return;
      }
      dirs.forEach(function (d, i) {
        var row = el("button", "picker__item");
        row.type = "button";
        row.setAttribute("role", "option");
        row.setAttribute("aria-selected", "false");
        row.appendChild(svg(ICONS.dirClosed, "picker__icon"));
        var nameEl = el("span", "picker__name", d.name);
        row.appendChild(nameEl);
        if (d.has_git) {
          row.appendChild(el("span", "picker__tag picker__tag--git", "git"));
        } else if (d.project) {
          row.appendChild(el("span", "picker__tag", d.project));
        }
        row.setAttribute("data-tip", d.path);
        // 单击进入（而不是"单击选中、双击进入"）：这个对话框只干一件事，
        // 多一层哑状态只会让人多点一次。选中靠键盘 ↑↓。
        row.addEventListener("click", function () { chdir(d.path); });
        row.addEventListener("mouseenter", function () { setSel(i); });
        list.appendChild(row);
      });
      if (data.truncated) {
        list.appendChild(el("div", "picker__loading",
          "目录太多，只列出前 " + dirs.length + " 个。可以手输路径直达。"));
      }
    }

    function setSel(i) {
      var rows = list.querySelectorAll(".picker__item");
      if (!rows.length) { sel = -1; return; }
      if (i < 0) i = rows.length - 1;
      if (i >= rows.length) i = 0;
      sel = i;
      Array.prototype.forEach.call(rows, function (r, k) {
        r.classList.toggle("is-sel", k === sel);
        r.setAttribute("aria-selected", k === sel ? "true" : "false");
      });
      rows[sel].scrollIntoView({ block: "nearest" });
    }

    function renderStat() {
      stat.className = "picker__stat";
      upBtn.disabled = !(data && data.parent);
      if (!data) { stat.textContent = ""; return; }
      if (data.error) { stat.textContent = ""; pickBtn.disabled = true; return; }

      var bits = [];
      bits.push((data.count || 0) + " 个子目录");
      // 隐藏目录被跳过要报个数 —— 静默省略会让人以为磁盘上就这么多东西
      if (data.skipped_hidden) {
        bits.push(data.skipped_hidden + " 个隐藏目录未列出");
      }
      if (data.has_git) bits.push("git 仓库");
      else if (data.project) bits.push("含 " + data.project);
      else bits.push("未检测到项目标志");

      // 能不能当工作区：浏览时就说，不要等点"选择"才报错
      var blocked = data.blocked_reason;
      if (blocked) {
        pickBtn.disabled = true;
        stat.classList.add("is-bad");
        stat.textContent = blocked;
        return;
      }
      pickBtn.disabled = false;
      if (data.writable === false) {
        stat.classList.add("is-warn");
        bits.push("不可写 —— agent 的写操作会失败");
      }
      stat.textContent = bits.join(" · ");
    }

    /* ── 新建文件夹：内联输入，不叠第二层模态 ──
       叠一层的话 Esc 分层又要多一份判断（确认框、设置页、选择器三层），
       而这里只需要一个名字。 */
    function startNewFolder() {
      if (list.querySelector(".picker__new")) {
        list.querySelector(".picker__new input").focus();
        return;
      }
      var row = el("div", "picker__new");
      var input = el("input", "picker__newinput");
      input.type = "text";
      input.placeholder = "新文件夹名";
      input.setAttribute("aria-label", "新文件夹名");
      var ok = el("button", "btn btn--sm btn--primary", "创建");
      ok.type = "button";
      var no = el("button", "btn btn--sm", "取消");
      no.type = "button";
      row.appendChild(input);
      row.appendChild(ok);
      row.appendChild(no);
      list.insertBefore(row, list.firstChild);
      input.focus();

      var errEl = null;
      function showErr(msg) {
        if (!errEl) {
          errEl = el("div", "picker__newerr");
          row.appendChild(errEl);
        }
        errEl.textContent = msg;
      }
      function submit() {
        var name = String(input.value || "").trim();
        if (!name) { input.focus(); return; }
        if (!Backend.available()) {
          showErr("后端未连接，建不了目录。");
          return;
        }
        ok.disabled = true;
        // 复用工作区的新建端点：workspace = 当前浏览目录，path = 名字。
        // **不接受带分隔符的名字** —— 那会让"在这建一个文件夹"变成
        // "在别处建一条路径"。（后端也会拒，这里先拦是为了能就地给出提示。）
        if (/[\\/]/.test(name)) {
          ok.disabled = false;
          showErr("名字里不能带斜杠。要分层次的话，先建外层，进去再建。");
          return;
        }
        Backend.createEntry(data.path, name, "dir").then(function (r) {
          ok.disabled = false;
          if (r && r.ok) {
            var np = String(data.path).replace(/[\\/]+$/, "") + "/" + name;
            chdir(np);       // 建完直接进去 —— 建好还要再找一遍太绕
            UI.toast({ tone: "success", title: "已创建 " + name, duration: 2400 });
          } else {
            // 失败原因写在输入框下面，不弹 toast：用户接下来要改的就是这个名字
            showErr((r && r.body && r.body.detail) || "创建失败，原因未知。");
            input.focus();
          }
        });
      }
      ok.addEventListener("click", submit);
      no.addEventListener("click", function () { row.parentNode && row.parentNode.removeChild(row); });
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); submit(); }
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          row.parentNode && row.parentNode.removeChild(row);
        }
      });
    }

    /* ── 确认选择 ── */
    function confirmPick() {
      if (!data || data.error || !data.usable) return;
      var target = data.path;
      var ok = switchWorkspace(target);
      close();
      if (ok) {
        UI.toast({
          tone: "success",
          title: "工作区已切到 " + workspaceName(target),
          desc: target,
          duration: 3600,
        });
      }
    }

    /* ── 键盘 ── */
    function onKey(e) {
      // Esc 分层：这个对话框可能压在设置页上面。
      // 判据与其他模态一致 —— 只有自己是最后一个时才处理。
      var backs = document.querySelectorAll(".modalback");
      if (backs.length && backs[backs.length - 1] !== back) return;
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        close();
        return;
      }
      // 焦点在输入框里时不抢方向键（要改路径文字）
      var tag = (e.target && e.target.tagName) || "";
      if (tag === "INPUT") {
        if (e.key === "Enter") {
          e.preventDefault();
          chdir(pathInput.value.trim());
        }
        return;
      }
      if (e.key === "ArrowDown") { e.preventDefault(); setSel(sel + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setSel(sel <= 0 ? -1 : sel - 1); }
      else if (e.key === "Enter") {
        var rows = list.querySelectorAll(".picker__item");
        if (sel >= 0 && rows[sel]) {
          e.preventDefault();
          rows[sel].click();
        }
      } else if (e.key === "Backspace") {
        e.preventDefault();
        if (data && data.parent) chdir(data.parent);
      }
    }

    document.addEventListener("keydown", onKey, true);
    var releaseTrap = UI.trapFocus(dlg);
    UI.lockScroll(true);
    back.addEventListener("mousedown", function (e) {
      if (e.target === back) close();
    });
    closeBtn.addEventListener("click", close);
    cancelBtn.addEventListener("click", close);
    pickBtn.addEventListener("click", confirmPick);
    newBtn.addEventListener("click", startNewFolder);
    upBtn.addEventListener("click", function () {
      if (data && data.parent) chdir(data.parent);
    });
    goBtn.addEventListener("click", function () { chdir(pathInput.value.trim()); });

    document.body.appendChild(back);
    chdir(startAt);
    // 焦点放在列表上而不是路径框：进来第一件事通常是"看子目录 / ↑↓ 选"，
    // 而不是重新敲一遍路径。
    setTimeout(function () { try { list.focus(); } catch (e) { /* 忽略 */ } }, 30);
  }

  /* ============================================================
     启动
     ============================================================ */
  function restorePersisted() {
    document.documentElement.style.setProperty("--rail-w", (persisted.railW || 248) + "px");
    if (persisted.railCollapsed) D.shell.classList.add("is-rail-collapsed");
    if (persisted.agentHidden && !state.agentDrawer) D.shell.classList.add("is-agent-hidden");
    if (persisted.termH) document.documentElement.style.setProperty("--terminal-h", persisted.termH + "px");
    var btn = $('.actbar__btn[data-view="' + state.railView + '"]');
    if (btn) {
      $$(".actbar__btn[data-view]").forEach(function (x) {
        x.classList.remove("is-on");
        x.setAttribute("aria-pressed", "false");
      });
      btn.classList.add("is-on");
      btn.setAttribute("aria-pressed", "true");
    }
  }

  /* ============================================================
     会话桥接：把 harness 的真实事件接到同一套渲染器
     ============================================================ */
  var liveVMs = [];   // 本次真实运行的全部视图模型（重画时的唯一真相）

  function attachHarness() {
    if (!window.Harness) return;
    window.Harness.attach({
      push: function (vm) {
        // 收到真实事件说明"现在正在跑"—— 立即退出回放态。
        // 不退出的话，会把一个新运行的事件追加到历史回放的画面上，
        // 那是最糟的一种混淆（两条运行的卡片混在一起）。
        if (state.streamMode !== "live") {
          if (state.streamMode === "replay") {
            D.stream.innerHTML = "";
          }
          state.streamMode = "live";
          state.replayRunId = null;
          state.replayVMs = null;
          state.replayDetail = null;
          liveVMs = [];
        }
        liveVMs.push(vm);
        var node = buildEvent(vm, "live" + liveVMs.length);
        if (!node) return;
        node.classList.add("ev--live");
        D.stream.appendChild(node);
        D.stream.scrollTop = D.stream.scrollHeight;
        if (refreshStreamFade) refreshStreamFade();
        applyFilter();
        expandLiveCard(node);
      },
      refresh: function () {
        // tool.output 已就地合并进对应 requested 卡片 —— 从同一份 liveVMs 重画，
        // 保证"渲染出来的"与"模型持有的"永远一致（避免两套真相）。
        rerenderLive();
      },
      onDone: function (data) {
        if (data && data.status) UI.announce("运行结束：" + data.status);
        // 运行结束 → 拉这次的真实用量，并刷新运行列表
        // （新 run 已经在落盘里了，刷新后「会话历史」能看到它）
        var rid = window.Harness.runId && window.Harness.runId();
        if (rid) {
          Backend.loadUsage(rid, state.workspace, "live");
        }
        Backend.loadChainSummary();
        Backend.loadRuns();
        Backend.loadGitStatus();
        Backend.loadIndex(false);
        // 工作区可能被改动了 → 让文件树与工作区概要失效重读。
        // **不清缓存的话，改完文件后树里还是旧的** ——
        // 那会让人以为 agent 没生效。
        Backend.invalidateWorkspace();
      },
    });
  }

  function rerenderLive() {
    $$(".ev--live", D.stream).forEach(function (n) { n.remove(); });
    var frag = document.createDocumentFragment();
    liveVMs.forEach(function (vm, i) {
      var node = buildEvent(vm, "live" + (i + 1));
      if (node) {
        node.classList.add("ev--live");
        frag.appendChild(node);
        expandLiveCard(node);
      }
    });
    D.stream.appendChild(frag);
    D.stream.scrollTop = D.stream.scrollHeight;
    if (refreshStreamFade) refreshStreamFade();
    applyFilter();
  }

  /* 真实的工具输出默认展开 —— 用户要看到 harness 实际做了什么 */
  function expandLiveCard(node) {
    var head = node.querySelector(".tool__head");
    var body = node.querySelector(".tool__body");
    if (head && body) {
      head.setAttribute("aria-expanded", "true");
      body.hidden = false;
      var ml = node.querySelector(".tool__metaline");
      if (ml) ml.hidden = true;
    }
  }

  function init() {
    cacheDom();
    initTheme();
    restorePersisted();

    sessionStart = Date.now();
    renderRail();
    renderTabs();
    renderEditor();
    renderTerminal();
    renderPlan();
    renderStream();
    renderCtxRefs();
    bind();

    refreshStreamFade = bindFade(D.stream, D.stream);
    refreshTermFade = bindFade(D.termScroll, D.termScroll);

    UI.initTooltips(document.body);
    applyFilter();
    updateTabOverflow();

    // 探测 harness 后端：通了就把这一页从「原型」切到「真实运行」。
    // 探测失败（file:// 直接打开）时静默维持 mock 演示，不打扰用户。
    attachHarness();
    if (window.Harness) {
      window.Harness.probe().then(function (live) {
        if (!live) return;
        var chip = $(".ctxchip");
        if (chip) {
          chip.setAttribute("title", "已连接 harness 后端：真实运行可用");
          chip.classList.add("ctxchip--live");
          var dot = chip.querySelector(".ctxchip__dot");
          if (dot) dot.style.background = "var(--jade)";
        }
        var hint = $(".dock__hint");
        if (hint) {
          hint.textContent = "";
          hint.appendChild(el("span", null, "已连接 harness · 发送即真实运行"));
        }
        UI.announce("已连接 harness 后端");
        // 后端在线 → 立即把"真实数据"拉起来（运行列表 + 工具清单 + 哈希链概览）。
        // 三个都是只读查询，并发发出，谁先回来谁先渲染。
        Backend.loadChainSummary();
        Backend.loadRuns();
        if (state.railView === "mcp") Backend.loadTools();
        // 工作区真实数据：概要（状态栏）+ 文件树根层。
        // 三个只读查询并发发出 —— 串行会让首屏白等三倍。
        Backend.loadWorkspaceInfo();
        Backend.loadTree("");
        Backend.loadGitStatus();
        Backend.loadIndex(false);
        uiHooks.updateMeter();
        // ★ 探针是异步的，首屏渲染早已跑完 —— 必须把"依赖连接状态"的文案
        //   重画一遍。否则执行轨迹面板会一直挂着"后端未连接。这是演示占位"，
        //   而底部提示写的是"已连接 harness"。**同一屏两个结论相反的文案**
        //   比只显示一个错的更糟：用户没法判断该信哪个。
        refreshConnectionUI();
        // 把已保存的模型接入配置反映到界面。
        // 若未配置，状态栏模型会显示"未配置"并在 title 里提示点此接入 ——
        // 不静默显示一个假模型名，否则用户会以为真的在用那个模型。
        return loadEndpoint().then(function (ep) {
          if (ep && !ep.configured) {
            var h = $(".dock__hint");
            if (h) {
              h.textContent = "";
              h.appendChild(el("span", null, "已连接 harness · 点左下角设置接入模型"));
            }
            return;
          }
          // 演示用：`?autorun=1&prompt=...` 打开即自动发起一次真实运行。
          // 这是给"演示/验收"用的显式入口 —— 默认绝不自动跑，
          // 否则每次打开页面都会产生一次真实副作用。
          if (urlParams.autorun === "1" && urlParams.prompt) {
            var ta = $("#dock-input");
            if (ta) {
              ta.value = urlParams.prompt;
              ta.dispatchEvent(new Event("input", { bubbles: true }));
              $("#btn-dock-send").click();
            }
          }
        });
      });
    }

    // 首屏骨架淡出：数据已就绪
    var boot = $("#boot");
    if (boot) {
      var bt = $("#boot-txt");
      if (bt) bt.textContent = "工作区已就绪";
      setTimeout(function () {
        boot.classList.add("boot--out");
        setTimeout(function () { boot.hidden = true; }, 300);
      }, 260);
    }

    // 打开时的引导提示：只提示一次，之后不再打扰
    try {
      if (!localStorage.getItem("crucible.seen.v1")) {
        setTimeout(function () {
          UI.toast({
            tone: "info",
            title: "欢迎用 Crucible",
            desc: "按 Ctrl+K 打开命令面板，? 查看全部快捷键。",
            duration: 9000,
            actions: [
              { label: "看快捷键", primary: true, onClick: function () { $("#btn-help").click(); } },
            ],
          });
          localStorage.setItem("crucible.seen.v1", "1");
        }, 900);
      }
    } catch (e) { /* 隐私模式下忽略 */ }
  }

  // 探针入口：只在通过 ?probe=1 打开时暴露，正常使用时不挂任何全局。
  // 目的：让几何/交互探针走**真实的渲染代码**，而不是复制一份渲染逻辑 ——
  // 复制出来的探针会验证它自己，而不是产品。
  try {
    if (/[?&]probe=1\b/.test(location.search)) {
      window.__probeBuildCheck = buildCheck;
    }
  } catch (e) { /* file:// 下 location.search 可用，出错也不该影响加载 */ }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
