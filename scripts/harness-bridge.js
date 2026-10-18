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
        // ── 能力账本（CapScope）──
        // 这里有一条**非常重要的语义**：越界记录不是失败。
        // policy 已经决定了动作执不执行；账本只回答"任务之外出现了一个新能力"。
        // 用红色 ✗ 渲染会让人以为动作被拦下了；用绿色 ✓ 又会让人以为没事。
        // 所以用琥珀警示色 + "不阻断"措辞 —— 它是个信号，不是判决。
        if (d.scope === "capability") {
          if (d.out_of_scope) {
            return {
              kind: "check", state: "done", tsOffset: tsOffset,
              title: "能力越界信号（未阻断）",
              model: "capability ledger",
              items: [
                { ok: false, warn: true,
                  text: d.reason + "［" + (d.code || "") + " · " + (d.severity || "") + "］" },
              ].concat((d.evidence || []).map(function (t) {
                return { ok: true, text: t };
              })),
            };
          }
          // 上限推导事件：把"这次会话被授予了什么"摆出来。
          // 不做成一条"检查通过"，因为上限本身不是检查结果 —— 它是**前提**。
          var ceil = d.ceiling || {};
          return {
            kind: "check", state: "done", tsOffset: tsOffset,
            title: "已确定本次会话的能力上限",
            model: "capability ledger",
            items: [
              { ok: true, text: "可写工作区：" + (ceil.allows_write_workspace ? "是" : "否") },
              { ok: true, text: "可写工作区之外：" + (ceil.allows_write_outside ? "是" : "否") },
              { ok: true, text: "可使用网络：" + (ceil.allows_network ? "是" : "否") },
              { ok: true, text: "任务要求只读：" + (ceil.readonly_task ? "是" : "否") },
            ].concat((ceil.basis || []).map(function (t) {
              return { ok: true, text: "依据：" + t };
            })),
          };
        }
        // ── 完成证书（ECT）──
        // 这里的措辞很关键：证书签发**不是**"任务完成"，而是
        // "声称的每一件事都能指到轨迹里的一条证据"。
        // 混成一句"完成"会让人以为证书还验证了语义正确性 —— 它没有。
        if (d.scope === "certificate") {
          return {
            kind: "check", state: "done", tsOffset: tsOffset,
            title: "完成证书已签发",
            model: "certificate",
            items: [
              { ok: true, text: d.reason || "" },
              { ok: true, text: "绑定证据的声明：" + (d.claims || 0) + " 条" },
              { ok: true, text: "证书指纹：" + String(d.digest || "").slice(0, 16) },
              { ok: false, warn: true,
                text: "证书只证明「声称的事项在记录的轨迹里有成功证据」，"
                    + "但不能证明产物在语义上正确。" },
            ],
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
        // 完成证书拒签：带的是 codes + problems（每条含 remedy）。
        // 原因码是 ECT 让模型"针对性修正"的那部分，必须摆出来 ——
        // 只显示一句 reason 等于把它退化成一个打回原形的"没完成"。
        if (d.cls === "certificate_rejected" && (d.problems || []).length) {
          return {
            kind: "check", state: "done", tsOffset: tsOffset,
            title: "完成证书被拒（第 " + (d.attempt || 1) + " 次）",
            model: "certificate",
            items: d.problems.map(function (p) {
              return {
                ok: false, warn: true,
                text: "[" + (p.code || "") + "] " + (p.detail || "")
                    + (p.remedy ? " → " + p.remedy : ""),
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
        // 能力账本若有越界，必须在**结束卡片上**就看得见。
        // 只在事件流中段浮一条会被滚走，而"这次会话出现过任务外的能力"
        // 是回合结束时最该被看见的东西。
        var capOOS = (d.capability && d.capability.out_of_scope) || 0;
        var capItems = [{
          ok: d.status === "completed",
          text: (d.reason || d.answer || "").slice(0, 300),
        }];
        if (capOOS > 0) {
          capItems.push({
            ok: false, warn: true,
            text: "本次会话有 " + capOOS + " 次超出任务范围的能力使用"
                + "（" + JSON.stringify(d.capability.by_code || {}) + "），"
                + "详见上方「能力越界信号」。",
          });
        } else if (d.capability) {
          capItems.push({
            ok: true,
            text: "能力账本：本次会话没有超出任务范围的使用。",
          });
        }
        return {
          kind: "check", state: "done", tsOffset: tsOffset,
          title: "回合结束 · " + (d.status || "completed"),
          model: "harness",
          items: capItems,
        };

      case "subagent.completed":
        // 子代理结果：**产物在前，自述在后并标注非证据**。
        // 顺序不能反 —— 反过来读的人第一眼看到的就是模型说了什么，
        // 那正是隔离要避免的认知路径。
        var art = d.artifacts || {};
        var items = [];
        if ((art.files_changed || []).length) {
          items.push({ ok: true, text: "落盘：" + art.files_changed.join("、") });
        } else {
          items.push({ ok: false, warn: true, text: "落盘：无" });
        }
        (art.commands || []).slice(0, 5).forEach(function (c) {
          items.push({
            ok: c.exit_code === 0,
            warn: c.exit_code !== 0,
            text: "[" + c.exit_code + "] " + String(c.command || "").slice(0, 80),
          });
        });
        if (art.tool_sequence && art.tool_sequence.length) {
          items.push({ ok: true, text: "工具序列：" + art.tool_sequence.join(" → ") });
        }
        if (d.isolation) {
          items.push({
            ok: true,
            text: "上下文隔离：" + d.isolation.context_messages + " 条消息"
                + "（刻意不给：" + (d.isolation.withheld || []).length + " 类内容）",
          });
        }
        if (d.claim) {
          items.push({
            ok: true, warn: true,
            text: "生成者自述（非证据）：" + String(d.claim).slice(0, 160),
          });
        }
        return {
          kind: "check", state: "done", tsOffset: tsOffset,
          title: "子代理 " + (d.agent_id || "?") + " 结束 · " + (d.status || ""),
          model: "subagent",
          items: items,
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

  /* ============================================================
     历史回放：把落盘事件流转成前端视图模型
     ============================================================
     与实时流的差别（这是本块唯一要小心的地方）：
     · 实时流的 tsOffset 基于 `state.startedAt`（会话开始时刻）
     · 回放的 ts 是**绝对秒**，需要以该 run 的第一个事件为基准
       重新算偏移 —— 否则时间列会显示成 1970 年附近的某个时刻

     所以回放前必须 `resetReplay(events)`,它会把 startedAt 设成
     第一条事件的时间戳。**不要复用 live 的 startedAt**。
     ============================================================ */
  function resetReplay(events) {
    state.callIndex = {};
    state.pendingGate = null;
    state.seq = 0;
    // 以第一条事件的绝对时间为基准 —— 这样时间列显示的是
    // "相对本次运行开始过了多久"，与实时流的语义一致。
    var first = events && events[0];
    state.startedAt = first && first.ts ? Math.round(first.ts * 1000) : Date.now();
  }

  /* 把一条落盘事件翻成视图模型。
     落盘事件与实时事件的**结构相同**（type/seq/ts/data），
     所以可以直接复用 translate()。唯一区别是 replay 模式需要
     保证 viewModel 不丢 seq —— 前端渲染输出卡片时按 seq 排序更稳。 */
  function translateReplayEvent(e) {
    var vm = translate(e);
    if (vm) vm.seq = e.seq;
    return vm;
  }

  /* 一整份落盘事件 → 视图模型数组。
     未识别的事件类型会返回 null 并被**丢掉**（不渲染空白卡片），
     这与实时流同一策略 —— 保持行为一致，避免"回放少了几张卡"
     看起来像数据缺失。 */
  function replayToViewModels(payload) {
    var events = (payload && payload.events) || [];
    resetReplay(events);
    var out = [];
    events.forEach(function (e) {
      try {
        var vm = translateReplayEvent(e);
        if (vm) out.push(vm);
      } catch (err) {
        // 单条事件翻译失败不该让整份回放失败 —— 一次崩溃的运行
        // 恰恰是最该被回看的那次，此时轨迹里可能有畸形事件。
        out.push({
          kind: "check", state: "done", tsOffset: 0, seq: e.seq,
          title: "事件解析失败（seq " + e.seq + "）",
          model: "replay",
          items: [{ ok: false, warn: true,
                    text: "类型 " + e.type + "：" + String(err && err.message || err) }],
        });
      }
    });
    return out;
  }

  function runReplayEvents(runId, workspace) {
    return get(runPath(runId, "/events", workspace)).then(function (p) {
      if (p && p.error) {
        var err = new Error(p.detail || p.error);
        err.payload = p;
        throw err;
      }
      if (!p || !p.mode) {
        // 收到了 SSE 形态（不该发生在 replay 路径上）→ 明确报错
        throw new Error("端点返回了非回放形态的数据");
      }
      return { payload: p, viewModels: replayToViewModels(p) };
    });
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

  /* ============================================================
     只读查询接口（把后端全部端点封装到一处）
     ============================================================
     设计取舍：
     1. **全部走同一条 get()**。不各写一个 fetch —— 只要有一条
        自己拼 URL 的路径，就会有人忘记带 workspace，
        于是又出现"列表里有、详情取不到"（后端刚修过这个坑）。
     2. **详情查询一律带 workspace**。从 /api/runs 拿到的每一项
        都带 `workspace`，把它原样带回去，定位就不会失效。
        这是"列表 → 详情"这条链路上唯一可靠的做法。
     3. **失败原样抛出**。不在这里吞掉错误、也不编造空数据 ——
        空数据会被读成"确实没有"，而 404/500 是"读不到"。
        这两者对用户的意义完全不同。
     ============================================================ */

  /* ---------- 全局运行列表（跨工作区）---------- */
  function listRunsAll(workspace) {
    var qs = workspace ? "?workspace=" + encodeURIComponent(workspace) : "";
    return get("/api/runs/all" + qs);
  }

  /* ---------- 单 run 的各类明细 ---------- */
  function runPath(runId, suffix, workspace) {
    var p = "/api/run/" + encodeURIComponent(runId) + suffix;
    if (workspace) p += "?workspace=" + encodeURIComponent(workspace);
    return p;
  }

  function runResult(runId, ws) { return get(runPath(runId, "/result", ws)); }
  function runEvents(runId, ws) { return get(runPath(runId, "/events", ws)); }
  function runDiagnose(runId, ws) { return get(runPath(runId, "/diagnose", ws)); }
  function runChangesets(runId, ws) { return get(runPath(runId, "/changesets", ws)); }
  function runCapability(runId, ws) { return get(runPath(runId, "/capability", ws)); }
  function runSubagents(runId, ws) { return get(runPath(runId, "/subagents", ws)); }
  function runCertificate(runId, ws) { return get(runPath(runId, "/certificate", ws)); }
  function runVerifyChain(runId, ws) { return get(runPath(runId, "/verify-chain", ws)); }

  /* ---------- 批量核验 ---------- */
  function verifyChains(workspace) {
    var qs = workspace ? "?workspace=" + encodeURIComponent(workspace) : "";
    return get("/api/verify-chains" + qs);
  }
  function diagnoseWorkspace(workspace, flawedOnly) {
    var parts = [];
    if (workspace) parts.push("workspace=" + encodeURIComponent(workspace));
    if (flawedOnly) parts.push("flawed_only=1");
    return get("/api/diagnose" + (parts.length ? "?" + parts.join("&") : ""));
  }
  function toolManifest() { return get("/api/tools"); }

  /* ---------- 工作区只读访问 ---------- */
  function wsQuery(workspace, extra) {
    var parts = [];
    if (workspace) parts.push("workspace=" + encodeURIComponent(workspace));
    Object.keys(extra || {}).forEach(function (k) {
      if (extra[k] != null) parts.push(k + "=" + encodeURIComponent(extra[k]));
    });
    return parts.length ? "?" + parts.join("&") : "";
  }
  function workspaceTree(workspace, path) {
    return get("/api/workspace/tree" + wsQuery(workspace, { path: path || "" }));
  }
  function workspaceFile(workspace, path, maxBytes) {
    return get("/api/workspace/file" + wsQuery(workspace, {
      path: path, max_bytes: maxBytes || 524288,
    }));
  }
  function workspaceInfo(workspace) {
    return get("/api/workspace/info" + wsQuery(workspace, {}));
  }
  /* 新建空文件 / 空目录。
     **拒绝也要把 body 带回来**：400 的 body 里有原因码与人话，
     丢掉它前端就只能说"失败了" —— 而用户需要知道是名字不行还是目录没了。 */
  function workspaceCreate(workspace, path, kind) {
    return post("/api/workspace/create",
                { workspace: workspace, path: path, kind: kind })
      .then(function (r) {
        var b = r.body || {};
        return { ok: r.status === 200 && b.ok === true, status: r.status, body: b };
      })
      .catch(function (e) {
        return { ok: false, status: 0, body: { error: "network", detail: String(e && e.message || e) } };
      });
  }
  function workspaceSearch(workspace, q, limit) {
    return get("/api/workspace/search" + wsQuery(workspace, { q: q, limit: limit || 200 }));
  }
  /* 列本机目录，供"选择工作区"。
     与 workspace 无关 —— 它的用途就是去看工作区**之外**的地方，
     所以刻意不接受 workspace 参数。 */
  function fsList(path) {
    return get("/api/fs/list" + wsQuery(null, { path: path || "" }));
  }
  /* 把一个工作区的模型配置复制到另一个。
     配置存在 <workspace>/.crucible/config.json，换工作区必然"这个目录还没配过"；
     这里只做显式的沿用，不自动。 */
  function copyConfig(from, to) {
    return post("/api/config/copy", { from: from, to: to })
      .then(function (r) { return r.body || {}; })
      .catch(function (e) {
        return { ok: false, error: "network", detail: String(e && e.message || e) };
      });
  }
  function workspaceTerminal(workspace, cmd) {
    return get("/api/workspace/terminal" + wsQuery(workspace, { cmd: cmd }));
  }
  function workspaceGitStatus(workspace) {
    return get("/api/workspace/git-status" + wsQuery(workspace, {}));
  }
  function workspaceIndex(workspace, rebuild) {
    return get("/api/workspace/index" + wsQuery(workspace, { rebuild: rebuild ? 1 : 0 }));
  }
  function indexQuery(workspace, q) {
    return get("/api/workspace/index/query" + wsQuery(workspace, { q: q }));
  }

  /* ---------- 回滚 ---------- */
  function rollback(runId, changeId, workspace) {
    return post("/api/run/" + encodeURIComponent(runId) + "/rollback",
                { change_id: changeId, workspace: workspace })
      .then(function (r) {
        if (r.status !== 200 || !r.body) {
          throw new Error((r.body && r.body.error) || ("HTTP " + r.status));
        }
        return r.body;
      });
  }

  /* ============================================================
     结果 → 前端视图模型的翻译
     ============================================================
     这些函数**不渲染**，只把后端 JSON 变成前端好用的形状。
     渲染留给 app.js —— 保持"桥接层只管翻译"这条边界，
     否则会出现两套格式化逻辑，迟早漂移。
     ============================================================ */

  /* 运行列表 → 会话卡片所需的字段。
     `tsMin` 是前端 fmtRel 需要的"多少分钟前"。 */
  function toRunCards(payload) {
    var runs = (payload && payload.runs) || [];
    return runs.map(function (r) {
      return {
        id: r.session_id || r.run_id || "",
        title: r.model || "（未知模型）",
        model: r.model || "",
        workspace: r.workspace_dir || r.workspace || "",
        sandbox: r.sandbox || "",
        approval: r.approval || "",
        provider: r.provider || "",
        prefixHash: r.prefix_hash || "",
        toolOrder: r.tool_order || [],
        tsMin: r.created_at ? (Date.now() / 60000 - r.created_at / 60) : null,
        raw: r,
      };
    });
  }

  /* 诊断报告 → 信号卡片。**verdict 的三态必须守住**：
     no_issue / flawed / 未知 —— 不能把"没信号"说成"有问题"，
     也不能把"读不到"说成"没问题"。 */
  function toDiagnoseCards(report) {
    if (!report) return { verdict: "unknown", reasons: [], stats: null };
    var stats = report.stats || {};
    return {
      verdict: report.verdict || "unknown",
      reasons: (report.signals || []).map(function (s) {
        return {
          code: s.code || "",
          text: s.detail || s.reason || String(s),
        };
      }),
      stats: {
        events: stats.events,
        toolCalls: stats.tool_calls,
        executed: stats.executed_tool_calls,
        filesChanged: stats.files_changed,
        iterations: stats.iterations,
        status: stats.status,
        reason: stats.reason,
      },
      disclaimer: report.disclaimer || "",
    };
  }

  /* 能力账本 → 一行摘要 + 越界明细。
     注意 `enabled: false` 与"没有越界"是**两件事**：
     前者是"没记录"，后者是"记录了且干净"。 */
  function toCapability(payload) {
    if (!payload) return null;
    if (payload.error) {
      return { available: false, error: payload.error, detail: payload.detail || "" };
    }
    var s = payload.summary || {};
    return {
      available: true,
      enabled: s.enabled !== false,
      outOfScope: s.out_of_scope || 0,
      total: s.total || 0,
      byCode: s.by_code || {},
      grantee: s.grantee || "",
      readonlyTask: !!(payload.ceiling || {}).readonly_task,
      allowsWrite: !!(payload.ceiling || {}).allows_write_workspace,
      details: (payload.out_of_scope || []).map(function (o) {
        return { code: o.code || "", detail: o.detail || "" };
      }),
    };
  }

  /* 完成证书 → 卡片。`ok` 与 `verification` 都要看：
     证书自己说"没问题"是不够的，还要独立重放也通过。 */
  function toCertificate(payload) {
    if (!payload) return null;
    if (payload.error) {
      return { available: false, error: payload.error, detail: payload.detail || "" };
    }
    var c = payload.certificate || {};
    var v = payload.verification || {};
    return {
      available: true,
      ok: !!c.ok,
      signed: !!(c.ok && v.ok !== false),
      claims: (c.claims || []).map(function (x) {
        return {
          kind: x.kind || "",
          subject: x.subject || "",
          evidenceSeq: (x.evidence || []).map(function (e) { return e.seq; }),
        };
      }),
      problems: (c.problems || []).map(function (p) {
        return { code: p.code || "", detail: p.detail || "", remedy: p.remedy || "" };
      }),
      digest: c.digest || "",
      scope: c.scope || "",
      replayOk: v.ok,
      replayCodes: v.reason_codes || [],
      verification: v,
    };
  }

  /* 子代理 → 卡片。产物在前、自述在后并标"非证据"（同渲染层的约定）。 */
  function toSubagents(payload) {
    if (!payload) return null;
    if (payload.error) {
      return { available: false, error: payload.error, detail: payload.detail || "" };
    }
    return {
      available: true,
      count: payload.count || 0,
      agents: (payload.subagents || []).map(function (a) {
        return {
          id: a.agent_id || "",
          runDir: a.run_dir || "",
          capability: a.capability || null,
        };
      }),
    };
  }

  /* 变更集 → 摘要 + 历史条目。 */
  function toChangesets(payload) {
    if (!payload) return { enabled: false, summary: null, history: [] };
    var s = payload.summary || {};
    return {
      enabled: s.enabled !== false,
      summary: {
        changes: s.changes || 0,
        files: s.files || 0,
        errors: s.errors || 0,
      },
      history: (payload.history || []).map(function (h) {
        return {
          changeId: h.change_id || h.id || "",
          seq: h.seq,
          tool: h.tool || "",
          paths: h.paths || [],
          bytes: h.bytes || 0,
        };
      }),
    };
  }

  /* 哈希链核验 → 结果。`without_anchor` 不等于失败：
     没有外部锚点只是"无法防止整链重写"，与"链被改过"不同。 */
  function toChainVerify(payload) {
    if (!payload) return null;
    if (payload.error) {
      return { available: false, error: payload.error };
    }
    return {
      available: true,
      ok: payload.ok !== false,
      runs: payload.runs,
      okCount: payload.ok,
      failed: payload.failed || [],
      withoutAnchor: payload.without_anchor || [],
    };
  }

  /* 用量 → 可显示的统计。
     ## 三态必须守住
     `usage === null` 表示**没记录用量**（老 run 或端点没返回），
     与"用量确实是 0"不是一回事。前者应显示"未记录"，
     后者才显示 0。报成 0 会让人以为这次调用不花钱。

     `ctxTokens` 是当前上下文的估算量（= 最后一次 prompt_tokens），
     不是"窗口上限"。前端有个 /200k 的进度条，**分母必须有来源** ——
     编一个上限比不显示更坏，所以这里把 `contextLimit` 也如实说出来：
     它是**配置里的预算**，不是模型真实窗口。 */
  function toUsage(result) {
    if (!result) return { available: false };
    var u = result.usage;
    if (!u) {
      return { available: false, note: "这次运行没有用量记录（端点未返回 usage）" };
    }
    var p = Number(u.prompt_tokens) || 0;
    var c = Number(u.completion_tokens) || 0;
    var t = Number(u.total_tokens) || (p + c);
    return {
      available: true,
      prompt: p,
      completion: c,
      total: t,
      // 单次运行的成本明细：这是 harness 唯一能给出的真实成本量
      fmt: fmtTokens(t),
      parts: "输入 " + fmtTokens(p) + " · 输出 " + fmtTokens(c),
    };
  }

  function fmtTokens(n) {
    n = Number(n) || 0;
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1000) return (n / 1000).toFixed(1) + "k";
    return String(n);
  }

  /* 工具清单 → 分类分组（mcp 视图要用）。 */
  function toToolGroups(payload) {
    if (!payload) return [];
    var order = payload.order || [];
    var cats = payload.categories || {};
    var byName = {};
    (payload.specs || []).forEach(function (s) {
      var fn = s["function"] || {};
      byName[fn.name] = fn;
    });
    return Object.keys(cats).map(function (cat) {
      return {
        name: cat,
        tools: cats[cat].map(function (n) {
          var fn = byName[n] || {};
          return {
            name: n,
            desc: fn.description || "",
            params: (fn.parameters || {}).properties || {},
          };
        }),
      };
    }).sort(function (a, b) {
      // 按注册顺序排：分类内首个工具在 order 里的位置
      return order.indexOf(a.tools[0] && a.tools[0].name)
           - order.indexOf(b.tools[0] && b.tools[0].name);
    });
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

    // 只读查询（本次新增，覆盖后端全部端点）
    listRunsAll: listRunsAll,
    runResult: runResult,
    runEvents: runEvents,
    runReplayEvents: runReplayEvents,     // 历史事件流回放
    replayToViewModels: replayToViewModels,
    resetReplay: resetReplay,
    runDiagnose: runDiagnose,
    runChangesets: runChangesets,
    runCapability: runCapability,
    runSubagents: runSubagents,
    runCertificate: runCertificate,
    runVerifyChain: runVerifyChain,
    verifyChains: verifyChains,
    diagnoseWorkspace: diagnoseWorkspace,
    toolManifest: toolManifest,
    workspaceTree: workspaceTree,
    workspaceFile: workspaceFile,
    workspaceCreate: workspaceCreate,
    workspaceInfo: workspaceInfo,
    workspaceSearch: workspaceSearch,
    fsList: fsList,
    copyConfig: copyConfig,
    workspaceTerminal: workspaceTerminal,
    workspaceGitStatus: workspaceGitStatus,
    workspaceIndex: workspaceIndex,
    indexQuery: indexQuery,
    rollback: rollback,

    // 翻译（纯函数，便于单测）
    toRunCards: toRunCards,
    toDiagnoseCards: toDiagnoseCards,
    toCapability: toCapability,
    toCertificate: toCertificate,
    toSubagents: toSubagents,
    toChangesets: toChangesets,
    toChainVerify: toChainVerify,
    toToolGroups: toToolGroups,
    toUsage: toUsage,
    fmtTokens: fmtTokens,

    _state: state,
    attach: function (h) { host = h; },
    isLive: function () { return state.live; },
    runId: function () { return state.runId; },
  };
})(window);
