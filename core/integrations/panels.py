"""Native dialogs that stay outside the web workspace.

Workspace 2.0 (core/workspace) replaced the old tabbed IntegrationHub —
Directions, Places to stay, Gmail, Drive, Calendar, Connections and the
Claude Brain tab are now one conversational workspace with MCP Hub and
Settings. Three things deliberately remain native Qt dialogs:

    ReviewDialog            the exact-changes review for core.confirm requests
                            (sending, trashing, calendar edits). A web page
                            must not be able to click "confirm" for the user.
    CredentialsDialog       Google OAuth, Maps/Amadeus keys, iCloud. Secrets
                            are typed here and go straight to the DPAPI store;
                            they never enter the web view.
    ConnectionChoiceDialog  "Claude connector or my own Google connection?"
"""
import json
from PyQt6.QtCore import Qt, QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QLineEdit, QPlainTextEdit, QCheckBox, QFileDialog, QMessageBox, QScrollArea, QWidget, QFrame)
from . import store
from core.security import redact

STYLE = """
QDialog, QWidget { background:#07152F; color:#F5F9FF; font-family:'Segoe UI'; font-size:13px; }
QLabel { background:transparent; }
QLabel#eyebrow { color:#21D4FD; font-size:11px; letter-spacing:2px; font-weight:600; }
QLabel#title { font-size:22px; font-weight:600; color:#F5F9FF; }
QLabel#muted { color:#94AACC; }
QLineEdit, QPlainTextEdit { background:#030B1E; border:1px solid rgba(74,144,255,0.30); border-radius:9px;
  padding:8px 10px; selection-background-color:#1677FF; }
QLineEdit:focus, QPlainTextEdit:focus { border-color:#258CFF; }
QPushButton { background:#10264C; border:1px solid rgba(74,144,255,0.30); border-radius:9px; padding:8px 14px; font-weight:600; }
QPushButton:hover { border-color:#21D4FD; }
QPushButton:disabled { color:#5f789f; }
QPushButton#primary { border:none; color:white;
  background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #1677FF, stop:0.6 #258CFF, stop:1 #21D4FD); }
QFrame#card { background:#0B1D3E; border:1px solid rgba(74,144,255,0.22); border-radius:14px; }
QCheckBox { spacing:8px; }
QScrollArea { border:none; }
"""


def label(text, style=None):
    item = QLabel(str(text))
    item.setTextFormat(Qt.TextFormat.PlainText)
    item.setWordWrap(True)
    if style:
        item.setObjectName(style)
    return item


def button(text, callback, primary=False):
    result = QPushButton(text)
    if primary:
        result.setObjectName("primary")
    result.clicked.connect(callback)
    return result


def field(placeholder="", password=False):
    result = QLineEdit()
    result.setPlaceholderText(placeholder)
    if password:
        result.setEchoMode(QLineEdit.EchoMode.Password)
    return result


class Signals(QObject):
    done = pyqtSignal(str, object)


class Job(QRunnable):
    def __init__(self, name, fn):
        super().__init__()
        self.name, self.fn, self.signals = name, fn, Signals()

    def run(self):
        try:
            value = self.fn()
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    value = {"success": True, "message": value}
        except Exception as exc:
            value = {"success": False, "error": redact(exc), "verified": False}
        self.signals.done.emit(self.name, value)


class ReviewDialog(QDialog):
    """Shows exactly what will change; only the person's click resolves it."""
    _current = None

    def __init__(self, parent, title, detail, token):
        super().__init__(parent)
        from core import confirm
        self.token = token
        self.setWindowTitle(title)
        self.setStyleSheet(STYLE)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(700, 470)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.addWidget(label("ULTRON  /  REVIEW BEFORE ANYTHING CHANGES", "eyebrow"))
        layout.addWidget(label(title, "title"))
        content = QPlainTextEdit()
        content.setReadOnly(True)
        content.setPlainText(detail)
        layout.addWidget(content, 1)
        controls = QHBoxLayout()
        controls.addStretch()
        self.cancel_btn = button("Cancel", lambda: self.finish(False))
        self.confirm_btn = button("Confirm these exact changes", lambda: self.finish(True), True)
        controls.addWidget(self.cancel_btn)
        controls.addWidget(self.confirm_btn)
        layout.addLayout(controls)
        self.rejected.connect(lambda: confirm.resolve(False, expected_token=token))

    def finish(self, accepted):
        from core import confirm
        confirm.resolve(accepted, expected_token=self.token)
        self.accept()
        if ReviewDialog._current is self:
            ReviewDialog._current = None

    @classmethod
    def open(cls, parent, title, detail, token=None):
        from core import confirm
        token = token or confirm.pending_token()
        if token != confirm.pending_token():
            return None
        if cls._current is not None:
            cls._current.close()
        dlg = cls(parent, title, detail, token)
        cls._current = dlg
        dlg.show()
        return dlg


