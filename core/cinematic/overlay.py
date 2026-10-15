"""
core/cinematic/overlay.py — the stage every cinematic plays on.

A CinematicOverlay is a child widget laid over the whole main window. It owns
one timer and one timeline clock:

  * time is measured, not counted — each tick advances the timeline by the
    real elapsed time × the speed setting (capped, so a stall never makes a
    sequence jump), which keeps timing identical at 30 or 144 frames a second;
  * cues are callbacks pinned to timeline times (play a sound, spawn a bolt),
    each fired exactly once even if a slow frame skips past several;
  * if frames keep running slow it lowers its own quality profile instead of
    stuttering;
  * if drawing ever raises, it logs once and finishes immediately — an
    animation is never allowed to stop ULTRON from starting or shutting down.

The overlay is opaque (it paints every pixel itself), so Qt never has to paint
the busy dashboard underneath while it runs.
"""
from __future__ import annotations

import random
import time
import traceback

from PyQt6.QtCore import QElapsedTimer, QEvent, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import QWidget

from .settings import QUALITY, AnimationSettings, lower_quality


class CinematicOverlay(QWidget):
    finished = pyqtSignal()
    phase_changed = pyqtSignal(str)

    DURATION = 1.0
    SKIPPABLE = False

    def __init__(self, host: QWidget, settings: AnimationSettings, sfx=None):
        super().__init__(host)
        self.host = host
        self.settings = settings
        self.q = settings.profile()
        self.sfx = sfx
        self.rng = random.Random()
        self.t = 0.0
        self.duration = self.DURATION
        self._done = False
        self._failed = False
        self._cues: list[tuple[float, int, object]] = []
        self._cue_seq = 0
        self._phase = ""
        self._slow_frames = 0
        self._frames = 0
        self._paint_ms = 0.0
        self._buf: QPixmap | None = None

        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._clock = QElapsedTimer()
        self._last_ms = 0
        self.hide()

    # ── geometry ─────────────────────────────────────────────────────────────
    def eventFilter(self, obj, ev):
        if obj is self.host and ev.type() == QEvent.Type.Resize:
            self.setGeometry(self.host.rect())
            self._buf = None
            try:
                self.on_resize()
            except Exception:
                traceback.print_exc()
        return False

    def on_resize(self) -> None:
        """Subclasses re-lay-out size-dependent art here."""

    @property
    def W(self) -> float:
        return float(self.width())

    @property
    def H(self) -> float:
        return float(self.height())

    @property
    def scale(self) -> float:
        """1.0 at the default 1440×880 window; effects size themselves by it."""
        return max(0.45, min(self.width(), self.height() * 1.6) / 1408.0)

    # ── timeline ─────────────────────────────────────────────────────────────
    def cue(self, at: float, fn) -> None:
        self._cue_seq += 1
        self._cues.append((at, self._cue_seq, fn))
        self._cues.sort(key=lambda c: (c[0], c[1]))

    def sound(self, name: str, gain: float = 1.0) -> None:
        if self.sfx is not None and self.settings.sound:
            self.sfx.play(name, gain)

    def set_phase(self, name: str) -> None:
        if name != self._phase:
            self._phase = name
            self.phase_changed.emit(name)

    @property
    def phase(self) -> str:
        return self._phase

    def start(self) -> None:
        self.host.installEventFilter(self)
        self.setGeometry(self.host.rect())
        try:
            self.on_resize()
            self.prepare()
        except Exception:
            self._fail("prepare")
            return
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._clock.start()
        self._last_ms = 0
        self._timer.start(self.q.frame_ms)
        self._tick()

    def prepare(self) -> None:
        """One-time setup before the first frame (snapshots, layouts)."""

    def advance(self, dt: float) -> None:
        """Move the simulation on by dt timeline-seconds."""

    def draw(self, p: QPainter) -> None:
        raise NotImplementedError

    def timeline(self, dt: float) -> float:
        """Where the timeline is now, given `dt` real seconds since the last
        tick. The default is the frame clock × the speed setting; a sequence
        that follows audio returns the audio position instead."""
        return self.t + dt * self.settings.speed_factor

    def _tick(self) -> None:
        if self._done:
            return
        ms = self._clock.elapsed()
        real = min(0.08, max(0.0, (ms - self._last_ms) / 1000.0))
        self._last_ms = ms
        new_t = max(self.t, self.timeline(real))      # never runs backwards
        dt, self.t = new_t - self.t, new_t
        while self._cues and self._cues[0][0] <= self.t:
            _at, _seq, fn = self._cues.pop(0)
            try:
                fn()
            except Exception:
                traceback.print_exc()
        try:
            self.advance(dt)
        except Exception:
            self._fail("advance")
            return
        if self.t >= self.duration:
            self.finish()
            return
        self.update()

    def finish(self) -> None:
        if self._done:
            return
        self._done = True
        self._timer.stop()
        try:
            self.host.removeEventFilter(self)
        except Exception:
            pass
        self._buf = None
        self.finished.emit()

    @property
    def done(self) -> bool:
        return self._done

    def skip(self) -> None:
        self.finish()

    def _fail(self, where: str) -> None:
        if not self._failed:
            self._failed = True
            print(f"[Cinematic] {type(self).__name__} failed in {where} - finishing safely:")
            traceback.print_exc()
        self.finish()

    # ── painting ─────────────────────────────────────────────────────────────
    def buffer(self) -> QPixmap:
        """An off-screen frame the size of the overlay, for post-processing
        (glitch, distortion, RGB split) of the whole picture."""
        dpr = self.devicePixelRatioF() or 1.0
        w, h = max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr))
        if self._buf is None or self._buf.width() != w or self._buf.height() != h:
            self._buf = QPixmap(w, h)
            self._buf.setDevicePixelRatio(dpr)
        return self._buf

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        t0 = time.perf_counter()
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            self.draw(p)
        except Exception:
            p.end()
            self._fail("draw")
            return
        p.end()
        self._watch_frame((time.perf_counter() - t0) * 1000.0)

    def _watch_frame(self, ms: float) -> None:
        """Adaptive quality: a run of frames over budget steps the profile down."""
        self._frames += 1
        if self._frames < 6:            # first frames build caches; ignore them
            return
        budget = self.q.frame_ms * 1.25
        self._slow_frames = self._slow_frames + 1 if ms > budget else max(0, self._slow_frames - 1)
        if self._slow_frames >= 12 and self.q.name != "low":
            name = lower_quality(self.q.name)
            print(f"[Cinematic] frames at {ms:.1f} ms - lowering quality to {name}")
            self.q = QUALITY[name]
            self._timer.setInterval(self.q.frame_ms)
            self._slow_frames = 0

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape and self.SKIPPABLE:
            self.skip()
            return
        e.accept()          # swallow keys while the cinematic owns the screen

    def mousePressEvent(self, e) -> None:
        e.accept()

    def full_rect(self) -> QRectF:
        return QRectF(0, 0, self.W, self.H)
