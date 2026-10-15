"""
core/command_router.py — every typed command takes the same route.

    USER COMMAND
      → manual connection command?  (check / connect / reconnect / tools / chrome)
      → CHECK CLAUDE   (cheap revalidation if the status is stale)
      → CHECK GEMINI   (only if needed)
      → CHECK MCP / NATIVE TOOLS for what the request needs
      → EXECUTE through the first provider that can actually do it
      → REPORT the result, with the provider and stages that ran
    If nothing can do it: STOP, say what is disconnected and how to fix it.
    A browser is never a fallback — see core/browser_policy.py.

Provider order comes from the routing settings ("claude" then "gemini" by
default). Naming a provider ("use Claude …", "pitaj Gemini …") pins it: if that
provider is down the router reports it instead of substituting the other.

Voice still runs on the Gemini Live session (only it hears audio). Its tool
calls pass through the same capability and browser checks in main.py.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from core import browser_policy
from core import connections as conn

# ── Intent detection ──────────────────────────────────────────────────────────

_MANUAL = [
    ("errors",    re.compile(r"\b(view|show|prika(z|ž)i)\b.*\b(connection\s+)?(errors?|gre(s|š)k\w*)\b", re.I)),
    ("mcp_tools", re.compile(r"\b(show|list|view|prika(z|ž)i|izlistaj)\b.*\bmcp\b.*\b(tools?|alat\w*)\b|\bavailable\s+mcp\s+tools\b|\bmcp\s+tools\b", re.I)),
    ("check",     re.compile(r"\b(check|test|verify|proveri|provjeri|testiraj)\b.*\b(ai\s+)?(connections?|konekcij\w*|veze|povezanost)\b|\bai\s+connections?\b|\bstatus\s+(konekcij\w*|veza)\b", re.I)),
    ("claude",    re.compile(r"\b(re)?connect\s+claude\b|\b(pove(z|ž)i|ponovo\s+pove(z|ž)i)\s+claude\w*\b", re.I)),
    ("gemini",    re.compile(r"\b(re)?connect\s+gemini\b|\b(pove(z|ž)i|ponovo\s+pove(z|ž)i)\s+gemini\w*\b", re.I)),
    ("mcp",       re.compile(r"\b(re)?connect\s+(the\s+)?mcp\b|\b(pove(z|ž)i|ponovo\s+pove(z|ž)i)\s+mcp\b", re.I)),
    ("chrome",    re.compile(r"\bopen\s+(google\s+)?chrome(\s+manually)?\b|\botvori\s+(google\s+)?(chrome|hrom\w*|krom\w*)\b", re.I)),
]

_PROVIDER = [
    ("claude", re.compile(r"\b(using|with|via|through|ask|use)\s+claude\b|\b(pitaj|preko|koristi|kroz)\s+claude\w*\b|^\s*claude\s*[:,]", re.I)),
    ("gemini", re.compile(r"\b(using|with|via|through|ask|use)\s+gemini\b|\b(pitaj|preko|koristi|kroz)\s+gemini\w*\b|^\s*gemini\s*[:,]", re.I)),
]

_MCP_SERVICE = [
    ("gmail", re.compile(r"\bgmail\b|\be-?mail\w*\b|\binbox\b|\bmejl\w*\b|\bimejl\w*\b|\bpo(s|š)t[aeiu]\b|\bposta\b|\bsandu(c|č)\w*\b", re.I)),
    ("drive", re.compile(r"\bgoogle\s+drive\b|\bdrive\b|\bgdrive\b|\bdrajv\w*\b", re.I)),
    ("docs",  re.compile(r"\bclaude\s+docs?\b", re.I)),
    ("gamma", re.compile(r"\bgamma\b|\bprezentacij\w*\b|\bpresentation\b|\bslide\s*deck\b", re.I)),
]


def detect_manual(text: str) -> Optional[str]:
    for name, rx in _MANUAL:
        if rx.search(text or ""):
            return name
    return None


def detect_provider(text: str) -> Optional[str]:
    for name, rx in _PROVIDER:
        if rx.search(text or ""):
            return name
    return None


def detect_mcp_service(text: str) -> Optional[str]:
    for name, rx in _MCP_SERVICE:
        if rx.search(text or ""):
            return name
    return None


# ── Results ───────────────────────────────────────────────────────────────────

@dataclass
class RouteResult:
    ok: bool
    path: str                       # manual | claude_mcp | claude_plan | gemini_live | gemini_plan | blocked
    provider: str = ""
    reply: str = ""
    tool: str = ""
    tool_result: str = ""
    error: str = ""
    stages: list = field(default_factory=list)
    # True when the provider itself failed (auth, limit, network, bad reply),
    # as opposed to a missing capability. Only these may fall back.
    provider_failed: bool = False

    def chat_text(self) -> str:
        parts = []
        if self.reply:
            parts.append(self.reply.strip())
        if self.tool_result and self.tool_result.strip() != self.reply.strip():
            parts.append(self.tool_result.strip())
        if self.error and not self.ok:
            parts.append(self.error.strip())
        return "\n".join(p for p in parts if p)


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["reply", "tool", "mcp"]},
        "reply": {"type": "string"},
        "tool": {"type": "string"},
        "arguments": {"type": "object", "additionalProperties": True},
        "service": {"type": "string", "enum": ["gmail", "drive", "docs", "gamma", ""]},
        "task": {"type": "string"},
    },
    "required": ["action", "reply"],
    "additionalProperties": False,
}

PLANNER_SYSTEM = (
    "You are the command planner inside ULTRON, a desktop AI assistant for "
    "{user}. Decide how to handle the user's request:\n"
    "- action=reply: answer directly in 'reply' (conversation, knowledge, reasoning, writing, code).\n"
    "- action=tool: run ONE of ULTRON's local tools listed below; put its name in 'tool' and "
    "its arguments in 'arguments'; put a one-sentence acknowledgement in 'reply'.\n"
    "- action=mcp: the request needs the user's Gmail, Google Drive, Claude Docs or Gamma; set "
    "'service' and restate the task fully in 'task'.\n"
    "Rules: never choose a tool that opens or navigates a web browser (browser_control, "
    "youtube_video, flight_finder, computer_control navigate/search, open_app of a browser) "
    "unless the user explicitly asked for a browser, website, tab, URL or YouTube. "
    "Do not invent tools or arguments. Reply in the user's language, concisely.\n\n"
    "LOCAL TOOLS:\n{tools}"
)


def compact_catalog(declarations: list[dict], limit_chars: int = 26000) -> str:
    """Tool list for the planner: name, purpose, and each parameter with its
    own (trimmed) description, so argument values are chosen, not guessed.
    Required parameters are marked with *."""
    lines = []
    for d in declarations:
        name = d.get("name", "")
        desc = re.sub(r"\s+", " ", str(d.get("description", ""))).strip()
        first = re.split(r"(?<=[.!?])\s", desc, maxsplit=1)[0][:180]
        params = (d.get("parameters") or {})
        props = params.get("properties") or {}
        req = set(params.get("required") or [])
        lines.append(f"- {name}: {first}")
        for p, spec in props.items():
            pdesc = re.sub(r"\s+", " ", str((spec or {}).get("description", ""))).strip()[:420]
            enum = (spec or {}).get("enum")
            if enum:
                pdesc = (pdesc + " " if pdesc else "") + "one of " + " | ".join(map(str, enum))[:300]
            lines.append(f"    {p}{'*' if p in req else ''}: {pdesc}")
    out = "\n".join(lines)
    return out[:limit_chars]


class CommandRouter:
    def __init__(self,
                 tool_runner: Callable[[str, dict], str],
                 tool_catalog: Callable[[], list],
                 live_sender: Callable[[str], bool],
                 live_reconnect: Optional[Callable[[], None]] = None,
                 user_name: str = "the user",
                 recent_context: Optional[Callable[[], str]] = None,
                 manager: Optional[conn.ConnectionManager] = None):
        self._run_tool = tool_runner
        self._catalog = tool_catalog
        self._send_live = live_sender
        self._live_reconnect = live_reconnect
        self._user = user_name or "the user"
        self._context = recent_context or (lambda: "")
        self._mgr = manager or conn.manager()
        self._busy = threading.Lock()

    # ── public ───────────────────────────────────────────────────────────
    def route(self, text: str) -> RouteResult:
        text = (text or "").strip()
        browser_policy.note_user_text(text)
        stages: list[str] = []
        try:
            res = self._route(text, stages)
        except Exception as exc:
            res = RouteResult(False, "blocked", error=f"Routing failed: {exc}")
        res.stages = stages
        self._mgr.report_task(state="COMPLETED" if res.ok else "FAILED",
                              provider=res.provider, tool=res.tool,
                              stage="Completed" if res.ok else "Failed",
                              result=(res.chat_text() or "")[:300])
        return res

    # ── internals ────────────────────────────────────────────────────────
    def _stage(self, stages: list, name: str, provider: str = "", tool: str = "") -> None:
        stages.append(name)
        self._mgr.report_task(state="PROCESSING", stage=name, provider=provider, tool=tool)

    def _route(self, text: str, stages: list) -> RouteResult:
        manual = detect_manual(text)
        if manual:
            self._stage(stages, "Manual command")
            return self.manual(manual, text)

        explicit = detect_provider(text)
        settings = conn.routing_settings()
        order = [explicit] if explicit else list(settings["provider_order"])
        service = detect_mcp_service(text)
        mcp_wanted = service and self._mcp_preferred(service)

        blockers: list[str] = []
        for provider in order:
            self._stage(stages, f"Checking {provider.title()}", provider)
            if provider == "claude":
                st = self._mgr.ensure_fresh("claude")
                if not st.usable:
                    blockers.append(self._describe_down(st))
                    continue
                if mcp_wanted:
                    self._stage(stages, f"Checking MCP · {service}", "claude")
                    srv = self._mcp_server_for(service)
                    if srv is None or srv.state != conn.CONNECTED:
                        why = (f"The {service.title()} MCP server is "
                               f"{(conn.describe_state(srv.state) if srv else 'not configured in Claude')}.")
                        blockers.append(why + " Say 'reconnect MCP' to recheck, or re-authorise "
                                        "the connector at claude.ai → Settings → Connectors.")
                        continue
                    res = self._exec_claude_mcp(service, text, stages)
                else:
                    res = self._exec_plan("claude", text, stages)
                if explicit and not res.ok and res.provider_failed:
                    res.error += " You asked for Claude, so I did not substitute another provider."
                if res.ok or not res.provider_failed or explicit or not settings["allow_provider_fallback"]:
                    return res
                blockers.append(f"Claude failed during execution: {res.error}")
                continue

            if provider == "gemini":
                live = self._mgr.live_status()
                if mcp_wanted:
                    blockers.append(f"{service.title()} is reached through Claude's MCP connector, "
                                    "which Gemini cannot use.")
                    continue
                if live.get("connected"):
                    self._stage(stages, "Executing · Gemini Live", "gemini")
                    if self._send_live(text):
                        return RouteResult(True, "gemini_live", "gemini",
                                           reply="")
                st = self._mgr.ensure_fresh("gemini")
                if not st.usable:
                    blockers.append(self._describe_down(st, live=live))
                    continue
                res = self._exec_plan("gemini", text, stages)
                if blockers and res.ok:
                    res.reply = (f"(Using Gemini — {blockers[-1]})\n" + res.reply).strip()
                if explicit and not res.ok and res.provider_failed:
                    res.error += " You asked for Gemini, so I did not substitute another provider."
                if res.ok or not res.provider_failed or explicit or not settings["allow_provider_fallback"]:
                    return res
                blockers.append(f"Gemini failed during execution: {res.error}")
                continue

        self._stage(stages, "Blocked")
        return RouteResult(False, "blocked", error=self._blocked_text(order, blockers, explicit))

    def _mcp_preferred(self, service: str) -> bool:
        """Honour the saved Claude-vs-own-account preference per service."""
        try:
            from core import claude_brain
            pref = claude_brain.get_connection_preference(service)
        except Exception:
            pref = "ask"
        if pref == "user":
            return False
        if pref == "claude":
            return True
        native = {"gmail": "ultron_mail_manager", "drive": "google_drive"}.get(service)
        names = {d.get("name") for d in self._catalog()}
        return native not in names

    def _mcp_server_for(self, service: str) -> Optional[conn.ServiceStatus]:
        for s in self._mgr.mcp_servers():
            if s.extra.get("service") == service:
                return s
        return None

    def _describe_down(self, st: conn.ServiceStatus, live: Optional[dict] = None) -> str:
        name = st.label
        if st.state == conn.NOT_CONFIGURED:
            msg = f"{name} is not configured. {st.detail or st.last_error}"
        elif st.state == conn.CHECKING:
            msg = f"{name} is still being checked; try again in a few seconds."
        elif st.state == conn.LIMITED:
            msg = f"{name} is in a usage limit ({st.detail or st.last_error})."
        else:
            msg = f"{name} is not connected ({st.last_error or st.state.lower()})."
            if st.key == "claude":
                msg += " To reconnect, run in a terminal: claude auth login — or say 'connect Claude'."
            if st.key == "gemini":
                if live is not None and not live.get("connected"):
                    msg += " The Gemini Live voice session is also down."
                msg += " Say 'reconnect Gemini' to retry."
        return msg

    def _blocked_text(self, order: list, blockers: list, explicit: Optional[str]) -> str:
        head = "I cannot execute this through the configured AI providers."
        if explicit:
            head = (f"You asked for {explicit.title()}, and I will not substitute another "
                    f"provider without asking.")
        lines = [head] + [f"• {b}" for b in blockers]
        lines.append("Nothing was opened in a browser. Say 'check AI connections' for a full status.")
        return "\n".join(lines)

    # ── executors ────────────────────────────────────────────────────────
    def _exec_claude_mcp(self, service: str, text: str, stages: list) -> RouteResult:
        from core import claude_brain
        self._stage(stages, f"Executing · Claude MCP ({service})", "claude", f"mcp:{service}")
        res = claude_brain.execute_claude_mcp_query(service, text)
        self._mgr.report_provider_result("claude", res["success"], res.get("error") or "",
                                         res.get("model", ""))
        note = ""
        if res.get("permission_denials"):
            note = ("\n(Not permitted without your review: "
                    + ", ".join(sorted({d.split('__')[-1] for d in res['permission_denials']})) + ")")
        if res["success"]:
            return RouteResult(True, "claude_mcp", "claude", reply=res["output"] + note,
                               tool=f"mcp:{service}")
        return RouteResult(False, "claude_mcp", "claude", tool=f"mcp:{service}", provider_failed=True,
                           error=f"Claude could not complete the {service} task: {res.get('error')}")

    def _planner_prompt(self, text: str) -> tuple[str, str]:
        system = PLANNER_SYSTEM.format(user=self._user, tools=compact_catalog(self._catalog()))
        ctx = (self._context() or "").strip()
        prompt = (f"Recent conversation:\n{ctx}\n\n" if ctx else "") + f"User request: {text}"
        return system, prompt

    def _plan(self, provider: str, text: str) -> tuple[Optional[dict], str]:
        system, prompt = self._planner_prompt(text)
        if provider == "claude":
            from core import claude_brain
            res = claude_brain.ask_claude(prompt, system=system, timeout=120,
                                          json_schema=PLAN_SCHEMA)
            self._mgr.report_provider_result("claude", res["success"], res.get("error") or "",
                                             res.get("model", ""))
            plan = res.get("structured")
            if res["success"] and not isinstance(plan, dict):
                try:
                    plan = json.loads(res.get("output") or "")
                except Exception:
                    plan = {"action": "reply", "reply": res.get("output") or ""}
            return (plan if res["success"] else None), (res.get("error") or "")
        from core import gemini
        instruction = (system + "\n\nRespond with ONLY a JSON object with keys action, reply, "
                       "tool, arguments, service, task.\n\n" + prompt)
        plan = gemini.as_json(instruction, tier=gemini.FAST, timeout_ms=45000, default=None)
        ok = isinstance(plan, dict) and plan.get("action") in ("reply", "tool", "mcp")
        self._mgr.report_provider_result("gemini", ok, "" if ok else "Gemini returned no usable plan.")
        return (plan if ok else None), ("" if ok else "Gemini did not return a usable plan.")

    def _exec_plan(self, provider: str, text: str, stages: list) -> RouteResult:
        self._stage(stages, f"Planning · {provider.title()}", provider)
        plan, err = self._plan(provider, text)
        if plan is None:
            return RouteResult(False, f"{provider}_plan", provider, provider_failed=True,
                               error=f"{provider.title()} failed while planning: {err}")
        action = plan.get("action")
        reply = str(plan.get("reply") or "")
        if action == "reply":
            return RouteResult(True, f"{provider}_plan", provider, reply=reply)
        if action == "mcp":
            service = str(plan.get("service") or "")
            task = str(plan.get("task") or text)
            if provider != "claude":
                st = self._mgr.ensure_fresh("claude")
                if not st.usable:
                    return RouteResult(False, "blocked", provider,
                                       error=f"This needs {service.title()} through Claude's MCP "
                                             f"connector, but {self._describe_down(st)}")
            srv = self._mcp_server_for(service)
            if srv is None or srv.state != conn.CONNECTED:
                return RouteResult(False, "blocked", "claude",
                                   error=f"The {service.title() or 'requested'} MCP server is not "
                                         f"connected. Say 'reconnect MCP'.")
            return self._exec_claude_mcp(service, task, stages)
        tool = str(plan.get("tool") or "")
        args = plan.get("arguments") if isinstance(plan.get("arguments"), dict) else {}
        names = {d.get("name") for d in self._catalog()}
        self._stage(stages, f"Checking tool · {tool or '?'}", provider, tool)
        if tool not in names:
            return RouteResult(False, f"{provider}_plan", provider, tool=tool,
                               error=f"The required capability is not available: there is no "
                                     f"loaded tool named '{tool}'. {provider.title()} is connected, "
                                     f"but that does not make every action possible.")
        self._stage(stages, f"Executing · {tool}", provider, tool)
        result = self._run_tool(tool, args)
        failed = str(result).startswith(("[BROWSER_BLOCKED]", "Tool '", "Action '", "Unknown tool"))
        return RouteResult(not failed, f"{provider}_plan", provider, reply=reply, tool=tool,
                           tool_result=_humanise(result))

    # ── manual commands ──────────────────────────────────────────────────
    def manual(self, name: str, text: str = "") -> RouteResult:
        if name == "check":
            self.check_now(deep=True)
            return RouteResult(True, "manual", reply=self.status_text())
        if name == "claude":
            st = self._mgr.check_claude(deep=True)
            if st.state == conn.CONNECTED:
                return RouteResult(True, "manual", "claude",
                                   reply=f"Claude is connected ({st.integration}, {st.model}).")
            return RouteResult(False, "manual", "claude", error=self._describe_down(st))
        if name == "gemini":
            if self._live_reconnect:
                self._live_reconnect()
            st = self._mgr.check_gemini(deep=True)
            msg = (f"Gemini API: {st.state}. "
                   + ("Live session reconnect requested." if self._live_reconnect else ""))
            return RouteResult(st.usable, "manual", "gemini", reply=msg if st.usable else "",
                               error="" if st.usable else self._describe_down(st))
        if name == "mcp":
            servers = self._mgr.check_mcp()
            if self._mgr.get("claude").usable:
                self._mgr.check_claude(deep=True)
            return RouteResult(bool(servers), "manual", "claude", reply=self.mcp_text())
        if name == "mcp_tools":
            if not self._mgr.mcp_tools():
                self._mgr.check_claude(deep=True)
            return RouteResult(True, "manual", reply=self.mcp_text(with_tools=True))
        if name == "errors":
            errs = self._mgr.errors()
            if not errs:
                return RouteResult(True, "manual", reply="No connection errors recorded.")
            return RouteResult(True, "manual", reply="\n".join(
                f"• {e['service']} [{e['state']}]: {e['error']}" for e in errs))
        if name == "chrome":
            browser_policy.note_user_text(text or "open chrome")
            out = self._run_tool("open_app", {"app_name": "Chrome"})
            return RouteResult(not str(out).startswith("[BROWSER_BLOCKED]"), "manual",
                               tool="open_app", tool_result=_humanise(out))
        return RouteResult(False, "manual", error=f"Unknown manual command '{name}'.")

    def check_now(self, deep: bool = True) -> None:
        threads = [threading.Thread(target=self._mgr.check_claude, kwargs={"deep": deep}),
                   threading.Thread(target=self._mgr.check_gemini, kwargs={"deep": deep}),
                   threading.Thread(target=self._mgr.check_mcp)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=90)

    def status_text(self) -> str:
        snap = self._mgr.snapshot()
        c, g, live = snap["claude"], snap["gemini"], snap["gemini_live"]
        lines = [
            f"Claude: {c['state']} — {c['integration']}" + (f" · {c['model']}" if c['model'] else "")
            + (f" · {c['last_error']}" if c['last_error'] and not c['usable'] else ""),
            f"Gemini: {g['state']}" + (f" · {g['last_error']}" if g['last_error'] and not g['usable'] else "")
            + f" · Live session {'connected' if live['connected'] else 'not connected'}",
            self.mcp_text(),
            f"Active provider: {snap['active_provider'] or 'none'} "
            f"(order: {' → '.join(snap['routing']['provider_order'])})",
        ]
        return "\n".join(lines)

    def mcp_text(self, with_tools: bool = False) -> str:
        servers = self._mgr.mcp_servers()
        if not servers:
            return "MCP: no servers configured in Claude Code (or Claude CLI unavailable)."
        ok = sum(1 for s in servers if s.state == conn.CONNECTED)
        lines = [f"MCP: {ok}/{len(servers)} servers connected · {len(self._mgr.mcp_tools())} tools"]
        for s in servers:
            lines.append(f"  • {s.label}: {s.state}" + (f" — {s.last_error}" if s.last_error else "")
                         + (f" ({len(s.tools)} tools)" if s.tools else ""))
            if with_tools and s.tools:
                lines.append("      " + ", ".join(t.split("__")[-1] for t in s.tools))
        return "\n".join(lines)


def _humanise(result) -> str:
    """Tool results are often written as instructions for the voice model.
    Show the user the substance instead of the control text."""
    s = str(result or "").strip()
    if s.startswith("[BROWSER_BLOCKED]"):
        m = re.search(r"this would (.+?), and the user", s)
        what = m.group(1) if m else "use a browser"
        tail = (" A confirmation is on screen if you do want it." if "confirmation banner" in s
                else "")
        return (f"Not done: this would {what}, and you did not ask for a browser. "
                f"ULTRON does not open a browser as a fallback.{tail}")
    if s.startswith("[CONFIRMATION_PENDING]"):
        return "Waiting for your confirmation on screen."
    s = re.sub(r"^\[[A-Z_]+\]\s*", "", s)
    return s[:4000]


_router: Optional[CommandRouter] = None


def set_router(router: Optional[CommandRouter]) -> None:
    global _router
    _router = router


def get_router() -> Optional[CommandRouter]:
    return _router
