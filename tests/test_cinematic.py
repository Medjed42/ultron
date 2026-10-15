"""Startup / shutdown cinematics (core/cinematic) — offscreen, silent, no hardware.

Sequences are stepped deterministically (no wall clock) so the tests are fast
and repeatable; the window-level tests never let the real close path run, so
no plugin exit hook (TV, display mode) can fire.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ULTRON_SKIP_DEVICE_AUTOSTART"] = "1"
import random
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

from core import cinematic
from core import theme
from core.cinematic import fx
from core.cinematic import welcome as welcome_mod
from core.cinematic.settings import AnimationSettings
from memory import config_manager

# Tests never play the welcome recording: without a media player the narration
# clock runs on the wall clock, exactly its playback-failure path.
welcome_mod.QMediaPlayer = None


def _app():
    if QApplication.instance() is None:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    # A real argv[0]: with an empty argv Qt WebEngine (the Orbit view inside
    # MainWindow) aborts the process natively while the window is built.
    return QApplication.instance() or QApplication([sys.argv[0]])


def _run(ov, until=None, dt=1 / 60):
    """Step an overlay's timeline exactly like its timer would, minus the clock."""
    end = ov.duration if until is None else until
    while ov.t < end - 1e-9 and not ov.done:
        ov.t += min(dt, end - ov.t)
        while ov._cues and ov._cues[0][0] <= ov.t:
            ov._cues.pop(0)[2]()
        ov.advance(dt)
    # (a float sum can land a hair under `duration`: treat that as arrived)
    if not ov.done and ov.t >= ov.duration - 1e-9:
        ov.finish()


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(config_manager, "CONFIG_FILE", Path(self.tmp.name) / "api_keys.json")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_defaults_when_nothing_saved(self):
        self.assertEqual(config_manager.get_animation_settings(), config_manager.ANIMATION_DEFAULTS)

    def test_round_trip_persists_and_keeps_other_keys(self):
        config_manager._patch_config(assistant_name="Ultron")
        s = AnimationSettings.load()
        s.quality, s.sound, s.speed, s.skip_intro = "low", False, 1.5, True
        s.save()
        again = AnimationSettings.load()
        self.assertEqual((again.quality, again.sound, again.speed, again.skip_intro), ("low", False, 1.5, True))
        self.assertEqual(config_manager.load_api_keys()["assistant_name"], "Ultron")

    def test_launch_fullscreen_is_on_by_default_and_persists(self):
        self.assertTrue(AnimationSettings.load().launch_fullscreen)
        s = AnimationSettings.load()
        s.launch_fullscreen = False
        s.save()
        self.assertFalse(AnimationSettings.load().launch_fullscreen)
        self.assertIs(config_manager._clean_animation_settings({"launch_fullscreen": 1})["launch_fullscreen"], True)

    def test_invalid_values_are_cleaned(self):
        clean = config_manager._clean_animation_settings(
            {"quality": "insane", "speed": float("nan"), "reduced_motion": "ON", "glitch": 0})
        self.assertEqual(clean["quality"], "ultra")
        self.assertEqual(clean["speed"], 1.0)
        self.assertEqual(clean["reduced_motion"], "on")
        self.assertIs(clean["glitch"], False)
        self.assertEqual(config_manager._clean_animation_settings({"speed": 40})["speed"], 2.0)
        self.assertEqual(config_manager._clean_animation_settings("garbage"), config_manager.ANIMATION_DEFAULTS)

    def test_reduced_motion_disables_shake_glitch_and_strong_flashes(self):
        s = AnimationSettings(reduced_motion="on")
        self.assertTrue(s.reduced)
        self.assertFalse(s.use_shake)
        self.assertFalse(s.use_glitch)
        self.assertLess(s.flash_scale, 0.5)
        self.assertFalse(AnimationSettings(reduced_motion="off").reduced)


