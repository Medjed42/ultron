"""
core/claude_brain.py — Ultron's bridge to Claude Code, and its Gemini fallback.

Claude is reached through the Claude Code CLI the user is already signed in to
(claude.ai OAuth). This module owns every subprocess call to it, so the rules
below are enforced in one place.

WHAT WAS WRONG, AND WHAT THIS VERSION DOES INSTEAD
    Shell injection.  Every call went through `cmd.exe /c claude.cmd -p <text>`.
    cmd.exe re-parses that line, so a request containing `"` and `&` ran an
    arbitrary command (reproduced with `x" & echo INJECTED`), and a newline cut
    the prompt short. Now the native claude.exe is started directly and the
    prompt travels on stdin; no user text is ever placed on a command line.

    No permission boundary.  `--dangerously-skip-permissions` gave every voice
    request Bash, file writes and web access. Calls now run in `dontAsk` mode
    with no built-in tools, and MCP tools only when a caller names the exact
    tools it needs. Anything else is refused by Claude Code itself.

    Fake status.  When `auth status` failed the code filled in loggedIn=True,
    a "pro" plan and a hardcoded e-mail, and a probe that timed out still set
    claude_available=True. Status now only ever reflects what the CLI returned.

    Quota burn.  The health probe sent "ping" on the default model with Claude
    Code's full system prompt every two minutes — about 0.49 USD-equivalent of
    the plan's usage each time. The probe now uses Haiku with a one-line system
    prompt (~0.0002) and runs only when asked or when a status is stale.

    Browser.  `--no-chrome` is passed on every call: Claude is never allowed to
    reach for a browser on Ultron's behalf.

The public functions keep their names and return shapes, so ask_claude,
code_helper, dev_agent, the dashboard and the Integration hub work unchanged.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Optional

for _stream in ("stdout", "stderr"):
    try:
        _s = getattr(sys, _stream, None)
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).resolve().parent.parent

_CONFIG_DIR = _BASE / "config"
_STATUS_FILE = _CONFIG_DIR / "claude_brain_status.json"
_LOCK = threading.RLock()

_STATE_LISTENERS: list[Callable[[dict], None]] = []
_ALERT_LISTENERS: list[Callable[[str], None]] = []

# Model used for health probes. Real tasks use the account's default model
# unless the routing settings name one.
PROBE_MODEL = "haiku"
PROBE_SYSTEM = "You are a connectivity probe. Follow the instruction literally."

# The probe's result is trusted for this long before a task revalidates it.
STATUS_TTL_SECONDS = 15 * 60

# Read-only and draft-only MCP tools Claude may use without a desktop review.
# Sending, trashing, sharing, deleting and label changes are deliberately
# absent: those stay with Ultron's own reviewed plugins and confirmation gate.
SAFE_MCP_TOOLS: dict[str, tuple[str, ...]] = {
    "gmail": (
        "mcp__claude_ai_Gmail__search_threads",
        "mcp__claude_ai_Gmail__get_thread",
        "mcp__claude_ai_Gmail__get_message",
        "mcp__claude_ai_Gmail__list_labels",
        "mcp__claude_ai_Gmail__list_drafts",
        "mcp__claude_ai_Gmail__get_draft",
        "mcp__claude_ai_Gmail__create_draft",
    ),
    "drive": (
        "mcp__claude_ai_Google_Drive__search_files",
        "mcp__claude_ai_Google_Drive__list_recent_files",
        "mcp__claude_ai_Google_Drive__get_file_metadata",
        "mcp__claude_ai_Google_Drive__read_file_content",
        "mcp__claude_ai_Google_Drive__get_file_permissions",
    ),
    "docs": (
        "mcp__claude_ai_Claude_Docs__read",
        "mcp__claude_ai_Claude_Docs__query",
        "mcp__claude_ai_Claude_Docs__guide",
    ),
    "gamma": (
        "mcp__claude_ai_Gamma__search_gammas",
        "mcp__claude_ai_Gamma__get_gammas",
        "mcp__claude_ai_Gamma__read_gamma",
        "mcp__claude_ai_Gamma__get_themes",
        "mcp__claude_ai_Gamma__search_templates",
        "mcp__claude_ai_Gamma__get_folders",
    ),
}

# claude.ai connector name → the service key used above.
MCP_SERVICE_OF_SERVER = {
    "claude.ai Gmail": "gmail",
    "claude.ai Google Drive": "drive",
    "claude.ai Claude Docs": "docs",
    "claude.ai Gamma": "gamma",
}


def get_base_dir() -> Path:
    return _BASE


# ── Locating and running the CLI ──────────────────────────────────────────────

def find_claude_cli() -> Optional[Path]:
    """The Claude Code executable, preferring the native claude.exe.

    The npm shim `claude.cmd` only forwards to
    node_modules/@anthropic-ai/claude-code/bin/claude.exe; calling the exe
    directly is what keeps cmd.exe — and its parsing — out of the path."""
    npm = Path.home() / "AppData" / "Roaming" / "npm"
    candidates = [
        npm / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe",
        Path.home() / ".local" / "bin" / "claude.exe",
    ]
    for c in candidates:
        if c.exists():
            return c
    for name in ("claude.exe", "claude"):
        which = shutil.which(name)
        if which and not which.lower().endswith((".cmd", ".ps1", ".bat")):
            return Path(which)
    for c in (npm / "claude.cmd", Path(shutil.which("claude.cmd") or "")):
        if str(c) and c.exists():
            return c
    return None


def _child_env(extra: Optional[dict] = None) -> dict:
    """Environment for the CLI.

    When Ultron is itself started from inside a Claude Code session, that
    session's variables (CLAUDECODE, CLAUDE_CODE_*, its proxy base URL) leak
    into the child and make it behave as a nested session. They are removed in
    that case only; a normal launch passes the environment through untouched."""
    env = dict(os.environ)
    if env.get("CLAUDECODE"):
        for k in list(env):
            if (k == "CLAUDECODE" or k.startswith("CLAUDE_CODE_")
                    or k.startswith("CLAUDE_AGENT_SDK") or k == "CLAUDE_PID"
                    or k == "ANTHROPIC_BASE_URL"):
                env.pop(k, None)
    # Use the claude.ai subscription login unless API-key auth was chosen.
    # An ANTHROPIC_API_KEY that leaks into Ultron's environment (another
    # project's .env, a shell profile) silently takes precedence over the
    # login in Claude Code, and also disables the claude.ai MCP connectors.
    if _auth_mode() != "api_key":
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            env.pop(k, None)
    if extra:
        env.update(extra)
    return env


def _auth_mode() -> str:
    """"subscription" (default: the claude.ai login) or "api_key"."""
    try:
        from core.connections import routing_settings
        return routing_settings().get("claude_auth") or "subscription"
    except Exception:
        return "subscription"


def run_claude_command(args: list[str], timeout: int = 60,
                       stdin_text: Optional[str] = None,
                       env_extra: Optional[dict] = None) -> tuple[int, str, str]:
    """Run the CLI hidden, with UTF-8 I/O. Returns (code, stdout, stderr).

    `args` must be fixed flags chosen by code. Anything the user said goes in
    `stdin_text`. If only the .cmd shim exists, arguments are refused when they
    contain characters cmd.exe would interpret."""
    cli = find_claude_cli()
    if not cli:
        return 127, "", "Claude CLI not found on system."

    cmd = [str(cli)] + list(args)
    if cli.suffix.lower() in (".cmd", ".bat"):
        if any(re.search(r'["&|<>^%!\r\n]', a) for a in args):
            return 2, "", "Refusing to pass special characters through cmd.exe."
        cmd = ["cmd.exe", "/c"] + cmd

    kw = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": _child_env(env_extra),
        "cwd": str(_BASE),
    }
    if stdin_text is None:
        kw["stdin"] = subprocess.DEVNULL
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(cmd, timeout=timeout, input=stdin_text, **kw)
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"Claude command timed out after {timeout}s"
    except Exception as exc:
        return 1, "", str(exc)


def cli_version() -> str:
    code, out, _ = run_claude_command(["--version"], timeout=15)
    return out.splitlines()[0].strip() if code == 0 and out else ""


# ── Limits ────────────────────────────────────────────────────────────────────

def parse_reset_time(text: str) -> tuple[bool, Optional[str], Optional[float], Optional[str]]:
    """
    Parses limit messages such as:
    "You've hit your session limit · resets 1:20am (Europe/Belgrade)"
    Returns: (is_limit_hit, reset_time_str, reset_timestamp, time_remaining_human)
    """
    if not text:
        return False, None, None, None

    lower = text.lower()
    is_hit = any(signal in lower for signal in [
        "hit your session limit",
        "hit your limit",
        "usage limit reached",
        "rate limit",
        "quota exceeded",
        "session limit",
        "too many requests",
        "resets at",
        "resets in",
    ])

    if not is_hit:
        return False, None, None, None

    m = re.search(r"resets?\s+(?:at\s+)?([0-9]{1,2}(?::[0-9]{2})?\s*[ap]m)(?:\s*\(([^)]+)\))?", text, re.I)
    reset_str = None
    reset_ts = None
    time_remaining_str = None

    if m:
        time_part = m.group(1).strip()
        tz_part = (m.group(2) or "").strip()
        reset_str = f"{time_part} ({tz_part})" if tz_part else time_part

        try:
            now = datetime.now()
            clean_time = time_part.lower().replace(" ", "")
            if ":" in clean_time:
                parsed_time = datetime.strptime(clean_time, "%I:%M%p").time()
            else:
                parsed_time = datetime.strptime(clean_time, "%I%p").time()

            target_dt = datetime.combine(now.date(), parsed_time)
            if target_dt <= now:
                target_dt += timedelta(days=1)

            diff = target_dt - now
            mins = max(1, int(diff.total_seconds() // 60))
            hours = mins // 60
            rem_m = mins % 60
            time_remaining_str = f"{hours}h {rem_m}m" if hours > 0 else f"{rem_m}m"
            reset_ts = target_dt.timestamp()
        except Exception:
            pass

    if not reset_str:
        m2 = re.search(r"resets?\s+([^·\n\r]+)", text, re.I)
        if m2:
            reset_str = m2.group(1).strip()

    return True, reset_str, reset_ts, time_remaining_str


# ── Account ───────────────────────────────────────────────────────────────────

def get_claude_auth() -> dict:
    """`claude auth status`, as reported. No API call, ~0.2 s.

    Returns loggedIn=False with an `error` when the CLI is missing or does not
    answer — never a guessed account."""
    code, out, err = run_claude_command(["auth", "status"], timeout=20)
    if code == 0 and out.startswith("{"):
        try:
            data = json.loads(out)
            return {
                "loggedIn": bool(data.get("loggedIn")),
                "authMethod": data.get("authMethod", ""),
                "subscriptionType": data.get("subscriptionType") or "",
                "email": data.get("email") or "",
                "apiProvider": data.get("apiProvider", ""),
            }
        except Exception as exc:
            err = f"unreadable auth status: {exc}"
    if code == 127:
        err = "Claude Code CLI is not installed."
    return {"loggedIn": False, "subscriptionType": "", "email": "",
            "error": (err or out or f"auth status exited with {code}")[:300]}


# ── Cached status (shared with the Integration hub and the dashboard) ─────────

def _empty_status() -> dict:
    return {
        "active_brain": "none",
        "claude_available": False,
        "verified": False,
        "limit_hit": False,
        "reset_str": None,
        "reset_timestamp": None,
        "time_remaining_str": None,
        "last_checked": 0,
        "last_ok": 0,
        "last_error": "Not checked yet.",
        "account_email": "",
        "subscription": "",
        "cli_version": "",
        "model": "",
        "connected_mcp": [],
        "mcp_servers": [],
        "mcp_tools": [],
        "connection_preferences": {"gmail": "ask", "drive": "ask"},
        "antigravity_status": "",
        "both_exhausted": False,
    }


def load_cached_status() -> dict:
    """Last known status. Defaults to *unverified*, never to available."""
    with _LOCK:
        state = _empty_status()
        if _STATUS_FILE.exists():
            try:
                saved = json.loads(_STATUS_FILE.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    state.update(saved)
            except Exception:
                pass
        return state


def save_cached_status(data: dict):
    """Saves status dictionary to disk atomically."""
    with _LOCK:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_STATUS_FILE)

    for listener in list(_STATE_LISTENERS):
        try:
            listener(data)
        except Exception:
            pass


def _update_status(**fields) -> dict:
    with _LOCK:
        state = load_cached_status()
        state.update(fields)
        save_cached_status(state)
        return state


def register_state_listener(callback: Callable[[dict], None]):
    with _LOCK:
        if callback not in _STATE_LISTENERS:
            _STATE_LISTENERS.append(callback)


def register_alert_listener(callback: Callable[[str], None]):
    with _LOCK:
        if callback not in _ALERT_LISTENERS:
            _ALERT_LISTENERS.append(callback)


def notify_alert(message: str):
    print(f"[Brain Alert] ⚠️ {message}")
    for listener in list(_ALERT_LISTENERS):
        try:
            listener(message)
        except Exception:
            pass


# ── Talking to Claude ─────────────────────────────────────────────────────────

def _parse_stream(out: str) -> tuple[dict, dict]:
    """(init_event, result_event) from stream-json output."""
    init, result = {}, {}
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            init = ev
        elif ev.get("type") == "result":
            result = ev
    return init, result


def probe(timeout: int = 60) -> dict:
    """One real, minimal request that verifies Claude and its MCP connectors.

    Haiku, no built-in tools, one-line system prompt. With
    MCP_CONNECTION_NONBLOCKING=false the init event waits for the claude.ai
    connectors, so the same call also reports every MCP server and tool.
    Returns {ok, output, model, mcp_servers, mcp_tools, limit_hit, error, ms}."""
    t0 = time.monotonic()
    args = ["-p", "--output-format", "stream-json", "--verbose",
            "--no-chrome", "--no-session-persistence",
            "--permission-mode", "dontAsk", "--tools", "",
            "--model", PROBE_MODEL, "--system-prompt", PROBE_SYSTEM]
    code, out, err = run_claude_command(
        args, timeout=timeout, stdin_text="Reply with exactly: ULTRON_LINK_OK",
        env_extra={"MCP_CONNECTION_NONBLOCKING": "false"})
    init, result = _parse_stream(out)
    text = str(result.get("result") or "")
    combined = f"{text}\n{err}"
    is_hit, reset_str, reset_ts, rem = parse_reset_time(combined)
    ok = (code == 0 and result.get("subtype") == "success"
          and not result.get("is_error") and "ULTRON_LINK_OK" in text)
    servers = [{"name": s.get("name", ""), "status": s.get("status", "")}
               for s in init.get("mcp_servers") or []]
    tools = [t for t in init.get("tools") or [] if t.startswith("mcp__")]
    error = None
    if not ok:
        error = (text or err or f"exit code {code}").strip()[:300]
    return {"ok": ok, "output": text, "model": init.get("model", ""),
            "mcp_servers": servers, "mcp_tools": tools,
            "limit_hit": is_hit, "reset_str": reset_str, "reset_timestamp": reset_ts,
            "time_remaining": rem, "error": error,
            "ms": int((time.monotonic() - t0) * 1000)}


def mcp_list(timeout: int = 45) -> list[dict]:
    """`claude mcp list`: [{name, url, status}] with status connected / failed /
    needs_auth / unknown. A real health check of each server (~2 s)."""
    code, out, err = run_claude_command(["mcp", "list"], timeout=timeout)
    servers = []
    for line in (out or "").splitlines():
        m = re.match(r"^(.+?):\s+(\S+.*?)\s+-\s+(.+)$", line.strip())
        if not m:
            continue
        name, url, raw = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        low = raw.lower()
        if "connected" in low and "fail" not in low:
            status = "connected"
        elif "auth" in low:
            status = "needs_auth"
        elif "fail" in low or "✗" in raw:
            status = "failed"
        else:
            status = "unknown"
        servers.append({"name": name, "url": url, "status": status, "raw": raw})
    if code != 0 and not servers:
        raise RuntimeError((err or out or f"claude mcp list exited {code}")[:300])
    return servers


def get_status(force_probe: bool = False) -> dict:
    """Current Claude status. Probes only when forced, or when the last real
    check is older than STATUS_TTL_SECONDS and Claude is not in a known limit."""
    state = load_cached_status()
    now_ts = time.time()

    if state.get("limit_hit") and state.get("reset_timestamp"):
        if now_ts >= state["reset_timestamp"]:
            state.update(limit_hit=False, reset_str=None, reset_timestamp=None,
                         time_remaining_str=None, both_exhausted=False)
            save_cached_status(state)
        else:
            mins = max(1, int((state["reset_timestamp"] - now_ts) // 60))
            state["time_remaining_str"] = f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m"

    stale = (now_ts - float(state.get("last_checked") or 0)) > STATUS_TTL_SECONDS
    if force_probe or (stale and not state.get("limit_hit")):
        state = refresh_status()
    return state


def refresh_status() -> dict:
    """Account check + real probe, written to the status file."""
    now_ts = time.time()
    auth = get_claude_auth()
    fields = {
        "last_checked": now_ts,
        "account_email": auth.get("email", ""),
        "subscription": auth.get("subscriptionType", ""),
    }
    if not auth.get("loggedIn"):
        fields.update(claude_available=False, verified=False, active_brain="none",
                      last_error=auth.get("error") or "Claude Code is not signed in.")
        return _update_status(**fields)

    fields["cli_version"] = cli_version()
    p = probe()
    if p["ok"]:
        fields.update(
            claude_available=True, verified=True, active_brain="claude",
            last_ok=now_ts, last_error=None, probe_model=p["model"],
            limit_hit=False, reset_str=None, reset_timestamp=None,
            time_remaining_str=None, both_exhausted=False,
            mcp_servers=p["mcp_servers"], mcp_tools=p["mcp_tools"],
            connected_mcp=[s["name"].removeprefix("claude.ai ")
                           for s in p["mcp_servers"] if s["status"] == "connected"],
        )
    elif p["limit_hit"]:
        fields.update(claude_available=False, verified=False, limit_hit=True,
                      reset_str=p["reset_str"], reset_timestamp=p["reset_timestamp"],
                      time_remaining_str=p["time_remaining"], active_brain="antigravity",
                      last_error=p["error"])
    else:
        fields.update(claude_available=False, verified=False, active_brain="none",
                      last_error=p["error"])
    return _update_status(**fields)


def _result_from_json(code: int, out: str, err: str) -> dict:
    """Shape a `--output-format json` reply into Ultron's result dict."""
    data = {}
    if out.startswith("{"):
        try:
            data = json.loads(out)
        except Exception:
            data = {}
    text = str(data.get("result") or ("" if data else out) or "")
    combined = f"{text}\n{err}"
    is_hit, reset_str, reset_ts, rem = parse_reset_time(combined)
    denials = [d.get("tool_name", "") for d in data.get("permission_denials") or []]
    ok = (code == 0 and not data.get("is_error")
          and data.get("subtype", "success") == "success" and not is_hit)
    res = {
        "success": ok,
        "output": text if ok else (text if not is_hit else ""),
        "limit_hit": is_hit,
        "reset_str": reset_str,
        "time_remaining": rem,
        "structured": data.get("structured_output"),
        "permission_denials": denials,
        "model": next(iter(data.get("modelUsage") or {}), ""),
        "error": None,
    }
    if is_hit:
        res["error"] = f"Claude session limit reached. Resets: {reset_str or 'soon'}"
        _update_status(limit_hit=True, claude_available=False, verified=False,
                       reset_str=reset_str, reset_timestamp=reset_ts,
                       time_remaining_str=rem, last_error=res["error"],
                       last_checked=time.time())
    elif not ok:
        res["error"] = (text or err or f"Claude exited with code {code}").strip()[:500]
        _update_status(last_error=res["error"], last_checked=time.time())
    else:
        # A real task succeeding is the strongest health check there is.
        fields = dict(claude_available=True, verified=True, active_brain="claude",
                      last_ok=time.time(), last_checked=time.time(), last_error=None,
                      limit_hit=False)
        if res["model"]:
            fields["model"] = res["model"]
        _update_status(**fields)
    return res


