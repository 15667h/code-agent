/* 收尾探针：只查几件必须确定的事 —— 关闭动画是否真的移除节点、热区是否达标、几何是否零溢出 */
(function () {
  var L = [], errs = [];
  window.addEventListener("error", function (e) { errs.push(e.message); });
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
  function add(a) { a.forEach(function (f) { S.push(f); }); }

  add([
    function () {
      L.push("=== 几何：横向溢出 ===");
      var de = document.documentElement;
      L.push("html scrollW=" + de.scrollWidth + " clientW=" + de.clientWidth +
        " 溢出=" + (de.scrollWidth - de.clientWidth));
      var over = [];
      [".shell", ".topbar", ".statusbar", ".tabbar", ".workbench", ".agentpanel", ".rail"].forEach(function (s) {
        var n = q(s); if (!n) return;
        var r = n.getBoundingClientRect();
        if (r.right - de.clientWidth > 1.5) over.push(s + " 右溢 " + Math.round(r.right - de.clientWidth));
        if (r.left < -1.5) over.push(s + " 左溢 " + Math.round(-r.left));
      });
      L.push("关键容器溢出=" + (over.length ? over.join(" ; ") : "无"));
    },
  ]);

  /* 确认框：等足够久再确认节点已移除 */
  add([
    function () { L.push(""); L.push("=== 确认框移除（跨 tick 等待）==="); 
      var dt = q(".tab__dirty");
      if (dt) dt.closest(".tab").querySelector(".tab__close").click();
    },
    function () { L.push("弹框出现=" + !!q(".modal")); qa(".modal__acts .btn")[0].click(); },
    function () { L.push("取消后 200ms：modal=" + !!q(".modal") + " backdrop=" + !!q(".modalback") + " body overflow=" + JSON.stringify(document.body.style.overflow)); },
    function () {
      var dt = q(".tab__dirty");
      if (dt) dt.closest(".tab").querySelector(".tab__close").click();
    },
    function () { if (q(".modal")) qa(".modal__acts .btn")[1].click(); },
    function () { L.push("确认后：modal=" + !!q(".modal") + " 标签数=" + qa(".tab").length); },
  ]);

  /* Toast 自动消失 */
  add([
    function () { L.push(""); L.push("=== Toast 自动消失 ==="); UI.toast({ tone: "ok", title: "短命", duration: 300 }); },
    function () { L.push("刚创建 数量=" + qa(".toast").length); },
    function () { L.push("+700ms 数量=" + qa(".toast").length + "（应为 0）"); },
  ]);

  /* 热区（用 elementFromPoint 实测点击命中，而不是只看盒子尺寸） */
  add([
    function () {
      L.push("");
      L.push("=== 热区实测（中心点是否命中自身）===");
      var targets = [
        [".switch", "开关"],
        [".tab__close", "标签关闭"],
        [".chip", "过滤器"],
        [".statusitem--btn", "状态栏项"],
        [".termtab", "终端标签"],
        [".sect__head", "侧栏分区"],
        [".iconbtn--sm", "小图标按钮"],
      ];
      targets.forEach(function (t) {
        var nodes = qa(t[0]).filter(vis);
        if (!nodes.length) { L.push(t[1] + ": 无可见实例"); return; }
        var n = nodes[0];
        var r = n.getBoundingClientRect();
        var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
        var hit = document.elementFromPoint(cx, cy);
        var ok = hit && (n.contains(hit) || hit.contains(n) || hit === n);
        L.push(t[1] + ": 盒 " + Math.round(r.width) + "x" + Math.round(r.height) +
          " · 中心命中自身=" + ok + (ok ? " ✓" : " ✗"));
      });
      // 伪元素外扩是否真的在扩大命中区：在盒子外 6px 处测
      L.push("");
      L.push("--- 盒外 6px 处是否仍命中（验证伪元素外扩）---");
      var sw = qa(".switch").filter(vis)[0];
      if (sw) {
        var r2 = sw.getBoundingClientRect();
        var h1 = document.elementFromPoint(r2.left - 5, r2.top + r2.height / 2);
        var h2 = document.elementFromPoint(r2.left + r2.width / 2, r2.top - 9);
        L.push("开关左外 5px 命中=" + (h1 && (sw.contains(h1) || h1 === sw)) +
          " · 上外 9px 命中=" + (h2 && (sw.contains(h2) || h2 === sw)));
      }
    },
  ]);

  /* 键盘可达性：Tab 能否走到主要控件 */
  add([
    function () {
      L.push("");
      L.push("=== 键盘可达 ===");
      var focusables = qa('a[href],button:not([disabled]),input,textarea,[tabindex]:not([tabindex="-1"])')
        .filter(function (n) { return vis(n); });
      L.push("可聚焦元素=" + focusables.length);
      // 抽 3 个主控件看是否在列表里
      [["#btn-cmdk", "命令面板"], ["#term-input", "终端输入"], ["#dock-input", "指令输入"]].forEach(function (p) {
        var n = q(p[0]);
        L.push(p[1] + " 可聚焦=" + (n && focusables.indexOf(n) > -1));
      });
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
      try { S[i](); } catch (e) { L.push("!! step " + i + ": " + e.message); }
      i++; render();
      setTimeout(next, 320);
    })();
  }
  function boot() { setTimeout(schedule, 1000); }
  if (document.readyState === "complete") boot();
  else window.addEventListener("load", boot);
})();
