"""The Workspace window: a sandboxed web view plus the bridge into Python.

Security model
    * The page is ULTRON's own local files; it may not navigate anywhere else
      or open windows. External links go through the bridge, are checked
      (https only) and open in the system browser.
    * The web profile is off-the-record and every permission request
      (location, camera, microphone, notifications…) is denied.
    * The only object the page can reach is WorkspaceBridge, which accepts
      JSON requests and hands them to core.workspace.service — never secrets,
      tokens or Python objects. Credentials are edited in a native dialog.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PyQt6.QtCore import QFile, QIODevice, QObject, Qt, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices, QGuiApplication, QIcon
from PyQt6.QtWidgets import QMainWindow

from core.workspace import service

WEB = Path(__file__).resolve().parent / "web"


class _Relay(QObject):
    """Carries events from worker threads to the GUI thread. Deliberately a
    separate object: QWebChannel listens to every signal of the object it
    publishes, so a signal on the bridge itself would reach the channel on
    the worker thread and crash it."""
    sig = pyqtSignal(str)


class WorkspaceBridge(QObject):
    """Requests arrive here from the page; results and events go back through
    `event`. Work runs on a small thread pool so the window never freezes."""

    event = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ws-bridge")
        # No Qt parent: parenting it to the bridge crashes this PyQt6 build;
        # the bridge's reference keeps it alive, on the GUI thread.
        self._relay = _Relay()
        # Queued: _deliver runs on this object's (GUI) thread, the only
        # thread QWebChannel may be used from.
        self._relay.sig.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        service.subscribe(self._forward)

    def _deliver(self, payload: str) -> None:
        self.event.emit(payload)

    def _forward(self, ev: dict) -> None:
        try:
            self._relay.sig.emit(json.dumps(ev, ensure_ascii=False, default=str))
        except RuntimeError:
            pass                                  # window already destroyed

    @pyqtSlot(str, result=str)
    def request(self, payload: str) -> str:
        try:
            req = json.loads(payload)
        except ValueError:
            return json.dumps({"ok": False, "error": "Malformed request"})
        rid = str(req.pop("_rid", ""))[:40] if isinstance(req, dict) else ""
        if not isinstance(req, dict):
            return json.dumps({"ok": False, "error": "Malformed request"})

        def work():
            result = service.handle(req)
            self._forward({"type": "response", "rid": rid, "result": result})
        self._pool.submit(work)
        return json.dumps({"ok": True, "accepted": rid})

    def close(self) -> None:
        service.unsubscribe(self._forward)
        self._pool.shutdown(wait=False, cancel_futures=True)


def _make_page(profile, parent, index_url: QUrl):
    from PyQt6.QtWebEngineCore import QWebEnginePage

    class _Page(QWebEnginePage):
        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            # Only the workspace document itself; everything else is refused.
            if url.isLocalFile() and url.toLocalFile().replace("\\", "/") == index_url.toLocalFile().replace("\\", "/"):
                return True
            if url.scheme() in ("about", "data") and not is_main_frame:
                return True
            if is_main_frame and url.scheme() == "https":
                service.handle({"type": "open.url", "url": url.toString()})
            return False

        def createWindow(self, _type):
            return None

        def javaScriptConsoleMessage(self, level, message, line, source):
            if level.value >= 2:          # errors only
                print(f"[Workspace JS] {message} ({Path(source).name}:{line})", file=sys.stderr)

    return _Page(profile, parent)


class WorkspaceWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        from PyQt6.QtWebChannel import QWebChannel
        from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineScript, QWebEngineSettings
        from PyQt6.QtWebEngineWidgets import QWebEngineView

        self.setWindowTitle("ULTRON · AI Workspace")
        icon = Path(__file__).resolve().parents[2] / "config" / "ultron.ico"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1440, 900)
        self.setMinimumSize(380, 560)
        self.setStyleSheet("QMainWindow { background: #030B1E; }")

        index = QUrl.fromLocalFile(str(WEB / "index.html"))
        self.profile = QWebEngineProfile(self)          # off-the-record: nothing written to disk
        # Map tiles come from tile.openstreetmap.org, whose policy asks apps
        # to identify themselves.
        self.profile.setHttpUserAgent("ULTRON-Workspace/2.0 (QtWebEngine; personal desktop assistant)")
        self.page = _make_page(self.profile, self, index)
        try:
            self.page.permissionRequested.connect(lambda request: request.deny())
        except (AttributeError, TypeError):
            try:
                from PyQt6.QtWebEngineCore import QWebEnginePage
                self.page.featurePermissionRequested.connect(
                    lambda url, feature: self.page.setFeaturePermission(
                        url, feature, QWebEnginePage.PermissionPolicy.PermissionDeniedByUser))
            except Exception:
                pass
        s = self.page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        # Needed for https map tiles and listing photos in this local page.
        # The page's CSP still forbids remote scripts and any fetch/XHR
        # (script-src 'self'; connect-src 'self'), so only images get through.
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, False)

        # qwebchannel.js comes from Qt's own resources and runs before the page.
        f = QFile(":/qtwebchannel/qwebchannel.js")
        if f.open(QIODevice.OpenModeFlag.ReadOnly):
            script = QWebEngineScript()
            script.setName("qwebchannel")
            script.setSourceCode(bytes(f.readAll()).decode("utf-8"))
            script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            script.setRunsOnSubFrames(False)
            self.page.scripts().insert(script)
            f.close()

        self.bridge = WorkspaceBridge(self)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("ultron", self.bridge)
        self.page.setWebChannel(self.channel)

        self.view = QWebEngineView(self)
        self.view.setPage(self.page)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setCentralWidget(self.view)
        self.view.load(index)

        service.set_native("open_url", self._open_url)
        service.set_native("copy", self._copy)
        service.set_native("credentials", self._credentials)
        service.set_native("open_config", self._open_config)
        service.set_native("present", lambda payload: service.navigate("chat", (payload or {}).get("conversation_id", "")))
        service.orchestrator()          # starts the MCP registry and status relay
        self._dark_title_bar()

    # ── native abilities offered to the page ──────────────────────────────
    def _open_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    def _copy(self, text) -> None:
        QGuiApplication.clipboard().setText(str(text)[:20000])

    def _credentials(self) -> None:
        from core.integrations.panels import CredentialsDialog
        # Created on the GUI thread via the event loop.
        from PyQt6.QtCore import QTimer

        def show():
            dlg = CredentialsDialog(self)
            dlg.show()
            self._cred_dialog = dlg
        QTimer.singleShot(0, show)

    def _open_config(self) -> None:
        from core.workspace import storage
        d = storage.data_dir()
        d.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    # ── called by the main HUD ────────────────────────────────────────────
    def open_view(self, view: str = "chat", conversation_id: str = "") -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        service.navigate(view, conversation_id)

    def present(self, panel: str, result: dict) -> None:
        """Results published by voice tools and plugins (core.integrations.common.publish)."""
        from core.workspace import legacy
        legacy.present(panel, result if isinstance(result, dict) else {"message": str(result)})
        if panel == "claude_brain":
            self.open_view("settings")
        elif panel not in ("drive", "calendar", "gmail") or (result or {}).get("success") is not False:
            self.show()
            self.raise_()

    def review(self, title: str, detail: str, token=None) -> None:
        from core.integrations.panels import ReviewDialog
        ReviewDialog.open(self, title, detail, token)

    def _dark_title_bar(self) -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        # Hidden, not destroyed: running tasks keep going and the window
        # reopens where it was.
        event.ignore()
        self.hide()
