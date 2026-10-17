const fs = require("fs");
const root = "C:/Users/34442/Desktop/porject/代码开发agent/";

// CSS 花括号配平
["styles/tokens.css", "styles/app.css", "styles/ui-kit.css"].forEach(function (f) {
  const s = fs.readFileSync(root + f, "utf8");
  let o = 0, c = 0;
  for (const ch of s) { if (ch === "{") o++; if (ch === "}") c++; }
  console.log(f.padEnd(22), "{=" + o, "}=" + c, o === c ? "OK" : "❌ 不平衡");
});

// HTML 标签配平
const h = fs.readFileSync(root + "index.html", "utf8");
["div", "button", "aside", "main", "section", "form", "ul", "li", "pre", "header", "footer", "nav"].forEach(function (t) {
  const open = (h.match(new RegExp("<" + t + "[ >]", "g")) || []).length;
  const close = (h.match(new RegExp("</" + t + ">", "g")) || []).length;
  console.log(t.padEnd(9), "open=" + open, "close=" + close, open === close ? "OK" : "❌");
});

// 残留占位/调试痕迹
["undefined", "NaN", "TODO", "FIXME", "console.log", "debugger"].forEach(function (k) {
  let n = 0;
  ["index.html", "scripts/app.js", "scripts/ui-kit.js", "scripts/mock-data.js"].forEach(function (f) {
    const s = fs.readFileSync(root + f, "utf8");
    n += (s.match(new RegExp(k.replace(/[.$]/g, "\\$&"), "g")) || []).length;
  });
  console.log("残留 " + k.padEnd(12) + " = " + n);
});

// 检查是否还有 title= 未换成 data-tip 的地方（统一 tooltip 机制）
const appCss = fs.readFileSync(root + "styles/app.css", "utf8");
console.log("\n--- 交付文件 ---");
["index.html", "styles/tokens.css", "styles/app.css", "styles/ui-kit.css", "scripts/mock-data.js", "scripts/ui-kit.js", "scripts/app.js",
 "docs/codex-architecture.md", "docs/design-plan.md", "README.md"].forEach(function (f) {
  const st = fs.statSync(root + f);
  console.log(f.padEnd(34), String(st.size).padStart(7) + " B");
});
