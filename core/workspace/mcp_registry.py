"""Every MCP server ULTRON can reach, with health that comes from real calls.

Three kinds of server:

    stdio    a local process (the built-in ultron-workspace server, or any
             server added to config/workspace/mcp_servers.json)
    http     a Streamable HTTP endpoint (optional bearer token kept in the
             encrypted credential store, referenced by name)
    claude   a connector of the signed-in Claude account (Gmail, Drive, …),
             reached through Claude Code with a read/draft-only tool list

A status is set only by something that happened: a handshake, a tool list, a
call. "connected" means the session is open and answered; nothing starts as
connected. Sessions live on one background asyncio loop; callers use the
synchronous API from any thread.
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from core.workspace import storage

CONNECTED, CONNECTING, AUTH, OFFLINE, ERROR, NOT_CONFIGURED, DISABLED = (
    "connected", "connecting", "auth_required", "offline", "error", "not_configured", "disabled")

BUILTIN_ID = "ultron-workspace"
CONNECT_TIMEOUT = 30.0


class MCPToolError(RuntimeError):
    def __init__(self, message: str, status: str = ERROR):
        super().__init__(message)
        self.status = status


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_config() -> list[dict]:
    return [{"id": BUILTIN_ID, "name": "ULTRON Workspace", "transport": "stdio",
             "command": sys.executable, "args": ["-m", "core.workspace.mcp_server"],
             "enabled": True, "builtin": True, "fallback": "inproc",
             "description": "Maps, routes, nearby places, accommodation search and exchange rates."}]


def load_config() -> list[dict]:
    path = storage.data_dir() / "mcp_servers.json"
    servers = default_config()
    if path.exists():
        try:
            extra = json.loads(path.read_text(encoding="utf-8"))
            for s in extra.get("servers", []):
                if not isinstance(s, dict) or not s.get("id") or s["id"] == BUILTIN_ID:
                    if isinstance(s, dict) and s.get("id") == BUILTIN_ID:
                        if "enabled" in s:
                            servers[0]["enabled"] = bool(s["enabled"])
                        if s.get("transport") in ("stdio", "inproc"):
                            servers[0]["transport"] = s["transport"]
                    continue
                if s.get("transport") not in ("stdio", "http"):
                    continue
                servers.append({"id": str(s["id"])[:64], "name": str(s.get("name") or s["id"])[:80],
                                "transport": s["transport"], "command": str(s.get("command", "")),
                                "args": [str(a) for a in s.get("args", [])][:40],
                                "url": str(s.get("url", "")), "auth_secret": str(s.get("auth_secret", "")),
                                "enabled": bool(s.get("enabled", True)), "builtin": False,
                                "description": str(s.get("description", ""))[:300]})
        except Exception as exc:
            print(f"[MCP] mcp_servers.json unreadable: {exc}", file=sys.stderr)
    return servers


def save_enabled(server_id: str, enabled: bool) -> None:
    path = storage.data_dir() / "mcp_servers.json"
    data = {"servers": []}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8")) or {"servers": []}
        except Exception:
            pass
    found = False
    for s in data.setdefault("servers", []):
        if s.get("id") == server_id:
            s["enabled"] = bool(enabled)
            found = True
    if not found and server_id == BUILTIN_ID:
        data["servers"].append({"id": BUILTIN_ID, "enabled": bool(enabled)})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def _classify(exc: BaseException) -> tuple[str, str]:
    """Status and a readable message for a connection failure."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    if "401" in low or "403" in low or "unauthorized" in low or "forbidden" in low:
        return AUTH, "The server rejected the credentials (authorization required)."
    if isinstance(exc, (FileNotFoundError, PermissionError)) or "no such file" in low:
        return ERROR, "The server program could not be started: " + str(exc)[:160]
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or "timed out" in low or "timeout" in low:
        return OFFLINE, "The server did not answer in time."
    if "connect" in low or "refused" in low or "unreachable" in low or "closed" in low:
        return OFFLINE, "Could not reach the server: " + str(exc)[:160]
    return ERROR, text[:240]