def ask_claude(prompt: str, system: Optional[str] = None, timeout: int = 120,
               allowed_tools: Optional[Iterable[str]] = None,
               model: Optional[str] = None,
               json_schema: Optional[dict] = None) -> dict:
    """
    Ask Claude for one answer.

    No built-in tools (no shell, files or web). `allowed_tools` may name exact
    MCP tools; nothing else is permitted. Returns
        {"success", "output", "limit_hit", "reset_str", "error",
         "structured", "permission_denials", "model"}
    """
    allowed = [t for t in (allowed_tools or []) if t.startswith("mcp__")]
    args = ["-p", "--output-format", "json", "--no-chrome",
            "--no-session-persistence", "--permission-mode", "dontAsk",
            "--tools", "ToolSearch" if allowed else ""]
    if allowed:
        args += ["--allowedTools", ",".join(allowed)]
    model = model or _routing_model()
    if model:
        args += ["--model", model]
    sys_file = None
    try:
        if system:
            sys_file = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                                   encoding="utf-8")
            sys_file.write(system)
            sys_file.close()
            args += ["--system-prompt-file", sys_file.name]
        if json_schema:
            args += ["--json-schema", json.dumps(json_schema, separators=(",", ":"))]
        code, out, err = run_claude_command(
            args, timeout=timeout, stdin_text=prompt,
            env_extra={"MCP_CONNECTION_NONBLOCKING": "false"} if allowed else None)
    finally:
        if sys_file is not None:
            try:
                os.unlink(sys_file.name)
            except OSError:
                pass
    return _result_from_json(code, out, err)


