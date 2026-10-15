"""
actions/claude_mcp.py — Gmail, Google Drive, Claude Docs and Gamma through
Claude's MCP connectors.

core.claude_brain.execute_claude_mcp_query existed but nothing called it, so the
saved "use Claude's connection" preference did nothing and a Gmail request had
no working path at all once the native mail plugin was disabled. This is that
path. It checks, in order, that Claude is usable and that the service's MCP
server is connected, and reports which one is missing instead of improvising.

Claude may only read, search and create drafts here; sending, trashing, sharing
and label changes are refused by Claude Code and reported back.
"""
from __future__ import annotations

from core import claude_brain
from core import connections as conn


def claude_mcp(parameters: dict, player=None, **_kw) -> str:
    p = parameters or {}
    service = str(p.get("service", "")).lower().strip()
    task = str(p.get("task", "")).strip()
    if service not in claude_brain.SAFE_MCP_TOOLS:
        return f"Unknown service '{service}'. Use gmail, drive, docs or gamma."
    if not task:
        return "Describe the task to run."

    mgr = conn.manager()
    st = mgr.ensure_fresh("claude")
    if not st.usable:
        return (f"Claude is {conn.describe_state(st.state)} ({st.last_error or st.detail}). "
                f"I cannot reach {service.title()} through Claude's MCP until it is reconnected — "
                f"the user can run 'claude auth login' in a terminal or say 'connect Claude'. "
                f"Do not open a browser instead.")
    srv = next((s for s in mgr.mcp_servers() if s.extra.get("service") == service), None)
    if srv is None:
        mgr.check_mcp()
        srv = next((s for s in mgr.mcp_servers() if s.extra.get("service") == service), None)
    if srv is None or srv.state != conn.CONNECTED:
        state = conn.describe_state(srv.state) if srv else "not configured in Claude"
        return (f"The {service.title()} MCP server is {state}. The user can say 'reconnect MCP' "
                f"or re-authorise the connector at claude.ai → Settings → Connectors. "
                f"Do not open a browser instead.")

    if player:
        try:
            player.write_log(f"SYS: Claude MCP · {service} — {task[:80]}")
        except Exception:
            pass
    mgr.report_task(state="PROCESSING", provider="claude", tool=f"mcp:{service}",
                    stage=f"Executing · Claude MCP ({service})")
    res = claude_brain.execute_claude_mcp_query(service, task)
    mgr.report_provider_result("claude", res["success"], res.get("error") or "", res.get("model", ""))
    mgr.report_task(state="COMPLETED" if res["success"] else "FAILED",
                    stage="Completed" if res["success"] else "Failed",
                    result=(res.get("output") or res.get("error") or "")[:300])
    if not res["success"]:
        return f"Claude could not complete the {service} task: {res.get('error')}"
    out = res.get("output") or "Done."
    denied = sorted({d.split("__")[-1] for d in res.get("permission_denials") or []})
    if denied:
        out += ("\n(Not permitted through Claude without the user's own review: "
                + ", ".join(denied) + ".)")
    return out


TOOL = {
    "name": "claude_mcp",
    "description": (
        "Runs a task on the user's Gmail, Google Drive, Claude Docs or Gamma through "
        "Claude's connected MCP servers. Use for reading, searching or summarising email, "
        "creating email drafts, finding or reading Drive files, reading Claude Docs, and "
        "finding Gamma presentations. It checks Claude and the MCP server first and says "
        "what is disconnected. Sending, deleting, trashing and sharing are not permitted "
        "here. Never use a browser for these services."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "service": {"type": "STRING", "description": "gmail | drive | docs | gamma"},
            "task": {"type": "STRING",
                     "description": "The full task in the user's words, e.g. 'summarise my 5 newest unread emails'"},
        },
        "required": ["service", "task"],
    },
    "handler": claude_mcp,
}
