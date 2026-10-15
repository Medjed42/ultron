import unittest
from unittest.mock import patch, MagicMock
from computer_control.desktop.process_manager import ProcessManager
from computer_control.desktop.windows_controller import WindowsController
from computer_control.youtube.youtube_controller import YouTubeController
from computer_control.action_router import ActionRouter
from computer_control.plugin import handler

class TestComputerControl(unittest.TestCase):
    def setUp(self):
        self.router = ActionRouter()

    @patch('computer_control.desktop.process_manager.psutil.process_iter')
    def test_find_running_app(self, mock_process_iter):
        # Mock psutil
        mock_proc = MagicMock()
        mock_proc.info = {'pid': 1234, 'name': 'chrome.exe'}
        mock_process_iter.return_value = [mock_proc]

        pm = ProcessManager()
        result = pm.find_running_app("chrome")
        self.assertIsNotNone(result)
        self.assertEqual(result['pid'], 1234)
        self.assertEqual(result['name'], 'chrome.exe')

    @patch('computer_control.desktop.process_manager.psutil.process_iter')
    @patch('computer_control.desktop.windows_controller.WindowsController.focus_window')
    def test_focus_existing_app(self, mock_focus, mock_process_iter):
        mock_proc = MagicMock()
        mock_proc.info = {'pid': 1234, 'name': 'chrome.exe'}
        mock_process_iter.return_value = [mock_proc]
        mock_focus.return_value = True

        result = self.router._focus_or_open_app("chrome")
        self.assertTrue(result['success'])
        self.assertEqual(result['status'], "focused existing")
        mock_focus.assert_called_once()

    @patch('computer_control.desktop.process_manager.psutil.process_iter')
    @patch('computer_control.desktop.process_manager.subprocess.Popen')
    def test_launch_new_app(self, mock_popen, mock_process_iter):
        mock_process_iter.return_value = [] # no apps running
        mock_popen.return_value = MagicMock()

        with patch.object(self.router.windows_controller, "focus_window", return_value=False), patch("shutil.which", return_value="C:/Windows/notepad.exe"):
            result = self.router._focus_or_open_app("notepad")
        self.assertTrue(result['success'])
        self.assertEqual(result['status'], "launched new")
        mock_popen.assert_called_once()

    @patch('computer_control.desktop.windows_controller.WindowsController.get_active_window')
    def test_read_active_window(self, mock_active):
        mock_active.return_value = "YouTube - Google Chrome"
        result = self.router._observe()
        self.assertTrue(result['success'])
        self.assertEqual(result['active_window'], "YouTube - Google Chrome")
        self.assertEqual(result['active_app'], "Chrome")

    @patch('pywinauto.keyboard.send_keys')
    def test_keyboard_input(self, mock_write):
        result = self.router.route("type_text", value="hello world")
        self.assertTrue(result['success'])
        mock_write.assert_called_once_with("hello world", with_spaces=True, with_tabs=True, with_newlines=True, vk_packet=True)

    @patch('computer_control.desktop.windows_controller.pyautogui.scroll')
    def test_scrolling(self, mock_scroll):
        result = self.router.route("scroll_down")
        self.assertTrue(result['success'])
        mock_scroll.assert_called_with(-5)

    def test_youtube_pause_uses_observed_player(self):
        browser = MagicMock()
        browser.snapshot.return_value = {"title":"YouTube - Google Chrome"}
        controller = YouTubeController(browser)
        pause, play = MagicMock(), MagicMock()
        with patch.object(controller, "_button", side_effect=[pause, play]):
            result = controller.handle_action("youtube_pause")
        self.assertTrue(result["verified"])
        pause.click_input.assert_called_once()
        browser.press.assert_not_called()

    def test_youtube_pause_missing_state_never_toggles(self):
        browser = MagicMock()
        browser.snapshot.return_value = {"title":"YouTube - Google Chrome"}
        controller = YouTubeController(browser)
        with patch.object(controller, "_button", return_value=None):
            result = controller.handle_action("youtube_pause")
        self.assertFalse(result["success"])
        browser.press.assert_not_called()

    def test_plugin_handler(self):
        import json
        with patch.object(self.router, 'route', return_value={"success": True}) as mock_route:
            # We must patch the global _router in plugin
            import computer_control.plugin as plugin
            with patch.object(plugin, "_router", self.router):
                res_str = handler({"action": "observe"})
            res = json.loads(res_str)
            self.assertTrue(res['success'])
            mock_route.assert_called_with("observe", None, None)

if __name__ == '__main__':
    unittest.main()
