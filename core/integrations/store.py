"""Local preferences and Windows user-bound encrypted credentials."""
import json
import os
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "config" / "integrations"
_lock = threading.RLock()


def path(name):
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise ValueError("Invalid credential name")
    return DATA / (name + ".bin")


def get_secret(name, default=None):
    with _lock:
        p = path(name)
        if not p.exists():
            return default
        if os.name != "nt":
            raise RuntimeError("This credential store requires Windows DPAPI.")
        import win32crypt
        value = json.loads(win32crypt.CryptUnprotectData(p.read_bytes(), None, None, None, 0)[1])
        from core.security import register_secrets
        register_secrets({"token": json.dumps(value), **(value if isinstance(value, dict) else {"secret": value})})
        return value


def set_secret(name, value):
    with _lock:
        if os.name != "nt":
            raise RuntimeError("Windows DPAPI is required; credentials were not saved.")
        import win32crypt
        DATA.mkdir(parents=True, exist_ok=True)
        p = path(name)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(win32crypt.CryptProtectData(json.dumps(value).encode(), "Ultron", None, None, None, 0))
        tmp.replace(p)
        from core.security import register_secrets
        register_secrets(value if isinstance(value, dict) else {"secret": value})


def delete_secret(name):
    with _lock:
        path(name).unlink(missing_ok=True)


def preferences():
    with _lock:
        p = DATA / "preferences.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_preferences(**values):
    with _lock:
        current = preferences()
        current.update(values)
        DATA.mkdir(parents=True, exist_ok=True)
        p = DATA / "preferences.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
        tmp.replace(p)


def require_permission(name):
    # Existing Chrome control was expressly authorized; it remains revocable.
    if not preferences().get("permissions", {}).get(name, name == "chrome"):
        raise PermissionError(f"{name} access is disabled. Enable it in Connections & permissions.")


def set_permission(name, enabled):
    """Called from local settings UI, never exposed as an AI tool."""
    with _lock:
        permissions = preferences().get("permissions", {})
        permissions[name] = bool(enabled)
        save_preferences(permissions=permissions)


def api_key(name):
    value = get_secret(name) or os.getenv(name.upper())
    if not value:
        raise PermissionError(f"{name.upper()} is missing. Add it in Connections & permissions.")
    return value
