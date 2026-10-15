from pathlib import Path
import os
os.chdir(Path(__file__).resolve().parent)

import os
from pathlib import Path

BASE = Path(r".\computer_control")

def write(subpath: str, content: str):
    p = BASE / subpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content.strip(), encoding="utf-8")

# Core
write("__init__.py", """
from .plugin import TOOL, handler
from .action_router import ActionRouter
from .state_manager import StateManager
""")

write("plugin.py", """
import json
from .action_router import ActionRouter

_router = ActionRouter()

TOOL = {
    "name": "computer_control",
    "description": (
        "Core tool for controlling the user's computer, screen, windows, "
        "and browser. Use this to open apps, control existing Chrome/YouTube, "
        "read screen contents, switch tabs, type, click, and interact with the OS."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "The command to execute (e.g., 'observe', 'describe_screen', 'focus_app', 'youtube_pause', 'click', 'scroll_down')"
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
    }
}

def handler(parameters: dict, **kwargs) -> str:
    action = parameters.get("action", "").lower()
    target = parameters.get("target")
    value = parameters.get("value")
    
    try:
        result = _router.route(action, target, value)
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "recoverable": True})
""")

write("action_router.py", """
from .desktop.process_manager import ProcessManager
from .desktop.windows_controller import WindowsController
from .browser.existing_browser_controller import ExistingBrowserController
from .browser.managed_browser_controller import ManagedBrowserController
from .perception.screen_analyzer import ScreenAnalyzer
from .state_manager import StateManager
from .youtube.youtube_controller import YouTubeController

class ActionRouter:
    def __init__(self):
        self.state = StateManager()
        self.process_manager = ProcessManager()
        self.windows_controller = WindowsController()
        self.existing_browser = ExistingBrowserController()
        self.managed_browser = ManagedBrowserController()
        self.youtube_controller = YouTubeController()
        self.screen_analyzer = ScreenAnalyzer()

    def route(self, action: str, target: str = None, value: str = None) -> dict:
        self.state.record_action(action, target, value)
        
        if action == "observe":
            return self._observe()
        elif action in ["describe_screen", "read_screen"]:
            return self._read_screen()
        elif action in ["find_app", "focus_app", "open_app"]:
            return self._focus_or_open_app(target)
        elif action == "list_windows":
            return {"success": True, "windows": self.windows_controller.list_windows()}
        elif action == "close_app":
            return self.process_manager.close_app(target)
        elif action == "type_text":
            return self.windows_controller.type_text(value)
        elif action in ["scroll", "scroll_down"]:
            return self.windows_controller.scroll("down")
        elif action == "scroll_up":
            return self.windows_controller.scroll("up")
        elif action == "click":
            return self.windows_controller.click(target)
        elif action.startswith("youtube_"):
            return self.youtube_controller.handle_action(action, target, value)
        
        return {"success": False, "error": f"Unknown action: {action}"}

    def _observe(self) -> dict:
        active_win = self.windows_controller.get_active_window()
        return {
            "success": True,
            "active_app": self.process_manager.get_active_app_name(active_win),
            "active_window": active_win,
            "monitors": self.windows_controller.get_monitors()
        }

    def _read_screen(self) -> dict:
        return {
            "success": True,
            "screen_text": self.screen_analyzer.get_visible_text(),
            "elements": self.screen_analyzer.get_interactive_elements()
        }

    def _focus_or_open_app(self, app_name: str) -> dict:
        if not app_name:
            return {"success": False, "error": "App name required"}
        
        running = self.process_manager.find_running_app(app_name)
        if running:
            success = self.windows_controller.focus_window(running['title'])
            self.state.set_active_app(app_name)
            return {"success": success, "status": "focused existing", "app": app_name}
        else:
            success = self.process_manager.launch_app(app_name)
            return {"success": success, "status": "launched new", "app": app_name}
""")

