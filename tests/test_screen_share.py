"""
Tests for Ultron Screen Sharing, Guidance Pointer, and Screen Automation Subsystems.
"""
import unittest
from unittest.mock import MagicMock, patch

from core.screen_share import ScreenShareManager, SYNONYMS
from core.screen_overlay import AnimatedScreenArrowOverlay
from actions.screen_share import handler, TOOL
from computer_control.action_router import ActionRouter


class ScreenShareManagerTests(unittest.TestCase):
    def setUp(self):
        self.mgr = ScreenShareManager()
        self.mgr.set_active(False)

    def test_state_toggle(self):
        self.assertFalse(self.mgr.is_active())
        self.assertTrue(self.mgr.set_active(True))
        self.assertTrue(self.mgr.is_active())
        self.assertFalse(self.mgr.set_active(False))
        self.assertFalse(self.mgr.is_active())
        self.assertTrue(self.mgr.toggle())
        self.assertTrue(self.mgr.is_active())
        self.assertFalse(self.mgr.toggle())

    def test_ui_callbacks(self):
        mock_ui = MagicMock()
        mock_ptr = MagicMock()
        self.mgr.register_ui_callback(mock_ui)
        self.mgr.register_pointer_callback(mock_ptr)

        self.mgr.set_active(True)
        mock_ui.assert_called_with(True)

        self.mgr.show_pointer(100, 200, "SETTINGS", [90, 190, 110, 210], 4.0)
        mock_ptr.assert_called_with(100, 200, "SETTINGS", [90, 190, 110, 210], 4.0)

    def test_clean_query_serbian_and_english(self):
        cases = [
            ("e ultrone gde je ovde settings", "settings"),
            ("gde se nalazi dugme za search", "search"),
            ("pokaži mi gde je dugme close", "close"),
            ("gde je ovde podešavanja?", "podešavanja"),
            ("where is the settings button?", "settings"),
            ("show me the search", "search"),
            ("point to the button save", "save"),
            ("Ultron molim te gde je pretraga", "pretraga"),
        ]
        for query, expected in cases:
            cleaned = self.mgr._clean_query(query)
            self.assertEqual(cleaned, expected, f"Query '{query}' was cleaned to '{cleaned}' instead of '{expected}'")

    def test_synonyms_lookup(self):
        variations = self.mgr._get_target_variations("settings")
        self.assertIn("settings", variations)
        self.assertTrue(any("podešavanj" in v or "podesavanj" in v for v in variations))
        self.assertIn("options", variations)

    def test_action_handler_routes(self):
        # 1. Status
        res_status = handler({"action": "status"})
        self.assertIn("isključeno", res_status)

        # 2. Share
        mock_player = MagicMock()
        res_share = handler({"action": "share"}, player=mock_player)
        self.assertIn("uključeno", res_share)
        self.assertTrue(self.mgr.is_active())
        mock_player.set_screen_share_active.assert_called_with(True)

        # 3. Stop
        res_stop = handler({"action": "stop"}, player=mock_player)
        self.assertIn("isključeno", res_stop)
        self.assertFalse(self.mgr.is_active())
        mock_player.set_screen_share_active.assert_called_with(False)

    @patch.object(ScreenShareManager, "locate_element")
    def test_point_to_workflow(self, mock_locate):
        mock_locate.return_value = {
            "found": True,
            "target": "settings",
            "x": 800,
            "y": 120,
            "bbox": [780, 100, 820, 140],
            "description": "Settings se nalazi u gornjem desnom uglu."
        }
        mock_ptr = MagicMock()
        self.mgr.register_pointer_callback(mock_ptr)

        res = handler({"action": "point_to", "target": "settings"})
        self.assertIn("Settings se nalazi", res)
        mock_ptr.assert_called_once()
        self.assertEqual(mock_ptr.call_args[0][0], 800)
        self.assertEqual(mock_ptr.call_args[0][1], 120)

    def test_computer_control_action_router_integration(self):
        router = ActionRouter()
        with patch.object(ScreenShareManager, "point_to") as mock_pt:
            mock_pt.return_value = {"success": True, "message": "Označeno strelicom"}
            res = router.route("point_to", target="settings")
            self.assertEqual(res, {"success": True, "message": "Označeno strelicom"})
            mock_pt.assert_called_with("settings")


if __name__ == "__main__":
    unittest.main()