class _Session:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.client = None
        self.status = DISABLED if not cfg.get("enabled", True) else NOT_CONFIGURED
        self.error = ""
        self.tools: list[dict] = []
        self.last_ok = 0.0
        self.last_call = 0.0
        self.last_check = 0.0
        self.server_info = ""
        self.via = ""
        self._stop: Optional[asyncio.Event] = None
        self._ready: Optional[asyncio.Event] = None
        self._task: Optional[asyncio.Task] = None
        self.lock: Optional[asyncio.Lock] = None

    def target(self):
        from mcp.client.stdio import StdioServerParameters
        c = self.cfg
        if c["transport"] == "inproc":
            # The built-in server's MCPServer object, spoken to over the SDK's
            # in-memory transport (same protocol, no subprocess).
            from core.workspace.mcp_server import server
            return server
        if c["transport"] == "stdio":
            if not c.get("command"):
                raise MCPToolError("No command configured.", NOT_CONFIGURED)
            return StdioServerParameters(command=c["command"], args=c.get("args", []), cwd=str(_root()))
        url = c.get("url", "")
        if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise MCPToolError("HTTP MCP servers must use HTTPS (or localhost).", NOT_CONFIGURED)
        if c.get("auth_secret"):
            from core.integrations import store
            token = store.get_secret(c["auth_secret"])
            if not token:
                raise MCPToolError("The saved token for this server is missing.", AUTH)
            import httpx2
            from mcp.client.streamable_http import streamable_http_client
            return streamable_http_client(url, http_client=httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {token}"}, timeout=30))
        return url


class MCPRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._sessions: dict[str, _Session] = {}
        self._listeners: list[Callable[[], None]] = []
        self._started = False

    # ── lifecycle ─────────────────────────────────────────────────────────
    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._loop.run_forever, name="mcp-registry", daemon=True)
            self._thread.start()
            for cfg in load_config():
                self._sessions[cfg["id"]] = _Session(cfg)
        for sid, s in list(self._sessions.items()):
            if s.cfg.get("enabled", True):
                self._submit(self._connect(sid))

    def shutdown(self) -> None:
        with self._lock:
            loop, self._started = self._loop, False
        if not loop:
            return
        for s in list(self._sessions.values()):
            if s._stop is not None:
                loop.call_soon_threadsafe(s._stop.set)
        time.sleep(0.3)
        loop.call_soon_threadsafe(loop.stop)

    def subscribe(self, fn: Callable[[], None]) -> None:
        if fn not in self._listeners:
            self._listeners.append(fn)

    def _emit(self) -> None:
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:
                pass

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ── connections ───────────────────────────────────────────────────────
    async def _connect(self, sid: str) -> None:
        s = self._sessions[sid]
        if s._task and not s._task.done():
            # A handshake (or a live session) is already running: wait for it
            # instead of starting a second process.
            if s._ready is not None and not s._ready.is_set():
                try:
                    await asyncio.wait_for(s._ready.wait(), CONNECT_TIMEOUT)
                except asyncio.TimeoutError:
                    pass
            return
        s.lock = s.lock or asyncio.Lock()
        s._stop, s._ready = asyncio.Event(), asyncio.Event()
        s.status, s.error, s.last_check = CONNECTING, "", time.time()
        self._emit()
        s._task = asyncio.create_task(self._hold(s))
        try:
            await asyncio.wait_for(s._ready.wait(), CONNECT_TIMEOUT)
        except asyncio.TimeoutError:
            s.status, s.error = OFFLINE, "The server did not finish its handshake in time."
            s._stop.set()
        self._emit()

    async def _hold(self, s: _Session) -> None:
        from mcp import Client
        from mcp_types import Implementation
        try:
            async with Client(s.target(), read_timeout_seconds=90,
                              client_info=Implementation(name="ultron-workspace", version="2.0.0")) as client:
                listed = await client.list_tools()
                s.tools = [_tool_dict(t) for t in listed.tools]
                s.client = client
                info = client.server_info
                s.server_info = f"{info.name} {info.version}".strip() if info else ""
                s.status, s.error, s.last_ok, s.via = CONNECTED, "", time.time(), s.cfg["transport"]
                s._ready.set()
                self._emit()
                await s._stop.wait()
        except MCPToolError as exc:
            s.status, s.error = exc.status, str(exc)
        except BaseException as exc:          # noqa: BLE001 — anyio raises exception groups
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if s.status == CONNECTED and s._stop is not None and s._stop.is_set():
                s.status = DISABLED if not s.cfg.get("enabled", True) else NOT_CONFIGURED
            else:
                s.status, s.error = _classify(exc)
        finally:
            s.client = None
            if s.status == CONNECTED:
                s.status = DISABLED if not s.cfg.get("enabled", True) else OFFLINE
            if s._ready is not None:
                s._ready.set()
            self._emit()

    def _sync(self, coro, timeout: float):
        if not self._started:
            self.start()
        return self._submit(coro).result(timeout)

    # ── public API ───────────────────────────────────────────────────────
    def servers(self) -> list[dict]:
        """Everything the MCP Hub shows. No secrets, no raw command lines."""
        if not self._started:
            self.start()
        out = []
        for s in list(self._sessions.values()):
            c = s.cfg
            out.append({"id": c["id"], "name": c["name"], "kind": c["transport"], "builtin": c.get("builtin", False),
                        "description": c.get("description", ""), "enabled": c.get("enabled", True),
                        "status": s.status, "error": s.error, "last_ok": s.last_ok, "last_call": s.last_call,
                        "last_check": s.last_check, "server_info": s.server_info,
                        "endpoint": (Path(c.get("command", "")).name + " " + " ".join(c.get("args", [])[:3])).strip()
                        if c["transport"] == "stdio" else "in-process" if c["transport"] == "inproc"
                        else _strip(c.get("url", "")),
                        "auth": "token (encrypted store)" if c.get("auth_secret") else "none",
                        "via": s.via,
                        "tools": [{"name": t["name"], "title": t["title"], "description": t["description"][:200],
                                   "read_only": t["read_only"]} for t in s.tools],
                        "capabilities": _builtin_capabilities() if c.get("builtin") else []})
        out.extend(_claude_servers())
        return out

    def tools(self) -> list[dict]:
        """Catalogue for the AI planner: every tool of every connected server."""
        if not self._started:
            self.start()
        out = []
        for s in self._sessions.values():
            if s.status == CONNECTED:
                for t in s.tools:
                    out.append(dict(t, server=s.cfg["id"]))
        return out

    def ensure(self, sid: str, timeout: float = CONNECT_TIMEOUT + 5) -> _Session:
        if not self._started:
            self.start()
        s = self._sessions.get(sid)
        if s is None:
            raise MCPToolError(f"No MCP server named {sid}.", NOT_CONFIGURED)
        if not s.cfg.get("enabled", True):
            raise MCPToolError(f"{s.cfg['name']} is disabled in MCP Hub.", DISABLED)
        if s.status != CONNECTED or s.client is None:
            self._sync(self._connect(sid), timeout)
        return s

    def call(self, sid: str, tool: str, args: dict, timeout: float = 90.0, cancel=None) -> dict:
        """Call one tool; returns its structured result. Raises MCPToolError."""
        s = self._sessions.get(sid)
        try:
            s = self.ensure(sid)
            if s.status != CONNECTED:
                raise MCPToolError(f"{s.cfg['name']} is {s.status.replace('_', ' ')}: {s.error}", s.status)
            known = {t["name"]: t for t in s.tools}
            if tool not in known:
                raise MCPToolError(f"{s.cfg['name']} has no tool named {tool}.", ERROR)
            validate_args(args, known[tool]["input_schema"])
            fut = self._submit(self._call(s, tool, args, timeout))
            deadline = time.monotonic() + timeout + 5
            while True:
                try:
                    result = fut.result(0.25)
                    break
                except TimeoutError:
                    if cancel is not None and cancel.is_set():
                        fut.cancel()
                        raise MCPToolError("Cancelled.", "cancelled")
                    if time.monotonic() > deadline:
                        fut.cancel()
                        raise MCPToolError(f"{tool} timed out after {int(timeout)} s.", OFFLINE)
            s.last_call = time.time()
            s.last_ok = time.time()
            self._emit()
            return result
        except MCPToolError as exc:
            # Only an unreachable server falls back — never a refused argument,
            # a tool error or a cancellation.
            unreachable = s is not None and (s.status != CONNECTED or exc.status == OFFLINE)
            if (s is not None and s.cfg.get("fallback") == "inproc" and unreachable
                    and exc.status in (OFFLINE, ERROR, NOT_CONFIGURED, CONNECTING)):
                return self._inproc(tool, args, timeout, reason=str(exc))
            raise

    async def _call(self, s: _Session, tool: str, args: dict, timeout: float) -> dict:
        async with s.lock:
            if s.client is None:
                raise MCPToolError(f"{s.cfg['name']} disconnected.", OFFLINE)
            try:
                res = await asyncio.wait_for(s.client.call_tool(tool, args), timeout)
            except asyncio.TimeoutError as exc:
                raise MCPToolError(f"{tool} timed out after {int(timeout)} s.", OFFLINE) from exc
            except Exception as exc:
                status, msg = _classify(exc)
                s.status, s.error = status, msg
                if s._stop:
                    s._stop.set()
                raise MCPToolError(msg, status) from exc
        return _result_dict(res)

    def _inproc(self, tool: str, args: dict, timeout: float, reason: str) -> dict:
        """The built-in server's own code, run in this process — used only
        when its stdio process cannot be reached, and reported as such."""
        from mcp import Client
        from core.workspace.mcp_server import server

        async def go():
            async with Client(server) as c:
                return _result_dict(await asyncio.wait_for(c.call_tool(tool, args), timeout))
        try:
            out = asyncio.run_coroutine_threadsafe(go(), self._loop).result(timeout + 5)
        except BaseExceptionGroup as group:
            inner = group
            while isinstance(inner, BaseExceptionGroup) and inner.exceptions:
                inner = inner.exceptions[0]
            if isinstance(inner, MCPToolError):
                raise inner from None
            raise MCPToolError(_classify(inner)[1], ERROR) from None
        out["_via"] = "in-process fallback"
        out["_fallback_reason"] = reason[:200]
        s = self._sessions.get(BUILTIN_ID)
        if s:
            s.via = "in-process fallback"
        return out

    def test(self, sid: str) -> dict:
        """Reconnect and list tools: a real round trip."""
        if sid.startswith("claude:"):
            from core import connections as conn
            conn.manager().check_async("mcp")
            return {"ok": True, "message": "Claude connector check started; results appear in a few seconds."}
        s = self._sessions.get(sid)
        if s is None:
            return {"ok": False, "message": "Unknown server."}
        if s._stop is not None and self._loop:
            self._loop.call_soon_threadsafe(s._stop.set)
            time.sleep(0.3)
        t0 = time.monotonic()
        try:
            self._sync(self._connect(sid), CONNECT_TIMEOUT + 5)
        except Exception as exc:
            s.status, s.error = _classify(exc)
        self._emit()
        ok = s.status == CONNECTED
        return {"ok": ok, "status": s.status, "tools": len(s.tools), "ms": int((time.monotonic() - t0) * 1000),
                "message": (f"Connected · {len(s.tools)} tools · {int((time.monotonic() - t0) * 1000)} ms"
                            if ok else f"{s.status.replace('_', ' ')}: {s.error}")}

    def set_enabled(self, sid: str, enabled: bool) -> dict:
        s = self._sessions.get(sid)
        if s is None:
            return {"ok": False, "message": "Unknown server."}
        s.cfg["enabled"] = bool(enabled)
        save_enabled(sid, enabled)
        if not enabled:
            if s._stop is not None and self._loop:
                self._loop.call_soon_threadsafe(s._stop.set)
            s.status, s.error = DISABLED, ""
            self._emit()
            return {"ok": True, "status": DISABLED}
        return self.test(sid)


