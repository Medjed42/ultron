"""
core/cinematic/boot.py — ULTRON waking up.

    1  AWAKENING      black; one red spark; electricity creeps out of it;
                      embers rise; small strikes land with a camera kick;
                      the red glow climbs.
    2  STRIKE         one massive bolt hits the centre: a single flash, a
                      shockwave with chromatic split, heat-shimmer distortion,
                      RGB glitch, a spark burst and an energy ring that stays.
    3  INITIALIZING   a terminal opens (line, then box), seven status lines
                      type out character by character with sparks at the
                      cursor, per-line progress, a segmented sync bar,
                      scanlines and the odd line tearing sideways.
    4  REVEAL         the terminal collapses like a CRT; U L T R O N lands one
                      letter at a time, each struck by its own bolt; a bolt
                      runs through the whole word, it blazes, a holographic
                      pulse rolls out and SYSTEM ONLINE tracks in.
       ENTRANCE       the wordmark flies into the header title while the real
                      dashboard assembles panel by panel; the AI core powers on
                      last (MainWindow does that when this finishes).

ESC or the SKIP button jumps straight to the entrance; a second ESC ends it.
Times below are seconds at speed 1.0 — the speed setting scales the clock.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPen,
    QPixmap, QRadialGradient,
)
from PyQt6.QtWidgets import QPushButton

from . import entrance, fx
from .fx import (
    PLUS, SPLIT_CYAN, SPLIT_RED, VOID, bell, clamp, ease_in_cubic, ease_in_out_cubic,
    ease_out_back, ease_out_cubic, ease_out_expo, lerp, rgba, seg,
)
from .overlay import CinematicOverlay

_ALIGN_LV = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
_ALIGN_RV = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
_ALIGN_C = Qt.AlignmentFlag.AlignCenter


class BootSequence(CinematicOverlay):
    SKIPPABLE = True

    T_SPARK = 0.20
    T_AMBIENT = (0.92, 1.27, 1.56)
    T_STRIKE = 1.85
    T_TERM = 2.75
    OPEN = 0.32
    LINE_DT = 0.36
    CPS = 105.0                 # typing speed, characters per second
    LETTER_DT = 0.15
    ENTRANCE = 0.95

    def __init__(self, host, settings, sfx=None, name: str = "ULTRON", dashboard=None):
        super().__init__(host, settings, sfx)
        self.name = (name or "ULTRON").upper()
        self.lines = [
            f"> INITIALIZING {self.name} CORE...",
            "> LOADING NEURAL NETWORKS...",
            "> CONNECTING AI SYSTEMS...",
            "> ACTIVATING VOICE INTERFACE...",
            "> ESTABLISHING SECURITY PROTOCOLS...",
            "> SYNCHRONIZING MODULES...",
            "> ALL SYSTEMS OPERATIONAL.",
        ]
        self.t_term_end = self.T_TERM + self.OPEN + len(self.lines) * self.LINE_DT + 0.22
        self.t_logo = self.t_term_end + 0.30
        self.t_travel = self.t_logo + (len(self.name) - 1) * self.LETTER_DT + 0.24
        self.t_bright = self.t_travel + 0.30
        self.t_online = self.t_bright + 0.16
        self.t_out = self.t_online + 0.85
        self.duration = self.t_out + self.ENTRANCE
        self.dashboard = dashboard      # () -> (pixmap, rect, pieces, title_rect)

        self.parts = fx.Particles(self.q.particles, self.rng)
        self.bolts: list[fx.Bolt] = []
        self.crawls: list[fx.Bolt] = []
        self.rings: list[tuple] = []          # (born, dur, max_r, width, split, squash)
        self.shake = fx.Shake(self.rng)
        self.flash: tuple | None = None       # (born, peak, dur)
        self.glitch_until = -1.0
        self.distort_until = -1.0
        self.travel: fx.Bolt | None = None
        self.logo = fx.LogoArt(self.name)
        self._ember_acc = 0.0
        self._tendril_acc = 0.0
        self._crawl_acc = 0.0
        self._typed = [0] * len(self.lines)
        self._jitter: dict[int, tuple[float, float]] = {}
        self._bg_key = None
        self._bg_plain: QPixmap | None = None
        self._bg_grid: QPixmap | None = None
        self._term_key = None
        self._term: dict = {}
        self._snap = None
        self._snap_failed = False
        self._skipped = False

        self._skip_btn = QPushButton("SKIP INTRO   ESC", self)
        self._skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skip_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._skip_btn.setProperty("_ultron_no_theme", True)   # styled by the cinematic itself
        self._skip_btn.setStyleSheet(
            "QPushButton { color: #f6eef0; background: rgba(255,22,60,16);"
            " border: 1px solid rgba(255,22,60,140); border-radius: 3px;"
            " padding: 6px 14px; font-family: Consolas; font-size: 9pt; letter-spacing: 2px; }"
            "QPushButton:hover { background: rgba(255,22,60,70); border-color: #ff163c; }")
        self._skip_btn.clicked.connect(self.skip)
        self._skip_btn.hide()

        self._light = settings.use_lightning
        self.cue(0.0, lambda: (self.set_phase("AWAKENING"), self.sound("01_core_wakeup", 0.9)))
        self.cue(0.55, self._show_skip)
        self.cue(self.T_SPARK, self._spark_born)
        for i, at in enumerate(self.T_AMBIENT):
            self.cue(at, lambda i=i: self._ambient_strike(0.55 + 0.25 * i))
        self.cue(self.T_STRIKE, self._main_strike)
        self.cue(self.T_TERM, lambda: self.set_phase("INITIALIZING"))
        for i in range(len(self.lines)):
            self.cue(self._line_start(i), lambda: self.sound("12_ui_confirm_tick", 0.55))
        self.cue(self.t_term_end, lambda: self.sound("07_system_scan", 0.7))
        for i in range(len(self.name)):
            self.cue(self.t_logo + i * self.LETTER_DT, lambda i=i: self._letter_strike(i))
        self.cue(self.t_travel, self._travel_strike)
        self.cue(self.t_bright, lambda: (self.set_phase("REVEAL"), self.sound("11_system_online_chime", 1.0),
                                         self._logo_pulse()))
        self.cue(self.t_online + 0.30, self._grab_dashboard)
        self.cue(self.t_out, self._begin_entrance)

    # ── timeline helpers ─────────────────────────────────────────────────────
    def _line_start(self, i: int) -> float:
        return self.T_TERM + self.OPEN + i * self.LINE_DT

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
        self._bg_key = None
        self._term_key = None
        if self._skip_btn.isVisible():
            self._place_skip()

    def skip(self) -> None:
        """First press: jump to the entrance (a clean landing, not a cut).
        Second press, or a press during the entrance: finish now."""
        if self.done:
            return
        if self.t >= self.t_out - 0.05 or self._skipped:
            self.finish()
            return
        self._skipped = True
        if self.sfx is not None:
            self.sfx.stop_all()
        self._cues = [c for c in self._cues if c[0] >= self.t_out]
        self.bolts.clear(); self.crawls.clear(); self.travel = None
        self.flash = None
        self.glitch_until = self.distort_until = -1.0
        self._grab_dashboard()
        self.t = self.t_out
        self._begin_entrance()

    def finish(self) -> None:
        self._skip_btn.hide()
        super().finish()

    # ── events ───────────────────────────────────────────────────────────────
    def _spark_born(self) -> None:
        s = self.scale
        self.parts.burst(self.W / 2, self.H / 2, 10, speed=(30 * s, 140 * s), life=(0.2, 0.5),
                         gravity=200 * s, size=(0.6 * s, 1.2 * s))

    def _edge_point(self) -> tuple[float, float]:
        W, H, rng = self.W, self.H, self.rng
        side = rng.choice(("top", "top", "left", "right"))
        if side == "top":
            return rng.uniform(0.1 * W, 0.9 * W), -10.0
        if side == "left":
            return -10.0, rng.uniform(0.05 * H, 0.55 * H)
        return W + 10.0, rng.uniform(0.05 * H, 0.55 * H)

    def _ambient_strike(self, power: float) -> None:
        W, H, s, rng = self.W, self.H, self.scale, self.rng
        mwh = min(W, H)
        tx = W / 2 + rng.uniform(-0.2, 0.2) * mwh
        ty = H / 2 + rng.uniform(-0.2, 0.16) * mwh
        if self._light:
            self.bolts.append(fx.Bolt(self._edge_point(), (tx, ty), self.t, 0.28,
                                      1.5 * s * power, rng, detail=self.q.bolt_detail,
                                      rough=0.19, forks=3.0 * power,
                                      depth=min(2, self.q.branch_depth), restrikes=2))
        else:
            self.rings.append((self.t, 0.7, 0.16 * mwh * power, 1.2 * s, 0.0, 1.0))
        if self.settings.use_shake:
            self.shake.kick(3.2 * s * power, 0.26)
        self.flash = (self.t, 0.07 * power * self.settings.flash_scale, 0.20)
        self.parts.burst(tx, ty, int(14 * power), speed=(80 * s, 360 * s), life=(0.25, 0.7),
                         gravity=560 * s, size=(0.7 * s, 1.6 * s))
        self.sound("02_electric_spark", 0.45 * power)

    def _main_strike(self) -> None:
        self.set_phase("STRIKE")
        W, H, s, rng, q = self.W, self.H, self.scale, self.rng, self.q
        cx, cy = W / 2, H / 2
        diag = math.hypot(W, H)
        if self._light:
            top = (cx + rng.uniform(-0.15, 0.15) * W, -20.0)
            self.bolts.append(fx.Bolt(top, (cx, cy), self.t, 0.65, 4.4 * s, rng,
                                      detail=q.bolt_detail, rough=0.15, forks=7.0,
                                      depth=q.branch_depth, restrikes=3, travel=0.06,
                                      max_channels=28))
            for sgn in (-1, 1):
                end = (cx + sgn * rng.uniform(0.16, 0.30) * W, cy + rng.uniform(-0.12, 0.22) * H)
                self.bolts.append(fx.Bolt((cx, cy), end, self.t + 0.03, 0.34, 1.7 * s, rng,
                                          detail=max(4, q.bolt_detail - 1), rough=0.2, forks=2.5,
                                          depth=min(2, q.branch_depth), restrikes=1, travel=0.05))
        if self.settings.use_shake:
            self.shake.kick(11 * s, 0.5)
        self.flash = (self.t, 0.62 * self.settings.flash_scale, 0.34)
        split = 7 * s if self.settings.use_glitch else 0.0
        self.rings.append((self.t, 0.95, 0.42 * diag, 3.0 * s, split, 1.0))
        self.rings.append((self.t + 0.12, 1.25, 0.58 * diag, 1.5 * s, split * 0.6, 1.0))
        self.parts.burst(cx, cy, int(q.particles * 0.42), speed=(220 * s, 980 * s),
                         life=(0.35, 1.15), gravity=900 * s, drag=1.2, size=(1.0 * s, 2.6 * s))
        self.parts.burst(cx, cy, int(q.particles * 0.10), speed=(40 * s, 220 * s),
                         life=(1.2, 2.6), gravity=-30 * s, drag=0.8, size=(1.2 * s, 2.6 * s),
                         kind=fx.EMBER)
        if self.settings.use_glitch:
            self.glitch_until = self.t + 0.38
        if not self.settings.reduced and q.distortion_strips:
            self.distort_until = self.t + 0.55
        self.sound("04_lightning_strike", 1.0)

    def _logo_layout(self) -> None:
        W, H = self.W, self.H
        size = clamp(min(W, H) * 0.155, 52.0, 180.0)
        self.logo.layout(W / 2, H / 2 - 0.03 * H, size, W * 0.82, self.devicePixelRatioF() or 1.0)

    def _letter_strike(self, i: int) -> None:
        self._logo_layout()
        s, rng = self.scale, self.rng
        c = self.logo.letter_center(i)
        r = self.logo.rects[i]
        if self._light:
            top = (c.x() + rng.uniform(-0.6, 0.6) * r.width(), r.top() - 0.22 * min(self.W, self.H))
            self.bolts.append(fx.Bolt(top, (c.x(), c.y()), self.t, 0.24, 1.6 * s, rng,
                                      detail=max(4, self.q.bolt_detail - 1), rough=0.2, forks=2.0,
                                      depth=1, restrikes=1, travel=0.04))
        self.parts.burst(c.x(), c.y(), 16, speed=(90 * s, 420 * s), life=(0.25, 0.7),
                         gravity=620 * s, size=(0.7 * s, 1.7 * s))
        if self.settings.use_shake:
            self.shake.kick(2.0 * s, 0.14)
        self.sound("02_electric_spark", 0.55)

    def _travel_strike(self) -> None:
        self._logo_layout()
        b, s, rng = self.logo.bounds, self.scale, self.rng
        y = b.center().y()
        if self._light:
            self.travel = fx.Bolt((b.left() - 80 * s, y + rng.uniform(-8, 8) * s),
                                  (b.right() + 80 * s, y + rng.uniform(-8, 8) * s), self.t, 0.62,
                                  2.4 * s, rng, detail=self.q.bolt_detail, rough=0.07, forks=6.0,
                                  depth=min(2, self.q.branch_depth), restrikes=2, travel=0.30)
        self.sound("08_color_transform_whoosh", 0.8)

    def _logo_pulse(self) -> None:
        self._logo_layout()
        b, s = self.logo.bounds, self.scale
        c = b.center()
        self.rings.append((self.t, 1.0, 0.46 * self.W, 2.2 * s,
                           4 * s if self.settings.use_glitch else 0.0, 0.16))
        self.parts.burst(c.x(), c.y(), int(self.q.particles * 0.18), speed=(120 * s, 620 * s),
                         angle=0.0, spread=math.tau, life=(0.3, 0.9), gravity=500 * s,
                         size=(0.8 * s, 2.0 * s))
        if self.settings.use_shake:
            self.shake.kick(3.0 * s, 0.25)
        self.flash = (self.t, 0.16 * self.settings.flash_scale, 0.35)

    def _grab_dashboard(self) -> None:
        if self._snap is not None or self._snap_failed:
            return
        try:
            self._snap = self.dashboard() if self.dashboard else None
        except Exception as e:
            print(f"[Cinematic] dashboard snapshot failed: {e}")
            self._snap = None
        if self._snap is None:
            self._snap_failed = True

    def _begin_entrance(self) -> None:
        self._grab_dashboard()
        self._skip_btn.hide()
        self.set_phase("ONLINE")
        if self._snap is None:
            # No picture of the dashboard — fade the black away over the live UI.
            self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    # ── simulation ───────────────────────────────────────────────────────────
    def advance(self, dt: float) -> None:
        t, s, rng = self.t, self.scale, self.rng
        W, H = self.W, self.H
        cx, cy = W / 2, H / 2
        mwh = min(W, H)
        qf = {"low": 0.45, "medium": 0.75}.get(self.q.name, 1.0)
        self.shake.step(dt)
        self.parts.step(dt)
        for b in self.bolts:
            b.step(t, rng)
        if self.travel is not None:
            self.travel.step(t, rng)
            if not self.travel.alive(t):
                self.travel = None
        self.bolts = [b for b in self.bolts if b.alive(t)]
        self.crawls = [b for b in self.crawls if b.alive(t)]
        self.rings = [r for r in self.rings if t < r[0] + r[1]]

        if t < self.t_out:
            rate = (10 + 46 * min(1.2, self._intensity(t))) * qf
            self._ember_acc += rate * dt
            while self._ember_acc >= 1:
                self._ember_acc -= 1
                self.parts.add(rng.uniform(0, W), rng.uniform(H * 0.2, H * 1.02),
                               rng.uniform(-8, 8) * s, -rng.uniform(14, 54) * s,
                               rng.uniform(2.4, 5.2), rng.uniform(0.7, 2.2) * s, fx.EMBER)

        if self._light and self.T_SPARK + 0.15 <= t < self.T_STRIKE:
            pr = seg(t, self.T_SPARK, self.T_STRIKE)
            self._tendril_acc += (3 + 24 * pr ** 1.3) * qf * dt
            while self._tendril_acc >= 1:
                self._tendril_acc -= 1
                ang = rng.uniform(0, math.tau)
                L = lerp(22 * s, 0.40 * mwh, pr ** 0.85) * rng.uniform(0.45, 1.0)
                self.bolts.append(fx.Bolt(
                    (cx + rng.uniform(-3, 3) * s, cy + rng.uniform(-3, 3) * s),
                    (cx + math.cos(ang) * L, cy + math.sin(ang) * L), t,
                    rng.uniform(0.07, 0.17), lerp(0.6, 1.3, pr) * s, rng,
                    detail=max(3, self.q.bolt_detail - 2), rough=0.3, forks=1.2, depth=1,
                    restrikes=1))

        if self._light and self.logo.paths and self.t_logo <= t < self.t_bright + 0.4:
            n = len(self.name)
            if self.t_bright <= t:
                active = list(range(n))
            else:
                active = [i for i in range(n)
                          if 0 <= t - (self.t_logo + i * self.LETTER_DT) < 0.38]
            self._crawl_acc += len(active) * 20 * qf * dt
            while self._crawl_acc >= 1 and active:
                self._crawl_acc -= 1
                r = self.logo.rects[rng.choice(active)]
                a = (rng.uniform(r.left(), r.right()), rng.uniform(r.top(), r.bottom()))
                b = (a[0] + rng.uniform(-0.6, 0.6) * r.width(), a[1] + rng.uniform(-0.5, 0.5) * r.height())
                self.crawls.append(fx.Bolt(a, b, t, rng.uniform(0.05, 0.11), 0.9 * s, rng,
                                           detail=4, rough=0.3, forks=0.8, depth=1, restrikes=0))

        if self.T_TERM <= t < self.t_term_end:
            L = self._term_layout()
            for i, line in enumerate(self.lines):
                st = self._line_start(i)
                if t < st:
                    break
                n = min(len(line), int((t - st) * self.CPS) + 1)
                if n > self._typed[i]:
                    if rng.random() < 0.18 + 0.3 * qf:
                        x = L["tx"] + L["fm"].horizontalAdvance(line[:n])
                        y = L["y0"] + L["head"] + L["pad"] + (i + 0.5) * L["line_h"]
                        self.parts.burst(x, y, rng.randint(1, 3), speed=(40 * s, 170 * s),
                                         angle=-math.pi / 2, spread=math.pi * 1.4,
                                         life=(0.15, 0.4), gravity=380 * s,
                                         size=(0.6 * s, 1.3 * s))
                    self._typed[i] = n
            if self.settings.use_glitch and rng.random() < 2.2 * dt:
                vis = [i for i in range(len(self.lines)) if t >= self._line_start(i)]
                if vis:
                    self._jitter[rng.choice(vis)] = (t + 0.06, rng.uniform(-7, 7) * s)

    def _intensity(self, t: float) -> float:
        if t < self.T_STRIKE:
            return 0.6 * ease_in_cubic(seg(t, 0.1, self.T_STRIKE))
        base = lerp(1.0, 0.38, ease_out_cubic(seg(t, self.T_STRIKE, self.T_STRIKE + 1.0)))
        if t >= self.t_bright:
            base += 0.45 * bell(seg(t, self.t_bright, self.t_bright + 0.7))
        if t >= self.t_out:
            base *= 1 - seg(t, self.t_out, self.t_out + 0.6)
        return base

    # ── painting ─────────────────────────────────────────────────────────────
    def draw(self, p: QPainter) -> None:
        t = self.t
        if t >= self.t_out:
            self._draw_entrance(p)
            return
        glitch = self.settings.use_glitch and t < self.glitch_until
        distort = t < self.distort_until
        if glitch or distort:
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
            p.fillRect(full, QColor(VOID))
            if distort:
                k = seg(t, self.T_STRIKE, self.distort_until)
                fx.wave_distort(p, buf, full, self.q.distortion_strips,
                                16 * self.scale * (1 - k) ** 2, t * 38.0)
            else:
                p.drawPixmap(full, buf, QRectF(buf.rect()))
            if glitch:
                # Torn scanlines of the finished frame. The RGB split itself is
                # drawn on the bolts and rings (see _split) — a full-frame
                # channel split costs two whole-frame tint passes per frame.
                k = seg(t, self.T_STRIKE, self.glitch_until)
                fx.glitch_slices(p, buf, full, self.rng, int(self.q.glitch_slices * (1 - k)) + 1,
                                 30 * self.scale * (1 - k), tint=fx.ACCENT, tint_alpha=0.08)
        else:
            self._scene(p)

    def _backdrops(self) -> tuple[QPixmap, QPixmap]:
        """Static layers (void, grid, dark vignette) rendered once per size."""
        dpr = self.devicePixelRatioF() or 1.0
        key = (self.width(), self.height(), dpr)
        if key != self._bg_key or self._bg_plain is None:
            self._bg_key = key
            w, h = self.W, self.H
            s = self.scale

            def make(grid: bool) -> QPixmap:
                pm = QPixmap(max(1, int(w * dpr)), max(1, int(h * dpr)))
                pm.setDevicePixelRatio(dpr)
                pm.fill(QColor(VOID))
                q = QPainter(pm)
                q.setRenderHint(QPainter.RenderHint.Antialiasing)
                if grid:
                    step = 46 * s
                    q.setPen(QPen(rgba(fx.ACCENT_DEEP, 0.55), max(1.0, 1.2 * s)))
                    y = (h / 2) % step
                    while y < h:
                        x = (w / 2) % step
                        while x < w:
                            q.drawPoint(QPointF(x, y))
                            x += step
                        y += step
                    q.setPen(QPen(rgba(fx.ACCENT_DEEP, 0.22), 1))
                    q.drawLine(QPointF(0, h / 2), QPointF(w, h / 2))
                    q.drawLine(QPointF(w / 2, 0), QPointF(w / 2, h))
                g = QRadialGradient(QPointF(w / 2, h / 2), math.hypot(w, h) / 2)
                g.setColorAt(0.0, QColor(0, 0, 0, 0))
                g.setColorAt(0.55, QColor(0, 0, 0, 40))
                g.setColorAt(1.0, QColor(0, 0, 0, 215))
                q.fillRect(QRectF(0, 0, w, h), QBrush(g))
                q.end()
                return pm

            self._bg_plain = make(False)
            self._bg_grid = make(True)
        return self._bg_plain, self._bg_grid

    def _scene(self, p: QPainter) -> None:
        t, s = self.t, self.scale
        W, H = self.W, self.H
        cx, cy = W / 2, H / 2
        mwh = min(W, H)
        full = self.full_rect()

        plain, grid = self._backdrops()
        p.drawPixmap(full, plain, QRectF(plain.rect()))
        ga = 0.85 * seg(t, self.T_STRIKE, self.T_STRIKE + 0.9)
        if ga > 0:
            p.setOpacity(ga)
            p.drawPixmap(full, grid, QRectF(grid.rect()))
            p.setOpacity(1.0)

        p.save()
        dx, dy = self.shake.offset()
        p.translate(dx, dy)

        I = self._intensity(t)
        if I > 0.01:
            R = mwh * (0.55 + 0.35 * min(1.0, I))
            g = QRadialGradient(QPointF(cx, cy), R)
            Ia = min(1.3, I)
            g.setColorAt(0.0, rgba(fx.ACCENT, 0.26 * Ia))
            g.setColorAt(0.25, rgba(fx.ACCENT, 0.11 * Ia))
            g.setColorAt(0.6, rgba(fx.ACCENT_DEEP, 0.07 * Ia))
            g.setColorAt(1.0, rgba(fx.ACCENT_DEEP, 0.0))
            p.save()
            p.setCompositionMode(PLUS)
            p.fillRect(QRectF(cx - R, cy - R, 2 * R, 2 * R), QBrush(g))
            p.restore()

        self._draw_energy_ring(p)
        for born, dur, max_r, width, split, squash in self.rings:
            k = seg(t, born, born + dur)
            if 0 < k < 1:
                fx.draw_ring(p, cx, cy if squash == 1.0 else self.logo.bounds.bottom() + 18 * s,
                             max_r * ease_out_expo(k), width * (1 - 0.6 * k), (1 - k) ** 1.6,
                             split=split * (1 - k), squash=squash)

        # the first spark — grows with the glow, swallowed by the strike
        sp = seg(t, self.T_SPARK, self.T_SPARK + 0.25)
        if sp > 0 and t < self.T_STRIKE + 0.25:
            gone = 1 - seg(t, self.T_STRIKE, self.T_STRIKE + 0.25)
            r = (5 + 16 * I + 3 * math.sin(t * 13)) * s
            fx.draw_glow(p, cx, cy, r * 3.4, sp * gone * (0.75 + 0.25 * math.sin(t * 23)))
            fx.draw_glow(p, cx, cy, r * 1.1, sp * gone, color=fx.HOT)

        passes = self.q.glow_passes
        split = self._split(t)
        for b in self.bolts:
            b.draw(p, t, passes, 1.0, split)
        for b in self.crawls:
            b.draw(p, t, min(2, passes), 0.8)

        self._draw_terminal(p)
        self._draw_logo(p)
        self.parts.draw(p)
        p.restore()

        if self.flash is not None:
            born, peak, dur = self.flash
            k = seg(t, born, born + dur)
            if k < 1:
                # A burst of light from the impact, not a grey wash: white-hot
                # at the centre, crimson by the edges, gone in a third of a second.
                a = peak * (1 - k) ** 2.4
                g = QRadialGradient(QPointF(cx, cy), math.hypot(W, H) * 0.62)
                g.setColorAt(0.0, rgba(fx.mix(fx.CORE_WHITE, fx.HOT, k * 1.6), a))
                g.setColorAt(0.35, rgba(fx.ACCENT, a * 0.42))
                g.setColorAt(1.0, rgba(fx.ACCENT, a * 0.12))
                p.save()
                p.setCompositionMode(PLUS)
                p.fillRect(full, QBrush(g))
                p.restore()

    def _split(self, t: float) -> float:
        """Sideways offset of the red / cyan fringes while the strike glitches."""
        if not self.settings.use_glitch or t >= self.glitch_until:
            return 0.0
        return 9 * self.scale * (1 - seg(t, self.T_STRIKE, self.glitch_until))

    def _draw_energy_ring(self, p: QPainter) -> None:
        t, s = self.t, self.scale
        ek = seg(t, self.T_STRIKE + 0.05, self.T_STRIKE + 0.7)
        if ek <= 0:
            return
        W, H = self.W, self.H
        cx, cy = W / 2, H / 2
        R = 0.31 * min(W, H) * ease_out_back(ek)
        a = lerp(0.95, 0.30, ease_out_cubic(seg(t, self.T_STRIKE + 0.4, self.T_TERM + 0.6)))
        if t >= self.t_logo:
            R *= lerp(1.0, 1.18, ease_out_cubic(seg(t, self.t_logo, self.t_logo + 0.8)))
        if t >= self.t_bright:
            a += 0.5 * bell(seg(t, self.t_bright, self.t_bright + 0.6))
        fx.draw_ring(p, cx, cy, R, 1.1 * s, a * 0.55)
        fx.draw_arc_ring(p, cx, cy, R * 0.94, 3.2 * s, a * 0.45, 24, t * 14, 0.55)
        fx.draw_arc_ring(p, cx, cy, R * 1.07, 1.5 * s, a * 0.65, 3, -t * 32, 0.18)
        fx.draw_arc_ring(p, cx, cy, R * 0.86, 1.0 * s, a * 0.35, 60, -t * 6, 0.35)

    # ── terminal ─────────────────────────────────────────────────────────────
    def _term_layout(self) -> dict:
        key = (self.width(), self.height())
        if key == self._term_key:
            return self._term
        s, W, H = self.scale, self.W, self.H
        f = fx.font(fx.FONT_MONO, 15 * s)
        fh = fx.font(fx.FONT_MONO, 11 * s, QFont.Weight.Bold, 2 * s)
        fs = fx.font(fx.FONT_MONO, 10 * s, QFont.Weight.Bold, 1 * s)
        w = min(720 * s, W * 0.7)
        line_h, head, foot, pad = 31 * s, 40 * s, 60 * s, 14 * s
        h = head + pad + line_h * len(self.lines) + pad + foot
        x0, y0 = (W - w) / 2, (H - h) / 2 - 0.02 * H
        self._term = dict(x0=x0, y0=y0, w=w, h=h, line_h=line_h, head=head, foot=foot,
                          pad=pad, f=f, fm=QFontMetricsF(f), fh=fh, fs=fs, tx=x0 + 24 * s)
        self._term_key = key
        return self._term

    def _draw_terminal(self, p: QPainter) -> None:
        t, s = self.t, self.scale
        op = seg(t, self.T_TERM, self.T_TERM + self.OPEN)
        cl = seg(t, self.t_term_end, self.t_term_end + 0.28)
        if op <= 0 or cl >= 1:
            return
        L = self._term_layout()
        x0, y0, w, h = L["x0"], L["y0"], L["w"], L["h"]
        tcx, tcy = x0 + w / 2, y0 + h / 2
        if cl <= 0:
            wf = ease_out_cubic(seg(op, 0, 0.45))
            hf = ease_out_cubic(seg(op, 0.38, 1))
        else:
            hf = 1 - ease_in_cubic(seg(cl, 0, 0.6))
            wf = 1 - ease_in_cubic(seg(cl, 0.55, 1))
        rw, rh = max(2.0, w * wf), max(2.0, h * hf)
        rect = QRectF(tcx - rw / 2, tcy - rh / 2, rw, rh)

        p.save()
        p.fillRect(rect, rgba(fx.panel_bg(), 0.88))
        top = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + 120 * s))
        top.setColorAt(0, rgba(fx.ACCENT, 0.10))
        top.setColorAt(1, rgba(fx.ACCENT, 0.0))
        p.fillRect(rect, QBrush(top))
        p.setPen(QPen(rgba(fx.ACCENT, 0.55), max(1.0, s)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)
        fx.draw_corner_brackets(p, rect.adjusted(-3 * s, -3 * s, 3 * s, 3 * s), 16 * s, 0.95,
                                2 * s)
        if hf < 0.2:
            p.save()
            p.setCompositionMode(PLUS)
            p.fillRect(rect.adjusted(0, -1, 0, 1), rgba(fx.HOT, 0.7 * (1 - hf / 0.2)))
            p.restore()
        if hf < 0.6:
            p.restore()
            return

        p.setClipRect(rect)
        if cl > 0:
            p.translate(tcx, tcy)
            p.scale(1.0, max(0.02, hf))
            p.translate(-tcx, -tcy)

        # header
        hr = QRectF(x0 + 24 * s, y0, w - 48 * s, L["head"])
        fx.draw_glow_text(p, hr, _ALIGN_LV, f"{self.name}  //  CORE BOOT SEQUENCE", L["fh"],
                          fx.ACCENT, 1.0, 0.5)
        blink = 0.55 + 0.45 * math.sin(t * 6)
        fx.draw_glow_text(p, hr, _ALIGN_RV, "SECURE CHANNEL  ·  v2.0", L["fs"], fx.TEXT_DIM, 1.0, 0.0)
        fx.draw_glow(p, hr.right() - QFontMetricsF(L["fs"]).horizontalAdvance(
            "SECURE CHANNEL  ·  v2.0") - 14 * s, hr.center().y(), 7 * s, blink)
        p.setPen(QPen(rgba(fx.ACCENT, 0.32), max(1.0, s)))
        p.drawLine(QPointF(x0 + 12 * s, y0 + L["head"]), QPointF(x0 + w - 12 * s, y0 + L["head"]))

        f, fm = L["f"], L["fm"]
        prompt_w = fm.horizontalAdvance("> ")
        n_lines = len(self.lines)
        current = max((i for i in range(n_lines) if t >= self._line_start(i)), default=-1)
        for i, line in enumerate(self.lines):
            st = self._line_start(i)
            if t < st:
                break
            n = min(len(line), int((t - st) * self.CPS) + 1)
            typed = line[:n]
            y = y0 + L["head"] + L["pad"] + i * L["line_h"]
            jit = self._jitter.get(i)
            jdx = jit[1] if jit and t < jit[0] else 0.0
            lr = QRectF(L["tx"] + jdx, y, w - 48 * s, L["line_h"])
            final = i == n_lines - 1
            if jdx:
                p.save()
                p.setCompositionMode(PLUS)
                p.setFont(f)
                p.setPen(rgba(SPLIT_RED, 0.5))
                p.drawText(lr.translated(-3 * s, 0), _ALIGN_LV, typed)
                p.setPen(rgba(SPLIT_CYAN, 0.35))
                p.drawText(lr.translated(3 * s, 0), _ALIGN_LV, typed)
                p.restore()
            fx.draw_glow_text(p, lr, _ALIGN_LV, typed[:1], f, fx.ACCENT, 1.0, 0.8)
            body = typed[2:] if len(typed) > 2 else ""
            fx.draw_glow_text(p, lr.translated(prompt_w, 0), _ALIGN_LV, body, f,
                              "#ffffff" if final else fx.TEXT, 1.0, 0.85 if final else 0.3)
            typing = n < len(line)
            if typing and n > 2:
                xo = fm.horizontalAdvance(typed[:-1])
                fx.draw_glow_text(p, lr.translated(xo, 0), _ALIGN_LV, typed[-1], f, "#ffffff",
                                  1.0, 1.0, spread=2.4)
            xe = L["tx"] + jdx + fm.horizontalAdvance(typed)
            if typing or (i == current and int(t * 3.2) % 2 == 0):
                p.fillRect(QRectF(xe + 2 * s, y + L["line_h"] * 0.22, 9 * s, L["line_h"] * 0.56),
                           rgba(fx.ACCENT, 0.9))
            done_t = st + len(line) / self.CPS
            if t >= done_t:
                ps = ease_out_cubic(seg(t, done_t, done_t + 0.16))
                bw = 70 * s
                bx = x0 + w - 24 * s - bw - 92 * s
                by = y + L["line_h"] / 2 - 1.5 * s
                p.fillRect(QRectF(bx, by, bw, 3 * s), rgba(fx.ACCENT_DEEP, 0.55))
                p.fillRect(QRectF(bx, by, bw * ps, 3 * s), rgba(fx.ACCENT, 0.95))
                if ps < 1:
                    fx.draw_glow(p, bx + bw * ps, by + 1.5 * s, 9 * s, 1.0)
                else:
                    tag = "[ READY ]" if final else "[  OK  ]"
                    fx.draw_glow_text(p, QRectF(bx + bw, y, x0 + w - 24 * s - bx - bw, L["line_h"]),
                                      _ALIGN_RV, tag, L["fs"], "#ffffff" if final else fx.soft(),
                                      1.0, 0.9 if final else 0.4)

        # footer: segmented sync bar
        fy = y0 + h - L["foot"]
        fill = seg(t, self.T_TERM + self.OPEN, self.t_term_end - 0.1)
        fr = QRectF(x0 + 24 * s, fy + 6 * s, w - 48 * s, 18 * s)
        fx.draw_glow_text(p, fr, _ALIGN_LV, "NEURAL MESH SYNC", L["fs"], fx.TEXT_DIM, 1.0, 0.0)
        fx.draw_glow_text(p, fr, _ALIGN_RV, f"{int(fill * 100):03d}%", L["fs"], "#ffffff", 1.0, 0.6)
        segs = 44
        gap = 2.2 * s
        sw = (w - 48 * s - gap * (segs - 1)) / segs
        lit = fill * segs
        for k in range(segs):
            r = QRectF(x0 + 24 * s + k * (sw + gap), fy + 30 * s, sw, 9 * s)
            if k < int(lit):
                p.fillRect(r, rgba(fx.ACCENT, 0.92))
            elif k == int(lit) and fill < 1:
                p.fillRect(r, rgba(fx.CORE_WHITE, 0.9 * (lit - k)))
            else:
                p.fillRect(r, rgba(fx.ACCENT_DEEP, 0.22))
        if 0 < fill < 1:
            fx.draw_glow(p, x0 + 24 * s + lit * (sw + gap), fy + 34.5 * s, 16 * s, 0.9)

        fx.draw_scanlines(p, rect, 0.55)
        fx.draw_scan_bar(p, rect, ((t - self.T_TERM) / 1.15) % 1.0, 46 * s, 0.8)
        p.restore()

    # ── wordmark ─────────────────────────────────────────────────────────────
    def _draw_logo(self, p: QPainter, alpha_mul: float = 1.0) -> None:
        t, s = self.t, self.scale
        if t < self.t_logo - 0.01:
            return
        self._logo_layout()
        logo = self.logo
        reduced = self.settings.reduced
        bright = bell(seg(t, self.t_bright, self.t_bright + 0.55)) ** 0.8 * (0.5 if reduced else 1.0)

        hx = None
        if self.travel is not None and self.travel.channels:
            prog = clamp((t - self.travel.born) / self.travel.travel)
            if prog < 1:
                hx, hy = fx.point_on(self.travel.channels[0][0], prog)
        elif not self._light and self.t_travel <= t < self.t_travel + 0.3:
            b = logo.bounds
            hx = lerp(b.left() - 60 * s, b.right() + 60 * s, seg(t, self.t_travel, self.t_travel + 0.3))
            hy = b.center().y()

        for i in range(len(logo.paths)):
            ti = self.t_logo + i * self.LETTER_DT
            if t < ti:
                continue
            a = ease_out_cubic(seg(t, ti, ti + 0.18)) * alpha_mul
            heat = 1 - ease_out_cubic(seg(t, ti, ti + 0.55))
            if hx is not None:
                d = abs(hx - logo.rects[i].center().x()) / max(1.0, logo.size * 0.9)
                heat = max(heat, clamp(1 - d))
            heat = max(heat, bright)
            sc = 1.0 if reduced else lerp(1.22, 1.0, ease_out_back(seg(t, ti, ti + 0.32)))
            glow = 0.55 + 0.3 * heat + 0.4 * bright + 0.06 * math.sin(t * 3 + i)
            logo.draw_letter(p, i, a, heat * (0.75 if reduced else 1.0), glow, sc)

        if self.travel is not None:
            self.travel.draw(p, t, self.q.glow_passes)
        if hx is not None:
            fx.draw_glow(p, hx, hy, 34 * s, 1.0)

        b = logo.bounds
        k = seg(t, self.t_bright, self.t_bright + 0.9)
        if 0 < k < 1:
            m = lerp(14 * s, 130 * s, ease_out_cubic(k))
            a = (1 - k) ** 1.5 * alpha_mul
            r = b.adjusted(-m, -m * 0.55, m, m * 0.55)
            p.setPen(QPen(rgba(fx.HOT, 0.5 * a), 1.2 * s))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            fx.draw_corner_brackets(p, r.adjusted(-4 * s, -4 * s, 4 * s, 4 * s), 22 * s, a, 2 * s)
            p.save()
            p.setCompositionMode(PLUS)
            p.setPen(QPen(rgba(fx.CORE_WHITE, 0.16 * bell(k) * alpha_mul), max(1.0, s)))
            step = 4 * s
            yy = b.top() + (t * 60 * s) % step
            while yy < b.bottom():
                p.drawLine(QPointF(b.left() - 10 * s, yy), QPointF(b.right() + 10 * s, yy))
                yy += step
            p.restore()

        k = seg(t, self.t_online, self.t_online + 0.55)
        if k > 0:
            e = ease_out_cubic(k)
            f = fx.font(fx.FONT_HEAD, 21 * s, QFont.Weight.DemiBold, lerp(30 * s, 10 * s, e))
            y = b.bottom() + 40 * s
            rr = QRectF(0, y, self.W, 32 * s)
            fx.draw_glow_text(p, rr, _ALIGN_C, "SYSTEM ONLINE", f, "#ffffff", e * alpha_mul, 0.9)
            tw = QFontMetricsF(f).horizontalAdvance("SYSTEM ONLINE")
            lk = ease_out_cubic(seg(t, self.t_online + 0.1, self.t_online + 0.65))
            Lx = 160 * s * lk
            ym = rr.center().y()
            cx = self.W / 2
            for sgn in (-1, 1):
                x_in = cx + sgn * (tw / 2 + 20 * s)
                x_out = x_in + sgn * Lx
                g = QLinearGradient(QPointF(x_in, ym), QPointF(x_out, ym))
                g.setColorAt(0, rgba(fx.ACCENT, 0.9 * alpha_mul))
                g.setColorAt(1, rgba(fx.ACCENT, 0.0))
                p.setPen(QPen(QBrush(g), 1.6 * s))
                p.drawLine(QPointF(x_in, ym), QPointF(x_out, ym))
                fx.draw_glow(p, x_in, ym, 8 * s, lk * alpha_mul)
            k2 = seg(t, self.t_online + 0.3, self.t_online + 0.75)
            if k2 > 0:
                fs = fx.font(fx.FONT_MONO, 11 * s, QFont.Weight.Normal, 2 * s)
                fx.draw_glow_text(p, QRectF(0, y + 36 * s, self.W, 20 * s), _ALIGN_C,
                                  "NEURAL CORE ACTIVE  ·  ALL MODULES SYNCHRONIZED", fs, fx.TEXT_DIM,
                                  ease_out_cubic(k2) * alpha_mul, 0.0)

    # ── entrance ─────────────────────────────────────────────────────────────
    def _draw_entrance(self, p: QPainter) -> None:
        k = seg(self.t, self.t_out, self.duration)
        entrance.draw(p, self._snap, k, self.W, self.H, self.scale,
                      self.settings.reduced or not self.settings.motion, self.logo)
        if self._snap is None:
            return
        so = 1 - seg(k, 0.0, 0.3)
        if so > 0:
            p.setOpacity(so)
            self._draw_logo_caption_only(p)
            p.setOpacity(1.0)
        if len(self.parts):
            self.parts.draw(p, 1 - k)

    def _draw_logo_caption_only(self, p: QPainter) -> None:
        s, b = self.scale, self.logo.bounds
        f = fx.font(fx.FONT_HEAD, 21 * s, QFont.Weight.DemiBold, 10 * s)
        fx.draw_glow_text(p, QRectF(0, b.bottom() + 40 * s, self.W, 32 * s), _ALIGN_C,
                          "SYSTEM ONLINE", f, "#ffffff", 1.0, 0.9)
