(function () {
  var L = [];
  function q(s) { return document.querySelector(s); }
  function r() {
    var pre = document.getElementById("__t") || document.createElement("pre");
    pre.id = "__t";
    pre.style.cssText = "position:fixed;left:0;top:0;z-index:99999;background:#000;color:#0f0;padding:8px;font:11px/1.5 monospace;white-space:pre-wrap;max-height:100vh;overflow:auto;margin:0";
    pre.textContent = L.join("\n");
    document.body.appendChild(pre);
  }
  var seq = [
    function () {
      var wb = q(".workbench"), t = q(".terminal");
      L.push("折叠前: rows=" + getComputedStyle(wb).gridTemplateRows +
        " termH=" + Math.round(t.getBoundingClientRect().height) +
        " vis=" + getComputedStyle(t).visibility);
      q("#btn-term-toggle").click();
    },
    function () {
      var wb = q(".workbench"), t = q(".terminal");
      L.push("折叠后(下一 tick): rows=" + getComputedStyle(wb).gridTemplateRows +
        " termH=" + Math.round(t.getBoundingClientRect().height) +
        " vis=" + getComputedStyle(t).visibility);
      q("#btn-term-toggle").click();
    },
    function () {
      var t = q(".terminal");
      L.push("展开后: termH=" + Math.round(t.getBoundingClientRect().height) +
        " vis=" + getComputedStyle(t).visibility);
      document.documentElement.style.setProperty("--terminal-h", "300px");
    },
    function () {
      L.push("设 300px 后: termH=" + Math.round(q(".terminal").getBoundingClientRect().height));
      q("#btn-term-toggle").click();
    },
    function () {
      var wb = q(".workbench"), t = q(".terminal");
      L.push("★ 拖过之后折叠: rows=" + getComputedStyle(wb).gridTemplateRows +
        " termH=" + Math.round(t.getBoundingClientRect().height) +
        " vis=" + getComputedStyle(t).visibility + "  ← 必须 <60 且 hidden");
      document.documentElement.style.removeProperty("--terminal-h");
      r();
    },
  ];
  var i = 0;
  function next() {
    if (i >= seq.length) { r(); return; }
    try { seq[i](); } catch (e) { L.push("!! " + e.message); }
    i++; r();
    setTimeout(next, 120);
  }
  setTimeout(next, 1200);
})();