def _strip(url: str) -> str:
    from urllib.parse import urlsplit
    p = urlsplit(url)
    return f"{p.scheme}://{p.hostname or ''}{p.path}" if p.scheme else ""


def _tool_dict(t) -> dict:
    ann = t.annotations
    return {"name": t.name, "title": t.title or t.name, "description": (t.description or "").strip(),
            "input_schema": t.input_schema or {"type": "object"},
            "read_only": bool(ann and ann.read_only_hint)}


def _result_dict(res) -> dict:
    if res.is_error:
        text = " ".join(getattr(c, "text", "") for c in res.content or [])
        raise MCPToolError(text[:400] or "The tool reported an error.", ERROR)
    if isinstance(res.structured_content, dict):
        data = dict(res.structured_content)
        if set(data) == {"result"} and isinstance(data["result"], dict):
            data = data["result"]
        return data
    text = "".join(getattr(c, "text", "") for c in res.content or [])
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"ok": True, "value": parsed}
    except ValueError:
        return {"ok": True, "text": text[:20000]}


def validate_args(args: Any, schema: dict) -> None:
    """Check arguments against a tool's JSON schema (the subset MCP servers
    emit): object, required, property types, enums. Unknown keys are refused
    when the schema closes the object."""
    if not isinstance(args, dict):
        raise MCPToolError("Tool arguments must be an object.", "invalid_args")
    props = schema.get("properties") or {}
    for req in schema.get("required") or []:
        if req not in args:
            raise MCPToolError(f"Missing required argument: {req}.", "invalid_args")
    for k, v in args.items():
        spec = props.get(k)
        if spec is None:
            if schema.get("additionalProperties") is False:
                raise MCPToolError(f"Unknown argument: {k}.", "invalid_args")
            continue
        types = spec.get("type")
        options = [x.get("type") for x in spec.get("anyOf", []) if isinstance(x, dict)] if not types else (
            types if isinstance(types, list) else [types])
        if options and not any(_is_type(v, t) for t in options):
            raise MCPToolError(f"Argument {k} has the wrong type.", "invalid_args")
        if "enum" in spec and v not in spec["enum"]:
            raise MCPToolError(f"Argument {k} must be one of {spec['enum']}.", "invalid_args")
        if isinstance(v, str) and len(v) > 4000:
            raise MCPToolError(f"Argument {k} is too long.", "invalid_args")


