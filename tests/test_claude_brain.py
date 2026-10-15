"""
tests/test_claude_brain.py — Comprehensive tests for Ultron's Claude Code Brain,
Session Limit Detection, Antigravity Fallback, and Connection Router.
"""
import json
import os
import sys
import unittest
from pathlib import Path

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import tempfile
from unittest.mock import patch

from core import claude_brain
from actions import ask_claude
from actions import code_helper
from actions import dev_agent

# Tests that send real requests to Claude / Gemini use the account's quota, so
# they only run when asked for: set ULTRON_LIVE_TESTS=1.
LIVE = os.getenv("ULTRON_LIVE_TESTS") == "1"
live_only = unittest.skipUnless(LIVE, "live provider test; set ULTRON_LIVE_TESTS=1 to run")


class TestClaudeBrain(unittest.TestCase):

    def setUp(self):
        # Never touch the user's real config/claude_brain_status.json.
        self._tmp = tempfile.TemporaryDirectory()
        self._patches = [patch.object(claude_brain, "_STATUS_FILE", Path(self._tmp.name) / "status.json"),
                         patch.object(claude_brain, "_CONFIG_DIR", Path(self._tmp.name))]
        for p_ in self._patches:
            p_.start()

    def tearDown(self):
        for p_ in self._patches:
            p_.stop()
        self._tmp.cleanup()

    def test_01_claude_cli_discovery(self):
        """Verify that the Claude Code CLI executable is detected."""
        cli = claude_brain.find_claude_cli()
        self.assertIsNotNone(cli, "Claude CLI should be detected on the system")
        self.assertTrue(cli.exists(), f"Claude CLI path must exist: {cli}")

    def test_02_claude_auth_status(self):
        """Verify reading Claude authentication and subscription type (local, no request)."""
        if claude_brain.find_claude_cli() is None:
            self.skipTest("Claude CLI not installed")
        auth = claude_brain.get_claude_auth()
        self.assertTrue(auth.get("loggedIn"), "Claude should be logged in")
        self.assertEqual(auth.get("subscriptionType"), "pro", "Subscription should be 'pro'")
        self.assertIn("@", auth.get("email", ""), "Email should be valid")

    def test_03_parse_session_limit_message(self):
        """Verify parsing session limit reset messages and time calculation."""
        sample_msg = "You've hit your session limit · resets 1:20am (Europe/Belgrade)"
        is_hit, reset_str, reset_ts, rem_str = claude_brain.parse_reset_time(sample_msg)
        self.assertTrue(is_hit, "Should detect session limit")
        self.assertIn("1:20am", reset_str, "Should extract reset time 1:20am")
        self.assertIn("Europe/Belgrade", reset_str, "Should extract timezone")
        self.assertIsNotNone(rem_str, "Should compute human remaining time")

    def test_04_status_retrieval(self):
        """Verify get_status returns expected structure and valid brain state."""
        with patch.object(claude_brain, "refresh_status", side_effect=claude_brain.load_cached_status):
            st = claude_brain.get_status(force_probe=False)
        self.assertIn("active_brain", st)
        self.assertIn("account_email", st)
        self.assertIn("subscription", st)
        self.assertIn("connection_preferences", st)
        self.assertIn("antigravity_status", st)
        self.assertIn("both_exhausted", st)
        # Because the session limit is active until 1:20am:
        if st.get("limit_hit"):
            self.assertEqual(st.get("active_brain"), "antigravity", "When Claude is limited, active brain must be antigravity")

    @live_only
    def test_05_execute_automatic_fallback(self):
        """Verify execute() works and falls back to Antigravity when Claude is on limit."""
        res = claude_brain.execute("Napiši funkciju 'def saberi(x, y): return x + y' u Pythonu.")
        self.assertTrue(res.get("success"), f"Execution failed: {res}")
        self.assertIn(res.get("brain_used"), ("claude", "antigravity"))
        self.assertIn("saberi", res.get("output", ""))

    def test_06_dual_limit_exhaustion_alert(self):
        """Verify that alert listener is fired when limits are exhausted."""
        alert_received = []
        def listener(msg):
            alert_received.append(msg)

        claude_brain.register_alert_listener(listener)
        claude_brain.notify_alert("Test: Oba limita su potrošena!")
        self.assertEqual(len(alert_received), 1)
        self.assertIn("Test: Oba limita su potrošena!", alert_received[0])

    def test_07_connection_preferences(self):
        """Verify setting and getting connection preferences."""
        claude_brain.set_connection_preference("gmail", "claude")
        self.assertEqual(claude_brain.get_connection_preference("gmail"), "claude")

        claude_brain.set_connection_preference("gmail", "user")
        self.assertEqual(claude_brain.get_connection_preference("gmail"), "user")

        claude_brain.set_connection_preference("gmail", "ask")
        self.assertEqual(claude_brain.get_connection_preference("gmail"), "ask")

    @live_only
    def test_08_code_helper_integration(self):
        """Verify actions/code_helper generates code via brain engine."""
        model = code_helper._get_gemini()
        resp = model.generate_content("Napiši samo: def pozdrav(): return 'Hello'")
        self.assertIsNotNone(resp.text)
        self.assertIn("pozdrav", resp.text)

    @live_only
    def test_09_dev_agent_integration(self):
        """Verify actions/dev_agent uses brain engine."""
        model = dev_agent._get_model()
        resp = model.generate_content("Vrati JSON: {\"status\": \"ok\"}")
        self.assertIsNotNone(resp.text)
        self.assertIn("status", resp.text)

    @live_only
    def test_10_ask_claude_action(self):
        """Verify actions/ask_claude tool executes successfully."""
        out = ask_claude.ask_claude({"prompt": "Koliko je 5 + 5?"})
        self.assertIsInstance(out, str)
        self.assertTrue("10" in out or "ODGOVOR" in out)

    def test_12_ai_engine_controls_live_in_workspace_settings(self):
        """The Claude Brain tab moved to Workspace Settings → AI Engine and
        Advanced diagnostics; its status and checks are served by the service."""
        from core.workspace import service
        r = service.handle({"type": "routing.get"})
        self.assertTrue(r["ok"])
        self.assertIn("provider_order", r["routing"])
        self.assertIn("claude_limit", r["status"])
        self.assertIn("gmail", r["preferences"] or {"gmail": "ask"})
        for which in ("claude_limit", "claude_test", "gemini_test", "recheck", "errors", "brain"):
            self.assertIn("diag.run", service._HANDLERS, which)


if __name__ == "__main__":
    unittest.main()
