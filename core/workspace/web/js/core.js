/* ULTRON Workspace — runtime: DOM builder, formatting, transport, store.
   Rule: no HTML strings. Every node is created with DOM APIs and every piece
   of text goes through textContent, so provider or model output can never
   become markup or script. */
(function () {
  "use strict";
  const W = (window.W = window.W || {});

  // ── DOM ─────────────────────────────────────────────────────────────────
  W.el = function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === undefined || v === null || v === false) continue;
        if (k === "class") node.className = v;
        else if (k === "text") node.textContent = String(v);
        else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
        else if (k === "dataset") Object.assign(node.dataset, v);
        else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
        else if (k === "href" || k === "src") {
          const safe = W.safeUrl(v, k === "src");
          if (safe) node.setAttribute(k, safe);
        } else if (v === true) node.setAttribute(k, "");
        else node.setAttribute(k, String(v));
      }
    }
    for (const c of children.flat(Infinity)) {
      if (c === null || c === undefined || c === false) continue;
      node.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  };
  W.clear = (n) => { while (n && n.firstChild) n.removeChild(n.firstChild); return n; };
  W.$ = (sel, root) => (root || document).querySelector(sel);
  W.$$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  W.safeUrl = function (u, isImage) {
    try {
      const s = String(u || "").trim();
      if (!s) return "";
      if (isImage && s.startsWith("data:image/")) return s;
      const url = new URL(s, location.href);
      if (url.protocol === "https:" && !url.username && !url.password) return url.href;
      if (url.protocol === location.protocol && url.origin === location.origin) return url.href;
    } catch (e) { /* invalid */ }
    return "";
  };

  W.button = function (label, opts) {
    opts = opts || {};
    const b = W.el("button", {
      type: "button", class: "btn " + (opts.kind ? "btn-" + opts.kind : "") + (opts.small ? " btn-sm" : "") + (opts.class ? " " + opts.class : ""),
      title: opts.title, "aria-label": opts.aria || (opts.iconOnly ? label : undefined), disabled: opts.disabled,
      "aria-pressed": opts.pressed === undefined ? undefined : String(!!opts.pressed),
      onclick: opts.onClick,
    }, opts.icon ? W.icon(opts.icon, opts.iconSize || 16) : null, opts.iconOnly ? null : W.el("span", { text: label }));
    return b;
  };

  // ── formatting ──────────────────────────────────────────────────────────
  const lang = () => (W.state && W.state.lang) || "en";
  const L = (l) => l || lang();
  W.fmt = {
    km(m, l) {
      if (m === null || m === undefined || isNaN(m)) return "—";
      if (m < 1000) return Math.round(m) + " m";
      const v = (m / 1000).toFixed(1);
      return (L(l) === "sr" ? v.replace(".", ",") : v) + " km";
    },
    dur(s) {
      if (s === null || s === undefined || isNaN(s)) return "—";
      const mins = Math.round(s / 60);
      if (mins < 60) return mins + " min";
      const h = Math.floor(mins / 60), m = mins % 60;
      if (h >= 24) return Math.floor(h / 24) + " d " + (h % 24) + " h";
      return m ? `${h} h ${m} min` : `${h} h`;
    },
    money(a, cur, l) {
      if (a === null || a === undefined || isNaN(a)) return "—";
      const whole = Math.abs(a - Math.round(a)) < 0.005;
      const loc = L(l) === "sr" ? "sr-Latn-RS" : "en-GB";
      try {
        return new Intl.NumberFormat(loc, { style: "currency", currency: cur || "EUR",
          minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: whole ? 0 : 2 }).format(a);
      } catch (e) { return a.toFixed(whole ? 0 : 2) + " " + (cur || ""); }
    },
    rel(ts) {
      if (!ts) return "never";
      const d = (Date.now() / 1000 - ts);
      if (d < 45) return "just now";
      if (d < 3600) return Math.round(d / 60) + " min ago";
      if (d < 86400) return Math.round(d / 3600) + " h ago";
      return Math.round(d / 86400) + " d ago";
    },
    day(iso, l) {
      try {
        const d = new Date(iso + "T12:00:00");
        return d.toLocaleDateString(L(l) === "sr" ? "sr-Latn-RS" : "en-GB", { day: "numeric", month: "short", weekday: "short" });
      } catch (e) { return iso; }
    },
    time(iso, l) {
      try {
        const d = new Date(iso);
        return d.toLocaleString(L(l) === "sr" ? "sr-Latn-RS" : "en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
      } catch (e) { return iso; }
    },
    bytes(n) {
      if (n === null || n === undefined) return "";
      const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; let v = Number(n);
      while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
      return v.toFixed(v < 10 && i ? 1 : 0) + " " + u[i];
    },
  };

  // ── markdown-lite → DOM (paragraphs, lists, **bold**, `code`, ``` blocks) ─
  function inline(text) {
    const frag = document.createDocumentFragment();
    const rx = /(\*\*[^*]+\*\*|`[^`]+`)/g;
    let last = 0, m;
    while ((m = rx.exec(text))) {
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      const tok = m[0];
      if (tok.startsWith("**")) frag.appendChild(W.el("strong", { text: tok.slice(2, -2) }));
      else frag.appendChild(W.el("code", { text: tok.slice(1, -1) }));
      last = m.index + tok.length;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    return frag;
  }
  W.md = function (src) {
    const root = W.el("div", { class: "md" });
    const lines = String(src || "").replace(/\r/g, "").split("\n");
    let i = 0, para = [], list = null;
    const flushPara = () => { if (para.length) { root.appendChild(W.el("p", null, inline(para.join(" ")))); para = []; } };
    const flushList = () => { if (list) { root.appendChild(list); list = null; } };
    while (i < lines.length) {
      const line = lines[i];
      if (/^```/.test(line)) {
        flushPara(); flushList();
        const langTag = line.slice(3).trim();
        const buf = []; i++;
        while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i++; }
        i++;
        const code = W.el("code", { text: buf.join("\n") });
        const pre = W.el("pre", { class: "codeblock" }, code);
        const copy = W.button("Copy", { small: true, icon: "copy", kind: "ghost", class: "code-copy",
          onClick: () => W.copy(buf.join("\n")) });
        root.appendChild(W.el("div", { class: "code-wrap" }, langTag ? W.el("span", { class: "code-lang", text: langTag }) : null, copy, pre));
        continue;
      }
      const bullet = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
      if (bullet) {
        flushPara();
        const ordered = /^\s*\d/.test(line);
        if (!list || (ordered && list.tagName !== "OL") || (!ordered && list.tagName !== "UL")) {
          flushList(); list = W.el(ordered ? "ol" : "ul");
        }
        list.appendChild(W.el("li", null, inline(bullet[1])));
      } else if (!line.trim()) { flushPara(); flushList(); }
      else if (/^#{1,4}\s/.test(line)) { flushPara(); flushList(); root.appendChild(W.el("h4", null, inline(line.replace(/^#+\s/, "")))); }
      else { flushList(); para.push(line.trim()); }
      i++;
    }
    flushPara(); flushList();
    return root;
  };

  // ── transport: QWebChannel (desktop) or HTTP+SSE (dev browser) ──────────
  const pending = new Map();
  const handlers = new Set();
  let rid = 0, mode = "none", bridge = null, token = "";

  function dispatch(ev) {
    if (ev && ev.type === "response") {
      const p = pending.get(ev.rid);
      if (p) { pending.delete(ev.rid); clearTimeout(p.timer); p.resolve(ev.result || { ok: false }); }
      return;
    }
    handlers.forEach((h) => { try { h(ev); } catch (e) { console.error(e); } });
  }

  W.transport = {
    get mode() { return mode; },
    on(fn) { handlers.add(fn); },
    init() {
      return new Promise((resolve) => {
        if (window.qt && window.qt.webChannelTransport && window.QWebChannel) {
          new window.QWebChannel(window.qt.webChannelTransport, (channel) => {
            bridge = channel.objects.ultron;
            bridge.event.connect((s) => { try { dispatch(JSON.parse(s)); } catch (e) { console.error(e); } });
            mode = "qt"; resolve(mode);
          });
          return;
        }
        const m = location.hash.match(/token=([\w-]+)/);
        if (location.protocol.startsWith("http") && m) {
          token = m[1];
          try { sessionStorage.setItem("ultron-token", token); } catch (e) { /* private */ }
        } else {
          try { token = sessionStorage.getItem("ultron-token") || ""; } catch (e) { token = ""; }
        }
        if (location.protocol.startsWith("http") && token) {
          const es = new EventSource("/events?token=" + encodeURIComponent(token));
          es.onmessage = (e) => { try { dispatch(JSON.parse(e.data)); } catch (err) { console.error(err); } };
          es.onerror = () => W.bus && W.bus.emit("transport.error");
          mode = "http"; resolve(mode); return;
        }
        mode = "none"; resolve(mode);
      });
    },
    request(type, payload, timeoutMs) {
      const req = Object.assign({ type }, payload || {});
      if (mode === "http") {
        return fetch("/api", { method: "POST", headers: { "Content-Type": "application/json", "X-Ultron-Token": token },
          body: JSON.stringify(req) }).then((r) => r.json()).catch((e) => ({ ok: false, error: String(e) }));
      }
      if (mode !== "qt") return Promise.resolve({ ok: false, error: "ULTRON is not connected" });
      return new Promise((resolve) => {
        const id = "r" + (++rid);
        req._rid = id;
        const timer = setTimeout(() => { pending.delete(id); resolve({ ok: false, error: "Timed out" }); }, timeoutMs || 120000);
        pending.set(id, { resolve, timer });
        bridge.request(JSON.stringify(req), () => {});
      });
    },
  };
  W.api = (type, payload, t) => W.transport.request(type, payload, t);

  // ── tiny event bus ──────────────────────────────────────────────────────
  const subs = {};
  W.bus = {
    on(name, fn) { (subs[name] = subs[name] || []).push(fn); },
    emit(name, data) { (subs[name] || []).forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } }); },
  };

  // ── toasts & clipboard ──────────────────────────────────────────────────
  W.toast = function (text, tone) {
    const host = W.$("#toasts");
    if (!host) return;
    const t = W.el("div", { class: "toast toast-" + (tone || "info"), role: "status" },
      W.icon(tone === "error" ? "alertCircle" : tone === "success" ? "checkCircle" : "info", 16), W.el("span", { text }));
    host.appendChild(t);
    setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 300); }, 3600);
  };
  W.copy = function (text) {
    const done = () => W.toast(W.t("Copied", "Kopirano"), "success");
    if (W.transport.mode === "qt") { W.api("copy", { text: String(text) }).then(done); return; }
    if (navigator.clipboard) navigator.clipboard.writeText(String(text)).then(done, () => W.toast("Copy failed", "error"));
  };
  W.open = function (url) {
    const u = W.safeUrl(url);
    if (!u) { W.toast(W.t("Only secure (https) links can be opened.", "Mogu da se otvore samo bezbedni (https) linkovi."), "error"); return; }
    if (W.transport.mode === "qt") W.api("open.url", { url: u }).then((r) => { if (!r.ok) W.toast(r.error || "Failed", "error"); });
    else window.open(u, "_blank", "noopener,noreferrer");
  };
  W.t = (en, sr) => (lang() === "sr" ? (sr || en) : en);
  W.reducedMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
})();
