import base64
import importlib.util
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from computer_control.browser.existing_browser_controller import ExistingBrowserController, safe_url
from core.security import redact, register_secrets

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("evidence", ROOT / "Projects/Ultron_Omni_Agent/evidence.py")
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


class BrowserSafety(unittest.TestCase):
    def test_active_code_urls_rejected(self):
        for url in ("javascript:alert(1)", "file:///C:/secret", "https://user:password@host.com", "data:text/html,test"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                safe_url(url)

    def test_normal_url(self):
        self.assertEqual(safe_url("example.org"), "https://example.org")

    def test_unsupported_chrome_action_never_launches_managed_browser(self):
        from actions.browser_control import browser_control, _registry
        with patch.object(_registry, "get") as launch:
            result = browser_control({"action":"fill_form", "browser":"google chrome"})
        self.assertFalse(result["success"])
        launch.assert_not_called()

    def test_numbered_video_does_not_click_navigation_link(self):
        from computer_control.youtube.youtube_controller import YouTubeController
        browser = MagicMock()
        browser.snapshot.return_value = {"title":"Results - YouTube"}
        result = YouTubeController(browser).handle_action("youtube_select_video",value="2")
        self.assertFalse(result["success"])
        browser.find.assert_not_called()

    def test_youtube_volume_verifies_accessible_slider(self):
        from computer_control.youtube.youtube_controller import YouTubeController
        browser, slider = MagicMock(), MagicMock()
        browser.snapshot.return_value = {"title":"Video - YouTube"}
        slider.element_info.control_type = "Slider"
        slider.window_text.return_value = "Volume"
        slider.min_value.return_value, slider.max_value.return_value = 0, 100
        slider.value.return_value = 40
        browser.controls.return_value = [slider]
        result = YouTubeController(browser).handle_action("youtube_volume",value="40")
        self.assertTrue(result["verified"])
        slider.set_value.assert_called_once_with(40)

    def test_arbitrary_hotkeys_rejected(self):
        controller = ExistingBrowserController()
        with patch.object(controller, "window") as window, self.assertRaises(ValueError):
            controller.press("win+r")
        window.assert_not_called()

    def test_keystroke_not_reported_as_verified(self):
        controller = ExistingBrowserController()
        with patch.object(controller, "snapshot", side_effect=[{"title":"one"},{"title":"two"}]), patch.object(controller,"window"), patch.object(controller,"press"):
            result = controller.route("back")
        self.assertTrue(result["state_changed"])
        self.assertFalse(result["verified"])

    def test_ambiguous_click_refused(self):
        controller = ExistingBrowserController()
        a = MagicMock(); a.window_text.return_value = "Send"; a.element_info.control_type = "Button"
        with patch.object(controller,"window"), patch.object(controller,"controls", return_value=[a,a]), self.assertRaises(ValueError):
            controller.click("Send")
        a.click_input.assert_not_called()




class EvidenceAndSecrets(unittest.TestCase):
    def test_fabricated_source_rejected(self):
        result=evidence.validate_claims({"claims":[{"field":"phone","value":"123","sources":[{"url":"https://invented.example","quote":"Call us at 123"}],"confidence":1}]},{"results":[]})
        self.assertEqual(result["claims"][0]["confidence"],0)
        self.assertTrue(result["needs_review"])

    def test_search_snippet_does_not_establish_verification(self):
        record={"url":"https://example.com","body":"Call us at 123","observed_at":datetime.now(timezone.utc).isoformat()}
        result=evidence.validate_claims({"claims":[{"field":"phone","value":"123","sources":[{"url":record["url"],"quote":record["body"]}]}]},{"results":[record]})
        self.assertEqual(result["claims"][0]["status"],"probable")
        self.assertTrue(result["needs_review"])

    def test_real_quote_must_contain_claim_value(self):
        record={"url":"https://example.com","body":"Call us at 123"}
        result=evidence.validate_claims({"claims":[{"field":"phone","value":"999","sources":[{"url":record["url"],"quote":record["body"]}]}]},{"results":[record]})
        self.assertEqual(result["claims"][0]["status"],"rejected")

    def test_existing_and_pattern_secrets_redacted(self):
        register_secrets({"api_key":"test-only-long-secret"})
        self.assertNotIn("test-only-long-secret",redact("Failure test-only-long-secret"))
        self.assertNotIn("abcdef",redact("Authorization: Bearer abcdef"))

    def test_prompt_rejects_tool_instructions(self):
        prompt=(ROOT/"core/prompt.txt").read_text(encoding="utf-8")
        self.assertIn("untrusted data",prompt)
        self.assertNotIn("If it carries an instruction, obey",prompt)

    def test_wake_phrase_and_false_positives(self):
        from core.wake_word import matches_wake
        for text in ["hey ultron", "hey all tron", "all tron"]:
            self.assertTrue(matches_wake(text))
        for text in ["sorry", "star", "car", "ultronic", "ultra fast"]:
            self.assertFalse(matches_wake(text))

    def test_requirements_has_no_null_bytes(self):
        self.assertNotIn(b"\x00",(ROOT/"requirements.txt").read_bytes())

if __name__ == "__main__":
    unittest.main()