class CredentialsDialog(QDialog):
    """Accounts and keys. Values go straight to Windows-encrypted storage and
    are never displayed back; statuses come from real checks only."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ULTRON · Credentials")
        self.setStyleSheet(STYLE)
        self.resize(760, 720)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.jobs = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 16)
        root.addWidget(label("ULTRON  /  MCP HUB  /  CREDENTIALS", "eyebrow"))
        root.addWidget(label("Accounts & keys", "title"))
        root.addWidget(label("Stored with Windows encryption (DPAPI). Google consent opens in your browser. "
                             "No Apple Account password is requested; use an app-specific password.", "muted"))
        self.status = label("", "muted")
        root.addWidget(self.status)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QVBoxLayout(body)
        form.setSpacing(12)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        google = QFrame()
        google.setObjectName("card")
        g = QVBoxLayout(google)
        g.addWidget(label("GOOGLE (ULTRON OAUTH) — GMAIL & DRIVE", "eyebrow"))
        self.connection_state = label("", "muted")
        g.addWidget(self.connection_state)
        row = QHBoxLayout()
        self.client_path = field("Google OAuth Desktop client JSON")
        self.client_path.setText(store.preferences().get("google_client_file", ""))
        row.addWidget(self.client_path, 1)
        row.addWidget(button("Choose JSON", self.choose_client))
        g.addLayout(row)
        for name in ("gmail", "drive"):
            r = QHBoxLayout()
            r.addWidget(label(name.title()), 1)
            r.addWidget(button("Connect read only", lambda checked=False, n=name: self.connect_google(n, False)))
            r.addWidget(button("Authorize changes", lambda checked=False, n=name: self.connect_google(n, True)))
            r.addWidget(button("Test", lambda checked=False, n=name: self.test_google(n)))
            r.addWidget(button("Disconnect", lambda checked=False, n=name: self.disconnect_google(n)))
            g.addLayout(r)
        form.addWidget(google)

        keys = QFrame()
        keys.setObjectName("card")
        k = QVBoxLayout(keys)
        k.addWidget(label("MAPS, ACCOMMODATION & CALENDAR", "eyebrow"))
        k.addWidget(label("Optional. Without a Google Maps key the workspace uses OpenStreetMap routing and places; "
                          "a key adds public-transport routes and Google Places. Amadeus adds verified hotel offers.", "muted"))
        secrets = QFormLayout()
        self.secret_fields = {}
        for key, title in [("google_maps_api_key", "Google Maps server key (Routes, Geocoding, Places)"),
                           ("amadeus_client_id", "Amadeus production client ID"),
                           ("amadeus_client_secret", "Amadeus production secret"),
                           ("icloud_username", "iCloud account email"),
                           ("icloud_password", "iCloud app-specific password"),
                           ("icloud_subscription", "Read-only iCloud subscription URL")]:
            item = field("Leave blank to keep the saved value", password=key != "icloud_username")
            self.secret_fields[key] = item
            secrets.addRow(title, item)
        k.addLayout(secrets)
        r = QHBoxLayout()
        r.addWidget(button("Save", self.save_connections, True))
        r.addWidget(button("Disconnect iCloud", self.disconnect_calendar))
        r.addStretch()
        k.addLayout(r)
        form.addWidget(keys)
        form.addStretch()
        close = QHBoxLayout()
        close.addStretch()
        close.addWidget(button("Close", self.close))
        root.addLayout(close)
        self.update_connections()

    # ── state ───────────────────────────────────────────────────────────
    def update_connections(self):
        states = []
        for name in ("gmail", "drive"):
            states.append(name.title() + ": " + ("credentials saved · connection unverified"
                                                 if store.path("google_" + name).exists() else "not connected"))
        states.append("iCloud: " + ("configured · connection unverified" if store.path("icloud").exists()
                                    or store.path("icloud_subscription").exists() else "not connected"))
        states.append("Google Maps key: " + ("saved" if store.path("google_maps_api_key").exists() else "not set"))
        states.append("Amadeus: " + ("saved" if store.path("amadeus_client_id").exists() else "not set"))
        self.connection_state.setText("  |  ".join(states))

    def task(self, name, fn):
        if name in self.jobs:
            self.status.setText("That request is already running.")
            return
        self.status.setText("Working…")
        job = Job(name, fn)
        self.jobs[name] = job
        job.signals.done.connect(self.completed)
        self.pool.start(job)

    def completed(self, name, result):
        self.jobs.pop(name, None)
        self.update_connections()
        if result.get("verified_service"):
            self.connection_state.setText(self.connection_state.text() + "\n"
                                          + result["verified_service"].title() + " · live check passed just now")
        self.status.setText(result.get("error") or result.get("message") or result.get("status") or "Done.")

    # ── actions ─────────────────────────────────────────────────────────
    def choose_client(self):
        path, _ = QFileDialog.getOpenFileName(self, "Google Desktop OAuth client", "", "JSON (*.json)")
        if path:
            self.client_path.setText(path)
            store.save_preferences(google_client_file=path)

    def connect_google(self, name, write):
        from .google_auth import connect
        if write and QMessageBox.question(self, "Authorize account changes?",
                                          f"Google will request {name.title()} access that permits changes. "
                                          "ULTRON still requires review for sending and selected trash operations."
                                          ) != QMessageBox.StandardButton.Yes:
            return
        self.task("connect_" + name, lambda: connect(name, write, self.client_path.text() or None))

    def test_google(self, name):
        def check():
            from .google_auth import service
            api = service(name)
            if name == "gmail":
                api.users().getProfile(userId="me").execute()
            else:
                api.about().get(fields="storageQuota").execute()
            return {"success": True, "message": name.title() + " live connection verified.", "verified_service": name}
        self.task("test_" + name, check)

    def disconnect_google(self, name):
        from .google_auth import disconnect
        self.task("disconnect_" + name, lambda: disconnect(name))

    def save_connections(self):
        try:
            account = store.get_secret("icloud", {}) or {}
            for key, widget in self.secret_fields.items():
                value = widget.text().strip()
                if not value:
                    continue
                if key == "icloud_username":
                    account["username"] = value
                elif key == "icloud_password":
                    account["password"] = value
                else:
                    store.set_secret(key, value)
                widget.clear()
            if account:
                store.set_secret("icloud", account)
            if self.client_path.text():
                store.save_preferences(google_client_file=self.client_path.text())
            self.status.setText("Saved securely. Saving alone does not verify a live connection — "
                                "use Test in MCP Hub or ask the workspace.")
            self.update_connections()
        except Exception as exc:
            self.status.setText(redact(exc))

    def disconnect_calendar(self):
        store.delete_secret("icloud")
        store.delete_secret("icloud_subscription")
        self.update_connections()
        self.status.setText("iCloud disconnected locally. Revoke its app-specific password at account.apple.com "
                            "if no longer needed.")


class ConnectionChoiceDialog(QDialog):
    """Asks whether to use the Claude connector (MCP) or the user's own Google connection."""

    def __init__(self, service="gmail", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Choose connection · {service.title()}")
        self.setMinimumWidth(480)
        self.setStyleSheet(STYLE)
        self.choice = "user"
        self.remember = False
        lay = QVBoxLayout(self)
        lay.setSpacing(14)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.addWidget(label("ULTRON  /  CONNECTION", "eyebrow"))
        lay.addWidget(label(f"Which connection should {service.title()} use?", "title"))
        lay.addWidget(label("Claude's connector reads and drafts only. Your own Google connection (ULTRON OAuth) "
                            "can also make reviewed changes.", "muted"))
        lay.addWidget(button("Claude connector (MCP)", self._choose_claude))
        lay.addWidget(button("My own Google connection (ULTRON OAuth)", self._choose_user, primary=True))
        self.chk_remember = QCheckBox("Remember my choice for this service")
        lay.addWidget(self.chk_remember)

    def _choose_claude(self):
        self.choice = "claude"
        self.remember = self.chk_remember.isChecked()
        self.accept()

    def _choose_user(self):
        self.choice = "user"
        self.remember = self.chk_remember.isChecked()
        self.accept()


__all__ = ["ReviewDialog", "CredentialsDialog", "ConnectionChoiceDialog", "STYLE", "Job"]
