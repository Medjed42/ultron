"""
actions/ai_connections.py — let ULTRON check and repair its own AI links.

Voice front-end for the same manual commands the chat box understands:
"check AI connections", "connect Claude", "reconnect Gemini", "reconnect MCP",
"show MCP tools", "show connection errors". Every answer comes from a real
check in core.connections — nothing here reports a status it did not observe.
"""
from __future__ import annotations

from core import command_router

_ACTIONS = {
    "status": "check", "check": "check",
    "connect_claude": "claude", "reconnect_claude": "claude",
    "connect_gemini": "gemini", "reconnect_gemini": "gemini",
    "reconnect_mcp": "mcp", "check_mcp": "mcp",
    "list_mcp_tools": "mcp_tools", "mcp_tools": "mcp_tools",
    "errors": "errors",
}


def ai_connections(parameters: dict, player=None, **_kw) -> str:
    action = str((parameters or {}).get("action", "status")).lower().strip()
    manual = _ACTIONS.get(action)
    if manual is None:
        return f"Unknown action '{action}'. Use one of: {', '.join(sorted(_ACTIONS))}."
    router = command_router.get_router()
    if router is None:
        return "The command router is not running yet; try again in a moment."
    res = router.manual(manual)
    text = res.chat_text() or ("Done." if res.ok else "Failed.")
    if player:
        try:
            player.write_log(f"SYS: {text}")
        except Exception:
            pass
    return text


TOOL = {
    "name": "ai_connections",
    "description": (
        "Checks or repairs ULTRON's AI connections: Claude (Claude Code CLI), Gemini "
        "(API and Live session) and MCP servers/tools reached through Claude. Call it when "
        "the user asks whether Claude/Gemini/MCP is connected, asks to connect or "
        "reconnect one of them, asks which MCP tools are available, or asks what the "
        "connection errors are — and BEFORE telling the user something cannot be done "
        "because a provider is unavailable. Never open a browser instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": ("status | check | connect_claude | reconnect_gemini | "
                                "reconnect_mcp | list_mcp_tools | errors"),
            },
        },
        "required": ["action"],
    },
    "handler": ai_connections,
}
