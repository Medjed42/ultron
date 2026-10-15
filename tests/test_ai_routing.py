"""
tests/test_ai_routing.py — AI-first routing, the browser policy, the Claude
bridge and honest connection status.

Everything here is offline: providers, MCP and the CLI are replaced with
fakes, and every way of opening a browser is patched so a launch fails the
test. The live checks are in test_claude_brain.py behind ULTRON_LIVE_TESTS=1.

The eight "no Chrome" scenarios from the repair brief are NoChromeScenarios
test_1 … test_8.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core import browser_policy, claude_brain, command_router
from core import connections as conn


# ── helpers ───────────────────────────────────────────────────────────────────

class BrowserTrap:
    """Patches every browser entry point; any call is recorded as a launch."""

    def __init__(self):
        self.launches: list = []

    def __enter__(self):
        self._stack = ExitStack()
        rec = lambda *a, **k: self.launches.append((a, k)) or True
        import webbrowser
        for name in ("open", "open_new", "open_new_tab"):
            self._stack.enter_context(patch.object(webbrowser, name, side_effect=rec))
        if hasattr(os, "startfile"):
            self._stack.enter_context(patch.object(os, "startfile", side_effect=rec))
        return self

    def __exit__(self, *exc):
        self._stack.close()


def make_manager(claude=conn.CONNECTED, gemini=conn.READY, live=False, mcp=None):
    """A ConnectionManager in a known state that never runs real checks."""
    m = conn.ConnectionManager.__new__(conn.ConnectionManager)
    import threading
    m._lock = threading.RLock()
    m._listeners, m._inflight, m._fail_streak = [], set(), {}
    m._pool = MagicMock()
    m._services = {
        "claude": conn.ServiceStatus("claude", "Claude", "provider", configured=True, state=claude,
                                     integration="Claude Code CLI · claude.ai (pro)",
                                     last_error="" if claude in conn.USABLE else "Invalid API key"),
        "gemini": conn.ServiceStatus("gemini", "Gemini", "provider", configured=True, state=gemini,
                                     last_error="" if gemini in conn.USABLE else "network down"),
    }
    m._mcp, m._mcp_error = {}, ""
    for label, state, service in (mcp or []):
        m._mcp["mcp:claude.ai " + label] = conn.ServiceStatus(
            "mcp:claude.ai " + label, label, "mcp", configured=True, state=state,
            tools=[f"mcp__claude_ai_{label}__x"], extra={"service": service})
    m._live = {"connected": live, "model": "gemini-live" if live else "", "error": "", "since": 0.0}
    m._task = {"state": "IDLE", "provider": "", "tool": "", "stage": "", "result": "", "updated": 0.0}
    m.ensure_fresh = lambda key: m.get(key)       # no real revalidation in tests
    return m


CATALOG = [
    {"name": "computer_settings", "description": "Controls volume.",
     "parameters": {"properties": {"action": {"description": "volume_up | volume_down"}}, "required": ["action"]}},
    {"name": "open_app", "description": "Opens an application.",
     "parameters": {"properties": {"app_name": {}}, "required": ["app_name"]}},
    {"name": "browser_control", "description": "Controls a browser.",
     "parameters": {"properties": {"action": {}, "url": {}}, "required": ["action"]}},
]


def make_router(mgr, live_ok=True, runner=None):
    calls = SimpleNamespace(runner=[], live=[])

    def run(name, args):
        calls.runner.append((name, args))
        return runner(name, args) if runner else f"ran {name}"

    def live(text):
        calls.live.append(text)
        return live_ok and mgr.live_status()["connected"]

    r = command_router.CommandRouter(run, lambda: CATALOG, live, manager=mgr)
    return r, calls


ROUTING_DEFAULT = {"provider_order": ["claude", "gemini"], "allow_provider_fallback": True,
                   "browser_policy": "ask", "claude_model": "", "claude_auth": "subscription"}


class _Base(unittest.TestCase):
    def setUp(self):
        browser_policy.reset_for_tests()
        p = patch.object(conn, "routing_settings", return_value=dict(ROUTING_DEFAULT))
        self.routing = p.start()
        self.addCleanup(p.stop)


# ── The eight scenarios ───────────────────────────────────────────────────────

class NoChromeScenarios(_Base):

    def test_1_claude_connected_mcp_tool_available(self):
        mgr = make_manager(mcp=[("Gmail", conn.CONNECTED, "gmail")])
        r, calls = make_router(mgr)
        with BrowserTrap() as trap, \
                patch.object(claude_brain, "get_connection_preference", return_value="claude"), \
                patch.object(claude_brain, "execute_claude_mcp_query",
                             return_value={"success": True, "output": "3 unread", "error": None,
                                           "permission_denials": [], "model": "m"}) as q:
            res = r.route("Check my Gmail inbox for unread messages")
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.path, "claude_mcp")
        self.assertEqual(res.provider, "claude")
        q.assert_called_once()
        self.assertEqual(trap.launches, [])
        self.assertEqual(calls.runner, [])
        self.assertIn("Checking Claude", res.stages)

    def test_2_claude_down_gemini_connected(self):
        mgr = make_manager(claude=conn.ERROR, gemini=conn.CONNECTED, live=True)
        r, calls = make_router(mgr)
        with BrowserTrap() as trap:
            res = r.route("turn the volume up a bit")
        self.assertTrue(res.ok)
        self.assertEqual(res.path, "gemini_live")
        self.assertEqual(calls.live, ["turn the volume up a bit"])
        self.assertEqual(trap.launches, [])

    def test_2b_claude_down_gemini_rest_plans_native_tool(self):
        mgr = make_manager(claude=conn.DISCONNECTED, gemini=conn.CONNECTED, live=False)
        r, calls = make_router(mgr)
        plan = {"action": "tool", "reply": "Turning it up.", "tool": "computer_settings",
                "arguments": {"action": "volume_up"}}
        with BrowserTrap() as trap, patch.object(r, "_plan", return_value=(plan, "")):
            res = r.route("turn the volume up")
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.provider, "gemini")
        self.assertEqual(calls.runner, [("computer_settings", {"action": "volume_up"})])
        self.assertIn("Using Gemini", res.reply)          # the substitution is stated
        self.assertEqual(trap.launches, [])

    def test_3_claude_and_gemini_disconnected(self):
        mgr = make_manager(claude=conn.DISCONNECTED, gemini=conn.DISCONNECTED, live=False)
        r, calls = make_router(mgr)
        with BrowserTrap() as trap:
            res = r.route("summarise the news for me")
        self.assertFalse(res.ok)
        self.assertEqual(res.path, "blocked")
        self.assertIn("Claude is not connected", res.error)
        self.assertIn("Gemini is not connected", res.error)
        self.assertIn("claude auth login", res.error)       # a real manual command
        self.assertIn("Nothing was opened in a browser", res.error)
        self.assertEqual(calls.runner, [])
        self.assertEqual(trap.launches, [])

    def test_4_mcp_disconnected(self):
        mgr = make_manager(mcp=[("Gmail", conn.DISCONNECTED, "gmail")], gemini=conn.CONNECTED, live=True)
        r, calls = make_router(mgr)
        with BrowserTrap() as trap, \
                patch.object(claude_brain, "get_connection_preference", return_value="claude"), \
                patch.object(claude_brain, "execute_claude_mcp_query") as q:
            res = r.route("read my latest emails")
        self.assertFalse(res.ok)
        self.assertIn("Gmail MCP server is disconnected", res.error)
        self.assertIn("reconnect MCP", res.error)
        q.assert_not_called()
        self.assertEqual(calls.live, [])          # Gemini is not used as a Gmail stand-in
        self.assertEqual(trap.launches, [])

    def test_5_user_explicitly_asks_for_chrome(self):
        mgr = make_manager()
        r, calls = make_router(mgr, runner=lambda n, a: "Opened Chrome.")
        res = r.route("ULTRON, open Chrome")
        self.assertEqual(res.path, "manual")
        self.assertEqual(calls.runner, [("open_app", {"app_name": "Chrome"})])
        self.assertTrue(browser_policy.has_explicit_browser_intent())

    def test_6_claude_fails_during_execution(self):
        mgr = make_manager(claude=conn.CONNECTED, gemini=conn.CONNECTED, live=False)
        r, calls = make_router(mgr)
        fail = (None, "Invalid API key")
        ok = ({"action": "reply", "reply": "42"}, "")
        with BrowserTrap() as trap, patch.object(r, "_plan", side_effect=[fail, ok]):
            res = r.route("what is six times seven")
        self.assertTrue(res.ok)
        self.assertEqual(res.provider, "gemini")
        self.assertIn("Claude failed", res.reply)
        self.assertEqual(trap.launches, [])
        # …but an explicit Claude request is never substituted.
        with BrowserTrap() as trap, patch.object(r, "_plan", side_effect=[fail]):
            res = r.route("use Claude: what is six times seven")
        self.assertFalse(res.ok)
        self.assertEqual(res.provider, "claude")
        self.assertIn("did not substitute", res.error)
        self.assertEqual(trap.launches, [])

    def test_7_tool_missing_despite_provider_connected(self):
        mgr = make_manager(claude=conn.CONNECTED)
        r, calls = make_router(mgr)
        plan = {"action": "tool", "reply": "On it.", "tool": "teleporter", "arguments": {}}
        with BrowserTrap() as trap, patch.object(r, "_plan", return_value=(plan, "")):
            res = r.route("teleport me home")
        self.assertFalse(res.ok)
        self.assertIn("required capability is not available", res.error)
        self.assertIn("does not make every action possible", res.error)
        self.assertEqual(calls.runner, [])
        self.assertEqual(trap.launches, [])

    def test_8_restart_keeps_settings_and_revalidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "config"
            cfg_dir.mkdir()
            (cfg_dir / "api_keys.json").write_text(json.dumps({"gemini_api_key": "x" * 40}), encoding="utf-8")
            (cfg_dir / "claude_brain_status.json").write_text(json.dumps({
                "verified": True, "claude_available": True, "last_ok": 1.0,
                "mcp_servers": [{"name": "claude.ai Gmail", "status": "connected"}],
                "mcp_tools": ["mcp__claude_ai_Gmail__search_threads"]}), encoding="utf-8")
            from memory import config_manager as cm
            with patch.object(conn, "_BASE", Path(tmp)), \
                    patch.object(cm, "CONFIG_DIR", cfg_dir), patch.object(cm, "CONFIG_FILE", cfg_dir / "api_keys.json"), \
                    patch.object(claude_brain, "_STATUS_FILE", cfg_dir / "claude_brain_status.json"), \
                    patch.object(claude_brain, "_CONFIG_DIR", cfg_dir), \
                    patch.object(conn, "routing_settings", _real_routing):
                conn.save_routing_settings(provider_order=["gemini", "claude"], browser_policy="explicit_only")
                # "restart": a brand-new manager
                conn.reset_for_tests()
                with patch.object(claude_brain, "find_claude_cli", return_value=Path("claude.exe")):
                    m2 = conn.ConnectionManager()
                self.assertEqual(conn.routing_settings()["provider_order"], ["gemini", "claude"])
                self.assertEqual(conn.routing_settings()["browser_policy"], "explicit_only")
                saved = json.loads((cfg_dir / "api_keys.json").read_text(encoding="utf-8"))
                self.assertEqual(saved["gemini_api_key"], "x" * 40)      # other settings intact
                # The cache said "verified" — after a restart that is NOT shown as connected.
                self.assertEqual(m2.get("claude").state, conn.CHECKING)
                self.assertEqual(m2.mcp_servers()[0].state, conn.CHECKING)
                self.assertFalse(m2.live_status()["connected"])
                # A quick check with a stale last_ok downgrades to READY, never CONNECTED.
                with patch.object(claude_brain, "find_claude_cli", return_value=Path("claude.exe")), \
                        patch.object(claude_brain, "get_claude_auth",
                                     return_value={"loggedIn": True, "subscriptionType": "pro",
                                                   "authMethod": "claude.ai"}):
                    st = m2.check_claude(deep=False)
                self.assertEqual(st.state, conn.READY)
                m2._pool.shutdown(wait=False)


_real_routing = conn.routing_settings


# ── Browser policy ────────────────────────────────────────────────────────────

class BrowserPolicyTests(unittest.TestCase):
    def setUp(self):
        browser_policy.reset_for_tests()

    def test_browser_tools_are_classified(self):
        self.assertIsNotNone(browser_policy.browser_use("browser_control", {"action": "go_to"}))
        self.assertIsNotNone(browser_policy.browser_use("youtube_video", {}))
        self.assertIsNotNone(browser_policy.browser_use("open_app", {"app_name": "Google Chrome"}))
        self.assertIsNotNone(browser_policy.browser_use("open_app", {"app_name": "gmail.com"}))
        self.assertIsNone(browser_policy.browser_use("open_app", {"app_name": "Spotify"}))
        self.assertIsNotNone(browser_policy.browser_use("computer_control", {"action": "navigate"}))
        self.assertIsNone(browser_policy.browser_use("computer_control", {"action": "browser_observe"}))
        self.assertIsNone(browser_policy.browser_use("computer_control", {"action": "youtube_pause"}))
        self.assertIsNone(browser_policy.browser_use("weather_report", {"city": "Novi Sad"}))
        self.assertIsNotNone(browser_policy.browser_use("weather_report", {"city": "X", "action": "open"}))

    def test_gate_blocks_without_intent_and_asks(self):
        run, confirm = MagicMock(), MagicMock()
        ok, msg = browser_policy.gate("browser_control", {"action": "go_to", "url": "https://mail.google.com"},
                                      run, policy="ask", confirm=confirm)
        self.assertFalse(ok)
        self.assertTrue(msg.startswith("[BROWSER_BLOCKED]"))
        confirm.assert_called_once()
        self.assertIs(confirm.call_args[0][3], run)   # runs only if the user confirms
        run.assert_not_called()

    def test_gate_explicit_only_never_asks(self):
        confirm = MagicMock()
        ok, msg = browser_policy.gate("youtube_video", {}, MagicMock(), policy="explicit_only", confirm=confirm)
        self.assertFalse(ok)
        confirm.assert_not_called()

    def test_explicit_words_allow(self):
        for text in ("open youtube and play lofi", "otvori sajt novosti.rs", "open a new tab in chrome",
                     "pusti na jutjubu", "go to https://example.com"):
            browser_policy.reset_for_tests()
            browser_policy.note_user_text(text)
            ok, _ = browser_policy.gate("browser_control", {"action": "go_to"}, MagicMock(), confirm=MagicMock())
            self.assertTrue(ok, text)

    def test_ordinary_requests_do_not_count_as_intent(self):
        for text in ("check my email", "what's the weather in Belgrade", "play some music",
                     "search for flights to Rome"):
            self.assertFalse(browser_policy.has_explicit_browser_intent(text), text)

    def test_voice_fragments_count_before_turn_completes(self):
        browser_policy.note_user_fragment("open")
        browser_policy.note_user_fragment("chrome please")
        self.assertTrue(browser_policy.has_explicit_browser_intent())


class VoicePathGateTests(unittest.TestCase):
    """The Gemini Live tool path goes through the same gate."""

    def make_live(self):
        from main import CarLive
        live = CarLive.__new__(CarLive)
        live.ui = MagicMock(muted=False)
        live._conn = make_manager()
        live._action_registry = MagicMock()
        live._action_registry.has.return_value = True
        live._action_registry.scheduling.return_value = None
        live._plugin_registry = MagicMock()
        live._plugin_registry.has.return_value = False
        live._plugin_registry.scheduling.return_value = None
        live.speak = MagicMock()
        live.speak_error = MagicMock()
        live.session = None
        return live

    def setUp(self):
        browser_policy.reset_for_tests()

    def test_unrequested_browser_tool_is_not_executed(self):
        live = self.make_live()
        fc = SimpleNamespace(id="1", name="browser_control", args={"action": "go_to", "url": "https://gmail.com"})
        with BrowserTrap() as trap, patch("main.confirm_gate.request") as confirm, \
                patch("main._connections.routing_settings", return_value=dict(ROUTING_DEFAULT)):
            resp = asyncio.run(live._execute_tool(fc))
        self.assertTrue(resp.response["result"].startswith("[BROWSER_BLOCKED]"))
        live._action_registry.run.assert_not_called()
        confirm.assert_called_once()
        self.assertEqual(trap.launches, [])

    def test_requested_browser_tool_runs(self):
        live = self.make_live()
        live._action_registry.run.return_value = "Opened."
        browser_policy.note_user_text("open chrome and go to github.com")
        fc = SimpleNamespace(id="2", name="browser_control", args={"action": "go_to", "url": "https://github.com"})
        with patch("main._connections.routing_settings", return_value=dict(ROUTING_DEFAULT)):
            resp = asyncio.run(live._execute_tool(fc))
        self.assertEqual(resp.response["result"], "Opened.")
        live._action_registry.run.assert_called_once()

    def test_ask_claude_only_is_not_substituted_when_claude_is_down(self):
        live = self.make_live()
        live._conn = make_manager(claude=conn.DISCONNECTED)
        with patch("main._connections.routing_settings", return_value=dict(ROUTING_DEFAULT)):
            msg = live._gate_tool("ask_claude", {"prompt": "hi", "force_brain": "claude"}, MagicMock())
        self.assertIn("Not substituting", msg)
        self.assertIn("claude auth login", msg)

    def test_typed_command_when_live_is_down_still_routes(self):
        live = self.make_live()
        live._awake = True
        live._asst_name = "ULTRON"
        live._session_log = []
        live._router = MagicMock()
        live._router.route.return_value = command_router.RouteResult(
            False, "blocked", error="Claude is not connected.")
        live._on_user_command("hello")
        live.ui.write_log.assert_called_with("ERR: Claude is not connected.")

    def test_internal_message_reports_live_down_instead_of_silence(self):
        live = self.make_live()
        live._loop = None
        live._on_text_command("[FILE_UPLOADED] x")
        self.assertIn("Gemini Live is not connected", live.ui.write_log.call_args[0][0])


class FixedToolsDoNotOpenBrowser(unittest.TestCase):

    def test_weather_returns_data_without_browser(self):
        from actions import weather_report
        geo = MagicMock()
        geo.json.return_value = {"results": [{"name": "Novi Sad", "country": "Serbia",
                                              "latitude": 45.25, "longitude": 19.84}]}
        fc = MagicMock()
        fc.json.return_value = {
            "current": {"time": "2026-10-10T01:30", "temperature_2m": 16.1, "apparent_temperature": 14.8,
                        "relative_humidity_2m": 63, "weather_code": 1, "wind_speed_10m": 6.8},
            "daily": {"time": ["2026-10-10", "2026-10-11"], "weather_code": [3, 61],
                      "temperature_2m_max": [19, 17], "temperature_2m_min": [9, 10],
                      "precipitation_probability_max": [10, 70]}}
        with BrowserTrap() as trap, patch.object(weather_report.requests, "get", side_effect=[geo, fc]):
            out = weather_report.weather_action({"city": "Novi Sad"})
        self.assertIn("16.1°C", out)
        self.assertIn("Tomorrow: light rain", out)
        self.assertEqual(trap.launches, [])

    def test_weather_failure_does_not_fall_back_to_browser(self):
        from actions import weather_report
        with BrowserTrap() as trap, patch.object(weather_report.requests, "get", side_effect=OSError("offline")):
            out = weather_report.weather_action({"city": "Novi Sad"})
        self.assertIn("Nothing was opened in the browser", out)
        self.assertEqual(trap.launches, [])

    def test_video_resolution_failure_does_not_open_browser(self):
        from actions import video_player
        player = MagicMock()
        with BrowserTrap() as trap, \
                patch.object(video_player, "_resolve_youtube", side_effect=RuntimeError("no stream")), \
                patch.object(video_player, "_still_wanted", return_value=True):
            video_player._play_youtube(player, "lofi beats", 1)
        self.assertEqual(trap.launches, [])


# ── Claude bridge ─────────────────────────────────────────────────────────────

class ClaudeBridgeTests(unittest.TestCase):

    def test_no_shell_and_prompt_on_stdin(self):
        fake = MagicMock(returncode=0, stdout="2.1", stderr="")
        with patch.object(claude_brain, "find_claude_cli", return_value=Path(r"C:\x\claude.exe")), \
                patch.object(claude_brain.subprocess, "run", return_value=fake) as run:
            claude_brain.run_claude_command(["-p"], stdin_text='x" & echo INJECTED & rem "')
        cmd = run.call_args[0][0]
        self.assertTrue(cmd[0].endswith("claude.exe"))
        self.assertNotIn("cmd.exe", cmd)
        self.assertEqual(run.call_args.kwargs["input"], 'x" & echo INJECTED & rem "')
        self.assertNotIn("INJECTED", " ".join(cmd))

    def test_cmd_shim_refuses_special_characters(self):
        with patch.object(claude_brain, "find_claude_cli", return_value=Path(r"C:\x\claude.cmd")), \
                patch.object(claude_brain.subprocess, "run") as run:
            code, _, err = claude_brain.run_claude_command(["--model", 'a" & calc'])
        self.assertEqual(code, 2)
        run.assert_not_called()

    def test_ask_claude_is_least_privilege(self):
        out = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "hi",
                          "permission_denials": [], "modelUsage": {"claude-x": {}}})
        with patch.object(claude_brain, "run_claude_command", return_value=(0, out, "")) as rc, \
                patch.object(claude_brain, "_update_status"):
            res = claude_brain.ask_claude("my private prompt", allowed_tools=["mcp__claude_ai_Gmail__search_threads"])
        args = rc.call_args[0][0]
        self.assertTrue(res["success"])
        self.assertNotIn("--dangerously-skip-permissions", args)
        self.assertIn("--no-chrome", args)
        self.assertEqual(args[args.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(args[args.index("--allowedTools") + 1], "mcp__claude_ai_Gmail__search_threads")
        self.assertEqual(args[args.index("--tools") + 1], "ToolSearch")
        self.assertNotIn("my private prompt", args)
        self.assertEqual(rc.call_args.kwargs["stdin_text"], "my private prompt")

    def test_mcp_allowlist_has_no_destructive_tools(self):
        for service, tools in claude_brain.SAFE_MCP_TOOLS.items():
            for t in tools:
                for bad in ("send", "trash", "delete", "share", "forward", "update_label", "spam"):
                    self.assertNotIn(bad, t.lower(), f"{service}: {t}")

    def test_leaked_api_key_is_not_passed_in_subscription_mode(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "leaked", "ANTHROPIC_AUTH_TOKEN": "t"}), \
                patch.object(claude_brain, "_auth_mode", return_value="subscription"):
            env = claude_brain._child_env()
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "chosen"}), \
                patch.object(claude_brain, "_auth_mode", return_value="api_key"):
            self.assertEqual(claude_brain._child_env()["ANTHROPIC_API_KEY"], "chosen")

    def test_nested_session_variables_are_removed(self):
        with patch.dict(os.environ, {"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "x"}):
            env = claude_brain._child_env()
        self.assertNotIn("CLAUDECODE", env)
        self.assertNotIn("CLAUDE_CODE_ENTRYPOINT", env)

    def test_auth_failure_reports_no_fake_account(self):
        with patch.object(claude_brain, "run_claude_command", return_value=(127, "", "")):
            auth = claude_brain.get_claude_auth()
        self.assertFalse(auth["loggedIn"])
        self.assertEqual(auth["email"], "")
        self.assertEqual(auth["subscriptionType"], "")
        self.assertIn("not installed", auth["error"])

    def test_invalid_key_is_an_error_not_available(self):
        out = json.dumps({"type": "result", "subtype": "success", "is_error": True,
                          "result": "Invalid API key · Fix external API key"})
        with patch.object(claude_brain, "run_claude_command", return_value=(1, out, "")), \
                patch.object(claude_brain, "_update_status") as upd:
            res = claude_brain.ask_claude("hi")
        self.assertFalse(res["success"])
        self.assertIn("Invalid API key", res["error"])
        self.assertNotIn("claude_available", upd.call_args.kwargs)

    def test_probe_parses_init_and_result(self):
        lines = [json.dumps({"type": "system", "subtype": "init", "model": "claude-haiku",
                             "mcp_servers": [{"name": "claude.ai Gmail", "status": "connected"}],
                             "tools": ["mcp__claude_ai_Gmail__search_threads", "Read"]}),
                 json.dumps({"type": "result", "subtype": "success", "is_error": False,
                             "result": "ULTRON_LINK_OK"})]
        with patch.object(claude_brain, "run_claude_command", return_value=(0, "\n".join(lines), "")) as rc:
            p = claude_brain.probe()
        self.assertTrue(p["ok"])
        self.assertEqual(p["mcp_tools"], ["mcp__claude_ai_Gmail__search_threads"])
        self.assertEqual(rc.call_args.kwargs["env_extra"], {"MCP_CONNECTION_NONBLOCKING": "false"})
        self.assertIn("haiku", rc.call_args[0][0])

    def test_probe_timeout_is_not_success(self):
        with patch.object(claude_brain, "run_claude_command", return_value=(-1, "", "timed out")):
            p = claude_brain.probe()
        self.assertFalse(p["ok"])
        self.assertIn("timed out", p["error"])

    def test_mcp_list_parsing(self):
        out = ("Checking MCP server health…\n\n"
               "claude.ai Gmail: https://gmailmcp.googleapis.com/mcp/v1 - ✔ Connected\n"
               "local: python server.py - ✗ Failed to connect\n"
               "claude.ai Drive: https://d/mcp - ⚠ Needs authentication\n")
        with patch.object(claude_brain, "run_claude_command", return_value=(0, out, "")):
            servers = claude_brain.mcp_list()
        self.assertEqual([s["status"] for s in servers], ["connected", "failed", "needs_auth"])

    def test_execute_claude_only_never_substitutes(self):
        with patch.object(claude_brain, "ask_claude", return_value={"success": False, "error": "down",
                                                                    "limit_hit": False}), \
                patch.object(claude_brain, "load_cached_status", return_value={}), \
                patch.object(claude_brain, "ask_antigravity") as anti:
            res = claude_brain.execute("x", preferred_brain="claude")
        self.assertFalse(res["success"])
        anti.assert_not_called()

    def test_execute_auto_fallback_is_announced(self):
        with patch.object(claude_brain, "ask_claude", return_value={"success": False, "error": "down",
                                                                    "limit_hit": False}), \
                patch.object(claude_brain, "load_cached_status", return_value={}), \
                patch.object(claude_brain, "_fallback_allowed", return_value=True), \
                patch.object(claude_brain, "ask_antigravity", return_value={"success": True, "output": "ok"}):
            res = claude_brain.execute("x")
        self.assertEqual(res["brain_used"], "antigravity")
        self.assertIn("fallback", res["note"])

    def test_execute_auto_respects_disabled_fallback(self):
        with patch.object(claude_brain, "ask_claude", return_value={"success": False, "error": "down",
                                                                    "limit_hit": False}), \
                patch.object(claude_brain, "load_cached_status", return_value={}), \
                patch.object(claude_brain, "_fallback_allowed", return_value=False), \
                patch.object(claude_brain, "ask_antigravity") as anti:
            res = claude_brain.execute("x")
        self.assertFalse(res["success"])
        anti.assert_not_called()

    def test_claude_mcp_action_checks_before_running(self):
        from actions import claude_mcp
        down = make_manager(claude=conn.DISCONNECTED)
        with patch.object(claude_mcp.conn, "manager", return_value=down), \
                patch.object(claude_brain, "execute_claude_mcp_query") as q:
            out = claude_mcp.claude_mcp({"service": "gmail", "task": "count unread"})
        self.assertIn("Claude is disconnected", out)
        self.assertIn("Do not open a browser", out)
        q.assert_not_called()
        no_mcp = make_manager(mcp=[("Gmail", conn.ERROR, "gmail")])
        with patch.object(claude_mcp.conn, "manager", return_value=no_mcp), \
                patch.object(claude_brain, "execute_claude_mcp_query") as q:
            out = claude_mcp.claude_mcp({"service": "gmail", "task": "count unread"})
        self.assertIn("Gmail MCP server is failing", out)
        q.assert_not_called()


# ── Connection honesty ────────────────────────────────────────────────────────

class ConnectionStatusTests(_Base):

    def test_never_connected_without_verification(self):
        m = make_manager(claude=conn.READY, gemini=conn.READY)
        snap = m.snapshot()
        self.assertFalse(snap["claude"]["verified"])
        self.assertTrue(snap["claude"]["usable"])

    def test_verified_status_ages_to_ready(self):
        m = make_manager(claude=conn.CONNECTED)
        m._services["claude"].last_ok = 1.0           # long ago
        self.assertEqual(m.get("claude").state, conn.READY)

    def test_live_report_drives_gemini_and_active_provider(self):
        m = make_manager(claude=conn.DISCONNECTED, gemini=conn.READY)
        m.report_live(True, "models/gemini-3.1-flash-live-preview")
        self.assertEqual(m.get("gemini").state, conn.CONNECTED)
        self.assertEqual(m.active_provider(), "gemini")
        m.report_live(False, error="OSError: network")
        self.assertIn("network", m.live_status()["error"])

    def test_task_result_updates_provider_health(self):
        m = make_manager(claude=conn.READY)
        m.report_provider_result("claude", False, "You've hit your session limit")
        self.assertEqual(m.get("claude").state, conn.LIMITED)
        m.report_provider_result("claude", True, model="claude-opus")
        self.assertEqual(m.get("claude").state, conn.CONNECTED)

    def test_manual_commands_are_recognised(self):
        cases = {"check AI connections": "check", "ULTRON, connect Claude": "claude",
                 "reconnect Gemini": "gemini", "reconnect MCP": "mcp", "show available MCP tools": "mcp_tools",
                 "open Chrome manually": "chrome", "provjeri konekcije": "check",
                 "view connection errors": "errors"}
        for text, want in cases.items():
            self.assertEqual(command_router.detect_manual(text), want, text)
        self.assertIsNone(command_router.detect_manual("what is the capital of France"))

    def test_status_text_reports_real_states(self):
        m = make_manager(claude=conn.DISCONNECTED, mcp=[("Gmail", conn.CONNECTED, "gmail")])
        r, _ = make_router(m)
        txt = r.status_text()
        self.assertIn("Claude: DISCONNECTED", txt)
        self.assertIn("MCP: 1/1 servers connected", txt)


# ── Graph data ────────────────────────────────────────────────────────────────

class GraphModelTests(unittest.TestCase):

    def test_graph_contains_only_real_categories(self):
        from core.ui_command_center import build_graph
        snap = {"claude": {"state": "CONNECTED"}, "gemini": {"state": "READY"},
                "gemini_live": {"connected": False},
                "mcp": [{"label": "Gmail", "state": "CONNECTED", "tools": ["mcp__claude_ai_Gmail__search_threads"]}],
                "mcp_summary": {"configured": 1, "connected": 1, "tools": 1}}
        decl = [{"name": "computer_settings", "description": "x"}, {"name": "dev_agent", "description": "y"},
                {"name": "smartthings_control", "description": "z"}]
        plugins = [{"name": "smartthings_control", "valid": True, "enabled": False}]
        nodes, edges = build_graph(snap, decl, plugins, None, [], "")
        self.assertIn("tool:computer_settings", nodes)
        self.assertIn("tool:dev_agent", nodes)                  # agent tool → Agents hub
        self.assertEqual(nodes["tool:dev_agent"].parent, "agents")
        self.assertNotIn("tool:smartthings_control", nodes)     # plugins are not double-listed
        self.assertEqual(nodes["plugin:smartthings_control"].status, "DISABLED")
        self.assertNotIn("memory", nodes)                        # no memory source → no hub
        self.assertEqual(nodes["mcp:Gmail"].status, "CONNECTED")
        self.assertEqual(nodes["mcpt:mcp__claude_ai_Gmail__search_threads"].parent, "mcp:Gmail")
        self.assertEqual(nodes["gemini.live"].status, "DISCONNECTED")
        ids = set(nodes)
        for e in edges:
            self.assertIn(e.a, ids)
            self.assertIn(e.b, ids)

    def test_layout_is_deterministic(self):
        from core.ui_command_center import build_graph
        a, _ = build_graph({}, [{"name": "t1"}, {"name": "t2"}], [], {"notes": {"k": {"value": "v"}}}, ["ai"], "")
        b, _ = build_graph({}, [{"name": "t1"}, {"name": "t2"}], [], {"notes": {"k": {"value": "v"}}}, ["ai"], "")
        self.assertEqual({k: n.pos for k, n in a.items()}, {k: n.pos for k, n in b.items()})


if __name__ == "__main__":
    unittest.main()