def _routing_model() -> str:
    try:
        from core.connections import routing_settings
        return routing_settings().get("claude_model") or ""
    except Exception:
        return ""


def ask_antigravity(prompt: str, system: Optional[str] = None, timeout: int = 60) -> dict:
    """Gemini's SMART ladder (the "Antigravity" fallback)."""
    try:
        from core import gemini
        contents = []
        if system:
            contents.append(f"System: {system}\n\n")
        contents.append(prompt)

        resp = gemini.call(contents, tier=gemini.SMART, timeout_ms=timeout * 1000)
        if resp is not None:
            return {"success": True, "output": (resp.text or "").strip(),
                    "limit_hit": False, "brain": "antigravity", "error": None}
        return {"success": False, "output": "", "limit_hit": True,
                "brain": "antigravity",
                "error": "Gemini SMART ladder exhausted or unreachable"}
    except Exception as exc:
        err_msg = str(exc)
        is_quota = any(q in err_msg.lower() for q in ("429", "quota", "resource_exhausted", "exhausted"))
        return {"success": False, "output": "", "limit_hit": is_quota,
                "brain": "antigravity", "error": err_msg}


def _fallback_allowed() -> bool:
    try:
        from core.connections import routing_settings
        return bool(routing_settings().get("allow_provider_fallback", True))
    except Exception:
        return True


