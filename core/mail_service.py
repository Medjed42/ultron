"""Gmail connector using existing OAuth files. No MCP runtime or password login."""
import base64
import json
import os
import re
import threading
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_lock = threading.RLock()


def token_path():
    from core.integrations import store, google_auth
    secured = store.path("google_gmail")
    return secured if secured.exists() else google_auth.legacy_token()


def service(write=False):
    from core.integrations.google_auth import service as google_service
    return google_service("gmail", write)


def headers(message):
    return {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}


def decode_body(payload):
    texts = []
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        data = payload["body"]["data"]
        texts.append(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace"))
    for part in payload.get("parts", []):
        texts.append(decode_body(part))
    return "\n".join(t for t in texts if t)[:20000]


def summarize(message, full=False):
    h = headers(message)
    result = {"id": message.get("id"), "thread_id": message.get("threadId"),
              "from": h.get("from", ""), "subject": h.get("subject", ""),
              "date": h.get("date", ""), "labels": message.get("labelIds", []),
              "snippet": message.get("snippet", ""), "has_unsubscribe": bool(h.get("list-unsubscribe")),
              "attachments": attachment_metadata(message.get("payload", {}))}
    if full:
        result["body"] = decode_body(message.get("payload", {})) or message.get("snippet", "")
    return result


def attachment_metadata(payload):
    rows = []
    if payload.get("filename"):
        rows.append({"filename": payload["filename"], "mime_type": payload.get("mimeType", ""),
                     "size": payload.get("body", {}).get("size", 0),
                     "attachment_id": payload.get("body", {}).get("attachmentId")})
    for part in payload.get("parts", []):
        rows.extend(attachment_metadata(part))
    return rows


def message_body(to, subject, body, original=None):
    address = parseaddr(to or "")[1]
    if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", address):
        raise ValueError("A valid recipient email address is required")
    if any("\n" in str(v) or "\r" in str(v) for v in (to, subject)):
        raise ValueError("Email headers must be single-line")
    if not str(body or "").strip():
        raise ValueError("Message body is required")
    message = EmailMessage()
    message["To"], message["Subject"] = to, subject or ""
    message.set_content(body)
    result = {}
    if original:
        h = headers(original)
        if h.get("message-id"):
            message["In-Reply-To"] = h["message-id"]
            message["References"] = (h.get("references", "") + " " + h["message-id"]).strip()
        result["threadId"] = original["threadId"]
    result["raw"] = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    return result


def unsubscribe_details(message):
    from urllib.parse import urlsplit
    h = headers(message)
    urls = re.findall(r"<([^>]+)>", h.get("list-unsubscribe", ""))
    url = next((u for u in urls if u.startswith("https://")), None)
    return {"url": url, "host": urlsplit(url).hostname if url else None,
            "one_click": "list-unsubscribe=one-click" in h.get("list-unsubscribe-post", "").lower()}


def unsubscribe(url):
    import ipaddress
    import socket
    import requests
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.port not in (None, 443):
        raise ValueError("Unsubscribe requires a public HTTPS address")
    addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("Unsubscribe address is not public")
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(url, data={"List-Unsubscribe": "One-Click"}, timeout=12, allow_redirects=False)
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Unsubscribe endpoint returned HTTP {response.status_code}; no redirect followed")
    return "Unsubscribe request accepted by sender; future delivery has not yet been verified."
