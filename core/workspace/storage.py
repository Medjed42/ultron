"""Workspace persistence: conversations, saved results, activity, settings.

Plain JSON under config/workspace/ (git-ignored: it is personal data), written
atomically. Nothing here stores a credential — secrets live in the DPAPI store
of core.integrations.store and never reach this folder or the web view.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parents[2]
_DATA: Optional[Path] = None
_lock = threading.RLock()

MAX_CONVERSATIONS = 200
MAX_MESSAGES = 400
MAX_SAVED = 500
MAX_ACTIVITY = 60

DEFAULT_SETTINGS = {
    "default_guests": 1,
    "currency": "EUR",
    "home_origin": "",           # optional start point for routes ("" = ask)
    "developer_mode": False,
    "sidebar_collapsed": False,
    "context_panel": True,
    "language": "auto",          # auto | sr | en
}


def data_dir() -> Path:
    return _DATA or (_ROOT / "config" / "workspace")


def set_data_dir(path: Optional[Path]) -> None:
    """Tests point storage at a temporary folder."""
    global _DATA
    _DATA = Path(path) if path else None


def _read(name: str, default):
    p = data_dir() / name
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        # A damaged file is kept for inspection, never silently overwritten.
        try:
            p.replace(p.with_suffix(f".corrupt-{int(time.time())}"))
        except OSError:
            pass
        return default


def _write(name: str, value) -> None:
    p = data_dir() / name
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:12]


_ID = re.compile(r"^[a-z]{0,4}[0-9a-f]{12}$")


def valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID.match(value))


# ── settings ─────────────────────────────────────────────────────────────────

def settings() -> dict:
    with _lock:
        merged = dict(DEFAULT_SETTINGS)
        merged.update({k: v for k, v in (_read("settings.json", {}) or {}).items()
                       if k in DEFAULT_SETTINGS})
        return merged


def save_settings(**values) -> dict:
    with _lock:
        current = settings()
        for k, v in values.items():
            if k not in DEFAULT_SETTINGS:
                continue
            default = DEFAULT_SETTINGS[k]
            if isinstance(default, bool):
                v = bool(v)
            elif isinstance(default, int):
                try:
                    v = max(1, min(16, int(v)))
                except (TypeError, ValueError):
                    continue
            elif isinstance(default, str):
                v = str(v or "")[:200]
            current[k] = v
        if current["currency"] not in ("EUR", "RSD", "USD", "GBP", "CHF"):
            current["currency"] = "EUR"
        if current["language"] not in ("auto", "sr", "en"):
            current["language"] = "auto"
        _write("settings.json", current)
        return current


# ── conversations ────────────────────────────────────────────────────────────

def _conv_file(cid: str) -> str:
    if not valid_id(cid):
        raise ValueError("Invalid conversation id")
    return f"conversations/{cid}.json"


def list_conversations() -> list[dict]:
    with _lock:
        index = _read("conversations/index.json", []) or []
        return sorted(index, key=lambda c: c.get("updated", 0), reverse=True)


def load_conversation(cid: str) -> Optional[dict]:
    with _lock:
        return _read(_conv_file(cid), None)


def create_conversation(title: str = "") -> dict:
    with _lock:
        conv = {"id": new_id("c"), "title": title or "New conversation",
                "created": time.time(), "updated": time.time(), "messages": [],
                "context": {}}
        _store_conversation(conv)
        return conv


def _store_conversation(conv: dict) -> None:
    conv["messages"] = conv.get("messages", [])[-MAX_MESSAGES:]
    _write(_conv_file(conv["id"]), conv)
    index = [c for c in (_read("conversations/index.json", []) or []) if c.get("id") != conv["id"]]
    last = next((m for m in reversed(conv["messages"]) if m.get("role") == "user"), None)
    index.append({"id": conv["id"], "title": conv.get("title", ""), "updated": conv["updated"],
                  "created": conv.get("created", conv["updated"]),
                  "preview": (last or {}).get("text", "")[:140],
                  "count": len(conv["messages"])})
    index.sort(key=lambda c: c.get("updated", 0), reverse=True)
    for stale in index[MAX_CONVERSATIONS:]:
        try:
            (data_dir() / _conv_file(stale["id"])).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
    _write("conversations/index.json", index[:MAX_CONVERSATIONS])


def append_message(cid: str, message: dict, context: Optional[dict] = None) -> dict:
    with _lock:
        conv = load_conversation(cid) or {"id": cid, "title": "", "created": time.time(),
                                          "messages": [], "context": {}}
        conv["messages"].append(message)
        conv["updated"] = time.time()
        if context is not None:
            conv["context"] = context
        if (not conv.get("title") or conv["title"] == "New conversation") and message.get("role") == "user":
            conv["title"] = _title_from(message.get("text", ""))
        _store_conversation(conv)
        return conv


def update_message(cid: str, message_id: str, **fields) -> Optional[dict]:
    with _lock:
        conv = load_conversation(cid)
        if not conv:
            return None
        for m in conv["messages"]:
            if m.get("id") == message_id:
                m.update(fields)
                conv["updated"] = time.time()
                _store_conversation(conv)
                return m
        return None


def save_context(cid: str, context: dict) -> None:
    with _lock:
        conv = load_conversation(cid)
        if conv:
            conv["context"] = context
            _store_conversation(conv)


def delete_conversation(cid: str) -> bool:
    with _lock:
        path = data_dir() / _conv_file(cid)
        existed = path.exists()
        path.unlink(missing_ok=True)
        index = [c for c in (_read("conversations/index.json", []) or []) if c.get("id") != cid]
        _write("conversations/index.json", index)
        return existed


def _title_from(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return (text[:56] + "…") if len(text) > 57 else (text or "New conversation")


# ── saved results ────────────────────────────────────────────────────────────

SAVED_KINDS = ("route", "property", "place", "research", "note")


def list_saved() -> list[dict]:
    with _lock:
        return sorted(_read("saved.json", []) or [], key=lambda s: s.get("saved_at", 0), reverse=True)


def add_saved(kind: str, title: str, data: dict, source: str = "") -> dict:
    if kind not in SAVED_KINDS:
        raise ValueError("Unsupported saved-result kind")
    with _lock:
        items = _read("saved.json", []) or []
        fingerprint = json.dumps([kind, data.get("url") or data.get("id") or title], sort_keys=True)
        items = [i for i in items if i.get("fingerprint") != fingerprint]
        item = {"id": new_id("s"), "kind": kind, "title": str(title)[:200], "data": data,
                "source": str(source)[:120], "saved_at": time.time(), "fingerprint": fingerprint}
        items.append(item)
        _write("saved.json", items[-MAX_SAVED:])
        return item


def remove_saved(item_id: str) -> bool:
    with _lock:
        items = _read("saved.json", []) or []
        kept = [i for i in items if i.get("id") != item_id]
        _write("saved.json", kept)
        return len(kept) != len(items)


# ── recent activity ──────────────────────────────────────────────────────────

def list_activity() -> list[dict]:
    with _lock:
        return list(reversed(_read("activity.json", []) or []))


def record_activity(kind: str, title: str, detail: str = "") -> None:
    """A short, privacy-conscious line: what kind of thing, never message bodies."""
    with _lock:
        items = _read("activity.json", []) or []
        items = [i for i in items if not (i.get("kind") == kind and i.get("title") == title)]
        items.append({"kind": kind, "title": str(title)[:120], "detail": str(detail)[:160],
                      "at": time.time()})
        _write("activity.json", items[-MAX_ACTIVITY:])
