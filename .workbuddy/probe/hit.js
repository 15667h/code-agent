/* 热区实测（隐藏探针浮层后再测，避免自己盖住命中点） */
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
      out.style.cssText = "position:fixed;right:0;bottom:30px;z-index:99999;background:#000;color:#0f0;padding:10px;font:11px/1.5 monospace;white-space:pre-wrap;width:640px;max-height:60vh;overflow:auto;margin:0";
      document.body.appendChild(out);
    }
    out.textContent = L.join("\n");
  }
  /* 测量前把浮层移出视口，测完再放回来 */
  function withOverlayHidden(fn) {
    var prev = out ? out.style.display : null;
    if (out) out.style.display = "none";
    var r;
    try { r = fn(); } finally { if (out) out.style.display = prev || "block"; }
    return r;
  }
  function add(a) { a.forEach(function (f) { S.push(f); }); }

  add([function () {
    L.push("=== 热区实测（浮层已隐藏）===");
    var cases = [
      [".switch", "开关"],
      [".tab__close", "标签关闭"],
      [".chip", "过滤器 chip"],
      [".statusitem--btn", "状态栏项"],
      [".termtab", "终端标签"],
      [".sect__head", "侧栏分区标题"],
      [".iconbtn--sm", "小图标按钮"],
      [".copybtn", "复制按钮"],
      [".tab", "标签"],
      [".fileitem", "文件树项"],
    ];
    withOverlayHidden(function () {
      cases.forEach(function (c) {
        var nodes = qa(c[0]).filter(vis);
        if (!nodes.length) { L.push(c[1] + ": 无可见实例"); return; }
        var n = nodes[0];
        var r = n.getBoundingClientRect();
        var hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        var ok = !!hit && (n.contains(hit) || hit === n || hit.contains(n));
        L.push(c[1] + ": 盒 " + Math.round(r.width) + "x" + Math.round(r.height) +
          " · 中心命中=" + (ok ? "✓" : "✗ 命中=" + (hit ? hit.className || hit.tagName : "null")));
      });
      L.push("");
      L.push("--- 伪元素外扩：盒外仍可命中 ---");
      var sw = qa(".switch").filter(vis)[0];
      if (sw) {
        var r2 = sw.getBoundingClientRect();
        var pts = [
          ["左外 5px", r2.left - 5, r2.top + r2.height / 2],
          ["右外 5px", r2.right + 5, r2.top + r2.height / 2],
          ["上外 9px", r2.left + r2.width / 2, r2.top - 9],
        ];
        pts.forEach(function (p) {
          var h = document.elementFromPoint(p[1], p[2]);
          L.push("  开关 " + p[0] + " 命中=" + (h && (sw.contains(h) || h === sw) ? "✓" : "✗"));
        });
      }
      return null;
    });
  }]);

  add([
    function () { L.push(""); L.push("=== Toast 自动消失（等足时长）==="); UI.toast({ tone: "ok", title: "短命", duration: 400 }); },
    function () { L.push("刚创建=" + qa(".toast").length); },
    function () { L.push("+350ms=" + qa(".toast").length); },
    function () { L.push("+700ms=" + qa(".toast").length + "（应 0）"); },
    function () { L.push("+1050ms=" + qa(".toast").length); },
  ]);

  add([
    function () { L.push(""); L.push("=== js 错误 ==="); L.push(errs.length ? errs.join("\n") : "0"); render(); },
  ]);

  function schedule() {
    var i = 0;
    (function next() {
      if (i >= S.length) { render(); return; }
      try { S[i](); } catch (e) { L.push("!! " + e.message); }
      i++; render();
      setTimeout(next, 350);
    })();
  }
  function boot() { setTimeout(schedule, 1000); }
  if (document.readyState === "complete") boot();
  else window.addEventListener("load", boot);
})();
