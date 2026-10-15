"""The workspace service: one orchestrator, many views.

The Qt window, the developer browser transport and the voice tool all talk to
the same Orchestrator through `handle(request)`; its events fan out to every
subscribed view. All requests are plain JSON-able dicts, validated here —
the interface never reaches Python objects directly.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Callable, Optional

from core.workspace import storage
from core.workspace.components import url as safe_url

Listener = Callable[[dict], None]

_lock = threading.RLock()
_listeners: list[Listener] = []
_orch = None
_native: dict[str, Callable] = {}     # host-provided callables (open url, clipboard, dialogs)
_pending_nav: dict = {}               # a navigation asked for before the page finished loading


def subscribe(fn: Listener) -> None:
    with _lock:
        if fn not in _listeners:
            _listeners.append(fn)


def unsubscribe(fn: Listener) -> None:
    with _lock:
        if fn in _listeners:
            _listeners.remove(fn)


def publish(event: dict) -> None:
    with _lock:
        targets = list(_listeners)
    for fn in targets:
        try:
            fn(event)
        except Exception:
            pass


def navigate(view: str = "chat", conversation_id: str = "", section: str = "") -> None:
    """Ask every open view to show something; remembered for a page still loading."""
    nav = {"view": view, "conversation_id": conversation_id, "section": section}
    with _lock:
        _pending_nav.clear()
        _pending_nav.update(nav)
    publish({"type": "navigate", **nav})


def set_native(name: str, fn: Optional[Callable]) -> None:
    """The host (Qt window) provides UI-only abilities: open_url, copy,
    credentials, open_config, present."""
    if fn is None:
        _native.pop(name, None)
    else:
        _native[name] = fn


def orchestrator():
    global _orch
    with _lock:
        if _orch is None:
            from core.workspace.orchestrator import Orchestrator
            _orch = Orchestrator(publish)
            _wire_status()
        return _orch


def _wire_status() -> None:
    from core import connections as conn
    from core.workspace.mcp_registry import registry
    conn.manager().subscribe(lambda snap: publish({"type": "status.updated", "status": _status(snap)}))
    registry().subscribe(lambda: publish({"type": "mcp.updated"}))
    registry().start()
    # Cheap checks (sign-in state, API key, `claude mcp list`) so the first
    # screen shows real states instead of "checking"; no paid request.
    conn.manager().check_all(deep=False)


def _status(snap: Optional[dict] = None) -> dict:
    from core import claude_brain, connections as conn
    snap = snap or conn.manager().snapshot()
    cached = claude_brain.load_cached_status()
    return {
        "claude": {k: snap["claude"].get(k) for k in ("state", "model", "integration", "last_ok", "last_error", "detail", "usable")},
        "gemini": {k: snap["gemini"].get(k) for k in ("state", "model", "last_ok", "last_error", "detail", "usable")},
        "gemini_live": {"connected": snap["gemini_live"].get("connected"), "model": snap["gemini_live"].get("model")},
        "active_provider": snap.get("active_provider"),
        "routing": snap.get("routing"),
        "mcp_summary": snap.get("mcp_summary"),
        "claude_limit": {"limit_hit": bool(cached.get("limit_hit")), "reset": cached.get("reset_str"),
                         "remaining": cached.get("time_remaining_str")},
        "task": snap.get("task"),
    }


# ── request handling ────────────────────────────────────────────────────────

def handle(req: Any) -> dict:
    """One request from a view. Never raises; errors come back as {ok: False}."""
    if isinstance(req, str):
        try:
            req = json.loads(req)
        except ValueError:
            return {"ok": False, "error": "Malformed request"}
    if not isinstance(req, dict) or not isinstance(req.get("type"), str):
        return {"ok": False, "error": "Malformed request"}
    kind = req["type"]
    fn = _HANDLERS.get(kind)
    if fn is None:
        return {"ok": False, "error": f"Unknown request {kind}"}
    try:
        out = fn(req)
        return out if isinstance(out, dict) else {"ok": True, "value": out}
    except Exception as exc:
        from core.security import redact
        return {"ok": False, "error": redact(str(exc))[:300]}


def _init(req):
    from core.workspace.dates import today
    with _lock:
        nav = dict(_pending_nav)
        _pending_nav.clear()
    return {"ok": True, "navigate": nav or None, "settings": storage.settings(), "conversations": storage.list_conversations()[:60],
            "saved": storage.list_saved()[:200], "activity": storage.list_activity()[:12],
            "status": _status(), "today": today().isoformat(), "version": "2.0.0",
            "native": sorted(_native)}


def _chat_send(req):
    text = str(req.get("text") or "")
    if not text.strip():
        return {"ok": False, "error": "Empty message"}
    if text.startswith("__view:"):
        return {"ok": False, "error": "View commands are handled by the interface"}
    return orchestrator().submit(req.get("conversation_id"), text)


def _cancel(req):
    return {"ok": orchestrator().cancel(str(req.get("task_id") or ""))}


def _component_action(req):
    return orchestrator().component_action(str(req.get("conversation_id") or ""), str(req.get("message_id") or ""),
                                           str(req.get("component_id") or ""), str(req.get("action") or ""),
                                           req.get("args") if isinstance(req.get("args"), dict) else {})


def _conv_load(req):
    cid = str(req.get("id") or "")
    if not storage.valid_id(cid):
        return {"ok": False, "error": "Invalid id"}
    conv = storage.load_conversation(cid)
    if not conv:
        return {"ok": False, "error": "Not found"}
    conv = dict(conv)
    conv.pop("context", None)            # follow-up state stays on the Python side
    return {"ok": True, "conversation": conv}


def _conv_delete(req):
    cid = str(req.get("id") or "")
    ok = storage.valid_id(cid) and storage.delete_conversation(cid)
    publish({"type": "conversations.updated"})
    return {"ok": bool(ok)}


def _conv_rename(req):
    cid = str(req.get("id") or "")
    title = re.sub(r"\s+", " ", str(req.get("title") or "")).strip()[:80]
    conv = storage.load_conversation(cid) if storage.valid_id(cid) else None
    if not conv or not title:
        return {"ok": False}
    conv["title"] = title
    storage._store_conversation(conv)
    publish({"type": "conversations.updated"})
    return {"ok": True}


def _search(req):
    """Local workspace search: conversation titles and message text, saved
    results. External web search is a separate, explicit action."""
    q = str(req.get("query") or "").strip()
    if len(q) < 2:
        return {"ok": True, "conversations": [], "saved": []}
    from core.workspace.text import fold
    fq = fold(q)
    convs = []
    for c in storage.list_conversations()[:200]:
        hit = None
        if fq in fold(c.get("title", "")) or fq in fold(c.get("preview", "")):
            hit = c.get("preview") or c.get("title")
        else:
            full = storage.load_conversation(c["id"]) or {}
            for m in full.get("messages", []):
                if fq in fold(m.get("text", "")):
                    hit = m["text"]
                    break
        if hit:
            i = fold(hit).find(fq)
            snippet = hit[max(0, i - 50): i + 90] if i >= 0 else hit[:140]
            convs.append({"id": c["id"], "title": c.get("title", ""), "snippet": snippet, "updated": c.get("updated")})
        if len(convs) >= 20:
            break
    saved = [s for s in storage.list_saved() if fq in fold(s.get("title", "") + " " + s.get("source", ""))][:20]
    return {"ok": True, "conversations": convs, "saved": saved}


def _saved_add(req):
    kind = str(req.get("kind") or "")
    data = req.get("data") if isinstance(req.get("data"), dict) else {}
    # Only sanitised, size-bounded data is persisted.
    blob = json.dumps(data, ensure_ascii=False)
    if len(blob) > 200_000:
        return {"ok": False, "error": "Too large to save"}
    item = storage.add_saved(kind, str(req.get("title") or "")[:200], data, str(req.get("source") or "")[:120])
    publish({"type": "saved.updated"})
    return {"ok": True, "item": item}


def _saved_remove(req):
    ok = storage.remove_saved(str(req.get("id") or ""))
    publish({"type": "saved.updated"})
    return {"ok": ok}


def _mcp_list(req):
    from core.workspace.mcp_registry import registry
    if req.get("refresh"):
        from core import connections as conn
        conn.manager().check_async("mcp")
        conn.manager().check_async("claude")
    return {"ok": True, "servers": registry().servers(), "checked_at": time.time()}


def _mcp_test(req):
    from core.workspace.mcp_registry import registry
    return registry().test(str(req.get("id") or ""))


def _mcp_enable(req):
    from core.workspace.mcp_registry import registry
    return registry().set_enabled(str(req.get("id") or ""), bool(req.get("enabled")))


def _settings_set(req):
    values = req.get("values") if isinstance(req.get("values"), dict) else {}
    s = storage.save_settings(**values)
    publish({"type": "settings.updated", "settings": s})
    return {"ok": True, "settings": s}


def _routing_get(req):
    from core import claude_brain, connections as conn
    st = claude_brain.load_cached_status()
    return {"ok": True, "routing": conn.routing_settings(), "status": _status(),
            "preferences": st.get("connection_preferences", {}),
            "permissions": _permissions()}


def _permissions() -> dict:
    from core.integrations import store
    prefs = store.preferences()
    return {"chrome": prefs.get("permissions", {}).get("chrome", True),
            "system_alerts": bool(prefs.get("system_alerts_enabled", False))}


def _routing_set(req):
    from core import connections as conn
    values = {}
    order = req.get("provider_order")
    if isinstance(order, list) and order and all(p in ("claude", "gemini") for p in order):
        values["provider_order"] = list(dict.fromkeys(order))
    if "allow_provider_fallback" in req:
        values["allow_provider_fallback"] = bool(req["allow_provider_fallback"])
    if "claude_model" in req and str(req["claude_model"]) in ("", "opus", "sonnet", "haiku", "fable"):
        values["claude_model"] = str(req["claude_model"])
    routing = conn.save_routing_settings(**values)
    publish({"type": "status.updated", "status": _status()})
    return {"ok": True, "routing": routing}


def _prefs_set(req):
    from core import claude_brain
    for service in ("gmail", "drive"):
        v = req.get(service)
        if v in ("ask", "claude", "user"):
            claude_brain.set_connection_preference(service, v)
    return {"ok": True}


def _permission_set(req):
    from core.integrations import store
    name, enabled = str(req.get("name") or ""), bool(req.get("enabled"))
    if name == "chrome":
        store.set_permission("chrome", enabled)
    elif name == "system_alerts":
        store.save_preferences(system_alerts_enabled=enabled)
    else:
        return {"ok": False, "error": "Unknown permission"}
    return {"ok": True, "permissions": _permissions()}


def _diag(req):
    """Advanced diagnostics. Each runs off the UI thread and reports back."""
    which = str(req.get("which") or "")
    task_id = storage.new_id("d")

    def run():
        from core import claude_brain, connections as conn
        out: dict[str, Any] = {"which": which}
        try:
            if which == "claude_limit":
                st = claude_brain.get_status(force_probe=True)
                out.update(ok=bool(st.get("verified")), text=(
                    f"Claude: {'limit reached — resets ' + str(st.get('reset_str')) if st.get('limit_hit') else ('verified' if st.get('verified') else 'not verified')}"
                    f" · {st.get('last_error') or ''}").strip(" ·"))
            elif which == "claude_test":
                r = claude_brain.ask_claude("Reply with exactly: ULTRON workspace link OK", timeout=60)
                out.update(ok=r["success"], text=r.get("output") or r.get("error") or "")
            elif which == "gemini_test":
                r = claude_brain.ask_antigravity("Reply with exactly: ULTRON Gemini fallback OK", timeout=60)
                out.update(ok=r["success"], text=r.get("output") or r.get("error") or "")
            elif which == "recheck":
                conn.manager().check_all(deep=True)
                out.update(ok=True, text="Checks started for Claude, Gemini and MCP; statuses update as they finish.")
            elif which == "errors":
                errs = conn.manager().errors()
                out.update(ok=True, text="\n".join(f"{e['service']} [{e['state']}]: {e['error']}" for e in errs)
                           or "No connection errors recorded.")
            elif which == "brain":
                prompt = str(req.get("prompt") or "").strip()[:4000]
                if not prompt:
                    out.update(ok=False, text="Enter a prompt first.")
                else:
                    r = claude_brain.execute(prompt, timeout=120)
                    out.update(ok=r.get("success"), text=(f"[{str(r.get('brain_used', '')).upper()}] " if r.get("brain_used") else "")
                               + (r.get("output") or r.get("error") or ""))
            else:
                out.update(ok=False, text="Unknown diagnostic")
        except Exception as exc:
            out.update(ok=False, text=str(exc)[:300])
        publish({"type": "diag.result", "id": task_id, **out})

    threading.Thread(target=run, name="ws-diag", daemon=True).start()
    return {"ok": True, "id": task_id}


def _open_url(req):
    u = safe_url(req.get("url"))
    if not u:
        return {"ok": False, "error": "Only https links can be opened."}
    fn = _native.get("open_url")
    if not fn:
        return {"ok": False, "error": "Opening links is not available here."}
    fn(u)
    storage.record_activity("link", u.split("/")[2], "")
    return {"ok": True}


def _native_call(name: str):
    def go(req):
        fn = _native.get(name)
        if not fn:
            return {"ok": False, "error": f"{name} is not available in this view."}
        arg = req.get("text") if name == "copy" else None
        fn(arg) if arg is not None else fn()
        return {"ok": True}
    return go


_HANDLERS: dict[str, Callable[[dict], dict]] = {
    "init": _init,
    "chat.send": _chat_send,
    "task.cancel": _cancel,
    "component.action": _component_action,
    "conversation.list": lambda r: {"ok": True, "conversations": storage.list_conversations()[:200]},
    "conversation.load": _conv_load,
    "conversation.delete": _conv_delete,
    "conversation.rename": _conv_rename,
    "search": _search,
    "saved.list": lambda r: {"ok": True, "saved": storage.list_saved()},
    "saved.add": _saved_add,
    "saved.remove": _saved_remove,
    "activity.list": lambda r: {"ok": True, "activity": storage.list_activity()},
    "status": lambda r: {"ok": True, "status": _status()},
    "mcp.list": _mcp_list,
    "mcp.test": _mcp_test,
    "mcp.enable": _mcp_enable,
    "settings.get": lambda r: {"ok": True, "settings": storage.settings()},
    "settings.set": _settings_set,
    "routing.get": _routing_get,
    "routing.set": _routing_set,
    "prefs.set": _prefs_set,
    "permission.set": _permission_set,
    "diag.run": _diag,
    "open.url": _open_url,
    "copy": _native_call("copy"),
    "credentials.open": _native_call("credentials"),
    "config.open": _native_call("open_config"),
}


def run_voice_request(text: str, timeout: float = 75.0) -> dict:
    """Used by the voice tool: run a request in the 'Voice' conversation,
    show it in the workspace, wait for the answer text."""
    done = threading.Event()
    box: dict = {}
    orch = orchestrator()
    conv_id = _voice_conversation()

    def listen(ev):
        if ev.get("conversation_id") == conv_id and ev.get("type") in ("task.completed", "task.cancelled"):
            box.update(ev)
            done.set()
    subscribe(listen)
    try:
        res = orch.submit(conv_id, text)
        fn = _native.get("present")
        if fn:
            fn({"conversation_id": res.get("conversation_id")})
        done.wait(timeout)
    finally:
        unsubscribe(listen)
    msg = box.get("message") or {}
    return {"ok": bool(msg), "text": msg.get("text") or "Still working — the result will appear in the Workspace.",
            "conversation_id": conv_id}


def _voice_conversation() -> str:
    for c in storage.list_conversations()[:30]:
        if c.get("title") == "Voice requests":
            return c["id"]
    conv = storage.create_conversation("Voice requests")
    return conv["id"]


def devserver_token() -> str:
    return os.environ.get("ULTRON_WS_TOKEN", "")