class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _app()

    def test_full_cycle(self):
        lc = cinematic.Lifecycle()
        seen = []
        lc.changed.connect(lambda a, b: seen.append(b))
        for st in (cinematic.BOOTING, cinematic.INITIALIZING, cinematic.ONLINE):
            self.assertTrue(lc.go(st))
        self.assertTrue(lc.begin_shutdown())
        self.assertTrue(lc.go(cinematic.OFFLINE))
        self.assertEqual(seen, ["BOOTING", "INITIALIZING", "ONLINE", "SHUTTING_DOWN", "OFFLINE"])
        self.assertTrue(lc.is_shutting_down)

    def test_illegal_steps_change_nothing(self):
        lc = cinematic.Lifecycle()
        self.assertFalse(lc.go(cinematic.SHUTTING_DOWN))      # not booted yet
        self.assertFalse(lc.go(cinematic.INITIALIZING))
        self.assertEqual(lc.state, cinematic.OFFLINE)
        lc.go(cinematic.ONLINE)                              # intro skipped
        self.assertTrue(lc.begin_shutdown())
        self.assertFalse(lc.begin_shutdown())                # second request is a no-op
        self.assertFalse(lc.go(cinematic.BOOTING))           # no boot over a shutdown

    def test_shutdown_can_interrupt_a_boot(self):
        lc = cinematic.Lifecycle()
        lc.go(cinematic.BOOTING)
        self.assertTrue(lc.begin_shutdown())


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _app()

    def test_bolt_hits_both_ends_and_respects_the_channel_cap(self):
        rng = random.Random(3)
        ch = fx.bolt_channels(10, 20, 900, 700, rng, detail=6, forks=40, fork_depth=3, max_channels=12)
        main = ch[0][0]
        self.assertEqual(main[0], (10, 20))
        self.assertEqual(main[-1], (900, 700))
        self.assertEqual(len(main), 2 ** 6 + 1)
        self.assertLessEqual(len(ch), 12)
        self.assertTrue(all(0.0 <= start < 1.0 for _p, _s, start in ch))

    def test_particles_age_out(self):
        ps = fx.Particles(50, random.Random(1))
        ps.burst(0, 0, 500, life=(0.1, 0.2))
        self.assertEqual(len(ps), 50)                        # capped
        for _ in range(20):
            ps.step(0.02)
        self.assertEqual(len(ps), 0)

    def test_easing_is_bounded(self):
        for f in (fx.ease_out_cubic, fx.ease_in_cubic, fx.ease_in_out_cubic, fx.ease_out_expo, fx.smoothstep):
            self.assertAlmostEqual(f(0.0), 0.0, places=3)
            self.assertAlmostEqual(f(1.0), 1.0, places=3)
        self.assertEqual(fx.seg(5, 1, 3), 1.0)
        self.assertEqual(fx.seg(0, 1, 3), 0.0)


class SequenceTests(unittest.TestCase):
    W, H = 1000, 660

    @classmethod
    def setUpClass(cls):
        cls.app = _app()

    def setUp(self):
        self.host = QWidget()
        self.host.resize(self.W, self.H)
        self.snap = QPixmap(self.W, self.H)
        self.snap.fill(QColor("#03070d"))

    def tearDown(self):
        self.host.deleteLater()

    def _settings(self, **kw):
        return AnimationSettings(**{"sound": False, "reduced_motion": "off", **kw})

    def _boot(self, **kw):
        dash = lambda: (self.snap, QRectF(0, 0, self.W, self.H),
                        [("left", QRectF(0, 60, 200, 500), (-1, 0))], QRectF(40, 10, 100, 30))
        ov = cinematic.BootSequence(self.host, self._settings(**kw), None, "ULTRON", dashboard=dash)
        ov.setGeometry(self.host.rect()); ov.prepare(); ov.on_resize()
        return ov

    def _shutdown(self, **kw):
        ov = cinematic.ShutdownSequence(self.host, self._settings(**kw), None, "ULTRON",
                                        snapshot=self.snap,
                                        secondary=[("footer", QRectF(0, 640, self.W, 20))])
        ov.setGeometry(self.host.rect()); ov.prepare(); ov.on_resize()
        return ov

    def test_boot_visits_every_phase_once_and_finishes_once(self):
        for quality in ("low", "ultra"):
            ov = self._boot(quality=quality)
            phases, done = [], []
            ov.phase_changed.connect(phases.append)
            ov.finished.connect(lambda: done.append(1))
            while not ov.done:
                _run(ov, min(ov.duration, ov.t + 0.5))
                ov.grab()                                    # paint a frame per half second
            self.assertEqual(phases, ["AWAKENING", "STRIKE", "INITIALIZING", "REVEAL", "ONLINE"])
            self.assertEqual(done, [1])
            ov.finish()
            self.assertEqual(done, [1])                       # idempotent

    def test_skip_lands_in_the_entrance_then_ends(self):
        ov = self._boot()
        _run(ov, 1.0)
        ov.skip()
        self.assertGreaterEqual(ov.t, ov.t_out)
        self.assertFalse(ov.done)
        self.assertIsNotNone(ov._snap)                        # dashboard picture taken
        ov.grab()
        ov.skip()
        self.assertTrue(ov.done)

    def test_reduced_motion_and_no_lightning_still_complete(self):
        ov = self._boot(reduced_motion="on", lightning=False, glitch=False)
        _run(ov, ov.T_STRIKE + 0.1)
        self.assertEqual(ov.shake.current(), 0.0)
        self.assertEqual(ov.bolts, [])
        _run(ov)
        self.assertTrue(ov.done)

    def test_shutdown_cuts_the_frame_into_two_halves(self):
        ov = self._shutdown()
        done = []
        ov.finished.connect(lambda: done.append(1))
        _run(ov, ov.T_SPLIT + 0.2)
        ov.grab()
        self.assertIsNotNone(ov._upper)
        self.assertIsNotNone(ov._lower)
        ux, uy, nx, ny, _L = ov._axes()
        self.assertGreater(nx, 0)                             # upper half heads right…
        self.assertLess(ny, 0)                                # …and up
        ox, oy = ov._offset(ov.T_AWAY_END, +1)
        lx, ly = ov._offset(ov.T_AWAY_END, -1)
        self.assertGreater(ox, 0); self.assertLess(oy, 0)
        self.assertLess(lx, 0); self.assertGreater(ly, 0)     # lower: left and down
        _run(ov)
        self.assertEqual(done, [1])
        self.assertIsNone(ov._upper)                          # big pixmaps released

    def test_a_drawing_error_finishes_instead_of_hanging(self):
        ov = self._shutdown()
        done = []
        ov.finished.connect(lambda: done.append(1))
        with patch.object(ov, "draw", side_effect=RuntimeError("boom")):
            ov.show()
            ov.grab()
        self.assertEqual(done, [1])
        ov.hide()

    def test_adaptive_quality_steps_down_on_slow_frames(self):
        ov = self._boot(quality="ultra")
        for _ in range(40):
            ov._watch_frame(80.0)
        self.assertEqual(ov.q.name, "low")


class CoreFxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _app()

    def test_power_on_ramps_from_dormant(self):
        f = cinematic.CoreFx()
        f.set_dormant()
        self.assertEqual(f.power(time.monotonic()), 0.0)
        t = time.monotonic()
        f.power_on(t, dur=1.0)
        self.assertGreater(f.power(t + 0.5), 0.3)
        self.assertEqual(f.power(t + 1.1), 1.0)

    def test_error_glitches_unless_reduced(self):
        f = cinematic.CoreFx()
        t = time.monotonic()
        f.on_state("ERROR", t)
        self.assertTrue(f.glitching(t + 0.1))
        g = cinematic.CoreFx()
        g.configure(motion=True, glitch=True, reduced=True)
        g.on_state("ERROR", t)
        self.assertFalse(g.glitching(t + 0.1))

    def test_an_expired_pulse_is_never_drawn(self):
        """Regression: a pulse past its life gave (1 - k) ** 1.4 a negative base —
        a complex number — and the core's paint raised on the main screen."""
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QPainter
        f = cinematic.CoreFx()
        now = time.monotonic()
        f.pulses = [(now - 1.5, 1.0), (now - 3.0, 0.5), (now - 0.2, 1.0)]
        pm = QPixmap(200, 200)
        pm.fill(QColor("#000000"))
        p = QPainter(pm)
        try:
            f.paint(p, QPointF(100, 100), 80, now)           # must not raise
        finally:
            p.end()
        self.assertTrue(f.enabled)

    def test_aicore_survives_a_broken_motion_layer(self):
        from core.ui_command_center import AICore
        core = AICore("ULTRON")
        core.resize(280, 230)
        for st in ("LISTENING", "THINKING", "RESPONDING", "ERROR", "IDLE"):
            core.set_state(st)
            core.set_level(0.7)
            core._tick()
            core.grab()
        with patch.object(core.fx, "paint", side_effect=RuntimeError("boom")):
            core.grab()                                       # must not raise
        self.assertFalse(core.fx.enabled)
        core.grab()


