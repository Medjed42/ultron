"""Results from voice tools and plugins, shown as workspace cards.

Plugins that still run on their own (Google Drive storage analysis, iCloud
Calendar, the Gemini Live tools) publish through
core.integrations.common.publish(panel, result). Before Workspace 2.0 each
panel had its own tab; now the result becomes an assistant message with the
matching card in the "Voice requests" conversation.
"""
from __future__ import annotations

import time

from core.workspace import components as C
from core.workspace import service, storage


def present(panel: str, result: dict) -> None:
    if panel == "workspace":
        service.navigate("chat", result.get("conversation_id", ""))
        return
    if panel == "claude_brain":
        return
    comps, text = [], ""
    if result.get("success") is False:
        title = {"drive": "Google Drive", "calendar": "Calendar", "gmail": "Gmail"}.get(panel, panel.title())
        comps.append(C.error_recovery(f"{title}: request not completed", str(result.get("error") or ""),
                                      [{"label": "MCP Hub", "kind": "view", "value": "mcp"}]))
        text = str(result.get("error") or "")
    elif panel == "drive" and "files" in result:
        q = result.get("quota") or {}
        used, limit = q.get("usage"), q.get("limit")
        summary = (f"Storage used: {_bytes(used)} of {_bytes(limit)}." if used is not None and limit else
                   "Drive storage analysis.")
        if result.get("duplicate_candidates"):
            summary += f" {len(result['duplicate_candidates'])} groups of identical files (same checksum and size)."
        items = sorted(result["files"], key=lambda f: -int(f.get("size_bytes") or 0))[:40]
        comps.append(C.drive_files(summary, [{"name": f.get("name"), "type": f.get("mimeType"),
                                             "modified": f.get("modifiedTime"), "owner": f.get("owner"),
                                             "size": f.get("size_bytes"), "url": f.get("webViewLink")} for f in items],
                                   "Google Drive · ULTRON OAuth"))
        text = summary
    elif panel == "calendar" and "events" in result:
        comps.append(C.calendar_agenda("", result["events"], "iCloud Calendar"))
        text = f"{len(result['events'])} calendar events."
    elif panel == "gmail" and "messages" in result:
        items = [{"subject": m.get("subject"), "from": m.get("from"), "date": m.get("date"),
                  "snippet": m.get("snippet"), "unread": "UNREAD" in (m.get("labels") or [])}
                 for m in result["messages"]]
        comps.append(C.email_summary(f"{len(items)} messages.", items, "Gmail · ULTRON OAuth"))
        text = f"{len(items)} messages."
    else:
        return                      # review steps and status pings need no card
    cid = service._voice_conversation()
    msg = {"id": storage.new_id("m"), "role": "assistant", "text": text, "components": comps,
           "suggestions": [], "activity": [], "provider": "local", "lang": "en", "created": time.time(),
           "status": "error" if result.get("success") is False else "ok", "intent": panel}
    conv = storage.append_message(cid, msg)
    service.publish({"type": "message.added", "conversation_id": cid, "message": msg,
                     "conversation": {"id": conv["id"], "title": conv["title"]}})


def _bytes(v) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.1f} {unit}"
        n /= 1024
    return "—"
