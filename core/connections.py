"""
core/connections.py — what is actually usable right now: Claude, Gemini, MCP.

Every status the interface shows and every routing decision the command router
makes reads from here, so there is one answer to "is Claude connected?".

THE RULE
    CONNECTED means a real request succeeded recently — a probe, or a real task.
    Being signed in, having an API key, or a process running is reported as
    READY / CONFIGURED, never as CONNECTED. Nothing here has a hopeful default.

COST
    Each check is the cheapest thing that proves the claim:
      Claude  quick  `claude auth status`              ~0.2 s, no API usage
              deep   one Haiku request + MCP init       ~4 s, ~0.0002 USD-eq.
      Gemini  quick  API key present
              deep   `models.get`                       ~1 s, no generation quota
              live   reported by main.py's session loop
      MCP            `claude mcp list` (real per-server health)   ~2 s
    Automatic checks back off after failures; a button press always checks.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Optional

if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).resolve().parent.parent

# ── States ────────────────────────────────────────────────────────────────────
NOT_CONFIGURED = "NOT CONFIGURED"
CHECKING       = "CHECKING"
READY          = "READY"          # configured + authenticated, not yet verified
CONNECTED      = "CONNECTED"      # a real request succeeded recently
LIMITED        = "LIMITED"        # provider reachable but in a usage limit
DISCONNECTED   = "DISCONNECTED"
ERROR          = "ERROR"

USABLE = (CONNECTED, READY)

_PHRASES = {CONNECTED: "connected", READY: "ready but not yet verified", CHECKING: "still being checked",
            LIMITED: "in a usage limit", DISCONNECTED: "disconnected", ERROR: "failing",
            NOT_CONFIGURED: "not configured"}


def describe_state(state: str) -> str:
    """'The Gmail MCP server is <phrase>' — readable, for messages."""
    return _PHRASES.get(state, (state or "unknown").lower())

# A CONNECTED state older than this is shown as READY until revalidated.
VERIFIED_TTL = 30 * 60

# ── Routing settings (persisted in config/api_keys.json under "ai_routing") ──
_ROUTING_DEFAULTS = {
    # Order in which providers are checked for typed commands and AI tasks.
    "provider_order": ["claude", "gemini"],
    # When the preferred provider fails, may the other one be used? The
    # substitution is always announced. An explicit "use Claude" is never
    # substituted regardless of this setting.
    "allow_provider_fallback": True,
    # "ask"           — browser tools need explicit browser wording in the
    #                    request, or the user confirms on the HUD.
    # "explicit_only" — browser tools run only on explicit browser wording.
    "browser_policy": "ask",
    # Model alias for real Claude tasks ("" = the account's default).
    "claude_model": "",
    # "subscription" — Claude Code uses the claude.ai login; any
    #                  ANTHROPIC_API_KEY in the environment is ignored.
    # "api_key"      — let ANTHROPIC_API_KEY from the environment be used.
    "claude_auth": "subscription",
}


def routing_settings() -> dict:
    data = {}
    try:
        cfg = json.loads((_BASE / "config" / "api_keys.json").read_text(encoding="utf-8"))
        data = cfg.get("ai_routing") or {}
    except Exception:
        pass
    merged = dict(_ROUTING_DEFAULTS)
    merged.update({k: v for k, v in data.items() if k in _ROUTING_DEFAULTS})
    order = [p for p in merged.get("provider_order") or [] if p in ("claude", "gemini")]
    merged["provider_order"] = order or list(_ROUTING_DEFAULTS["provider_order"])
    if merged.get("browser_policy") not in ("ask", "explicit_only"):
        merged["browser_policy"] = "ask"
    return merged


def save_routing_settings(**values) -> dict:
    from memory.config_manager import load_api_keys, _patch_config
    current = (load_api_keys() or {}).get("ai_routing") or {}
    current.update({k: v for k, v in values.items() if k in _ROUTING_DEFAULTS})
    _patch_config(ai_routing=current)
    return routing_settings()


# ── Status records ────────────────────────────────────────────────────────────

@dataclass
class ServiceStatus:
    key: str
    label: str
    kind: str                       # "provider" | "mcp"
    configured: bool = False
    state: str = CHECKING
    integration: str = ""
    model: str = ""
    detail: str = ""
    last_ok: float = 0.0
    last_check: float = 0.0
    last_error: str = ""
    tools: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.state in USABLE

    @property
    def verified(self) -> bool:
        return self.state == CONNECTED

    def to_dict(self) -> dict:
        d = asdict(self)
        d["usable"] = self.usable
        d["verified"] = self.verified
        return d


class ConnectionManager:
    """Thread-safe registry of provider and MCP health."""

    QUICK_MIN_INTERVAL = 20.0
    MCP_MIN_INTERVAL = 60.0

    def __init__(self):
        self._lock = threading.RLock()
        self._listeners: list[Callable[[dict], None]] = []
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="conn-check")
        self._inflight: set[str] = set()
        self._fail_streak: dict[str, int] = {}
        self._services: dict[str, ServiceStatus] = {
            "claude": ServiceStatus("claude", "Claude", "provider",
                                    integration="Claude Code CLI"),
            "gemini": ServiceStatus("gemini", "Gemini", "provider",
                                    integration="Google GenAI API"),
        }
        self._mcp: dict[str, ServiceStatus] = {}
        self._mcp_error = ""
        self._live = {"connected": False, "model": "", "error": "", "since": 0.0}
        self._task = {"state": "IDLE", "provider": "", "tool": "", "stage": "",
                      "result": "", "updated": 0.0}
        self._seed_from_cache()

    # ── listeners ────────────────────────────────────────────────────────
    def subscribe(self, callback: Callable[[dict], None]) -> None:
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def _emit(self) -> None:
        snap = self.snapshot()
        for cb in list(self._listeners):
            try:
                cb(snap)
            except Exception:
                pass

    # ── reads ────────────────────────────────────────────────────────────
    def get(self, key: str) -> ServiceStatus:
        with self._lock:
            if key.startswith("mcp:"):
                return self._mcp.get(key) or ServiceStatus(key, key[4:], "mcp",
                                                           state=DISCONNECTED)
            svc = self._services[key]
            self._age(svc)
            return svc

    def mcp_servers(self) -> list[ServiceStatus]:
        with self._lock:
            return sorted(self._mcp.values(), key=lambda s: s.label)

    def mcp_tools(self) -> list[str]:
        with self._lock:
            out = []
            for s in self._mcp.values():
                out.extend(s.tools)
            return sorted(set(out))

    def live_status(self) -> dict:
        with self._lock:
            return dict(self._live)

    def task_status(self) -> dict:
        with self._lock:
            return dict(self._task)

    def snapshot(self) -> dict:
        with self._lock:
            for s in self._services.values():
                self._age(s)
            mcp = [s.to_dict() for s in sorted(self._mcp.values(), key=lambda s: s.label)]
            connected = [m for m in mcp if m["state"] == CONNECTED]
            return {
                "claude": self._services["claude"].to_dict(),
                "gemini": self._services["gemini"].to_dict(),
                "gemini_live": dict(self._live),
                "mcp": mcp,
                "mcp_summary": {
                    "configured": len(mcp), "connected": len(connected),
                    "tools": sum(len(m["tools"]) for m in mcp),
                    "error": self._mcp_error,
                },
                "active_provider": self.active_provider(),
                "routing": routing_settings(),
                "task": dict(self._task),
            }

    def active_provider(self) -> str:
        """The provider the router would pick first right now, or ''."""
        with self._lock:
            for p in routing_settings()["provider_order"]:
                if p == "gemini" and self._live["connected"]:
                    return "gemini"
                if self._services[p].usable:
                    return p
            return ""

    def _age(self, svc: ServiceStatus) -> None:
        if svc.state == CONNECTED and svc.last_ok and time.time() - svc.last_ok > VERIFIED_TTL:
            svc.state = READY
            svc.detail = "Signed in; last verified over 30 min ago."

    # ── writes from the running app ──────────────────────────────────────
    def report_live(self, connected: bool, model: str = "", error: str = "") -> None:
        """Called by main.py's Gemini Live loop on connect / disconnect."""
        with self._lock:
            was = self._live["connected"]
            self._live.update(connected=bool(connected), model=model or self._live["model"],
                              error=error[:300] if error else ("" if connected else self._live["error"]))
            if connected and not was:
                self._live["since"] = time.time()
            g = self._services["gemini"]
            if connected:
                g.configured = True
                g.state = CONNECTED
                g.last_ok = time.time()
                g.last_error = ""
                g.model = (model or g.model).removeprefix("models/")
                g.detail = "Live voice session active."
        self._emit()

    def report_task(self, **fields) -> None:
        with self._lock:
            self._task.update(fields, updated=time.time())
        self._emit()

    def report_provider_result(self, key: str, ok: bool, error: str = "",
                               model: str = "") -> None:
        """A real task outcome is a health check; record it."""
        with self._lock:
            svc = self._services.get(key)
            if not svc:
                return
            svc.last_check = time.time()
            if ok:
                svc.state, svc.last_ok, svc.last_error = CONNECTED, time.time(), ""
                if model:
                    svc.model = model
            else:
                low = (error or "").lower()
                svc.state = LIMITED if ("limit" in low or "quota" in low or "429" in low) else ERROR
                svc.last_error = (error or "failed")[:300]
        self._emit()

    # ── checks ───────────────────────────────────────────────────────────
    def check_claude(self, deep: bool = False) -> ServiceStatus:
        from core import claude_brain as cb
        svc = self._services["claude"]
        self._set(svc, state=CHECKING if deep else svc.state, last_check=time.time())
        cli = cb.find_claude_cli()
        if not cli:
            return self._set(svc, configured=False, state=NOT_CONFIGURED,
                             integration="Claude Code CLI (not installed)",
                             last_error="Claude Code CLI was not found on this PC.",
                             detail="Install Claude Code, then run: claude auth login")
        auth = cb.get_claude_auth()
        if not auth.get("loggedIn"):
            return self._set(svc, configured=True, state=DISCONNECTED,
                             integration="Claude Code CLI",
                             last_error=auth.get("error") or "Not signed in.",
                             detail="Run in a terminal: claude auth login")
        plan = auth.get("subscriptionType") or ""
        method = auth.get("authMethod") or ""
        integ = "Claude Code CLI" + (f" · {method}" if method else "") + (f" ({plan})" if plan else "")
        cached = cb.load_cached_status()
        if not deep:
            if cached.get("limit_hit") and (cached.get("reset_timestamp") or 0) > time.time():
                return self._set(svc, configured=True, state=LIMITED, integration=integ,
                                 last_error=cached.get("last_error") or "Usage limit reached.",
                                 detail=f"Resets {cached.get('reset_str') or 'soon'}")
            fresh = time.time() - float(cached.get("last_ok") or 0) < VERIFIED_TTL
            return self._set(svc, configured=True,
                             state=CONNECTED if (fresh and cached.get("verified")) else READY,
                             integration=integ, model=_task_model(cached.get("model") or svc.model),
                             last_ok=float(cached.get("last_ok") or 0),
                             detail="Verified recently." if fresh and cached.get("verified")
                                    else "Signed in; not verified with a request yet.")
        st = cb.refresh_status()
        if st.get("verified"):
            self._ingest_probe_mcp(st)
            # The probe runs on Haiku; that is not the model tasks use.
            task_model = _task_model(svc.model)
            return self._set(svc, configured=True, state=CONNECTED, integration=integ,
                             model=task_model, last_ok=time.time(), last_error="",
                             detail=f"Test request succeeded ({st.get('model') or 'probe'}).")
        if st.get("limit_hit"):
            return self._set(svc, configured=True, state=LIMITED, integration=integ,
                             last_error=st.get("last_error") or "Usage limit reached.",
                             detail=f"Resets {st.get('reset_str') or 'soon'}")
        return self._set(svc, configured=True, state=ERROR, integration=integ,
                         last_error=st.get("last_error") or "Probe failed.",
                         detail="Signed in, but the test request failed.")

    def check_gemini(self, deep: bool = False) -> ServiceStatus:
        from core import gemini
        svc = self._services["gemini"]
        key = gemini.api_key(refresh=True)
        if not key:
            return self._set(svc, configured=False, state=NOT_CONFIGURED,
                             last_error="No Gemini API key in config/api_keys.json.",
                             detail="Add a key in the setup screen.")
        if self._live["connected"] and not deep:
            return self._set(svc, configured=True, state=CONNECTED,
                             detail="Live voice session active.")
        if not deep:
            fresh = svc.last_ok and time.time() - svc.last_ok < VERIFIED_TTL
            return self._set(svc, configured=True,
                             state=CONNECTED if fresh else READY,
                             detail=svc.detail if fresh else "API key configured; not verified yet.")
        self._set(svc, state=CHECKING, last_check=time.time())
        try:
            cl = gemini.client(timeout_ms=15000)
            m = cl.models.get(model="gemini-2.5-flash")
            live = self._live["connected"]
            return self._set(svc, configured=True, state=CONNECTED, last_ok=time.time(),
                             last_error="", model=self._live["model"] or str(getattr(m, "name", "")),
                             detail="API verified" + (" · Live session active." if live
                                                      else " · Live session not connected."))
        except Exception as exc:
            msg = str(exc)
            low = msg.lower()
            state = LIMITED if gemini.is_quota_error(msg) else (
                ERROR if ("api key" in low or "permission" in low or "403" in msg) else DISCONNECTED)
            return self._set(svc, configured=True, state=state, last_error=msg[:300],
                             detail="Check the API key and network.")

    def check_mcp(self) -> list[ServiceStatus]:
        from core import claude_brain as cb
        if not cb.find_claude_cli():
            with self._lock:
                self._mcp = {}
                self._mcp_error = "Claude Code CLI not found — MCP servers are reached through it."
            self._emit()
            return []
        try:
            servers = cb.mcp_list()
        except Exception as exc:
            with self._lock:
                self._mcp_error = f"claude mcp list failed: {exc}"[:300]
                for s in self._mcp.values():
                    s.state, s.last_error, s.last_check = ERROR, str(exc)[:300], time.time()
            print(f"[Connections] {self._mcp_error}")
            self._emit()
            return self.mcp_servers()
        cached_tools = cb.load_cached_status().get("mcp_tools") or []
        with self._lock:
            fresh = {}
            for srv in servers:
                key = "mcp:" + srv["name"]
                prev = self._mcp.get(key)
                state = {"connected": CONNECTED, "needs_auth": DISCONNECTED,
                         "failed": ERROR}.get(srv["status"], ERROR)
                tools = _tools_for_server(srv["name"], cached_tools) or (prev.tools if prev else [])
                fresh[key] = ServiceStatus(
                    key=key, label=srv["name"].removeprefix("claude.ai "), kind="mcp",
                    configured=True, state=state, integration="via Claude Code",
                    detail=srv["url"], last_check=time.time(),
                    last_ok=time.time() if state == CONNECTED else (prev.last_ok if prev else 0),
                    last_error="" if state == CONNECTED else srv["raw"][:200],
                    tools=tools,
                    extra={"service": cb.MCP_SERVICE_OF_SERVER.get(srv["name"], ""),
                           "url": srv["url"]})
            self._mcp = fresh
            self._mcp_error = "" if servers else "Claude Code has no MCP servers configured."
        self._emit()
        return self.mcp_servers()

    def _ingest_probe_mcp(self, st: dict) -> None:
        tools = st.get("mcp_tools") or []
        with self._lock:
            for srv in st.get("mcp_servers") or []:
                key = "mcp:" + srv["name"]
                s = self._mcp.get(key) or ServiceStatus(
                    key, srv["name"].removeprefix("claude.ai "), "mcp", configured=True,
                    integration="via Claude Code")
                s.tools = _tools_for_server(srv["name"], tools)
                if srv.get("status") == "connected":
                    s.state, s.last_ok = CONNECTED, time.time()
                s.last_check = time.time()
                from core import claude_brain as cb
                s.extra.setdefault("service", cb.MCP_SERVICE_OF_SERVER.get(srv["name"], ""))
                self._mcp[key] = s

    def check_all(self, deep: bool = False) -> None:
        """Run every check off-thread; listeners receive the results."""
        self.check_async("claude", deep=deep)
        self.check_async("gemini", deep=deep)
        self.check_async("mcp")

    def check_async(self, which: str, deep: bool = False,
                    done: Optional[Callable[[], None]] = None) -> bool:
        tag = f"{which}:{int(deep)}"
        with self._lock:
            if tag in self._inflight:
                return False
            self._inflight.add(tag)

        def work():
            try:
                if which == "claude":
                    self.check_claude(deep=deep)
                elif which == "gemini":
                    self.check_gemini(deep=deep)
                elif which == "mcp":
                    self.check_mcp()
            except Exception as exc:
                print(f"[Connections] {which} check failed: {exc}")
            finally:
                with self._lock:
                    self._inflight.discard(tag)
                if done:
                    try:
                        done()
                    except Exception:
                        pass
        self._pool.submit(work)
        return True

    def ensure_fresh(self, key: str) -> ServiceStatus:
        """Before executing on a provider: a cheap synchronous recheck when the
        status is older than QUICK_MIN_INTERVAL. Never a paid request."""
        svc = self.get(key)
        if time.time() - svc.last_check > self.QUICK_MIN_INTERVAL:
            if key == "claude":
                self.check_claude(deep=False)
            elif key == "gemini":
                self.check_gemini(deep=False)
        return self.get(key)

    def _set(self, svc: ServiceStatus, **fields) -> ServiceStatus:
        with self._lock:
            for k, v in fields.items():
                setattr(svc, k, v)
            if "state" in fields and fields["state"] not in (CHECKING,):
                svc.last_check = fields.get("last_check", time.time())
                if fields["state"] in (ERROR, DISCONNECTED, LIMITED):
                    self._fail_streak[svc.key] = self._fail_streak.get(svc.key, 0) + 1
                elif fields["state"] in USABLE:
                    self._fail_streak[svc.key] = 0
        self._emit()
        return svc

    def _seed_from_cache(self) -> None:
        """Start from what is known, marked unverified — never from a guess."""
        try:
            from core import claude_brain as cb
            st = cb.load_cached_status()
            svc = self._services["claude"]
            svc.configured = cb.find_claude_cli() is not None
            svc.state = CHECKING if svc.configured else NOT_CONFIGURED
            svc.model = _task_model(st.get("model") or "")
            svc.last_ok = float(st.get("last_ok") or 0)
            tools = st.get("mcp_tools") or []
            for srv in st.get("mcp_servers") or []:
                key = "mcp:" + srv["name"]
                self._mcp[key] = ServiceStatus(
                    key, srv["name"].removeprefix("claude.ai "), "mcp", configured=True,
                    state=CHECKING, integration="via Claude Code",
                    tools=_tools_for_server(srv["name"], tools),
                    extra={"service": cb.MCP_SERVICE_OF_SERVER.get(srv["name"], "")})
        except Exception:
            pass
        try:
            from core import gemini
            g = self._services["gemini"]
            g.configured = bool(gemini.api_key())
            g.state = CHECKING if g.configured else NOT_CONFIGURED
        except Exception:
            pass

    def errors(self) -> list[dict]:
        """Every current error, for the 'View connection errors' control."""
        out = []
        for s in list(self._services.values()) + self.mcp_servers():
            if s.last_error:
                out.append({"service": s.label, "state": s.state, "error": s.last_error,
                            "at": s.last_check})
        if self._mcp_error:
            out.append({"service": "MCP", "state": ERROR, "error": self._mcp_error, "at": time.time()})
        if self._live.get("error"):
            out.append({"service": "Gemini Live", "state": "DISCONNECTED" if not self._live["connected"] else "CONNECTED",
                        "error": self._live["error"], "at": time.time()})
        return out


def _task_model(model: str) -> str:
    """The model real tasks run on. A probe model (Haiku) is never shown as it."""
    configured = routing_settings().get("claude_model") or ""
    if not model or ("haiku" in model.lower() and "haiku" not in configured.lower()):
        return configured or "account default"
    return model


def _tools_for_server(server_name: str, tools: list[str]) -> list[str]:
    prefix = "mcp__" + re.sub(r"[^A-Za-z0-9_]", "_", server_name) + "__"
    return sorted(t for t in tools if t.startswith(prefix))


_manager: Optional[ConnectionManager] = None
_manager_lock = threading.Lock()


def manager() -> ConnectionManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = ConnectionManager()
        return _manager


def reset_for_tests() -> None:
    global _manager
    with _manager_lock:
        _manager = None
