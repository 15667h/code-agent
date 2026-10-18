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
    activeTab: OPEN_TABS[0].path,
    tabs: OPEN_TABS.map(function (t) { return { path: t.path, dirty: t.dirty }; }),
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
  };

  var dirtyFile = { "src/transport/reconnect.ts": true };

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

  function renderRail() {
    var body = $("#rail-body");
    var title = $("#rail-title");
    title.textContent = RAIL_TITLES[state.railView] || "";
    body.innerHTML = "";

    if (state.railView === "explorer") {
      var sect = el("div", "sect");
      var head = el("button", "sect__head");
      head.type = "button";
      head.setAttribute("aria-expanded", "true");
      head.appendChild(svg(ICONS.chevron, "sect__chev"));
      head.appendChild(el("span", null, "crucible-core"));
      head.appendChild(el("span", "sect__tail", "· " + WORKSPACE.branch));
      var listWrap = el("div");
      head.addEventListener("click", function () {
        var open = head.getAttribute("aria-expanded") === "true";
        head.setAttribute("aria-expanded", open ? "false" : "true");
        listWrap.hidden = open;
      });
      sect.appendChild(head);
      sect.appendChild(listWrap);
      listWrap.appendChild(renderTree(FILE_TREE, 0, listWrap));
      body.appendChild(sect);

      var ig = el("div", "sect");
      var igHead = el("button", "sect__head");
      igHead.type = "button";
      igHead.setAttribute("aria-expanded", "false");
      igHead.appendChild(svg(ICONS.chevron, "sect__chev"));
      igHead.appendChild(el("span", null, "未纳管 / 已忽略"));
      igHead.appendChild(el("span", "sect__tail", "3"));
      var igWrap = el("div");
      igWrap.hidden = true;
      ["node_modules", ".next", "dist"].forEach(function (n) {
        var r = el("div", "fileitem");
        r.appendChild(svg(ICONS.dirClosed, "fileitem__icon"));
        r.appendChild(el("span", "fileitem__name", n));
        igWrap.appendChild(r);
      });
      igHead.addEventListener("click", function () {
        var open = igHead.getAttribute("aria-expanded") === "true";
        igHead.setAttribute("aria-expanded", open ? "false" : "true");
        igWrap.hidden = open;
      });
      ig.appendChild(igHead);
      ig.appendChild(igWrap);
      body.appendChild(ig);

      body.appendChild(buildIndexNote());
      return;
    }

    if (state.railView === "search") {
      var wrap = el("div", "railinput");
      wrap.appendChild(svg(ICONS.search));
      var inp = el("input");
      inp.type = "search";
      inp.placeholder = "在工作区内搜索…";
      inp.setAttribute("aria-label", "在工作区内搜索");
      wrap.appendChild(inp);
      body.appendChild(wrap);

      var hits = [
        { f: "src/transport/sse-client.ts", l: 9, t: "private cursor: number = 0;" },
        { f: "src/transport/sse-client.ts", l: 23, t: "this.cursor = frame.seq;" },
        { f: "src/protocol.ts", l: 88, t: "export type SessionCursor = number;" },
        { f: "harness/loop.py", l: 141, t: "cursor = session.cursor" },
        { f: "harness/loop.py", l: 203, t: "if cursor < last_seq:  # 检测丢序" },
      ];
      var hr = el("div", "sect__head");
      hr.appendChild(el("span", null, "cursor · 5 命中 · 4 文件"));
      body.appendChild(hr);
      hits.forEach(function (h) {
        var it = el("button", "fileitem");
        it.type = "button";
        it.style.alignItems = "flex-start";
        it.style.paddingTop = "5px";
        it.style.paddingBottom = "5px";
        it.appendChild(svg(ICONS.file, "fileitem__icon"));
        var box = el("div");
        box.style.minWidth = "0";
        box.style.flex = "1 1 auto";
        var l1 = el("div", null, h.f);
        l1.style.fontFamily = "var(--font-mono)";
        l1.style.fontSize = "11px";
        l1.style.color = "var(--text)";
        l1.style.overflow = "hidden";
        l1.style.textOverflow = "ellipsis";
        l1.style.whiteSpace = "nowrap";
        var l2 = el("div", null, h.l + "  " + h.t);
        l2.style.fontFamily = "var(--font-mono)";
        l2.style.fontSize = "10px";
        l2.style.color = "var(--text-faint)";
        l2.style.overflow = "hidden";
        l2.style.textOverflow = "ellipsis";
        l2.style.whiteSpace = "nowrap";
        box.appendChild(l1);
        box.appendChild(l2);
        it.appendChild(box);
        it.addEventListener("click", function () {
          if (h.f === "src/transport/sse-client.ts" || h.f === "src/protocol.ts") openTab(h.f);
        });
        body.appendChild(it);
      });
      return;
    }

    if (state.railView === "scm") {
      var sess = el("div", "sect");
      var sh = el("button", "sect__head");
      sh.type = "button";
      sh.setAttribute("aria-expanded", "true");
      sh.appendChild(svg(ICONS.chevron, "sect__chev"));
      sh.appendChild(el("span", null, "工作区改动"));
      sh.appendChild(el("span", "sect__tail", "4"));
      sess.appendChild(sh);

      var changes = WORKSPACE_CHANGES;
      changes.forEach(function (c) {
        var it = el("button", "fileitem");
        it.type = "button";
        it.style.paddingLeft = "16px";
        it.appendChild(svg(ICONS.file, "fileitem__icon"));
        it.appendChild(el("span", "fileitem__name", c.path.split("/").pop()));
        it.title = c.path;
        var s = el("span", "fileitem__status st-" + c.st, c.st);
        it.appendChild(s);
        var st = el("span", "fileitem__lines");
        st.innerHTML = '<span style="color:var(--jade)">+' + c.a + "</span>";
        if (c.d) st.innerHTML += ' <span style="color:var(--vermilion)">−' + c.d + "</span>";
        it.appendChild(st);
        if (c.openable) {
          it.addEventListener("click", function () { openTab(c.path); });
        }
        body.appendChild(it);
      });
      body.appendChild(sess);

      var box = el("div");
      box.style.padding = "0 12px";
      var cb = el("button", "btn btn--primary");
      cb.type = "button";
      cb.style.width = "100%";
      var totA = changes.reduce(function (n, c) { return n + c.a; }, 0);
      var totD = changes.reduce(function (n, c) { return n + c.d; }, 0);
      cb.appendChild(el("span", null, "提交 " + changes.length + " 个文件 · +" + totA + " −" + totD));
      box.appendChild(cb);
      var wt = el("div");
      wt.style.cssText = "margin-top:10px;font-size:10px;color:var(--text-faint);font-family:var(--font-mono);line-height:1.5;word-break:break-all";
      wt.textContent = "worktree: " + WORKSPACE.worktree;
      box.appendChild(wt);
      body.appendChild(box);
      return;
    }

    if (state.railView === "sessions") {
      var s = el("div", "sect");
      var sh = el("div", "sect__head");
      sh.appendChild(el("span", null, "会话"));
      sh.appendChild(el("span", "sect__tail", SESSIONS.length + " 个 · 按时间倒序"));
      s.appendChild(sh);

      SESSIONS.forEach(function (x) {
        var b = el("button", "sess" + (x.active ? " is-on" : ""));
        b.type = "button";
        b.setAttribute("data-tip", x.branch);
        var top = el("div", "sess__top");
        top.appendChild(el("span", "sess__title truncate", x.title));
        if (x.active) top.appendChild(el("span", "sess__badge", "当前"));
        top.appendChild(el("span", "sess__time", UI.fmtRel(x.tsMin)));
        b.appendChild(top);

        var sub = el("div", "sess__sub");
        sub.innerHTML =
          '<span class="sess__branch truncate">' + esc(x.branch) + "</span>" +
          '<span class="sess__stat"><span class="sess__add">+' + x.add + '</span> <span class="sess__del">−' + x.del + "</span></span>" +
          '<span class="sess__stat sess__turns">' + x.turns + " 轮</span>" +
          '<span class="sess__stat">' + UI.fmtTokens(x.tokens) + "</span>";
        b.appendChild(sub);

        b.addEventListener("click", function () {
          $$(".sess", body).forEach(function (n) {
            n.classList.remove("is-on");
            var bd = n.querySelector(".sess__badge");
            if (bd) bd.remove();
          });
          b.classList.add("is-on");
          if (!b.querySelector(".sess__badge")) {
            var bdg = el("span", "sess__badge", "当前");
            b.querySelector(".sess__top").insertBefore(bdg, b.querySelector(".sess__time"));
          }
          UI.announce("已切换到会话：" + x.title);
          UI.toast({ tone: "info", title: "已切到会话「" + x.title + "」", desc: "分支 " + x.branch + " · " + x.turns + " 轮对话", duration: 3400 });
        });

        b.addEventListener("contextmenu", function (ev) {
          ev.preventDefault();
          UI.contextMenu(ev.clientX, ev.clientY, [
            { label: "继续这个会话", icon: ICONS.play, onClick: function () { b.click(); } },
            { label: "复制分支名", icon: ICONS.file, onClick: function () { UI.copy(x.branch, "分支名"); } },
            { sep: true },
            { label: "归档会话", icon: ICONS.book, onClick: function () {
                UI.toast({ tone: "ok", title: "已归档「" + x.title + "」", duration: 4000,
                  actions: [{ label: "撤销", primary: true, onClick: function () { UI.toast({ tone: "ok", title: "已恢复", duration: 2000 }); } }] });
              } },
            { label: "删除会话", icon: ICONS.x, danger: true, onClick: function () {
                UI.confirm({
                  title: "删除这个会话？",
                  desc: "「" + x.title + "」的对话记录会被移除。分支上的代码改动不受影响。",
                  confirmLabel: "删除", cancelLabel: "取消", tone: "danger", detail: x.branch,
                }).then(function (ok) {
                  if (!ok) return;
                  UI.toast({ tone: "warn", title: "已删除会话", desc: "演示环境，未真正删除。", duration: 4000,
                    actions: [{ label: "撤销", primary: true, onClick: function () { UI.toast({ tone: "ok", title: "已撤销", duration: 2000 }); } }] });
                });
              } },
          ]);
        });

        s.appendChild(b);
      });
      body.appendChild(s);
      return;
    }

    if (state.railView === "mcp") {
      var m = el("div", "sect");
      var mh = el("div", "sect__head");
      mh.appendChild(el("span", null, "已注册的 MCP 服务"));
      mh.appendChild(el("span", "sect__tail", MCP_TOOLS.filter(function (t) { return t.enabled; }).length + "/" + MCP_TOOLS.length));
      m.appendChild(mh);
      MCP_TOOLS.forEach(function (t) {
        var row = el("div", "mcpitem");
        var meta = el("div", "mcpitem__meta");
        meta.appendChild(el("span", "mcpitem__name", t.name));
        meta.appendChild(el("span", "mcpitem__desc", t.desc));
        row.appendChild(meta);
        var sw = el("button", "switch");
        sw.type = "button";
        sw.setAttribute("role", "switch");
        sw.setAttribute("aria-checked", t.enabled ? "true" : "false");
        sw.setAttribute("aria-label", "启用 " + t.name);
        sw.addEventListener("click", function () {
          t.enabled = !t.enabled;
          sw.setAttribute("aria-checked", t.enabled ? "true" : "false");
          renderRail();
        });
        row.appendChild(sw);
        m.appendChild(row);
      });
      body.appendChild(m);

      var note = el("div");
      note.style.cssText = "padding:8px 16px;font-size:10px;color:var(--text-faint);line-height:1.6;text-wrap:pretty";
      note.textContent = "工具定义按需呈现：未启用的服务不占用上下文窗口。这是降低上下文膨胀的关键一步。";
      body.appendChild(note);
      return;
    }
  }

  function buildIndexNote() {
    var d = el("div");
    d.style.cssText = "margin:0 12px;padding:8px 10px;background:var(--surface-raised);border:1px solid var(--border);border-radius:6px;font-size:10px;line-height:1.6;color:var(--text-faint)";
    d.textContent = WORKSPACE.indexStatus;
    return d;
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<<": "&lt;", ">": "&gt;", '"': "&quot;" }[c] || c;
    });
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

  /* 由正文生成 diff：行数、行号、内容三者天然一致，不可能漂移 */
  function buildDiffFor(path) {
    var body = FILE_BODIES[path];
    if (!body) return null;
    var rows = body.map(function (segs, i) {
      var txt = (segs || []).map(function (s) { return s[1]; }).join("");
      return { t: i === 0 ? "add" : "add", n: [null, i + 1], text: txt };
    });
    // 在注释块之间插一条上下文行，让它看起来像真实 patch 而不是整文件转储
    if (rows.length > 16) {
      rows.splice(15, 0, { t: "ctx", n: [null, null], text: "" });
    }
    return rows;
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

    var body = FILE_BODIES[state.activeTab];
    if (body && body.length) {
      code.innerHTML = renderCodeLines(body);
      gutter.textContent = gutterFor(body.length);
      $("#editor-scroll").scrollTop = 0;
      return;
    }

    // 没有正文的文件：给一段说明性占位，行数如实标为 0，不伪造
    var ph = [
      "// " + state.activeTab,
      "//",
      "// 这个文件还没有内置正文。当前演示完整着色的文件有三个：",
      "//   src/transport/reconnect.ts",
      "//   src/transport/sse-client.ts",
      "//   harness/approval.py",
      "//",
      "// 从左侧资源管理器点开任一即可看到带真实行号的正文。",
    ];
    code.innerHTML = ph.map(function (l) {
      return '<span class="t-cm">' + escHtml(l) + "</span>";
    }).join("\n");
    gutter.textContent = gutterFor(ph.length);
    $("#editor-scroll").scrollTop = 0;
  }

  /* ============================================================
     三、终端
     ============================================================ */
  /* ============================================================
     三、终端 —— 带命令历史与真实回显
     ============================================================ */
  var termLines = TERMINAL_LINES.slice();
  var termHistory = [];
  var termHistIdx = -1;

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
      termAppend({ t: "dim", text: "演示终端支持：git status · npx tsc · npm test · ls · crucible · clear" });
      return;
    }

    var hit = TERM_RESPONSES.filter(function (r) { return r.match.test(cmd); })[0];
    if (hit) {
      hit.lines.forEach(function (l) { termAppend(l); });
    } else {
      termAppend({ t: "fail", text: "zsh: command not found: " + cmd.split(/\s+/)[0] });
      termAppend({ t: "dim", text: "这是演示终端；输入 help 看支持的命令。" });
    }
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
  function renderPlan() {
    var list = $("#plan-list");
    var goal = $("#plan-goal");
    list.innerHTML = "";
    goal.textContent = PLAN.goal;
    PLAN.steps.forEach(function (s, i) {
      var li = el("li", "pstep pstep--" + s.state);
      var mark = el("span", "pstep__mark");
      if (s.state === "done") {
        mark.appendChild(svg(ICONS.check, null));
      } else {
        mark.textContent = String(i + 1);
      }
      li.appendChild(mark);
      var box = el("div");
      var txt = el("span", "pstep__text", s.text);
      box.appendChild(txt);
      if (s.evidence) box.appendChild(el("span", "pstep__ev", s.evidence));
      li.appendChild(box);
      list.appendChild(li);
    });
    var done = PLAN.steps.filter(function (s) { return s.state === "done"; }).length;
    $("#plan-count").textContent = done + "/" + PLAN.steps.length;
  }

  function renderStream() {
    var stream = D.stream;
    stream.innerHTML = "";
    EVENT_STREAM.forEach(function (ev, i) { stream.appendChild(buildEvent(ev, i)); });

    // 闸门已批准 → 注入后续事件
    if (state.gateResolved === "approved") {
      DEFERRED_AFTER_GATE.forEach(function (ev, i) {
        stream.appendChild(buildEvent(ev, EVENT_STREAM.length + i));
      });
      showVerify();
    } else if (state.gateResolved === "rejected") {
      var halt = el("div", "ev ev--halt");
      var bar = el("div", "haltbar");
      bar.appendChild(svg(ICONS.pause));
      bar.appendChild(el("span", null, "你拒绝了这次执行。后续 4 个事件未运行，工作区保持原样。"));
      halt.appendChild(bar);
      stream.appendChild(halt);
    }

    applyFilter();
    if (refreshStreamFade) refreshStreamFade();
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
    var st = $("#stream-stat");
    if (st) {
      st.textContent = (f === "all" ? total + " 个事件" : shown + " / " + total + " 个事件")
        + " · " + state.tabs.length + " 个文件已打开";
    }

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

  function showVerify() {
    var v = $("#verify-bar");
    v.innerHTML = "";
    var dot = el("span", "verify__dot");
    dot.setAttribute("aria-hidden", "true");
    v.appendChild(dot);
    v.appendChild(el("span", "verify__label", "自证检查通过 · " + VERIFY.checked + "/" + VERIFY.total + " 条目标条件"));
    var spacer = el("span", "verify__spacer");
    v.appendChild(spacer);
    v.appendChild(el("span", "verify__model", VERIFY.model));
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

    // 优先用后端给的真实 diff；没有才回退到 mock 的 PATCHES
    var rows = parseUnifiedDiff(ev.diffText) || buildDiffFor(ev.patch || ev.file);
    var meta = PATCHES[ev.patch || ev.file] || {};

    if (rows) {
      // 统计增删
      var adds = 0, dels = 0;
      rows.forEach(function (r) { if (r.t === "add") adds++; if (r.t === "del") dels++; });

      var diff = el("div", "diff");
      var dh = el("div", "diff__head");
      if (meta.isNew) dh.appendChild(el("span", "diff__badge", "新增"));
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
    }

    if (meta.verified) {
      var v = el("div", "verified");
      v.appendChild(svg(ICONS.check));
      v.appendChild(el("span", null, meta.verified));
      bodyEl.appendChild(v);
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
    head.appendChild(el("span", "gate__label", "审批闸门 · 等待你的决定"));
    head.appendChild(el("span", "gate__risk", ev.risk));
    gate.appendChild(head);

    var bodyEl = el("div", "gate__body");

    var act = el("div", "gate__action copyscope");
    act.appendChild(el("span", "gate__tool", ev.action));
    act.appendChild(el("span", "gate__cmd", ev.detail));
    act.appendChild(copyBtn(function () { return ev.detail; }, "命令", "复制命令"));
    bodyEl.appendChild(act);

    bodyEl.appendChild(el("p", "gate__reason", ev.reason));

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

    if (decision === "approved") {
      if (PLAN.steps[2].state !== "done") {
        PLAN.steps[2].state = "done";
        PLAN.steps[2].evidence = "12 passed";
        PLAN.steps[3].state = "active";
        renderPlan();
      }
    } else {
      PLAN.steps[2].state = "todo";
      delete PLAN.steps[2].evidence;
      renderPlan();
    }
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

    var files = [];
    (function walk(nodes, prefix) {
      nodes.forEach(function (n) {
        if (n.kind === "file" && !/\.(md|json)$/.test(n.name) && n.name.indexOf(".") !== -1) {
          files.push(prefix ? prefix + "/" + n.name : n.name);
        }
        if (n.kind === "dir") walk(n.children, prefix ? prefix + "/" + n.name : n.name);
      });
    })(FILE_TREE, "");

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

  function allFiles() {
    var out = [];
    (function walk(nodes, prefix) {
      nodes.forEach(function (n) {
        if (n.kind === "file" && !/^(AGENTS\.md|package\.json|\.codexignore)$/.test(n.name)) {
          out.push(prefix ? prefix + "/" + n.name : n.name);
        }
        if (n.kind === "dir") walk(n.children, prefix ? prefix + "/" + n.name : n.name);
      });
    })(FILE_TREE, "");
    return out;
  }

  function showMention(mode, query, caret) {
    mentionMode = mode;
    mentionQuery = query;
    var box = D.mine;
    box.innerHTML = "";

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
        { label: "全部暂存", icon: ICONS.check, onClick: function () { UI.toast({ tone: "ok", title: "已暂存 4 个文件", duration: 2600 }); } },
        { label: "丢弃全部改动", icon: ICONS.x, danger: true, onClick: function () {
            UI.confirm({
              title: "丢弃全部改动？",
              desc: "4 个文件的未提交改动都会被还原。这个操作无法撤销。",
              confirmLabel: "丢弃", cancelLabel: "取消", tone: "danger",
              detail: WORKSPACE_CHANGES.map(function (c) { return c.st + " " + c.path; }).join("\n"),
            }).then(function (ok) {
              if (ok) UI.toast({ tone: "warn", title: "已丢弃改动", desc: "演示环境，未真正还原。", duration: 4000,
                actions: [{ label: "撤销", primary: true, onClick: function () { UI.toast({ tone: "ok", title: "已撤销", duration: 2000 }); } }] });
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

      // 会话
      SESSIONS.forEach(function (s) {
        cmds.push({ label: s.title, sub: s.branch + " · " + UI.fmtRel(s.tsMin), group: "会话", icon: ICONS.clock,
          onRun: function () { goView("sessions"); UI.toast({ tone: "info", title: "已切到会话「" + s.title + "」", duration: 3000 }); } });
      });

      // 斜杠命令
      SLASH_COMMANDS.forEach(function (c) {
        cmds.push({ label: c.cmd, sub: c.desc, group: "Agent 命令", icon: ICONS.terminal,
          onRun: function () {
            var ta = $("#dock-input");
            ta.value = c.cmd + " ";
            ta.focus();
            ta.dispatchEvent(new Event("input", { bubbles: true }));
            UI.toast({ tone: "info", title: "已插入 " + c.cmd, desc: "补上参数后按 Enter 发送。", duration: 3400 });
          } });
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
        { label: "索引详情", icon: ICONS.book, onClick: function () {
            UI.toast({ tone: "info", title: "索引进度", desc: WORKSPACE.indexStatus, duration: 4200 });
          } },
      ]);
    });
    $("#btn-diag").addEventListener("click", function (e) {
      var r = this.getBoundingClientRect();
      UI.contextMenu(r.left - 100, r.top - 84, [
        { label: "2 个警告", icon: ICONS.warning, disabled: true },
        { sep: true },
        { label: "reconnect.ts:22 未使用的常量 JITTER_RATIO", icon: ICONS.warning, onClick: function () { openTab("src/transport/reconnect.ts"); } },
        { label: "approval.py:31 分支缺少返回值注释", icon: ICONS.warning, onClick: function () { openTab("harness/approval.py"); } },
      ]);
    });
    $("#btn-meter").addEventListener("click", showTokenDetail);

    function showTokenDetail() {
      UI.toast({
        tone: "info",
        title: "上下文用量 38.2k / 200k",
        desc: "系统提示 3.1k · AGENTS.md 0.8k · 文件正文 12.4k · 工具结果 18.9k · 压缩余量 3.0k",
        duration: 7000,
        actions: [{ label: "压缩上下文", primary: true, onClick: function () {
          UI.toast({ tone: "ok", title: "已压缩上下文", desc: "保留结构化工作记忆，丢弃可重建的工具输出。", duration: 4000 });
        } }],
      });
    }

    function refreshIndex() {
      UI.toast({ tone: "info", title: "正在重建索引…", duration: 1800 });
      setTimeout(function () {
        UI.toast({ tone: "ok", title: "索引已更新", desc: WORKSPACE.indexStatus, duration: 3200 });
      }, 1500);
    }

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