def _is_type(v, t) -> bool:
    return {"string": isinstance(v, str), "integer": isinstance(v, int) and not isinstance(v, bool),
            "number": isinstance(v, (int, float)) and not isinstance(v, bool), "boolean": isinstance(v, bool),
            "object": isinstance(v, dict), "array": isinstance(v, list), "null": v is None}.get(t, True)


def _builtin_capabilities() -> list[dict]:
    """The real data sources behind the built-in server and their state."""
    from core.workspace import geo
    from core.workspace.stays.providers import registry
    google = bool(geo.google_key())
    caps = [
        {"name": "Routing", "provider": "Google Routes" if google else "OSRM (OpenStreetMap)",
         "status": "configured", "note": "" if google else "Car, walking, cycling. Public transport needs Google Routes."},
        {"name": "Geocoding", "provider": "Google Geocoding" if google else "OpenStreetMap Nominatim",
         "status": "configured", "note": ""},
        {"name": "Nearby places", "provider": "Google Places" if google else "OpenStreetMap Overpass",
         "status": "configured", "note": ""},
        {"name": "Exchange rates", "provider": "NBS via kurs.resenje.org", "status": "configured", "note": ""},
    ]
    for p in registry():
        d = p.describe()
        caps.append({"name": "Stays · " + p.name, "provider": p.name,
                     "status": "link only" if p.kind == "link" else ("configured" if d["configured"] else "not_configured"),
                     "note": ("Opens the provider's own search; no data is read." if p.kind == "link" else
                              ("Needs: " + ", ".join(d["needs"])) if not d["configured"] else
                              ("verified quotes" if d["verifies_prices"] else
                               "indicative prices" if p.key == "staninadan" else "discovery, no prices")),
                     "last_status": d.get("last_status"), "last_ok": d.get("last_ok"),
                     "last_error": d.get("last_error", "")})
    return caps