class WindowTests(unittest.TestCase):
    """The real MainWindow: intro skipped, nothing allowed to reach the exit path."""

    @classmethod
    def setUpClass(cls):
        cls.app = _app()
        os.environ["ULTRON_SKIP_INTRO"] = "1"

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("ULTRON_SKIP_INTRO", None)

    def setUp(self):
        import ui
        # No Orbit view: Qt WebEngine crashes the interpreter's own exit when a
        # window that holds it is built offscreen (with or without this
        # feature), and nothing here needs it.
        with patch.object(AnimationSettings, "load", classmethod(lambda cls: AnimationSettings(sound=False))), \
                patch.object(ui, "HAVE_WEBENGINE", False):
            self.win = ui.MainWindow("")
        # Never let a test reach the real close path (plugin exit hooks).
        self.win._fx_force_close = lambda: None
        # ...nor write the user's colour theme.
        self.saved = []
        self._save_patch = patch.object(config_manager, "save_ui_theme",
                                        lambda name, hx: self.saved.append((name, hx)))
        self._save_patch.start()

    def tearDown(self):
        self._save_patch.stop()
        self.win._rgb_stop()
        self.win._fx_shutdown_done = True
        self.win.hide()
        self.win.deleteLater()

    def _pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def test_instant_theme_change_recolours_and_persists(self):
        self.win._fx_settings.color_transform = False
        self.win.show()
        self.app.processEvents()
        import ui
        self.assertEqual(self.win.change_theme("blue", "ui"), "applied")
        self.assertAlmostEqual(theme.hex_to_hsv(ui.C.PRI)[0], theme.hex_to_hsv("#0088ff")[0], places=2)
        self.assertEqual(self.win.core.accent, theme.vivid("#0088ff"))
        self.assertEqual(self.saved[-1], ("blue", "#0088ff"))
        self.assertEqual(self.win.change_theme("#0088ff", "ui"), "same")
        self.assertEqual(self.win.change_theme("no such colour", "ui"), "invalid")
        # the header's literal colours moved too: nothing left in the default cyan
        cyan = theme.hex_to_hsv(ui.DEFAULT_UI_COLOR)[0]
        header = self.win._header_w.styleSheet()
        for m in theme._CSS_HEX.finditer(header):
            h, sat, _v = theme.hex_to_hsv("#" + m.group(1))
            if sat >= 0.10:
                self.assertGreater(abs(h - cyan), 0.03, header)

    def test_transformation_applies_once_and_queues_rapid_requests(self):
        self.win.show()
        self.app.processEvents()
        applied = []
        real = self.win._theme_apply

        def spy(spec, persist=True):
            applied.append(spec.name)
            real(spec, persist)
        with patch.object(self.win, "_theme_apply", side_effect=spy):
            self.assertEqual(self.win.change_theme("purple", "voice"), "started")
            ov = self.win._theme_seq
            self.assertIsNotNone(ov)
            self.assertEqual(self.win.change_theme("gold", "voice"), "queued")
            self.assertEqual(self.win.change_theme("green", "voice"), "queued")   # newest wins
            _run(ov)                                       # the whole transformation
            self.assertTrue(ov.done)
            self.assertEqual(applied, ["purple"])          # the real UI changed exactly once
            self._pump(0.4)                                # the queued request starts
            nxt = self.win._theme_seq
            self.assertIsNotNone(nxt)
            self.assertEqual(nxt.spec.name, "green")
            self.win._theme_abort()                        # e.g. shutdown mid-change
        self.assertEqual(self.win._theme.name, "green")    # ...still lands the colour

    def test_carui_change_theme_answers_immediately(self):
        import ui
        car = ui.CarUI.__new__(ui.CarUI)
        car._win = self.win
        self.win._theme = theme.resolve("red")
        self.assertEqual(car.change_theme("crvenu"), "same")
        self.assertEqual(car.change_theme("plavu"), "started")
        self.assertEqual(car.change_theme("xyz"), "invalid")
        self.assertFalse(car.intro_active)
        said = []
        car.request_say = said.append
        self.assertIs(self.win.request_say, car.request_say)

    def test_launch_opens_full_screen_unless_switched_off(self):
        self.assertTrue(self.win._fx_settings.launch_fullscreen)
        self.win.show_at_launch()
        self.app.processEvents()
        self.assertTrue(self.win.isFullScreen())
        self.win._toggle_fullscreen()                       # F11 still leaves it
        self.app.processEvents()
        self.assertFalse(self.win.isFullScreen())
        self.win.hide()
        self.win._fx_settings.launch_fullscreen = False
        self.win.show_at_launch()
        self.app.processEvents()
        self.assertTrue(self.win.isVisible())
        self.assertFalse(self.win.isFullScreen())

    def test_skipped_intro_goes_straight_online(self):
        self.win.show()
        self.app.processEvents()
        self.assertEqual(self.win._lifecycle.state, cinematic.ONLINE)
        self.assertIsNone(self.win._fx_boot)

    def test_close_is_intercepted_and_shutdown_runs_once(self):
        self.win.show()
        self.app.processEvents()
        with patch.object(self.win, "request_shutdown", wraps=self.win.request_shutdown) as req:
            self.win.close()
            self.win.close()
        self.assertTrue(self.win.isVisible())                 # cinematic first, not a vanish
        self.assertEqual(req.call_count, 2)
        self.assertIsNotNone(self.win._fx_shutdown)
        self.assertFalse(self.win.request_shutdown("ui"))     # already shutting down
        self.win._fx_shutdown.finish()
        self.assertTrue(self.win._fx_shutdown_event.is_set())
        self.assertEqual(self.win._lifecycle.state, cinematic.OFFLINE)

    def test_animation_off_completes_without_a_sequence(self):
        self.win._fx_settings.shutdown = False
        self.win.show()
        self.app.processEvents()
        self.assertTrue(self.win.request_shutdown("voice"))
        self.app.processEvents()
        self.assertIsNone(self.win._fx_shutdown)
        self.assertTrue(self.win._fx_shutdown_event.is_set())

    def test_escape_skips_the_intro_instead_of_interrupting(self):
        self.win._fx_settings.skip_intro = False
        os.environ.pop("ULTRON_SKIP_INTRO", None)
        try:
            interrupted = []
            self.win.on_interrupt = lambda: interrupted.append(1)
            self.win._fx_boot_started = False
            self.win.show()
            self.app.processEvents()
            boot = self.win._fx_boot
            self.assertIsNotNone(boot)
            self.win._do_interrupt()
            self.assertEqual(interrupted, [])
            self.assertGreaterEqual(boot.t, boot.t_out)
            boot.finish()
            self.assertEqual(self.win._lifecycle.state, cinematic.ONLINE)
        finally:
            os.environ["ULTRON_SKIP_INTRO"] = "1"


