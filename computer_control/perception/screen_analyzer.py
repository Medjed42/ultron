"""Bounded accessibility observations, compatible with future visual backends."""
class ScreenAnalyzer:
    def _controls(self):
        import win32gui
        from pywinauto import Desktop
        from ..browser.existing_browser_controller import ExistingBrowserController
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd: raise RuntimeError("No foreground window")
        root = Desktop(backend="uia").window(handle=hwnd).wrapper_object()
        return ExistingBrowserController.controls(root)

    def get_visible_text(self):
        return list(dict.fromkeys(c.window_text() for c in self._controls() if c.window_text()))[:250]

    def get_interactive_elements(self):
        result=[]
        for c in self._controls():
            try:
                if c.is_enabled() and c.element_info.control_type in {"Button","ListItem","MenuItem","TabItem","Hyperlink","Edit","Slider"}:
                    rect=c.rectangle()
                    result.append({"type":c.element_info.control_type,"text":c.window_text(),"bounds":[rect.left,rect.top,rect.right,rect.bottom]})
            except Exception: pass
        return result[:180]
