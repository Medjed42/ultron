"""
actions/workspace_assistant.py — voice access to ULTRON Workspace 2.0.

Replaces the google_maps and accommodation_search plugins. The spoken request
goes to the same orchestrator the Workspace window uses (MCP tools for routes,
places and stays), the interactive map or stay comparison opens in the
Workspace, and a short answer comes back to be spoken. Every number in that
answer was returned by a provider; nothing is booked.
"""
from __future__ import annotations

import re


def ultron_workspace(parameters: dict, player=None, **_kw) -> str:
    request = str((parameters or {}).get("request") or "").strip()
    if not request:
        return "Tell me what to look up — a route, a place, or somewhere to stay."
    from core.integrations.common import publish
    from core.workspace import service
    conv = service._voice_conversation()
    publish("workspace", {"conversation_id": conv})          # opens the Workspace on the HUD's thread
    if player:
        try:
            player.write_log(f"SYS: Workspace · {request[:90]}")
        except Exception:
            pass
    res = service.run_voice_request(request)
    text = re.sub(r"\*\*|`", "", res.get("text") or "")
    text = re.sub(r"^\s*-\s+", "", text, flags=re.M)
    return text[:1200] + ("\n(Details and the map are open in the Workspace.)" if res.get("ok") else "")


TOOL = {
    "name": "ultron_workspace",
    "description": (
        "ULTRON Workspace: routes, distances and travel times between places; restaurants, parking, "
        "fuel and other places near somewhere; apartments, 'stan na dan', rooms and hotels for given "
        "dates with real prices compared across sources; follow-ups such as 'only under 30 euros', "
        "'which is closest to the centre', 'show it on the map', 'the booking link'. Opens an "
        "interactive map or comparison in the Workspace window and returns a short answer. Uses real "
        "providers only — never invents distances or prices — and never books or pays."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "request": {"type": "STRING",
                        "description": "The user's full request in their own words and language, e.g. "
                                       "'Koliko ima od Niša do Beograda?' or 'Nađi mi najjeftiniji stan na dan "
                                       "u Nišu za 16. oktobar'."},
        },
        "required": ["request"],
    },
    "handler": ultron_workspace,
}
