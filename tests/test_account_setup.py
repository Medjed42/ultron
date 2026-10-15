"""Offline tests for the single-file account wizard. No browser consent is run."""
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from core.integrations import google_auth,store
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from test_phase2 import IsolatedState

ROOT=Path(__file__).resolve().parents[1]
BAT=ROOT/"ULTRON_SETUP_ALL_ACCOUNTS.bat"
source=BAT.read_text(encoding="utf-8-sig").split("\n# ULTRON_SETUP_PYTHON\n",1)[1]
wizard=types.ModuleType("ultron_account_wizard")
wizard.__file__=str(BAT)
exec(compile(source,str(BAT),"exec"),wizard.__dict__)

class CombinedConsentTests(IsolatedState):
    def test_single_consent_stores_both_encrypted_grants(self):
        info={"token":"fake-only","scopes":google_auth.SCOPES["gmail_write"]+google_auth.SCOPES["drive_write"]}
        creds=MagicMock();creds.to_json.return_value=json.dumps(info)
        with patch.object(google_auth,"_authorize",return_value=creds) as flow:
            google_auth.connect_suite(write=True,client_file="desktop.json")
        flow.assert_called_once_with(info["scopes"],"desktop.json")
        self.assertEqual(store.get_secret("google_gmail"),info)
        self.assertEqual(store.get_secret("google_drive"),info)
        self.assertNotIn(b"fake-only",store.path("google_gmail").read_bytes())
    def test_read_only_choice_does_not_request_mutation_scopes(self):
        creds=MagicMock();creds.to_json.return_value="{}"
        with patch.object(google_auth,"_authorize",return_value=creds) as flow:
            google_auth.connect_suite(write=False)
        scopes=flow.call_args.args[0]
        self.assertEqual(scopes,google_auth.SCOPES["gmail"]+google_auth.SCOPES["drive"])
    def test_partial_consent_cannot_replace_existing_token(self):
        client=Path(self.temp.name)/"desktop.json"
        client.write_text(json.dumps({"installed":{"client_id":"fake","client_secret":"fake"}}))
        creds=MagicMock(granted_scopes=google_auth.SCOPES["gmail"],scopes=google_auth.SCOPES["gmail_write"]+google_auth.SCOPES["drive_write"])
        flow=MagicMock();flow.run_local_server.return_value=creds
        store.set_secret("google_gmail",{"token":"kept-old-token"})
        with patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_config",return_value=flow):
            with self.assertRaises(PermissionError):google_auth.connect_suite(client_file=client)
        self.assertEqual(store.get_secret("google_gmail"),{"token":"kept-old-token"})
        self.assertIsNone(store.get_secret("google_drive"))
    def test_web_client_is_rejected_before_browser_opens(self):
        client=Path(self.temp.name)/"web.json";client.write_text('{"web":{}}')
        with patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_config") as browser:
            with self.assertRaises(ValueError):google_auth.connect_suite(client_file=client)
        browser.assert_not_called()
    def test_canceled_consent_preserves_existing_grant(self):
        store.set_secret("google_drive",{"token":"kept-old-token"})
        with patch.object(google_auth,"_authorize",side_effect=TimeoutError("Canceled")):
            with self.assertRaises(TimeoutError):google_auth.connect_suite()
        self.assertEqual(store.get_secret("google_drive"),{"token":"kept-old-token"})

