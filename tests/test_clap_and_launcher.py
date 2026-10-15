"""
tests/test_clap_and_launcher.py — clap recognition, name calling and the tray
launcher's activation modes.

Audio comes from tests/audio_fixtures.py (physically modelled claps and hard
negatives) and, on Windows, real synthesised speech (SAPI) for name calling.
The microphone itself is replaced by a fake stream that is driven through the
listener's real callback, so the whole path

    audio block → ActivationListener._callback → detector → activation → launch

runs exactly as in the app, minus the hardware. Settings are written to a
temporary config file, never to config/api_keys.json.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import audio_fixtures as af                                   # noqa: E402
from core.clap_detector import ClapDetector, SENSITIVITY      # noqa: E402


def run_detector(x, sensitivity="medium", claps_required=2, block=1024, tail=1.2, as_float=False):
    ev = SimpleNamespace(claps=[], double=0, single=0, pattern=0, threads=set())

    def mark(kind):
        def cb(*_a):
            ev.threads.add(threading.current_thread().name)
            setattr(ev, kind, getattr(ev, kind) + 1)
        return cb

    d = ClapDetector(on_single_clap=mark("single"), on_double_clap=mark("double"),
                     on_pattern=mark("pattern"), on_clap=lambda i: ev.claps.append(i),
                     sensitivity=sensitivity, claps_required=claps_required)
    for b in af.blocks(np.concatenate([x, af.silence(tail)]), block):
        d.feed(b.astype(np.float32) / 32768.0 if as_float else b)
    d.wait_idle()
    d.close()
    return ev


# ── Clap recognition ──────────────────────────────────────────────────────────

class ClapRecognitionTests(unittest.TestCase):

    def test_double_clap_detected_across_levels(self):
        for level in (-6, -12, -18, -24):
            for seed in range(3):
                ev = run_detector(af.clap_sequence([0.6, 0.95], 2.0, peak_dbfs=level, seed=seed))
                self.assertEqual(len(ev.claps), 2, f"{level} dBFS seed {seed}")
                self.assertEqual((ev.double, ev.pattern, ev.single), (1, 1, 0), f"{level} dBFS")

    def test_quiet_claps_need_high_sensitivity(self):
        x = af.clap_sequence([0.6, 0.95], 2.0, peak_dbfs=-31, seed=2)
        self.assertEqual(run_detector(x, "medium").pattern, 0)
        self.assertEqual(run_detector(x, "high").pattern, 1)

    def test_claps_under_background_noise_and_quiet_talk(self):
        x = af.clap_sequence([0.6, 0.95], 2.0, peak_dbfs=-16, seed=21) + af.fan_noise(2.0, -40, 1)
        self.assertEqual(run_detector(x).pattern, 1)
        x = af.clap_sequence([0.6, 0.95], 2.0, peak_dbfs=-12, seed=31) + af.vowel_speech(2.0, -26, 1)
        self.assertEqual(run_detector(x).pattern, 1)

    def test_triple_pattern_is_exact(self):
        triple = af.clap_sequence([0.5, 0.85, 1.2], 2.2, peak_dbfs=-16, seed=10)
        self.assertEqual(run_detector(triple, claps_required=3).pattern, 1)
        self.assertEqual(run_detector(triple, claps_required=2).pattern, 0)   # 3 claps ≠ double
        double = af.clap_sequence([0.6, 0.95], 2.0, peak_dbfs=-16, seed=11)
        self.assertEqual(run_detector(double, claps_required=3).pattern, 0)

    def test_single_clap_and_slow_claps(self):
        ev = run_detector(af.clap_sequence([0.6], 1.2, peak_dbfs=-14, seed=4))
        self.assertEqual((ev.single, ev.double, ev.pattern), (1, 0, 0))
        ev = run_detector(af.clap_sequence([0.5, 1.9], 2.6, peak_dbfs=-14, seed=5))   # 1.4 s apart
        self.assertEqual((ev.single, ev.double, ev.pattern), (2, 0, 0))

    def test_block_size_and_sample_format_do_not_matter(self):
        x = af.clap_sequence([0.6, 0.95], 2.0, peak_dbfs=-18, seed=6)
        for block in (256, 1024, 4096):
            self.assertEqual(run_detector(x, block=block).pattern, 1, block)
        self.assertEqual(run_detector(x, as_float=True).pattern, 1)

    def test_callbacks_never_run_on_the_audio_thread(self):
        ev = run_detector(af.clap_sequence([0.6, 0.95], 2.0, peak_dbfs=-18, seed=7))
        self.assertEqual(ev.threads, {"ClapDispatch"})

    def test_no_false_triggers_from_everyday_sounds(self):
        negatives = []
        for seed in range(3):
            negatives += [("speech", af.vowel_speech(4.0, -14, seed)), ("loud speech", af.vowel_speech(4.0, -6, 50 + seed)),
                          ("music", af.music(4.0, -10, seed)), ("knocks", af.knocks(3.0, -10, seed)),
                          ("cough", af.cough(2.0, -8, seed)), ("fan", af.fan_noise(3.0, -30, seed))]
        # Use synthesized audio so the repository contains no personal recording.
        negatives.append(("quiet speech fixture", af.vowel_speech(2.2, -24, 123)))
        for name, x in negatives:
            for sens in ("medium", "high"):
                ev = run_detector(x, sens)
                self.assertEqual((ev.pattern, ev.double, ev.single), (0, 0, 0), f"{name} @ {sens}")

    def test_typing_never_completes_a_launch_pattern(self):
        for seed in range(4):
            for level, sens in ((-22, "medium"), (-18, "medium"), (-22, "high")):
                ev = run_detector(af.typing(5.0, level, 200 + seed), sens)
                self.assertEqual(ev.pattern, 0, f"typing {level} {sens} seed {seed}")
                self.assertEqual(ev.single, 0)

    def test_real_speech_does_not_clap(self):
        for text in ("Hello Ultron, what is the weather like today?", "Peter picked a peck of pickled peppers.",
                     "Tick tock, the clock struck twelve. Pop! Bang! Clap!"):
            s = af.windows_tts(text)
            if s is None:
                self.skipTest("Windows speech synthesis not available")
            ev = run_detector(s / np.max(np.abs(s)) * 32767 * 0.6, "high")
            self.assertEqual((ev.pattern, ev.double), (0, 0), text)

    def test_compatibility_with_previous_api(self):
        d = ClapDetector(on_single_clap=lambda: None, on_double_clap=lambda: None,
                         threshold=4000, min_crest=3.8, min_zcr=50, enabled=False)
        self.assertEqual(d.threshold, 4000)
        self.assertFalse(d.feed(np.zeros((1024, 1), dtype=np.int16)))
        d.reset()
        self.assertEqual(ClapDetector(sensitivity="bogus").sensitivity, "medium")
        self.assertEqual(ClapDetector(sensitivity="high").min_peak, SENSITIVITY["high"]["min_peak"])


class InAppWakeOnClapTests(unittest.TestCase):
    """main.py: double clap wakes ULTRON, single clap puts it to sleep."""

    def test_double_clap_wakes_and_single_clap_sleeps(self):
        from main import CarLive
        live = CarLive.__new__(CarLive)
        live.ui = MagicMock(muted=False)
        live._awake, live._is_speaking = False, False
        live._wake_enabled, live._clap_enabled = False, True
        live._last_user_speech = 0.0
        live.set_speaking = MagicMock()
        det = ClapDetector(on_single_clap=live._on_single_clap_detected,
                           on_double_clap=live._on_double_clap_detected, sensitivity="medium")
        for b in af.blocks(np.concatenate([af.clap_sequence([0.5, 0.85], 1.6, -16, seed=8), af.silence(1)])):
            det.feed(b)
        det.wait_idle()
        self.assertTrue(live._awake)
        for b in af.blocks(np.concatenate([af.clap_sequence([0.4], 1.0, -16, seed=9), af.silence(1.2)])):
            det.feed(b)
        det.wait_idle()
        self.assertFalse(live._awake)
        det.close()


# ── Name calling ──────────────────────────────────────────────────────────────

class NameCallingTests(unittest.TestCase):

    def test_phrases_follow_the_configured_name(self):
        from core.wake_word import WAKE_PHRASES, matches_wake, phrases_for
        self.assertEqual(phrases_for("Ultron"), WAKE_PHRASES)
        self.assertEqual(phrases_for("Jarvis"), ("jarvis", "hey jarvis"))
        self.assertTrue(matches_wake("hey jarvis", phrases_for("Jarvis")))
        self.assertFalse(matches_wake("jarvisian", phrases_for("Jarvis")))

    def test_spoken_name_is_recognised_offline_and_others_are_not(self):
        from core.wake_word import is_ready
        if not is_ready() or af.windows_tts("Ultron") is None:
            self.skipTest("Vosk model or Windows speech synthesis not available")
        from core.wake_word import WakeWordDetector
        positives = [("Ultron", 0), ("Ultron", -2), ("Ultron", 2), ("Hey Ultron", 0), ("Ultron, wake up", 0)]
        negatives = [("Please start the car", 0), ("I am sorry Terry", 0), ("The ultra fast train", 0),
                     ("All right, turn it on", 0), ("Let's watch Tron tonight", 0)]
        for text, rate in positives + negatives:
            hits = []
            det = WakeWordDetector(on_detect=lambda: hits.append(1), logger=lambda m: None, name="Ultron")
            self.assertTrue(det.start())
            speech = af.windows_tts(text, rate)
            for b in af.blocks(np.concatenate([af.silence(0.3), speech * 0.6, af.silence(1.2)]), 1024):
                det.feed(b)
                time.sleep(0.004)
            deadline = time.time() + 5
            while not hits and time.time() < deadline and (text, rate) in positives:
                time.sleep(0.05)
            time.sleep(0.3)
            det.stop()
            self.assertEqual(bool(hits), (text, rate) in positives, text)


# ── Launcher listener (fake microphone, real detectors) ───────────────────────

class FakeStream:
    instances: list = []

    def __init__(self, callback, device_name=""):
        self.callback, self.device_name = callback, device_name
        self.started = self.stopped = self.closed = False
        FakeStream.instances.append(self)

    @property
    def active(self):
        return self.started and not self.stopped

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True

    def push(self, x, block=1024):
        for b in af.blocks(x, block):
            self.callback(b, len(b), None, None)


class FakeWake:
    def __init__(self, on_detect, name, logger, ok=True):
        self.on_detect, self.name, self.ok = on_detect, name, ok
        self.started = self.stopped = False
        self.fed = 0

    def start(self):
        self.started = True
        return self.ok

    def stop(self):
        self.stopped = True

    def feed(self, block):
        self.fed += 1
        if np.max(np.abs(block)) == 12345:            # marker block = "the name was said"
            self.on_detect()


def make_listener(mode="clap", stream_factory=FakeStream, wake_ok=True, **kw):
    from car_launcher import ActivationListener
    FakeStream.instances = []
    acts, statuses, wakes = [], [], []

    def wake_factory(on_detect, name, logger):
        w = FakeWake(on_detect, name, logger, wake_ok)
        wakes.append(w)
        return w

    lst = ActivationListener(on_activate=acts.append, on_status=lambda s, t: statuses.append((s, t)),
                             mode=mode, stream_factory=stream_factory, wake_factory=wake_factory, **kw)
    return lst, acts, statuses, wakes


def _drain(lst):
    lst.clap.wait_idle()


class ActivationListenerTests(unittest.TestCase):

    def test_clap_mode_activates_once_per_pattern(self):
        lst, acts, statuses, _ = make_listener("clap")
        self.assertTrue(lst.start())
        stream = FakeStream.instances[0]
        stream.push(np.concatenate([af.clap_sequence([0.5, 0.85], 1.5, -16, seed=12), af.silence(1.2)]))
        _drain(lst)
        self.assertEqual(acts, ["clap"])
        stream.push(np.concatenate([af.clap_sequence([0.3, 0.65], 1.2, -16, seed=13), af.silence(1.2)]))
        _drain(lst)
        self.assertEqual(acts, ["clap"])            # debounced: one activation per call
        self.assertEqual(statuses[-1][0], "listening")

    def test_name_mode_ignores_claps_and_uses_the_name(self):
        lst, acts, _, wakes = make_listener("name")
        self.assertTrue(lst.start())
        stream = FakeStream.instances[0]
        stream.push(np.concatenate([af.clap_sequence([0.5, 0.85], 1.5, -16, seed=12), af.silence(1.2)]))
        _drain(lst)
        self.assertEqual(acts, [])
        self.assertGreater(wakes[0].fed, 0)
        marker = np.zeros(1024)
        marker[10] = 12345
        stream.push(marker)
        self.assertEqual(acts, ["name"])

    def test_switching_modes_swaps_detector_on_one_stream(self):
        lst, acts, statuses, wakes = make_listener("clap")
        lst.start()
        self.assertTrue(lst.clap.enabled)
        self.assertTrue(lst.set_mode("name"))
        self.assertFalse(lst.clap.enabled)
        self.assertEqual(len(wakes), 1)
        self.assertTrue(wakes[0].started)
        self.assertTrue(lst.set_mode("clap"))
        self.assertTrue(wakes[0].stopped)           # previous listener stopped
        self.assertTrue(lst.clap.enabled)
        self.assertEqual(len(FakeStream.instances), 1)   # never a second microphone stream
        lst.set_mode("clap")                        # no-op, no duplicate detector
        self.assertEqual(len(wakes), 1)
        self.assertIn("Listening for a double clap", statuses[-1][1])

    def test_pause_releases_microphone_and_resume_keeps_mode(self):
        lst, *_ = make_listener("name")
        lst.start()
        s1 = FakeStream.instances[0]
        lst.pause("Paused — ULTRON is running")
        self.assertTrue(s1.stopped and s1.closed)
        self.assertEqual(lst.state, "paused")
        lst.start()
        self.assertEqual(len(FakeStream.instances), 2)
        self.assertEqual(lst.mode, "name")

    def test_microphone_error_is_reported_not_raised(self):
        def broken(cb, name=""):
            raise RuntimeError("Error opening InputStream: Invalid device [PaErrorCode -9996]")
        lst, acts, statuses, _ = make_listener("clap", stream_factory=broken)
        self.assertFalse(lst.start())
        self.assertEqual(lst.state, "error")
        self.assertIn("no usable input device", statuses[-1][1])

    def test_silent_microphone_warns_about_privacy_or_mute(self):
        lst, acts, statuses, _ = make_listener("clap")
        lst.start()
        FakeStream.instances[0].push(np.zeros(int(4.5 * af.SR)))
        self.assertEqual(statuses[-1][0], "warning")
        self.assertIn("Privacy", statuses[-1][1])
        FakeStream.instances[0].push(af.silence(0.2))
        self.assertEqual(statuses[-1][0], "listening")

    def test_name_mode_unavailable_is_an_error_state(self):
        lst, acts, statuses, _ = make_listener("name", wake_ok=False)
        self.assertFalse(lst.start())
        self.assertEqual(lst.state, "error")
        self.assertIn("Name recognition is unavailable", statuses[-1][1])

    def test_real_name_recognition_through_the_listener(self):
        from core.wake_word import is_ready
        speech = af.windows_tts("Ultron")
        if not is_ready() or speech is None:
            self.skipTest("Vosk model or Windows speech synthesis not available")
        from car_launcher import ActivationListener
        FakeStream.instances = []
        acts = []
        lst = ActivationListener(on_activate=acts.append, on_status=lambda s, t: None, mode="name",
                                 stream_factory=FakeStream)
        self.assertTrue(lst.start())
        for b in af.blocks(np.concatenate([af.silence(0.3), speech * 0.6, af.silence(1.2)])):
            FakeStream.instances[0].callback(b, len(b), None, None)
            time.sleep(0.004)
        deadline = time.time() + 6
        while not acts and time.time() < deadline:
            time.sleep(0.05)
        lst.stop()
        self.assertEqual(acts, ["name"])


# ── Tray app: settings, persistence, activation ───────────────────────────────

class FakeListener:
    def __init__(self, on_activate, on_status, mode, clap_count, sensitivity, name, device_name, logger):
        self.on_activate, self.on_status = on_activate, on_status
        self.mode, self.name = mode, name
        self.state, self.started, self.paused = "stopped", 0, []
        self.clap = SimpleNamespace(claps_detected=0, claps_required=clap_count, sensitivity=sensitivity,
                                    min_peak=1600, close=lambda: None)
        self.is_healthy = True
        self.start_ok = True

    def start(self):
        self.started += 1
        self.state = "listening" if self.start_ok else "error"
        return self.start_ok

    def stop(self, state="stopped", reason=""):
        self.state = state

    def pause(self, reason):
        if self.state != "paused":
            self.paused.append(reason)
        self.state = "paused"

    def healthy(self):
        return self.is_healthy

    def set_mode(self, mode):
        self.mode = mode
        return True

    def set_clap_count(self, n):
        self.clap.claps_required = n

    def set_sensitivity(self, s):
        self.clap.sensitivity = s

    def take_levels(self):
        return {"peak_dbfs": -20.0, "floor_dbfs": -60.0, "blocks": 10, "claps": 0, "min_clap_dbfs": -26.2}


class LauncherAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        cls.qapp = QApplication.instance() or QApplication([])

    def setUp(self):
        from memory import config_manager as cm
        self.tmp = tempfile.TemporaryDirectory()
        cfg = Path(self.tmp.name)
        (cfg / "api_keys.json").write_text('{"assistant_name": "Ultron", "gemini_api_key": "keep-me"}',
                                           encoding="utf-8")
        self.patches = [patch.object(cm, "CONFIG_DIR", cfg), patch.object(cm, "CONFIG_FILE", cfg / "api_keys.json")]
        for p_ in self.patches:
            p_.start()
        self.running = False
        self.launches = []

    def tearDown(self):
        for p_ in self.patches:
            p_.stop()
        self.tmp.cleanup()

    def make_app(self, **kw):
        from car_launcher import CarLauncherApp
        app = CarLauncherApp(listener_factory=FakeListener, ultron_running=lambda: self.running,
                             launcher=lambda: self.launches.append(time.monotonic()),
                             single_instance=kw.pop("single_instance", False), **kw)
        self.addCleanup(app.watchdog.stop)
        return app

    def test_mode_switch_applies_immediately_and_survives_restart(self):
        from memory import config_manager as cm
        app = self.make_app()
        self.assertEqual(app.listener.mode, "clap")                 # default
        self.assertEqual(app.listener.started, 1)                   # listening at start-up
        app.set_mode("name")
        self.assertEqual(app.listener.mode, "name")                 # immediately
        self.assertEqual(cm.get_launcher_activation(), "name")      # saved immediately
        self.assertTrue(app.mode_actions["name"].isChecked())
        self.assertFalse(app.clap_menu.isEnabled())                 # clap options greyed out
        app.set_clap_count(3)
        app.set_sensitivity("high")
        app.quit_app()
        restarted = self.make_app()                                 # "restart"
        self.assertEqual(restarted.listener.mode, "name")
        self.assertTrue(restarted.mode_actions["name"].isChecked())
        self.assertEqual(restarted.listener.clap.claps_required, 3)
        self.assertEqual(restarted.listener.clap.sensitivity, "high")
        self.assertEqual(cm.load_api_keys()["gemini_api_key"], "keep-me")   # nothing else lost

    def test_activation_launches_once_and_respects_running_ultron(self):
        app = self.make_app()
        app.bridge.activate.emit("clap")
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(app.listener.state, "paused")              # mic released for ULTRON
        app.bridge.activate.emit("clap")
        self.assertEqual(len(self.launches), 1)                     # cooldown: no duplicate launch
        self.running = True
        app._launched_at = 0.0
        app.bridge.activate.emit("name")
        self.assertEqual(len(self.launches), 1)                     # already running: no launch

    def test_listener_pauses_while_ultron_runs_and_resumes_after(self):
        app = self.make_app()
        self.running = True
        app.check_state()
        self.assertEqual(app.listener.state, "paused")
        self.assertIn("ULTRON is running", app.listener.paused[-1])
        self.running = False
        app.check_state()
        self.assertEqual(app.listener.state, "listening")

    def test_dead_stream_and_errors_are_retried(self):
        app = self.make_app()
        app.listener.is_healthy = False
        starts = app.listener.started
        app.check_state()
        self.assertEqual(app.listener.started, starts + 1)          # reopened
        app.listener.start_ok = False
        app.listener.state = "error"
        app._retry_at = 0
        app.check_state()
        self.assertGreater(app._retry_at, time.monotonic())         # next retry scheduled

    def test_status_is_shown_in_menu_and_tooltip(self):
        app = self.make_app()
        app.bridge.status.emit("listening", "Listening for a double clap (medium sensitivity)")
        self.assertIn("double clap", app.status_action.text())
        self.assertIn("double clap", app.tray_icon.toolTip())

    def test_only_one_launcher_owns_the_microphone(self):
        if os.name != "nt":
            self.skipTest("Windows named mutex")
        from car_launcher import CarLauncherApp

        class Unique(CarLauncherApp):
            MUTEX_NAME = rf"Local\ULTRON_AutoLauncher_test_{os.getpid()}"

        first = Unique(listener_factory=FakeListener, ultron_running=lambda: False, launcher=lambda: None)
        second = Unique(listener_factory=FakeListener, ultron_running=lambda: False, launcher=lambda: None)
        self.addCleanup(first.watchdog.stop)
        self.assertFalse(first.duplicate)
        self.assertTrue(second.duplicate)
        self.assertEqual(second.listener.started, 0)                # the duplicate never listens
        first.quit_app()
        third = Unique(listener_factory=FakeListener, ultron_running=lambda: False, launcher=lambda: None)
        self.addCleanup(third.watchdog.stop)
        self.assertFalse(third.duplicate)                           # released on quit
        third.quit_app()


class LauncherHelpersTests(unittest.TestCase):

    def test_running_check_uses_script_path_not_folder_name(self):
        import car_launcher
        with patch("launch_ultron.running", return_value=True) as r:
            self.assertTrue(car_launcher.is_car_running())
        r.assert_called_once_with("main.py")

    def test_launch_uses_launch_ultron_without_a_shell(self):
        import car_launcher
        with patch.object(car_launcher.subprocess, "Popen") as popen:
            car_launcher.launch_ultron_process()
        args, kw = popen.call_args
        self.assertTrue(args[0][1].endswith("launch_ultron.py"))
        self.assertNotIn("shell", kw)

    def test_heard_speech_is_no_longer_logged_to_disk(self):
        src = (ROOT / "car_launcher.py").read_text(encoding="utf-8")
        self.assertNotIn('open(self.base_dir / "vosk_log.txt"', src)


if __name__ == "__main__":
    unittest.main()
