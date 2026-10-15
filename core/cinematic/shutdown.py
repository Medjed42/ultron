"""
core/cinematic/shutdown.py — ULTRON going dark: the electric slash.

Plays over a snapshot of the real window taken the instant shutdown began, so
it is the actual interface — whatever was on screen — that gets cut.

    1  SHUTDOWN INITIATED   the UI is re-graded to red monochrome and glows;
                            secondary panels die one by one (CRT collapse to a
                            line, then a point); hazard lines slide in around
                            a typed warning; the picture tears now and then.
    2  THE ELECTRIC SLASH   a hairline races from the top-left corner to the
                            bottom-right; a bolt rides it; sparks erupt along the
                            whole cut; the frame splits into two real pieces
                            (pre-masked from the frozen frame, antialiased edge)
                            that jolt apart with energy arcing across the gap and
                            white-hot edges, hang for a beat, then slide away —
                            upper piece up-and-right, lower down-and-left — with
                            motion-blur ghosts into darkness. A last spark
                            crackles in the centre and dies.
    3  FINAL POWER DOWN     ULTRON / POWERING DOWN... / SYSTEM OFFLINE, a CRT
                            collapse of the text, one red point that shrinks to
                            nothing. Then `finished` — and only then does the
                            window close.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QImage, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QPolygonF,
)

from . import fx
from .fx import (
    PLUS, VOID, bell, clamp, ease_in_cubic, ease_in_out_cubic, ease_out_back,
    ease_out_cubic, lerp, rgba, seg,
)
from .overlay import CinematicOverlay

_ALIGN_C = Qt.AlignmentFlag.AlignCenter
_ALIGN_LV = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter


def accent_grade(pm: QPixmap) -> QPixmap:
    """Luminance → accent. A multiply alone would turn cyan text black under a
    red theme (it has no red to keep); mapping brightness onto the accent makes
    every bright pixel of the UI glow in the theme colour instead, which is the
    look of the whole interface going red (or blue, or gold…)."""
    dpr = pm.devicePixelRatio() or 1.0
    img = pm.toImage().convertToFormat(QImage.Format.Format_Grayscale8)
    grey = QPixmap.fromImage(img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied))
    grey.setDevicePixelRatio(dpr)
    out = fx.tinted(grey, fx.mix(fx.ACCENT, "#ffffff", 0.12))
    q = QPainter(out)
    q.setCompositionMode(PLUS)
    q.fillRect(QRectF(0, 0, out.width() / dpr, out.height() / dpr), rgba(fx.ACCENT_DEEP, 0.10))
    q.end()
    return out


class ShutdownSequence(CinematicOverlay):
    SKIPPABLE = False

    T_SLASH = 1.45              # hairline leaves the top-left corner
    LINE_DUR = 0.26
    T_BOLT = T_SLASH + 0.14
    T_IMPACT = T_SLASH + 0.30
    T_SPLIT = T_IMPACT + 0.08
    T_HOLD_END = T_SPLIT + 0.72
    T_AWAY_END = T_HOLD_END + 0.95
    T_LAST_SPARK = T_AWAY_END - 0.18
    T_TEXT = T_AWAY_END + 0.12
    T_OFFLINE = T_TEXT + 0.80
    T_COLLAPSE = T_TEXT + 1.40
    T_DOT = T_COLLAPSE + 0.32
    T_END = T_DOT + 0.78
    DURATION = T_END + 0.12

    def __init__(self, host, settings, sfx=None, name: str = "ULTRON",
                 snapshot: QPixmap | None = None, secondary=None, preview: bool = False):
        super().__init__(host, settings, sfx)
        self.name = (name or "ULTRON").upper()
        self.preview = preview
        self.snap = snapshot
        self.secondary = list(secondary or [])      # [(name, QRectF)] panels to kill first
        self.parts = fx.Particles(self.q.particles, self.rng)
        self.arcs: list[fx.Bolt] = []
        self.bolt: fx.Bolt | None = None
        self.shake = fx.Shake(self.rng)
        self.flash = None
        self.logo = fx.LogoArt(self.name)
        self._red = self._red_split = self._cyan_split = None
        self._frozen = self._upper = self._lower = None
        self._arc_acc = 0.0
        self._drip_acc = 0.0
        self._glitches = [(0.42, 0.09), (0.80, 0.07), (1.12, 0.11)] if settings.use_glitch else []
        self.duration = self.DURATION

        self.cue(0.0, lambda: (self.set_phase("SHUTDOWN_INITIATED"), self.sound("03_energy_charge", 0.7)))
        self.cue(0.42, lambda: self.sound("13_digital_glitch", 0.4) if self.settings.use_glitch else None)
        self.cue(self.T_SLASH, self._begin_slash)
        self.cue(self.T_BOLT, self._bolt_follows)
        self.cue(self.T_IMPACT, self._impact)
        self.cue(self.T_SPLIT, lambda: self.sound("09_shutdown_electric_slash", 1.0))   # exactly at the split
        self.cue(self.T_LAST_SPARK, self._last_spark)
        self.cue(self.T_TEXT, lambda: (self.set_phase("POWERING_DOWN"), self.sound("10_power_down", 0.9)))

    # ── setup ────────────────────────────────────────────────────────────────
    def prepare(self) -> None:
        if self.snap is None or self.snap.isNull():
            self.snap = QPixmap(max(1, self.width()), max(1, self.height()))
            self.snap.fill(QColor(VOID))
        self._red = accent_grade(self.snap)
        if self.settings.use_glitch and self.q.rgb_split:
            self._red_split = fx.tinted(self.snap, "#ff0000")
            self._cyan_split = fx.tinted(self.snap, "#00ffff")

    def _cut_line(self) -> tuple[QPointF, QPointF]:
        W, H = self.W, self.H
        return QPointF(-0.02 * W, -0.03 * H), QPointF(1.02 * W, 1.03 * H)

    def _axes(self) -> tuple[float, float, float, float, float]:
        a, b = self._cut_line()
        dx, dy = b.x() - a.x(), b.y() - a.y()
        L = math.hypot(dx, dy) or 1.0
        ux, uy = dx / L, dy / L
        # normal pointing to the upper-right side of the cut
        return ux, uy, uy, -ux, L

    def _halves(self) -> tuple[QPainterPath, QPainterPath]:
        a, b = self._cut_line()
        ux, uy, nx, ny, L = self._axes()
        M = (self.W + self.H) * 2
        a2 = QPointF(a.x() - ux * M, a.y() - uy * M)
        b2 = QPointF(b.x() + ux * M, b.y() + uy * M)
        up = QPainterPath()
        up.addPolygon(QPolygonF([a2, b2, QPointF(b2.x() + nx * M, b2.y() + ny * M),
                                 QPointF(a2.x() + nx * M, a2.y() + ny * M)]))
        lo = QPainterPath()
        lo.addPolygon(QPolygonF([a2, b2, QPointF(b2.x() - nx * M, b2.y() - ny * M),
                                 QPointF(a2.x() - nx * M, a2.y() - ny * M)]))
        return up, lo

    def _mask(self, src: QPixmap, path: QPainterPath) -> QPixmap:
        out = QPixmap(src.size())
        out.setDevicePixelRatio(src.devicePixelRatio())
        out.fill(Qt.GlobalColor.transparent)
        q = QPainter(out)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.drawPixmap(0, 0, src)
        q.setCompositionMode(fx.DEST_IN)
        q.fillPath(path, QColor(255, 255, 255, 255))
        q.end()
        return out

    # ── events ───────────────────────────────────────────────────────────────
    def _begin_slash(self) -> None:
        self.set_phase("SLASH")
        # Freeze phase 1's last frame, and cut it into the two pieces now, so
        # every later frame is just two blits.
        dpr = self.devicePixelRatioF() or 1.0
        frozen = QPixmap(max(1, int(self.W * dpr)), max(1, int(self.H * dpr)))
        frozen.setDevicePixelRatio(dpr)
        frozen.fill(QColor(VOID))
        q = QPainter(frozen)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._draw_base(q, self.T_SLASH)
        q.end()
        self._frozen = frozen
        up, lo = self._halves()
        self._upper = self._mask(frozen, up)
        self._lower = self._mask(frozen, lo)
        self._red_split = self._cyan_split = None     # phase 1 only — free them
        self.sound("02_electric_spark", 0.8)

    def _bolt_follows(self) -> None:
        a, b = self._cut_line()
        s = self.scale
        if self.settings.use_lightning:
            self.bolt = fx.Bolt((a.x(), a.y()), (b.x(), b.y()), self.t, 1.05, 3.2 * s, self.rng,
                                detail=self.q.bolt_detail, rough=0.035, forks=9.0,
                                depth=min(2, self.q.branch_depth), restrikes=6, travel=0.16,
                                max_channels=22)

    def _impact(self) -> None:
        s = self.scale
        a, _b = self._cut_line()
        ux, uy, nx, ny, L = self._axes()
        if self.settings.use_shake:
            self.shake.kick(8 * s, 0.45)
        self.flash = (self.t, 0.32 * self.settings.flash_scale, 0.36)
        n = int(self.q.particles * 0.6)
        rng = self.rng
        for _ in range(min(n, self.parts.room())):
            u = rng.uniform(0.02, 0.98)
            x, y = a.x() + ux * L * u, a.y() + uy * L * u
            side = rng.choice((-1.0, 1.0))
            ang = math.atan2(ny * side, nx * side)
            self.parts.burst(x, y, 1, speed=(120 * s, 760 * s), angle=ang, spread=2.0,
                             life=(0.3, 1.0), gravity=800 * s, drag=1.5, size=(0.8 * s, 2.2 * s))

    def _last_spark(self) -> None:
        s = self.scale
        cx, cy = self.W / 2, self.H / 2
        if self.settings.use_lightning:
            for _ in range(3):
                ang = self.rng.uniform(0, math.tau)
                L = self.rng.uniform(16, 46) * s
                self.arcs.append(fx.Bolt((cx, cy), (cx + math.cos(ang) * L, cy + math.sin(ang) * L),
                                         self.t, self.rng.uniform(0.08, 0.16), 1.0 * s, self.rng,
                                         detail=4, rough=0.3, forks=1.0, depth=1, restrikes=1))
        self.parts.burst(cx, cy, 14, speed=(40 * s, 220 * s), life=(0.2, 0.6), gravity=420 * s,
                         size=(0.6 * s, 1.5 * s))
        self.sound("02_electric_spark", 0.4)

    # ── simulation ───────────────────────────────────────────────────────────
    def advance(self, dt: float) -> None:
        t, s, rng = self.t, self.scale, self.rng
        self.shake.step(dt)
        self.parts.step(dt)
        if self.bolt is not None:
            self.bolt.step(t, rng)
            if not self.bolt.alive(t):
                self.bolt = None
        self.arcs = [a for a in self.arcs if a.alive(t)]

        if self.T_SPLIT <= t < self.T_HOLD_END + 0.35 and self.settings.use_lightning:
            a, _b = self._cut_line()
            ux, uy, nx, ny, L = self._axes()
            gap = self._separation(t)
            if gap < 180 * s:
                self._arc_acc += 34 * dt * (0.5 + 0.5 * (self.q.name != "low"))
                while self._arc_acc >= 1:
                    self._arc_acc -= 1
                    u = rng.uniform(0.04, 0.96)
                    du = rng.uniform(-0.02, 0.02)
                    x, y = a.x() + ux * L * u, a.y() + uy * L * u
                    ox, oy = self._offset(t, +1)
                    x2, y2 = a.x() + ux * L * (u + du), a.y() + uy * L * (u + du)
                    lx, ly = self._offset(t, -1)
                    self.arcs.append(fx.Bolt((x + ox, y + oy), (x2 + lx, y2 + ly), t,
                                             rng.uniform(0.06, 0.13), 1.1 * s, rng, detail=4,
                                             rough=0.35, forks=1.0, depth=1, restrikes=1))

        if self.T_SPLIT <= t < self.T_AWAY_END - 0.2:
            a, _b = self._cut_line()
            ux, uy, nx, ny, L = self._axes()
            self._drip_acc += 45 * dt
            while self._drip_acc >= 1:
                self._drip_acc -= 1
                u = rng.uniform(0.03, 0.97)
                side = rng.choice((-1, 1))
                ox, oy = self._offset(t, side)
                self.parts.burst(a.x() + ux * L * u + ox, a.y() + uy * L * u + oy, 1,
                                 speed=(20 * s, 120 * s), angle=math.pi / 2, spread=1.4,
                                 life=(0.3, 0.8), gravity=700 * s, size=(0.6 * s, 1.4 * s))

    def _separation(self, t: float) -> float:
        """Distance each half has moved from the cut, in pixels."""
        s = self.scale
        diag = math.hypot(self.W, self.H)
        reduced = self.settings.reduced
        jolt = (10 if reduced else 16) * s * ease_out_back(seg(t, self.T_SPLIT, self.T_SPLIT + 0.35), 2.2)
        away = ease_in_cubic(seg(t, self.T_HOLD_END, self.T_AWAY_END))
        return jolt + away * (0.06 if reduced else 0.85) * diag

    def _offset(self, t: float, side: int) -> tuple[float, float]:
        ux, uy, nx, ny, _L = self._axes()
        d = self._separation(t)
        slide = 0.18 * d
        return side * (nx * d + ux * slide), side * (ny * d + uy * slide)

    # ── painting ─────────────────────────────────────────────────────────────
    def draw(self, p: QPainter) -> None:
        t = self.t
        full = self.full_rect()
        p.fillRect(full, QColor(VOID))
        p.save()
        dx, dy = self.shake.offset()
        p.translate(dx, dy)
        if t < self.T_SLASH:
            self._draw_base(p, t)
            self._draw_phase1_overlay(p, t)
        elif t < self.T_TEXT + 0.2:
            self._draw_slash(p, t)
        p.restore()
        if t >= self.T_TEXT - 0.05:
            self._draw_power_down(p, t)
        self.parts.draw(p)
        for a in self.arcs:
            a.draw(p, t, min(2, self.q.glow_passes))
        if self.flash is not None:
            born, peak, dur = self.flash
            k = seg(t, born, born + dur)
            if k < 1:
                p.save()
                p.setCompositionMode(PLUS)
                p.fillRect(full, rgba(fx.mix(fx.CORE_WHITE, fx.ACCENT, k * 1.5), peak * (1 - k) ** 2.2))
                p.restore()

    # phase 1 ─────────────────────────────────────────────────────────────────
    def _draw_base(self, p: QPainter, t: float) -> None:
        """The UI as it is at time t of phase 1: re-graded red, secondary
        panels collapsed, the rest dimming."""
        full = self.full_rect()
        snap, red = self.snap, self._red
        p.drawPixmap(full, snap, QRectF(snap.rect()))
        tk = ease_in_out_cubic(seg(t, 0.0, 0.9)) * 0.9
        if tk > 0:
            p.setOpacity(tk)
            p.drawPixmap(full, red, QRectF(red.rect()))
            p.setOpacity(1.0)
        s = self.scale
        sx = red.width() / max(1.0, self.W)
        sy = red.height() / max(1.0, self.H)
        for i, (_name, r) in enumerate(self.secondary):
            st = 0.20 + i * 0.17
            ck = seg(t, st, st + 0.38)
            if ck <= 0:
                continue
            p.fillRect(r, rgba(fx.mix(VOID, fx.ACCENT, 0.02)))
            if ck < 1:
                sq = ease_in_cubic(seg(ck, 0.0, 0.62))
                wq = ease_in_cubic(seg(ck, 0.55, 1.0))
                h = max(2.0 * s, r.height() * (1 - sq))
                w = max(1.0, r.width() * (1 - wq))
                dst = QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h)
                if sq < 1:
                    p.drawPixmap(dst, red, QRectF(r.left() * sx, r.top() * sy,
                                                  r.width() * sx, r.height() * sy))
                p.save()
                p.setCompositionMode(PLUS)
                p.fillRect(dst, rgba(fx.HOT, 0.55 * sq * (1 - wq)))
                p.restore()
                if sq >= 1:
                    fx.draw_glow(p, r.center().x(), r.center().y(), 26 * s * (1 - wq), 1 - wq)
            edge = bell(ck) * 0.75
            if edge > 0.01:
                p.setPen(QPen(rgba(fx.ACCENT, edge), max(1.0, s)))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
        dim = 0.30 * seg(t, 0.3, self.T_SLASH)
        if dim > 0:
            p.fillRect(full, QColor(0, 0, 0, int(255 * dim)))

    def _draw_phase1_overlay(self, p: QPainter, t: float) -> None:
        full = self.full_rect()
        s, W, H = self.scale, self.W, self.H
        cx, cy = W / 2, H / 2
        fx.draw_vignette(p, full, 0.95 * seg(t, 0.0, 0.8), fx.ACCENT, 0.45)

        for at, dur in self._glitches:
            if at <= t < at + dur:
                k = seg(t, at, at + dur)
                if self._red_split is not None:
                    # the UI's real colours flicker through the red, offset
                    fx.rgb_split(p, self._red_split, self._cyan_split, full, 8 * s * (1 - k), 0.28)
                fx.glitch_slices(p, self._red, full, self.rng, self.q.glitch_slices, 26 * s,
                                 tint=fx.ACCENT, tint_alpha=0.12)

        # hazard lines framing the warning
        open_k = ease_out_cubic(seg(t, 0.22, 0.55))
        close_k = ease_in_cubic(seg(t, self.T_SLASH - 0.28, self.T_SLASH - 0.04))
        vis = open_k * (1 - close_k)
        if vis > 0.01:
            band_h = 16 * s
            box_w, box_h = min(620 * s, W * 0.7), 96 * s
            for sgn in (-1, 1):
                y = cy + sgn * (box_h / 2 + 22 * s) - band_h / 2
                slide = (1 - open_k) * W * 0.6 * sgn
                bw = W * vis
                band = QRectF(cx - bw / 2 + slide, y, bw, band_h)
                p.fillRect(band, rgba(fx.panel_bg(), 0.85 * vis))
                p.fillRect(band, fx.hazard_brush(int(22 * s) or 1, 0.75, t * 60 * s * sgn))
                p.setPen(QPen(rgba(fx.ACCENT, 0.9 * vis), max(1.0, s)))
                p.drawLine(QPointF(band.left(), band.top()), QPointF(band.right(), band.top()))
                p.drawLine(QPointF(band.left(), band.bottom()), QPointF(band.right(), band.bottom()))

            # the warning box: opens as a line, then a box
            wf = ease_out_cubic(seg(t, 0.30, 0.48)) * (1 - close_k)
            hf = ease_out_cubic(seg(t, 0.40, 0.58)) * (1 - ease_in_cubic(seg(t, self.T_SLASH - 0.30, self.T_SLASH - 0.14)))
            if wf > 0.01:
                rw, rh = max(2.0, box_w * wf), max(2.0, box_h * hf)
                rect = QRectF(cx - rw / 2, cy - rh / 2, rw, rh)
                p.fillRect(rect, rgba(fx.panel_bg(), 0.92))
                p.setPen(QPen(rgba(fx.ACCENT, 0.85), max(1.0, 1.2 * s)))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(rect)
                fx.draw_corner_brackets(p, rect.adjusted(-4 * s, -4 * s, 4 * s, 4 * s), 18 * s, 1.0, 2 * s)
                if hf > 0.7:
                    p.save()
                    p.setClipRect(rect)
                    msg = f"{self.name} SHUTDOWN INITIATED"
                    n = int(len(msg) * seg(t, 0.52, 0.86))
                    pulse = 0.65 + 0.35 * math.sin(t * 7.0)
                    self._warning_icon(p, rect.left() + 46 * s, cy - 6 * s, 20 * s, pulse)
                    f = fx.font(fx.FONT_HEAD, 24 * s, QFont.Weight.Bold, 3 * s)
                    tr = QRectF(rect.left() + 80 * s, cy - 30 * s, rect.width() - 100 * s, 36 * s)
                    fx.draw_glow_text(p, tr, _ALIGN_LV, msg[:n], f, "#ffffff", 1.0, 1.0, spread=2.2)
                    sub = "TERMINATING NEURAL PROCESSES" + "." * (int(t * 5) % 4)
                    fs = fx.font(fx.FONT_MONO, 11 * s, QFont.Weight.Normal, 2 * s)
                    fx.draw_glow_text(p, QRectF(tr.left(), cy + 10 * s, tr.width(), 20 * s), _ALIGN_LV,
                                      sub, fs, fx.soft(), seg(t, 0.7, 0.9), 0.3)
                    fx.draw_scanlines(p, rect, 0.6)
                    p.restore()

    def _warning_icon(self, p: QPainter, x: float, y: float, r: float, a: float) -> None:
        tri = QPainterPath()
        tri.moveTo(x, y - r)
        tri.lineTo(x + r * 1.05, y + r * 0.8)
        tri.lineTo(x - r * 1.05, y + r * 0.8)
        tri.closeSubpath()
        fx.draw_glow(p, x, y, r * 2.4, 0.6 * a)
        p.save()
        p.setPen(QPen(rgba(fx.ACCENT, a), max(1.5, r * 0.14)))
        p.setBrush(rgba(fx.ACCENT, 0.18 * a))
        p.drawPath(tri)
        p.setPen(QPen(rgba("#ffffff", a), max(1.5, r * 0.14)))
        p.drawLine(QPointF(x, y - r * 0.35), QPointF(x, y + r * 0.25))
        p.drawPoint(QPointF(x, y + r * 0.55))
        p.restore()

    # phase 2 ─────────────────────────────────────────────────────────────────
    def _draw_slash(self, p: QPainter, t: float) -> None:
        full = self.full_rect()
        s = self.scale
        a, b = self._cut_line()
        ux, uy, nx, ny, L = self._axes()

        if t < self.T_SPLIT or self._upper is None:
            if self._frozen is not None:
                p.drawPixmap(full, self._frozen, QRectF(self._frozen.rect()))
        else:
            fade = 1 - ease_in_cubic(seg(t, self.T_HOLD_END + 0.2, self.T_AWAY_END))
            away = seg(t, self.T_HOLD_END, self.T_AWAY_END)
            ghosts = self.q.motion_blur if (away > 0.05 and not self.settings.reduced) else 0
            for side, pm in ((-1, self._lower), (+1, self._upper)):
                for gi in range(ghosts, -1, -1):
                    lag = gi * 0.035
                    ox, oy = self._offset(max(self.T_SPLIT, t - lag), side)
                    alpha = fade * (1.0 if gi == 0 else 0.22 / gi)
                    if alpha <= 0.01:
                        continue
                    p.setOpacity(alpha)
                    p.drawPixmap(full.translated(round(ox), round(oy)), pm, QRectF(pm.rect()))
                p.setOpacity(1.0)
                # white-hot cut edge on each piece, cooling to crimson as it goes
                ox, oy = self._offset(t, side)
                heat = 1 - seg(t, self.T_SPLIT, self.T_AWAY_END)
                ea = fade * (0.35 + 0.65 * heat)
                if ea > 0.01:
                    pen = QPen(rgba(fx.mix(fx.ACCENT, fx.CORE_WHITE, heat), ea), (1.2 + 1.6 * heat) * s)
                    p.save()
                    p.setCompositionMode(PLUS)
                    p.setPen(QPen(rgba(fx.ACCENT, 0.35 * ea), 9 * s))
                    p.drawLine(QPointF(a.x() + ox, a.y() + oy), QPointF(b.x() + ox, b.y() + oy))
                    p.setPen(pen)
                    p.drawLine(QPointF(a.x() + ox, a.y() + oy), QPointF(b.x() + ox, b.y() + oy))
                    p.restore()
            # energy filling the gap — only while it is a gap, not once the
            # pieces are sliding off the screen
            ga = 1 - seg(t, self.T_HOLD_END, self.T_HOLD_END + 0.3)
            gap = min(self._separation(t), 48 * s)
            if ga > 0.01 and gap > 1:
                p.save()
                p.setCompositionMode(PLUS)
                g = QLinearGradient(QPointF(a.x() - nx * gap, a.y() - ny * gap),
                                    QPointF(a.x() + nx * gap, a.y() + ny * gap))
                g.setColorAt(0.0, rgba(fx.ACCENT, 0.0))
                g.setColorAt(0.5, rgba(fx.ACCENT, 0.5 * ga))
                g.setColorAt(1.0, rgba(fx.ACCENT, 0.0))
                p.setPen(QPen(QBrush(g), gap * 2))
                p.drawLine(a, b)
                p.restore()

        # the hairline racing ahead of the bolt
        lp = ease_in_out_cubic(seg(t, self.T_SLASH, self.T_SLASH + self.LINE_DUR))
        line_a = 1 - seg(t, self.T_SPLIT, self.T_SPLIT + 0.25)
        if lp > 0 and line_a > 0:
            hx, hy = a.x() + ux * L * lp, a.y() + uy * L * lp
            p.save()
            p.setCompositionMode(PLUS)
            for w, al, col in ((10 * s, 0.10, fx.ACCENT), (3.2 * s, 0.45, fx.ACCENT),
                               (1.4 * s, 0.95, fx.HOT), (0.7 * s, 1.0, fx.CORE_WHITE)):
                p.setPen(QPen(rgba(col, al * line_a), w))
                p.drawLine(a, QPointF(hx, hy))
            p.restore()
            if lp < 1:
                fx.draw_glow(p, hx, hy, 34 * s, 1.0)
                fx.draw_glow(p, hx, hy, 12 * s, 1.0, color=fx.HOT)

        if self.bolt is not None:
            # it stays on the original line — between the two halves
            self.bolt.draw(p, t, self.q.glow_passes, 1.0)

    # phase 3 ─────────────────────────────────────────────────────────────────
    def _draw_power_down(self, p: QPainter, t: float) -> None:
        s, W, H = self.scale, self.W, self.H
        cx, cy = W / 2, H / 2
        if t < self.T_DOT:
            ia = ease_out_cubic(seg(t, self.T_TEXT - 0.05, self.T_TEXT + 0.35))
            sq = ease_in_cubic(seg(t, self.T_COLLAPSE, self.T_COLLAPSE + 0.2))
            wq = ease_in_cubic(seg(t, self.T_COLLAPSE + 0.16, self.T_DOT))
            p.save()
            p.translate(cx, cy)
            p.scale(max(0.002, 1 - wq), max(0.006, 1 - sq))
            p.translate(-cx, -cy)
            size = clamp(min(W, H) * 0.095, 40.0, 120.0)
            self.logo.layout(cx, cy - 0.07 * H, size, W * 0.7, self.devicePixelRatioF() or 1.0)
            for i in range(len(self.logo.paths)):
                self.logo.draw_letter(p, i, ia, 0.0, 0.55 + 0.1 * math.sin(t * 2.0))
            b = self.logo.bounds
            fm_ = fx.font(fx.FONT_MONO, 17 * s, QFont.Weight.Bold, 4 * s)
            msg = "POWERING DOWN"
            k = seg(t, self.T_TEXT + 0.25, self.T_TEXT + 0.6)
            dots = "." * (int((t - self.T_TEXT) * 4) % 4) if k >= 1 else ""
            fx.draw_glow_text(p, QRectF(0, b.bottom() + 34 * s, W, 26 * s), _ALIGN_C,
                              msg[:int(len(msg) * k)] + dots, fm_, fx.soft(), ia, 0.6)
            ok = seg(t, self.T_OFFLINE, self.T_OFFLINE + 0.5)
            if ok > 0:
                e = ease_out_cubic(ok)
                f = fx.font(fx.FONT_HEAD, 22 * s, QFont.Weight.DemiBold, lerp(28 * s, 10 * s, e))
                fx.draw_glow_text(p, QRectF(0, b.bottom() + 70 * s, W, 32 * s), _ALIGN_C,
                                  "SYSTEM OFFLINE", f, "#ffffff", e, 0.9)
            p.restore()
            if sq > 0:
                la = (1 - wq)
                p.save()
                p.setCompositionMode(PLUS)
                p.fillRect(QRectF(cx - W * 0.35 * (1 - wq), cy - 1.2 * s, W * 0.7 * (1 - wq), 2.4 * s),
                           rgba(fx.HOT, 0.8 * sq * la))
                p.restore()
                fx.draw_glow(p, cx, cy, 40 * s * la, sq * la)
        else:
            k = seg(t, self.T_DOT, self.T_END)
            shrink = 1 - ease_in_cubic(k)
            # one soft dip — the point gutters once before it goes, never strobes
            a = (1 - 0.5 * bell(seg(k, 0.45, 0.65))) * (1 - seg(k, 0.85, 1.0))
            fx.draw_glow(p, cx, cy, 46 * s * shrink, a)
            fx.draw_glow(p, cx, cy, 10 * s * shrink, a, color=fx.HOT)
            if shrink > 0.02:
                p.save()
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(rgba(fx.CORE_WHITE, a))
                p.drawEllipse(QPointF(cx, cy), 2.6 * s * shrink, 2.6 * s * shrink)
                p.restore()

    def finish(self) -> None:
        if self.sfx is not None and self.preview:
            self.sfx.stop_all()
        super().finish()
        self.snap = self._red = self._frozen = self._upper = self._lower = None
        self._red_split = self._cyan_split = None
