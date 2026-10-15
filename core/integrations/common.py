"""Typed-input checks, local panel events and safe HTTP errors."""
import json
import threading
from datetime import datetime, timezone
from urllib.parse import urlsplit
import requests
from core.security import redact

_listener = None
_lock = threading.RLock()


def bind_panel(listener):
    global _listener
    _listener = listener


def publish(panel, result):
    if _listener:
        _listener(panel, result)
    return result


def now():
    return datetime.now(timezone.utc).isoformat()


def request_json(method, url, **kwargs):
    # Provider endpoints are constants. OAuth bearer values never appear in URLs.
    try:
        response = requests.request(method, url, timeout=(5, 20), **kwargs)
        if response.status_code >= 400:
            if response.status_code in (401, 403):
                raise PermissionError("Provider rejected authorization. Check API enablement, account permissions and credentials.")
            if response.status_code == 429:
                raise RuntimeError("Provider rate limit reached. Try again later; request was not replayed.")
            raise RuntimeError(f"Provider returned HTTP {response.status_code}. No result was verified.")
        return response.json()
    except requests.RequestException as exc:
        raise RuntimeError("Provider connection failed or timed out. No result was verified.") from exc


def text(value, name, maximum=500):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must contain 1–{maximum} characters.")
    return value.strip()


def ids(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 50:
        raise ValueError("Select 1–50 items for review.")
    return list(dict.fromkeys(text(v, "ID", 300) for v in values))


def public_link(url):
    parsed = urlsplit(str(url))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only HTTPS links without credentials may be opened.")
    return url


def validate(parameters, schema):
    if not isinstance(parameters, dict):
        raise ValueError("Tool parameters must be an object")
    for name in schema.get("required", []):
        if name not in parameters:
            raise ValueError(f"{name} is required")
    for name, value in parameters.items():
        rule = schema.get("properties", {}).get(name)
        if rule is None:
            raise ValueError(f"Unknown parameter: {name}")
        kind = rule.get("type")
        if kind == "STRING" and not isinstance(value, str):
            raise ValueError(f"{name} must be text")
        if kind == "INTEGER" and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError(f"{name} must be an integer")
        if kind == "NUMBER" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise ValueError(f"{name} must be a number")
        if kind == "BOOLEAN" and not isinstance(value, bool):
            raise ValueError(f"{name} must be true or false")
        if kind == "ARRAY" and (not isinstance(value, list) or len(value) > 100):
            raise ValueError(f"{name} must be an array of at most 100 values")
        if kind == "ARRAY":
            for item in value:
                validate({"item": item}, {"properties": {"item": rule.get("items", {})}})
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError(f"Unsupported {name}")
        if isinstance(value, str) and len(value) > 20000:
            raise ValueError(f"{name} is too long")


def dispatch(panel, parameters, service):
    try:
        result = service(parameters)
        if isinstance(result, str):
            try: result = json.loads(result)
            except ValueError: result = {"success": True, "message": result}
    except Exception as exc:
        result = {"success": False, "status": "authorization_required" if isinstance(exc, PermissionError) else "error",
                  "error": redact(exc), "verified": False}
    publish(panel, result)
    # UI-only data such as image bytes and document previews is never injected.
    safe = {k: v for k, v in result.items() if not k.startswith("_")}
    if panel == "drive" and "files" in safe:
        safe["file_count"] = len(safe["files"])
        safe["files"] = [{k:r.get(k) for k in ("id","name","size_bytes","mimeType","modifiedTime","webViewLink")}
                         for r in safe["files"][:20]]
        safe["model_result_limited"] = True
        safe["note"] = str(safe.get("note","")) + " Full scanned results are in the Drive panel."
    if panel == "calendar" and "events" in safe:
        safe["event_count"] = len(safe["events"])
        safe["events"] = safe["events"][:50]
    return json.dumps(safe, ensure_ascii=False, default=str)
