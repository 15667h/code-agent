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
    mobile: window.matchMedia("(max-width: 1024px)").matches,
    ctxRefs: [],
    filter: persisted.filter || "all",
    focusIdx: 0,             // 文件树的键盘游标
    renaming: null,          // 正在内联重命名的路径
    endpoint: null,          // 当前模型接入配置（公开形态，不含明文 key）
    // 只从 URL 取。曾经写成 `urlParams.workspace || state.workspace || null`，
    // 那是在 state 自己的初始化表达式里引用 state —— `var` 提升让此刻的 state
    // 是 undefined，于是抛 TypeError，**整个 app.js 从这一行起全部不执行**。
    // 表现为「页面看着正常、所有交互都是死的」，极难定位。
    workspace: urlParams.workspace || null,

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

  /* ---------- 主题：跟随系统偏好，同时允许手动覆盖 ---------- */
  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    var mc = document.querySelector('meta[name="theme-color"]');
    if (mc) mc.setAttribute("content", t === "light" ? "#f2f0eb" : "#10141a");
    var b = $("#btn-theme");
    if (b) b.setAttribute("aria-label", t === "light" ? "切换到深色主题" : "切换到浅色主题");
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
          UI.contextMenu(ev.clientX, ev.clientY, [
            { label: "打开", icon: ICONS.file, onClick: function () { openTab(en.path); } },
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
      // 真实搜索：走 /api/workspace/search，后端复用 agent 的 grep 工具。
      // 三态（未搜 / 搜索中 / 有结果或明确空）都要分得清。
      var wrap = el("div", "railinput");
      wrap.appendChild(svg(ICONS.search));
      var inp = el("input");
      inp.type = "search";
      inp.placeholder = "在工作区内搜索…";
      inp.setAttribute("aria-label", "在工作区内搜索");
      inp.value = state.searchQuery || "";
      wrap.appendChild(inp);
      body.appendChild(wrap);

      if (!Backend.available()) {
        body.appendChild(emptyHint("后端未连接",
          "搜索由后端复用 agent 的 grep 工具执行。"));
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
        state.searchHits = null;      // 搜索中
        state.searchError = null;
        renderRail();
        // 重绘后输入框是新的，恢复焦点与光标
        window.Harness.workspaceSearch(state.workspace, q)
          .then(function (r) {
            if (r && r.error) {
              state.searchHits = { error: r.error, detail: r.detail || "" };
            } else {
              state.searchHits = r;
            }
            renderRail();
            var ni = $("#rail-body input[type=search]");
            if (ni) { ni.focus(); ni.setSelectionRange(ni.value.length, ni.value.length); }
          })
          .catch(function (e) {
            state.searchError = String(e && e.message || e);
            state.searchHits = null;
            renderRail();
          });
      };

      var timer = null;
      inp.addEventListener("input", function () {
        if (timer) clearTimeout(timer);
        var v = inp.value;
        timer = setTimeout(function () { doSearch(v); }, 260);
      });
      inp.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { if (timer) clearTimeout(timer); doSearch(inp.value); }
      });

      // 首次进入且没有查过 → 提示怎么用（不自动搜，避免打开就触发全库扫描）
      if (state.searchHits === undefined || state.searchHits === null) {
        if (!state.searchQuery) {
          body.appendChild(emptyHint("输入关键词开始搜索",
            "搜索复用 agent 使用的 grep 工具，逐文件匹配内容。"));
          return;
        }
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
      // **说明搜索范围有限**：grep 是逐文件文本匹配，没有索引，
      // 也不理解语义。不写这句会让人以为这是全文检索。
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
          state.railView = "explorer";
          $$(".actbar__btn[data-view]").forEach(function (x2) {
            x2.classList.toggle("is-on", x2.dataset.view === "explorer");
            x2.setAttribute("aria-pressed", x2.dataset.view === "explorer" ? "true" : "false");
          });
          renderRail();
          openRailIfMobile();
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
        + (f.truncated ? " · **已截断（只显示前 " + lines.length + " 行）**" : "");
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
      termAppend({ t: "dim", text: "这是**受控只读终端**：允许 ls/cat/head/tail/grep/find/wc/"
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
      offline_script: urlParams.offline || persisted.offlineScript,
    }).then(function (info) {
      pending.remove();
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
    $$(".actbar__btn[data-view]").forEach(function (b) {
      b.addEventListener("click", function () {
        var v = b.dataset.view;
        var already = b.classList.contains("is-on");
        $$(".actbar__btn[data-view]").forEach(function (x) {
          x.classList.remove("is-on");
          x.setAttribute("aria-pressed", "false");
        });
        b.classList.add("is-on");
        b.setAttribute("aria-pressed", "true");
        state.railView = v;
        persisted.activeView = v;
        save();
        state.focusIdx = 0;
        renderRail();
        // 窄屏下点活动栏把侧栏拉开；再点同一个则收起
        if (state.mobile && !already) openRail();
        UI.announce(RAIL_TITLES[v] || v);
      });
    });

    // ---------- 侧栏折叠（记忆状态） ----------
    $("#btn-collapse-rail").addEventListener("click", function () {
      if (state.mobile) { closeRail(); return; }
      var collapsed = D.shell.classList.toggle("is-rail-collapsed");
      persisted.railCollapsed = collapsed;
      save();
      UI.announce(collapsed ? "侧栏已折叠" : "侧栏已展开");
    });

    // ---------- 侧栏宽度拖拽 ----------
    (function initRailResize() {
      var h = $("#rail-resize");
      if (!h) return;
      // 指针按下即拖，不需要先点准 1px 的边
      h.addEventListener("pointerdown", function (e) {
        if (state.mobile) return;
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
      btnSettings.addEventListener("click", function () { openModelSettings(); });
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

      cmds.push({ label: "键盘快捷键总览", sub: "查看全部快捷键", group: "帮助", icon: ICONS.terminal, keys: ["?"], onRun: openShortcuts });

      cmds.push({ label: "显示 / 隐藏 Agent 面板", group: "视图", icon: ICONS.play, keys: ["Ctrl", "B"],
        onRun: function () { setAgentOpen(!agentIsOpen()); } });
      cmds.push({ label: "显示 / 隐藏终端", group: "视图", icon: ICONS.terminal, keys: ["Ctrl", "`"],
        onRun: function () { toggleTerminal(); } });
      cmds.push({ label: "折叠 / 展开侧栏", group: "视图", icon: ICONS.dirClosed,
        onRun: function () { $("#btn-collapse-rail").click(); } });

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
    $("#btn-model").addEventListener("click", function (e) {
      var r = this.getBoundingClientRect();
      UI.contextMenu(r.left, r.top - 96, [
        { label: "gpt-5.4-codex", icon: ICONS.play, onClick: function () { UI.toast({ tone: "info", title: "模型：gpt-5.4-codex", desc: "主推理模型，用于实际编码。", duration: 3000 }); } },
        { label: "gpt-5.4-mini", icon: ICONS.play, onClick: function () { UI.toast({ tone: "info", title: "模型：gpt-5.4-mini", desc: "轻量，适合子代理与目标检查。", duration: 3000 }); } },
        { sep: true },
        { label: "查看 token 消耗", icon: ICONS.clock, onClick: showTokenDetail },
      ]);
    });
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
    $("#btn-diag").addEventListener("click", function (e) {
      var r = this.getBoundingClientRect();
      // 原来这里列了"reconnect.ts:22 未使用的常量"之类 —— 全是编的，
      // 而且指向一个不存在的文件。**harness 不做 lint / 类型检查**，
      // 所以没有真实的诊断可列。这里如实说明，而不是继续编两条。
      UI.contextMenu(r.left - 100, r.top - 84, [
        { label: "无诊断来源", icon: ICONS.help, disabled: true },
        { sep: true },
        { label: "harness 不做 lint/类型检查", icon: ICONS.book, disabled: true },
        { label: "看代码改动 → 会话历史", icon: ICONS.file, onClick: function () { goView("sessions"); } },
      ]);
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
          + "\n这是**单次运行**的计量，不是上下文窗口占用。",
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

    function refreshIndex() {
      // 这个 harness **没有代码索引**（搜索走 grep 工具）。
      // 所以这里不假装"正在重建索引"，而是如实说明并重新读一次工作区概要。
      if (!Backend.available()) {
        UI.toast({ tone: "warning", title: "后端未连接",
                   desc: "工作区信息由 harness 提供。", duration: 5000 });
        return;
      }
      Backend.loadWorkspaceInfo().then(function (info) {
        if (!info) {
          UI.toast({ tone: "danger", title: "读取工作区失败",
                     desc: state.wsInfoError || "", duration: 5000 });
          return;
        }
        UI.toast({
          tone: "info", duration: 6000,
          title: "工作区：" + info.files + " 个文件 · " + info.dirs + " 个目录",
          desc: "**没有代码索引** — " + info.index_note
            + "（扫描上限：深度 " + info.scan.max_depth
            + "、最多 " + info.scan.max_files + " 个文件）",
        });
      });
    }

    /* 状态栏：把真实工作区信息写回去。
       mock 版写着"已索引 · 1,284 文件 · 18.6 万 token" —— 全是编的。
       现在只显示能核实的量（文件数、分支），没有的就说没有。 */
    function renderStatusbar() {
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
      var il = $("#status-index-label");
      if (il) {
        il.textContent = info ? ((info.truncated ? "≥" : "") + info.files + " 文件")
                              : (Backend.available() ? "读取中" : "未连接");
      }
      var dot = $("#btn-index .statusitem__dot");
      if (dot && info) dot.className = "statusitem__dot statusitem__dot--ok";
    }

    // 挂到外层钩子：Backend 在 init 之后才会用到它们，
    // 而且只有挂上之后调用才有效 —— 未挂时是空操作，不会在 DOM 未就绪时炸。
    uiHooks.updateMeter = updateMeter;
    uiHooks.renderStatusbar = renderStatusbar;
    uiHooks.renderBranchChip = renderBranchChip;

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
      if (state.mobile) D.shell.classList.toggle("is-agent-open", open);
      else D.shell.classList.toggle("is-agent-hidden", !open);
      var t = $("#btn-agent-toggle");
      if (t) t.setAttribute("aria-expanded", open ? "true" : "false");
      persisted.agentHidden = !open;
      save();
      UI.announce(open ? "Agent 面板已展开" : "Agent 面板已收起");
    }
    function agentIsOpen() {
      return state.mobile
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
        if (state.mobile) { setAgentOpen(false); closeRail(); return; }
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

    function submit() {
      var v = ta.value;
      if (!v.trim()) return;
      sendPrompt(v);
      ta.value = "";
      autoGrow();
      $("#btn-dock-send").disabled = true;
      hideMention();
    }

    // ---------- 新建文件 ----------
    $("#btn-newfile").addEventListener("click", function () {
      var tree = D.railBody.querySelector(".filetree");
      if (!tree || state.railView !== "explorer") { goView("explorer"); }
      UI.contextMenu(
        D.railBody.getBoundingClientRect().left + 24,
        D.railBody.getBoundingClientRect().top + 60,
        [
          { label: "新建文件", icon: ICONS.plus, onClick: function () {
              UI.toast({ tone: "info", title: "新建文件", desc: "演示环境：先给一个名字，再打开空标签。", duration: 4000,
                actions: [{ label: "输入名字", primary: true, onClick: function () {
                  var n = window.prompt("新文件名");
                  if (!n) return;
                  openTab("src/" + n);
                  UI.toast({ tone: "ok", title: "已创建 " + n, desc: "文件尚未落盘（演示环境）。", duration: 3600 });
                } }] });
            } },
          { label: "新建目录", icon: ICONS.dirClosed, onClick: function () {
              UI.toast({ tone: "info", title: "新建目录", desc: "演示环境未落盘。", duration: 3000 });
            } },
          { sep: true },
          { label: "刷新", icon: ICONS.play, onClick: refreshIndex },
        ]
      );
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
    var mq = window.matchMedia("(max-width: 1024px)");
    var onChange = function (m) {
      state.mobile = m.matches;
      D.shell.classList.remove("is-agent-open");
      closeRail();
    };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }

  function goView(v) {
    var btn = $('.actbar__btn[data-view="' + v + '"]');
    if (btn) btn.click();
  }

  function expandAll(open) {
    Object.keys(treeState).forEach(function (k) {
      if (k.indexOf("0:") === 0 || k.indexOf("1:") === 0) treeState[k] = open;
    });
    renderRail();
    UI.toast({ tone: "info", title: open ? "已全部展开" : "已全部折叠", duration: 2200 });
  }

  function openRail() { D.shell.classList.add("is-rail-open"); }
  function closeRail() { D.shell.classList.remove("is-rail-open"); }
  function openRailIfMobile() { /* 窄屏点文件后保留侧栏，方便连续操作 */ }

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

  function openModelSettings() {
    if (!window.Harness || !window.Harness.isLive()) {
      UI.toast({
        tone: "warning",
        title: "后端未连接",
        desc: "模型接入配置由 harness 后端持久化。启动 `python harness/server.py` 后刷新页面即可。",
        duration: 6000,
      });
      return;
    }

    var ws = state.workspace;
    var dlg = null, api = null;
    var current = { base_url: "", model: "", has_key: false, api_key_masked: "" };

    // 先读现有配置，再用它作为表单初值 —— 让「改一个字段」成为默认操作
    window.Harness.getConfig(ws).then(function (r) {
      var ep = (r && r.endpoint) || {};
      current = ep;
      dlg = UI.formDialog({
        title: "模型接入",
        desc: "填这三项即可接任意 OpenAI 兼容端点（含中转）。",
        submitLabel: "保存",
        focusName: "base_url",
        extraActions: [{ act: "test", label: "测试连接" }],
        fields: [
          {
            name: "base_url", label: "接口地址", type: "text", required: true,
            placeholder: "https://api.deepseek.com/v1",
            autocomplete: "off", spellcheck: false,
            value: ep.base_url || "",
            hint: "填到 /v1 即可；末尾带不带 /chat/completions 都行，会自动补。",
          },
          {
            name: "api_key", label: "API Key", type: "password", required: !ep.has_key,
            placeholder: ep.has_key ? ("已保存：" + (ep.api_key_masked || "••••") + "（留空则沿用）") : "sk-…",
            autocomplete: "new-password", spellcheck: false,
            value: "",
            hint: ep.has_key
              ? "已有一份保存的服务端。这里留空即沿用，填入则替换。"
              : "只存在服务端的 .crucible/config.json（已在 .gitignore），不会回传明文。",
          },
          {
            name: "model", label: "模型名称", type: "text", required: true,
            placeholder: "deepseek-chat",
            autocomplete: "off", spellcheck: false,
            value: ep.model || "",
            hint: "该端点上可用的模型 id，原样填即可。",
          },
        ],
        onSubmit: submit,
      });
      api = dlg;
      wireLiveHints();
    }).catch(function (err) {
      UI.toast({ tone: "danger", title: "读取配置失败", desc: String(err.message || err), duration: 6000 });
    });

    /* ---------- 表单内：地址预览 + 测试连接 ---------- */
    function wireLiveHints() {
      var root = document.querySelector(".modal--form");
      if (!root) return;
      var baseInput = root.querySelector('input[name="base_url"]');
      var keyInput = root.querySelector('input[name="api_key"]');
      var modelInput = root.querySelector('input[name="model"]');
      var extra = root.querySelector(".fd__extra");
      var testBtn = root.querySelector('[data-act="test"]');

      // —— 实时地址预览：把「最终会请求什么」摆在用户眼前 ——
      var preview = document.createElement("p");
      preview.className = "field__hint";
      preview.style.marginTop = "6px";
      baseInput.parentElement.parentElement.appendChild(preview);
      function refreshPreview() {
        var full = normalizeBase(baseInput.value);
        preview.textContent = full ? ("将请求：" + full) : "";
        preview.hidden = !full;
      }
      baseInput.addEventListener("input", refreshPreview);
      refreshPreview();

      // —— 填入新 key 时解除"必填"约束（留空=沿用） ——
      keyInput.addEventListener("input", function () {
        if (dlg) dlg.clearErrors();
      });

      if (testBtn) {
        testBtn.addEventListener("click", function () {
          var payload = collect(testBtn);
          extra.innerHTML = "";
          extra.appendChild(checkBar("busy", "正在连接该端点…", ""));
          testBtn.disabled = true;
          window.Harness.testConfig(ws, payload, 20).then(function (rep) {
            testBtn.disabled = false;
            extra.innerHTML = "";
            extra.appendChild(checkBar(
              rep.ok ? "ok" : "bad",
              rep.message || (rep.ok ? "连通" : "失败"),
              (rep.url ? rep.url : "") + (rep.elapsed_s != null ? ("　" + rep.elapsed_s + "s") : "")
            ));
            if (rep.ok) UI.announce("连接测试通过");
            else UI.announce("连接测试未通过");
          }).catch(function (err) {
            testBtn.disabled = false;
            extra.innerHTML = "";
            extra.appendChild(checkBar("bad", String(err.message || err), ""));
          });
        });
      }

      function collect(testBtn) {
        // 测试用当前表单值；key 留空则沿用已保存的（服务端会合并）
        var v = {
          base_url: (baseInput.value || "").trim(),
          model: (modelInput.value || "").trim(),
        };
        var k = (keyInput.value || "").trim();
        if (k) v.api_key = k;
        return v;
      }
      void collect;
    }

    function checkBar(kind, text, meta) {
      var box = el("div", "fdcheck fdcheck--" + kind);
      var icon = kind === "ok"
        ? '<path d="M3.5 8.5l3 3 6-6.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
        : '<path d="M8 2.5l6 11H2z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M8 6.5v3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>';
      box.appendChild(svg(icon));
      var body = el("div");
      body.appendChild(document.createTextNode(text));
      if (meta) {
        var m = el("span", "fdcheck__meta", meta);
        body.appendChild(m);
      }
      box.appendChild(body);
      return box;
    }

    /* ---------- 提交 ---------- */
    function submit(v, formApi) {
      formApi.busy(true, "保存中…");
      var payload = {
        base_url: (v.base_url || "").trim(),
        model: (v.model || "").trim(),
      };
      var k = (v.api_key || "").trim();
      if (k) payload.api_key = k;                 // 填了 → 替换
      else if (!current.has_key) payload.api_key = "";  // 本来没有也没填 → 明确空
      // 有 key 且留空 → **不带该字段**，服务端沿用旧值

      return window.Harness.saveConfig(ws, payload).then(function (r) {
        formApi.busy(false);
        applyEndpoint(r.endpoint);
        UI.toast({
          tone: "success",
          title: "模型接入已保存",
          desc: r.endpoint.configured
            ? (r.endpoint.model + " @ " + r.endpoint.chat_url)
            : "配置不完整，运行时仍走离线脚本。",
          duration: 5000,
        });
        return true;
      }).catch(function (err) {
        formApi.busy(false);
        formApi.setError("base_url", "保存失败：" + (err.message || err));
        UI.toast({ tone: "danger", title: "保存失败", desc: String(err.message || err), duration: 6000 });
        return false;   // 保持打开，别把用户填的内容丢掉
      });
    }
  }

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

  /* ============================================================
     启动
     ============================================================ */
  function restorePersisted() {
    document.documentElement.style.setProperty("--rail-w", (persisted.railW || 248) + "px");
    if (persisted.railCollapsed) D.shell.classList.add("is-rail-collapsed");
    if (persisted.agentHidden && !state.mobile) D.shell.classList.add("is-agent-hidden");
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
        uiHooks.updateMeter();
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
