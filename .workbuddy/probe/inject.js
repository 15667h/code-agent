// 把探针脚本注入到 index.html 的副本里，避免 iframe 跨文档与虚拟时间刷新问题
const fs = require("fs");
const path = require("path");

const root = "C:/Users/34442/Desktop/porject/代码开发agent";
const src = fs.readFileSync(path.join(root, "index.html"), "utf8");

const probe = fs.readFileSync(path.join(root, ".workbuddy/probe/inject.js"), "utf8");

let out = src
  .replace(/href="styles\//g, 'href="../../styles/')
  .replace(/src="scripts\//g, 'src="../../scripts/')
  .replace("</body>", "<script>\n" + probe + "\n</" + "script>\n</body>");

const dest = path.join(root, ".workbuddy/probe/injected.html");
fs.writeFileSync(dest, out);
console.log("注入完成:", dest, "字节:", out.length);
console.log("含探针:", /CRUCIBLE_PROBE/.test(out));
