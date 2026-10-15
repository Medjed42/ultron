"""Local diagnostics expose configuration presence, never secret values."""
import importlib.util
import json
from pathlib import Path

PLUGIN = {"name": "ultron_health", "description": "Inspect Ultron core, installed plugin dependencies, Chrome mode and integration configuration without exposing credentials or sending messages.",
          "version": "2.0.0", "capabilities": ["inspect"], "authentication": "None",
          "permissions": [], "strict_inputs": True,
          "parameters": {"type": "OBJECT", "properties": {}}}


def run(parameters=None, **kwargs):
    from core.runtime_status import snapshot
    from core.mail_service import token_path
    from memory.config_manager import get_gemini_key, get_assistant_name
    from core.integrations import store
    packages = ["playwright", "pywinauto", "googleapiclient", "vosk", "midea_beautiful", "pycaw",
                "caldav", "icalendar", "recurring_ical_events", "PyQt6.QtWebEngineWidgets"]
    return json.dumps({"name": get_assistant_name(), "runtime": snapshot(),
                       "Gemini": "CONFIGURED" if get_gemini_key() else "MISSING",
                       "Gmail": "CONFIGURED" if token_path().exists() else "MISSING",
                       "dependencies": {p: bool(importlib.util.find_spec(p)) for p in packages},
                       "integrations": {name: "CONFIGURED; live verification required" if store.path(name).exists() else "MISSING"
                                        for name in ("google_drive", "google_maps_api_key", "google_maps_embed_key", "icloud", "icloud_subscription")},
                       "idle_speech": "disabled", "credentials": "new integration credentials use Windows DPAPI",
                       "chrome": "existing window / Windows accessibility",
                       "gestures": "not enabled", "claude_pc_access": "not initialized"})
