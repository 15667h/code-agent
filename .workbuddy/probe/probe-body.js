/* CRUCIBLE_PROBE v2 —— 注入页面副本运行（非 iframe）。
   两条纪律（上一版踩过的坑）：
   1. 不用 await sleep()：无头 Chrome 虚拟时间推进 setTimeout，但不推进 Promise 链。
   2. 不把「改类」与「读布局」放在同一 tick：grid 轨道尺寸不同步刷新，
      必须下一个 setTimeout 再读，否则读到陈旧值并误判成 bug。 */
(function () {
  var L = [], errs = [];
  window.addEventListener("error", function (e) { errs.push(e.message + " @" + e.lineno); });
  window.onunhandledrejection = function (e) {
    errs.push("unhandled: " + ((e.reason && e.reason.message) || e.reason));
  };

  function q(s) { return document.querySelector(s); }
  function qa(s) { return Array.prototype.slice.call(document.querySelectorAll(s)); }
  function vis(n) { return n && n.offsetParent !== null; }

  var S = [], out;

  function render() {
    if (!out) {
      out = document.createElement("pre");
      out.id = "__probe";
      out.style.cssText = "position:fixed;left:0;top:0;z-index:99999;background:#000;color:#0f0;padding:10px;font:11px/1.5 monospace;white-space:pre-wrap;max-width:100vw;max-height:100vh;overflow:auto;margin:0";
      document.body.appendChild(out);
    }
    out.textContent = L.join("\n");
  }

  function add(fns) { fns.forEach(function (f) { S.push(f); }); }

  /* ================= 用例 ================= */

  add([function () {
    L.push("=== 初始结构 ===");
    L.push("标签=" + qa(".tab").length + " 树目录=" + qa(".fileitem[aria-expanded]").length +
      " 树文件=" + qa(".fileitem[role=treeitem]").length);
    L.push("事件=" + qa(".ev").length + " 闸门=" + qa(".gate").length + " 计划步=" + qa(".pstep").length +
      " diff行=" + qa(".dline").length + " 复制按钮=" + qa(".copybtn").length + " chip=" + qa(".chip").length);
    L.push("骨架隐藏=" + q("#boot").hidden + " · " + q("#boot-txt").textContent);
    L.push("流统计=" + q("#stream-stat").textContent);
  }]);

  add([
    function () {
      L.push("");
      L.push("=== 终端折叠 ===");
      var wb = q(".workbench"), t = q(".terminal");
      L.push("前 rows=" + getComputedStyle(wb).gridTemplateRows +
        " h=" + Math.round(t.getBoundingClientRect().height) +
        " vis=" + getComputedStyle(t).visibility);
      q("#btn-term-toggle").click();
    },
    function () {
      var wb = q(".workbench"), t = q(".terminal");
      L.push("折叠 rows=" + getComputedStyle(wb).gridTemplateRows +
        " h=" + Math.round(t.getBoundingClientRect().height) +
        " vis=" + getComputedStyle(t).visibility +
        " aria=" + q("#btn-term-toggle").getAttribute("aria-expanded"));
      q("#btn-term-toggle").click();
    },
    function () {
      var t = q(".terminal");
      L.push("展开 h=" + Math.round(t.getBoundingClientRect().height) + " vis=" + getComputedStyle(t).visibility);
      document.documentElement.style.setProperty("--terminal-h", "300px");
    },
    function () {
      L.push("设 300px → h=" + Math.round(q(".terminal").getBoundingClientRect().height));
      q("#btn-term-toggle").click();
    },
    function () {
      var wb = q(".workbench"), t = q(".terminal");
      L.push("★ 拖过之后折叠 rows=" + getComputedStyle(wb).gridTemplateRows +
        " h=" + Math.round(t.getBoundingClientRect().height) +
        " vis=" + getComputedStyle(t).visibility);
      q("#btn-term-toggle").click();
      document.documentElement.style.removeProperty("--terminal-h");
    },
  ]);

  add([
    function () { L.push(""); L.push("=== 快捷键面板与焦点 ==="); q("#btn-help").click(); },
    function () {
      var sh = q(".sheet");
      L.push("打开=" + !!sh + " 分组=" + qa(".sheet__group").length + " 行=" + qa(".sheet__row").length);
      L.push("焦点入面板=" + (sh && sh.contains(document.activeElement)) + " tag=" + (document.activeElement && document.activeElement.tagName));
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    },
    function () { L.push("Esc 关闭=" + !q(".sheet") + " 焦点归还=" + (document.activeElement && (document.activeElement.id || document.activeElement.tagName))); },
  ]);

  add([
    function () {
      L.push("");
      L.push("=== 确认框（脏标签）===");
      var dt = q(".tab__dirty");
      L.push("有脏标签=" + !!dt);
      if (dt) dt.closest(".tab").querySelector(".tab__close").click();
    },
    function () {
      var m = q(".modal");
      L.push("弹框=" + !!m + " 标题=" + (m ? q(".modal__title").textContent : ""));
      if (m) {
        L.push("role=" + m.getAttribute("role") + " aria-modal=" + m.getAttribute("aria-modal") +
          " 焦点在内=" + m.contains(document.activeElement));
        window.__n0 = qa(".tab").length;
        qa(".modal__acts .btn")[0].click();
      }
    },
    function () {
      L.push("取消 → 移除=" + !q(".modal") + " 标签未变=" + (qa(".tab").length === window.__n0));
      var dt = q(".tab__dirty");
      if (dt) dt.closest(".tab").querySelector(".tab__close").click();
      else L.push("（脏标签已关闭，跳过确认路径）");
    },
    function () { if (q(".modal")) qa(".modal__acts .btn")[1].click(); },
    function () { L.push("确认 → 标签 " + window.__n0 + " → " + qa(".tab").length); },
  ]);

  add([
    function () {
      L.push("");
      L.push("=== 右键菜单 ===");
      var t = qa(".tab")[0];
      if (t) t.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, clientX: 260, clientY: 70 }));
    },
    function () {
      L.push("打开=" + !!q(".ctxmenu") + " 项=" + qa(".ctxmenu__item").length +
        " 首项获焦=" + (qa(".ctxmenu__item")[0] === document.activeElement));
      if (document.activeElement && q(".ctxmenu")) {
        document.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
      }
    },
    function () {
      L.push("↓ 焦点=" + (document.activeElement ? document.activeElement.textContent.trim().slice(0, 14) : "无"));
      if (document.activeElement) document.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    },
    function () { L.push("Esc 关闭=" + !q(".ctxmenu")); },
  ]);

  add([
    function () { L.push(""); L.push("=== 命令面板 ==="); q("#btn-cmdk").click(); },
    function () {
      L.push("打开=" + !!q(".palette") + " 项=" + qa(".palette__item").length +
        " 分组=" + qa(".palette__group").length + " 搜索框获焦=" + (document.activeElement === q(".palette__input")));
      var i = q(".palette__input"); i.value = "reconnect"; i.dispatchEvent(new Event("input", { bubbles: true }));
    },
    function () {
      L.push("搜 reconnect → " + qa(".palette__item").length + " 项 首项=" + (qa(".palette__label")[0] || {}).textContent);
      var i = q(".palette__input"); i.value = "zzzzz"; i.dispatchEvent(new Event("input", { bubbles: true }));
    },
    function () {
      L.push("无结果 → 空状态=" + !!q(".palette__empty"));
      var i = q(".palette__input"); i.value = ""; i.dispatchEvent(new Event("input", { bubbles: true }));
    },
    function () {
      L.push("清空恢复 → " + qa(".palette__item").length + " 项");
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    },
    function () { L.push("Esc 关闭=" + !q(".paletteback")); },
  ]);

  add([
    function () {
      L.push("");
      L.push("=== Toast ===");
      UI.toast({ tone: "ok", title: "T1", duration: 400000 });
      UI.toast({ tone: "warn", title: "T2", duration: 400000, actions: [{ label: "撤销", primary: true, onClick: function () { window.__u = 1; } }] });
    },
    function () {
      L.push("数量=" + qa(".toast").length + " 进度条=" + qa(".toast__bar").length + " 操作=" + qa(".toast__act").length);
      qa(".toast__act")[0].click();
    },
    function () {
      L.push("点撤销 → 回调=" + (window.__u === 1) + " 剩余=" + qa(".toast").length);
      if (qa(".toast__x")[0]) qa(".toast__x")[0].click();
    },
    function () { L.push("点 × → 剩余=" + qa(".toast").length); },
  ]);

  add([
    function () {
      L.push("");
      L.push("=== 事件过滤 ===");
      q("#btn-filter-tools").click();
      L.push("仅工具 → 可见=" + qa(".ev").filter(function (n) { return n.getAttribute("data-hidden") !== "true"; }).length +
        " | " + q("#stream-stat").textContent);
    },
    function () {
      q("#btn-filter-gate").click();
      L.push("仅闸门 → 可见=" + qa(".ev").filter(function (n) { return n.getAttribute("data-hidden") !== "true"; }).length);
    },
    function () {
      q("#btn-filter-all").click();
      L.push("全部 → 可见=" + qa(".ev").filter(function (n) { return n.getAttribute("data-hidden") !== "true"; }).length);
    },
  ]);

  add([
    function () { L.push(""); L.push("=== 闸门批准（核心路径）==="); },
    function () {
      L.push("闸门按钮=" + qa(".gate .btn").length + " 暂缓提示=" + (q(".gate__deferred") ? "有" : "无"));
      qa(".gate .btn")[0].click();
    },
    function () {
      L.push("批准后 事件=" + qa(".ev").length + " 自证条=" + !q("#verify-bar").hidden +
        " 计划=" + q("#plan-count").textContent + " 检查卡=" + qa(".checkcard").length);
      L.push("测试通过行=" + qa(".out-pass").length + " 自证项=" + qa(".checklist li").length);
    },
  ]);

  add([
    function () {
      L.push("");
      L.push("=== 终端命令与历史 ===");
      window.__tb = qa("#terminal-body span").length;
      q("#term-input").value = "git status";
      q("#termform").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    },
    function () {
      L.push("git status → 行 " + window.__tb + " → " + qa("#terminal-body span").length);
      var ti = q("#term-input");
      ti.focus();
      ti.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
      L.push("↑ 历史回填=" + JSON.stringify(ti.value));
      ti.value = "boguscmd";
      q("#termform").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    },
    function () { L.push("未知命令提示=" + /command not found/.test(q("#terminal-body").textContent)); },
  ]);

  add([
    function () {
      L.push("");
      L.push("=== @ 与 / 补全 ===");
      var ta = q("#dock-input");
      ta.focus(); ta.value = "@recon"; ta.setSelectionRange(6, 6);
      ta.dispatchEvent(new Event("input", { bubbles: true }));
    },
    function () {
      L.push("@recon → 浮层=" + !q("#mention").hidden + " 项=" + qa(".mention__item").length +
        " 首项=" + (qa(".mention__name")[0] || {}).textContent);
      if (qa(".mention__item")[0]) qa(".mention__item")[0].dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    },
    function () {
      var ta = q("#dock-input");
      L.push("选中后输入框=" + JSON.stringify(ta.value) + " 浮层已关=" + q("#mention").hidden);
      ta.value = "/"; ta.setSelectionRange(1, 1);
      ta.dispatchEvent(new Event("input", { bubbles: true }));
    },
    function () {
      L.push("/ → 命令浮层=" + !q("#mention").hidden + " 项=" + qa(".mention__item").length +
        " 首项=" + (qa(".mention__name")[0] || {}).textContent);
      q("#dock-input").dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    },
    function () { L.push("Esc 关浮层=" + q("#mention").hidden); },
  ]);

  add([
    function () {
      L.push("");
      L.push("=== 标签操作 ===");
      window.__t1 = qa(".tab").length;
      var t = qa(".tab")[1];
      if (t) t.dispatchEvent(new MouseEvent("auxclick", { bubbles: true, button: 1 }));
    },
    function () {
      L.push("中键关闭 " + window.__t1 + " → " + qa(".tab").length);
      window.__cur = (q(".tab.is-on") || {}).textContent;
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "PageDown", ctrlKey: true, bubbles: true }));
    },
    function () {
      L.push("Ctrl+PgDn " + (window.__cur || "").trim().slice(0, 14) + " → " + ((q(".tab.is-on") || {}).textContent || "").trim().slice(0, 14));
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "PageUp", ctrlKey: true, bubbles: true }));
    },
    function () {
      L.push("Ctrl+PgUp 回到 " + ((q(".tab.is-on") || {}).textContent || "").trim().slice(0, 14));
      L.push("标签溢出 scrollW=" + q(".tabbar__scroll").scrollWidth + " clientW=" + q(".tabbar__scroll").clientWidth +
        " more=" + (q("#tabbar-more").hidden ? "隐藏" : "显示") + " count=" + (q("#tabbar-count").hidden ? "隐藏" : q("#tabbar-count").textContent));
    },
  ]);

  (function () {
    var views = ["explorer", "search", "scm", "sessions", "mcp"];
    var names = { explorer: "资源管理器", search: "搜索", scm: "源码管理", sessions: "会话历史", mcp: "MCP 工具" };
    L.push("");
    L.push("=== 侧栏五视图 ===");
    views.forEach(function (v) {
      add([
        function () { var b = document.querySelector('.actbar__btn[data-view="' + v + '"]'); if (b) b.click(); },
        function () {
          L.push(v + ": 标题=" + q("#rail-title").textContent + " 交互项=" + qa("#rail-body button").length +
            " " + (q("#rail-title").textContent === names[v] ? "✓" : "✗ 期望 " + names[v]));
        },
      ]);
    });
  })();

  add([
    function () {
      L.push("");
      L.push("=== 侧栏宽度拖拽 ===");
      window.__rw0 = q(".rail").getBoundingClientRect().width;
      var rh = q("#rail-resize");
      rh.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, clientX: 300, pointerId: 7 }));
      rh.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: 400, pointerId: 7 }));
      rh.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, clientX: 400, pointerId: 7 }));
    },
    function () {
      L.push("宽度 " + Math.round(window.__rw0) + " → " + Math.round(q(".rail").getBoundingClientRect().width) + " (期望 +100)");
      L.push("已存 railW=" + (JSON.parse(localStorage.getItem("crucible.ui.v1") || "{}").railW || "无"));
    },
  ]);

  add([
    function () {
      L.push("");
      L.push("=== 持久化 ===");
      q("#btn-theme").click();
      q("#btn-agent-toggle").click();
    },
    function () {
      var st = JSON.parse(localStorage.getItem("crucible.ui.v1") || "{}");
      L.push("theme=" + st.theme + " | view=" + st.activeView + " | filter=" + st.filter +
        " | agentHidden=" + st.agentHidden + " | railW=" + st.railW);
      L.push("shell=" + q(".shell").className);
      q("#btn-agent-toggle").click();
    },
  ]);

  add([function () {
    L.push("");
    L.push("=== 行号一致性（回归）===");
    var g = q("#editor-gutter").textContent.trim().split("\n");
    var c = q("#editor-code").textContent.split("\n");
    L.push("gutter=" + g.length + " code=" + c.length + " 一致=" + (g.length === c.length));
  }]);

  add([
    function () {
      L.push("");
      L.push("=== 无障碍抽查 ===");
      var noname = qa("button").filter(function (b) {
        if (b.closest("[hidden]") || !vis(b)) return false;
        return !(b.textContent || "").trim() && !b.getAttribute("aria-label") && !b.getAttribute("data-tip");
      });
      L.push("无名称按钮=" + (noname.length ? noname.map(function (b) { return b.className.split(" ")[0]; }).join(",") : "无"));
      L.push("aria-live=" + qa("[aria-live]").length + " role=log=" + qa("[role=log]").length +
        " role=dialog=" + qa("[role=dialog]").length);
      var small = qa("button, input, textarea").filter(function (b) {
        if (b.closest("[hidden]") || !vis(b)) return false;
        var r = b.getBoundingClientRect();
        return r.width > 0 && (r.height < 24 || r.width < 20);
      });
      L.push("热区偏小(" + small.length + ")=" + (small.length ? small.slice(0, 8).map(function (b) {
        return (b.getAttribute("aria-label") || b.className.split(" ")[0]) + " " +
          Math.round(b.getBoundingClientRect().width) + "x" + Math.round(b.getBoundingClientRect().height);
      }).join(" ; ") : "无"));
    },
    function () {
      L.push("");
      L.push("=== JS 错误 ===");
      L.push(errs.length ? errs.join("\n") : "0");
      render();
    },
  ]);

  function schedule() {
    var i = 0;
    (function next() {
      if (i >= S.length) { render(); return; }
      try { S[i](); } catch (e) { L.push("!! step " + i + " 异常: " + e.message); }
      i++; render();
      setTimeout(next, 110);
    })();
  }

  function boot() { setTimeout(schedule, 1000); }
  if (document.readyState === "complete") boot();
  else window.addEventListener("load", boot);
})();
