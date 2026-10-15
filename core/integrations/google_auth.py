"""Installed-app OAuth with separate scope grants per service and DPAPI storage."""
import json
import os
import threading
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from . import store

SCOPES = {
    "gmail": ["https://www.googleapis.com/auth/gmail.readonly"],
    "gmail_write": ["https://www.googleapis.com/auth/gmail.modify"],
    "drive": ["https://www.googleapis.com/auth/drive.metadata.readonly"],
    "drive_write": ["https://www.googleapis.com/auth/drive"],
}
_lock = threading.RLock()

class BoundedRequest(Request):
    def __call__(self, *args, **kwargs):
        kwargs["timeout"] = 20
        return super().__call__(*args, **kwargs)


def legacy_token():
    return Path(os.getenv("GMAIL_TOKEN_PATH") or store.ROOT / "Projects/Ultron_Omni_Agent/token.json")


def _info(service):
    info = store.get_secret("google_" + service)
    if info is None and service == "gmail" and not store.preferences().get("gmail_disconnected"):
        p = legacy_token()
        if p.is_file():
            info = json.loads(p.read_text(encoding="utf-8"))
            store.set_secret("google_gmail", info)
            # Existing consumers use this shared service; private recovery copy
            # remains in the pre-change backup, not active credential storage.
            p.unlink()
    return info


def credentials(service, write=False):
    with _lock:
        info = _info(service)
        if not info:
            raise PermissionError(f"Connect {service.title()} in Connections & permissions.")
        creds = Credentials.from_authorized_user_info(info)
        needed = SCOPES[service + ("_write" if write else "")]
        granted = set(creds.scopes or [])
        expanded = granted | (set(SCOPES[service]) if set(SCOPES[service+"_write"]) <= granted else set())
        if not set(needed) <= expanded:
            raise PermissionError(f"{service.title()} needs {'write' if write else 'read'} authorization. Reconnect with the appropriate access in settings.")
        if not creds.valid:
            if not creds.refresh_token:
                raise PermissionError(f"{service.title()} authorization expired. Reconnect in settings.")
            creds.refresh(BoundedRequest())
            store.set_secret("google_" + service, json.loads(creds.to_json()))
        return creds


def service(name, write=False):
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    creds = credentials(name, write)
    return build(name, "v1" if name == "gmail" else "v3",
                 http=AuthorizedHttp(creds, http=httplib2.Http(timeout=20)), cache_discovery=False)


def _authorize(scope, client_file=None):
    """Browser consent; never called automatically by model tools."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    client_file = Path(client_file or store.preferences().get("google_client_file") or
                       store.ROOT / "Projects/Ultron_Omni_Agent/credentials.json")
    if not client_file.is_file():
        raise FileNotFoundError("Choose a Google OAuth Desktop app client JSON file in settings.")
    client = json.loads(client_file.read_text(encoding="utf-8"))
    if "installed" not in client:
        raise ValueError("Use a Desktop app OAuth client, not a web/server client.")
    flow = InstalledAppFlow.from_client_config(client, scopes=scope, autogenerate_code_verifier=True)
    creds = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True,
                                 timeout_seconds=180, access_type="offline", prompt="consent",
                                 authorization_prompt_message="Complete Google authorization in your browser.",
                                 success_message="Ultron authorization completed. You may close this tab.")
    granted = set(creds.granted_scopes if creds.granted_scopes is not None else (creds.scopes or []))
    if not set(scope) <= granted:
        raise PermissionError("Not all requested Google permissions were granted. Existing credentials were kept.")
    return creds


def connect(name, write=False, client_file=None):
    """Only the human-operated settings dialog calls this authorization flow."""
    creds = _authorize(SCOPES[name + ("_write" if write else "")], client_file)
    store.set_secret("google_" + name, json.loads(creds.to_json()))
    if name == "gmail":
        store.save_preferences(gmail_disconnected=False)
    return {"success": True, "status": "authorized", "service": name}


def connect_suite(write=True, client_file=None):
    """One explicit Google consent for Gmail and Drive in the setup wizard."""
    scope = SCOPES["gmail_write" if write else "gmail"] + SCOPES["drive_write" if write else "drive"]
    creds = _authorize(scope, client_file)
    info = json.loads(creds.to_json())
    for name in ("gmail", "drive"):
        store.set_secret("google_" + name, info)
    store.save_preferences(gmail_disconnected=False)
    return {"success": True, "status": "authorized", "services": ["gmail", "drive"]}


def disconnect(name):
    # Local disconnect is immediate and remains effective if revocation is offline.
    info = store.get_secret("google_" + name)
    store.delete_secret("google_" + name)
    if name == "gmail":
        store.save_preferences(gmail_disconnected=True)
    revoked = False
    if info:
        import requests
        try:
            response = requests.post("https://oauth2.googleapis.com/revoke",
                                     data={"token": info.get("refresh_token") or info.get("token")},
                                     timeout=10)
            revoked = response.status_code == 200
        except requests.RequestException:
            pass
    return {"success": True, "status": "disconnected", "remote_revoked": revoked,
            "note": "Google may revoke other grants for this OAuth client. Reconnect other services if needed."}
