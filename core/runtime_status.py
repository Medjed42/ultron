"""Thread-safe runtime facts shared by the Gemini loop and the desktop HUD."""
from threading import RLock
from time import time

_lock = RLock()
_state = {"provider": "Gemini", "model": "Waiting for connection", "connected": False,
          "tool": "Ready", "last_result": "", "updated_at": 0}


def update(**values):
    if "last_result" in values:
        from core.security import redact
        values["last_result"] = redact(values["last_result"])
    with _lock:
        _state.update(values, updated_at=time())


def snapshot():
    with _lock:
        return dict(_state)


_plugin_callback = None

def bind_plugin_refresh(callback):
    global _plugin_callback
    _plugin_callback = callback

def refresh_plugins():
    if _plugin_callback:
        _plugin_callback()
