import platform
import time
try:
    import pyautogui
except ImportError:
    pass

import pywinauto

class WindowsController:
    def __init__(self):
        self.os = platform.system()

    def get_active_window(self) -> str:
        if self.os == "Windows":
            try:
                import win32gui
                hwnd = win32gui.GetForegroundWindow()
                if hwnd:
                    return win32gui.GetWindowText(hwnd)
            except Exception:
                pass
        return "Unknown"

    def focus_handle(self, hwnd):
        import win32gui, win32con
        try:
            # Refocusing an already active Chrome root can blur its address bar
            # between Ctrl+L, typing and Enter. Preserve the focused child.
            if win32gui.GetForegroundWindow() == hwnd:
                return True
            if win32gui.IsIconic(hwnd): win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            from pywinauto import Desktop
            Desktop(backend="win32").window(handle=hwnd).set_focus()
            time.sleep(0.15)
            return win32gui.GetForegroundWindow() == hwnd
        except Exception:
            return False

    def focus_window(self, title_substr):
        import win32gui
        if not title_substr: return False
        hwnds = []
        def visit(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and title_substr.casefold() in win32gui.GetWindowText(hwnd).casefold():
                hwnds.append(hwnd)
        win32gui.EnumWindows(visit, None)
        return bool(hwnds and self.focus_handle(hwnds[0]))

    def list_windows(self) -> list:
        if self.os == "Windows":
            try:
                import win32gui
                titles = []
                def enum_cb(hwnd, results):
                    if win32gui.IsWindowVisible(hwnd):
                        t = win32gui.GetWindowText(hwnd)
                        if t: results.append(t)
                win32gui.EnumWindows(enum_cb, titles)
                return titles
            except:
                pass
        return []

    def get_monitors(self):
        import win32api
        return [{"bounds": list(rect), "primary": bool(win32api.GetMonitorInfo(handle).get("Flags", 0) & 1)}
                for handle, _, rect in win32api.EnumDisplayMonitors()]

    def type_text(self, text):
        if text is None: return {"success":False,"error":"Text is required"}
        try:
            # VK_PACKET/Unicode injection treats braces, %, ^ and + as literal text.
            from pywinauto.keyboard import send_keys
            escaped = "".join({"{":"{{}","}":"{}}","+":"{+}","^":"{^}","%":"{%}","~":"{~}","(":"{(}",")":"{)}"}.get(c,c) for c in str(text))
            send_keys(escaped, with_spaces=True, with_tabs=True, with_newlines=True, vk_packet=True)
            return {"success":True,"verified":False,"status":"text dispatched"}
        except Exception as e:
            return {"success":False,"error":str(e)}

    def scroll(self, direction):
        try:
            pyautogui.scroll(-5 if direction == "down" else 5)
            return {"success":True,"verified":False}
        except Exception as e: return {"success":False,"error":str(e)}

    def click(self, target):
        from ..browser.existing_browser_controller import ExistingBrowserController
        import win32gui
        from pywinauto import Desktop
        root = Desktop(backend="uia").window(handle=win32gui.GetForegroundWindow()).wrapper_object()
        matches = [c for c in ExistingBrowserController.controls(root)
                   if c.window_text().strip().casefold() == str(target).strip().casefold() and c.is_enabled()]
        if len(matches) != 1: return {"success":False,"error":"Target missing or ambiguous; observe again"}
        matches[0].click_input()
        return {"success":True,"verified":False,"status":"clicked observed element"}
