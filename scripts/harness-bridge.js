/* ============================================================
   Crucible — harness 桥接层
   把后端 harness 的真实事件流翻译成前端已有的"视图模型"，
   让同一个事件流渲染器既能渲染 mock 数据，也能渲染真实运行。

   设计取舍：
   1. **不新造一套渲染**。前端已有 buildEvent(ev) 契约，
      桥接层的唯一职责是 harness Event → 那个契约的 ev。
      这样「换数据源」不会引出第二种代码路径（书 §1：不要有两套真相）。
   2. **离线可降级**。后端不在时（file:// 直接打开 index.html），
      桥接层完全不介入，页面维持 mock 演示。这是有意的：
      静态原型仍然可以双击打开自证 UI，而不是白屏。
   3. **审批不伪造**。真实运行时闸门按钮回灌 /api/run/<id>/approve，
      而不是只在前端改本地状态 —— 否则「批准」是个装饰。
   ============================================================ */
(function (global) {
  "use strict";

  var API = "";                 // 同源托管，空串即可
  var state = {
    live: false,                // 是否已探测到 harness 后端
    runId: null,
    es: null,                   // EventSource
    startedAt: 0,
    seq: 0,
    pendingGate: null,          // 当前待决闸门的 call_id
    callIndex: {},              // call_id → 该调用的 tool.requested 视图模型
    lastEventAt: 0,
  };

  function get(path) {
    return fetch(API + path, { headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); });
  }
  function post(path, body) {
    return fetch(API + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); });
  }

  /* ---------- 后端可用性探测 ----------
     用 /api/health 探一次：通了才切到 live 模式。
     file:// 下 fetch 会直接 reject，静默忽略即可 —— 不打扰用户。 */
  function probe() {
    return fetch(API + "/api/health", { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (j && j.ok) { state.live = true; return true; }
        return false;
      })
      .catch(function () { return false; });
  }

  /* ---------- 时间戳：用会话起始偏移，前端统一走 fmtClock ---------- */
  function offsetOf(tsSec) {
    if (!state.startedAt) return 0;
    return Math.max(0, Math.round((tsSec * 1000) - state.startedAt));
  }

  /* ---------- 工具名 → 前端图标键 ----------
     前端 TOOL_ICON 只有 code_search/read/shell/apply_patch 四个。
     harness 的工具名更细，这里做一次映射，避免图标全空。 */
  function iconTool(name) {
    switch (name) {
      case "read_file": case "list_dir": return "read";
      case "grep": case "glob": return "code_search";
      case "apply_patch": return "apply_patch";
      case "shell": return "shell";
      default: return name;
    }
  }

  /* ---------- 把 tool.output 的文本切成前端 output 行 ----------
     前端 output 是 [{t, text}]，t ∈ plain/pass/fail/dim。
     这里只做最朴素的行切 + 命中标记，不做花哨推断：
     宁可少标色，也不要误标色（书 §1 消融实验的教训）。 */
  function toOutputLines(text, ok) {
    if (!text) return [];
    var rows = String(text).split("\n");
    var cap = 60;
    var out = rows.slice(0, cap).map(function (line) {
      var t = "plain";
      if (/^\s*(FAIL|FAILED|Error|error:|✗|失败)/.test(line)) t = "fail";
      else if (/^\s*(ok |OK |PASS|passed|✓|通过)/.test(line)) t = "pass";
      else if (/^\s*(…|\.\.\.|\(已省略|已截断)/.test(line)) t = "dim";
      return { t: t, text: line };
    });
    if (rows.length > cap) out.push({ t: "dim", text: "… 共 " + rows.length + " 行，此处只显示前 " + cap + " 行" });
    if (!out.length) out.push({ t: ok ? "dim" : "fail", text: ok ? "（无输出）" : "（执行失败）" });
    return out;
  }

  /* ---------- harness Event → 前端 ev ----------
     这是本文件的核心：一张显式的翻译表。
     任何新增的事件类型都必须在这里被显式处理，未处理的会落到 null
     并被丢弃，而不是渲染成空白卡片（避免"看上去正常"的假象）。 */
  function translate(e) {
    var d = e.data || {};
    var tsOffset = offsetOf(e.ts);

    switch (e.type) {
      case "turn.started":
        return {
          kind: "reasoning", state: "done", tsOffset: tsOffset,
          text: "开始执行。工作区 " + (d.workspace || "") +
                " · 沙箱 " + (d.sandbox || "") + " · 审批 " + (d.approval || "") +
                " · 工具 " + ((d.tool_order || []).length) + " 个（顺序固定）。",
        };

      case "reasoning":
        if (!d.text) return null;
        return { kind: "reasoning", state: "done", tsOffset: tsOffset, text: d.text };

      case "message":
        if (!d.text) return null;
        return { kind: "reasoning", state: "done", tsOffset: tsOffset, text: d.text };

      case "tool.requested":
        // 先建一个 running 卡片；tool.output 到达时再就地补全（同一 call_id）。
        var vm = {
          kind: "tool", state: "running", tsOffset: tsOffset,
          tool: iconTool(d.tool), toolRaw: d.tool,
          title: (d.tool || "") + " " + compactArgs(d.args),
          meta: [],
          output: null,
          _callId: d.call_id,
        };
        if (d.call_id) state.callIndex[d.call_id] = vm;
        // 危险升格 / 拒绝：本身就是重要信息，放进 meta
        if (d.escalated_by) vm.meta.push({ k: "升格", v: d.escalated_by });
        if (d.decision) vm.meta.push({ k: "判定", v: d.decision });
        if (d.risk) vm.meta.push({ k: "风险", v: d.risk });
        return vm;

      case "approval.required":
        if (d.state === "resolved" || d.decision) {
          // 已决的回灌（含"本会话已记住"的自动放行）—— 不渲染成闸门，只留一行记录
          if (d.auto) {
            return {
              kind: "reasoning", state: "done", tsOffset: tsOffset,
              text: "已自动放行（本会话已记住该操作的批准）：" + (d.reason || ""),
            };
          }
          return null;
        }
        state.pendingGate = d.call_id || null;
        return {
          kind: "gate", state: "pending", tsOffset: tsOffset,
          action: d.action || d.tool || "action",
          detail: d.summary || d.command || d.reason || "",
          risk: d.risk || "待判定",
          reason: d.reason || "",
          deferred: d.deferred || 0,
          escalatedBy: d.escalated_by || null,
          _callId: state.pendingGate,
        };

      case "tool.output":
        // 把结果补给对应的 requested 卡片；找不到就独立成一张卡
        var target = d.call_id ? state.callIndex[d.call_id] : findLatestRunning();
        if (target) {
          target.state = "done";
          target.dur = d.dur != null ? fmtDur(d.dur) : null;
          target.output = toOutputLines(d.text, d.ok);
          target.meta = target.meta || [];
          if (d.exit_code != null) target.meta.push({ k: "退出码", v: String(d.exit_code) });
          if (d.truncated) target.meta.push({ k: "截断", v: "是" });
          if (!d.ok && d.error_layer) target.meta.push({ k: "错误层", v: d.error_layer });
          // 已在原卡片上就地补全 —— 不新起一条，由 feed() 通知宿主刷新那一张
          return null;
        }
        return {
          kind: "tool", state: "done", tsOffset: tsOffset,
          tool: iconTool(d.tool || "tool"), toolRaw: d.tool,
          title: (d.tool || "工具") + (d.ok === false ? "（失败）" : ""),
          meta: d.exit_code != null ? [{ k: "退出码", v: String(d.exit_code) }] : [],
          output: toOutputLines(d.text, d.ok),
        };

      case "file.changed":
        // 前端 buildPatch 认的是 {kind:"patch", file}，但需要 diff 内容才有价值。
        // 后端把 diff 放在 data.diff；没有就退化成一条说明，不假装有 diff。
        return {
          kind: "patch", state: "done", tsOffset: tsOffset,
          file: d.path || d.file || "（未知文件）",
          diffText: d.diff || "",
        };

      case "check":
        // 目标判定有三种状态：satisfied / pending / unmatched。
        // 不能都渲染成一个叉 —— "无法自动判定"和"确实没做到"是两回事，
        // 混在一起会让人误以为闸门在瞎报。
        var goalScope = d.scope === "goal";
        // 首次挂上证据的单条事件（newly_met）只报一条增量，不刷整块清单
        if (goalScope && d.newly_met) {
          return {
            kind: "check", state: "done", tsOffset: tsOffset,
            title: "目标条件已挂上证据",
            model: "goal evaluator",
            items: [{ ok: true, text: d.newly_met + "（" + (d.satisfied || 0) + "/" + (d.total || 0) + "）" }],
          };
        }
        if (goalScope && d.warning === "unmatched_conditions") {
          return {
            kind: "check", state: "done", tsOffset: tsOffset,
            title: "目标条件无法自动判定",
            model: "goal evaluator",
            items: (d.items || []).map(function (it) {
              return { ok: false, warn: true, text: it.text + " —— 无法自动判定，需人工复核" };
            }),
          };
        }
        // 完成前检查的通过事件（只有提醒、无硬缺口）
        if (d.scope === "preflight" && d.ok) {
          return {
            kind: "check", state: "done", tsOffset: tsOffset,
            title: "完成前检查通过（有提醒）",
            model: "preflight",
            items: (d.warnings || []).map(function (f) {
              return { ok: true, warn: true, text: f.detail + " → " + f.remedy };
            }).concat([{ ok: true, text: d.reason || "" }]),
          };
        }
        return {
          kind: "check", state: "done", tsOffset: tsOffset,
          title: goalScope ? "目标达成判定" : "工具结果验证",
          model: goalScope ? "goal evaluator" : "verifier",
          items: (d.items || []).map(function (it) {
            var st = it.status;
            return {
              ok: !!it.ok,
              // unmatched 用"未知"语气，不能用"失败"语气
              warn: st === "unmatched" || st === "pending",
              text: (it.text || it.reason || String(it))
                + (it.evidence ? " · 证据：" + it.evidence : "")
                + (st === "unmatched" ? " · 无法自动判定" : "")
                + (st === "pending" ? " · 尚无证据" : ""),
            };
          }),
        };

      case "retry":
        return {
          kind: "reasoning", state: "done", tsOffset: tsOffset,
          text: "重试第 " + d.attempt + " 次（等待 " + d.delay_s + "s）：" + (d.reason || ""),
        };

      case "circuit.open":
        return {
          kind: "check", state: "done", tsOffset: tsOffset,
          title: "熔断打开",
          model: "corrector",
          items: [{ ok: false, text: (d.reason || "连续失败超过阈值，已停止重试") }],
        };

      case "loop.detected":
        return {
          kind: "check", state: "done", tsOffset: tsOffset,
          title: "检测到重复调用",
          model: "corrector",
          items: [{ ok: false, text: (d.reason || "") + "（工具 " + (d.tool || "") + "）" }],
        };

      case "context.compacted":
        return {
          kind: "reasoning", state: "done", tsOffset: tsOffset,
          text: "上下文已压缩：保留最近 " + (d.keep || "?") + " 条，其余折叠为摘要。",
        };

      case "error":
        // 完成前检查的拦截：事件里的 **findings** 才是真正有用的信息
        // （哪一条动作没发生、为什么、要补什么）。只显示 reason 会把
        // 具体的缺口全丢掉，那就等于又变回一句笼统的"出错了"。
        if (d.cls === "preflight_incomplete" && (d.findings || []).length) {
          return {
            kind: "check", state: "done", tsOffset: tsOffset,
            title: "完成前检查：拦下 " + d.findings.length + " 项未做的动作",
            model: "preflight",
            items: d.findings.map(function (f) {
              return {
                ok: false,
                text: f.detail + " → 要做的：" + f.remedy
                  + ((f.evidence || []).length ? "（依据：" + f.evidence.join("；") + "）" : ""),
              };
            }),
          };
        }
        return {
          kind: "check", state: "done", tsOffset: tsOffset,
          title: "错误（" + (d.error_layer || "未知层") + "）",
          model: d.cls || "harness",
          items: [{ ok: false, text: (d.reason || "") + (d.detail ? " :: " + d.detail : "") }],
        };

      case "turn.completed":
        return {
          kind: "check", state: "done", tsOffset: tsOffset,
          title: "回合结束 · " + (d.status || "completed"),
          model: "harness",
          items: [{
            ok: d.status === "completed",
            text: (d.reason || d.answer || "").slice(0, 300),
          }],
        };

      default:
        // 显式丢弃未识别类型，不产出空卡片
        return null;
    }
  }

  function compactArgs(args) {
    if (!args) return "";
    try {
      var s = JSON.stringify(args);
      return s === "{}" ? "" : (s.length > 90 ? s.slice(0, 90) + "…" : s);
    } catch (e) { return ""; }
  }
  function fmtDur(sec) {
    if (sec == null) return null;
    return sec >= 1 ? sec.toFixed(1) + "s" : Math.round(sec * 1000) + "ms";
  }
  function findLatestRunning() {
    var keys = Object.keys(state.callIndex);
    for (var i = keys.length - 1; i >= 0; i--) {
      if (state.callIndex[keys[i]].state === "running") return state.callIndex[keys[i]];
    }
    return null;
  }

  /* ---------- 事件入流 → 交给宿主渲染 ---------- */
  var host = null;   // 由 app.js 注入：{ push(vm), onGate(callId), onDone(info) }

  function feed(e) {
    if (e.seq && e.seq <= state.seq) return;   // 去重：SSE 可能重复推送
    state.seq = e.seq || state.seq + 1;
    state.lastEventAt = Date.now();

    var vm = translate(e);
    if (!vm) {
      // tool.output 已经就地合并进原卡片 —— 通知宿主刷新那一张
      if (e.type === "tool.output" && host && host.refresh) host.refresh();
      return;
    }
    if (host && host.push) host.push(vm);
    if (e.type === "turn.completed" && host && host.onDone) host.onDone(e.data || {});
  }

  /* ---------- 发起一次真实运行 ---------- */
  function run(prompt, opts) {
    opts = opts || {};
    state.seq = 0;
    state.callIndex = {};
    state.pendingGate = null;
    state.startedAt = Date.now();

    return post("/api/run", {
      prompt: prompt,
      workspace: opts.workspace,
      // 不再有 `provider` 字段：模型接入已收敛为三字段端点，
      // 未配置时服务端自动走离线脚本。这里只保留一个显式的离线脚本入口。
      sandbox: opts.sandbox || "workspace-write",
      approval: opts.approval || "on-request",
      offline_script: opts.offline_script,
      goal_conditions: opts.goal_conditions,
    }).then(function (r) {
      if (r.status !== 200 || !r.body || !r.body.run_id) {
        var msg = (r.body && r.body.error) || ("HTTP " + r.status);
        throw new Error(msg);
      }
      state.runId = r.body.run_id;
      openStream(state.runId);
      return r.body;
    });
  }

  function openStream(rid) {
    if (state.es) { try { state.es.close(); } catch (e) {} }
    var es = new EventSource(API + "/api/run/" + rid + "/events");
    state.es = es;

    // 逐个事件类型显式订阅（SSE 的 event: 字段会走这里）
    var TYPES = [
      "turn.started", "reasoning", "message", "tool.requested",
      "approval.required", "tool.output", "file.changed", "check",
      "retry", "circuit.open", "loop.detected", "context.compacted",
      "error", "turn.completed",
    ];
    function handler(evt) {
      var payload;
      try { payload = JSON.parse(evt.data); } catch (e) { return; }
      feed(payload);
    }
    TYPES.forEach(function (t) { es.addEventListener(t, handler); });
    // 兜底：未命名 message 也收
    es.onmessage = function (evt) {
      if (!evt.data || evt.data === "{}") return;
      try { feed(JSON.parse(evt.data)); } catch (e) {}
    };
    es.addEventListener("end", function () {
      try { es.close(); } catch (e) {}
      if (state.es === es) state.es = null;
    });
    es.onerror = function () {
      // 连接中断（服务停了 / 网络抖动）。SSE 会自动重连，
      // 我们不清空状态，靠 seq 去重保证重连后不重复渲染。
    };
  }

  /* ---------- 审批回灌：真实运行时"批准"必须打到后端 ---------- */
  function approve(callId, decision, remember) {
    if (!state.runId || !callId) return Promise.resolve(false);
    return post("/api/run/" + state.runId + "/approve", {
      call_id: callId, decision: decision, remember: !!remember,
    }).then(function (r) {
      state.pendingGate = null;
      return r.status === 200 && r.body && r.body.ok === true;
    }).catch(function () { return false; });
  }

  /* ---------- 模型接入配置（只有三个字段） ----------
     走服务端 /api/config，让配置**落在服务端**而不是浏览器 localStorage：
     · base_url / model 是运行必需，服务端也要用；
     · api_key 是机密，存服务端一份、浏览器不持久化明文，
       避免把 key 留在 localStorage 被任何同源脚本读到。
  */
  function getConfig(workspace) {
    var qs = workspace ? ("?workspace=" + encodeURIComponent(workspace)) : "";
    return get("/api/config" + qs).then(function (r) { return r; });
  }

  function saveConfig(workspace, endpoint) {
    return post("/api/config", { workspace: workspace, endpoint: endpoint })
      .then(function (r) {
        if (r.status !== 200 || !r.body || !r.body.ok) {
          throw new Error((r.body && r.body.error) || ("HTTP " + r.status));
        }
        return r.body;
      });
  }

  function testConfig(workspace, endpoint, timeoutS) {
    return post("/api/config/test", {
      workspace: workspace,
      endpoint: endpoint,
      timeout_s: timeoutS || 20,
    }).then(function (r) {
      if (r.status !== 200 || !r.body) {
        throw new Error((r.body && r.body.error) || ("HTTP " + r.status));
      }
      return r.body;   // { ok, stage, message, url, elapsed_s }
    });
  }

  function clearConfig(workspace) {
    return post("/api/config/clear", { workspace: workspace })
      .then(function (r) { return r.body; });
  }

  /* ---------- 对外接口 ---------- */
  global.Harness = {
    probe: probe,
    run: run,
    approve: approve,
    feed: feed,                     // 给自测用：可直接灌一条 harness 事件
    translate: translate,           // 纯函数，便于单测
    getConfig: getConfig,
    saveConfig: saveConfig,
    testConfig: testConfig,
    clearConfig: clearConfig,
    _state: state,
    attach: function (h) { host = h; },
    isLive: function () { return state.live; },
    runId: function () { return state.runId; },
  };
})(window);
