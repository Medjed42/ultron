"""Regression tests for exact approvals and reconnect/shutdown race conditions."""
import asyncio
import threading
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager, contextmanager
from core import confirm
from core.integrations import calendar
from test_phase2 import IsolatedState
import test_phase2 as fixtures

class ExactReviewTests(IsolatedState):
    def test_old_review_cannot_approve_new_request_with_same_key(self):
        old,new=MagicMock(),MagicMock()
        confirm.request("same","Old","Old details",old);token=confirm.pending_token()
        confirm.request("same","New","New details",new)
        confirm.resolve(True,expected_token=token)
        old.assert_not_called();new.assert_not_called()
        self.assertEqual(confirm.pending_title(),"New")
    def test_matching_review_executes_once(self):
        ran=threading.Event()
        confirm.request("one","Once","Exact details",ran.set);token=confirm.pending_token()
        confirm.resolve(True,expected_token=token)
        self.assertTrue(ran.wait(1))
        self.assertEqual(confirm.pending_token(),"")
    def test_token_travels_with_display_payload(self):
        show=MagicMock();confirm.bind(show,lambda:None,tokenized=True)
        confirm.request("x","Title","Full detail "*100,lambda:None)
        self.assertEqual(show.call_args.args[2],confirm.pending_token())
        self.assertEqual(len(show.call_args.args[1]),1200)
    def test_create_calendar_preview_includes_recurrence_alarm_and_conflicts(self):
        cal=MagicMock()
        cal.search.return_value=[]
        @contextmanager
        def connection():yield MagicMock()
        p={"action":"create","calendar_id":"c","title":"Fixture","start":"2026-10-24T10:00","end":"2026-10-24T11:00",
           "recurrence":"WEEKLY","count":3,"reminder_minutes":15}
        with patch.object(calendar,"connection",connection),patch.object(calendar,"_choose",return_value=cal):
            calendar.mutate(p)
        self.assertIn("WEEKLY × 3",confirm._pending.detail)
        self.assertIn("15 minutes before",confirm._pending.detail)
        self.assertIn("NOT been checked",confirm._pending.detail)
        confirm.resolve(False)
        cal.save_event.assert_not_called()
    def test_recurring_delete_requires_series_scope_and_detects_changed_event(self):
        cal=MagicMock();event=cal.event_by_uid.return_value;event.data=fixtures.CalendarTests.ICS
        @contextmanager
        def connection():yield MagicMock()
        p={"action":"delete","calendar_id":"c","uid":"recurring"}
        with patch.object(calendar,"connection",connection),patch.object(calendar,"_choose",return_value=cal):
            with self.assertRaises(ValueError):calendar.mutate(p)
            p["scope"]="series";calendar.mutate(p)
            callback=confirm._pending.run
            self.assertIn("2026-10-24",confirm._pending.detail)
            event.data=fixtures.CalendarTests.ICS.replace("Test recurring","Changed recurring")
            with self.assertRaises(RuntimeError):callback()
        event.delete.assert_not_called()
    def test_single_email_trash_also_requires_exact_review(self):
        import importlib.util
        if importlib.util.find_spec("plugins.ultron_mail") is None:
            self.skipTest("plugins/ultron_mail.py is disabled (renamed .disabled); Gmail now goes through claude_mcp")
        from plugins import ultron_mail
        api=MagicMock()
        api.users().messages().get.return_value.execute.return_value={"id":"m1","payload":{}}
        with patch.object(ultron_mail.mail,"service",return_value=api):
            result=ultron_mail.run({"action":"trash","message_id":"m1"})
            self.assertIn("CONFIRMATION_PENDING",result)
            self.assertIn("m1",confirm._pending.detail)
            confirm.resolve(False)
        api.users().messages().trash.assert_not_called()
    def test_calendar_create_requires_explicit_start_and_end(self):
        with patch.object(calendar,"connection") as connect:
            with self.assertRaises(ValueError):calendar.mutate({"action":"create","calendar_id":"c","title":"Test"})
        connect.assert_not_called()
    def test_nonexistent_and_ambiguous_local_times_are_rejected(self):
        for start,end in [("2026-03-29T02:30","2026-03-29T04:00"),("2026-10-25T02:30","2026-10-25T04:00")]:
            with self.assertRaises(ValueError):calendar.period({"start":start,"end":end})
        start,end,_=calendar.period({"start":"2026-10-25T02:30:00+02:00","end":"2026-10-25T04:00:00+01:00"})
        self.assertLess(start,end)
    def test_invalid_recurrence_rejected_before_connecting(self):
        with patch.object(calendar,"connection") as connect:
            with self.assertRaises(ValueError):calendar.mutate({"action":"create","calendar_id":"c","recurrence":"WEEKLY","count":1000,"start":"2026-10-24T10:00","end":"2026-10-24T11:00"})
        connect.assert_not_called()

