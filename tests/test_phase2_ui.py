"""Offscreen native dialogs that remain after Workspace 2.0. No browser pages, accounts or hardware used.

The old tabbed IntegrationHub (Directions, Places to stay, Gmail, Drive,
Calendar, Connections, Claude Brain) was replaced by the web workspace in
core/workspace; its UI is covered by tests/test_workspace.py. What stays
native — and is tested here — is everything that must not be clickable from
a web page: change reviews and credential entry.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
os.environ["ULTRON_SKIP_DEVICE_AUTOSTART"]="1"
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication,QPushButton
from core.integrations import store
from core.integrations import panels
from core.integrations.panels import ReviewDialog,CredentialsDialog,ConnectionChoiceDialog
from core import confirm


class NativeDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if QApplication.instance() is None:
            QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch.object(store,"DATA",Path(self.temp.name));self.patch.start()
        self.show,self.hide,self.log=confirm._show_cb,confirm._hide_cb,confirm._log_cb
    def tearDown(self):
        confirm.resolve(False);confirm.bind(self.show,self.hide,self.log)
        if ReviewDialog._current is not None:ReviewDialog._current.close();ReviewDialog._current=None
        self.patch.stop();self.temp.cleanup()

    def test_legacy_service_tabs_are_gone(self):
        for name in ("IntegrationHub","MapView","PANELS","PLUGIN_NAMES"):
            self.assertFalse(hasattr(panels,name),name)

    def _review(self):
        confirm.bind(lambda title,detail,token=None:ReviewDialog.open(None,title,detail,token),lambda:None)
    def test_review_cancel_has_no_mutation(self):
        operation=MagicMock()
        self._review()
        confirm.request("test","Trash selected files?","File one · 10 bytes",operation)
        buttons=ReviewDialog._current.findChildren(QPushButton)
        next(b for b in buttons if b.text()=="Cancel").click()
        operation.assert_not_called()
        self.assertEqual(confirm.pending_title(),"")
    def test_review_confirm_runs_exactly_once(self):
        operation=MagicMock(return_value="done")
        self._review()
        confirm.request("test","Move selected Drive files to trash?","File one · 10 bytes",operation)
        dialog=ReviewDialog._current
        self.assertIn("File one",dialog.findChildren(panels.QPlainTextEdit)[0].toPlainText())
        dialog.confirm_btn.click()
        operation.assert_called_once()
        dialog.confirm_btn.click()
        operation.assert_called_once()
    def test_stale_review_cannot_approve_a_newer_request(self):
        first,second=MagicMock(),MagicMock()
        self._review()
        confirm.request("a","First?","one",first)
        old=ReviewDialog._current
        confirm.request("b","Second?","two",second)
        old.finish(True)
        first.assert_not_called();second.assert_not_called()

    def test_credentials_dialog_never_shows_secret_values(self):
        store.set_secret("google_gmail",{"token":"super-private-token"})
        store.set_secret("google_maps_api_key","AIzaPRIVATE-maps-key-123456")
        dlg=CredentialsDialog()
        text=dlg.connection_state.text()
        self.assertNotIn("super-private-token",text)
        self.assertNotIn("AIzaPRIVATE",text)
        self.assertIn("unverified",text)
        self.assertIn("Google Maps key: saved",text)
        for widget in dlg.secret_fields.values():
            self.assertEqual(widget.text(),"")
        self.assertEqual(dlg.secret_fields["google_maps_api_key"].echoMode(),panels.QLineEdit.EchoMode.Password)
        dlg.close()
    def test_saving_credentials_clears_inputs_and_encrypts(self):
        dlg=CredentialsDialog()
        dlg.secret_fields["google_maps_api_key"].setText("test-maps-key-0001")
        dlg.save_connections()
        self.assertEqual(dlg.secret_fields["google_maps_api_key"].text(),"")
        self.assertEqual(store.get_secret("google_maps_api_key"),"test-maps-key-0001")
        self.assertNotIn(b"test-maps-key-0001",store.path("google_maps_api_key").read_bytes())
        dlg.close()

    def test_connection_choice_defaults_to_own_connection(self):
        dlg=ConnectionChoiceDialog("gmail")
        self.assertEqual(dlg.choice,"user")
        dlg._choose_claude()
        self.assertEqual(dlg.choice,"claude")


if __name__=="__main__":unittest.main()