class ThemeTests(unittest.TestCase):
    """core/theme.py — commands and the stylesheet engine."""

    @classmethod
    def setUpClass(cls):
        cls.app = _app()          # before `import ui`: Qt WebEngine must not come first

    def test_english_and_serbian_commands(self):
        cases = {
            "Ultron, change your color to blue.": "blue", "Ultron, activate red.": "red",
            "Ultron, switch to purple.": "purple", "Ultron, turn everything green.": "green",
            "Ultron, change your interface to cyan.": "cyan", "Ultron, activate RGB mode.": "rgb",
            "Ultron, return to your original color.": "red", "Ultron, set your theme to #00FFFF.": "cyan",
            "Ultron, promeni boju u crvenu.": "red", "Ultron, prebaci se na plavu.": "blue",
            "Ultron, aktiviraj ljubičastu.": "purple", "Ultron, aktiviraj ljubicastu": "purple",
            "Ultron, vrati originalnu boju.": "red", "set your theme to #12ab34": "custom",
        }
        for text, want in cases.items():
            spec = theme.parse_command(text)
            self.assertIsNotNone(spec, text)
            self.assertEqual(spec.name, want, text)

    def test_not_colour_commands(self):
        for text in ("turn on the red light", "prebaci se na orbitu", "upali plavo svetlo u sobi",
                     "what is the best green tea", "the blue whale is big", "switch to orbit view",
                     "change the TV input to HDMI"):
            self.assertIsNone(theme.parse_command(text), text)

    def test_resolve_and_protocol_lines(self):
        self.assertEqual(theme.resolve("Zlatnu").name, "gold")
        self.assertEqual(theme.resolve("#FF163C").name, "red")
        self.assertEqual(theme.resolve("#ab12cd").label, "CUSTOM #AB12CD")
        self.assertTrue(theme.resolve("rainbow").is_rgb)
        self.assertIsNone(theme.resolve(""))
        self.assertEqual(theme.protocol_lines(theme.resolve("blue"))[0], "> BLUE PROTOCOL INITIALIZED")
        for name, (_label, hx) in theme.THEMES.items():
            self.assertEqual(theme.resolve(name).hex, hx)
        self.assertGreaterEqual(theme.hex_to_hsv(theme.vivid("#200008"))[2], 0.78)

    def _engine(self):
        import ui
        return theme.CssTheme(ui._PALETTE_DEFAULTS), ui

    def test_css_shift_keeps_semantic_colours_and_is_anchored_to_the_base(self):
        eng, ui = self._engine()
        css = ("QWidget { background: #03070d; border: 1px solid #10263a; color: #22e1ff; }"
               "QLabel#ok { color: #22c55e; } QLabel#err { color: #ef4444; }"
               "QPushButton { background: rgba(9,22,37,0.9); }")
        blue = eng.shift(css, "#0088ff")
        self.assertNotIn("#22e1ff", blue)
        self.assertIn("#22c55e", blue)                     # connected stays green
        self.assertIn("#ef4444", blue)                     # error stays red
        self.assertEqual(eng.shift(css, ui.DEFAULT_UI_COLOR), css)   # the default is the identity

    def test_restyle_is_exact_after_any_number_of_themes(self):
        eng, _ui = self._engine()
        _app()
        w = QWidget()
        base = "QWidget { background: #03070d; color: #22e1ff; border: 1px solid #10263a; }"
        w.setStyleSheet(base)
        for target in ("#ff163c", "#00ff88", "#f5f7ff", "#0088ff"):
            eng.restyle(w, target)
        self.assertEqual(w.styleSheet(), eng.shift(base, "#0088ff"))
        swatch = QWidget()
        swatch.setStyleSheet("QWidget { background: #0088ff; }")
        swatch.setProperty("_ultron_no_theme", True)
        eng.restyle(swatch, "#ff163c")
        self.assertEqual(swatch.styleSheet(), "QWidget { background: #0088ff; }")

    def test_white_theme_desaturates(self):
        eng, _ui = self._engine()
        out = eng.shift("color: #22e1ff;", "#f5f7ff")
        hx = theme._CSS_HEX.search(out).group(1)
        self.assertLess(theme.hex_to_hsv("#" + hx)[1], 0.2)


