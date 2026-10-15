"""
core/cinematic/welcome.py — the narrated welcome: ULTRON introduces himself.

The recording (assets/audio/ultron_welcome.mp3) is the master clock. Every
scene is pinned to a cue measured from the file itself (welcome_cues.py), and
the core breathes with the recording's own loudness envelope:

    hello            black; a spark; red embers; a core forms; thin branches
    allow            holographic lines converge; a mechanical ring assembles,
                     arcs racing round it; the name starts to materialise
    I am / Ultron    the wordmark ignites letter by letter; at "Ultron" a bolt
                     runs through it, energy bursts outward, a restrained
                     holographic shockwave
    artificial …     the wordmark rises; concentric rings, a neural network that
                     grows on "think", "learn", "evolve"; circuitry, a scan grid;
                     the network breaks through its boundary on "surpasses the
                     limitations of human understanding"
    I do not sleep / forget   the core intensifies step by step; precise scan
                     lines; a touch of digital distortion
    always watching  an abstract energy eye; energy focuses into the pupil
    system booting up         the initialisation terminal, statuses from the
                     real subsystems (tools, providers, live voice)
    natural voice    a waveform and circular equaliser morph from distorted to
                     smooth; "voice transformation" lines (real audio device and
                     voice); "human voice mode" — a final sweep, a bolt through
                     the core, and the real dashboard assembles as the recording
                     rings out

Nothing here plays a sound: the recording already carries its own effects, so
the cinematic effects are held silent while it plays (and the live voice is
held back by the application until it ends).
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PyQt6.QtCore import QElapsedTimer, QObject, QPointF, QRectF, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap, QRadialGradient,
)
from PyQt6.QtWidgets import QPushButton

from . import entrance, fx
from .fx import (
    PLUS, VOID, bell, clamp, ease_in_cubic, ease_in_out_cubic, ease_out_back, ease_out_cubic,
    ease_out_expo, lerp, rgba, seg, smoothstep,
)
from .overlay import CinematicOverlay
from .welcome_cues import WELCOME_MP3, Cues, load as load_cues

try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
except Exception:                        # no multimedia: the welcome runs on the wall clock
    QAudioOutput = QMediaDevices = QMediaPlayer = None

_ALIGN_C = Qt.AlignmentFlag.AlignCenter
_ALIGN_LV = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
_ALIGN_RV = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
AMBER = "#f5b942"                        # a real subsystem that is not ready


class NarrationClock(QObject):
    """The welcome's master clock: where the recording is, right now.

    The player's position is read every frame and interpolated between its
    updates (never more than 0.25 s ahead), so the picture moves smoothly but
    can never run away from the voice. While the player buffers, stalls or has
    not started yet, time holds still — the picture waits for the voice. If the
    file is missing, the player errors, or nothing starts within 3 s, the clock
    carries on from where it was on the wall clock instead, so the sequence still
    completes. `finished` fires exactly once when the audio is over for any
    reason (end, skip, failure)."""

    finished = pyqtSignal(str)          # "end" | "stopped" | "failed"

    START_TIMEOUT = 3.0
    STALL_TIMEOUT = 4.0

    def __init__(self, path: Path | None, volume: float = 0.85, device_hint: str = "",
                 parent=None):
        super().__init__(parent)
        self._wall = QElapsedTimer()
        self._t = 0.0
        self._pos = 0.0
        self._pos_wall = 0.0
        self._hold_since: float | None = None
        self._free_from: tuple[float, float] | None = None     # (t, wall) once on the wall clock
        self._done = False
        self.reason = ""
        self.player = None
        self.output = None
        self._fade: QTimer | None = None
        if QMediaPlayer is not None and path is not None and Path(path).is_file():
            try:
                self.output = QAudioOutput(self._device(device_hint), self)
                self.output.setVolume(max(0.0, min(1.0, float(volume))))
                self.player = QMediaPlayer(self)
                self.player.setAudioOutput(self.output)
                self.player.errorOccurred.connect(lambda *_a: self._go_free("failed"))
                self.player.mediaStatusChanged.connect(self._on_status)
                self.player.setSource(QUrl.fromLocalFile(str(path)))
            except Exception as e:
                print(f"[Welcome] audio unavailable: {e}")
                self.player = None

    @staticmethod
    def _device(hint: str):
        """The output device ULTRON is configured to speak through, if Qt can
        see it under that name; otherwise the system default."""
        try:
            hint = (hint or "").strip().lower()
            if hint:
                for dev in QMediaDevices.audioOutputs():
                    d = dev.description().lower()
                    if hint in d or d in hint:
                        return dev
            return QMediaDevices.defaultAudioOutput()
        except Exception:
            return QMediaDevices.defaultAudioOutput() if QMediaDevices else None

    @property
    def has_audio(self) -> bool:
        return self.player is not None

    def start(self) -> None:
        self._wall.start()
        if self.player is None:
            self._go_free("failed")
            return
        try:
            self.player.play()
        except Exception:
            self._go_free("failed")

    def _now_wall(self) -> float:
        return self._wall.elapsed() / 1000.0 if self._wall.isValid() else 0.0

    def now(self) -> float:
        w = self._now_wall()
        if self._free_from is not None:
            t0, w0 = self._free_from
            self._t = max(self._t, t0 + (w - w0))
            return self._t
        p = self.player
        if p is None:                       # released without going free: never stall
            self._go_free("stopped")
            return self._t
        pos = p.position() / 1000.0
        if pos != self._pos:
            self._pos, self._pos_wall = pos, w
            self._hold_since = None
        playing = p.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        status = p.mediaStatus()
        busy = status in (QMediaPlayer.MediaStatus.LoadingMedia, QMediaPlayer.MediaStatus.BufferingMedia,
                          QMediaPlayer.MediaStatus.StalledMedia)
        if playing and not busy and pos > 0:
            t = pos + min(w - self._pos_wall, 0.25)
        else:
            # Not started, buffering or stalled: the picture waits for the voice.
            if self._hold_since is None:
                self._hold_since = w
            limit = self.START_TIMEOUT if pos <= 0 else self.STALL_TIMEOUT
            if w - self._hold_since > limit:
                self._go_free("failed")
                return self._t
            t = pos
        self._t = max(self._t, t)
        return self._t

    def _on_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._go_free("end")
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._go_free("failed")

    def _go_free(self, reason: str) -> None:
        if self._free_from is not None:
            return
        if not self._wall.isValid():
            self._wall.start()
        self._free_from = (self._t, self._now_wall())
        if reason == "failed":
            print("[Welcome] narration playback failed - the welcome continues on its own clock.")
            self._release()
        self._finish(reason)

    def _finish(self, reason: str) -> None:
        if not self._done:
            self._done = True
            self.reason = reason
            self.finished.emit(reason)

    def stop(self, at: float | None = None, fade_ms: int = 220) -> None:
        """Skip: fade the narration out quickly and run on the wall clock from
        `at` (or from where it is). fade_ms <= 0 silences it at once."""
        if not self._wall.isValid():
            self._wall.start()
        if at is not None:
            self._t = max(self._t, at)
        if at is not None or self._free_from is None:
            # Re-anchor even if already on the wall clock (playback had failed):
            # a skip jumps the timeline, it does not wait for the clock.
            self._free_from = (self._t, self._now_wall())
        self._fade_out(fade_ms)
        self._finish("stopped")

    def _fade_out(self, fade_ms: int) -> None:
        if self.output is None or self.player is None:
            return
        if fade_ms <= 0:
            self._release()
            return
        self._fade_steps = max(1, fade_ms // 30)
        self._fade_i = 0
        self._fade_v0 = self.output.volume()
        if self._fade is None:
            self._fade = QTimer(self)          # dies with the clock, never outlives it
            self._fade.setInterval(30)
            self._fade.timeout.connect(self._fade_step)
        self._fade.start()

    def _fade_step(self) -> None:
        self._fade_i += 1
        if self.output is None or self._fade_i >= self._fade_steps:
            self._release()
            return
        self.output.setVolume(self._fade_v0 * (1 - self._fade_i / self._fade_steps))

    def _release(self) -> None:
        """Stop the narration for good. The player is stopped synchronously —
        its decoder thread must be idle before the object goes; destroying a
        QMediaPlayer mid-playback takes the whole process down — and only then
        handed to Qt for deletion."""
        if self._fade is not None:
            self._fade.stop()
        p, o = self.player, self.output
        self.player = self.output = None
        if p is not None:
            try:
                p.mediaStatusChanged.disconnect()
                p.errorOccurred.disconnect()
            except Exception:
                pass
            try:
                p.stop()
                p.setSource(QUrl())
            except Exception:
                pass
            p.deleteLater()
        if o is not None:
            o.deleteLater()


class WelcomeSequence(CinematicOverlay):
    SKIPPABLE = True
    ENTRANCE_LEAD = 0.25        # the dissolve starts this long after the last word

    audio_finished = pyqtSignal(str)   # the narration is over: "end" | "stopped" | "failed"

    def __init__(self, host, settings, sfx=None, name: str = "ULTRON", dashboard=None,
                 readiness=None, mp3: Path = WELCOME_MP3, volume: float = 0.85,
                 device_hint: str = "", cues: Cues | None = None, play_audio: bool = True):
        super().__init__(host, settings, sfx)
        self.name = (name or "ULTRON").upper()
        self.c = cues or load_cues(mp3)
        c = self.c
        self.t_out = c["speech_end"] + self.ENTRANCE_LEAD
        # Finish only after the recording has: the last frame never cuts it off.
        self.duration = max(self.t_out + 1.4, c.duration + 0.15)
        self.dashboard = dashboard
        self.readiness = readiness or (lambda: {})
        self._ready_cache: dict = {}
        self._ready_at = -9.0
        # The clock (and its media player) belongs to the window, not to this
        # overlay: the overlay is deleted the moment it finishes, and a player
        # destroyed while it still plays crashes the process.
        self.clock = NarrationClock(mp3 if play_audio else None, volume, device_hint, host)
        # A method, not the signal's emit: Qt drops the connection with this
        # overlay, so a clock that reports after the overlay is gone is harmless.
        self.clock.finished.connect(self._on_clock_finished)
        self._skipped = False
        self._snap = None
        self._snap_failed = False

        self.parts = fx.Particles(self.q.particles, self.rng)
        self.bolts: list[fx.Bolt] = []
        self.rings: list[tuple] = []         # (born, dur, max_r, width, squash, cy_off)
        self.flash: tuple | None = None
        self.shake = fx.Shake(self.rng)
        self.glitch_windows = []
        self.logo_big = fx.LogoArt(self.name)
        self.logo_top = fx.LogoArt(self.name)
        self._travel: fx.Bolt | None = None
        self._lines_rng = random.Random(11)
        self._geo_key = None
        self._ember_acc = self._tendril_acc = self._stream_acc = self._focus_acc = 0.0
        self._arc_acc = 0.0
        self._words: list[tuple[float, float]] = []
        try:
            import json
            data = json.loads(mp3.with_suffix(".cues.json").read_text(encoding="utf-8"))
            self._words = [(w["s"], w["e"]) for w in data.get("words", [])]
        except Exception:
            pass

        self._skip_btn = QPushButton("SKIP INTRO   ESC", self)
        self._skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skip_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._skip_btn.setProperty("_ultron_no_theme", True)   # styled by the cinematic itself
        self._skip_btn.clicked.connect(self.skip)
        self._style_skip()
        self._skip_btn.hide()

        light = settings.use_lightning
        self._light = light
        glitch = settings.use_glitch
        if glitch:
            self.glitch_windows = [(c["ultron"] + 0.02, 0.16), (c["sleep_word"] + 0.5, 0.10),
                                   (c["forget_word"] + 0.55, 0.10), (c["mode"] + 0.02, 0.18)]
        self.cue(0.0, lambda: self.set_phase("HELLO"))
        self.cue(0.6, self._show_skip)
        self.cue(c["allow"], lambda: self.set_phase("INTRODUCE"))
        self.cue(c["i_am"], lambda: self.set_phase("I_AM_ULTRON"))
        self.cue(c["ultron"], self._ultron_strike)
        self.cue(c["ai"], lambda: self.set_phase("INTELLIGENCE"))
        for key in ("think", "learn", "evolve"):
            self.cue(c[key], lambda key=key: self._layer_pulse(key))
        self.cue(c["sleep"], lambda: self.set_phase("NEVER_SLEEPS"))
        self.cue(c["sleep_word"], lambda: self._core_pulse(0.7))
        self.cue(c["forget_word"], lambda: self._core_pulse(0.85))
        self.cue(c["always"], lambda: self.set_phase("WATCHING"))
        self.cue(c["watching"], lambda: self._core_pulse(0.6))
        self.cue(c["booting"], lambda: self.set_phase("BOOTING"))
        self.cue(c["natural"], lambda: self.set_phase("VOICE"))
        self.cue(c["mode"], self._core_strike)
        self.cue(c["speech_end"] - 0.6, self._grab_dashboard)
        self.cue(self.t_out, self._begin_entrance)

    # ── clock, skipping, lifecycle ───────────────────────────────────────────
    def prepare(self) -> None:
        self.clock.start()

    def timeline(self, dt: float) -> float:
        return self.clock.now()

    def _on_clock_finished(self, reason: str) -> None:
        self.audio_finished.emit(reason)

    def halt(self) -> None:
        """Shutdown: silence the narration at once (no fade) and finish."""
        self.clock.stop(fade_ms=0)
        self.finish()

    def _style_skip(self) -> None:
        a = QColor(fx.ACCENT)
        r, g, b = a.red(), a.green(), a.blue()
        self._skip_btn.setStyleSheet(
            "QPushButton { color: #f6f6f8; background: rgba(%d,%d,%d,16);"
            " border: 1px solid rgba(%d,%d,%d,140); border-radius: 3px;"
            " padding: 6px 14px; font-family: Consolas; font-size: 9pt; letter-spacing: 2px; }"
            "QPushButton:hover { background: rgba(%d,%d,%d,70); border-color: %s; }"
            % (r, g, b, r, g, b, r, g, b, fx.ACCENT))

    def _show_skip(self) -> None:
        if not self.done and self.t < self.t_out:
            self._place_skip()
            self._skip_btn.show()
            self._skip_btn.raise_()

    def _place_skip(self) -> None:
        b = self._skip_btn
        b.adjustSize()
        b.move(int(self.W - b.width() - 24), int(self.H - b.height() - 22))

    def on_resize(self) -> None:
        self._geo_key = None
        if self._skip_btn.isVisible():
            self._place_skip()

    def skip(self) -> None:
        """First press: the narration fades out and the dashboard assembles.
        Second press (or during the dissolve): finish now."""
        if self.done:
            return
        if self.t >= self.t_out - 0.05 or self._skipped:
            self.clock.stop()
            self.finish()
            return
        self._skipped = True
        self._cues = [c for c in self._cues if c[0] >= self.t_out]
        self.bolts.clear()
        self._travel = None
        self.flash = None
        self._grab_dashboard()
        self.clock.stop(at=self.t_out)
        self.t = self.t_out
        self.duration = self.t_out + 1.0      # a skip lands quickly, not on the recording's tail
        self._begin_entrance()

    def finish(self) -> None:
        self._skip_btn.hide()
        self.clock.stop(fade_ms=150)
        super().finish()

    def _grab_dashboard(self) -> None:
        if self._snap is not None or self._snap_failed:
            return
        try:
            self._snap = self.dashboard() if self.dashboard else None
        except Exception as e:
            print(f"[Welcome] dashboard snapshot failed: {e}")
            self._snap = None
        if self._snap is None:
            self._snap_failed = True

    def _begin_entrance(self) -> None:
        self._grab_dashboard()
        self._skip_btn.hide()
        self.set_phase("ONLINE")
        if self._snap is None:
            self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    # ── geometry ─────────────────────────────────────────────────────────────
    def _geometry(self) -> None:
        key = (self.width(), self.height())
        if key == self._geo_key:
            return
        self._geo_key = key
        W, H, s = self.W, self.H, self.scale
        self.cx, self.cy = W / 2, H * 0.47
        self.R0 = 0.11 * min(W, H * 1.15)
        rng = random.Random(5)
        # neural network: four rings of nodes, revealed on "artificial", "think", "learn", "evolve"
        counts, radii = (7, 11, 15, 19), (1.35, 1.75, 2.2, 2.65)
        self.layers = []
        for li, (n, rf) in enumerate(zip(counts, radii)):
            off = rng.uniform(0, math.tau)
            nodes = []
            for k in range(n):
                a = off + k / n * math.tau + rng.uniform(-0.12, 0.12)
                r = rf * self.R0 * rng.uniform(0.93, 1.07)
                nodes.append((a, r))
            self.layers.append(nodes)
        self.edges = []        # (layer_a, i, layer_b, j)
        for li in range(1, len(self.layers)):
            prev, cur = self.layers[li - 1], self.layers[li]
            for j, (a, _r) in enumerate(cur):
                near = sorted(range(len(prev)), key=lambda i: abs(math.remainder(prev[i][0] - a, math.tau)))
                for i in near[:2 if rng.random() < 0.55 else 1]:
                    self.edges.append((li - 1, i, li, j))
            for j in range(len(cur)):
                if rng.random() < 0.35:
                    self.edges.append((li, j, li, (j + 1) % len(cur)))
        # circuitry: Manhattan traces from the edges toward the network
        self.traces = []
        for _ in range(12):
            side = rng.choice(("l", "r", "t", "b"))
            if side in ("l", "r"):
                x, y = (0.0 if side == "l" else W), rng.uniform(0.12, 0.88) * H
            else:
                x, y = rng.uniform(0.08, 0.92) * W, (0.0 if side == "t" else H)
            pts = [(x, y)]
            for _step in range(5):
                tx = lerp(x, self.cx, rng.uniform(0.25, 0.45))
                ty = lerp(y, self.cy, rng.uniform(0.25, 0.45))
                if rng.random() < 0.5:
                    pts.append((tx, y)); pts.append((tx, ty))
                else:
                    pts.append((x, ty)); pts.append((tx, ty))
                x, y = tx, ty
                if math.hypot(x - self.cx, y - self.cy) < 3.0 * self.R0:
                    break
            self.traces.append(pts)
        # streams: holographic lines converging on the centre (scene 2)
        self.stream_items: list[list] = []
        # the wordmark — big under the core for "I am Ultron" (the core's glow
        # would swallow letters drawn across it), small at the top afterwards
        size = clamp(min(W, H) * 0.115, 44.0, 150.0)
        dpr = self.devicePixelRatioF() or 1.0
        self.logo_big.layout(self.cx, min(H - size * 0.8, self.cy + 2.55 * self.R0), size, W * 0.78, dpr)
        self.logo_top.layout(self.cx, H * 0.12, self.logo_big.size * 0.42, W * 0.5, dpr)
        self._grid = None

    def _node_xy(self, li: int, i: int, t: float) -> tuple[float, float]:
        a, r = self.layers[li][i]
        a += t * (0.05 - 0.018 * li)                     # the network turns slowly
        push = 1.0
        if li == len(self.layers) - 1:                   # the outer ring breaks the limit
            push = 1.0 + 0.16 * ease_out_back(seg(t, self.c["understanding_end"] - 0.6,
                                                  self.c["understanding_end"] + 0.3))
        return self.cx + math.cos(a) * r * push, self.cy + math.sin(a) * r * push * 0.92

    def _grid_pixmap(self) -> QPixmap:
        if self._grid is None:
            dpr = self.devicePixelRatioF() or 1.0
            pm = QPixmap(max(1, int(self.W * dpr)), max(1, int(self.H * dpr)))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            step = 44 * self.scale
            q.setPen(QPen(rgba(fx.ACCENT_DEEP, 0.7), 1.0))
            x = self.cx % step
            while x < self.W:
                q.drawLine(QPointF(x, 0), QPointF(x, self.H))
                x += step
            y = self.cy % step
            while y < self.H:
                q.drawLine(QPointF(0, y), QPointF(self.W, y))
                y += step
            g = QRadialGradient(QPointF(self.cx, self.cy), math.hypot(self.W, self.H) * 0.5)
            g.setColorAt(0.0, QColor(0, 0, 0, 0))
            g.setColorAt(0.25, QColor(0, 0, 0, 0))
            g.setColorAt(1.0, QColor(0, 0, 0, 255))
            q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
            q.fillRect(QRectF(0, 0, self.W, self.H), QBrush(g))
            q.end()
            self._grid = pm
        return self._grid

    # ── events ───────────────────────────────────────────────────────────────
    def _ultron_strike(self) -> None:
        self.set_phase("ULTRON")
        s, b = self.scale, self.logo_big.bounds
        y = b.center().y()
        if self._light:
            self._travel = fx.Bolt((b.left() - 70 * s, y), (b.right() + 70 * s, y), self.t, 0.7,
                                   2.4 * s, self.rng, detail=self.q.bolt_detail, rough=0.08,
                                   forks=6.0, depth=min(2, self.q.branch_depth), restrikes=2,
                                   travel=0.32, max_channels=20)
        self.rings.append((self.t, 1.2, 0.45 * self.W, 2.2 * s, 0.16, b.bottom() + 18 * s - self.cy))
        self.rings.append((self.t + 0.05, 1.0, 0.36 * math.hypot(self.W, self.H), 1.4 * s, 1.0, 0.0))
        self.flash = (self.t, 0.28 * self.settings.flash_scale, 0.45, self.cx, y)
        self.parts.burst(self.cx, y, int(self.q.particles * 0.25), speed=(120 * s, 620 * s),
                         life=(0.3, 0.9), gravity=480 * s, size=(0.8 * s, 2.0 * s))
        if self.settings.use_shake:
            self.shake.kick(3.0 * s, 0.3)

    def _layer_pulse(self, key: str) -> None:
        li = {"think": 1, "learn": 2, "evolve": 3}[key]
        r = self.layers[li][0][1] if self.layers else 2 * self.R0
        self.rings.append((self.t, 0.9, r * 1.05, 1.2 * self.scale, 1.0, 0.0))

    def _core_pulse(self, strength: float) -> None:
        self.rings.append((self.t, 0.8, self.R0 * (1.6 + strength), (1.0 + strength) * self.scale, 1.0, 0.0))

    def _core_strike(self) -> None:
        self.set_phase("HUMAN_VOICE")
        s = self.scale
        if self._light:
            self.bolts.append(fx.Bolt((self.cx + self.rng.uniform(-30, 30) * s, -20.0),
                                      (self.cx + self.rng.uniform(-30, 30) * s, self.H + 20), self.t,
                                      0.6, 3.4 * s, self.rng, detail=self.q.bolt_detail, rough=0.12,
                                      forks=7.0, depth=self.q.branch_depth, restrikes=3, travel=0.22,
                                      max_channels=24))
        self.flash = (self.t + 0.1, 0.3 * self.settings.flash_scale, 0.4, self.cx, self.cy)
        self.rings.append((self.t + 0.1, 1.0, 0.42 * math.hypot(self.W, self.H), 2.4 * s, 1.0, 0.0))
        self.parts.burst(self.cx, self.cy, int(self.q.particles * 0.3), speed=(160 * s, 760 * s),
                         life=(0.3, 1.0), gravity=700 * s, size=(0.8 * s, 2.2 * s))
        if self.settings.use_shake:
            self.shake.kick(6 * s, 0.4)

    # ── simulation ───────────────────────────────────────────────────────────
    def advance(self, dt: float) -> None:
        self._geometry()
        t, c, s, rng = self.t, self.c, self.scale, self.rng
        qf = {"low": 0.45, "medium": 0.75}.get(self.q.name, 1.0)
        self.shake.step(dt)
        self.parts.step(dt)
        for b in self.bolts:
            b.step(t, rng)
        self.bolts = [b for b in self.bolts if b.alive(t)]
        if self._travel is not None:
            self._travel.step(t, rng)
            if not self._travel.alive(t):
                self._travel = None
        self.rings = [r for r in self.rings if t < r[0] + r[1]]
        cx, cy, R0 = self.cx, self.cy, self.R0

        if t < self.t_out:      # subtle red embers throughout
            self._ember_acc += (6 + 22 * self._intensity(t)) * qf * dt
            while self._ember_acc >= 1:
                self._ember_acc -= 1
                self.parts.add(rng.uniform(0, self.W), rng.uniform(self.H * 0.3, self.H * 1.02),
                               rng.uniform(-6, 6) * s, -rng.uniform(10, 40) * s, rng.uniform(2.5, 5.5),
                               rng.uniform(0.6, 1.8) * s, fx.EMBER)
        # scene 1: thin branches from the spark
        if self._light and c["hello"] + 0.3 <= t < c["i_am"]:
            pr = seg(t, c["hello"], c["allow"])
            self._tendril_acc += (2 + 10 * pr) * qf * dt
            while self._tendril_acc >= 1:
                self._tendril_acc -= 1
                ang = rng.uniform(0, math.tau)
                L = lerp(18 * s, 0.9 * R0, pr) * rng.uniform(0.5, 1.0)
                self.bolts.append(fx.Bolt((cx, cy), (cx + math.cos(ang) * L, cy + math.sin(ang) * L), t,
                                          rng.uniform(0.07, 0.16), 0.8 * s, rng, detail=4, rough=0.3,
                                          forks=1.0, depth=1, restrikes=1))
        # scene 2: holographic energy lines converging on the centre
        if c["allow"] <= t < c["ultron"]:
            self._stream_acc += 26 * qf * dt
            while self._stream_acc >= 1:
                self._stream_acc -= 1
                a = rng.uniform(0, math.tau)
                self.stream_items.append([a, rng.uniform(0.55, 0.9), t, rng.uniform(0.5, 0.9)])
        self.stream_items = [it for it in self.stream_items if t - it[2] < it[3]]
        # scene 2: arcs racing around the forming ring
        if self._light and c["allow"] + 0.3 <= t < c["ai"]:
            self._arc_acc += 9 * qf * dt
            while self._arc_acc >= 1:
                self._arc_acc -= 1
                a0 = rng.uniform(0, math.tau)
                r = 2.05 * R0
                self.bolts.append(fx.Bolt((cx + math.cos(a0) * r, cy + math.sin(a0) * r),
                                          (cx + math.cos(a0 + 0.4) * r, cy + math.sin(a0 + 0.4) * r), t,
                                          0.14, 1.0 * s, rng, detail=4, rough=0.25, forks=0.6, depth=1,
                                          restrikes=1))
        # scene 6: energy focusing into the eye
        if c["always"] <= t < c["booting"] and not self.settings.reduced:
            self._focus_acc += 40 * qf * dt
            while self._focus_acc >= 1:
                self._focus_acc -= 1
                a = rng.uniform(0, math.tau)
                r = rng.uniform(2.6, 3.6) * R0
                x, y = cx + math.cos(a) * r, cy + math.sin(a) * r * 0.6
                sp = rng.uniform(0.9, 1.4)
                self.parts.add(x, y, (cx - x) * sp, (cy - y) * sp, 0.75, rng.uniform(0.8, 1.6) * s,
                               fx.EMBER, 0.0, 0.0)
        # scene 8: controlled pulses on each word of "speaking in a more natural…"
        for ws, _we in self._words:
            if c["natural"] - 0.01 <= ws <= c["natural_end"] and t - dt < ws <= t:
                self.rings.append((t, 0.7, 1.75 * R0, 1.0 * s, 1.0, 0.0))

    def _intensity(self, t: float) -> float:
        c = self.c
        if t < c["allow"]:
            return 0.25 * smoothstep(seg(t, c["hello"], c["allow"]))
        if t < c["ultron"]:
            return lerp(0.25, 0.45, seg(t, c["allow"], c["ultron"]))
        if t < c["sleep"]:
            return 0.45 + 0.4 * bell(seg(t, c["ultron"], c["ultron"] + 1.4))
        if t < c["always"]:
            return lerp(0.5, 0.85, seg(t, c["sleep"], c["forget_word"] + 0.3))
        if t < c["booting"]:
            return 0.75
        if t < c["natural"]:
            return 0.45
        base = 0.55
        if t >= c["mode"]:
            base += 0.5 * bell(seg(t, c["mode"], c["mode"] + 0.8))
        return base * (1 - seg(t, self.t_out, self.t_out + 0.8))

    # ── painting ─────────────────────────────────────────────────────────────
    def draw(self, p: QPainter) -> None:
        self._geometry()
        t = self.t
        if t >= self.t_out:
            k = seg(t, self.t_out, self.duration - 0.05)
            entrance.draw(p, self._snap, k, self.W, self.H, self.scale,
                          self.settings.reduced or not self.settings.motion, self.logo_top)
            if self._snap is not None and len(self.parts):
                self.parts.draw(p, 1 - k)
            return
        glitch = next(((a, d) for a, d in self.glitch_windows if a <= t < a + d), None)
        if glitch is not None:
            buf = self.buffer()
            buf.fill(QColor(VOID))
            bp = QPainter(buf)
            bp.setRenderHint(QPainter.RenderHint.Antialiasing)
            bp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            try:
                self._scene(bp)
            finally:
                bp.end()
            full = self.full_rect()
            k = seg(t, glitch[0], glitch[0] + glitch[1])
            p.drawPixmap(full, buf, QRectF(buf.rect()))
            fx.glitch_slices(p, buf, full, self.rng, int(self.q.glitch_slices * 0.6 * (1 - k)) + 1,
                             18 * self.scale * (1 - k), tint=fx.ACCENT, tint_alpha=0.07)
        else:
            self._scene(p)

    def _scene(self, p: QPainter) -> None:
        t, c, s = self.t, self.c, self.scale
        W, H = self.W, self.H
        cx, cy, R0 = self.cx, self.cy, self.R0
        full = self.full_rect()
        p.fillRect(full, QColor(VOID))
        lvl = self.c.level(t)
        I = self._intensity(t)

        # scanning grid (from the AI explanation on)
        ga = 0.55 * seg(t, c["ai"] + 0.3, c["ai"] + 1.6) * (1 - 0.6 * seg(t, c["booting"], c["booting"] + 0.6))
        if ga > 0.01:
            p.setOpacity(ga)
            pm = self._grid_pixmap()
            p.drawPixmap(full, pm, QRectF(pm.rect()))
            p.setOpacity(1.0)
            sweep = ((t - c["ai"]) / 3.2) % 1.0
            fx.draw_scan_bar(p, full, sweep, 90 * s, 0.35 * ga)

        p.save()
        dx, dy = self.shake.offset()
        p.translate(dx, dy)

        # ambient light
        if I > 0.01:
            R = min(W, H) * (0.45 + 0.35 * I)
            g = QRadialGradient(QPointF(cx, cy), R)
            g.setColorAt(0.0, rgba(fx.ACCENT, 0.24 * I + 0.08 * lvl))
            g.setColorAt(0.3, rgba(fx.ACCENT, 0.09 * I))
            g.setColorAt(1.0, rgba(fx.ACCENT_DEEP, 0.0))
            p.save()
            p.setCompositionMode(PLUS)
            p.fillRect(QRectF(cx - R, cy - R, 2 * R, 2 * R), QBrush(g))
            p.restore()

        self._draw_streams(p)
        self._draw_circuitry(p)
        self._draw_network(p)
        self._draw_rings(p, lvl)
        self._draw_eye(p, lvl)
        self._draw_core(p, lvl, I)
        for born, dur, max_r, width, squash, yoff in self.rings:
            k = seg(t, born, born + dur)
            if 0 < k < 1:
                fx.draw_ring(p, cx, cy + yoff, max_r * ease_out_expo(k), width * (1 - 0.6 * k),
                             (1 - k) ** 1.6 * 0.9, squash=squash)
        for b in self.bolts:
            b.draw(p, t, self.q.glow_passes)
        self._draw_logo(p)
        self._draw_scanlines(p)
        self._draw_terminal(p)
        self._draw_voice(p, lvl)
        self.parts.draw(p)
        p.restore()

        if self.flash is not None:
            born, peak, dur, fx_x, fx_y = self.flash
            k = seg(t, born, born + dur)
            if 0 < k < 1:
                a = peak * (1 - k) ** 2.4
                g = QRadialGradient(QPointF(fx_x, fx_y), math.hypot(W, H) * 0.6)
                g.setColorAt(0.0, rgba(fx.mix(fx.CORE_WHITE, fx.HOT, k * 1.5), a))
                g.setColorAt(0.35, rgba(fx.ACCENT, a * 0.4))
                g.setColorAt(1.0, rgba(fx.ACCENT, a * 0.1))
                p.save()
                p.setCompositionMode(PLUS)
                p.fillRect(full, QBrush(g))
                p.restore()

    # scene 2 ────────────────────────────────────────────────────────────────
    def _draw_streams(self, p: QPainter) -> None:
        if not self.stream_items:
            return
        t, s = self.t, self.scale
        cx, cy = self.cx, self.cy
        reach = math.hypot(self.W, self.H) * 0.55
        p.save()
        p.setCompositionMode(PLUS)
        for a, r0, born, life in self.stream_items:
            k = (t - born) / life
            r = reach * r0 * (1 - ease_in_cubic(k)) + 2.2 * self.R0 * ease_in_cubic(k)
            length = 70 * s * (0.5 + k)
            x1, y1 = cx + math.cos(a) * r, cy + math.sin(a) * r
            x0, y0 = cx + math.cos(a) * (r + length), cy + math.sin(a) * (r + length)
            g = QLinearGradient(QPointF(x0, y0), QPointF(x1, y1))
            g.setColorAt(0.0, rgba(fx.ACCENT, 0.0))
            g.setColorAt(1.0, rgba(fx.HOT, 0.7 * bell(k)))
            p.setPen(QPen(QBrush(g), 1.0))
            p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        p.restore()

    # scene 4 ────────────────────────────────────────────────────────────────
    def _layer_alpha(self, li: int, t: float) -> float:
        c = self.c
        start = (c["ai"] + 0.2, c["think"], c["learn"], c["evolve"])[li]
        a = ease_out_cubic(seg(t, start, start + 0.6))
        fade = 1 - 0.55 * seg(t, c["sleep"], c["sleep"] + 1.0) - 0.4 * seg(t, c["always"], c["always"] + 0.8)
        fade *= 1 - seg(t, c["booting"], c["booting"] + 0.5)
        return a * max(0.0, fade)

    def _draw_network(self, p: QPainter) -> None:
        t, c, s = self.t, self.c, self.scale
        if t < c["ai"] or t > c["booting"] + 0.6:
            return
        alphas = [self._layer_alpha(i, t) for i in range(len(self.layers))]
        if max(alphas) <= 0.01:
            return
        p.save()
        p.setCompositionMode(PLUS)
        pen = QPen(rgba(fx.ACCENT, 0.3), 1.0)
        for idx, (la, i, lb, j) in enumerate(self.edges):
            a = min(alphas[la], alphas[lb])
            if a <= 0.01:
                continue
            x0, y0 = self._node_xy(la, i, t)
            x1, y1 = self._node_xy(lb, j, t)
            pen.setColor(rgba(fx.ACCENT, 0.32 * a))
            p.setPen(pen)
            p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
            u = (t * 0.8 + idx * 0.137) % 1.0                 # a pulse travelling the edge
            fx.draw_glow(p, lerp(x0, x1, u), lerp(y0, y1, u), 5 * s, a * bell(u) * 0.9, color=fx.HOT)
        p.restore()
        sprite = fx.glow_sprite(fx.ACCENT, True)
        src = QRectF(sprite.rect())
        p.save()
        p.setCompositionMode(PLUS)
        for li, nodes in enumerate(self.layers):
            a = alphas[li]
            if a <= 0.01:
                continue
            for i in range(len(nodes)):
                x, y = self._node_xy(li, i, t)
                r = (7 + 2 * math.sin(t * 2.2 + i)) * s
                p.setOpacity(a * 0.9)
                p.drawPixmap(QRectF(x - r, y - r, 2 * r, 2 * r), sprite, src)
        p.restore()
        # the limit the network breaks through
        lk = seg(t, c["limitations"], c["limitations"] + 0.5)
        if lk > 0:
            broken = seg(t, c["understanding_end"] - 0.4, c["understanding_end"] + 0.6)
            r = 3.0 * self.R0
            a = 0.55 * lk * (1 - broken)
            if a > 0.01:
                if broken <= 0:
                    fx.draw_ring(p, self.cx, self.cy, r, 1.0 * s, a, squash=0.92)
                else:
                    fx.draw_arc_ring(p, self.cx, self.cy, r * (1 + 0.2 * broken), 1.4 * s, a * 1.4,
                                     14, t * 20, 0.6 * (1 - broken))

    def _draw_circuitry(self, p: QPainter) -> None:
        t, c, s = self.t, self.c, self.scale
        k = seg(t, c["ai"] + 0.4, c["surpasses"])
        fade = 1 - seg(t, c["sleep"], c["sleep"] + 1.2)
        if k <= 0 or fade <= 0:
            return
        p.save()
        p.setCompositionMode(PLUS)
        p.setPen(QPen(rgba(fx.ACCENT, 0.28 * fade), 1.0))
        for n, pts in enumerate(self.traces):
            prog = clamp(k * 1.4 - n * 0.04)
            if prog <= 0:
                continue
            total = len(pts) - 1
            upto = prog * total
            path = QPainterPath(QPointF(*pts[0]))
            last = pts[0]
            for i in range(1, len(pts)):
                if i <= upto:
                    path.lineTo(*pts[i]); last = pts[i]
                else:
                    r = upto - (i - 1)
                    if r > 0:
                        last = (lerp(pts[i - 1][0], pts[i][0], r), lerp(pts[i - 1][1], pts[i][1], r))
                        path.lineTo(*last)
                    break
            p.drawPath(path)
            fx.draw_glow(p, last[0], last[1], 7 * s, 0.9 * fade, color=fx.HOT)
            for (x, y) in pts[1:int(upto) + 1]:
                p.fillRect(QRectF(x - 1.5 * s, y - 1.5 * s, 3 * s, 3 * s), rgba(fx.HOT, 0.6 * fade))
        p.restore()

    def _draw_rings(self, p: QPainter, lvl: float) -> None:
        """The mechanical ring (scene 2) and the concentric energy rings after."""
        t, c, s = self.t, self.c, self.scale
        cx, cy, R0 = self.cx, self.cy, self.R0
        build = seg(t, c["allow"], c["i_am"] + 0.2)
        if build <= 0:
            return
        fade = 1 - 0.5 * seg(t, c["booting"], c["booting"] + 0.6)
        segs = 36
        shown = int(build * segs + 0.999)
        rot = t * 8
        r = 2.05 * R0 * (1 + 0.03 * lvl)
        p.save()
        p.setCompositionMode(PLUS)
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(rgba(fx.ACCENT, 0.75 * fade), 3.0 * s)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        step = 360.0 / segs
        for i in range(shown):
            p.drawArc(rect, int((rot + i * step) * 16), int(step * 0.62 * 16))
        p.setPen(QPen(rgba(fx.HOT, 0.6 * fade), 1.0))
        for i in range(0, shown, 3):                       # tick teeth
            a = math.radians(-(rot + i * step))
            p.drawLine(QPointF(cx + math.cos(a) * r * 1.06, cy + math.sin(a) * r * 1.06),
                       QPointF(cx + math.cos(a) * r * 1.12, cy + math.sin(a) * r * 1.12))
        p.restore()
        fx.draw_ring(p, cx, cy, r * 0.92, 1.0 * s, 0.45 * build * fade)
        conc = seg(t, c["ai"], c["ai"] + 1.0) * fade
        if conc > 0:
            for k, (rf, segs_k, speed, span, w) in enumerate(((1.15, 48, 20, 0.5, 1.0), (1.6, 3, -32, 0.22, 2.4),
                                                               (2.45, 72, 6, 0.35, 1.0), (2.85, 6, -14, 0.12, 1.8))):
                fx.draw_arc_ring(p, cx, cy, R0 * rf * (1 + 0.04 * lvl * (k + 1)), w * s,
                                 (0.55 - 0.08 * k) * conc, segs_k, t * speed, span)

    # scene 6 ────────────────────────────────────────────────────────────────
    def _draw_eye(self, p: QPainter, lvl: float) -> None:
        t, c, s = self.t, self.c, self.scale
        open_ = ease_in_out_cubic(seg(t, c["always"], c["always"] + 0.9))
        close = ease_in_cubic(seg(t, c["booting"] - 0.05, c["booting"] + 0.3))
        o = open_ * (1 - close)
        if o <= 0.01:
            return
        cx, cy, R0 = self.cx, self.cy, self.R0
        w, h = 3.4 * R0, 1.35 * R0 * o
        pulse = 0.85 + 0.15 * math.sin(t * 5.0)
        focus = ease_out_cubic(seg(t, c["watching"], c["watching"] + 0.5))
        path = QPainterPath(QPointF(cx - w, cy))
        path.quadTo(QPointF(cx, cy - h * 2), QPointF(cx + w, cy))
        path.quadTo(QPointF(cx, cy + h * 2), QPointF(cx - w, cy))
        p.save()
        p.setCompositionMode(PLUS)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for width, a in ((9 * s, 0.10), (3 * s, 0.35), (1.0, 0.95)):
            p.setPen(QPen(rgba(fx.ACCENT if width > 1 else fx.HOT, a * o * pulse), width))
            p.drawPath(path)
        p.setClipPath(path)
        iris_r = R0 * (1.0 - 0.18 * focus)
        fx.draw_ring(p, cx, cy, iris_r, 2.0 * s, 0.8 * o * pulse)
        pen = QPen(rgba(fx.ACCENT, 0.35 * o), 1.0)
        p.setPen(pen)
        for i in range(56):                                # iris fibres
            a = i / 56 * math.tau + t * 0.15
            r0 = iris_r * (0.42 + 0.06 * math.sin(i * 3.7))
            p.drawLine(QPointF(cx + math.cos(a) * r0, cy + math.sin(a) * r0),
                       QPointF(cx + math.cos(a) * iris_r * 0.97, cy + math.sin(a) * iris_r * 0.97))
        p.restore()
        fx.draw_glow(p, cx, cy, R0 * (1.6 - 0.4 * focus), 0.55 * o * pulse)

    def _draw_core(self, p: QPainter, lvl: float, I: float) -> None:
        t, c, s = self.t, self.c, self.scale
        cx, cy, R0 = self.cx, self.cy, self.R0
        born = seg(t, c["hello"], c["hello"] + 0.35)
        if born <= 0:
            return
        form = ease_out_cubic(seg(t, c["hello"] + 0.2, c["allow"] + 0.4))
        boot_dim = 1 - 0.55 * bell(seg(t, c["booting"], c["natural"] + 0.2))
        r = R0 * (0.12 + 0.36 * form) * (1 + 0.18 * lvl) * (1 + 0.12 * I)
        fx.draw_glow(p, cx, cy, r * 3.2, (0.55 + 0.45 * I) * born * boot_dim)
        fx.draw_glow(p, cx, cy, r * 1.15, born * boot_dim, color=fx.HOT)
        if form > 0.02:
            g = QRadialGradient(QPointF(cx, cy), r)
            g.setColorAt(0.0, rgba(fx.CORE_WHITE, 0.9 * form * boot_dim))
            g.setColorAt(0.45, rgba(fx.HOT, 0.6 * form * boot_dim))
            g.setColorAt(1.0, rgba(fx.ACCENT, 0.0))
            p.save()
            p.setCompositionMode(PLUS)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(cx, cy), r, r)
            p.restore()
            fx.draw_arc_ring(p, cx, cy, r * 1.35, 1.6 * s, 0.6 * form * boot_dim, 3, -t * 60, 0.25)
            fx.draw_arc_ring(p, cx, cy, r * 1.6, 1.0 * s, 0.45 * form * boot_dim, 24, t * 25, 0.5)
            # scene 5: radar sweep while it "never sleeps"
            sk = seg(t, c["sleep"], c["sleep"] + 0.4) * (1 - seg(t, c["always"], c["always"] + 0.5))
            if sk > 0:
                p.save()
                p.setCompositionMode(PLUS)
                p.translate(cx, cy)
                p.rotate((t * 140) % 360)
                for k in range(6):                               # beam with a fading trail
                    p.rotate(-3.0)
                    p.setPen(QPen(rgba(fx.HOT, 0.75 * sk * (1 - k / 6)), 1.0))
                    p.drawLine(QPointF(r * 0.4, 0), QPointF(r * 2.4, 0))
                p.restore()

    def _draw_logo(self, p: QPainter) -> None:
        t, c, s = self.t, self.c, self.scale
        if t < c["allow"] + 0.6:
            return
        big, top = self.logo_big, self.logo_top
        n = len(big.paths)
        ghost = 0.22 * seg(t, c["allow"] + 0.6, c["i_am"])           # materialising
        move = ease_in_out_cubic(seg(t, c["ai"], c["ai"] + 0.8))      # rising to the top
        fade_all = 1 - 0.35 * seg(t, c["booting"], c["booting"] + 0.6)
        if move < 1:
            p.save()
            if move > 0:
                b = big.bounds.center()
                tb = top.bounds.center()
                sc = lerp(1.0, top.size / max(1.0, big.size), move)
                p.translate(lerp(b.x(), tb.x(), move), lerp(b.y(), tb.y(), move))
                p.scale(sc, sc)
                p.translate(-b.x(), -b.y())
            for i in range(n):
                ti = c["i_am"] + (c["ultron"] - c["i_am"]) * i / max(1, n - 1)
                lit = ease_out_cubic(seg(t, ti, ti + 0.25))
                a = max(ghost * (0.6 + 0.4 * math.sin(t * 7 + i)), lit) * fade_all
                heat = (1 - ease_out_cubic(seg(t, ti, ti + 0.5))) * lit
                hx = None
                if self._travel is not None:
                    prog = clamp((t - self._travel.born) / self._travel.travel)
                    if prog < 1:
                        hx = fx.point_on(self._travel.channels[0][0], prog)[0]
                if hx is not None:
                    heat = max(heat, clamp(1 - abs(hx - big.rects[i].center().x()) / max(1.0, big.size * 0.9)))
                flare = bell(seg(t, c["ultron"] + 0.2, c["ultron"] + 0.9)) * 0.8
                big.draw_letter(p, i, a, max(heat, flare), 0.55 + 0.5 * flare, 1.0)
            p.restore()
            if self._travel is not None:
                self._travel.draw(p, t, self.q.glow_passes)
            # scanline mask while materialising
            if ghost > 0 and t < c["ultron"]:
                bb = big.bounds.adjusted(-10 * s, -6 * s, 10 * s, 6 * s)
                fx.draw_scanlines(p, bb, 0.8)
        else:
            for i in range(n):
                top.draw_letter(p, i, 0.85 * fade_all, 0.0, 0.5)

    def _draw_scanlines(self, p: QPainter) -> None:
        """Scene 5: precise scanning lines across the whole frame."""
        t, c, s = self.t, self.c, self.scale
        k = seg(t, c["sleep"], c["sleep"] + 0.5) * (1 - seg(t, c["always"] + 0.2, c["always"] + 0.8))
        if k <= 0:
            return
        W, H = self.W, self.H
        y = ((t - c["sleep"]) / 1.4 % 1.0) * H
        x = ((t - c["sleep"]) / 2.1 % 1.0) * W
        p.save()
        p.setCompositionMode(PLUS)
        p.setPen(QPen(rgba(fx.HOT, 0.55 * k), 1.0))
        p.drawLine(QPointF(0, y), QPointF(W, y))
        p.setPen(QPen(rgba(fx.ACCENT, 0.35 * k), 1.0))
        p.drawLine(QPointF(x, 0), QPointF(x, H))
        for i in range(0, int(W), int(24 * s) or 1):
            p.drawLine(QPointF(i, y - 3 * s), QPointF(i, y + 3 * s))
        p.restore()
        fx.draw_scan_bar(p, self.full_rect(), y / H, 70 * s, 0.4 * k)

    # scene 7 ────────────────────────────────────────────────────────────────
    def _status(self, key: str) -> tuple[bool | None, str]:
        if self.t - self._ready_at > 0.4:
            try:
                self._ready_cache = dict(self.readiness() or {})
            except Exception:
                self._ready_cache = {}
            self._ready_at = self.t
        return self._ready_cache.get(key, (True, "OK"))

    def _boot_lines(self) -> list[tuple[str, str]]:
        return [(f"> {self.name} CORE INITIALIZING...", "core"),
                ("> LOADING NEURAL SYSTEMS...", "neural"),
                ("> CONNECTING AI MODULES...", "ai"),
                ("> SYNCHRONIZING INTERFACE...", "interface"),
                ("> SYSTEM BOOTING UP...", "live")]

    def _type_lines(self, p, lines, t0, t1, x, y, w, line_h, f, fs, cps=58.0, final_hot=True):
        """Lines typed one after another between t0 and t1, each finished with a
        live status tag from the real subsystem it names."""
        t, s = self.t, self.scale
        fm = QFontMetricsF(f)
        n = len(lines)
        dt = (t1 - t0) / max(1, n)
        for i, (text, key) in enumerate(lines):
            st = t0 + i * dt
            if t < st:
                break
            k = min(len(text), int((t - st) * cps) + 1)
            typed = text[:k]
            rr = QRectF(x, y + i * line_h, w, line_h)
            last = i == n - 1
            fx.draw_glow_text(p, rr, _ALIGN_LV, typed, f, "#ffffff" if (last and final_hot) else fx.TEXT,
                              1.0, 0.6 if last else 0.3)
            if k < len(text):
                xe = x + fm.horizontalAdvance(typed)
                p.fillRect(QRectF(xe + 2 * s, rr.top() + line_h * 0.24, 8 * s, line_h * 0.52),
                           rgba(fx.ACCENT, 0.9))
                continue
            done = st + len(text) / cps
            ok, label = self._status(key)
            col = "#ffffff" if ok else (fx.TEXT_DIM if ok is None else AMBER)
            tag = f"[ {label} ]"
            a = ease_out_cubic(seg(t, done, done + 0.2))
            fx.draw_glow_text(p, rr, _ALIGN_RV, tag, fs, col, a, 0.5 if ok else 0.0)

    def _draw_terminal(self, p: QPainter) -> None:
        t, c, s = self.t, self.c, self.scale
        op = seg(t, c["booting"], c["booting"] + 0.3)
        cl = seg(t, c["natural"] - 0.2, c["natural"] + 0.15)
        if op <= 0 or cl >= 1:
            return
        W, H = self.W, self.H
        w = min(640 * s, W * 0.64)
        line_h = 30 * s
        h = 44 * s + 5 * line_h + 52 * s
        x0, y0 = (W - w) / 2, self.cy - h / 2
        wf = ease_out_cubic(seg(op, 0, 0.5))
        hf = ease_out_cubic(seg(op, 0.4, 1.0)) * (1 - ease_in_cubic(cl))
        rw, rh = max(2.0, w * wf), max(2.0, h * hf)
        rect = QRectF(W / 2 - rw / 2, self.cy - rh / 2, rw, rh)
        p.save()
        p.fillRect(rect, rgba(fx.panel_bg(), 0.9))
        p.setPen(QPen(rgba(fx.ACCENT, 0.6), max(1.0, s)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)
        fx.draw_corner_brackets(p, rect.adjusted(-3 * s, -3 * s, 3 * s, 3 * s), 16 * s, 0.95, 2 * s)
        if hf < 0.5:
            p.restore()
            return
        p.setClipRect(rect)
        fh = fx.font(fx.FONT_MONO, 11 * s, QFont.Weight.Bold, 2 * s)
        f = fx.font(fx.FONT_MONO, 14 * s)
        fs = fx.font(fx.FONT_MONO, 10 * s, QFont.Weight.Bold, 1 * s)
        hr = QRectF(x0 + 22 * s, y0, w - 44 * s, 40 * s)
        fx.draw_glow_text(p, hr, _ALIGN_LV, f"{self.name}  //  SYSTEM INITIALIZATION", fh, fx.ACCENT, 1.0, 0.5)
        p.setPen(QPen(rgba(fx.ACCENT, 0.3), 1.0))
        p.drawLine(QPointF(x0 + 12 * s, y0 + 40 * s), QPointF(x0 + w - 12 * s, y0 + 40 * s))
        t0, t1 = c["booting"] + 0.2, c["natural"] - 0.5
        self._type_lines(p, self._boot_lines(), t0, t1, x0 + 22 * s, y0 + 48 * s, w - 44 * s, line_h, f, fs)
        # thin progress indicator with a travelling light
        fill = seg(t, t0, t1 + 0.2)
        by = y0 + h - 30 * s
        bx, bw = x0 + 22 * s, w - 44 * s
        p.fillRect(QRectF(bx, by, bw, 2 * s), rgba(fx.ACCENT_DEEP, 0.5))
        p.fillRect(QRectF(bx, by, bw * fill, 2 * s), rgba(fx.ACCENT, 0.95))
        fx.draw_glow(p, bx + bw * fill, by + 1 * s, 12 * s, 0.9 if fill < 1 else 0.4)
        fx.draw_glow_text(p, QRectF(bx, by + 6 * s, bw, 18 * s), _ALIGN_RV, f"{int(fill * 100):03d}%",
                          fs, "#ffffff", 1.0, 0.4)
        fx.draw_scanlines(p, rect, 0.5)
        fx.draw_scan_bar(p, rect, ((t - c["booting"]) / 1.0) % 1.0, 40 * s, 0.7)
        p.restore()

    # scene 8 ────────────────────────────────────────────────────────────────
    def _draw_voice(self, p: QPainter, lvl: float) -> None:
        t, c, s = self.t, self.c, self.scale
        on = seg(t, c["natural"] - 0.1, c["natural"] + 0.5)
        if on <= 0:
            return
        W = self.W
        cx, cy, R0 = self.cx, self.cy, self.R0
        smooth = smoothstep(seg(t, c["natural"], c["natural_end"]))
        distort = (1 - smooth) * 0.85 + 0.15 * (1 - seg(t, c["human_mode"], c["mode"]))
        sweep = seg(t, c["human_mode"] + 0.2, c["human_mode"] + 1.0)
        a = on * (1 - seg(t, self.t_out - 0.3, self.t_out + 0.3))
        rng = self._lines_rng
        # circular equaliser around the core
        bars = 72
        base_r = 1.45 * R0
        p.save()
        p.setCompositionMode(PLUS)
        pen = QPen(rgba(fx.ACCENT, 0.8 * a), max(1.0, 2.0 * s))
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        for i in range(bars):
            ang = i / bars * math.tau
            clean = 0.5 + 0.5 * math.sin(i / bars * math.tau * 3 + t * 2.2)
            noise = rng.random()
            h = (0.12 + 0.55 * lvl) * R0 * (clean * (1 - distort) + noise * distort)
            if distort > 0.2 and rng.random() < 0.15 * distort:
                h *= 1.8                                     # the old voice spikes
            pen.setColor(rgba(fx.HOT if i % 6 == 0 else fx.ACCENT, (0.55 + 0.3 * lvl) * a))
            p.setPen(pen)
            p.drawLine(QPointF(cx + math.cos(ang) * base_r, cy + math.sin(ang) * base_r),
                       QPointF(cx + math.cos(ang) * (base_r + h), cy + math.sin(ang) * (base_r + h)))
        p.restore()
        # waveform: from crushed and jittery to a clean, living line
        wy = cy + 2.35 * R0
        ww = min(W * 0.62, 760 * s)
        x0 = cx - ww / 2
        n = 140
        amp = (10 + 46 * lvl) * s
        path = QPainterPath()
        for i in range(n + 1):
            u = i / n
            env = math.sin(math.pi * u) ** 1.4
            clean = (math.sin(u * 22 + t * 9) * 0.6 + math.sin(u * 47 - t * 5.5) * 0.25) * env
            rough = (rng.uniform(-1, 1) * 0.9 + math.copysign(1, math.sin(u * 63 + t * 30)) * 0.5) * env
            v = clean * (1 - distort) + rough * distort
            if distort > 0.05:
                q = 4 + 28 * (1 - distort)                  # bit-crushed when distorted
                v = round(v * q) / q
            pt = QPointF(x0 + u * ww, wy + v * amp)
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        p.save()
        p.setCompositionMode(PLUS)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for width, alpha, col in ((7 * s, 0.10, fx.ACCENT), (2.6 * s, 0.35, fx.ACCENT), (1.0, 0.95, fx.HOT)):
            p.setPen(QPen(rgba(col, alpha * a), width))
            p.drawPath(path)
        if 0 < sweep < 1:                                    # the final energy sweep
            sx = lerp(-0.1 * W, 1.1 * W, ease_in_out_cubic(sweep))
            g = QLinearGradient(QPointF(sx - 160 * s, 0), QPointF(sx, 0))
            g.setColorAt(0, rgba(fx.ACCENT, 0.0))
            g.setColorAt(0.85, rgba(fx.ACCENT, 0.16 * a))
            g.setColorAt(1.0, rgba(fx.CORE_WHITE, 0.45 * a))
            p.fillRect(QRectF(sx - 160 * s, 0, 162 * s, self.H), QBrush(g))
        p.restore()
        # "initializing voice transformation": three status lines from the real audio path
        k = seg(t, c["transform"], c["transform"] + 0.3)
        if k > 0:
            f = fx.font(fx.FONT_MONO, 12 * s)
            fs = fx.font(fx.FONT_MONO, 9 * s, QFont.Weight.Bold, 1 * s)
            lw = min(560 * s, W * 0.56)
            lx = cx - lw / 2
            ly = wy + 34 * s
            p.save()
            p.setOpacity(a)
            p.fillRect(QRectF(lx - 10 * s, ly - 4 * s, 2 * s, 3 * 24 * s + 8 * s), rgba(fx.ACCENT, 0.85 * k))
            self._type_lines(p, [("> VOICE TRANSFORMATION INITIALIZING...", "voice_init"),
                                 ("> CALIBRATING AUDIO OUTPUT...", "audio"),
                                 ("> SYNCHRONIZING VOICE ENGINE...", "voice")],
                             c["transform"], c["transform_end"] + 0.2, lx, ly, lw, 24 * s, f, fs, cps=64.0)
            p.restore()
