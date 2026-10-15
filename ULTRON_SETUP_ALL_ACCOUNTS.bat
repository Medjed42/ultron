@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "ULTRON_DISABLE_REMOTE=1"
set "ULTRON_START_TELEGRAM=0"
set "_ULTRON_SETUP_PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
set "_ULTRON_SETUP_ARGS="
if exist "%_ULTRON_SETUP_PY%" goto run_setup
where py.exe >nul 2>nul
if not errorlevel 1 (
    set "_ULTRON_SETUP_PY=py.exe"
    set "_ULTRON_SETUP_ARGS=-3"
    goto run_setup
)
where python.exe >nul 2>nul
if not errorlevel 1 (
    set "_ULTRON_SETUP_PY=python.exe"
    goto run_setup
)
echo Python was not found. Install Python from https://www.python.org/downloads/windows/
echo Keep this file inside your Ultron project folder.
pause
exit /b 1

:run_setup
"%_ULTRON_SETUP_PY%" %_ULTRON_SETUP_ARGS% -X utf8 -c "import sys; from pathlib import Path; p=Path(sys.argv[1]); code=p.read_text(encoding='utf-8-sig').split(chr(10)+'# ULTRON_SETUP_PYTHON'+chr(10),1)[1]; exec(compile(code,str(p),'exec'),{'__name__':'__main__','__file__':str(p)})" "%~f0" %*
set "_ULTRON_SETUP_RESULT=%ERRORLEVEL%"
if not "%_ULTRON_SETUP_RESULT%"=="0" (
    echo.
    echo Setup could not finish. The explanation is above. Existing account settings were kept unless you saved changes.
    if /i not "%~1"=="--check" pause
)
exit /b %_ULTRON_SETUP_RESULT%

# ULTRON_SETUP_PYTHON
"""One-file account setup entry point. Run the BAT from the Ultron project."""
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
PACKAGES = {
    "PyQt6": "PyQt6>=6.6,<7", "PyQt6.QtWebEngineWidgets": "PyQt6-WebEngine>=6.11,<7",
    "win32crypt": "pywin32", "requests": "requests>=2.31,<3",
    "googleapiclient": "google-api-python-client", "google_auth_oauthlib": "google-auth-oauthlib",
    "caldav": "caldav>=2.2.6,<3", "icalendar": "icalendar>=7.3,<8",
    "recurring_ical_events": "recurring-ical-events>=3.8,<4", "dotenv": "python-dotenv",
}

def prepare(check_only=False):
    if os.name != "nt":
        raise RuntimeError("This launcher requires Windows and Windows-encrypted credential storage.")
    if not (ROOT / "core/integrations/store.py").is_file():
        raise RuntimeError("Keep ULTRON_SETUP_ALL_ACCOUNTS.bat inside the Ultron project folder.")
    missing = []
    for module, package in PACKAGES.items():
        try:
            found = importlib.util.find_spec(module) is not None
        except ModuleNotFoundError:
            found = False
        if not found:
            missing.append(package)
    if missing:
        if check_only:
            raise RuntimeError("Missing setup components: " + ", ".join(missing))
        print("Installing missing setup components from the Python package registry...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--only-binary=:all:", *missing], check=True)
    from core.security import install_redaction
    install_redaction()

if __name__ == "__main__":
    try:
        prepare("--check" in sys.argv[2:])
    except Exception as exc:
        print("Setup preparation failed: " + str(exc))
        raise SystemExit(1)

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QWidget, QScrollArea, QLabel, QPushButton, QLineEdit, QComboBox, QFormLayout,
    QFileDialog, QCheckBox, QPlainTextEdit, QProgressBar, QFrame)
from PyQt6.QtCore import QThreadPool
from core.integrations import store, google_auth, calendar, common
from core.integrations.panels import STYLE, Job, label, field, button
from core.workspace import geo as _geo


