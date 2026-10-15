/* ULTRON Workspace 2.0 — application shell and views. */
(function () {
  "use strict";
  const W = window.W;
  const { el, icon, button, fmt, $, $$ } = W;

  const S = (W.state = {
    lang: "en", view: "chat", conversationId: null, messages: [], conversations: [], saved: [], activity: [],
    settings: {}, status: {}, tasks: new Map(), doneTasks: new Set(), panelComp: null, panelOpen: true, mcp: null, today: "",
  });

  // ── status vocabulary ───────────────────────────────────────────────────
  const STATUS_LABEL = { connected: "Connected", connecting: "Connecting", auth_required: "Authentication required",
    offline: "Offline", error: "Error", not_configured: "Not configured", disabled: "Disabled",
    CONNECTED: "Connected", READY: "Ready (not yet verified)", CHECKING: "Checking", LIMITED: "Usage limit",
    DISCONNECTED: "Disconnected", ERROR: "Error", "NOT CONFIGURED": "Not configured" };
  W.statusLabel = (s) => STATUS_LABEL[s] || String(s || "unknown").replace(/_/g, " ");
  const stClass = (s) => ({ CONNECTED: "connected", READY: "ready", CHECKING: "connecting", LIMITED: "offline", DISCONNECTED: "offline",
    ERROR: "error", "NOT CONFIGURED": "not_configured" }[s] || s);

  // ── layout skeleton ─────────────────────────────────────────────────────
  const NAV = [
    ["new", "plusChat", "New Chat"], ["chat", "grid", "Workspace"], ["conversations", "chats", "Conversations"],
    ["saved", "bookmark", "Saved Results"], ["mcp", "hub", "MCP Hub"], ["settings", "settings", "Settings"],
  ];
  const SHORTCUTS = [
    ["globe", "Search the Web", "Istraži ", false], ["pin", "Find a Place", "Pokaži mi restorane u ", false],
    ["bed", "Find Accommodation", "Nađi mi najjeftiniji stan na dan u ", false],
    ["calendar", "My Schedule", "Šta imam u kalendaru ove nedelje?", true],
  ];
  const QUICK = [
    ["pin", "Explore Places", "Find places, routes and nearby destinations.", "Koliko ima od Niša do Beograda?", "blue"],
    ["bed", "Find the Best Stay", "Compare apartments and accommodations across providers.", "Nađi mi najjeftiniji stan na dan u Nišu za 16. oktobar", "cyan"],
    ["globe", "AI Research", "Search multiple sources and get useful answers.", "Istraži ", "violet"],
    ["layers", "My Connected World", "Work with your calendar, files and messages.", "Sažmi moje nepročitane mejlove", "teal"],
  ];

  function buildShell() {
    const app = $("#app");
    W.clear(app);
    const nav = el("nav", { class: "nav", "aria-label": "Primary" }, NAV.map(([k, ic, label]) =>
      el("button", { type: "button", class: "nav-btn", dataset: { view: k }, title: label, onclick: () => navigate(k) },
        icon(ic, 19), el("span", { class: "nav-l", text: label }))));
    const shortcuts = el("div", { class: "side-sec" }, el("div", { class: "side-h", text: "Smart shortcuts" }),
      SHORTCUTS.map(([ic, label, prompt, send]) => el("button", { type: "button", class: "nav-btn sc", title: label,
        onclick: () => { navigate("chat"); if (send) sendMessage(prompt); else prefill(prompt); } },
        icon(ic, 17), el("span", { class: "nav-l", text: label }))));
    const recents = el("div", { class: "side-sec side-recent" }, el("div", { class: "side-h", text: "Recent" }), el("div", { id: "recentList" }));
    const sideStatus = el("div", { class: "side-status", id: "sideStatus", role: "status", "aria-live": "polite" });
    const sidebar = el("aside", { class: "sidebar", id: "sidebar", "aria-label": "Workspace navigation" },
      el("div", { class: "brand" }, el("div", { class: "brand-orb", "aria-hidden": "true" }, el("span")),
        el("div", { class: "brand-t" }, el("div", { class: "brand-n", text: "ULTRON" }), el("div", { class: "brand-s", text: "AI Workspace" })),
        button("Collapse sidebar", { icon: "chevLeft", iconOnly: true, kind: "ghost", small: true, class: "collapse-btn", onClick: toggleSidebar })),
      nav, shortcuts, recents, sideStatus);

    const topbar = el("header", { class: "topbar" },
      button("Open menu", { icon: "menu", iconOnly: true, kind: "ghost", class: "menu-btn", onClick: () => document.body.classList.toggle("drawer-open") }),
      el("div", { class: "top-titles" },
        el("div", { class: "eyebrow" }, el("span", { text: "ULTRON" }), el("span", { class: "sep", text: "/" }), el("span", { text: "AI" }), el("span", { class: "accent", text: "WORKSPACE" })),
        el("h1", { class: "top-h", id: "viewTitle" })),
      el("div", { class: "top-actions" },
        el("button", { type: "button", class: "kbd-btn", onclick: openPalette, "aria-label": "Command palette (Ctrl+K)" },
          icon("search", 16), el("span", { class: "kbd-l", text: "Search or jump to…" }), el("kbd", { text: "Ctrl K" })),
        el("div", { class: "ai-chip", id: "aiChip", role: "status" }),
        button("Toggle context panel", { icon: "panel", iconOnly: true, kind: "ghost", class: "panel-btn", onClick: () => setPanel(!S.panelOpen, true) })));

    const views = el("div", { class: "views" },
      el("section", { class: "view view-chat", id: "view-chat", "aria-label": "Conversation" },
        el("div", { class: "thread", id: "thread", role: "log", "aria-live": "polite", "aria-relevant": "additions" }),
        composer()),
      el("section", { class: "view view-page", id: "view-conversations", hidden: true }),
      el("section", { class: "view view-page", id: "view-saved", hidden: true }),
      el("section", { class: "view view-page", id: "view-mcp", hidden: true }),
      el("section", { class: "view view-page", id: "view-settings", hidden: true }));

    const panel = el("aside", { class: "ctx-panel", id: "ctxPanel", "aria-label": "Context panel" },
      el("div", { class: "ctx-head" }, el("div", { class: "ctx-t", id: "ctxTitle", text: "Context" }),
        button("Close panel", { icon: "x", iconOnly: true, kind: "ghost", small: true, onClick: () => setPanel(false, true) })),
      el("div", { class: "ctx-body", id: "ctxBody" }));
    const mapFab = el("button", { type: "button", class: "map-fab", id: "mapFab", onclick: () => setPanel(true, true), "aria-label": "Show results panel" },
      icon("map", 18), el("span", { text: "Map" }));

    app.append(sidebar, el("div", { class: "scrim", onclick: () => document.body.classList.remove("drawer-open") }),
      el("main", { class: "main" }, topbar, views), panel, mapFab);
  }

  function composer() {
    const ta = el("textarea", { id: "input", class: "input", rows: "1", "aria-label": "Message ULTRON",
      placeholder: "Ask anything, find places, compare stays, manage your day…", maxlength: "4000" });
    ta.addEventListener("input", autosize);
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
    });
    const send = el("button", { type: "button", class: "send", id: "sendBtn", "aria-label": "Send", onclick: () => submit() }, icon("send", 18));
    const stop = el("button", { type: "button", class: "send stop hidden", id: "stopBtn", "aria-label": "Stop", onclick: stopTask }, icon("stop", 16));
    return el("div", { class: "composer-wrap" },
      el("div", { class: "composer" }, el("div", { class: "composer-ico" }, icon("sparkles", 18)), ta, stop, send),
      el("div", { class: "composer-status", id: "composerStatus", "aria-live": "polite" }));
  }
  function autosize() {
    const ta = $("#input"); ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 200) + "px";
    ta.style.overflowY = ta.scrollHeight > 200 ? "auto" : "hidden";
  }
  function fitPlaceholder() {
    const ta = $("#input"); if (!ta) return;
    ta.placeholder = window.innerWidth < 600 ? "Ask anything, find places, stays…" : "Ask anything, find places, compare stays, manage your day…";
  }
  function prefill(text) {
    const ta = $("#input"); ta.value = text; autosize(); ta.focus();
    ta.setSelectionRange(text.length, text.length);
  }

  // ── navigation ──────────────────────────────────────────────────────────
  const TITLES = { chat: "Your world. Connected intelligently.", conversations: "Conversations", saved: "Saved Results",
    mcp: "MCP Hub", settings: "Settings" };
  function navigate(view, opts) {
    opts = opts || {};
    if (view === "new") { newChat(); view = "chat"; }
    S.view = view;
    document.body.classList.remove("drawer-open");
    $$(".nav-btn[data-view]").forEach((b) => {
      const on = b.dataset.view === view;
      b.classList.toggle("on", on);
      if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
    $$(".view").forEach((v) => { v.hidden = v.id !== "view-" + view; });
    const h = $("#viewTitle"); W.clear(h);
    if (view === "chat") h.append(el("span", { text: "Your world. " }), el("span", { class: "grad", text: "Connected intelligently." }));
    else h.textContent = TITLES[view] || "";
    document.body.dataset.view = view;
    if (view === "conversations") renderConversations();
    if (view === "saved") renderSaved();
    if (view === "mcp") renderMCP(true);
    if (view === "settings") renderSettings(opts.section);
    if (view === "chat") setTimeout(() => $("#input") && $("#input").focus(), 30);
    updatePanelVisibility();
  }
  W.bus.on("navigate", (d) => navigate(d.view, d));
  W.bus.on("send", (text) => { navigate("chat"); sendMessage(text); });

  function toggleSidebar() {
    document.body.classList.toggle("side-collapsed");
    W.api("settings.set", { values: { sidebar_collapsed: document.body.classList.contains("side-collapsed") } });
    setTimeout(W.maps.refresh, 260);
  }

  // ── chat ────────────────────────────────────────────────────────────────
  function newChat() {
    S.conversationId = null; S.messages = []; S.panelComp = null;
    renderThread(); updatePanelVisibility(); renderRecents();
  }
  function submit() {
    const ta = $("#input");
    const text = ta.value.trim();
    if (!text) return;
    ta.value = ""; autosize();
    sendMessage(text);
  }
  function sendMessage(text) {
    if (!text) return;
    if (text.startsWith("__view:")) { navigate(text.slice(7)); return; }
    const temp = { id: "tmp" + Date.now(), role: "user", text, created: Date.now() / 1000, pending: true };
    S.messages.push(temp);
    appendMessage(temp);
    W.api("chat.send", { conversation_id: S.conversationId, text }).then((r) => {
      if (!r.ok) {
        temp.failed = true;
        const n = $(`[data-mid="${temp.id}"]`);
        if (n) n.appendChild(el("div", { class: "msg-err" }, icon("alertCircle", 14), el("span", { text: r.error || "Not sent" })));
        return;
      }
      if (!S.conversationId) S.conversationId = r.conversation_id;
      const n = $(`[data-mid="${temp.id}"]`); if (n) n.dataset.mid = r.message_id;
      temp.id = r.message_id; temp.pending = false;
      liveBubble(r.task_id, r.conversation_id);
    });
  }
  function stopTask() {
    S.tasks.forEach((t, id) => { if (t.conversationId === S.conversationId) W.api("task.cancel", { task_id: id }); });
  }

  function renderThread() {
    const thread = $("#thread");
    W.clear(thread);
    if (!S.messages.length) { thread.appendChild(emptyState()); W.maps.sweep(); return; }
    S.messages.forEach((m) => thread.appendChild(renderMessage(m)));
    S.tasks.forEach((t, id) => { if (t.conversationId === S.conversationId) thread.appendChild(t.node); });
    W.maps.sweep();
    requestAnimationFrame(() => { thread.scrollTop = thread.scrollHeight; });
  }

  function emptyState() {
    const cards = el("div", { class: "quick-grid" }, QUICK.map(([ic, title, sub, prompt, tone], i) =>
      el("button", { type: "button", class: "quick quick-" + tone, style: { animationDelay: (i * 60) + "ms" },
        onclick: () => (prompt.endsWith(" ") ? prefill(prompt) : sendMessage(prompt)) },
        el("div", { class: "quick-ico" }, icon(ic, 22)),
        el("div", { class: "quick-t", text: title }), el("div", { class: "quick-s", text: sub }),
        el("div", { class: "quick-p" }, el("span", { text: prompt.trim() + (prompt.endsWith(" ") ? "…" : "") }), icon("arrowRight", 14)))));
    const recent = S.activity.length ? el("div", { class: "recent" }, el("div", { class: "side-h", text: "Recent activity" }),
      el("div", { class: "recent-row" }, S.activity.slice(0, 6).map((a) => el("span", { class: "chip chip-static" },
        icon({ route: "route", stays: "bed", nearby: "pin", research: "globe", email: "mail", drive: "drive", calendar: "calendar", link: "link" }[a.kind.split("_")[0]] || "activity", 13),
        el("span", { text: a.title }), el("span", { class: "chip-t", text: fmt.rel(a.at) }))))) : null;
    return el("div", { class: "empty" },
      el("div", { class: "hero" },
        el("div", { class: "hero-orb", "aria-hidden": "true" }, el("span"), el("span"), el("span")),
        el("h2", { class: "hero-h" }, el("span", { text: "One workspace. " }), el("span", { class: "grad", text: "Every tool." })),
        el("p", { class: "hero-p", text: "Ask in Serbian or English. ULTRON picks the right connected tools — maps, stays, research, your mail and files — and shows the answer as something you can use." })),
      cards, recent);
  }

  const PROVIDER = { claude: "Claude", gemini: "Gemini", mcp: "MCP tools", local: "ULTRON" };
  function renderMessage(m) {
    if (m.role === "user") {
      return el("div", { class: "msg msg-user", dataset: { mid: m.id } }, el("div", { class: "bubble", text: m.text }));
    }
    const ctx = { lang: m.lang || "en", conversationId: S.conversationId, messageId: m.id };
    const body = el("div", { class: "msg-body" });
    if (m.text) body.appendChild(W.md(m.text));
    (m.components || []).forEach((c) => body.appendChild(W.renderComponent(c, ctx)));
    if ((m.suggestions || []).length) body.appendChild(el("div", { class: "sugg", role: "group", "aria-label": "Suggested next steps" },
      m.suggestions.map((s) => el("button", { type: "button", class: "chip chip-sugg", onclick: () => sendMessage(s.prompt) }, icon("arrowUpRight", 13), el("span", { text: s.label })))));
    if ((m.activity || []).length) body.appendChild(activityDetails(m.activity));
    if (m.debug && S.settings.developer_mode) body.appendChild(el("details", { class: "debug" }, el("summary", { text: "Developer details" }),
      el("pre", { class: "codeblock", text: JSON.stringify(m.debug, null, 1) })));
    const tag = PROVIDER[m.provider] || "";
    return el("div", { class: "msg msg-ai" + (m.status === "error" ? " is-error" : ""), dataset: { mid: m.id } },
      el("div", { class: "avatar", "aria-hidden": "true" }, el("span")),
      el("div", { class: "msg-main" },
        el("div", { class: "msg-head" }, el("span", { class: "msg-n", text: "ULTRON" }),
          tag ? el("span", { class: "tag", text: tag }) : null,
          el("span", { class: "msg-time", text: new Date(m.created * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) })),
        body));
  }
  function activityDetails(steps) {
    const total = steps.reduce((a, s) => a + (s.ms || 0), 0);
    const failed = steps.filter((s) => s.status === "failed").length;
    return el("details", { class: "activity" },
      el("summary", null, icon("activity", 14), el("span", { text: `${steps.length} step${steps.length > 1 ? "s" : ""} · ${(total / 1000).toFixed(1)} s` + (failed ? ` · ${failed} failed` : "") })),
      el("ol", { class: "steps" }, steps.map(stepNode)));
  }
  function stepNode(s) {
    const ic = s.status === "done" ? "checkCircle" : s.status === "failed" ? "alertCircle" : null;
    return el("li", { class: "step st-" + s.status, dataset: { sid: s.id } },
      ic ? icon(ic, 15) : el("span", { class: "spinner sm" }),
      el("div", { class: "step-m" }, el("span", { class: "step-l", text: s.label }), s.detail ? el("span", { class: "step-d", text: s.detail }) : null),
      s.ms ? el("span", { class: "step-ms", text: (s.ms / 1000).toFixed(1) + " s" }) : null);
  }

  function liveBubble(taskId, conversationId) {
    if (S.tasks.has(taskId)) return S.tasks.get(taskId);
    // A fast task can finish before the reply to chat.send arrives.
    if (S.doneTasks.has(taskId)) return null;
    const steps = el("ol", { class: "steps live" });
    const node = el("div", { class: "msg msg-ai live", dataset: { task: taskId } },
      el("div", { class: "avatar thinking", "aria-hidden": "true" }, el("span")),
      el("div", { class: "msg-main" }, el("div", { class: "msg-head" }, el("span", { class: "msg-n", text: "ULTRON" }), el("span", { class: "tag", text: "working" })),
        el("div", { class: "thinking-row" }, el("span", { class: "dots" }, el("i"), el("i"), el("i")), el("span", { class: "thinking-t", text: "Understanding your request…" })),
        steps,
        button("Stop", { icon: "stop", kind: "ghost", small: true, class: "stop-inline", onClick: () => W.api("task.cancel", { task_id: taskId }) })));
    const t = { conversationId, node, steps, started: Date.now() };
    S.tasks.set(taskId, t);
    if (conversationId === S.conversationId) { $("#thread").appendChild(node); scrollDown(); }
    updateComposer();
    return t;
  }
  function scrollDown() { const th = $("#thread"); requestAnimationFrame(() => { th.scrollTo({ top: th.scrollHeight, behavior: W.reducedMotion() ? "auto" : "smooth" }); }); }
  function appendMessage(m) {
    const thread = $("#thread");
    const empty = $(".empty", thread); if (empty) empty.remove();
    thread.appendChild(renderMessage(m));
    scrollDown();
  }

  function updateComposer() {
    const running = [...S.tasks.values()].filter((t) => t.conversationId === S.conversationId || !S.conversationId);
    $("#stopBtn").classList.toggle("hidden", !running.length);
    $("#sendBtn").classList.toggle("hidden", !!running.length);
    renderComposerStatus();
  }
  function renderComposerStatus() {
    const box = $("#composerStatus"); if (!box) return;
    W.clear(box);
    const st = S.status || {};
    const active = st.active_provider;
    const prov = active ? (active === "claude" ? st.claude : st.gemini) : null;
    box.append(el("span", { class: "cs" }, el("span", { class: "sdot s-" + (prov ? stClass(prov.state) : "offline") }),
      el("span", { text: "AI: " + (active ? (active === "claude" ? "Claude" : "Gemini") + " · " + W.statusLabel(prov.state).toLowerCase() : "no provider ready") })));
    const servers = S.mcp ? S.mcp.length : null;
    if (servers !== null) {
      const ok = S.mcp.filter((s) => s.status === "connected").length;
      box.append(el("span", { class: "cs" }, icon("hub", 13), el("span", { text: `MCP ${ok}/${servers}` })));
    }
    const running = [...S.tasks.values()].find((t) => t.conversationId === S.conversationId);
    if (running) {
      const last = running.lastLabel || "Working…";
      box.append(el("span", { class: "cs cs-run" }, el("span", { class: "spinner sm" }), el("span", { text: last })));
    } else box.append(el("span", { class: "cs cs-hint", text: "Enter to send · Shift+Enter new line · Ctrl+K commands" }));
  }

  // ── events from Python ──────────────────────────────────────────────────
  function onEvent(ev) {
    switch (ev.type) {
      case "task.started": {
        if (ev.message && ev.component_id === undefined) {
          if (!S.conversationId || S.conversationId === ev.conversation_id) S.conversationId = ev.conversation_id;
          liveBubble(ev.task_id, ev.conversation_id);
        }
        if (ev.conversation) upsertConversation(ev.conversation);
        break;
      }
      case "task.step": {
        const t = S.tasks.get(ev.task_id); if (!t) break;
        const old = $(`[data-sid="${CSS.escape(ev.step.id)}"]`, t.steps);
        const n = stepNode(ev.step);
        if (old) old.replaceWith(n); else t.steps.appendChild(n);
        const th = $(".thinking-t", t.node); if (th && ev.step.status === "running") th.textContent = ev.step.label;
        t.lastLabel = ev.step.label;
        renderComposerStatus();
        break;
      }
      case "task.completed": case "task.cancelled": {
        S.doneTasks.add(ev.task_id);
        const t = S.tasks.get(ev.task_id);
        if (t) { t.node.remove(); S.tasks.delete(ev.task_id); }
        if (ev.conversation) upsertConversation(ev.conversation);
        if (ev.conversation_id === S.conversationId && ev.message) {
          S.messages.push(ev.message); appendMessage(ev.message);
          const mapComp = (ev.message.components || []).find((c) => W.mapCapable(c.kind));
          if (mapComp) showInPanel(mapComp, ev.message);
        } else if (ev.message) W.toast("Answer ready in “" + ((ev.conversation || {}).title || "another conversation") + "”", "info");
        updateComposer(); renderRecents();
        refreshActivity();
        break;
      }
      case "message.added": {
        if (ev.conversation) upsertConversation(ev.conversation);
        if (ev.conversation_id === S.conversationId) { S.messages.push(ev.message); appendMessage(ev.message); }
        else W.toast("New result in “" + ((ev.conversation || {}).title || "Voice requests") + "”", "info");
        break;
      }
      case "component.updated": {
        S.doneTasks.add(ev.task_id);
        const t = S.tasks.get(ev.task_id); if (t) { t.node.remove(); S.tasks.delete(ev.task_id); }
        $$(`[data-component-id="${CSS.escape(ev.component_id)}"]`).forEach((n) => W.cardBusy(n, false));
        if (ev.error) { W.toast(ev.error, "error"); updateComposer(); break; }
        const msg = S.messages.find((m) => m.id === ev.message_id);
        if (msg) msg.components = msg.components.map((c) => (c.id === ev.component_id ? ev.component : c));
        $$(`[data-component-id="${CSS.escape(ev.component_id)}"]`).forEach((n) => {
          const inPanel = !!n.closest("#ctxPanel");
          n.replaceWith(W.renderComponent(ev.component, { lang: (msg || {}).lang || "en", conversationId: ev.conversation_id, messageId: ev.message_id, inPanel }));
        });
        if (S.panelComp && S.panelComp.comp.id === ev.component_id) S.panelComp.comp = ev.component;
        W.maps.sweep(); updateComposer();
        break;
      }
      case "status.updated": S.status = ev.status; renderStatus(); break;
      case "mcp.updated": if (S.view === "mcp") renderMCP(false); else refreshMcpSummary(); break;
      case "saved.updated": W.api("saved.list").then((r) => { if (r.ok) { S.saved = r.saved; if (S.view === "saved") renderSaved(); } }); break;
      case "conversations.updated": refreshConversations(); break;
      case "settings.updated": S.settings = ev.settings; applySettings(); break;
      case "navigate": if (ev.conversation_id) openConversation(ev.conversation_id); else navigate(ev.view || "chat", { section: ev.section }); break;
      case "present": if (ev.conversation_id) { navigate("chat"); openConversation(ev.conversation_id); } break;
      case "diag.result": W.bus.emit("diag", ev); break;
      default: break;
    }
  }

  function upsertConversation(c) {
    const i = S.conversations.findIndex((x) => x.id === c.id);
    const row = Object.assign({}, S.conversations[i] || {}, c, { updated: Date.now() / 1000 });
    if (i >= 0) S.conversations.splice(i, 1);
    S.conversations.unshift(row);
    renderRecents();
  }
  function refreshConversations() {
    W.api("conversation.list").then((r) => { if (r.ok) { S.conversations = r.conversations; renderRecents(); if (S.view === "conversations") renderConversations(); } });
  }
  function refreshActivity() { W.api("activity.list").then((r) => { if (r.ok) S.activity = r.activity; }); }
  function openConversation(id) {
    return W.api("conversation.load", { id }).then((r) => {
      if (!r.ok) { W.toast(r.error || "Not found", "error"); return; }
      S.conversationId = id; S.messages = r.conversation.messages || [];
      S.panelComp = null;
      for (let i = S.messages.length - 1; i >= 0 && !S.panelComp; i--) {
        const c = (S.messages[i].components || []).find((x) => W.mapCapable(x.kind));
        if (c) S.panelComp = { comp: c, msg: S.messages[i] };
      }
      navigate("chat"); renderThread(); renderPanel(); updateComposer(); renderRecents();
    });
  }

  function renderRecents() {
    const box = $("#recentList"); if (!box) return;
    W.clear(box);
    S.conversations.slice(0, 7).forEach((c) => box.appendChild(el("button", { type: "button",
      class: "recent-btn" + (c.id === S.conversationId ? " on" : ""), title: c.title, onclick: () => openConversation(c.id) },
      icon("chats", 14), el("span", { text: c.title || "Conversation" }))));
  }

  // ── context panel ───────────────────────────────────────────────────────
  function showInPanel(comp, msg) {
    S.panelComp = { comp, msg };
    S.overlayOpen = false;
    renderPanel();
    if (S.settings.context_panel !== false && wide()) setPanel(true);
  }
  function renderPanel() {
    const body = $("#ctxBody"); if (!body) return;
    W.clear(body);
    if (!S.panelComp) { updatePanelVisibility(); return; }
    const { comp, msg } = S.panelComp;
    const titles = { route_map: "Route", place_results: "Places", stay_results: "Stays", property_focus: "Location", research: "Sources" };
    $("#ctxTitle").textContent = titles[comp.kind] || "Context";
    body.appendChild(W.renderComponent(comp, { lang: msg.lang || "en", conversationId: S.conversationId, messageId: msg.id, inPanel: true }));
    updatePanelVisibility();
    W.maps.sweep();
  }
  const wide = () => window.innerWidth >= 1280;
  function setPanel(open, user) {
    if (wide()) {
      S.panelOpen = open;
      if (user) W.api("settings.set", { values: { context_panel: open } });
    } else {
      S.overlayOpen = open;            // narrow screens: an overlay, opened only on request
    }
    updatePanelVisibility();
    setTimeout(W.maps.refresh, 280);
  }
  function updatePanelVisibility() {
    const has = !!S.panelComp && S.view === "chat";
    const open = has && (wide() ? S.panelOpen : !!S.overlayOpen);
    document.body.classList.toggle("panel-has", has);
    document.body.classList.toggle("panel-open", open);
  }

  // ── status chips ────────────────────────────────────────────────────────
  function renderStatus() {
    const st = S.status || {};
    const chip = $("#aiChip"); W.clear(chip);
    const active = st.active_provider;
    const prov = active ? st[active] : null;
    const limit = st.claude_limit && st.claude_limit.limit_hit;
    chip.append(...[el("span", { class: "sdot s-" + (prov ? stClass(prov.state) : "offline") }),
      el("span", { class: "ai-n", text: active ? (active === "claude" ? "Claude" : "Gemini") : "No AI" }),
      limit ? el("span", { class: "ai-x", title: "Claude usage limit · resets " + (st.claude_limit.reset || "soon"), text: "fallback" }) : null].filter(Boolean));
    chip.title = active ? `${active} · ${W.statusLabel(prov.state)}` : "No AI provider is ready — see Settings → AI Engine";
    chip.onclick = () => navigate("settings", { section: "ai" });
    const side = $("#sideStatus"); W.clear(side);
    ["claude", "gemini"].forEach((k) => {
      const p = st[k] || {};
      side.appendChild(el("div", { class: "ss" }, el("span", { class: "sdot s-" + stClass(p.state) }),
        el("span", { class: "ss-n", text: k === "claude" ? "Claude" : "Gemini" }), el("span", { class: "ss-s", text: W.statusLabel(p.state) })));
    });
    if (S.mcp) {
      const ok = S.mcp.filter((s) => s.status === "connected").length;
      side.appendChild(el("div", { class: "ss" }, el("span", { class: "sdot s-" + (ok ? "connected" : "offline") }),
        el("span", { class: "ss-n", text: "MCP" }), el("span", { class: "ss-s", text: `${ok}/${S.mcp.length} connected` })));
    }
    renderComposerStatus();
  }
  function refreshMcpSummary() {
    W.api("mcp.list").then((r) => { if (r.ok) { S.mcp = r.servers; renderStatus(); } });
  }

  // ── Conversations view (with unified local search) ──────────────────────
  function renderConversations() {
    const view = $("#view-conversations"); W.clear(view);
    const q = el("input", { class: "inp inp-lg", type: "search", placeholder: "Search conversations and saved results…", "aria-label": "Search workspace" });
    const results = el("div", { class: "search-results" });
    const list = el("div", { class: "conv-list" });
    let timer;
    q.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const v = q.value.trim(); W.clear(results);
        if (v.length < 2) { list.hidden = false; return; }
        list.hidden = true;
        W.api("search", { query: v }).then((r) => {
          W.clear(results);
          results.append(el("div", { class: "sr-h" }, el("span", { class: "badge-local", text: "Local workspace" }),
            el("span", { text: `${r.conversations.length} conversations · ${r.saved.length} saved` })));
          r.conversations.forEach((c) => results.appendChild(el("button", { type: "button", class: "conv", onclick: () => openConversation(c.id) },
            icon("chats", 16), el("div", { class: "conv-m" }, el("div", { class: "conv-t", text: c.title }), el("div", { class: "conv-p", text: "…" + c.snippet + "…" })))));
          r.saved.forEach((s) => results.appendChild(el("button", { type: "button", class: "conv", onclick: () => navigate("saved") },
            icon("bookmark", 16), el("div", { class: "conv-m" }, el("div", { class: "conv-t", text: s.title }), el("div", { class: "conv-p", text: s.kind + " · " + (s.source || "") })))));
          results.append(el("div", { class: "sr-h" }, el("span", { class: "badge-ext", text: "External" })),
            el("button", { type: "button", class: "conv", onclick: () => { newChat(); navigate("chat"); sendMessage("Istraži " + v); } },
              icon("globe", 16), el("div", { class: "conv-m" }, el("div", { class: "conv-t", text: `Search the web for “${v}”` }),
                el("div", { class: "conv-p", text: "Opens a new research conversation (DuckDuckGo + AI summary)" }))));
        });
      }, 220);
    });
    view.append(el("div", { class: "page" }, el("div", { class: "page-bar" }, q,
      button("New chat", { icon: "plusChat", kind: "primary", onClick: () => navigate("new") })), results, list));
    if (!S.conversations.length) list.appendChild(emptyPage("chats", "No conversations yet", "Start one from the Workspace."));
    S.conversations.forEach((c) => {
      const row = el("div", { class: "conv" + (c.id === S.conversationId ? " on" : "") },
        el("button", { type: "button", class: "conv-open", onclick: () => openConversation(c.id) }, icon("chats", 16),
          el("div", { class: "conv-m" }, el("div", { class: "conv-t", text: c.title || "Conversation" }),
            el("div", { class: "conv-p", text: (c.preview || "") + (c.count ? ` · ${c.count} messages` : "") }))),
        el("span", { class: "conv-time", text: fmt.rel(c.updated) }),
        button("Rename", { icon: "edit", iconOnly: true, kind: "ghost", small: true, onClick: () => {
          const title = window.prompt("Rename conversation", c.title || ""); if (title) W.api("conversation.rename", { id: c.id, title }); } }),
        button("Delete", { icon: "trash", iconOnly: true, kind: "ghost", small: true, onClick: () => {
          if (window.confirm("Delete this conversation? This cannot be undone.")) W.api("conversation.delete", { id: c.id }).then(() => { if (c.id === S.conversationId) newChat(); }); } }));
      list.appendChild(row);
    });
    setTimeout(() => q.focus(), 20);
  }
  function emptyPage(ic, title, sub) {
    return el("div", { class: "empty-page" }, el("div", { class: "quick-ico" }, icon(ic, 24)), el("div", { class: "ep-t", text: title }), el("div", { class: "ep-s", text: sub }));
  }

  // ── Saved Results ───────────────────────────────────────────────────────
  let savedFilter = "all";
  function renderSaved() {
    const view = $("#view-saved"); W.clear(view);
    const kinds = [["all", "All"], ["route", "Routes"], ["property", "Stays"], ["place", "Places"], ["research", "Research"]];
    const chips = el("div", { class: "chips" }, kinds.map(([k, l]) => el("button", { type: "button", class: "chip" + (savedFilter === k ? " on" : ""), "aria-pressed": String(savedFilter === k),
      onclick: () => { savedFilter = k; renderSaved(); } }, el("span", { text: l }))));
    const grid = el("div", { class: "saved-grid" });
    const items = S.saved.filter((s) => savedFilter === "all" || s.kind === savedFilter);
    if (!items.length) grid.appendChild(emptyPage("bookmark", "Nothing saved yet", "Use the bookmark on a route, place, stay or research result."));
    const IC = { route: "route", property: "bed", place: "pin", research: "globe", note: "file" };
    items.forEach((s) => {
      const d = s.data || {};
      const detail = s.kind === "route" ? `${fmt.km(d.distance_m)} · ${fmt.dur(d.duration_s)}` :
        s.kind === "property" ? [d.night_price != null ? fmt.money(d.night_price, d.total_currency) + " / night" : "", d.city].filter(Boolean).join(" · ") :
        s.kind === "place" ? (d.address || "") : s.kind === "research" ? (d.answer || "").slice(0, 120) : "";
      const link = d.open_url || d.url || d.maps_url;
      grid.appendChild(el("article", { class: "saved" },
        el("div", { class: "saved-h" }, el("div", { class: "card-ico" }, icon(IC[s.kind] || "bookmark", 16)),
          el("div", { class: "saved-t", text: s.title }),
          button("Remove", { icon: "trash", iconOnly: true, kind: "ghost", small: true, onClick: () => W.api("saved.remove", { id: s.id }) })),
        el("div", { class: "saved-d", text: detail }),
        el("div", { class: "saved-f" }, el("span", { class: "muted small", text: (s.source || s.kind) + " · " + fmt.rel(s.saved_at) }),
          link ? button("Open", { icon: "external", kind: "ghost", small: true, onClick: () => W.open(link) }) : null,
          button("Ask about it", { icon: "sparkles", kind: "ghost", small: true, onClick: () => { navigate("chat"); prefill(s.kind === "route" ? `Koliko ima od ${d.origin && d.origin.name} do ${d.destination && d.destination.name}?` : s.title + " — "); } }))));
    });
    view.append(el("div", { class: "page" }, el("div", { class: "page-bar" }, chips), grid));
  }

  // ── MCP Hub ─────────────────────────────────────────────────────────────
  const KIND = { stdio: "Local · stdio", http: "Remote · HTTP", claude: "Claude connector" };
  const SVC_ICON = { gmail: "mail", drive: "drive", docs: "file", gamma: "layers" };
  function renderMCP(refresh) {
    const view = $("#view-mcp");
    if (!view.firstChild) view.appendChild(el("div", { class: "page" }, el("div", { class: "skeleton-grid" }, [1, 2, 3, 4].map(() => el("div", { class: "skel" })))));
    W.api("mcp.list", { refresh: !!refresh }).then((r) => {
      if (!r.ok) { W.toast(r.error || "MCP list failed", "error"); return; }
      S.mcp = r.servers; renderStatus();
      W.clear(view);
      const ok = r.servers.filter((s) => s.status === "connected").length;
      const failed = r.servers.filter((s) => ["error", "offline", "auth_required"].includes(s.status)).length;
      const tools = r.servers.reduce((a, s) => a + (s.status === "connected" ? s.tools.length : 0), 0);
      const summary = el("div", { class: "health" },
        stat("Connected", `${ok} / ${r.servers.length}`, "checkCircle", "ok"), stat("Needs attention", String(failed), "alert", failed ? "warn" : "mute"),
        stat("Tools available", String(tools), "zap", "ok"), stat("Checked", fmt.rel(r.checked_at), "clock", "mute"));
      const bar = el("div", { class: "page-bar" },
        el("p", { class: "muted", text: "Every status below comes from a real handshake or call — nothing is shown as connected until it answers." }),
        el("div", { class: "row-gap" },
          button("Refresh all", { icon: "refresh", kind: "ghost", onClick: () => renderMCP(true) }),
          W.transport.mode === "qt" ? button("Credentials…", { icon: "key", kind: "ghost", title: "Google, Maps, Amadeus and iCloud credentials (native, encrypted)", onClick: () => W.api("credentials.open") }) : null,
          W.transport.mode === "qt" ? button("Config folder", { icon: "file", kind: "ghost", onClick: () => W.api("config.open") }) : null));
      const grid = el("div", { class: "mcp-grid" }, r.servers.map(serverCard));
      const builtin = r.servers.find((s) => s.builtin);
      const sources = builtin && builtin.capabilities.length ? el("section", { class: "rcard" },
        el("div", { class: "card-head" }, el("div", { class: "card-ico" }, icon("database", 18)),
          el("div", { class: "card-titles" }, el("div", { class: "card-title", text: "Data sources behind ULTRON Workspace" }),
            el("div", { class: "card-sub", text: "Which real provider answers each tool, and its last outcome" }))),
        el("ul", { class: "rows" }, builtin.capabilities.map((c) => el("li", { class: "row" },
          el("span", { class: "sdot s-" + (c.status === "configured" ? (c.last_status === "error" ? "error" : "connected") : c.status === "link only" ? "ready" : "not_configured") }),
          el("div", { class: "row-main" }, el("div", { class: "row-t", text: c.name }),
            el("div", { class: "row-s", text: [c.provider, c.note, c.last_ok ? "last success " + fmt.rel(c.last_ok) : "", c.last_error].filter(Boolean).join(" · ") })),
          el("span", { class: "pill pill-" + (c.status === "configured" ? "ok" : c.status === "link only" ? "mute" : "warn"), text: c.status.replace("_", " ") }))))) : null;
      const howto = el("details", { class: "rcard howto" }, el("summary", null, icon("plus", 15), el("span", { text: "Add another MCP server" })),
        el("p", { class: "muted", text: "Add an entry to config/workspace/mcp_servers.json, then press Refresh. Tokens are referenced by name from the encrypted credential store, never written in the file. Tools that are not marked read-only always ask for your confirmation." }),
        el("pre", { class: "codeblock", text: JSON.stringify({ servers: [{ id: "my-server", name: "My server", transport: "http", url: "https://example.com/mcp", auth_secret: "my_server_token" }] }, null, 1) }));
      view.append(el("div", { class: "page" }, bar, summary, grid, sources, howto));
    });
  }
  function stat(k, v, ic, tone) {
    return el("div", { class: "hstat hs-" + tone }, icon(ic, 18), el("div", null, el("div", { class: "hs-v", text: v }), el("div", { class: "hs-k", text: k })));
  }
  function serverCard(s) {
    const ic = s.kind === "claude" ? (SVC_ICON[s.service] || "brain") : s.builtin ? "orb" : "hub";
    const tools = el("details", { class: "tools" }, el("summary", { text: `${s.tools.length} tool${s.tools.length === 1 ? "" : "s"}` }),
      el("ul", null, s.tools.map((t) => el("li", null, el("span", { class: "tname", text: t.name }),
        el("span", { class: "pill pill-" + (t.read_only ? "ok" : "warn"), text: t.read_only ? "read-only" : "needs review" })))));
    const perms = s.kind === "claude" ? "Read & draft only (sending, deleting, sharing need your review)" : s.builtin ? "Read-only tools (annotated readOnlyHint)" : "Read-only tools run directly; others ask first";
    const actions = el("div", { class: "srv-acts" });
    const test = button("Test connection", { icon: "zap", kind: "ghost", small: true, onClick: () => {
      test.disabled = true; test.querySelector("span").textContent = "Testing…";
      W.api("mcp.test", { id: s.id }, 60000).then((r) => { W.toast(r.message || (r.ok ? "OK" : "Failed"), r.ok ? "success" : "error"); renderMCP(false); });
    } });
    actions.append(test);
    if (s.kind !== "claude") {
      actions.append(button(s.enabled ? "Disconnect" : "Connect", { icon: "power", kind: s.enabled ? "ghost" : "primary", small: true,
        onClick: () => W.api("mcp.enable", { id: s.id, enabled: !s.enabled }, 60000).then((r) => { W.toast(r.message || (r.ok ? "Done" : "Failed"), r.ok ? "success" : "error"); renderMCP(false); }) }));
    } else if (s.status !== "connected") {
      actions.append(button("How to reconnect", { icon: "info", kind: "ghost", small: true,
        onClick: () => W.toast("Re-authorise this connector at claude.ai → Settings → Connectors, then press Test connection.", "info") }));
    }
    return el("article", { class: "srv srv-" + s.status },
      el("div", { class: "srv-h" }, el("div", { class: "srv-ico" }, icon(ic, 20)),
        el("div", { class: "srv-t" }, el("div", { class: "srv-n", text: s.name }), el("div", { class: "srv-k", text: KIND[s.kind] || s.kind })),
        el("span", { class: "status s-" + s.status }, el("span", { class: "sdot s-" + s.status }), el("span", { text: W.statusLabel(s.status) }))),
      s.description ? el("p", { class: "srv-d", text: s.description }) : null,
      el("dl", { class: "srv-meta" },
        el("dt", { text: "Authorization" }), el("dd", { text: s.auth }),
        el("dt", { text: "Last success" }), el("dd", { text: s.last_ok ? fmt.rel(s.last_ok) : "never" }),
        el("dt", { text: "Endpoint" }), el("dd", { class: "mono", text: s.endpoint || "—" }),
        el("dt", { text: "Permissions" }), el("dd", { text: perms }),
        s.via && s.via !== s.kind ? el("dt", { text: "Running" }) : null, s.via && s.via !== s.kind ? el("dd", { text: s.via }) : null),
      s.error ? el("div", { class: "srv-err" }, icon("alertCircle", 14), el("span", { text: s.error })) : null,
      tools, actions);
  }

  // ── Settings ────────────────────────────────────────────────────────────
  function renderSettings(section) {
    const view = $("#view-settings"); W.clear(view);
    const tabs = [["general", "General"], ["ai", "AI Engine"], ["connections", "Connections & privacy"], ["diagnostics", "Advanced diagnostics"]];
    section = section || view.dataset.section || "general";
    view.dataset.section = section;
    const tabBar = el("div", { class: "tabs", role: "tablist" }, tabs.map(([k, l]) => el("button", { type: "button", role: "tab", class: "tab" + (k === section ? " on" : ""),
      "aria-selected": String(k === section), onclick: () => renderSettings(k) }, el("span", { text: l }))));
    const body = el("div", { class: "settings-body" });
    view.append(el("div", { class: "page" }, tabBar, body));
    ({ general: settingsGeneral, ai: settingsAI, connections: settingsConnections, diagnostics: settingsDiagnostics })[section](body);
  }
  function field(label, control, hint) {
    return el("label", { class: "field" }, el("span", { class: "field-l", text: label }), control, hint ? el("span", { class: "field-h", text: hint }) : null);
  }
  function toggle(checked, onChange, label) {
    const inp = el("input", { type: "checkbox", class: "tg-in", checked: !!checked, "aria-label": label });
    inp.addEventListener("change", () => onChange(inp.checked));
    return el("span", { class: "tg" }, inp, el("span", { class: "tg-ui", "aria-hidden": "true" }));
  }
  function setS(values) { return W.api("settings.set", { values }).then((r) => { if (r.ok) { S.settings = r.settings; applySettings(); W.toast("Saved", "success"); } }); }
  function settingsGeneral(body) {
    const st = S.settings;
    const lang = el("select", { class: "inp", onchange: (e) => setS({ language: e.target.value }) },
      [["auto", "Match my message (Serbian or English)"], ["sr", "Always Serbian"], ["en", "Always English"]].map(([v, l]) => el("option", { value: v, text: l, selected: st.language === v })));
    const guests = el("input", { class: "inp", type: "number", min: "1", max: "16", value: String(st.default_guests || 1), onchange: (e) => setS({ default_guests: Number(e.target.value) }) });
    const cur = el("select", { class: "inp", onchange: (e) => setS({ currency: e.target.value }) }, ["EUR", "RSD", "USD", "GBP", "CHF"].map((c) => el("option", { value: c, text: c, selected: st.currency === c })));
    const home = el("input", { class: "inp", type: "text", maxlength: "200", value: st.home_origin || "", placeholder: "e.g. Niš, or a street address" });
    home.addEventListener("change", () => setS({ home_origin: home.value.trim() }));
    body.append(section("Answers", [field("Reply language", lang)]),
      section("Travel defaults", [field("Default guests", guests, "Used when a request doesn't say how many people; always shown as an assumption."),
        field("Currency for prices", cur, "Converted at the National Bank of Serbia middle rate."),
        field("Default route start", home, "Used for “Koliko ima do …?” when no origin is given. Leave empty to be asked. Stored locally.")]),
      section("Layout", [el("div", { class: "field row-f" }, el("span", { class: "field-l", text: "Open the context panel automatically" }), toggle(st.context_panel !== false, (v) => setS({ context_panel: v }), "Context panel")),
        el("div", { class: "field row-f" }, el("span", { class: "field-l", text: "Developer mode (show intents and timings)" }), toggle(st.developer_mode, (v) => setS({ developer_mode: v }), "Developer mode"))]));
  }
  function section(title, children) { return el("section", { class: "set-sec" }, el("h3", { class: "set-h", text: title }), children); }
  function settingsAI(body) {
    body.appendChild(el("div", { class: "skel" }));
    W.api("routing.get").then((r) => {
      W.clear(body);
      if (!r.ok) { body.appendChild(el("div", { class: "notice notice-warning", text: r.error || "Unavailable" })); return; }
      const rt = r.routing, st = r.status;
      const orderKey = rt.provider_order.join(",");
      const order = el("select", { class: "inp", onchange: (e) => W.api("routing.set", { provider_order: e.target.value.split(",") }).then(() => { W.toast("Saved", "success"); renderSettings("ai"); }) },
        [["claude,gemini", "Claude first, then Gemini"], ["gemini,claude", "Gemini first, then Claude"], ["claude", "Claude only"], ["gemini", "Gemini only"]]
          .map(([v, l]) => el("option", { value: v, text: l, selected: v === orderKey })));
      const model = el("select", { class: "inp", onchange: (e) => W.api("routing.set", { claude_model: e.target.value }).then(() => W.toast("Saved", "success")) },
        [["", "Account default"], ["opus", "Opus"], ["sonnet", "Sonnet"], ["haiku", "Haiku (fastest)"], ["fable", "Fable"]].map(([v, l]) => el("option", { value: v, text: l, selected: rt.claude_model === v })));
      const provCard = (key, name) => {
        const p = st[key] || {};
        const extra = key === "claude" && st.claude_limit && st.claude_limit.limit_hit ? `Usage limit · resets ${st.claude_limit.reset || "soon"}${st.claude_limit.remaining ? " (" + st.claude_limit.remaining + ")" : ""}` :
          key === "gemini" ? (st.gemini_live && st.gemini_live.connected ? "Live voice session connected" : "Live voice session not connected") : "";
        const limited = key === "claude" && st.claude_limit && st.claude_limit.limit_hit;
        return el("div", { class: "prov" }, el("div", { class: "prov-h" }, el("span", { class: "sdot s-" + (limited ? "offline" : stClass(p.state)) }), el("strong", { text: name }),
          el("span", { class: "pill pill-" + (p.usable && !limited ? "ok" : "warn"), text: limited ? "Usage limit" : W.statusLabel(p.state) })),
          el("div", { class: "row-s", text: [p.integration, p.model, p.last_ok ? "verified " + fmt.rel(p.last_ok) : "", extra].filter(Boolean).join(" · ") }),
          p.last_error && !p.usable ? el("div", { class: "srv-err" }, icon("alertCircle", 14), el("span", { text: p.last_error })) : null);
      };
      body.append(
        section("Providers", [el("div", { class: "prov-grid" }, provCard("claude", "Claude"), provCard("gemini", "Gemini")),
          el("p", { class: "muted small", text: "Active provider: " + (st.active_provider || "none") + ". Statuses come from real requests; “ready” means signed in but not yet verified." })]),
        section("Routing", [field("Primary and fallback", order, "Used for conversation, planning and research summaries. Voice always uses Gemini Live."),
          el("div", { class: "field row-f" }, el("span", { class: "field-l", text: "Allow automatic fallback when the primary fails or hits a limit" }),
            toggle(rt.allow_provider_fallback, (v) => W.api("routing.set", { allow_provider_fallback: v }).then(() => W.toast("Saved", "success")), "Fallback")),
          field("Claude model", model)]),
        section("MCP configuration", [el("p", { class: "muted", text: "Servers, tools and their health live in MCP Hub." }),
          button("Open MCP Hub", { icon: "hub", kind: "ghost", onClick: () => navigate("mcp") })]));
    });
  }
  function settingsConnections(body) {
    W.api("routing.get").then((r) => {
      W.clear(body);
      const prefs = r.preferences || {}, perms = r.permissions || {};
      const pref = (svc) => el("select", { class: "inp", onchange: (e) => W.api("prefs.set", { [svc]: e.target.value }).then(() => W.toast("Saved", "success")) },
        [["ask", "Ask me each time"], ["claude", "Claude connector (MCP)"], ["user", "My own Google connection (ULTRON OAuth)"]].map(([v, l]) => el("option", { value: v, text: l, selected: (prefs[svc] || "ask") === v })));
      body.append(
        section("Which connection to use", [field("Gmail", pref("gmail")), field("Google Drive", pref("drive"),
          "The workspace reads Gmail and Drive through Claude's connectors (read and draft only). Voice commands follow this choice.")]),
        section("Credentials", [el("p", { class: "muted", text: "Google OAuth, Google Maps keys, Amadeus and iCloud credentials are edited in a native, encrypted dialog — they never enter this page." }),
          W.transport.mode === "qt" ? button("Manage credentials…", { icon: "key", kind: "primary", onClick: () => W.api("credentials.open") })
            : el("div", { class: "notice notice-info", text: "Open the desktop app to manage credentials." })]),
        section("Permissions", [
          el("div", { class: "field row-f" }, el("span", { class: "field-l", text: "Allow ULTRON's structured Chrome controls" }),
            toggle(perms.chrome, (v) => W.api("permission.set", { name: "chrome", enabled: v }).then(() => W.toast("Saved", "success")), "Chrome")),
          el("div", { class: "field row-f" }, el("span", { class: "field-l", text: "Spoken system-health alerts" }),
            toggle(perms.system_alerts, (v) => W.api("permission.set", { name: "system_alerts", enabled: v }).then(() => W.toast("Saved", "success")), "Alerts")),
          el("p", { class: "muted small", text: "Bookings, payments, sending email and deleting files are never done automatically — each needs your explicit confirmation in a native dialog." })]));
    });
  }
  function settingsDiagnostics(body) {
    const out = el("pre", { class: "codeblock diag-out", "aria-live": "polite", text: "Results appear here." });
    const run = (which, extra) => { out.textContent = "Running " + which + "…"; W.api("diag.run", Object.assign({ which }, extra || {})); };
    const prompt = el("textarea", { class: "inp", rows: "3", placeholder: "Prompt for the brain console (uses your routing settings)…" });
    body.append(
      section("Checks", [el("div", { class: "diag-grid" },
        button("Check Claude limit now", { icon: "clock", kind: "ghost", onClick: () => run("claude_limit") }),
        button("Test Claude", { icon: "zap", kind: "ghost", onClick: () => run("claude_test") }),
        button("Test Gemini fallback", { icon: "sparkles", kind: "ghost", onClick: () => run("gemini_test") }),
        button("Recheck all connections", { icon: "refresh", kind: "ghost", onClick: () => run("recheck") }),
        button("View connection errors", { icon: "alert", kind: "ghost", onClick: () => run("errors") }))]),
      section("Brain console", [prompt, button("Send to brain", { icon: "send", kind: "primary", onClick: () => run("brain", { prompt: prompt.value }) })]),
      section("Output", [out]));
    W.bus.on("diag", (ev) => { if (out.isConnected) out.textContent = (ev.ok ? "✓ " : "✗ ") + ev.which + "\n\n" + (ev.text || ""); });
  }

  // ── command palette ─────────────────────────────────────────────────────
  function paletteItems(q) {
    const items = [
      { g: "Actions", ic: "plusChat", l: "New chat", run: () => navigate("new") },
      { g: "Go to", ic: "grid", l: "Workspace", run: () => navigate("chat") },
      { g: "Go to", ic: "chats", l: "Conversations", run: () => navigate("conversations") },
      { g: "Go to", ic: "bookmark", l: "Saved Results", run: () => navigate("saved") },
      { g: "Go to", ic: "hub", l: "MCP Hub", run: () => navigate("mcp") },
      { g: "Go to", ic: "settings", l: "Settings", run: () => navigate("settings") },
      { g: "Go to", ic: "brain", l: "Settings → AI Engine", run: () => navigate("settings", { section: "ai" }) },
      { g: "Go to", ic: "terminal", l: "Settings → Advanced diagnostics", run: () => navigate("settings", { section: "diagnostics" }) },
      ...SHORTCUTS.map(([ic, l, p, send]) => ({ g: "Shortcuts", ic, l, run: () => { navigate("chat"); send ? sendMessage(p) : prefill(p); } })),
      ...S.conversations.slice(0, 30).map((c) => ({ g: "Conversations", ic: "chats", l: c.title || "Conversation", run: () => openConversation(c.id) })),
      ...S.saved.slice(0, 30).map((s) => ({ g: "Saved", ic: "bookmark", l: s.title, run: () => navigate("saved") })),
      ...(S.mcp || []).flatMap((s) => s.tools.map((t) => ({ g: "MCP tools", ic: "zap", l: `${t.name} · ${s.name}`, run: () => navigate("mcp") }))),
    ];
    const f = q.trim().toLowerCase();
    const list = f ? items.filter((i) => (i.l + " " + i.g).toLowerCase().includes(f)) : items.slice(0, 14);
    if (f) list.push({ g: "Ask", ic: "sparkles", l: `Ask ULTRON: “${q.trim()}”`, run: () => { navigate("chat"); sendMessage(q.trim()); } });
    return list.slice(0, 40);
  }
  function openPalette() {
    if ($(".palette")) return;
    const prevFocus = document.activeElement;
    const inp = el("input", { class: "pal-in", type: "text", placeholder: "Type a command, conversation or question…", "aria-label": "Command", role: "combobox", "aria-expanded": "true", "aria-controls": "palList" });
    const list = el("ul", { class: "pal-list", id: "palList", role: "listbox" });
    let sel = 0, items = [];
    const close = () => { pal.remove(); if (prevFocus && prevFocus.focus) prevFocus.focus(); };
    const draw = () => {
      items = paletteItems(inp.value); sel = Math.min(sel, items.length - 1); W.clear(list);
      let g = "";
      items.forEach((it, i) => {
        if (it.g !== g) { g = it.g; list.appendChild(el("li", { class: "pal-g", role: "presentation", text: g })); }
        list.appendChild(el("li", { class: "pal-i" + (i === sel ? " on" : ""), role: "option", "aria-selected": String(i === sel), id: "pal-" + i,
          onclick: () => { close(); it.run(); }, onmousemove: () => { if (sel !== i) { sel = i; mark(); } } }, icon(it.ic, 16), el("span", { text: it.l })));
      });
      inp.setAttribute("aria-activedescendant", "pal-" + sel);
    };
    const mark = () => { $$(".pal-i", list).forEach((n) => { const on = n.id === "pal-" + sel; n.classList.toggle("on", on); n.setAttribute("aria-selected", String(on)); if (on) n.scrollIntoView({ block: "nearest" }); }); inp.setAttribute("aria-activedescendant", "pal-" + sel); };
    inp.addEventListener("input", () => { sel = 0; draw(); });
    inp.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(items.length - 1, sel + 1); mark(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(0, sel - 1); mark(); }
      else if (e.key === "Enter") { e.preventDefault(); const it = items[sel]; close(); if (it) it.run(); }
      else if (e.key === "Escape") { e.preventDefault(); close(); }
      else if (e.key === "Tab") e.preventDefault();
    });
    const pal = el("div", { class: "palette", role: "dialog", "aria-modal": "true", "aria-label": "Command palette", onclick: (e) => { if (e.target === pal) close(); } },
      el("div", { class: "pal-box" }, el("div", { class: "pal-top" }, icon("search", 18), inp, el("kbd", { text: "Esc" })), list,
        el("div", { class: "pal-foot" }, el("span", { text: "↑↓ navigate · Enter run · Esc close" }))));
    document.body.appendChild(pal);
    draw(); inp.focus();
  }

  // ── settings application & keyboard ─────────────────────────────────────
  function applySettings() {
    document.body.classList.toggle("side-collapsed", !!S.settings.sidebar_collapsed && window.innerWidth >= 900);
    S.panelOpen = S.settings.context_panel !== false;
    S.lang = S.settings.language === "sr" ? "sr" : "en";
    updatePanelVisibility();
  }
  document.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
    else if (mod && e.key.toLowerCase() === "b") { e.preventDefault(); toggleSidebar(); }
    else if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) { e.preventDefault(); navigate("chat"); }
    else if (e.key === "Escape") document.body.classList.remove("drawer-open");
  });
  window.addEventListener("resize", () => { clearTimeout(window.__rz); window.__rz = setTimeout(() => { W.maps.refresh(); updatePanelVisibility(); fitPlaceholder(); }, 120); });

  // ── boot ────────────────────────────────────────────────────────────────
  function boot() {
    buildShell();
    fitPlaceholder();
    navigate("chat");
    renderThread();
    W.transport.init().then((mode) => {
      document.body.dataset.transport = mode;
      if (mode === "none") {
        $("#thread").prepend(el("div", { class: "notice notice-warning" }, icon("alert", 16),
          el("div", null, el("strong", { text: "Not connected to ULTRON." }), el("div", { text: "Open the Workspace from the ULTRON app, or run python -m core.workspace.devserver and use the link it prints." }))));
        return;
      }
      W.transport.on(onEvent);
      W.bus.on("transport.error", () => W.toast("Connection to ULTRON interrupted — retrying…", "error"));
      W.api("init").then((r) => {
        if (!r.ok) { W.toast(r.error || "Init failed", "error"); return; }
        S.settings = r.settings; S.conversations = r.conversations; S.saved = r.saved; S.activity = r.activity;
        S.status = r.status; S.today = r.today;
        applySettings(); renderStatus(); renderRecents(); renderThread(); refreshMcpSummary();
        if (r.navigate) {
          if (r.navigate.conversation_id) openConversation(r.navigate.conversation_id);
          else navigate(r.navigate.view || "chat", { section: r.navigate.section });
        }
      });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