class WizardChecks(IsolatedState):
    def test_maps_failure_does_not_hide_separate_places_success(self):
        with patch.object(wizard.maps,"route",return_value={"success":False,"error":"No route"}),patch.object(wizard.common,"request_json",return_value={"places":[]}),patch.object(store,"api_key",return_value="fake"):
            states=wizard.verify_maps()
        self.assertIn("Needs attention",states["Maps routing"])
        self.assertIn("Verified Places",states["Places discovery"])
    def test_amadeus_uses_production_and_does_not_claim_offer_verification(self):
        with patch.object(store,"api_key",return_value="fake"),patch.object(wizard.common,"request_json",return_value={"access_token":"fake-token"}) as request:
            status=wizard.verify_amadeus()
        self.assertEqual(request.call_args.args[1],"https://api.amadeus.com/v1/security/oauth2/token")
        self.assertIn("offers remain unverified",status)
        self.assertEqual(request.call_count,1)
    def test_cloud_link_uses_saved_client_project_without_any_secret(self):
        p=Path(self.temp.name)/"desktop.json"
        p.write_text(json.dumps({"installed":{"project_id":"example-project","client_secret":"do-not-leak"}}))
        url=wizard.cloud_url("apis/library",str(p))
        self.assertIn("project=example-project",url);self.assertNotIn("do-not-leak",url)
    def test_unchecked_services_are_not_marked_connected(self):
        with patch.object(wizard,"get_gemini_key",return_value="never-print-key"):
            report=wizard.summary_lines({}, {})
        self.assertIn("Not checked",report)
        self.assertNotIn("never-print-key",report)
    def test_google_read_check_never_writes_account_data(self):
        api=MagicMock()
        with patch.object(google_auth,"service",return_value=api) as service:
            status=wizard.check_google("drive",True)
        service.assert_called_once_with("drive",write=True)
        api.about().get.assert_called_once_with(fields="storageQuota")
        api.files.assert_not_called()
        self.assertIn("no changes made",status)

class WizardUITests(IsolatedState):
    @classmethod
    def setUpClass(cls):
        if QApplication.instance() is None:
            QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        cls.app=QApplication.instance() or QApplication([])
    def test_startup_opens_no_browser_and_has_six_steps(self):
        # Overview, Gemini, Google, Maps, Apple, Amadeus and finish: the Amadeus
        # step was added after this test was written, so there are seven.
        with patch.object(wizard.QDesktopServices,"openUrl") as browser,patch.object(wizard.common,"request_json") as network:
            ui=wizard.SetupWizard()
            self.assertEqual(ui.pages.count(),7)
            self.assertTrue(ui.maps_key.echoMode()==wizard.QLineEdit.EchoMode.Password)
            self.assertTrue(ui.apple_password.echoMode()==wizard.QLineEdit.EchoMode.Password)
            self.assertFalse(ui.map_seen.isEnabled())
            ui.close()
        browser.assert_not_called();network.assert_not_called()
    def test_save_summary_excludes_inputs(self):
        ui=wizard.SetupWizard()
        ui.apple_password.setText("secret-not-saved")
        ui.maps_key.setText("key-not-saved")
        self.assertNotIn("secret-not-saved",ui.summary.toPlainText())
        self.assertNotIn("key-not-saved",ui.summary.toPlainText())
        ui.close()
    def test_pending_connection_prevents_duplicate_job(self):
        ui=wizard.SetupWizard();ui.job=object()
        with patch.object(ui.pool,"start") as start:
            ui.task(lambda:{},lambda r:None)
        start.assert_not_called()
        ui.job=None;ui.close()
    def test_skipping_all_steps_is_honest(self):
        ui=wizard.SetupWizard()
        for _ in range(5):ui.step(1)
        self.assertEqual(ui.pages.currentIndex(),5)
        self.assertIn("Not checked",ui.summary.toPlainText())
        ui.close()

    def test_replacing_embed_key_requires_a_new_preview(self):
        ui=wizard.SetupWizard()
        ui.map_seen.setEnabled(True);ui.map_seen.setChecked(True)
        ui.mark_map(True)
        ui.embed_key.setText("new-test-key")
        with patch.object(ui,"task",side_effect=lambda work,done:done(work())),patch.object(wizard,"verify_maps",return_value={}):
            ui.save_maps()
        self.assertFalse(ui.map_seen.isChecked())
        self.assertFalse(ui.map_seen.isEnabled())
        self.assertIn("not yet verified",ui.states["Map display"])
        ui.close()

if __name__=="__main__":unittest.main()
