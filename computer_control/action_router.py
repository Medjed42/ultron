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
        self.youtube_controller = YouTubeController(self.existing_browser)
        self.screen_analyzer = ScreenAnalyzer()

    def route(self, action: str, target: str = None, value: str = None) -> dict:
        self.state.record_action(action, target, value)
        
        if action in {"browser_observe", "get_tabs", "read_page", "switch_tab", "navigate", "go_to", "search", "new_tab", "close_tab", "reopen_tab", "next_tab", "previous_tab", "back", "forward", "reload", "press_key", "browser_click", "browser_type"}:
            mapped = {"browser_observe":"observe", "browser_click":"click", "browser_type":"type_text"}.get(action, action)
            return self.existing_browser.route(mapped, target, value)
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
        elif action in ["point_to", "find_element", "locate", "show_arrow"]:
            from core.screen_share import screen_share_manager
            return screen_share_manager.point_to(target)
        elif action == "click":
            res = self.windows_controller.click(target)
            if not res.get("success"):
                from core.screen_share import screen_share_manager
                return screen_share_manager.click(target=target)
            return res
        elif action in ["double_click", "dupli_klik"]:
            from core.screen_share import screen_share_manager
            return screen_share_manager.double_click(target=target)
        elif action in ["right_click", "desni_klik"]:
            from core.screen_share import screen_share_manager
            return screen_share_manager.right_click(target=target)
        elif action in ["press_key", "hotkey"]:
            from core.screen_share import screen_share_manager
            return screen_share_manager.press_key(value or target)
        elif action in ["drag", "prevuci"]:
            from core.screen_share import screen_share_manager
            try:
                p1 = [int(p.strip()) for p in str(target).split(",")]
                p2 = [int(p.strip()) for p in str(value).split(",")]
                return screen_share_manager.drag(p1[0], p1[1], p2[0], p2[1])
            except Exception as e:
                return {"success": False, "error": str(e)}
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
        
        # Always try to focus first based on the app name
        if self.windows_controller.focus_window(app_name):
            self.state.set_active_app(app_name)
            return {"success": True, "status": "focused existing", "app": app_name}
            
        if running:
            # Maybe the window title doesn't contain the app name perfectly, try common aliases
            aliases = {"chrome": "Google Chrome", "discord": "Discord", "spotify": "Spotify"}
            alias = aliases.get(app_name.lower())
            if alias and self.windows_controller.focus_window(alias):
                self.state.set_active_app(app_name)
                return {"success": True, "status": "focused existing via alias", "app": app_name}
            
            return {"success": False, "status": "running but could not find window", "app": app_name}
        else:
            success = self.process_manager.launch_app(app_name)
            return {"success": success, "status": "launched new", "app": app_name}