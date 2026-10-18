/* ============================================================
   Crucible — 界面帮助常量（**不再是 mock 数据**）
   ============================================================
   这个文件原来叫 mock-data.js，装满了假的文件树、事件流、会话、
   工具清单与终端输出。随着前端逐块接上真实数据，那些常量已经
   **全部零引用**，于是一次性删除 —— 留着它们只会让下一个人
   误以为页面某处还在用假数据。

   剩下这一个块是**真的需要在前端硬编码**的：键盘快捷键总览。
   它不是服务端数据（harness 不需要知道我们有哪些快捷键），
   而是界面自身的说明书；且它的内容必须与真实绑定一致 ——
   下面每一条都在 app.js 里有对应的 addEventListener。

   判据：这里的 `keys` 组合在 app.js 里能搜到对应的按键处理。
   改动快捷键时两处都要动，所以这条注记写在这里。
   ============================================================ */

const SHORTCUTS = [
  { group: "通用", items: [
    { keys: ["Ctrl", "K"], desc: "打开命令面板" },
    { keys: ["Ctrl", "B"], desc: "显示 / 隐藏 Agent 面板" },
    { keys: ["Ctrl", "`"], desc: "显示 / 隐藏终端" },
    { keys: ["Ctrl", "Shift", "E"], desc: "聚焦资源管理器" },
    { keys: ["Ctrl", "Shift", "F"], desc: "在工作区内搜索" },
    { keys: ["Ctrl", "S"], desc: "保存当前文件" },
    { keys: ["?"], desc: "打开本快捷键总览" },
  ] },
  { group: "Agent", items: [
    { keys: ["Enter"], desc: "发送指令" },
    { keys: ["Shift", "Enter"], desc: "换行" },
    { keys: ["Shift", "Tab"], desc: "切到计划模式" },
    { keys: ["Esc"], desc: "中断当前运行 / 关闭浮层" },
  ] },
  { group: "编辑器与标签", items: [
    { keys: ["Ctrl", "W"], desc: "关闭当前标签" },
    { keys: ["Ctrl", "PageUp"], desc: "上一个标签" },
    { keys: ["Ctrl", "PageDown"], desc: "下一个标签" },
    { keys: ["中键"], desc: "关闭被点击的标签" },
    { keys: ["双击"], desc: "重命名文件" },
  ] },
  { group: "侧栏与文件树", items: [
    { keys: ["↑", "↓"], desc: "在文件树中移动" },
    { keys: ["→"], desc: "展开目录" },
    { keys: ["←"], desc: "折叠目录" },
    { keys: ["Home", "End"], desc: "跳到首 / 末项" },
    { keys: ["Enter"], desc: "打开选中项" },
  ] },
  { group: "终端", items: [
    { keys: ["↑", "↓"], desc: "翻阅命令历史" },
    { keys: ["Ctrl", "L"], desc: "清空屏幕" },
  ] },
];
