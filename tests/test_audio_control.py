import unittest
from unittest.mock import patch, MagicMock
from audio_control.audio_manager import AudioManager
import json

class TestAudioControl(unittest.TestCase):
    def setUp(self):
        self.manager = AudioManager()
        self.manager.state = MagicMock()

    @patch('audio_control.device_discovery.AudioUtilities.GetAllDevices')
    def test_list_outputs(self, mock_get_all):
        mock_dev = MagicMock()
        mock_dev.FriendlyName = "LG TV"
        mock_state = MagicMock()
        mock_state.value = 1
        mock_dev.state = mock_state
        mock_get_all.return_value = [mock_dev]

        res = self.manager.handle_action("list_outputs")
        self.assertTrue(res["success"])
        self.assertIn("LG TV", res["devices"])

    @patch('audio_control.device_discovery.AudioUtilities.GetSpeakers')
    def test_get_current(self, mock_speakers):
        mock_dev = MagicMock()
        mock_dev.FriendlyName = "Headphones"
        mock_speakers.return_value = mock_dev

        res = self.manager.handle_action("get_current")
        self.assertTrue(res["success"])
        self.assertEqual(res["current_device"], "Headphones")

    @patch('audio_control.device_discovery.DeviceDiscovery.get_default_output')
    @patch('audio_control.device_matcher.DeviceMatcher.match_device')
    @patch('audio_control.device_switcher.AudioUtilities.SetDefaultDevice')
    def test_switch_output(self, mock_set_default, mock_match, mock_get_default):
        mock_current = MagicMock()
        mock_current.id = "id1"
        mock_current.FriendlyName = "Speakers"
        mock_get_default.return_value = mock_current
        
        mock_target = MagicMock()
        mock_target.id = "id2"
        mock_target.FriendlyName = "LG TV"
        mock_match.return_value = mock_target
        mock_get_default.side_effect = [mock_current, mock_target]

        res = self.manager.handle_action("switch_output", device="televizor")
        self.assertTrue(res["success"])
        self.assertEqual(res["previous_device"], "Speakers")
        self.assertEqual(res["current_device"], "LG TV")
        mock_set_default.assert_called_with("id2")

    @patch('audio_control.device_aliases.DeviceAliases._save')
    def test_set_alias(self, mock_save):
        with patch.object(self.manager.matcher, 'match_device') as mock_match:
            mock_target = MagicMock()
            mock_target.FriendlyName = "WH-1000XM5"
            mock_match.return_value = mock_target

            res = self.manager.handle_action("set_alias", device="slusalice", value="WH-1000XM5")
            self.assertTrue(res["success"])
            self.assertEqual(self.manager.aliases.get_target("slusalice"), "WH-1000XM5")

    def test_previous_output_requires_observed_change(self):
        self.manager.state.get_previous.return_value = "previous-id"
        current = MagicMock(id="current-id")
        with patch.object(self.manager.discovery, "get_default_output", return_value=current), patch.object(self.manager.switcher, "set_default", return_value=True):
            result = self.manager.handle_action("switch_previous")
        self.assertFalse(result["success"])
        self.manager.state.set_previous.assert_not_called()

if __name__ == '__main__':
    unittest.main()
