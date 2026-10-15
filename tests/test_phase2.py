"""Offline regression tests: providers and devices are mocked unless explicitly stated."""
import asyncio
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from core.integrations import store, drive, calendar, google_auth, common
from core import confirm


class IsolatedState(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = patch.object(store, "DATA", Path(self.temp.name))
        self.storage.start()
        self.show, self.hide, self.log = confirm._show_cb, confirm._hide_cb, confirm._log_cb
        confirm.bind(lambda title,detail:None, lambda:None)
        common.bind_panel(None)
    def tearDown(self):
        confirm.resolve(False)
        confirm.bind(self.show,self.hide,self.log)
        self.storage.stop()
        self.temp.cleanup()


# Route and accommodation behaviour moved to Workspace 2.0 (core/workspace);
# its tests — no invented distances, no fabricated prices, no bookings — are
# in tests/test_workspace.py.


class GoogleTests(IsolatedState):
    def test_secret_storage_is_encrypted_and_round_trips(self):
        # This exercises Windows DPAPI locally, without any real credential.
        store.set_secret("test_token",{"refresh_token":"only-a-test-secret"})
        self.assertNotIn(b"only-a-test-secret",store.path("test_token").read_bytes())
        self.assertEqual(store.get_secret("test_token")["refresh_token"],"only-a-test-secret")
    def test_expired_token_refresh_is_saved(self):
        creds=MagicMock(valid=False,refresh_token="fake",scopes=google_auth.SCOPES["gmail"])
        creds.to_json.return_value='{"token":"refreshed-fake"}'
        with patch.object(google_auth,"_info",return_value={"token":"fake"}),patch.object(google_auth.Credentials,"from_authorized_user_info",return_value=creds),patch.object(store,"set_secret") as save:
            google_auth.credentials("gmail")
        creds.refresh.assert_called_once()
        save.assert_called_once_with("google_gmail",{"token":"refreshed-fake"})
    def test_read_only_grant_cannot_trash_drive(self):
        creds=MagicMock(valid=True,scopes=google_auth.SCOPES["drive"])
        with patch.object(google_auth,"_info",return_value={"token":"fake"}),patch.object(google_auth.Credentials,"from_authorized_user_info",return_value=creds):
            with self.assertRaises(PermissionError):google_auth.credentials("drive",write=True)
    def test_disconnect_prevents_reimporting_legacy_token(self):
        store.save_preferences(gmail_disconnected=True)
        with patch.object(google_auth,"legacy_token") as legacy:
            self.assertIsNone(google_auth._info("gmail"))
        legacy.assert_not_called()
    def test_gmail_attachment_metadata_is_recursive(self):
        from core.mail_service import attachment_metadata
        rows=attachment_metadata({"parts":[{"parts":[{"filename":"a.pdf","mimeType":"application/pdf","body":{"size":20,"attachmentId":"id"}}]}]})
        self.assertEqual(rows,[{"filename":"a.pdf","mime_type":"application/pdf","size":20,"attachment_id":"id"}])
    def test_bulk_email_cancel_changes_nothing(self):
        from core.integrations.gmail import bulk
        api=MagicMock()
        api.users().messages().get.return_value.execute.return_value={"id":"m1","payload":{}}
        with patch("core.mail_service.service",return_value=api):
            result=bulk("bulk_trash",["m1"])
            self.assertIn("CONFIRMATION_PENDING",result)
            confirm.resolve(False)
        api.users().messages().trash.assert_not_called()
    def test_drive_pagination_quota_and_duplicate_checksums(self):
        api=MagicMock()
        api.about().get.return_value.execute.return_value={"storageQuota":{"limit":"1000","usage":"800","usageInDrive":"300","usageInDriveTrash":"20"}}
        api.files().list.return_value.execute.side_effect=[
            {"files":[{"id":"a","name":"same","mimeType":"text/plain","size":"10","quotaBytesUsed":"10","md5Checksum":"one"}],"nextPageToken":"next"},
            {"files":[{"id":"b","name":"different","mimeType":"text/plain","size":"10","quotaBytesUsed":"10","md5Checksum":"one"},
                      {"id":"c","name":"same","mimeType":"text/plain","size":"20","quotaBytesUsed":"20","md5Checksum":"two"}]}]
        with patch.object(google_auth,"service",return_value=api):result=drive.analyze({})
        self.assertEqual(result["duplicate_candidates"],[["a","b"]])
        self.assertEqual(result["remaining_bytes"],200)
        self.assertEqual(result["quota"]["usageInDrive"],300)
        self.assertFalse(result["partial"])
    def test_drive_cleanup_is_scoped_cancelable_and_never_delete(self):
        api=MagicMock()
        api.files().get.return_value.execute.return_value={"id":"a","name":"Test","size":"10","mimeType":"text/plain","capabilities":{"canTrash":True}}
        with patch.object(google_auth,"service",return_value=api):
            result=drive.trash_preview(["a"])
            self.assertIn("CONFIRMATION_PENDING",result)
            confirm.resolve(False)
        api.files().update.assert_not_called();api.files().delete.assert_not_called()
    def test_confirmed_drive_cleanup_only_trashes_selected_ids(self):
        api=MagicMock()
        api.files().get.return_value.execute.return_value={"id":"a","name":"Test","size":"10","mimeType":"text/plain","capabilities":{"canTrash":True}}
        api.files().update.return_value.execute.return_value={"id":"a","trashed":True}
        with patch.object(google_auth,"service",return_value=api),patch.object(drive,"analyze",return_value={"files":[]}):
            drive.trash_preview(["a"])
            callback=confirm._pending.run
            callback()  # Simulates a human approval, no external API.
        api.files().update.assert_called_once_with(fileId="a",body={"trashed":True},fields="id,trashed")
        api.files().delete.assert_not_called()


class CalendarTests(IsolatedState):
    ICS="""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Ultron Tests//EN
BEGIN:VEVENT
UID:recurring
DTSTART;TZID=Europe/Belgrade:20261024T100000
DTEND;TZID=Europe/Belgrade:20261024T110000
RRULE:FREQ=DAILY;COUNT=3
EXDATE;TZID=Europe/Belgrade:20261025T100000
SUMMARY:Test recurring appointment
END:VEVENT
END:VCALENDAR
"""
    def test_recurrence_exception_and_dst(self):
        start,end,zone=calendar.period({"start":"2026-10-24","end":"2026-10-28"})
        rows=calendar.event_rows(self.ICS,start,end,zone)
        self.assertEqual(len(rows),2)
        self.assertTrue(rows[0]["start"].endswith("+02:00"))
        self.assertTrue(rows[1]["start"].endswith("+01:00"))
    def test_ics_subscription_rejects_other_hosts_and_local_files(self):
        for url in ["file:///C:/secret","http://127.0.0.1/a","https://evil.example/a","https://icloud.com.evil.example/a"]:
            with self.assertRaises(ValueError):calendar.subscription_data(url)
    def test_subscription_never_allows_writes(self):
        with patch.object(store,"get_secret",return_value="https://p01.icloud.com/calendar"):
            with self.assertRaises(PermissionError):calendar.mutate({"action":"create"})
    def test_calendar_permissions_are_detected_from_dav_xml(self):
        cal=MagicMock()
        cal.get_properties.return_value={"{DAV:}current-user-privilege-set":'<d:current-user-privilege-set xmlns:d="DAV:"><d:privilege><d:write/></d:privilege></d:current-user-privilege-set>'}
        self.assertTrue(calendar.can_write(cal))
        cal.get_properties.return_value={"{DAV:}current-user-privilege-set":'<d:current-user-privilege-set xmlns:d="DAV:"><d:privilege><d:read/></d:privilege></d:current-user-privilege-set>'}
        self.assertFalse(calendar.can_write(cal))
    def test_read_only_calendar_cannot_create_event(self):
        cal=MagicMock();cal.url="https://caldav.icloud.com/c"
        client=MagicMock();client.principal().calendars.return_value=[cal]
        with patch.object(calendar,"can_write",return_value=False):
            with self.assertRaises(PermissionError):calendar._choose(client,calendar.calendar_id(cal))


class LifecycleTests(IsolatedState):
    def test_failed_plugin_does_not_stop_healthy_plugin(self):
        from core.plugin_loader import PluginRecord,PluginRegistry
        bad=PluginRecord(name="bad",valid=True,run=MagicMock(side_effect=RuntimeError("broken")))
        good=PluginRecord(name="good",valid=True,run=lambda p:"ok",initialize=MagicMock(),shutdown=MagicMock())
        registry=PluginRegistry({"bad":bad,"good":good},lambda _:None)
        with patch("core.plugin_loader.traceback.print_exc"):
            self.assertIn("failed",registry.run("bad",{}))
        self.assertEqual(registry.run("good",{}),"ok")
        self.assertEqual(registry.run("good",{}),"ok")
        good.initialize.assert_called_once()
        registry.shutdown();good.shutdown.assert_called_once()
    def test_concurrent_preferences_preserve_all_fields(self):
        threads=[threading.Thread(target=store.save_preferences,kwargs={f"field{i}":i}) for i in range(10)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(len(store.preferences()),10)
    def test_disabled_chrome_blocks_managed_fallback_too(self):
        from actions.browser_control import browser_control,_registry
        store.set_permission("chrome",False)
        with patch.object(_registry,"get") as launch:
            result=browser_control({"action":"get_text","mode":"managed","browser":"chrome"})
        self.assertFalse(result["success"]);launch.assert_not_called()
    def test_large_drive_result_is_bounded_for_gemini_but_complete_for_ui(self):
        sink=MagicMock();common.bind_panel(sink)
        result=json.loads(common.dispatch("drive",{},lambda _:{"files":[{"id":str(i)} for i in range(300)]}))
        self.assertEqual(len(result["files"]),20)
        self.assertEqual(len(sink.call_args.args[1]["files"]),300)
        common.bind_panel(None)


class SilenceTests(unittest.IsolatedAsyncioTestCase):
    def create_live(self,muted):
        from main import CarLive
        live=CarLive.__new__(CarLive)
        live.ui=MagicMock(muted=muted)
        live.session=MagicMock(send_client_content=AsyncMock())
        live._awake=True;live._interrupted=False;live._out_latency=.05;live._play_cursor=0
        live._turn_done_event=asyncio.Event();live.audio_in_queue=asyncio.Queue()
        live.set_speaking=MagicMock();live._visemes=MagicMock();live._echo=MagicMock()
        return live
    async def test_month_of_inactivity_never_triggers_speech(self):
        from actions.proactive import ProactiveEngine
        engine=ProactiveEngine()
        with patch("time.monotonic",return_value=31*86400):
            self.assertFalse(engine.should_trigger(0))
            self.assertEqual(engine.build_prompt({}),"")
    async def test_muted_startup_brief_never_sends_prompt(self):
        live=self.create_live(True)
        await live._send_startup_briefing()
        live.session.send_client_content.assert_not_called()
    async def test_muted_output_drops_audio_unmuted_direct_output_plays(self):
        import main
        for muted in (True,False):
            live=self.create_live(muted)
            await live.audio_in_queue.put(b"\0"*2400)
            stream=MagicMock(latency=.05)
            with patch.object(main,"get_output_device",return_value=""),patch.object(main.audio_devices,"resolve",return_value=None),patch.object(main.sd,"RawOutputStream",return_value=stream),patch.object(main,"_pcm_visemes",return_value=[]):
                task=asyncio.create_task(live._play_audio())
                await asyncio.sleep(.07);task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
            self.assertEqual(stream.write.call_count,0 if muted else 1)
            stream.close.assert_called_once()
    async def test_muted_plugin_speech_does_not_schedule(self):
        live=self.create_live(True);live._loop=MagicMock()
        with patch("asyncio.run_coroutine_threadsafe") as schedule:
            live.plugin_say("Unsolicited greeting")
        schedule.assert_not_called()
    async def test_mute_interrupt_flushes_device_buffer_and_queue(self):
        live=self.create_live(True);live._speaker_stream=MagicMock()
        await live.audio_in_queue.put(b"\0"*2400)
        live.interrupt()
        self.assertTrue(live.audio_in_queue.empty())
        live._speaker_stream.abort.assert_called_once()
    async def test_idle_task_is_not_scheduled_in_runtime(self):
        import inspect
        from main import CarLive
        self.assertNotIn("_run_proactive_mode",inspect.getsource(CarLive.run))


if __name__=="__main__":
    unittest.main()
