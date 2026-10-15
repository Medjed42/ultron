"""
core/cinematic/core_fx.py — keeps the AI core alive between commands.

Layered on top of AICore's own rings (core/ui_command_center.py), driven by
the same state and audio level:

    LISTENING     crimson rings pulse out with the microphone level;
                  particles orbit the core with short trails.
    THINKING /    the core sways; data streams run along its radii; eight
    PROCESSING    lights pulse round the ring in sequence.
    RESPONDING    a ring that breathes with ULTRON's own voice level; electric
                  arcs crawl the rim, wilder the louder it speaks.
    IDLE          slow breathing, and every few seconds a tiny spark on the rim.
    ERROR         a short RGB glitch, a warning pulse, then a smooth recovery.

Also the power state: dormant while the boot cinematic plays, then a
spin-up when the dashboard is revealed — the core is the last thing to come on.

Coordinates are relative to the core's centre, so nothing here knows or cares
how big the widget is. Everything is cheap enough to run beside the dashboard:
no full-widget passes, a few dozen strokes per frame, and the owner drops to
30 fps whenever nothing here is moving.
"""
from __future__ import annotations

import math
import random
import time

from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QPainter, QPen, QPixmap

from . import fx
from .fx import PLUS, bell, clamp, ease_out_cubic, rgba, seg

ACTIVE_STATES = frozenset({"LISTENING", "THINKING", "PROCESSING", "RESPONDING"})
CALM_STATES = frozenset({"IDLE", "CONNECTED", "SLEEPING"})