def execute(
    prompt: str,
    system: Optional[str] = None,
    preferred_brain: str = "auto",
    timeout: int = 120,
    speak_fn: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Run a text task on Claude, falling back to Gemini only when allowed.

    preferred_brain:
      "claude"       — Claude only. A failure is reported, never substituted.
      "antigravity"  — Gemini only.
      "auto"         — Claude first; Gemini if Claude fails and the routing
                       settings allow provider fallback. The substitution is
                       always stated in the result and spoken if possible.
    """
    preferred_brain = (preferred_brain or "auto").lower()
    claude_res = None
    if preferred_brain in ("claude", "auto"):
        status = load_cached_status()
        if preferred_brain == "claude" or not status.get("limit_hit"):
            print("[Brain] 🧠 Asking Claude Code...")
            claude_res = ask_claude(prompt, system=system, timeout=timeout)
            if claude_res["success"]:
                return {"success": True, "output": claude_res["output"],
                        "brain_used": "claude",
                        "note": "Executed by Claude (Claude Code CLI)"}
        if preferred_brain == "claude":
            err = (claude_res or {}).get("error") or "Claude is in a usage limit."
            return {"success": False, "output": "", "brain_used": "none",
                    "limit_hit": bool((claude_res or {}).get("limit_hit")),
                    "error": f"Claude only was requested and Claude failed: {err}"}
        if not _fallback_allowed():
            err = (claude_res or {}).get("error") or "Claude is in a usage limit."
            return {"success": False, "output": "", "brain_used": "none",
                    "error": f"Claude failed and provider fallback is disabled: {err}"}

    reason = ((claude_res or {}).get("error") or "Claude is in a usage limit") \
        if preferred_brain == "auto" else "Gemini was requested"
    if preferred_brain == "auto":
        print(f"[Brain] ⚡ Falling back to Gemini: {reason}")
        if speak_fn:
            try:
                speak_fn("Claude is unavailable, so I am using Gemini for this.")
            except Exception:
                pass

    anti_res = ask_antigravity(prompt, system=system, timeout=min(60, timeout))
    if anti_res["success"]:
        return {"success": True, "output": anti_res["output"],
                "brain_used": "antigravity",
                "note": f"Executed by Gemini (fallback: {reason})"
                        if preferred_brain == "auto" else "Executed by Gemini"}

    if preferred_brain == "auto":
        _update_status(both_exhausted=True)
        both_msg = ("Neither Claude nor Gemini could complete the request. "
                    f"Claude: {reason}. Gemini: {anti_res.get('error')}")
        notify_alert(both_msg)
        return {"success": False, "output": "", "brain_used": "none",
                "both_exhausted": True, "error": both_msg}
    return {"success": False, "output": "", "brain_used": "none",
            "error": f"Gemini failed: {anti_res.get('error')}"}


# ── Connection preferences & Claude's MCP connectors ──────────────────────────

def get_connection_preference(service: str = "gmail") -> str:
    """Returns 'ask', 'claude', or 'user'."""
    prefs = load_cached_status().get("connection_preferences", {})
    return prefs.get(service.lower(), "ask")


def set_connection_preference(service: str, preference: str):
    """Saves connection preference for a service ('ask' | 'claude' | 'user')."""
    with _LOCK:
        state = load_cached_status()
        state.setdefault("connection_preferences", {})[service.lower()] = preference.lower()
        save_cached_status(state)
    print(f"[Brain] ⚙️ Connection for {service} set to: {preference}")


def mcp_service_tools(service: str) -> tuple[str, ...]:
    return SAFE_MCP_TOOLS.get((service or "").lower().strip(), ())


def execute_claude_mcp_query(service: str, task_description: str, timeout: int = 120,
                             json_schema: Optional[dict] = None) -> dict:
    """Run a task through one of Claude's MCP connectors with the read/draft
    allowlist for that service. Write actions outside the list are refused by
    Claude Code and reported in `permission_denials`. With `json_schema` the
    answer also comes back as validated structured output (`structured`)."""
    tools = mcp_service_tools(service)
    if not tools:
        return {"success": False, "output": "", "limit_hit": False,
                "error": f"Unknown MCP service '{service}'. Known: {', '.join(SAFE_MCP_TOOLS)}",
                "permission_denials": []}
    system = (
        "You are ULTRON's MCP executor. Use only the connected "
        f"{service.title()} tools you are given to complete the user's task. "
        "If the task needs an action you are not permitted to take (sending, "
        "deleting, trashing, sharing, labelling), do not attempt workarounds: "
        "say exactly which action needs the user's own confirmation in ULTRON. "
        "Never open a browser. Answer concisely in the user's language."
    )
    return ask_claude(task_description, system=system, timeout=timeout, allowed_tools=tools,
                      json_schema=json_schema)