class SettingsPersistenceTests(unittest.TestCase):
    def test_voice_or_identity_change_preserves_other_saved_settings(self):
        import tempfile,json
        from memory import config_manager as config
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"settings.json"
            with patch.object(config,"CONFIG_DIR",Path(folder)),patch.object(config,"CONFIG_FILE",path):
                config._patch_config(**{"gemini_api_key":"test-only","plugin_config":{"test":{"enabled":True}},"input_device":"Saved microphone"})
                config.save_voice("Kore")
                config.save_assistant_config("Ultron","User")
                data=config.load_api_keys()
                self.assertEqual(data["input_device"],"Saved microphone")
                self.assertEqual(data["gemini_api_key"],"test-only")
                self.assertEqual(data["plugin_config"],{"test":{"enabled":True}})
                self.assertEqual(data["voice_name"],"Kore")

class BrowserRegressionTests(unittest.TestCase):
    def test_already_active_window_keeps_focused_address_field(self):
        from computer_control.desktop.windows_controller import WindowsController
        with patch("win32gui.GetForegroundWindow",return_value=123),patch("pywinauto.Desktop") as desktop:
            self.assertTrue(WindowsController().focus_handle(123))
        desktop.assert_not_called()
    def test_address_is_observed_even_when_accessibility_name_is_localized(self):
        from computer_control.browser.existing_browser_controller import ExistingBrowserController
        c=ExistingBrowserController();root=MagicMock();edit=MagicMock()
        root.window_text.return_value="Fixture"
        edit.window_text.return_value="Localized omnibox"
        edit.element_info.control_type="Edit"
        edit.element_info.class_name="OmniboxViewViews"
        edit.get_value.return_value="https://example.com/"
        with patch.object(c,"window",return_value=root),patch.object(c,"controls",return_value=[edit]):
            self.assertEqual(c.snapshot()["url"],"https://example.com/")

class AudioEnumerationTests(unittest.TestCase):
    def test_resolving_tested_endpoint_does_not_open_a_competing_microphone(self):
        from core import audio_devices
        devices=[{"name":"Microphone","max_input_channels":1,"hostapi":0}]
        with patch.object(audio_devices,"_cache",{"input":["Microphone"]}),patch.object(audio_devices,"_chosen_devices",{"input":{"Microphone":(0,"Microphone")}}),patch("sounddevice.query_devices",return_value=devices),patch("sounddevice.query_hostapis",return_value=[{"name":"DirectSound"}]),patch.object(audio_devices,"_usable") as probe:
            self.assertEqual(audio_devices.resolve("Microphone","input"),0)
        probe.assert_not_called()
    def test_reconnected_usb_name_is_matched_only_when_unique(self):
        from core import audio_devices
        current="Microphone (2- Trust USB)"
        devices=[{"name":current,"max_input_channels":1,"hostapi":0}]
        with patch.object(audio_devices,"_cache",{"input":[current]}),patch.object(audio_devices,"_chosen_devices",{"input":{current:(0,current)}}),patch("sounddevice.query_devices",return_value=devices),patch("sounddevice.query_hostapis",return_value=[{"name":"DirectSound"}]):
            self.assertEqual(audio_devices.resolve("Microphone (Trust USB)","input"),0)
            audio_devices._chosen_devices["input"]["Microphone (3- Trust USB)"]=(1,"Microphone (3- Trust USB)")
            self.assertIsNone(audio_devices.resolve("Microphone (Trust USB)","input"))
    def test_concurrent_resolution_only_probes_devices_once(self):
        from core import audio_devices
        import concurrent.futures,time
        def query():
            time.sleep(.04)
            return {"input":["Microphone"],"output":["Speaker"]}
        with patch.object(audio_devices,"_cache",None),patch.object(audio_devices,"_query",side_effect=query) as probe:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                answers=list(pool.map(audio_devices.list_devices,["input"]*4))
        self.assertEqual(answers,[["Microphone"]]*4)
        probe.assert_called_once()

