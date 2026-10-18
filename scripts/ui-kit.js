/* ============================================================
   Crucible — 交互原语层
   Toast / Confirm / ContextMenu / CommandPalette / Tooltip / Announcer
   每个原语都保证：有关闭路径、键盘可达、焦点正确归还。
   ============================================================ */
var UI = (function () {
  "use strict";

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  function h(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  /* ---------- 焦点陷阱 ---------- */
  var FOCUSABLE = 'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])';

  function trapFocus(container) {
    function onKey(e) {
      if (e.key !== "Tab") return;
      var items = $$(FOCUSABLE, container).filter(function (n) {
        return n.offsetWidth || n.offsetHeight || n.getClientRects().length;
      });
      if (!items.length) { e.preventDefault(); return; }
      var first = items[0], last = items[items.length - 1];
      var act = document.activeElement;
      if (e.shiftKey) {
        if (act === first || !container.contains(act)) { e.preventDefault(); last.focus(); }
      } else {
        if (act === last || !container.contains(act)) { e.preventDefault(); first.focus(); }
      }
    }
    container.addEventListener("keydown", onKey);
    return function () { container.removeEventListener("keydown", onKey); };
  }

  /* 打开浮层时把焦点送进去并归还；关闭时把焦点还给触发者 */
  function manageFocus(container, preferred) {
    var prev = document.activeElement;
    var release = trapFocus(container);
    var target = preferred || $(FOCUSABLE, container) || container;
    // 同步试一次，再在下一帧补一次 —— 只靠 rAF 在后台标签页/无头环境里可能不触发
    function tryFocus() {
      if (target && target.focus && document.contains(target)) target.focus();
    }
    tryFocus();
    requestAnimationFrame(tryFocus);
    return function restore() {
      release();
      if (prev && prev.focus && document.contains(prev)) prev.focus();
    };
  }

  /* ---------- 滚动锁 ---------- */
  var lockCount = 0;
  function lockScroll(on) {
    if (on) {
      lockCount++;
      if (lockCount === 1) document.body.style.overflow = "hidden";
    } else {
      lockCount = Math.max(0, lockCount - 1);
      if (lockCount === 0) document.body.style.overflow = "";
    }
  }

  /* ============================================================
     无障碍播报
     ============================================================ */
  var live = null;
  function announce(msg, politeness) {
    if (!live) {
      live = h("div", "sr-only");
      live.setAttribute("aria-live", politeness || "polite");
      live.setAttribute("aria-atomic", "true");
      document.body.appendChild(live);
    }
    live.setAttribute("aria-live", politeness || "polite");
    live.textContent = "";
    // 同一内容连播两次时，清空一次才能触发朗读
    requestAnimationFrame(function () { live.textContent = msg; });
  }

  /* ============================================================
     Toast —— 支持操作按钮与撤销窗口
     ============================================================ */
  var toastHost = null;
  function toasts() {
    if (!toastHost) {
      toastHost = h("div", "toast-host");
      toastHost.setAttribute("role", "region");
      toastHost.setAttribute("aria-label", "通知");
      document.body.appendChild(toastHost);
    }
    return toastHost;
  }

  function toast(opts) {
    opts = opts || {};
    var host = toasts();
    var node = h("div", "toast toast--" + (opts.tone || "info"));
    node.setAttribute("role", opts.tone === "danger" ? "alert" : "status");

    var ico = h("span", "toast__ico");
    ico.setAttribute("aria-hidden", "true");
    ico.innerHTML = TOAST_ICON[opts.tone || "info"] || TOAST_ICON.info;
    node.appendChild(ico);

    var body = h("div", "toast__body");
    body.appendChild(h("p", "toast__title", opts.title || ""));
    if (opts.desc) body.appendChild(h("p", "toast__desc", opts.desc));
    node.appendChild(body);

    var acts = h("div", "toast__acts");
    (opts.actions || []).forEach(function (a) {
      var b = h("button", "toast__act" + (a.primary ? " toast__act--primary" : ""), a.label);
      b.type = "button";
      b.addEventListener("click", function () {
        if (a.onClick) a.onClick();
        dismiss();
      });
      acts.appendChild(b);
    });
    var closeBtn = h("button", "toast__x");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "关闭通知");
    closeBtn.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
    closeBtn.addEventListener("click", dismiss);
    acts.appendChild(closeBtn);
    node.appendChild(acts);

    // 进度条：让「多久后自动消失」可见，鼠标悬停时暂停
    var bar = h("span", "toast__bar");
    node.appendChild(bar);

    host.appendChild(node);

    var dur = opts.duration == null ? 5000 : opts.duration;
    var timer = null, start = 0, remain = dur, paused = false;

    function dismiss() {
      if (!node.parentNode) return;
      clearTimeout(timer);
      node.classList.add("toast--out");
      node.addEventListener("animationend", function () {
        if (node.parentNode) node.parentNode.removeChild(node);
      }, { once: true });
      // 兜底：动画被 reduced-motion 关掉时也要移除
      setTimeout(function () { if (node.parentNode) node.parentNode.removeChild(node); }, 400);
    }

    function arm(ms) {
      if (ms === Infinity) return;
      start = Date.now();
      timer = setTimeout(dismiss, ms);
      bar.style.transitionDuration = ms + "ms";
      bar.style.transform = "scaleX(0)";
    }
    function hold() {
      if (dur === Infinity) return;
      paused = true;
      clearTimeout(timer);
      remain -= (Date.now() - start);
      var cs = getComputedStyle(bar);
      bar.style.transform = cs.transform;      // 冻结当前视觉进度
      bar.style.transitionDuration = "0ms";
    }
    function resume() {
      if (dur === Infinity || !paused) return;
      paused = false;
      bar.style.transitionDuration = remain + "ms";
      bar.style.transform = "scaleX(0)";
      arm(remain);
    }

    node.addEventListener("mouseenter", hold);
    node.addEventListener("mouseleave", resume);
    node.addEventListener("focusin", hold);
    node.addEventListener("focusout", resume);

    arm(dur);
    // 键盘用户也要能关掉
    node.tabIndex = -1;

    return { dismiss: dismiss };
  }

  var TOAST_ICON = {
    info: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="6.6" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M8 7.2v4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><circle cx="8" cy="4.9" r=".9" fill="currentColor"/></svg>',
    ok: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="6.6" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M5.2 8.2l2 2 3.6-4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    warn: '<svg viewBox="0 0 16 16"><path d="M8 2l6 11H2z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M8 6.4v3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><circle cx="8" cy="11.2" r=".8" fill="currentColor"/></svg>',
    danger: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="6.6" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M8 4.6v4.6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><circle cx="8" cy="11.4" r=".9" fill="currentColor"/></svg>',
  };

  /* ============================================================
     Confirm —— 模态确认，焦点陷阱 + 归还，破坏性操作用
     ============================================================ */
  var modalRelease = null;
  function confirm(opts) {
    opts = opts || {};
    var back = h("div", "modalback");
    var dlg = h("div", "modal");
    dlg.setAttribute("role", "alertdialog");
    dlg.setAttribute("aria-modal", "true");
    var titleId = "modal-title-" + Date.now();
    dlg.setAttribute("aria-labelledby", titleId);

    dlg.appendChild(h("h2", "modal__title", opts.title || "确认操作")).id = titleId;
    dlg.appendChild(h("p", "modal__desc", opts.desc || ""));

    if (opts.detail) {
      var det = h("pre", "modal__detail", opts.detail);
      dlg.appendChild(det);
    }

    var acts = h("div", "modal__acts");
    var cancel = h("button", "btn", opts.cancelLabel || "取消");
    cancel.type = "button";
    var ok = h("button", "btn " + (opts.tone === "danger" ? "btn--danger" : "btn--primary"), opts.confirmLabel || "确定");
    ok.type = "button";
    acts.appendChild(cancel);
    acts.appendChild(ok);
    dlg.appendChild(acts);
    back.appendChild(dlg);
    document.body.appendChild(back);

    lockScroll(true);
    modalRelease = manageFocus(dlg, ok);

    return new Promise(function (resolve) {
      function done(v) {
        if (modalRelease) { modalRelease(); modalRelease = null; }
        lockScroll(false);
        back.classList.add("modalback--out");
        setTimeout(function () { if (back.parentNode) back.parentNode.removeChild(back); }, 200);
        resolve(v);
      }
      cancel.addEventListener("click", function () { done(false); });
      ok.addEventListener("click", function () { done(true); });
      back.addEventListener("mousedown", function (e) { if (e.target === back) done(false); });
      dlg.addEventListener("keydown", function (e) { if (e.key === "Escape") { e.preventDefault(); done(false); } });
    });
  }

  /* ============================================================
     FormDialog —— 带字段的表单模态
     与 confirm 的区别：confirm 只问是非，formDialog 收集**输入**。
     因此它多负责三件事：字段渲染、校验反馈、提交态（进行中禁用提交）。
     ============================================================ */
  function formDialog(opts) {
    opts = opts || {};
    var fields = opts.fields || [];
    var back = h("div", "modalback");
    var dlg = h("div", "modal modal--form");
    dlg.setAttribute("role", "dialog");
    dlg.setAttribute("aria-modal", "true");
    var titleId = "fd-title-" + Date.now();
    dlg.setAttribute("aria-labelledby", titleId);

    var head = h("div", "fd__head");
    var title = h("h2", "modal__title", opts.title || "设置");
    title.id = titleId;
    head.appendChild(title);
    var closeBtn = h("button", "iconbtn iconbtn--sm fd__close");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "关闭");
    closeBtn.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="M6 6l8 8M14 6l-8 8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>';
    head.appendChild(closeBtn);
    dlg.appendChild(head);

    if (opts.desc) dlg.appendChild(h("p", "modal__desc", opts.desc));

    var form = h("form", "fd__form");
    form.noValidate = true;
    var inputs = {};

    fields.forEach(function (f) {
      var wrap = h("div", "field" + (f.full ? " field--full" : ""));
      var lid = "fld-" + (f.name || Math.random().toString(36).slice(2));
      var label = h("label", "field__label", f.label || f.name);
      label.setAttribute("for", lid);
      wrap.appendChild(label);

      var box = h("div", "field__box");
      var input;
      if (f.type === "select") {
        input = h("select", "field__input");
        (f.options || []).forEach(function (o) {
          var op = h("option", null, o.label);
          op.value = o.value;
          if (o.value === f.value) op.selected = true;
          input.appendChild(op);
        });
      } else {
        input = h("input", "field__input");
        input.type = f.type || "text";
        input.value = f.value == null ? "" : String(f.value);
      }
      input.id = lid;
      input.name = f.name || "";
      if (f.placeholder) input.placeholder = f.placeholder;
      if (f.autocomplete) input.autocomplete = f.autocomplete;
      if (f.spellcheck === false) input.spellCheck = false;

      // 密码类字段：给一个可切换明文的小开关 —— 填 key 时看不到内容极难排错
      if (f.type === "password") {
        var eye = h("button", "field__reveal");
        eye.type = "button";
        eye.setAttribute("aria-label", "显示内容");
        eye.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="M2 10s3-5 8-5 8 5 8 5-3 5-8 5-8-5-8-5z" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="10" cy="10" r="2.2" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';
        eye.addEventListener("click", function () {
          var shown = input.type === "text";
          input.type = shown ? "password" : "text";
          eye.setAttribute("aria-label", shown ? "显示内容" : "隐藏内容");
          input.focus();
        });
        box.appendChild(input);
        box.appendChild(eye);
      } else {
        box.appendChild(input);
      }
      wrap.appendChild(box);

      var hint = h("div", "field__hint", f.hint || "");
      if (!f.hint) hint.hidden = true;
      wrap.appendChild(hint);

      form.appendChild(wrap);
      inputs[f.name] = { input: input, wrap: wrap, hint: hint, def: f };
    });

    dlg.appendChild(form);

    // 字段之外的自由区域（例如"测试连接"的结果区）
    var extra = h("div", "fd__extra");
    form.appendChild(extra);

    var acts = h("div", "modal__acts");
    if (opts.extraActions) {
      opts.extraActions.forEach(function (a) {
        var b = h("button", "btn " + (a.cls || ""), a.label);
        b.type = "button";      // 非提交按钮，否则会触发校验
        if (a.id) b.id = a.id;
        if (a.role) b.dataset.role = a.role;
        b.dataset.act = a.act || "";
        acts.appendChild(b);
      });
    }
    var spacer = h("span", "fd__spacer");
    acts.appendChild(spacer);
    var cancel = h("button", "btn", opts.cancelLabel || "取消");
    cancel.type = "button";
    var ok = h("button", "btn btn--primary", opts.submitLabel || "保存");
    ok.type = "submit";                 // 必须在 form 内，见下方注释
    acts.appendChild(cancel);
    acts.appendChild(ok);

    // **提交按钮必须位于 <form> 内部**。
    // 放在 form 外面时：回车提交仍然能用（form 的 submit 事件会触发），
    // 但**点击按钮什么都不会发生** —— 因为按钮不再是 form 的提交控件。
    // 这个不对称极难发现：两条路径一条通一条不通，实测就踩到了。
    form.appendChild(acts);

    back.appendChild(dlg);
    document.body.appendChild(back);

    lockScroll(true);
    var release = manageFocus(dlg, opts.focusName && inputs[opts.focusName]
      ? inputs[opts.focusName].input : null);

    function cleanup() {
      if (release) { release(); release = null; }
      lockScroll(false);
      back.classList.add("modalback--out");
      setTimeout(function () { if (back.parentNode) back.parentNode.removeChild(back); }, 200);
    }

    function values() {
      var out = {};
      Object.keys(inputs).forEach(function (k) { out[k] = inputs[k].input.value; });
      return out;
    }

    // ---- 校验：在字段下方就地给反馈，而不是弹一个笼统的 toast ----
    function setError(name, msg) {
      var f = inputs[name];
      if (!f) return;
      f.wrap.classList.toggle("field--bad", !!msg);
      f.hint.textContent = msg || f.def.hint || "";
      f.hint.hidden = !msg && !f.def.hint;
      if (msg) f.input.setAttribute("aria-invalid", "true");
      else f.input.removeAttribute("aria-invalid");
    }
    function clearErrors() {
      Object.keys(inputs).forEach(function (k) { setError(k, ""); });
    }
    function validate() {
      clearErrors();
      var bad = [];
      Object.keys(inputs).forEach(function (k) {
        var f = inputs[k];
        var v = (f.input.value || "").trim();
        if (f.def.required && !v) { setError(k, "这一项必填。"); bad.push(k); }
      });
      return bad;
    }

    var api = {
      el: dlg,
      body: extra,
      values: values,
      setError: setError,
      clearErrors: clearErrors,
      validate: validate,
      // 提交中的状态：禁用提交与取消，避免重复提交
      busy: function (on, label) {
        ok.disabled = !!on;
        cancel.disabled = !!on;
        ok.textContent = on ? (label || "处理中…") : (opts.submitLabel || "保存");
      },
      focusField: function (name) {
        if (inputs[name]) inputs[name].input.focus();
      },
      close: function () { cleanup(); if (opts.onClose) opts.onClose(); },
    };

    return new Promise(function (resolve) {
      var settled = false;
      function finish(v) {
        if (settled) return;
        settled = true;
        cleanup();
        resolve(v);
      }
      api._finish = finish;

      closeBtn.addEventListener("click", function () { if (!ok.disabled) finish(null); });
      cancel.addEventListener("click", function () { if (!ok.disabled) finish(null); });
      back.addEventListener("mousedown", function (e) {
        if (e.target === back && !ok.disabled) finish(null);
      });
      dlg.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !ok.disabled) { e.preventDefault(); finish(null); }
      });
      // extraActions 交给调用方绑（用 data-act 区分），但这里统一做"点击即回调"
      (opts.extraActions || []).forEach(function (a) {
        var btn = acts.querySelector('[data-act="' + a.act + '"]');
        if (btn && a.onClick) btn.addEventListener("click", function () { a.onClick(api); });
      });
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        if (ok.disabled) return;
        var bad = validate();
        if (bad.length) { api.focusField(bad[0]); return; }
        resolveAsync();
      });

      // onSubmit 可以返回 Promise（异步保存）；用返回值决定是否关闭
      function resolveAsync() {
        var v = values();
        if (!opts.onSubmit) { finish(v); return; }
        var r = opts.onSubmit(v, api);
        if (r && typeof r.then === "function") {
          r.then(function (keep) {
            if (keep === false) return;   // 调用方要求保持打开（例如校验失败）
            finish(v);
          });
        } else if (r !== false) {
          finish(v);
        }
      }
    });
  }

  /* ============================================================
     ContextMenu —— 右键菜单，键盘可达
     ============================================================ */
  var menuCleanup = null;
  function contextMenu(x, y, items) {
    closeMenu();
    var menu = h("div", "ctxmenu");
    menu.setAttribute("role", "menu");
    menu.tabIndex = -1;

    var idx = 0;
    var btns = [];

    items.forEach(function (it) {
      if (it.sep) { menu.appendChild(h("div", "ctxmenu__sep")); return; }
      var b = h("button", "ctxmenu__item" + (it.danger ? " ctxmenu__item--danger" : ""));
      b.type = "button";
      b.setAttribute("role", "menuitem");
      if (it.disabled) { b.disabled = true; b.setAttribute("aria-disabled", "true"); }
      var ic = h("span", "ctxmenu__ico");
      ic.setAttribute("aria-hidden", "true");
      if (it.icon) ic.innerHTML = it.icon;
      b.appendChild(ic);
      b.appendChild(h("span", "ctxmenu__label", it.label));
      if (it.hint) {
        var k = h("span", "ctxmenu__hint");
        (it.hint.split("+")).forEach(function (p) { k.appendChild(h("kbd", null, p)); });
        b.appendChild(k);
      }
      b.addEventListener("click", function () { closeMenu(); if (it.onClick) it.onClick(); });
      menu.appendChild(b);
      btns.push(b);
    });

    document.body.appendChild(menu);

    // 位置：先量再夹紧到视口内，绝不出界
    var mw = menu.offsetWidth, mh = menu.offsetHeight;
    var left = Math.max(6, Math.min(x, window.innerWidth - mw - 6));
    var top = Math.max(6, Math.min(y, window.innerHeight - mh - 6));
    menu.style.left = left + "px";
    menu.style.top = top + "px";

    function move(d) {
      var enabled = btns.filter(function (b) { return !b.disabled; });
      if (!enabled.length) return;
      var cur = enabled.indexOf(document.activeElement);
      var next = enabled[(cur + d + enabled.length) % enabled.length];
      next.focus();
    }
    function onKey(e) {
      if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "Escape") { e.preventDefault(); closeMenu(); }
      else if (e.key === "Tab") { e.preventDefault(); closeMenu(); }
    }
    menu.addEventListener("keydown", onKey);

    function onDocDown(e) {
      if (!menu.contains(e.target)) closeMenu();
    }
    var onScroll = function () { closeMenu(); };
    setTimeout(function () {
      document.addEventListener("mousedown", onDocDown);
      window.addEventListener("scroll", onScroll, true);
      window.addEventListener("resize", onScroll);
    }, 0);

    var release = manageFocus(menu, btns.filter(function (b) { return !b.disabled; })[0]);

    menuCleanup = function () {
      document.removeEventListener("mousedown", onDocDown);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
      release();
      if (menu.parentNode) menu.parentNode.removeChild(menu);
      menuCleanup = null;
    };
    return menu;
  }
  function closeMenu() { if (menuCleanup) menuCleanup(); }

  /* ============================================================
     CommandPalette —— Ctrl+K，模糊检索
     ============================================================ */
  function fuzzyScore(q, s) {
    q = q.toLowerCase(); s = s.toLowerCase();
    if (!q) return 1;
    if (s.indexOf(q) === 0) return 1000 - s.length;      // 前缀命中优先
    var qi = 0, score = 0, streak = 0;
    for (var i = 0; i < s.length && qi < q.length; i++) {
      if (s[i] === q[qi]) { qi++; streak++; score += 10 + streak * 6; }
      else streak = 0;
    }
    if (qi < q.length) return -1;                         // 不是子序列
    return score - s.length * 0.4;
  }

  var paletteCleanup = null;
  function commandPalette(commands) {
    closePalette();
    var back = h("div", "paletteback");
    var box = h("div", "palette");
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    box.setAttribute("aria-label", "命令面板");

    var searchWrap = h("div", "palette__search");
    searchWrap.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><circle cx="7" cy="7" r="4.2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M10.2 10.2l3.3 3.3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
    var input = h("input", "palette__input");
    input.type = "text";
    input.placeholder = "搜索命令、文件、会话…";
    input.setAttribute("aria-label", "搜索命令");
    input.setAttribute("aria-controls", "palette-list");
    input.setAttribute("autocomplete", "off");
    input.setAttribute("spellcheck", "false");
    searchWrap.appendChild(input);
    var esc = h("kbd", "palette__esc", "Esc");
    searchWrap.appendChild(esc);
    box.appendChild(searchWrap);

    var list = h("div", "palette__list");
    list.id = "palette-list";
    list.setAttribute("role", "listbox");
    box.appendChild(list);

    var foot = h("div", "palette__foot");
    foot.innerHTML = '<span><kbd>↑</kbd><kbd>↓</kbd> 选择</span><span><kbd>Enter</kbd> 执行</span><span><kbd>Esc</kbd> 关闭</span>';
    box.appendChild(foot);

    back.appendChild(box);
    document.body.appendChild(back);
    lockScroll(true);

    var flat = [];
    var active = 0;

    function render(q) {
      list.innerHTML = "";
      var scored = [];
      commands.forEach(function (c, i) {
        var hay = (c.label || "") + " " + (c.sub || "") + " " + (c.group || "");
        var s = fuzzyScore(q, hay);
        if (s >= 0) scored.push({ c: c, s: s, i: i });
      });
      scored.sort(function (a, b) { return b.s - a.s || a.i - b.i; });
      var top = scored.slice(0, 40);
      flat = top.map(function (o) { return o.c; });

      if (!flat.length) {
        var empty = h("div", "palette__empty");
        empty.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="10" cy="10" r="6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M14.5 14.5L19 19" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
        empty.appendChild(h("p", null, "没有匹配的项"));
        empty.appendChild(h("p", "u-dim", "试试更短的关键词，或换个说法。"));
        list.appendChild(empty);
        active = -1;
        return;
      }

      var curGroup = null;
      top.forEach(function (o, k) {
        var c = o.c;
        if (c.group && c.group !== curGroup) {
          curGroup = c.group;
          list.appendChild(h("div", "palette__group", curGroup));
        }
        var row = h("button", "palette__item" + (k === active ? " is-on" : ""));
        row.type = "button";
        row.setAttribute("role", "option");
        row.id = "pal-" + k;
        row.setAttribute("aria-selected", k === active ? "true" : "false");
        var ic = h("span", "palette__ico");
        ic.setAttribute("aria-hidden", "true");
        if (c.icon) ic.innerHTML = c.icon;
        row.appendChild(ic);
        var txt = h("span", "palette__txt");
        txt.appendChild(h("span", "palette__label", c.label));
        if (c.sub) txt.appendChild(h("span", "palette__sub", c.sub));
        row.appendChild(txt);
        if (c.keys) {
          var kk = h("span", "palette__keys");
          c.keys.forEach(function (p) { kk.appendChild(h("kbd", null, p)); });
          row.appendChild(kk);
        }
        row.addEventListener("mousemove", function () { setActive(k); });
        row.addEventListener("click", function () { run(k); });
        list.appendChild(row);
      });
      syncActive();
    }

    function setActive(k) {
      if (k === active) return;
      var rows = $$(".palette__item", list);
      if (rows[active]) { rows[active].classList.remove("is-on"); rows[active].setAttribute("aria-selected", "false"); }
      active = k;
      if (rows[active]) {
        rows[active].classList.add("is-on");
        rows[active].setAttribute("aria-selected", "true");
        input.setAttribute("aria-activedescendant", rows[active].id);
      }
      syncActive();
    }
    function syncActive() {
      if (active < 0) { input.removeAttribute("aria-activedescendant"); return; }
      input.setAttribute("aria-activedescendant", "pal-" + active);
      var rows = $$(".palette__item", list);
      if (rows[active] && rows[active].scrollIntoView) {
        rows[active].scrollIntoView({ block: "nearest" });
      }
    }
    function run(k) {
      var c = flat[k];
      if (!c) return;
      closePalette();
      if (c.onRun) setTimeout(function () { c.onRun(); }, 0);
    }

    function onKey(e) {
      if (e.key === "ArrowDown") { e.preventDefault(); if (flat.length) setActive((active + 1) % flat.length); }
      else if (e.key === "ArrowUp") { e.preventDefault(); if (flat.length) setActive((active - 1 + flat.length) % flat.length); }
      else if (e.key === "Enter") { e.preventDefault(); run(active); }
      else if (e.key === "Escape") { e.preventDefault(); closePalette(); }
      else if (e.key === "Home" && flat.length) { e.preventDefault(); setActive(0); }
      else if (e.key === "End" && flat.length) { e.preventDefault(); setActive(flat.length - 1); }
    }
    box.addEventListener("keydown", onKey);
    input.addEventListener("input", function () { active = 0; render(input.value.trim()); });
    back.addEventListener("mousedown", function (e) { if (e.target === back) closePalette(); });

    active = 0;
    render("");
    var release = manageFocus(box, input);

    paletteCleanup = function () {
      release();
      lockScroll(false);
      if (back.parentNode) back.parentNode.removeChild(back);
      paletteCleanup = null;
    };
    return { close: closePalette };
  }
  function closePalette() { if (paletteCleanup) paletteCleanup(); }

  /* ============================================================
     Tooltip —— 只在内容被截断时显示
     ============================================================ */
  function initTooltips(root) {
    var tip = null;
    var showT = null;

    function isClipped(n) {
      return n.scrollWidth - n.clientWidth > 1 && n.clientWidth > 0;
    }
    function show(n, text) {
      hide();
      tip = h("div", "tip");
      tip.setAttribute("role", "tooltip");
      tip.textContent = text;
      document.body.appendChild(tip);
      var r = n.getBoundingClientRect();
      var tr = tip.getBoundingClientRect();
      var left = Math.max(6, Math.min(r.left + r.width / 2 - tr.width / 2, window.innerWidth - tr.width - 6));
      var top = r.top - tr.height - 8;
      if (top < 6) top = r.bottom + 8;      // 上方放不下就翻到下方
      tip.style.left = left + "px";
      tip.style.top = top + "px";
      tip.setAttribute("data-open", "true");
    }
    function hide() {
      if (tip && tip.parentNode) tip.parentNode.removeChild(tip);
      tip = null;
      clearTimeout(showT);
    }

    root.addEventListener("mouseover", function (e) {
      var n = e.target.closest("[data-tip]");
      if (!n) return;
      clearTimeout(showT);
      showT = setTimeout(function () {
        var t = n.getAttribute("data-tip");
        // data-tip=auto 表示「仅在被截断时显示」
        if (t === "auto") {
          var inner = n.querySelector(".truncate") || n;
          if (!isClipped(inner)) return;
          show(n, inner.textContent.trim());
        } else {
          show(n, t);
        }
      }, 420);
    });
    root.addEventListener("mouseout", function (e) {
      if (e.target.closest("[data-tip]")) hide();
    });
    root.addEventListener("focusin", function (e) {
      var n = e.target.closest("[data-tip]");
      if (n) {
        var t = n.getAttribute("data-tip");
        if (t === "auto") {
          var inner = n.querySelector(".truncate") || n;
          if (isClipped(inner)) show(n, inner.textContent.trim());
        } else { show(n, t); }
      }
    });
    root.addEventListener("focusout", hide);
    root.addEventListener("mousedown", hide);
    window.addEventListener("scroll", hide, true);
  }

  /* ============================================================
     复制到剪贴板 —— 带降级路径与成功反馈
     ============================================================ */
  function copy(text, label) {
    function ok() { toast({ tone: "ok", title: "已复制" + (label ? " " + label : ""), duration: 2200 }); }
    function fail() { toast({ tone: "warn", title: "复制失败", desc: "浏览器拒绝了剪贴板访问，请手动选中复制。", duration: 4200 }); }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(ok).catch(function () {
        // 降级：textarea + execCommand（file:// 下 clipboard API 常被拒）
        try {
          var ta = document.createElement("textarea");
          ta.value = text;
          ta.setAttribute("readonly", "");
          ta.style.cssText = "position:fixed;left:-9999px;top:0";
          document.body.appendChild(ta);
          ta.select();
          var done = document.execCommand("copy");
          document.body.removeChild(ta);
          done ? ok() : fail();
        } catch (e) { fail(); }
      });
    } else {
      try {
        var ta2 = document.createElement("textarea");
        ta2.value = text;
        ta2.setAttribute("readonly", "");
        ta2.style.cssText = "position:fixed;left:-9999px;top:0";
        document.body.appendChild(ta2);
        ta2.select();
        var done2 = document.execCommand("copy");
        document.body.removeChild(ta2);
        done2 ? ok() : fail();
      } catch (e) { fail(); }
    }
  }

  /* ============================================================
     工具
     ============================================================ */
  function fmtRel(minutes) {
    // 用 Intl.RelativeTimeFormat —— 不硬编码「1 小时前」
    try {
      var rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto", style: "short" });
      if (Math.abs(minutes) < 1) return rtf.format(0, "minute");
      if (Math.abs(minutes) < 60) return rtf.format(-Math.round(minutes), "minute");
      if (Math.abs(minutes) < 1440) return rtf.format(-Math.round(minutes / 60), "hour");
      return rtf.format(-Math.round(minutes / 1440), "day");
    } catch (e) {
      return minutes < 1 ? "刚刚" : minutes < 60 ? Math.round(minutes) + " 分钟前" : Math.round(minutes / 60) + " 小时前";
    }
  }

  function fmtNum(n) {
    try { return new Intl.NumberFormat(undefined).format(n); }
    catch (e) { return String(n); }
  }
  function fmtTokens(n) {
    if (n >= 1000) {
      try { return new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(n / 1000) + "k"; }
      catch (e) { return (n / 1000).toFixed(1) + "k"; }
    }
    return String(n);
  }

  return {
    announce: announce,
    toast: toast,
    confirm: confirm,
    formDialog: formDialog,
    contextMenu: contextMenu,
    closeMenu: closeMenu,
    commandPalette: commandPalette,
    closePalette: closePalette,
    initTooltips: initTooltips,
    copy: copy,
    fmtRel: fmtRel,
    fmtNum: fmtNum,
    fmtTokens: fmtTokens,
    trapFocus: trapFocus,
    lockScroll: lockScroll,
  };
})();
