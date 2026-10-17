/* 几何探针（简洁版）：只报告自身尺寸与溢出 */
(function () {
  function geo() {
    var de = document.documentElement;
    var over = [];
    [".shell", ".topbar", ".statusbar", ".tabbar", ".workbench", ".agentpanel", ".rail"].forEach(function (s) {
      var n = document.querySelector(s);
      if (!n) return;
      var r = n.getBoundingClientRect();
      if (r.right - de.clientWidth > 1.5) over.push(s + "+" + Math.round(r.right - de.clientWidth));
      if (r.left < -1.5) over.push(s + "-" + Math.round(-r.left));
    });
    var st = document.querySelector(".stream");
    var rb = document.querySelector(".rail-body");
    return "W=" + de.clientWidth +
      " 溢出=" + (de.scrollWidth - de.clientWidth) +
      " 子溢出=" + (over.length ? over.join(",") : "无") +
      " streamH=" + (st ? Math.round(st.getBoundingClientRect().height) : "?") +
      " railH=" + (rb ? Math.round(rb.getBoundingClientRect().height) : "?");
  }
  function show() {
    var out = document.getElementById("__geo");
    if (!out) {
      out = document.createElement("pre");
      out.id = "__geo";
      out.style.cssText = "position:fixed;left:0;bottom:0;z-index:99999;background:#000;color:#0f0;padding:6px;font:11px monospace;margin:0";
      document.body.appendChild(out);
    }
    try { out.textContent = geo(); }
    catch (e) { out.textContent = "探针异常: " + e.message; }
  }
  if (document.readyState === "complete") setTimeout(show, 900);
  else window.addEventListener("load", function () { setTimeout(show, 900); });
})();