class WelcomeTests(unittest.TestCase):
    """The narrated welcome: its measured cues and its clock."""

    @classmethod
    def setUpClass(cls):
        cls.app = _app()

    def test_cue_file_matches_the_installed_recording(self):
        import json
        from core.cinematic import welcome_cues as wc
        self.assertTrue(wc.WELCOME_MP3.is_file())
        data = json.loads(wc.cue_path(wc.WELCOME_MP3).read_text(encoding="utf-8"))
        self.assertEqual(data["sha256"], wc.sha256(wc.WELCOME_MP3))
        cues = wc.load()
        self.assertEqual(cues.source, "measured")
        self.assertEqual({n for n, _k, _w in wc.ANCHORS}, set(cues.times))
        self.assertTrue(abs(cues.duration - 33.83) < 0.1)
        self.assertLess(cues["speech_end"], cues.duration)
        self.assertGreater(cues.level(cues["ultron"] + 0.2), cues.level(1.0))   # speech vs silence
        self.assertTrue(cues["hello"] < cues["allow"] < cues["ultron"] < cues["booting"]
                        < cues["natural"] < cues["mode"])

    def test_a_different_recording_is_reported_and_scaled(self):
        from core.cinematic import welcome_cues as wc
        with tempfile.TemporaryDirectory() as d:
            mp3 = Path(d) / "ultron_welcome.mp3"
            mp3.write_bytes(b"not the analysed recording")
            wc.cue_path(mp3).write_text(wc.cue_path(wc.WELCOME_MP3).read_text(encoding="utf-8"),
                                        encoding="utf-8")
            with patch.object(wc, "_duration", return_value=wc.MEASURED_DURATION * 2):
                cues = wc.load(mp3)
        self.assertEqual(cues.source, "scaled")
        self.assertAlmostEqual(cues["mode"], wc.MEASURED["mode"] * 2, places=1)

    def test_find_cues_follows_word_order(self):
        from core.cinematic.welcome_cues import find_cues
        words = [{"word": w, "start": s, "end": s + 0.3} for w, s in
                 (("hello", 0.0), ("allow", 1.0), ("i", 2.0), ("am", 2.35), ("ultra", 2.7),
                  ("voice", 5.0), ("natural", 8.0), ("voice", 8.35))]
        cues = find_cues(words)
        self.assertEqual(cues["ultron"], 2.7)
        self.assertEqual(cues["i_am"], 2.0)
        self.assertEqual(cues["natural"], 8.0)

    def test_clock_without_audio_runs_free_and_skips(self):
        clk = welcome_mod.NarrationClock(None)
        reasons = []
        clk.finished.connect(reasons.append)
        clk.start()
        self.assertEqual(reasons, ["failed"])
        time.sleep(0.05)
        self.assertGreater(clk.now(), 0.0)
        clk.stop(at=31.0)
        self.assertGreaterEqual(clk.now(), 31.0)          # a skip jumps, even on the wall clock
        self.assertEqual(reasons, ["failed"])             # finished fires once

    def test_welcome_runs_every_scene_and_reports_real_status(self):
        host = QWidget(); host.resize(1100, 700)
        snap = QPixmap(1100, 700); snap.fill(QColor("#03070d"))
        asked = []

        def ready():
            asked.append(1)
            return {"live": (None, "STANDBY"), "neural": (True, "24 TOOLS")}
        ov = cinematic.WelcomeSequence(host, AnimationSettings(sound=False, reduced_motion="off"), None,
                                       "ULTRON", readiness=ready, play_audio=False,
                                       dashboard=lambda: (snap, QRectF(0, 0, 1100, 700), [], None))
        ov.setGeometry(host.rect()); ov.on_resize()
        phases = []
        ov.phase_changed.connect(phases.append)
        while not ov.done:
            _run(ov, min(ov.duration, ov.t + 1.0))
            ov.grab()
        self.assertEqual(phases[:3], ["HELLO", "INTRODUCE", "I_AM_ULTRON"])
        for ph in ("ULTRON", "INTELLIGENCE", "NEVER_SLEEPS", "WATCHING", "BOOTING", "VOICE",
                   "HUMAN_VOICE", "ONLINE"):
            self.assertIn(ph, phases)
        self.assertTrue(asked)                             # the terminal asked the real subsystems
        self.assertEqual(ov._status("live"), (None, "STANDBY"))
        host.deleteLater()

    def test_the_narration_outlives_the_overlay(self):
        """Regression: the overlay is deleted the moment the intro ends; a media
        player destroyed while it still plays took the whole process down."""
        host = QWidget(); host.resize(800, 500)
        self.addCleanup(host.deleteLater)
        ov = cinematic.WelcomeSequence(host, AnimationSettings(sound=False), None, "ULTRON",
                                       play_audio=False, dashboard=lambda: None)
        self.assertIs(ov.clock.parent(), host)              # owned by the window
        self.assertGreater(ov.duration, ov.c.duration)      # never finishes before the recording

    def test_release_stops_the_player_before_handing_it_over(self):
        from unittest.mock import MagicMock
        clk = welcome_mod.NarrationClock(None)
        player, output = MagicMock(), MagicMock()
        clk.player, clk.output = player, output
        clk.stop(fade_ms=0)                                  # shutdown: no fade
        names = [c[0] for c in player.mock_calls]
        self.assertLess(names.index("stop"), names.index("deleteLater"))
        output.deleteLater.assert_called_once()
        self.assertIsNone(clk.player)
        self.assertGreaterEqual(clk.now(), 0.0)              # still a working clock

    def test_welcome_skip_lands_quickly(self):
        host = QWidget(); host.resize(900, 600)
        ov = cinematic.WelcomeSequence(host, AnimationSettings(sound=False), None, "ULTRON",
                                       play_audio=False, dashboard=lambda: None)
        ov.setGeometry(host.rect()); ov.on_resize()
        _run(ov, 3.0)
        ov.skip()
        self.assertEqual(ov.t, ov.t_out)
        self.assertAlmostEqual(ov.duration, ov.t_out + 1.0)
        ov.skip()
        self.assertTrue(ov.done)
        host.deleteLater()


class TransformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _app()

    def _make(self, **kw):
        host = QWidget(); host.resize(1000, 640)
        self.addCleanup(host.deleteLater)
        snap = QPixmap(1000, 640); snap.fill(QColor("#03070d"))
        calls = {"apply": 0, "grab": 0}

        def apply():
            calls["apply"] += 1

        def grab():
            calls["grab"] += 1
            return snap, QRectF(0, 0, 1000, 640)
        spec = theme.resolve("blue")
        ov = cinematic.ColorTransformSequence(host, AnimationSettings(sound=False, **kw), None,
                                              spec=spec, snapshot=snap, apply_theme=apply,
                                              grab_new=grab, lines=theme.protocol_lines(spec))
        ov.setGeometry(host.rect()); ov.on_resize(); ov.prepare()
        return ov, calls

    def test_full_transformation(self):
        ov, calls = self._make(reduced_motion="off")
        self.assertGreater(len(ov.shards), 20)
        phases = []
        ov.phase_changed.connect(phases.append)
        while not ov.done:
            _run(ov, min(ov.duration, ov.t + 0.5))
            ov.grab()
        self.assertEqual(calls, {"apply": 1, "grab": 1})
        self.assertEqual(phases, ["CHARGE", "CRACK", "SHATTER", "REINITIALIZE", "LIGHTNING", "ONLINE"])
        self.assertTrue(all(sh.new is not None for sh in ov.shards if sh.old is not None))

    def test_reduced_motion_still_changes_the_colour(self):
        ov, calls = self._make(reduced_motion="on")
        self.assertLess(ov.duration, 2.5)
        self.assertEqual(ov.shards, [])
        _run(ov)
        self.assertEqual(calls["apply"], 1)


class AudioManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _app()

    def test_source_resolution_order(self):
        from core.cinematic import sound
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "v2").mkdir()
            wav = (sound.ASSET_DIR / "v2" / "02_ui_click.wav").read_bytes()
            (root / "v2" / "02_ui_click.wav").write_bytes(wav)          # V2 match for 12…
            (root / "12_ui_confirm_tick.wav").write_bytes(wav)          # …but the pack's own wins
            (root / "v2" / "06_graph_focus.wav").write_bytes(wav)       # V2 match for 07
            src = sound.resolve_sources(root)
        self.assertEqual(src["12_ui_confirm_tick"][0], "pack")
        self.assertEqual(src["07_system_scan"][0], "v2")
        self.assertEqual(src["05_screen_fracture"][0], "synth")
        self.assertEqual(set(src), set(sound.SOUND_IDS))

    def test_ducking_and_caps(self):
        from core.cinematic import sound
        am = sound.AudioManager(enabled=False, volume=1.0)
        self.assertLessEqual(am._effective(1.0), sound.MAX_VOLUME)
        am.set_volume(0.4)
        am.duck("voice", 0.45)
        self.assertAlmostEqual(am._effective(1.0), 0.18)
        am.duck("narration", 0.0)
        self.assertEqual(am._effective(1.0), 0.0)                 # narration silences effects
        am.unduck("narration")
        self.assertAlmostEqual(am._effective(1.0), 0.18)
        self.assertFalse(am.play("04_lightning_strike"))          # disabled: never plays


class VoiceIntegrationTests(unittest.TestCase):
    """The voice tool and the backend's handling of colour and the welcome."""

    def test_voice_tool_is_discovered_and_answers(self):
        from core.action_loader import discover_actions
        reg = discover_actions(Path(__file__).resolve().parents[1] / "actions", logger=lambda m: None)
        self.assertTrue(reg.has("change_ui_color"))
        calls = []

        class Player:
            def change_theme(self, value, source="voice"):
                calls.append((value, source))
                return "started"
        out = reg.run("change_ui_color", {"color": "ljubičastu"}, {"player": Player()})
        self.assertEqual(calls, [("#a020f0", "voice")])
        self.assertIn("Understood. Initializing purple energy system.", out)
        self.assertIn("not a colour", reg.run("change_ui_color", {"color": "plaid"}, {"player": Player()}))

    def test_typed_colour_command_is_handled_locally(self):
        import main
        live = main.CarLive.__new__(main.CarLive)
        calls, logs = [], []

        class UI:
            def change_theme(self, value, source="voice"):
                calls.append((value, source))
                return "started"

            def write_log(self, text):
                logs.append(text)
        live.ui = UI()
        self.assertTrue(live._handle_view_command("Ultron, prebaci se na plavu."))
        self.assertEqual(calls, [("#0088ff", "typed")])
        self.assertIn("Initializing Blue energy system", logs[-1])
        self.assertTrue(live._handle_view_command("Ultron, activate RGB mode"))
        self.assertEqual(calls[-1], ("rgb", "typed"))
        self.assertFalse(live._handle_view_command("turn on the red light"))

    def test_voice_handoff_only_after_a_completed_welcome(self):
        import main
        live = main.CarLive.__new__(main.CarLive)
        said = []
        live.plugin_say = said.append
        live._awake = True

        class UI:
            muted = False
        live.ui = UI()
        with patch.object(main, "get_brief_enabled", return_value=False), \
                patch.object(config_manager, "get_animation_settings", return_value={"voice_handoff": True}):
            live._on_intro_finished("stopped")
            live._on_intro_finished("failed")
            self.assertEqual(said, [])
            live._on_intro_finished("end")
            self.assertEqual(len(said), 1)
        with patch.object(main, "get_brief_enabled", return_value=True):
            live._on_intro_finished("end")                 # the briefing greets instead
        self.assertEqual(len(said), 1)


if __name__ == "__main__":
    unittest.main()

