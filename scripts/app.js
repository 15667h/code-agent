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
    pause: '<rect x="4" y="3.5" width="2.6" height="9" rx="0.8" fill="currentColor"/><rect x="9.4" y="3.5" width="2.6" height="9" rx="0.8" fill="currentColor"/>',
    shield: '<path d="M8 1.8l5 1.8v4.6c0 3-2 5.3-5 6.2-3-.9-5-3.2-5-6.2V3.6z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>',
    terminal: '<path d="M3.5 5l3 3-3 3M9 11h4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>',
    search: '<circle cx="7" cy="7" r="4" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M10 10l3.5 3.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>',
    edit: '<path d="M11.2 2.6l2.2 2.2-8.2 8.2H3v-2.2z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
    book: '<path d="M3 3h4.5c.8 0 1.5.7 1.5 1.5V13c0-.6-.7-1-1.5-1H3zM13 3H8.5C7.7 3 7 3.7 7 4.5V13c0-.6.7-1 1.5-1H13z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
    clock: '<circle cx="8" cy="8" r="5.8" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M8 4.6V8l2.4 1.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
    plus: '<path d="M8 3.5v9M3.5 8h9" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
    play: '<path d="M5.5 3.5l7 4.5-7 4.5z" fill="currentColor"/>',
  };

  var TOOL_ICON = {
    code_search: ICONS.search,
    read: ICONS.book,
    shell: ICONS.terminal,
    apply_patch: ICONS.edit,
  };

  /* ---------- 状态 ---------- */
  var state = {
    activeTab: OPEN_TABS[0].path,
    tabs: OPEN_TABS.map(function (t) { return { path: t.path, dirty: t.dirty }; }),
    railView: "explorer",
    gateResolved: null,      // null | "approved" | "rejected"
    planOpen: true,
    termCollapsed: false,
    mobile: window.matchMedia("(max-width: 1024px)").matches,
    ctxRefs: [],
  };

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
        if (depth) { b.style.paddingLeft = (16 + depth * 12) + "px"; }
        if (state.activeTab === node.path) b.classList.add("is-on");

        b.appendChild(svg(ICONS.file, "fileitem__icon"));
        b.appendChild(el("span", "fileitem__name", node.name));
        if (node.status) {
          var s = el("span", "fileitem__status st-" + node.status, node.status);
          s.title = node.status === "M" ? "已修改" : node.status === "A" ? "新增" : "已删除";
          b.appendChild(s);
        }
        b.appendChild(el("span", "fileitem__lines", String(node.lines)));
        b.title = node.path || node.name;
        b.addEventListener("click", function () {
          if (node.path) openTab(node.path);
          if (state.mobile) closeRail();
        });
        li.appendChild(b);
      }
      ul.appendChild(li);
    });
    return ul;
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
      SESSIONS.forEach(function (x) {
        var b = el("button", "sess" + (x.active ? " is-on" : ""));
        b.type = "button";
        var top = el("div", "sess__top");
        top.appendChild(el("span", "sess__title", x.title));
        top.appendChild(el("span", "sess__time", x.time));
        b.appendChild(top);
        var sub = el("div", "sess__sub");
        sub.innerHTML = '<span class="sess__branch">' + esc(x.branch) + "</span>" +
          '<span class="sess__stat"><span class="sess__add">+' + x.add + "</span> <span class=\"sess__del\">−" + x.del + "</span></span>" +
          '<span class="sess__model">' + esc(x.model) + "</span>";
        b.appendChild(sub);
        b.addEventListener("click", function () {
          $$(".sess", body).forEach(function (n) { n.classList.remove("is-on"); });
          b.classList.add("is-on");
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
    if (!exists) state.tabs.push({ path: path, dirty: false });
    state.activeTab = path;
    renderTabs();
    renderEditor();
    if (state.railView === "explorer") renderRail();
  }

  function closeTab(path) {
    var i = state.tabs.findIndex(function (t) { return t.path === path; });
    if (i < 0) return;
    state.tabs.splice(i, 1);
    if (state.activeTab === path) {
      state.activeTab = state.tabs[Math.min(i, state.tabs.length - 1)]
        ? state.tabs[Math.min(i, state.tabs.length - 1)].path
        : null;
    }
    renderTabs();
    renderEditor();
  }

  function renderTabs() {
    var bar = $("#tabbar");
    bar.innerHTML = "";
    if (!state.tabs.length) {
      var e = el("span");
      e.style.cssText = "padding:0 14px;display:flex;align-items:center;font-size:11px;color:var(--text-faint)";
      e.textContent = "没有打开的文件";
      bar.appendChild(e);
      return;
    }
    state.tabs.forEach(function (t) {
      var b = el("button", "tab" + (t.path === state.activeTab ? " is-on" : ""));
      b.type = "button";
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", t.path === state.activeTab ? "true" : "false");
      b.appendChild(svg(ICONS.file, "tab__icon"));
      b.appendChild(el("span", "tab__name", t.path.split("/").pop()));
      if (t.dirty) {
        var dot = el("span", "tab__dirty");
        dot.title = "有未保存改动";
        b.appendChild(dot);
      }
      var c = el("button", "tab__close");
      c.type = "button";
      c.setAttribute("aria-label", "关闭 " + t.path.split("/").pop());
      c.appendChild(svg(ICONS.x));
      c.addEventListener("click", function (ev) {
        ev.stopPropagation();
        closeTab(t.path);
      });
      b.appendChild(c);
      b.title = t.path;
      b.addEventListener("click", function () {
        state.activeTab = t.path;
        renderTabs();
        renderEditor();
      });
      bar.appendChild(b);
    });
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
  function renderTerminal() {
    var body = $("#terminal-body");
    body.innerHTML = "";
    TERMINAL_LINES.forEach(function (l) {
      var s = el("span", "tl-" + (l.t === "cmd" ? "cmd" : l.t === "out-hi" ? "out-hi" : l.t === "dim" ? "dim" : "out"));
      s.textContent = l.text + "\n";
      body.appendChild(s);
    });
    var c = el("span", "tcursor");
    c.setAttribute("aria-hidden", "true");
    body.appendChild(c);
    body.appendChild(el("span", null, " "));
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
    var stream = $("#stream");
    stream.innerHTML = "";
    EVENT_STREAM.forEach(function (ev) { stream.appendChild(buildEvent(ev)); });

    // 闸门已批准 → 注入后续事件
    if (state.gateResolved === "approved") {
      DEFERRED_AFTER_GATE.forEach(function (ev) { stream.appendChild(buildEvent(ev)); });
      showVerify();
    } else if (state.gateResolved === "rejected") {
      var halt = el("div", "ev ev--halt");
      var bar = el("div", "haltbar");
      bar.appendChild(svg(ICONS.pause));
      bar.appendChild(el("span", null, "你拒绝了这次执行。后续 4 个事件未运行，工作区保持原样。"));
      halt.appendChild(bar);
      stream.appendChild(halt);
    }
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

  function buildEvent(ev) {
    switch (ev.kind) {
      case "reasoning": return buildReasoning(ev);
      case "tool": return buildTool(ev);
      case "patch": return buildPatch(ev);
      case "gate": return buildGate(ev);
      case "check": return buildCheck(ev);
      default: return el("div");
    }
  }

  function buildReasoning(ev) {
    var d = el("div", "ev ev--reasoning");
    var head = el("div", "ev__head");
    head.appendChild(el("span", "ev__kind", "推理"));
    if (ev.dur) head.appendChild(el("span", "ev__dur", ev.dur));
    d.appendChild(head);
    d.appendChild(el("p", "ev__text", ev.text));
    return d;
  }

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

    var meta = el("div", "tool__meta");
    (ev.meta || []).forEach(function (m) {
      var s = el("span", "meta");
      s.appendChild(el("span", "meta__k", m.k));
      s.appendChild(el("span", "meta__v", m.v));
      meta.appendChild(s);
    });
    card.appendChild(meta);

    var bodyEl = el("div", "tool__body");
    bodyEl.hidden = true;
    if (ev.output) {
      var ol = el("pre", "outlines");
      ev.output.forEach(function (l) {
        var cls = l.t === "pass" ? "out-pass" : l.t === "fail" ? "out-fail" : l.t === "dim" ? "out-dim" : "out-plain";
        var s = el("span", cls);
        s.textContent = l.text + "\n";
        ol.appendChild(s);
      });
      bodyEl.appendChild(ol);
    } else {
      var p = el("div", "outlines out-plain");
      p.textContent = ev.title;
      bodyEl.appendChild(p);
    }
    card.appendChild(bodyEl);

    var expanded = false;
    head.addEventListener("click", function () {
      expanded = !expanded;
      head.setAttribute("aria-expanded", expanded ? "true" : "false");
      bodyEl.hidden = !expanded;
    });

    wrap.appendChild(card);
    return wrap;
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

    var rows = buildDiffFor(ev.patch || ev.file);
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

    var act = el("div", "gate__action");
    act.appendChild(el("span", "gate__tool", ev.action));
    act.appendChild(el("span", "gate__cmd", ev.detail));
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
    var head = el("div", "checkcard__head");
    head.appendChild(svg(ICONS.check));
    head.appendChild(el("span", "checkcard__title", ev.title));
    head.appendChild(el("span", "checkcard__model", ev.model));
    card.appendChild(head);

    var ul = el("ul", "checklist");
    ev.items.forEach(function (it) {
      var li = el("li");
      li.appendChild(svg(it.ok ? ICONS.check : ICONS.warning));
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

  /* 发送：往事件流里追加一条用户消息 + 一次「正在读取上下文」 */
  function sendPrompt(text) {
    if (!text.trim()) return;
    var stream = $("#stream");

    var u = el("div", "ev ev--reasoning");
    u.style.paddingLeft = "0";
    var ub = el("div");
    ub.style.cssText = "padding:8px 12px;background:var(--brass-a12);border-left:2px solid var(--brass);border-radius:0 6px 6px 0;font-size:12px;color:var(--text);text-wrap:pretty";
    ub.textContent = text;
    u.appendChild(ub);
    stream.appendChild(u);

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

    // 演示：1.6s 后替换为「思考完成」
    setTimeout(function () {
      think.innerHTML = "";
      var h2 = el("div", "ev__head");
      h2.appendChild(el("span", "ev__kind", "推理"));
      h2.appendChild(el("span", "ev__dur", "0.9s"));
      think.appendChild(h2);
      think.appendChild(el("p", "ev__text", "已装配 " + (state.ctxRefs.length ? state.ctxRefs.length + " 个显式引用 + " : "") + "当前分支的 4 处改动。这只演示前端行为；接上真实 harness 后，这里会流入模型的实际事件。"));
      stream.scrollTop = stream.scrollHeight;
    }, 1600);
  }

  /* ============================================================
     六、事件绑定
     ============================================================ */
  function bind() {
    // 活动栏切换
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
        renderRail();

        // 窄屏下点活动栏把侧栏拉开；再点同一个则收起
        if (state.mobile && !already) openRail();
      });
    });

    // 侧栏折叠
    $("#btn-collapse-rail").addEventListener("click", function () {
      if (state.mobile) closeRail();
      else $(".shell").classList.toggle("is-rail-collapsed");
    });

    // 主题切换
    $("#btn-theme").addEventListener("click", function () {
      var root = document.documentElement;
      var cur = root.getAttribute("data-theme") === "light" ? "dark" : "light";
      root.setAttribute("data-theme", cur);
      var mc = document.querySelector('meta[name="theme-color"]');
      if (mc) mc.setAttribute("content", cur === "light" ? "#f2f0eb" : "#10141a");
      this.setAttribute("aria-label", cur === "light" ? "切换到深色主题" : "切换到浅色主题");
    });

    // 对话模式
    $$(".segctl__btn").forEach(function (b) {
      b.addEventListener("click", function () {
        $$(".segctl__btn").forEach(function (x) {
          x.classList.remove("is-on");
          x.setAttribute("aria-pressed", "false");
        });
        b.classList.add("is-on");
        b.setAttribute("aria-pressed", "true");
        // 模式与沙箱联动：全权 = full-access
        var m = b.dataset.mode;
        var sb = $("[data-sandbox]");
        if (m === "full") {
          sb.textContent = "danger-full-access";
          var dot = $(".statusitem__dot--sandbox");
          dot.style.background = "var(--vermilion)";
          sb.parentElement.title = "无沙箱、无审批 —— 慎用";
        } else if (m === "chat") {
          sb.textContent = "read-only";
          $(".statusitem__dot--sandbox").style.background = "var(--slate-blue)";
          sb.parentElement.title = "只读：可检查与提议，不改动任何文件";
        } else {
          sb.textContent = "workspace-write";
          $(".statusitem__dot--sandbox").style.background = "var(--amber)";
          sb.parentElement.title = "可在工作区内写入；越界需批准";
        }
      });
    });

    // 状态栏：切沙箱 / 审批
    var SBX = ["read-only", "workspace-write", "danger-full-access"];
    $("#btn-sandbox").addEventListener("click", function () {
      var cur = $("[data-sandbox]").textContent;
      var i = SBX.indexOf(cur);
      var nxt = SBX[(i + 1) % SBX.length];
      $("[data-sandbox]").textContent = nxt;
      var dot = $(".statusitem__dot--sandbox");
      dot.style.background = nxt === "read-only" ? "var(--slate-blue)" : nxt === "workspace-write" ? "var(--amber)" : "var(--vermilion)";
    });
    var AP = ["untrusted", "on-request", "never"];
    $("#btn-approval").addEventListener("click", function () {
      var cur = $("[data-approval]").textContent;
      var i = AP.indexOf(cur);
      $("[data-approval]").textContent = AP[(i + 1) % AP.length];
    });

    // 计划视图开合
    $("#plan-toggle").addEventListener("click", function () {
      var open = this.getAttribute("aria-expanded") === "true";
      this.setAttribute("aria-expanded", open ? "false" : "true");
      $("#plan-body").hidden = open;
    });

    // Agent 面板开合
    function setAgentOpen(open) {
      var sh = $(".shell");
      if (state.mobile) sh.classList.toggle("is-agent-open", open);
      else sh.classList.toggle("is-agent-hidden", !open);
      var t = $("#btn-agent-toggle");
      if (t) t.setAttribute("aria-expanded", open ? "true" : "false");
    }
    function agentIsOpen() {
      var sh = $(".shell");
      return state.mobile
        ? sh.classList.contains("is-agent-open")
        : !sh.classList.contains("is-agent-hidden");
    }
    $("#btn-agent-toggle").addEventListener("click", function () {
      setAgentOpen(!agentIsOpen());
    });
    $("#btn-agent-close").addEventListener("click", function () {
      setAgentOpen(false);
    });

    // 键盘快捷键
    document.addEventListener("keydown", function (e) {
      // Ctrl/Cmd + B 切换 agent 面板
      if ((e.ctrlKey || e.metaKey) && (e.key === "b" || e.key === "B")) {
        e.preventDefault();
        setAgentOpen(!agentIsOpen());
      }
      // Ctrl/Cmd + `  切换终端
      if ((e.ctrlKey || e.metaKey) && e.key === "`") {
        e.preventDefault();
        toggleTerminal();
      }
      if (e.key === "Escape") {
        if (popOpen) { closePop(); return; }
        if (state.mobile) {
          setAgentOpen(false);
          closeRail();
        }
      }
    });

    // 终端
    $("#btn-term-toggle").addEventListener("click", toggleTerminal);
    $("#btn-term-clear").addEventListener("click", function () {
      $("#terminal-body").innerHTML = "";
      var c = el("span", "tcursor");
      c.setAttribute("aria-hidden", "true");
      $("#terminal-body").appendChild(c);
    });
    $$(".termtab").forEach(function (b) {
      b.addEventListener("click", function () {
        $$(".termtab").forEach(function (x) {
          x.classList.remove("is-on");
          x.setAttribute("aria-selected", "false");
        });
        b.classList.add("is-on");
        b.setAttribute("aria-selected", "true");
      });
    });

    // 分割手柄：拖拽调终端高度
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
      wb.style.gridTemplateRows = "minmax(0,1fr) auto " + h + "px";
    });
    document.addEventListener("mouseup", function () {
      if (!dragging) return;
      dragging = false;
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    });
    // 手柄键盘操作
    handle.addEventListener("keydown", function (e) {
      var wb = $(".workbench");
      var cur = $("#terminal").getBoundingClientRect().height;
      if (e.key === "ArrowUp") { wb.style.gridTemplateRows = "minmax(0,1fr) auto " + (cur + 24) + "px"; e.preventDefault(); }
      if (e.key === "ArrowDown") { wb.style.gridTemplateRows = "minmax(0,1fr) auto " + Math.max(60, cur - 24) + "px"; e.preventDefault(); }
    });

    function toggleTerminal() {
      state.termCollapsed = !state.termCollapsed;
      $(".workbench").classList.toggle("is-term-collapsed", state.termCollapsed);
      var b = $("#btn-term-toggle");
      b.setAttribute("aria-expanded", state.termCollapsed ? "false" : "true");
      b.setAttribute("aria-label", state.termCollapsed ? "展开终端" : "折叠终端");
      var chev = b.querySelector("svg path");
      if (chev) chev.setAttribute("d", state.termCollapsed ? "M5.5 12.5L10 8l4.5 4.5" : "M5.5 7.5L10 12l4.5-4.5");
    }

    // 上下文浮层
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

    // 输入坞
    var ta = $("#dock-input");
    ta.addEventListener("input", function () {
      ta.style.height = "auto";
      ta.style.height = Math.min(ta.scrollHeight, 132) + "px";
      $("#btn-dock-send").disabled = !ta.value.trim();
    });
    ta.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        submit();
      }
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
      ta.style.height = "auto";
      $("#btn-dock-send").disabled = true;
    }

    // 新建文件（演示）
    $("#btn-newfile").addEventListener("click", function () {
      var n = prompt("新文件名（演示，不会真正写盘）：");
      if (!n) return;
      openTab("src/" + n);
    });

    // 遮罩点击关闭
    $("#scrim").addEventListener("click", function () {
      $(".shell").classList.remove("is-agent-open");
      closeRail();
    });

    // 媒体查询变化
    var mq = window.matchMedia("(max-width: 1024px)");
    var onChange = function (m) {
      state.mobile = m.matches;
      $(".shell").classList.remove("is-agent-open");
      closeRail();
    };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }

  function openRail() { $(".shell").classList.add("is-rail-open"); }
  function closeRail() { $(".shell").classList.remove("is-rail-open"); }

  /* ============================================================
     启动
     ============================================================ */
  function init() {
    renderRail();
    renderTabs();
    renderEditor();
    renderTerminal();
    renderPlan();
    renderStream();
    renderCtxRefs();
    bind();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
