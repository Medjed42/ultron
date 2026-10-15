import json
import threading
from core.security import redact
_lock = threading.RLock()
from .action_router import ActionRouter

_router = ActionRouter()

def handler(parameters: dict, **kwargs) -> str:
    action = parameters.get("action", "").lower()
    target = parameters.get("target")
    value = parameters.get("value")
    
    import pythoncom
    pythoncom.CoInitialize()
    try:
        with _lock:
            result = _router.route(action, target, value)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": redact(e), "recoverable": True})
    finally:
        pythoncom.CoUninitialize()

TOOL = {
    "name": "computer_control",
    "description": (
        "Core tool for controlling the user's computer, screen, windows, "
        "and EXISTING browser. Use this to open apps, control existing Chrome/YouTube, "
        "read screen contents, switch tabs, type, click, and interact with the OS. "
        "Use existing Chrome by default. Observe, act, verify; never claim a dispatched key is verified. "
        "Managed Playwright sessions require explicit user request."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "point_to (locates element and shows animated pointer arrow on screen), click, double_click, right_click, drag, observe, read_screen, focus_app, open_app, list_windows, browser_observe, get_tabs, switch_tab, navigate, search, new_tab, close_tab, reopen_tab, next_tab, previous_tab, back, forward, reload, browser_click, browser_type, press_key, scroll_up, scroll_down, youtube_search, youtube_play, youtube_pause, youtube_toggle, youtube_fullscreen, youtube_exit_fullscreen, youtube_next, youtube_prev, youtube_mute, youtube_unmute, youtube_volume, youtube_captions, youtube_seek_forward, youtube_seek_back, youtube_quality, youtube_speed, youtube_select_video"
            },
            "target": {
                "type": "STRING",
                "description": "The target app name, element name, or URL. Optional."
            },
            "value": {
                "type": "STRING",
                "description": "Additional value, such as text to type. Optional."
            }
        },
        "required": ["action"]
    },
    "handler": handler
}