def _claude_servers() -> list[dict]:
    from core import connections as conn
    from core import claude_brain
    mgr = conn.manager()
    claude = mgr.get("claude")
    out = []
    state_map = {conn.CONNECTED: CONNECTED, conn.READY: CONNECTED, conn.DISCONNECTED: AUTH, conn.ERROR: ERROR,
                 conn.CHECKING: CONNECTING, conn.NOT_CONFIGURED: NOT_CONFIGURED, conn.LIMITED: OFFLINE}
    for srv in mgr.mcp_servers():
        status = state_map.get(srv.state, ERROR)
        note = ""
        if not claude.usable:
            status = OFFLINE if status == CONNECTED else status
            note = f"Claude is {conn.describe_state(claude.state)} — connectors are reached through it."
        service = srv.extra.get("service", "")
        allowed = set(claude_brain.mcp_service_tools(service))
        out.append({"id": "claude:" + srv.label, "name": srv.label, "kind": "claude", "builtin": False,
                    "description": "Claude account connector, used read/draft-only by ULTRON.",
                    "enabled": True, "status": status, "error": srv.last_error or note,
                    "last_ok": srv.last_ok, "last_call": 0, "last_check": srv.last_check,
                    "server_info": "via Claude Code", "endpoint": srv.extra.get("url", "") and _strip(srv.extra["url"]),
                    "auth": "Claude account (claude.ai connector)", "via": "claude",
                    "service": service,
                    "tools": [{"name": t.split("__")[-1], "title": t.split("__")[-1].replace("_", " "),
                               "description": "", "read_only": t in allowed} for t in srv.tools],
                    "capabilities": []})
    return out


_registry: Optional[MCPRegistry] = None
_reg_lock = threading.Lock()


def registry() -> MCPRegistry:
    global _registry
    with _reg_lock:
        if _registry is None:
            _registry = MCPRegistry()
        return _registry


def reset_for_tests() -> None:
    global _registry
    with _reg_lock:
        if _registry is not None:
            _registry.shutdown()
        _registry = None
