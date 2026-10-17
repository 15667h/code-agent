/* 320px 溢出定位：找出所有 scrollWidth > clientWidth 的元素 */
(function () {
  function diag() {
    var de = document.documentElement;
    var L = [];
    L.push("W=" + de.clientWidth + " 页面溢出=" + (de.scrollWidth - de.clientWidth));
    var bad = [];
    Array.prototype.forEach.call(document.querySelectorAll("*"), function (n) {
      var d = n.scrollWidth - n.clientWidth;
      if (d > 1) {
        var cs = getComputedStyle(n);
        bad.push((n.className && n.className.toString().split(" ")[0] || n.tagName) +
          " sw=" + n.scrollWidth + " cw=" + n.clientWidth +
          " ovx=" + cs.overflowX + " ws=" + cs.whiteSpace.slice(0, 10) +
          " minW=" + cs.minWidth);
      }
    });
    L.push("--- 内部超出 ---");
    bad.slice(0, 12).forEach(function (b) { L.push(b); });
    if (!bad.length) L.push("无");

    // 找最宽的叶子
    var widest = null, mw = 0;
    Array.prototype.forEach.call(document.querySelectorAll("*"), function (n) {
      if (n.children.length) return;
      if (!(n.textContent || "").trim()) return;
      var r = n.getBoundingClientRect();
      if (r.width > mw) { mw = r.width; widest = n; }
    });
    if (widest) {
      L.push("--- 最宽叶子 ---");
      L.push((widest.className || widest.tagName) + " w=" + Math.round(mw) +
        " right=" + Math.round(widest.getBoundingClientRect().right));
      L.push("文本: " + widest.textContent.trim().slice(0, 60));
    }

    // 关键容器宽度
    L.push("--- 关键容器 ---");
    [".topbar", ".shell", ".statusbar", ".tabbar", ".workbench", ".editor", ".terminal", ".streambar", ".dock"].forEach(function (s) {
      var n = document.querySelector(s);
      if (!n) return;
      var r = n.getBoundingClientRect();
      L.push(s + " w=" + Math.round(r.width) + " right=" + Math.round(r.right) + " sw=" + n.scrollWidth);
    });

    var out = document.getElementById("__geo") || document.createElement("pre");
    out.id = "__geo";
    out.style.cssText = "position:fixed;left:0;top:0;z-index:99999;background:#000;color:#0f0;padding:6px;font:11px monospace;margin:0;white-space:pre-wrap;width:100%;max-height:100vh;overflow:auto";
    out.textContent = L.join("\n");
    document.body.appendChild(out);
  }
  if (document.readyState === "complete") setTimeout(diag, 900);
  else window.addEventListener("load", function () { setTimeout(diag, 900); });
})();