class CoreFx:
    GLITCH_DUR = 0.42
    WARN_DUR = 0.9

    def __init__(self):
        self.enabled = True
        self.reduced = False
        self.glitch_enabled = True
        self.rng = random.Random()
        self.state = "IDLE"
        self._state_t = time.monotonic()
        self._power = 1.0
        self._power_t0: float | None = None
        self._power_dur = 1.4
        self.sparks = fx.Particles(70, self.rng)
        self.pulses: list[tuple[float, float]] = []
        self.arcs: list[fx.Bolt] = []
        rng = self.rng
        self.orbiters = [(rng.uniform(0.62, 1.0), rng.uniform(0.35, 1.1) * rng.choice((-1, 1)),
                          rng.uniform(0, math.tau), rng.uniform(1.3, 2.6)) for _ in range(16)]
        self._orbit_t = 0.0
        now = time.monotonic()
        self._next_spark = now + 3.0
        self._next_pulse = 0.0
        self._next_arc = 0.0
        self._glitch_until = 0.0
        self._warn_t = -10.0
        self._batches: list[tuple[float, float, list]] = []
        self._lvl = 0.0

    # ── configuration ────────────────────────────────────────────────────────
    def configure(self, motion: bool, glitch: bool, reduced: bool) -> None:
        self.enabled = bool(motion)
        self.glitch_enabled = bool(glitch)
        self.reduced = bool(reduced)
        if not self.enabled:
            self.pulses.clear(); self.arcs.clear(); self.sparks.clear()
            self._glitch_until = 0.0

    # ── power ────────────────────────────────────────────────────────────────
    def set_dormant(self) -> None:
        self._power, self._power_t0 = 0.0, None

    def power_on(self, now: float | None = None, dur: float = 1.4) -> None:
        if self._power >= 1.0 and self._power_t0 is None:
            return
        if not self.enabled:
            self._power, self._power_t0 = 1.0, None
            return
        self._power_t0 = now if now is not None else time.monotonic()
        self._power_dur = max(0.2, dur)

    def power(self, now: float) -> float:
        if self._power_t0 is None:
            return self._power
        k = (now - self._power_t0) / self._power_dur
        if k >= 1.0:
            self._power, self._power_t0 = 1.0, None
            return 1.0
        return max(self._power, ease_out_cubic(clamp(k)))

    def spin_boost(self, now: float) -> float:
        """Rings spin up fast while powering on, then settle to their pace."""
        if self._power_t0 is None:
            return 1.0 if self._power > 0 else 0.15
        return 1.0 + 3.5 * (1.0 - self.power(now))

    # ── events ───────────────────────────────────────────────────────────────
    def on_state(self, state: str, now: float) -> None:
        if state == self.state:
            return
        if state == "ERROR":
            self.glitch(now)
        self.state = state
        self._state_t = now

    def glitch(self, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        self._warn_t = now
        if self.enabled and self.glitch_enabled and not self.reduced:
            self._glitch_until = now + self.GLITCH_DUR

    def glitching(self, now: float) -> bool:
        return self.enabled and now < self._glitch_until

    def push_schedule(self, levels: list, hop: float, at: float) -> None:
        """Speech levels scheduled against the wall clock (from the viseme
        pipeline), so the core reacts to ULTRON's voice as it is heard."""
        if not levels:
            return
        batches = self._batches[-11:] + [(float(at), max(1e-3, float(hop)), list(levels))]
        self._batches = batches     # one rebind: safe to call from the audio thread

    def _speech_level(self) -> float:
        now = time.time()
        for at, hop, lv in reversed(self._batches):
            i = int((now - at) / hop)
            if 0 <= i < len(lv):
                return float(lv[i])
        return 0.0

    def busy(self, now: float) -> bool:
        """Whether the owner should paint at 60 fps rather than 30."""
        if not self.enabled:
            return self._power_t0 is not None
        return (self.state in ACTIVE_STATES or self._power_t0 is not None
                or now < self._warn_t + self.WARN_DUR or bool(self.arcs) or len(self.sparks) > 0)

    def intensity(self, now: float) -> float:
        p = 0.06 + 0.94 * self.power(now)
        if not self.enabled:
            return p
        breath = 1.0
        if self.state in ("IDLE", "CONNECTED"):
            breath = 0.86 + 0.14 * math.sin(now * 1.35)
        elif self.state == "SLEEPING":
            breath = 0.80 + 0.20 * math.sin(now * 0.8)
        dip = 1.0 - 0.35 * bell(seg(now, self._warn_t, self._warn_t + self.WARN_DUR))
        return p * breath * dip

    def sway(self, now: float) -> float:
        if not self.enabled or self.reduced or self.state not in ("THINKING", "PROCESSING"):
            return 0.0
        fade = seg(now, self._state_t, self._state_t + 0.6)
        return 3.5 * math.sin(now * 1.1) * fade

    # ── simulation ───────────────────────────────────────────────────────────
    def step(self, dt: float, now: float, level: float, R: float) -> None:
        if not self.enabled or R <= 4:
            return
        rng = self.rng
        lv = max(float(level or 0.0), self._speech_level())
        self._lvl += (lv - self._lvl) * min(1.0, dt * 14.0)
        self.sparks.step(dt)
        self.arcs = [a for a in self.arcs if a.alive(now)]
        self.pulses = [(b, s) for (b, s) in self.pulses if now - b < 1.1]
        self._orbit_t += dt * (1.0 + 2.2 * self._lvl) * (0.5 if self.reduced else 1.0)
        st = self.state
        if st == "LISTENING":
            last = self.pulses[-1][0] if self.pulses else 0.0
            if now >= self._next_pulse and (self._lvl > 0.10 or now - last > 1.3):
                self.pulses.append((now, clamp(0.35 + self._lvl * 1.6)))
                self._next_pulse = now + (0.16 if self._lvl > 0.10 else 1.3)
        elif st == "RESPONDING":
            if now >= self._next_arc:
                for _ in range(1 + int(self._lvl * 4)):
                    a0 = rng.uniform(0, math.tau)
                    span = rng.uniform(0.25, 0.7)
                    r = R * rng.uniform(0.6, 0.8)
                    self.arcs.append(fx.Bolt(
                        (math.cos(a0) * r, math.sin(a0) * r),
                        (math.cos(a0 + span) * r, math.sin(a0 + span) * r), now,
                        rng.uniform(0.06, 0.12), 0.8 + 0.9 * self._lvl, rng, detail=4,
                        rough=0.12 + 0.25 * self._lvl, forks=0.6, depth=1, restrikes=0))
                self._next_arc = now + rng.uniform(0.05, 0.09)
        if st in CALM_STATES and now >= self._next_spark and self.power(now) >= 1.0:
            a0 = rng.uniform(0, math.tau)
            r = R * 0.82
            self.arcs.append(fx.Bolt((math.cos(a0) * r, math.sin(a0) * r),
                                     (math.cos(a0 + 0.35) * r, math.sin(a0 + 0.35) * r), now,
                                     0.13, 1.0, rng, detail=4, rough=0.2, forks=1.0, depth=1,
                                     restrikes=1))
            self.sparks.burst(math.cos(a0) * r, math.sin(a0) * r, 4, speed=(20, 90),
                              life=(0.2, 0.5), gravity=120, size=(0.6, 1.2))
            self._next_spark = now + rng.uniform(2.5, 6.5)

    # ── painting ─────────────────────────────────────────────────────────────
    def paint(self, p: QPainter, c: QPointF, R: float, now: float) -> None:
        pw = self.power(now)
        if pw <= 0.01:
            return
        p.save()
        p.translate(c)
        if self._power_t0 is not None:
            k = clamp((now - self._power_t0) / self._power_dur)
            fx.draw_ring(p, 0, 0, R * (0.35 + 0.85 * ease_out_cubic(k)), 2.0, (1 - k) ** 1.5)
            fx.draw_glow(p, 0, 0, R * 0.6, (1 - k) * 0.8)
        if not self.enabled:
            p.restore()
            return
        lv = self._lvl
        st = self.state
        fade_in = seg(now, self._state_t, self._state_t + 0.45)

        for born, strength in self.pulses:
            k = (now - born) / 1.1
            if not 0.0 <= k < 1.0:      # expired since the last step (a negative base
                continue                # to a fractional power is a complex number)
            fx.draw_ring(p, 0, 0, R * (0.52 + 0.5 * ease_out_cubic(k)), 1.8,
                         (1 - k) ** 1.4 * strength * pw)

        if st == "LISTENING":
            self._paint_orbiters(p, R, lv, fade_in * pw)
        elif st in ("THINKING", "PROCESSING"):
            self._paint_processing(p, R, now, fade_in * pw)
        elif st == "RESPONDING":
            rr = R * (0.56 + 0.18 * lv + 0.02 * math.sin(now * 9))
            fx.draw_ring(p, 0, 0, rr, 1.2 + 2.4 * lv, (0.35 + 0.6 * lv) * fade_in * pw)
            fx.draw_ring(p, 0, 0, rr * 1.13, 1.0, 0.4 * lv * fade_in * pw)

        for a in self.arcs:
            a.draw(p, now, 2, 0.9 * pw)
        wk = seg(now, self._warn_t, self._warn_t + self.WARN_DUR)
        if 0 < wk < 1:
            fx.draw_ring(p, 0, 0, R * (0.5 + 0.65 * ease_out_cubic(wk)), 2.2, (1 - wk) * 0.9,
                         color="#ff2a2a")
        self.sparks.draw(p, pw)
        p.restore()

    def _paint_orbiters(self, p: QPainter, R: float, lv: float, alpha: float) -> None:
        if alpha <= 0.01:
            return
        sprite = fx.glow_sprite(fx.ACCENT, True)
        src = QRectF(sprite.rect())
        p.save()
        p.setCompositionMode(PLUS)
        for rf, sp, ph, sz in self.orbiters:
            r = R * rf * (0.92 + 0.12 * lv)
            for k in range(4):                       # head + three trail points
                a = ph + (self._orbit_t - k * 0.05) * sp
                x, y = math.cos(a) * r, math.sin(a) * r * 0.92
                rad = sz * (3.0 - k * 0.6) * (1 + 0.6 * lv)
                p.setOpacity(alpha * (0.85 if k == 0 else 0.35 / k))
                p.drawPixmap(QRectF(x - rad, y - rad, rad * 2, rad * 2), sprite, src)
        p.restore()

    def _paint_processing(self, p: QPainter, R: float, now: float, alpha: float) -> None:
        if alpha <= 0.01:
            return
        p.save()
        p.setCompositionMode(PLUS)
        pen = QPen(rgba(fx.ACCENT, 0.6 * alpha), 1.4)
        n = 12
        for i in range(n):
            ang = i / n * math.tau + now * 0.35
            u = (now * 0.9 + i * 0.37) % 1.0
            r0 = R * (0.58 + 0.36 * u)
            seg_len = R * 0.08
            pen.setColor(rgba(fx.HOT if i % 3 == 0 else fx.ACCENT, 0.65 * bell(u) * alpha))
            p.setPen(pen)
            p.drawLine(QPointF(math.cos(ang) * r0, math.sin(ang) * r0),
                       QPointF(math.cos(ang) * (r0 + seg_len), math.sin(ang) * (r0 + seg_len)))
        p.restore()
        fx.draw_arc_ring(p, 0, 0, R * 0.9, 1.2, 0.35 * alpha, 40, -now * 40, 0.3)
        for i in range(8):
            a = i / 8 * math.tau - math.pi / 2
            pulse = 0.5 + 0.5 * math.sin(now * 2.2 - i * 0.785)
            fx.draw_glow(p, math.cos(a) * R * 0.76, math.sin(a) * R * 0.76, 3 + 8 * pulse,
                         (0.35 + 0.65 * pulse) * alpha)

    def paint_glitch(self, p: QPainter, frame: QPixmap, w: float, h: float, now: float) -> None:
        """Draw `frame` (the core rendered off-screen) torn and colour-split."""
        k = clamp(1 - (self._glitch_until - now) / self.GLITCH_DUR)
        target = QRectF(0, 0, w, h)
        p.drawPixmap(target, frame, QRectF(frame.rect()))
        fx.rgb_split(p, fx.tinted(frame, "#ff0000"), fx.tinted(frame, "#00ffff"), target,
                     6 * (1 - k), 0.65 * (1 - k))
        fx.glitch_slices(p, frame, target, self.rng, 5, 14 * (1 - k), tint=fx.ACCENT,
                         tint_alpha=0.15)