class maps:
    """Google Maps key checks for this wizard. ULTRON itself routes through
    core.workspace.geo (Google when a key is saved, OpenStreetMap otherwise)."""

    @staticmethod
    def geocode(name):
        rows = _geo._google_geocode(name, "rs", store.api_key("google_maps_api_key"))
        if not rows:
            raise RuntimeError("Geocoding returned no result for " + name)
        loc = rows[0]["geometry"]["location"]
        return {"lat": loc["lat"], "lon": loc["lng"], "name": name}

    @staticmethod
    def route(p):
        key = store.api_key("google_maps_api_key")
        a, b = maps.geocode(p["origin"]), maps.geocode(p["destination"])
        r = _geo._google_route(a, b, p.get("mode", "driving"), key, None)
        return {"success": True, "distance_km": r["routes"][0]["distance_m"] / 1000}


class MapView(QFrame):
    """Embed-API preview used only by this wizard's Maps step."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.layout = QVBoxLayout(self)
        self.notice = label("Map preview appears here after you save an Embed-only key.")
        self.layout.addWidget(self.notice)
        self.view = None
        self.setMinimumHeight(260)

    def show_map(self, mode, values):
        key = store.get_secret("google_maps_embed_key")
        if not key or mode not in {"place", "search", "directions"}:
            self.notice.show()
            return
        if self.view is None:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
            self.profile = QWebEngineProfile(self)
            self.page = QWebEnginePage(self.profile, self)
            self.page.permissionRequested.connect(lambda request: request.deny())
            self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
            self.view = QWebEngineView(self)
            self.view.setPage(self.page)
            self.layout.addWidget(self.view)
        import html
        src = "https://www.google.com/maps/embed/v1/" + mode + "?" + urlencode(dict(values, key=key))
        self.view.setHtml('<html><body style="margin:0"><iframe width="100%" height="100%" style="border:0" '
                          'referrerpolicy="strict-origin-when-cross-origin" src="' + html.escape(src, quote=True)
                          + '"></iframe></body></html>', QUrl("https://ultron.local/"))
        self.notice.hide()
from core.security import redact
from memory.config_manager import get_gemini_key

NAMES = ["Gemini AI", "Gmail", "Drive", "Maps routing", "Places discovery", "Map display", "iCloud Calendar", "Amadeus offers"]
OPTIONAL = {"Amadeus offers"}
GUIDES = {
    "google": "https://developers.google.com/identity/protocols/oauth2/native-app",
    "maps": "https://developers.google.com/maps/documentation/routes/get-api-key",
    "apple": "https://support.apple.com/en-us/102654",
    "amadeus": "https://developers.amadeus.com/",
}

def client_path():
    saved = store.preferences().get("google_client_file")
    return str(saved or ROOT / "Projects/Ultron_Omni_Agent/credentials.json")

def cloud_url(path, client_file=""):
    project = ""
    try:
        config = json.loads(Path(client_file or client_path()).read_text(encoding="utf-8-sig"))
        project = config.get("installed", {}).get("project_id", "")
    except (OSError, ValueError):
        pass
    return "https://console.cloud.google.com/" + path + ("?" + urlencode({"project": project}) if project else "")

def check_google(name, write=False):
    api = google_auth.service(name, write=write)
    if name == "gmail":
        api.users().getProfile(userId="me").execute()
    else:
        api.about().get(fields="storageQuota").execute()
    return "Verified read access" + (" and granted change permissions; no changes made" if write else "; account changes are not authorized")

def verify_maps():
    # Public sample city pair only; never accesses device location.
    results = {}
    for name, fn in [
        ("Maps routing", lambda: maps.route({"origin": "Novi Sad", "destination": "Vrbas", "mode": "driving"})),
        ("Places discovery", lambda: common.request_json("POST", "https://places.googleapis.com/v1/places:searchText",
            headers={"X-Goog-Api-Key": store.api_key("google_maps_api_key"), "X-Goog-FieldMask": "places.id"},
            json={"textQuery": "hotel in Belgrade", "pageSize": 1})),
    ]:
        try:
            result = fn()
            if result.get("success") is False:
                raise RuntimeError(result.get("error", "No route returned"))
            if name == "Maps routing":
                maps.geocode("Belgrade")
                results[name] = "Verified Routes and Geocoding using public city names"
            else:
                results[name] = "Verified Places API response; dated availability is not established"
        except Exception as exc:
            results[name] = "Needs attention: " + redact(exc)
    return results

def verify_calendar():
    if store.get_secret("icloud_subscription"):
        start, end, zone = calendar.period({})
        calendar.event_rows(calendar.subscription_data(store.get_secret("icloud_subscription")), start, end, zone)
        return "Verified read-only iCloud subscription"
    with calendar.connection() as client:
        calendars = client.principal().calendars()
        if not calendars:
            return "Verified iCloud authentication; no calendars found"
        writable = calendar.can_write(calendars[0])
        # Metadata/privileges only; no event is created.
        return "Verified iCloud calendar discovery; first calendar " + ("permits changes" if writable else "is read-only or write permission is unverified")

def verify_amadeus():
    key = store.api_key("amadeus_client_id")
    secret = store.api_key("amadeus_client_secret")
    response = common.request_json("POST", "https://api.amadeus.com/v1/security/oauth2/token",
        data={"grant_type": "client_credentials", "client_id": key, "client_secret": secret})
    token = response.get("access_token")
    if not token:
        raise RuntimeError("Amadeus returned no production access token.")
    from core.security import register_secrets
    register_secrets({"access_token": token})
    # Verifies authentication, not hotel availability or paid offer entitlement.
    return "Verified production authentication; dated hotel offers remain unverified"

def summary_lines(states, timestamps):
    lines = ["ULTRON ACCOUNT SETUP", "",
             "No emails sent, files trashed, events created, bookings or payments made.", ""]
    for name in NAMES:
        optional = " (optional)" if name in OPTIONAL else ""
        stamp = timestamps.get(name)
        lines.append(name + optional + ": " + states.get(name, "Not checked in this setup session"))
        if stamp:
            lines.append("  Checked/saved: " + stamp)
    return "\n".join(lines)

class SetupWizard(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ultron | Set up all accounts")
        self.resize(1040, 800)
        self.setMinimumSize(820, 650)
        self.setStyleSheet(STYLE)
        self.states, self.timestamps = {}, {}
        self.job = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.buttons = []
        self.pending_preview = False
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 20, 26, 20)
        root.addWidget(label("ULTRON / ACCOUNT SETUP", "eyebrow"))
        root.addWidget(label("Connect once. Use everything in Ultron.", "title"))
        self.step_title = label("")
        root.addWidget(self.step_title)
        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        for builder in (self.overview, self.gemini_page, self.google_page, self.maps_page, self.apple_page, self.amadeus_page, self.finish_page):
            scroll = QScrollArea(); scroll.setWidgetResizable(True)
            page = QWidget(); layout = QVBoxLayout(page)
            layout.setContentsMargins(18, 18, 18, 18)
            builder(layout)
            layout.addStretch()
            scroll.setWidget(page)
            self.pages.addWidget(scroll)
        self.status = label("Ready. Your existing account settings will be reused.")
        root.addWidget(self.status)
        self.progress = QProgressBar(); self.progress.setRange(0, 0); self.progress.hide()
        root.addWidget(self.progress)
        nav = QHBoxLayout()
        self.back_button = button("Back", lambda: self.step(-1))
        self.next_button = button("Next / continue later", lambda: self.step(1), True)
        nav.addWidget(self.back_button); nav.addStretch(); nav.addWidget(self.next_button)
        root.addLayout(nav)
        self.refresh_step()

    def action(self, layout, text, fn, primary=False):
        item = button(text, fn, primary)
        self.buttons.append(item); layout.addWidget(item)
        return item

    def link(self, layout, text, url):
        return self.action(layout, text, lambda: self.open_link(url() if callable(url) else url))

    def open_link(self, url):
        if not QDesktopServices.openUrl(QUrl(url)):
            self.status.setText("Could not open the browser. Open this address manually: " + url)

    def set_status(self, name, message):
        self.states[name] = message
        self.timestamps[name] = common.now()
        self.update_summary()

    def task(self, fn, on_done):
        if self.job:
            return
        self.progress.show()
        self.status.setText("Working... Complete any sign-in or consent in your own browser. OAuth allows up to 3 minutes.")
        for item in self.buttons:
            item.setEnabled(False)
        self.back_button.setEnabled(False); self.next_button.setEnabled(False)
        job = Job("setup", fn)
        self.job = job
        self.completion = on_done
        job.signals.done.connect(self.completed)
        self.pool.start(job)
        QTimer.singleShot(45000, lambda: self.slow(job))

    def slow(self, job):
        if self.job is job:
            self.status.setText("Still waiting for the provider or browser consent. Closing setup will not approve anything.")

    def completed(self, _name, result):
        self.job = None; self.progress.hide()
        for item in self.buttons:
            item.setEnabled(True)
        self.refresh_step()
        if result.get("success") is False:
            self.status.setText(result.get("error", "Connection failed."))
        try:
            self.completion(result)
        except Exception as exc:
            self.status.setText(redact(exc))
        self.update_summary()

    def step(self, amount):
        self.pages.setCurrentIndex(max(0, min(self.pages.count()-1, self.pages.currentIndex()+amount)))
        self.refresh_step()

    def refresh_step(self):
        names = ["Before you start", "Gemini AI", "Google: Gmail and Drive", "Google Maps and property discovery",
                 "Apple / iCloud Calendar", "Optional: priced accommodation offers", "Connection results"]
        index = self.pages.currentIndex()
        self.step_title.setText(f"STEP {index+1} OF 6  /  {names[index]}")
        self.back_button.setEnabled(index > 0 and not self.job)
        self.next_button.setEnabled(index < 5 and not self.job)
        self.update_summary()


    def gemini_page(self, layout):
        layout.addWidget(label("Gemini AI Account", "title"))
        layout.addWidget(label("Connect Gemini AI via API Key. Ultron requires this for its core intelligence."))
        self.link(layout, "Get Gemini API Key", "https://aistudio.google.com/app/apikey")
        form = QFormLayout()
        self.gemini_key = field("Gemini API Key; blank keeps saved", True)
        form.addRow("Gemini API Key", self.gemini_key)
        layout.addLayout(form)
        self.action(layout, "Save Gemini Connection", self.save_gemini, True)
        self.gemini_status = label("Gemini configuration controls the main brain of Ultron.")
        layout.addWidget(self.gemini_status)

    def save_gemini(self):
        key = self.gemini_key.text().strip()
        if key:
            from memory.config_manager import save_api_keys
            save_api_keys(key)
            self.set_status("Gemini AI", "Verified and saved Gemini API key.")
            self.gemini_status.setText("Gemini API key saved.")
            self.gemini_key.clear()
        else:
            self.gemini_status.setText("No key entered. Existing configuration kept.")
            self.set_status("Gemini AI", "Existing configuration kept.")

    def overview(self, layout):
        layout.addWidget(label("This single launcher guides every account required by the Phase 2 plugins.", "title"))
        layout.addWidget(label("Gemini is already configured and will be kept as it is." if get_gemini_key() else
                               "Gemini needs a key in Ultron's existing setup screen. This wizard does not replace its model or credentials."))
        for text in [
            "1. Gmail + Drive: one Google consent, then separate connection checks.",
            "2. Maps + stays: enable your Google APIs and paste restricted keys.",
            "3. Calendar: Apple app-specific password, or an existing read-only subscription.",
            "4. Optional Amadeus: production credentials for dated hotel offers.",
            "Passwords and two-factor codes are entered only on Google/Apple's own pages. Keys and app-specific passwords go in masked local fields and use Windows encryption.",
            "Google Cloud billing, account creation, API enablement and terms must be completed by you. Maps checks make a few API requests that may count toward your project's charges.",
            "You can skip a step and rerun this file later. Nothing is called connected merely because a credential was saved.",
        ]:
            layout.addWidget(label(text))
        self.link(layout, "Open the complete setup and limitations report", lambda: QUrl.fromLocalFile(str(ROOT/"docs/PHASE2_ENGINEERING_REPORT.md")).toString())

    def google_page(self, layout):
        layout.addWidget(label("Gmail and Drive with one Google sign-in", "title"))
        layout.addWidget(label("Use the Google Cloud project that owns this Desktop OAuth client. Enable Gmail API and Google Drive API. If the app is in Testing, add your own Google account as a test user in Audience."))
        self.client = field("Desktop OAuth client JSON"); self.client.setText(client_path())
        layout.addWidget(self.client)
        self.action(layout, "Choose a different Desktop client JSON", self.choose_client)
        row = QHBoxLayout()
        self.link(row, "Google API library", lambda: cloud_url("apis/library", self.client.text()))
        self.link(row, "OAuth clients / create Desktop client", lambda: cloud_url("auth/clients", self.client.text()))
        self.link(row, "Audience / test users", lambda: cloud_url("auth/audience", self.client.text()))
        layout.addLayout(row)
        self.access = QComboBox()
        self.access.addItems(["Read + reviewed changes (full plugin functionality)", "Read-only (no drafts, labels or cleanup)"])
        layout.addWidget(self.access)
        layout.addWidget(label("Full access requests gmail.modify and drive. It permits Gmail changes and access to existing Drive files. Ultron still asks for exact approval before sending mail or moving selected items to trash. Google shows the permissions before you grant them."))
        self.action(layout, "Connect Gmail + Drive in my browser", self.connect_google, True)
        self.action(layout, "Test saved Gmail + Drive connections", self.test_google)
        self.google_status = label("Existing Gmail read access can be reused. A new consent is needed for Drive or additional permissions.")
        layout.addWidget(self.google_status)
        self.link(layout, "Official Google Desktop OAuth guide", GUIDES["google"])

    def choose_client(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Google Desktop OAuth client", "", "JSON (*.json)")
        if path:
            self.client.setText(path)

    def connect_google(self):
        path, write = self.client.text().strip(), self.access.currentIndex() == 0
        def work():
            google_auth.connect_suite(write=write, client_file=path or None)
            if path:
                store.save_preferences(google_client_file=path)
            return self.google_results(write)
        self.task(work, self.google_done)

    def google_results(self, write):
        statuses = {}
        for name in ("gmail", "drive"):
            try:
                statuses[name.title()] = check_google(name, write)
            except Exception as exc:
                statuses[name.title()] = "Needs attention: " + redact(exc)
        return {"success": True, "statuses": statuses}

    def test_google(self):
        write = self.access.currentIndex() == 0
        self.task(lambda: self.google_results(write), self.google_done)

    def google_done(self, result):
        statuses = result.get("statuses") or {n: "Needs attention: " + result.get("error", "Authorization unfinished") for n in ("Gmail", "Drive")}
        for name, message in statuses.items():
            self.set_status(name, message)
        self.google_status.setText("\n".join(name + ": " + message for name, message in statuses.items()))
        self.status.setText("Google checks finished. If an API is disabled, enable it in the linked project and use Test saved connections.")

    def maps_page(self, layout):
        layout.addWidget(label("Maps, routes and property discovery", "title"))
        layout.addWidget(label("Enable Routes API, Geocoding API and Places API (New), with billing configured in your own project. Restrict the server key to those APIs. A separate public key is used only for Maps Embed API."))
        row = QHBoxLayout()
        self.link(row, "API library", lambda: cloud_url("apis/library", self.client.text()))
        self.link(row, "Keys and API restrictions", lambda: cloud_url("apis/credentials", self.client.text()))
        self.link(row, "Billing", lambda: cloud_url("billing", self.client.text()))
        layout.addLayout(row)
        form = QFormLayout()
        self.maps_key = field("Paste server key; blank keeps saved key", True)
        self.embed_key = field("Paste separate Embed-only key; blank keeps saved key", True)
        form.addRow("Routes / Geocoding / Places key", self.maps_key)
        form.addRow("Public Maps Embed-only key", self.embed_key)
        layout.addLayout(form)
        layout.addWidget(label("Restrict the Embed key to Maps Embed API and HTTP referrer https://ultron.local/* . The preview uses this origin. Verify your map appears before marking it visible."))
        self.action(layout, "Save keys and test Routes + Geocoding + Places", self.save_maps, True)
        self.maps_status = label("Tests use Novi Sad, Vrbas and Belgrade. No device location or private starting address is accessed.")
        layout.addWidget(self.maps_status)
        self.action(layout, "Preview the saved Embed map", self.preview_map)
        self.map_view = MapView(); layout.addWidget(self.map_view)
        self.map_seen = QCheckBox("I can see the actual Google map in the preview above")
        self.map_seen.setEnabled(False)
        self.map_seen.clicked.connect(self.mark_map)
        layout.addWidget(self.map_seen)
        self.link(layout, "Official Google Maps setup guide", GUIDES["maps"])

    def save_maps(self):
        values = {"google_maps_api_key": self.maps_key.text().strip(), "google_maps_embed_key": self.embed_key.text().strip()}
        def work():
            for key, value in values.items():
                if value:
                    store.set_secret(key, value)
            return {"success": True, "statuses": verify_maps()}
        def done(result):
            self.maps_key.clear(); self.embed_key.clear()
            statuses = result.get("statuses") or {"Maps routing": "Needs attention: " + result.get("error", "Test failed"), "Places discovery": "Not verified"}
            for name, message in statuses.items():
                self.set_status(name, message)
            if values["google_maps_embed_key"]:
                self.map_seen.setChecked(False)
                self.map_seen.setEnabled(False)
                self.set_status("Map display", "Key saved; map rendering not yet verified")
            self.maps_status.setText("\n".join(name + ": " + message for name, message in statuses.items()))
            self.status.setText("Maps checks finished. Review each result above.")
        self.task(work, done)

    def preview_map(self):
        try:
            if self.embed_key.text().strip():
                store.set_secret("google_maps_embed_key", self.embed_key.text().strip())
                self.embed_key.clear()
            if not store.get_secret("google_maps_embed_key"):
                raise ValueError("Enter an Embed-only key first.")
            self.map_seen.setChecked(False)
            self.map_view.show_map("place", {"q": "Belgrade, Serbia"})
            self.map_seen.setEnabled(True)
            self.set_status("Map display", "Preview opened; actual map visibility requires your check")
        except Exception as exc:
            self.status.setText(redact(exc))

    def mark_map(self, checked):
        self.set_status("Map display", "User verified map rendering" if checked else "Map rendering not verified")

    def apple_page(self, layout):
        layout.addWidget(label("Apple Calendar on this Windows computer", "title"))
        layout.addWidget(label("Sign in at Apple Account, then Sign-In and Security → App-Specific Passwords. Create one for Ultron. Two-factor authentication must be enabled. Paste only the generated app-specific password here, never your main Apple password."))
        row = QHBoxLayout()
        self.link(row, "Open Apple Account", "https://account.apple.com/")
        self.link(row, "Apple's app-specific password instructions", GUIDES["apple"])
        layout.addLayout(row)
        self.apple_mode = QComboBox()
        self.apple_mode.addItems(["iCloud account + app-specific password (read / supported writes)", "Existing iCloud subscription URL (read only)"])
        if store.path("icloud_subscription").exists():
            self.apple_mode.setCurrentIndex(1)
        layout.addWidget(self.apple_mode)
        form = QFormLayout()
        self.apple_email = field("Apple Account email; blank keeps saved")
        self.apple_password = field("App-specific password; blank keeps saved", True)
        self.apple_subscription = field("Existing authorized iCloud webcal/HTTPS URL", True)
        form.addRow("Account email", self.apple_email)
        form.addRow("App-specific password", self.apple_password)
        form.addRow("Read-only subscription", self.apple_subscription)
        layout.addLayout(form)
        layout.addWidget(label("Saving switches to the selected connection mode. It does not publish a private calendar or create any event."))
        self.action(layout, "Save and test iCloud Calendar", self.save_apple, True)
        self.apple_status = label("iCloud has its own calendar protocol; a Gmail/Drive login does not connect it.")
        layout.addWidget(self.apple_status)

    def save_apple(self):
        mode = self.apple_mode.currentIndex()
        email, password, subscription = self.apple_email.text().strip(), self.apple_password.text().strip(), self.apple_subscription.text().strip()
        def work():
            if mode == 1:
                if subscription:
                    # Validate the URL/provider before replacing an existing mode.
                    calendar.subscription_data(subscription)
                    store.set_secret("icloud_subscription", subscription)
                elif not store.get_secret("icloud_subscription"):
                    raise ValueError("Enter an authorized iCloud subscription URL.")
            else:
                account = store.get_secret("icloud", {}) or {}
                if email: account["username"] = email
                if password: account["password"] = password
                if not account.get("username") or not account.get("password"):
                    raise ValueError("Enter your Apple email and a generated app-specific password.")
                store.set_secret("icloud", account)
                store.delete_secret("icloud_subscription")
            return {"success": True, "message": verify_calendar()}
        def done(result):
            self.apple_email.clear(); self.apple_password.clear(); self.apple_subscription.clear()
            message = result.get("message") if result.get("success") else "Needs attention: " + result.get("error", "Connection failed")
            self.set_status("iCloud Calendar", message)
            self.apple_status.setText(message); self.status.setText(message)
        self.task(work, done)

    def amadeus_page(self, layout):
        layout.addWidget(label("Optional: dated hotel offers", "title"))
        layout.addWidget(label("Google Places already supports property discovery. Add Amadeus only for production hotel offers with dates and prices. In the developer portal, create your app and request Production access. Any contract/billing steps must be completed by you. Test/sandbox keys cannot verify real availability."))
        self.link(layout, "Open Amadeus for Developers", GUIDES["amadeus"])
        form = QFormLayout()
        self.amadeus_id = field("Production API key / client ID; blank keeps saved", True)
        self.amadeus_secret = field("Production API secret; blank keeps saved", True)
        form.addRow("Production client ID", self.amadeus_id)
        form.addRow("Production secret", self.amadeus_secret)
        layout.addLayout(form)
        self.action(layout, "Save and test production authentication", self.save_amadeus, True)
        self.amadeus_status = label("Optional. An authentication test does not book anything or prove a particular hotel is available.")
        layout.addWidget(self.amadeus_status)

    def save_amadeus(self):
        values = {"amadeus_client_id": self.amadeus_id.text().strip(), "amadeus_client_secret": self.amadeus_secret.text().strip()}
        def work():
            for key, value in values.items():
                if value: store.set_secret(key, value)
            return {"success": True, "message": verify_amadeus()}
        def done(result):
            self.amadeus_id.clear(); self.amadeus_secret.clear()
            message = result.get("message") if result.get("success") else "Needs attention: " + result.get("error", "Authentication failed")
            self.set_status("Amadeus offers", message)
            self.amadeus_status.setText(message); self.status.setText(message)
        self.task(work, done)

    def finish_page(self, layout):
        layout.addWidget(label("Your connection results", "title"))
        layout.addWidget(label("Only completed live checks are marked verified. Skipped services remain unverified. Rerun this same file whenever you want to finish a missing step."))
        self.summary = QPlainTextEdit(); self.summary.setReadOnly(True); self.summary.setMinimumHeight(280)
        layout.addWidget(self.summary)
        self.action(layout, "Save this status report (no credentials)", self.save_report)
        self.action(layout, "Start Ultron", self.start_ultron, True)
        layout.addWidget(label("In Ultron, open WORKSPACE. Gmail sends, selected cleanup and calendar changes retain their exact approval dialogs. This wizard does not activate Claude PC access, Telegram or the phone dashboard."))

    def update_summary(self):
        if hasattr(self, "summary"):
            self.summary.setPlainText(summary_lines(self.states, self.timestamps))

    def save_report(self):
        directory = ROOT / "logs"
        directory.mkdir(exist_ok=True)
        path = directory / "account-setup-status.txt"
        path.write_text(summary_lines(self.states, self.timestamps), encoding="utf-8")
        self.status.setText("Saved account status to logs/account-setup-status.txt. It contains no keys or tokens.")

    def start_ultron(self):
        self.save_report()
        os.environ["ULTRON_DISABLE_REMOTE"] = "1"
        os.environ["ULTRON_START_TELEGRAM"] = "0"
        from launch_ultron import launch
        launch("main.py")
        self.status.setText("Ultron launch requested. Open WORKSPACE there. Missing account steps remain listed above.")

    def closeEvent(self, event):
        if self.job:
            self.status.setText("A connection attempt is still running. Finish/cancel the browser sign-in, or wait for its timeout before closing.")
            event.ignore()
        else:
            event.accept()

def main():
    if "--check" in sys.argv[2:]:
        print("Ultron account setup startup check passed. No logins, browser tabs, account changes or installations were performed.")
        return 0
    if QApplication.instance() is None:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication([])
    wizard = SetupWizard()
    wizard.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())