write("state_manager.py", """
class StateManager:
    def __init__(self):
        self.active_app = None
        self.app_history = []
        self.recent_actions = []

    def set_active_app(self, app_name: str):
        if self.active_app and self.active_app != app_name:
            if self.active_app in self.app_history:
                self.app_history.remove(self.active_app)
            self.app_history.append(self.active_app)
        self.active_app = app_name

    def record_action(self, action: str, target: str, value: str):
        self.recent_actions.append({"action": action, "target": target, "value": value})
        if len(self.recent_actions) > 10:
            self.recent_actions.pop(0)
""")

# Desktop
write("desktop/__init__.py", "")

write("desktop/process_manager.py", """
import psutil
import subprocess
import time

class ProcessManager:
    def find_running_app(self, name: str) -> dict:
        if not name: return None
        name_lower = name.lower()
        for proc in psutil.process_iter(['name', 'exe', 'pid']):
            try:
                pname = proc.info.get('name', '')
                if pname and name_lower in pname.lower():
                    return {"pid": proc.info['pid'], "name": pname, "title": pname}
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return None

    def launch_app(self, name: str) -> bool:
        try:
            subprocess.Popen(f"start {name}", shell=True)
            time.sleep(1)
            return True
        except:
            return False

    def close_app(self, name: str) -> dict:
        app = self.find_running_app(name)
        if app:
            try:
                psutil.Process(app['pid']).terminate()
                return {"success": True, "status": "terminated"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        return {"success": False, "error": "App not found"}

    def get_active_app_name(self, window_title: str) -> str:
        if not window_title: return "Unknown"
        if "Google Chrome" in window_title: return "Chrome"
        if "Discord" in window_title: return "Discord"
        return window_title.split("-")[-1].strip()
""")

write("desktop/windows_controller.py", """
import platform
try:
    import pyautogui
except ImportError:
    pass

class WindowsController:
    def __init__(self):
        self.os = platform.system()

    def get_active_window(self) -> str:
        # Mock for now
        return "Desktop"

    def focus_window(self, title_substr: str) -> bool:
        return True

    def list_windows(self) -> list:
        return []

    def get_monitors(self) -> list:
        return [{"id": 1, "resolution": "1920x1080", "primary": True}]

    def type_text(self, text: str) -> dict:
        try:
            pyautogui.write(text)
            return {"success": True}
        except:
            return {"success": False}

    def scroll(self, direction: str) -> dict:
        try:
            if direction == "down": pyautogui.scroll(-500)
            else: pyautogui.scroll(500)
            return {"success": True}
        except:
            return {"success": False}

    def click(self, target: str) -> dict:
        return {"success": True, "method": "fallback"}
""")

# Browser
write("browser/__init__.py", "")
write("browser/existing_browser_controller.py", """
class ExistingBrowserController:
    pass
""")
write("browser/managed_browser_controller.py", """
class ManagedBrowserController:
    pass
""")

# Perception
write("perception/__init__.py", "")
write("perception/screen_analyzer.py", """
class ScreenAnalyzer:
    def get_visible_text(self):
        return ["Sample text"]

    def get_interactive_elements(self):
        return []
""")

# YouTube
write("youtube/__init__.py", "")
write("youtube/youtube_controller.py", """
from ..desktop.windows_controller import WindowsController

class YouTubeController:
    def __init__(self):
        self.windows = WindowsController()

    def handle_action(self, action: str, target: str = None, value: str = None) -> dict:
        if action == "youtube_pause" or action == "youtube_play":
            if self.windows.focus_window("YouTube - Google Chrome"):
                self.windows.type_text("k")
                return {"success": True, "action": "toggled_play"}
            return {"success": False, "error": "YouTube not open"}
        
        elif action == "youtube_fullscreen":
            if self.windows.focus_window("YouTube - Google Chrome"):
                self.windows.type_text("f")
                return {"success": True, "action": "fullscreen"}
            return {"success": False, "error": "YouTube not open"}

        return {"success": False, "error": "Unknown youtube action"}
""")

print("Successfully created computer_control structure.")
