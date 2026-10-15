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

    def launch_app(self, name):
        import shutil, os
        from pathlib import Path
        name = str(name or "").strip()
        # Pass only a resolved executable as argv; never interpolate shell commands.
        aliases = {"chrome":"chrome.exe", "notepad":"notepad.exe", "calculator":"calc.exe", "edge":"msedge.exe"}
        executable = shutil.which(aliases.get(name.lower(), name))
        if not executable and name.lower() in {"chrome", "chrome.exe"}:
            for base in (os.getenv("PROGRAMFILES",""),os.getenv("PROGRAMFILES(X86)",""),os.getenv("LOCALAPPDATA","")):
                candidate = Path(base)/"Google/Chrome/Application/chrome.exe"
                if candidate.is_file(): executable=str(candidate); break
        if not executable: return False
        try:
            subprocess.Popen([executable],creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            return True
        except OSError: return False

    def close_app(self, name):
        import win32gui, win32con, win32process
        app = self.find_running_app(name)
        if not app: return {"success":False,"error":"App not found"}
        windows=[]
        def visit(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1] == app["pid"]:
                windows.append(hwnd)
        win32gui.EnumWindows(visit,None)
        for hwnd in windows: win32gui.PostMessage(hwnd,win32con.WM_CLOSE,0,0)
        return {"success":bool(windows),"verified":False,"status":"close requested; save dialogs may remain"}

    def get_active_app_name(self, window_title: str) -> str:
        if not window_title: return "Unknown"
        if "Google Chrome" in window_title: return "Chrome"
        if "Discord" in window_title: return "Discord"
        return window_title.split("-")[-1].strip()