class DashboardContractTests(unittest.TestCase):
    def test_http_commands_require_auth_for_remote_clients(self):
        from dashboard.server import DashboardServer
        from fastapi.testclient import TestClient
        with patch("dashboard.server._local_ip",return_value="127.0.0.1"):
            server=DashboardServer()
        # In-memory ASGI only. Does not open a listener or modify firewall.
        client=TestClient(server.app)
        self.assertEqual(client.post("/api/command",json={"text":"fixture"}).status_code,401)
        server._tokens.add("test-only-token")
        response=client.post("/api/command",headers={"Authorization":"Bearer test-only-token"},json={"text":"fixture"})
        self.assertEqual(response.status_code,200)
        self.assertEqual(server._command_queue.get_nowait(),"fixture")
        client.close()

class RuntimeRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def live(self):
        from main import CarLive
        live=CarLive.__new__(CarLive)
        live.ui=MagicMock(muted=True);live._shutdown_requested=False
        live._resume_handle=None;live._enhanced_live=True;live._session_log=[]
        live._build_config=MagicMock(return_value={});live._wake_detector=None
        live._plugin_registry=MagicMock();live.set_speaking=MagicMock()
        live._tuned_live=False;live._conn_backoff=0;live._wake_enabled=False;live._clap_enabled=False
        live._briefing_sent=False;live._dashboard=None
        live._conn=MagicMock()   # connection manager (checks/reporting are not under test here)
        async def hold():await asyncio.Event().wait()
        for name in ["_watch_reconnect","_send_realtime","_listen_audio","_receive_audio","_play_audio",
                     "_run_system_monitor","_run_background_monitor","_run_sleep_watch","_run_connection_watch"]:
            setattr(live,name,hold)
        return live
    async def scenario(self, shutdown_in_backoff):
        import main
        live=self.live();connected=asyncio.Event();backoff=asyncio.Event();attempts=[]
        @asynccontextmanager
        async def connect(**kwargs):
            attempts.append(kwargs)
            if len(attempts)==1:raise OSError("simulated network outage")
            connected.set();yield MagicMock()
        client=MagicMock();client.aio.live.connect=connect
        original_sleep=asyncio.sleep
        async def sleep(seconds):
            backoff.set()
            if shutdown_in_backoff:await asyncio.Event().wait()
            else:await original_sleep(0)
        with patch.dict("os.environ",{"ULTRON_DISABLE_REMOTE":"1"}),patch.object(main.genai,"Client",return_value=client),\
             patch.object(main,"_get_api_key",return_value="test-only"),patch.object(main.audio_devices,"list_devices",return_value=[]),\
             patch.object(main._gemini,"note_live_failure",return_value=False),patch.object(main.traceback,"print_exc"),\
             patch.object(main.asyncio,"sleep",sleep):
            task=asyncio.create_task(live.run())
            await asyncio.wait_for((backoff if shutdown_in_backoff else connected).wait(),2)
            live.shutdown()
            await asyncio.wait_for(task,2)
        live._plugin_registry.shutdown.assert_called_once()
        return attempts
    async def test_network_outage_reconnects_then_shutdown_does_not_reconnect(self):
        attempts=await self.scenario(False);self.assertEqual(len(attempts),2)
    async def test_shutdown_during_backoff_still_cleans_up(self):
        attempts=await self.scenario(True);self.assertEqual(len(attempts),1)

if __name__=="__main__":unittest.main()
