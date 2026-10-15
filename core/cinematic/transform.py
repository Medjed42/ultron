"""
core/cinematic/transform.py — the interface breaks and is rebuilt in a new colour.

A non-destructive overlay over a picture of the real window. The application
underneath is never touched except once, out of sight, to apply the new theme.

    1  ENERGY BUILDUP     the UI flickers gently; energy in the new colour
                          gathers at the edges and around the AI core; arcs crawl
                          along the frame                         (12 tick, 03 charge)
    2  THE SCREEN CRACKS  a central crack, then radial fractures with the new
                          colour glowing through them, joined by ring fractures;
                          sparks run along the cracks; a brief tear (05 fracture, 13 glitch)
    3  SHATTER            the cracked picture separates along those same
                          fractures into glass shards that drift outward through
                          the dark, losing their old colour, energy leaking
                          between them                            (06 shatter)
    4  REINITIALISE       the protocol terminal in the new colour — and, hidden
                          behind it, the real theme is applied and photographed
                                                                  (07 scan)
    5  LIGHTNING          a giant bolt crosses the screen diagonally; behind it,
                          in a wave that follows the bolt, the shards fly home
                          carrying the re-coloured interface; a reflection sweeps
                          across; the AI core pulses   (08 whoosh, 04 strike, 11 chime)

Reduced motion keeps the colour change but drops the cracking and flying: a
gentle energy edge, then a cross-fade under a soft scan line.
"""
from __future__ import annotations

import math
import random

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
    QRadialGradient,
)

from . import fx
from .fx import (
    PLUS, VOID, bell, clamp, ease_in_cubic, ease_in_out_cubic, ease_out_back, ease_out_cubic,
    ease_out_expo, lerp, rgba, seg,
)
from .overlay import CinematicOverlay

_ALIGN_C = Qt.AlignmentFlag.AlignCenter
_ALIGN_LV = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter


class Shard:
    __slots__ = ("poly", "path", "bbox", "centroid", "old", "glass", "edge", "new", "vel", "spin",
                 "depth", "proj", "wobble")

    def __init__(self, poly: list[tuple[float, float]]):
        self.poly = poly
        path = QPainterPath()
        path.addPolygon(QPolygonF([QPointF(x, y) for x, y in poly]))
        path.closeSubpath()
        self.path = path
        self.bbox = path.boundingRect()
        self.centroid = QPointF(sum(x for x, _ in poly) / len(poly), sum(y for _, y in poly) / len(poly))
        # Each look is baked once into a small pixmap: (pixmap, rect) or None.
        self.old = None          # the original interface
        self.glass = None        # the same, drained of colour into dark glass
        self.edge = None         # the glowing fracture outline
        self.new = None          # the re-coloured interface
        self.vel = (0.0, 0.0)
        self.spin = 0.0
        self.depth = 0.0
        self.proj = 0.0
        self.wobble = 0.0


class ColorTransformSequence(CinematicOverlay):
    SKIPPABLE = False

    T_CRACK = 1.0
    T_SHATTER = 2.2
    T_TERM = 3.45
    T_APPLY = 3.6
    T_GRAB = 3.85
    T_WHOOSH = 5.0
    T_BOLT = 5.2
    T_IMPACT = 5.42
    T_SWEEP = 6.0
    T_PULSE = 6.45
    DURATION = 7.1

    REDUCED = {"apply": 0.55, "grab": 0.65, "end": 1.8}

    def __init__(self, host, settings, sfx=None, *, name: str = "ULTRON", spec=None,
                 snapshot: QPixmap | None = None, snap_rect: QRectF | None = None,
                 apply_theme=None, grab_new=None, core_point: QPointF | None = None,
                 lines: list[str] | None = None, accent_at=None):
        super().__init__(host, settings, sfx)
        self.name = (name or "ULTRON").upper()
        self.spec = spec
        self.label = (spec.label if spec is not None else "NEW")
        self.lines = lines or [f"> {self.label} PROTOCOL INITIALIZED", "> SYSTEMS BOOTING UP...",
                               "> RECONFIGURING INTERFACE...", "> ENERGY CORE ONLINE"]
        self.snap = snapshot
        self.snap_rect = snap_rect
        self.apply_theme = apply_theme
        self.grab_new = grab_new
        self.core_point = core_point
        self.accent_at = accent_at              # RGB: t → accent hex
        self.new_snap: QPixmap | None = None
        self.applied = False
        self.reduced = settings.reduced or not settings.motion
        # Decided once: if adaptive quality later lowers the profile, shards must
        # not suddenly stop rotating mid-flight.
        self._rotate = self.q.name == "ultra"
        self.parts = fx.Particles(self.q.particles, self.rng)
        self.bolt: fx.Bolt | None = None
        self.edge_arcs: list[fx.Bolt] = []
        self.shards: list[Shard] = []
        self.rays: list[list[tuple[float, float]]] = []
        self.ray_len: list[list[float]] = []
        self.ring_segs: list[list[list[tuple[float, float]]]] = []
        self.ring_d: list[list[float]] = []
        self.flash = None
        self._shock: tuple[float, float] | None = None
        self._rays_pm: QPixmap | None = None
        self._rings_pm: QPixmap | None = None
        self.shake = fx.Shake(self.rng)
        self._arc_acc = 0.0
        self._spark_acc = 0.0

        if self.reduced:
            r = self.REDUCED
            self.duration = r["end"]
            self.cue(0.0, lambda: (self.set_phase("CHARGE"), self.sound("12_ui_confirm_tick", 0.8),
                                   self.sound("03_energy_charge", 0.5)))
            self.cue(r["apply"], self._apply)
            self.cue(r["grab"], self._grab_new)
            self.cue(r["end"] - 0.45, lambda: self.sound("11_system_online_chime", 0.7))
        else:
            self.duration = self.DURATION
            self.cue(0.0, lambda: (self.set_phase("CHARGE"), self.sound("12_ui_confirm_tick", 0.9)))
            self.cue(0.08, lambda: self.sound("03_energy_charge", 0.85))
            self.cue(self.T_CRACK, self._first_crack)
            self.cue(self.T_CRACK + 0.62, lambda: self.sound("13_digital_glitch", 0.55))
            self.cue(self.T_SHATTER, self._shatter)
            self.cue(self.T_TERM, lambda: (self.set_phase("REINITIALIZE"), self.sound("07_system_scan", 0.8)))
            self.cue(self.T_APPLY, self._apply)
            self.cue(self.T_GRAB, self._grab_new)
            self.cue(self.T_WHOOSH, lambda: self.sound("08_color_transform_whoosh", 0.9))
            self.cue(self.T_BOLT, self._bolt_strike)
            self.cue(self.T_IMPACT, self._impact)
            self.cue(self.T_PULSE, self._final_pulse)

    # ── setup ────────────────────────────────────────────────────────────────
    def prepare(self) -> None:
        if self.snap is None or self.snap.isNull():
            self.snap = QPixmap(max(1, self.width()), max(1, self.height()))
            self.snap.fill(QColor(VOID))
        if self.snap_rect is None:
            self.snap_rect = QRectF(0, 0, self.W, self.H)
        if self.core_point is None:
            self.core_point = QPointF(self.W * 0.82, self.H * 0.25)
        if not self.reduced:
            self._build_cracks()
            self._bake_cracks()

    def _build_cracks(self) -> None:
        W, H = self.W, self.H
        rng = random.Random()
        cx, cy = W * 0.5 + rng.uniform(-0.03, 0.03) * W, H * 0.47
        self.center = (cx, cy)
        diag = math.hypot(W, H)
        n = 11
        base = rng.uniform(0, math.tau)
        far = diag * 0.85
        for i in range(n):
            a = base + i / n * math.tau + rng.uniform(-0.16, 0.16)
            pts, dists = [(cx, cy)], [0.0]
            x, y, d = cx, cy, 0.0
            while d < far:
                step = diag * rng.uniform(0.035, 0.07)
                a += rng.uniform(-0.16, 0.16)
                x, y, d = x + math.cos(a) * step, y + math.sin(a) * step, d + step
                pts.append((x, y))
                dists.append(d)
            self.rays.append(pts)
            self.ray_len.append(dists)
        rings = (0.085, 0.2, 0.36)
        self.ring_d = [[diag * r * rng.uniform(0.85, 1.15) for _ in range(n)] for r in rings]
        for k in range(len(rings)):
            segs = []
            for i in range(n):
                j = (i + 1) % n
                p0 = self._at(i, self.ring_d[k][i])
                p1 = self._at(j, self.ring_d[k][j])
                mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
                dx, dy = p1[0] - p0[0], p1[1] - p0[1]
                L = math.hypot(dx, dy) or 1.0
                jag = rng.uniform(-0.12, 0.12) * L
                mid = (mx - dy / L * jag, my + dx / L * jag)
                segs.append([p0, mid, p1])
            self.ring_segs.append(segs)
        # shards: the cells between consecutive rays and consecutive rings
        bands = len(rings) + 1
        for i in range(n):
            j = (i + 1) % n
            for k in range(bands):
                d0i = 0.0 if k == 0 else self.ring_d[k - 1][i]
                d0j = 0.0 if k == 0 else self.ring_d[k - 1][j]
                d1i = far if k == bands - 1 else self.ring_d[k][i]
                d1j = far if k == bands - 1 else self.ring_d[k][j]
                poly = self._between(i, d0i, d1i)
                if k < bands - 1:
                    poly += self.ring_segs[k][i][1:-1]
                poly += list(reversed(self._between(j, d0j, d1j)))
                if k > 0:
                    poly += list(reversed(self.ring_segs[k - 1][i][1:-1]))
                sh = Shard(poly)
                if not sh.bbox.intersects(QRectF(0, 0, W, H)):
                    continue
                c = sh.centroid
                ux, uy = c.x() - cx, c.y() - cy
                L = math.hypot(ux, uy) or 1.0
                speed = (0.14 + 0.30 * k / bands) * diag * rng.uniform(0.7, 1.2)
                sh.vel = (ux / L * speed + rng.uniform(-0.03, 0.03) * diag,
                          uy / L * speed + rng.uniform(-0.03, 0.03) * diag)
                sh.spin = rng.uniform(-30, 30)
                sh.depth = rng.uniform(0.04, 0.16)
                sh.wobble = rng.uniform(0, math.tau)
                self.shards.append(sh)
        # the bolt crosses top-right → bottom-left; shards return in that order
        bx0, by0, bx1, by1 = W * 1.02, -0.03 * H, -0.02 * W, H * 1.03
        dx, dy = bx1 - bx0, by1 - by0
        LL = dx * dx + dy * dy
        for sh in self.shards:
            c = sh.centroid
            sh.proj = clamp(((c.x() - bx0) * dx + (c.y() - by0) * dy) / LL)

    def _at(self, i: int, d: float) -> tuple[float, float]:
        pts, ds = self.rays[i], self.ray_len[i]
        for k in range(1, len(pts)):
            if ds[k] >= d:
                r = (d - ds[k - 1]) / max(1e-6, ds[k] - ds[k - 1])
                return (lerp(pts[k - 1][0], pts[k][0], r), lerp(pts[k - 1][1], pts[k][1], r))
        return pts[-1]

    def _between(self, i: int, d0: float, d1: float) -> list[tuple[float, float]]:
        pts, ds = self.rays[i], self.ray_len[i]
        out = [self._at(i, d0)]
        out += [pts[k] for k in range(len(pts)) if d0 < ds[k] < d1]
        out.append(self._at(i, d1))
        return out

    def _cut(self, src: QPixmap, attr: str) -> None:
        """Cut `src` (a picture of the window) into the shard pixmaps, baking
        every look a shard needs so a frame is only transformed blits."""
        dpr = src.devicePixelRatio() or 1.0
        sr = self.snap_rect
        screen = QRectF(0, 0, self.W, self.H)
        pad = 4.0 * self.scale

        def canvas(r):
            pm = QPixmap(max(1, int(r.width() * dpr)), max(1, int(r.height() * dpr)))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            q.translate(-r.left(), -r.top())
            return pm, q

        for sh in self.shards:
            r = QRectF(sh.bbox.intersected(screen).toAlignedRect())
            if r.isEmpty():
                setattr(sh, attr, None)
                continue
            pm, q = canvas(r)
            q.drawPixmap(sr, src, QRectF(src.rect()))
            q.setCompositionMode(fx.DEST_IN)
            q.fillPath(sh.path, QColor(255, 255, 255, 255))
            q.end()
            setattr(sh, attr, (pm, r))
            if attr != "old":
                continue
            gpm, q = canvas(r)                                   # dark glass
            q.drawPixmap(r.topLeft(), pm)
            q.setCompositionMode(fx.OVER)
            q.setClipPath(sh.path)
            q.fillRect(r, rgba(VOID, 0.55))
            q.setCompositionMode(PLUS)
            q.fillRect(r, rgba(fx.ACCENT, 0.06))
            q.end()
            sh.glass = (gpm, r)
            er = r.adjusted(-pad, -pad, pad, pad)                 # fracture outline
            epm, q = canvas(er)
            q.setBrush(Qt.BrushStyle.NoBrush)
            q.setPen(QPen(rgba(fx.ACCENT, 0.55), 2.2 * self.scale))
            q.drawPath(sh.path)
            q.setPen(QPen(rgba(fx.CORE_WHITE, 0.5), 1.0))
            q.drawPath(sh.path)
            q.end()
            sh.edge = (epm, er)

    def _bake_cracks(self) -> None:
        """Stroking the fracture web costs ~10 ms a pass, so it is drawn once,
        as two layers (radial cracks, ring cracks), and revealed by clipping."""
        dpr = self.devicePixelRatioF() or 1.0

        def layer(rays: bool, rings: bool) -> QPixmap:
            pm = QPixmap(max(1, int(self.W * dpr)), max(1, int(self.H * dpr)))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._stroke_cracks(q, self._web(rays, rings))
            q.end()
            return pm
        self._rays_pm = layer(True, False)
        self._rings_pm = layer(False, True)

    def _draw_cracks(self, p: QPainter, t: float, alpha: float) -> None:
        """The web as it has grown by time t: radial cracks race out from the
        centre, ring cracks follow."""
        if self._rays_pm is None:
            return
        cx, cy = self.center
        diag = math.hypot(self.W, self.H)
        p.save()
        p.setOpacity(p.opacity() * alpha)
        for pm, t0, t1 in ((self._rays_pm, self.T_CRACK, self.T_CRACK + 0.6),
                           (self._rings_pm, self.T_CRACK + 0.3, self.T_CRACK + 0.95)):
            k = ease_out_expo(seg(t, t0, t1)) if t1 > t0 else 1.0
            if k <= 0:
                continue
            if k < 1:
                clip = QPainterPath()
                clip.addEllipse(QPointF(cx, cy), diag * 0.9 * k, diag * 0.9 * k)
                p.save()
                p.setClipPath(clip)
                p.drawPixmap(0, 0, pm)
                p.restore()
            else:
                p.drawPixmap(0, 0, pm)
        p.restore()
        fx.draw_glow(p, cx, cy, 60 * self.scale, 0.9 * alpha)

    # ── events ───────────────────────────────────────────────────────────────
    def _first_crack(self) -> None:
        self.set_phase("CRACK")
        self.sound("05_screen_fracture", 1.0)
        cx, cy = self.center
        s = self.scale
        self.parts.burst(cx, cy, 30, speed=(120 * s, 520 * s), life=(0.25, 0.7), gravity=600 * s,
                         size=(0.8 * s, 1.8 * s))
        if self.settings.use_shake:
            self.shake.kick(4 * s, 0.25)
        self.flash = (self.t, 0.16 * self.settings.flash_scale, 0.3, cx, cy)

    def _shatter(self) -> None:
        self.set_phase("SHATTER")
        self.sound("06_holographic_shatter", 1.0)
        self._cut(self.snap, "old")
        s = self.scale
        cx, cy = self.center
        self.parts.burst(cx, cy, int(self.q.particles * 0.35), speed=(160 * s, 700 * s),
                         life=(0.4, 1.2), gravity=380 * s, drag=1.0, size=(0.9 * s, 2.2 * s))
        if self.settings.use_shake:
            self.shake.kick(6 * s, 0.35)

    def _apply(self) -> None:
        """Out of sight behind the overlay: the real interface takes the colour."""
        if self.applied:
            return
        self.applied = True
        if self.apply_theme is not None:
            try:
                self.apply_theme()
            except Exception as e:
                print(f"[Transform] theme apply failed: {e}")

    def _grab_new(self) -> None:
        try:
            got = self.grab_new() if self.grab_new else None
        except Exception as e:
            print(f"[Transform] snapshot failed: {e}")
            got = None
        if got is not None:
            self.new_snap, self.snap_rect = got
        else:
            self.new_snap = self.snap
        if self.shards:
            self._cut(self.new_snap, "new")

    def _bolt_strike(self) -> None:
        self.set_phase("LIGHTNING")
        s, W, H = self.scale, self.W, self.H
        if self.settings.use_lightning:
            self.bolt = fx.Bolt((W * 1.02, -0.03 * H), (-0.02 * W, H * 1.03), self.t, 1.1, 4.2 * s,
                                self.rng, detail=self.q.bolt_detail, rough=0.06, forks=9.0,
                                depth=min(2, self.q.branch_depth), restrikes=5, travel=0.24,
                                max_channels=24)

    def _impact(self) -> None:
        self.sound("04_lightning_strike", 1.0)
        s, W, H = self.scale, self.W, self.H
        self.flash = (self.t, 0.42 * self.settings.flash_scale, 0.42, W * 0.5, H * 0.5)
        if self.settings.use_shake:
            self.shake.kick(9 * s, 0.45)
        diag = math.hypot(W, H)
        for k in range(int(self.q.particles * 0.5)):
            u = self.rng.random()
            x, y = lerp(W * 1.02, -0.02 * W, u), lerp(-0.03 * H, H * 1.03, u)
            self.parts.burst(x, y, 1, speed=(120 * s, 700 * s), angle=self.rng.uniform(0, math.tau),
                             spread=0.6, life=(0.3, 0.9), gravity=700 * s, size=(0.8 * s, 2.2 * s))
        self._shock = (self.t, diag * 0.55)

    def _final_pulse(self) -> None:
        self.set_phase("ONLINE")
        self.sound("11_system_online_chime", 0.8)

    # ── simulation ───────────────────────────────────────────────────────────
    def advance(self, dt: float) -> None:
        t, s, rng = self.t, self.scale, self.rng
        if self.accent_at is not None:
            fx.set_accent(self.accent_at(t))
        self.shake.step(dt)
        self.parts.step(dt)
        if self.bolt is not None:
            self.bolt.step(t, rng)
            if not self.bolt.alive(t):
                self.bolt = None
        for a in self.edge_arcs:
            a.step(t, rng)
        self.edge_arcs = [a for a in self.edge_arcs if a.alive(t)]
        if self.reduced:
            return
        W, H = self.W, self.H
        # energy arcs crawling along the frame during the buildup
        if self.settings.use_lightning and t < self.T_CRACK + 0.4:
            self._arc_acc += (2 + 10 * seg(t, 0.0, self.T_CRACK)) * dt
            while self._arc_acc >= 1:
                self._arc_acc -= 1
                side = rng.randrange(4)
                u0 = rng.uniform(0.05, 0.85)
                u1 = u0 + rng.uniform(0.04, 0.12)
                if side == 0:
                    a, b = (u0 * W, 2.0), (u1 * W, 2.0)
                elif side == 1:
                    a, b = (u0 * W, H - 2.0), (u1 * W, H - 2.0)
                elif side == 2:
                    a, b = (2.0, u0 * H), (2.0, u1 * H)
                else:
                    a, b = (W - 2.0, u0 * H), (W - 2.0, u1 * H)
                self.edge_arcs.append(fx.Bolt(a, b, t, rng.uniform(0.1, 0.2), 1.3 * s, rng, detail=5,
                                              rough=0.25, forks=1.5, depth=1, restrikes=1))
        # sparks running along the cracks
        if self.T_CRACK <= t < self.T_SHATTER and self.rays:
            self._spark_acc += 40 * dt
            while self._spark_acc >= 1:
                self._spark_acc -= 1
                i = rng.randrange(len(self.rays))
                d = rng.uniform(0.02, 1.0) * self._grow(i, t)
                x, y = self._at(i, d)
                self.parts.burst(x, y, 1, speed=(30 * s, 160 * s), life=(0.2, 0.5), gravity=300 * s,
                                 size=(0.6 * s, 1.4 * s))

    def _grow(self, i: int, t: float) -> float:
        """How far ray i has cracked at time t."""
        delay = (i % 4) * 0.06
        return self.ray_len[i][-1] * ease_out_expo(seg(t, self.T_CRACK + delay, self.T_CRACK + delay + 0.55))

    # ── painting ─────────────────────────────────────────────────────────────
    def draw(self, p: QPainter) -> None:
        if self.reduced:
            self._draw_reduced(p)
            return
        t, s = self.t, self.scale
        full = self.full_rect()
        p.fillRect(full, QColor(VOID))
        p.save()
        dx, dy = self.shake.offset()
        p.translate(dx, dy)
        if t < self.T_SHATTER:
            self._draw_intact(p, t)
        else:
            self._draw_shards(p, t)
        p.restore()
        if self.T_TERM - 0.1 <= t < self.T_BOLT + 0.1:
            self._draw_terminal(p, t)
        if self.bolt is not None:
            self.bolt.draw(p, t, self.q.glow_passes, 1.0, 6 * s if t < self.T_IMPACT + 0.2 and self.settings.use_glitch else 0.0)
        for a in self.edge_arcs:
            a.draw(p, t, 2)
        shock = self._shock
        if shock is not None:
            k = seg(t, shock[0], shock[0] + 1.0)
            if 0 < k < 1:
                fx.draw_ring(p, self.W / 2, self.H / 2, shock[1] * ease_out_expo(k), 2.6 * s, (1 - k) ** 1.6,
                             split=5 * s * (1 - k) if self.settings.use_glitch else 0.0)
        self._draw_pulse(p, t)
        self.parts.draw(p)
        if self.flash is not None:
            born, peak, dur, x, y = self.flash
            k = seg(t, born, born + dur)
            if 0 < k < 1:
                a = peak * (1 - k) ** 2.4
                g = QBrush(self._radial(x, y, a, k))
                p.save()
                p.setCompositionMode(PLUS)
                p.fillRect(full, g)
                p.restore()

    def _radial(self, x, y, a, k):
        g = QRadialGradient(QPointF(x, y), math.hypot(self.W, self.H) * 0.6)
        g.setColorAt(0.0, rgba(fx.mix(fx.CORE_WHITE, fx.HOT, k * 1.5), a))
        g.setColorAt(0.35, rgba(fx.ACCENT, a * 0.42))
        g.setColorAt(1.0, rgba(fx.ACCENT, a * 0.12))
        return g

    def _draw_intact(self, p: QPainter, t: float) -> None:
        """Phases 1–2: the picture, flickering gently, then cracking."""
        full = self.full_rect()
        s = self.scale
        flick = 1.0 - 0.06 * seg(t, 0.15, self.T_CRACK) * (0.5 + 0.5 * math.sin(t * 17.0))
        p.setOpacity(flick)
        p.drawPixmap(self.snap_rect, self.snap, QRectF(self.snap.rect()))
        p.setOpacity(1.0)
        charge = seg(t, 0.0, self.T_CRACK)
        fx.draw_vignette(p, full, 0.8 * charge, fx.ACCENT, 0.5)
        cp = self.core_point
        fx.draw_glow(p, cp.x(), cp.y(), 120 * s * (0.6 + charge), 0.65 * charge)
        fx.draw_ring(p, cp.x(), cp.y(), 70 * s * (1 + 0.1 * math.sin(t * 9)), 1.6 * s, 0.6 * charge)
        if t >= self.T_CRACK and self.rays:
            self._draw_cracks(p, t, 1.0)
            if self.settings.use_glitch and (self.T_CRACK <= t < self.T_CRACK + 0.12
                                             or self.T_CRACK + 0.62 <= t < self.T_CRACK + 0.72):
                fx.glitch_slices(p, self.snap, self.snap_rect, self.rng, 5, 22 * s, tint=fx.ACCENT,
                                 tint_alpha=0.12)

    def _web(self, rays: bool, rings: bool) -> QPainterPath:
        web = QPainterPath()
        if rays:
            for pts in self.rays:
                path = QPainterPath(QPointF(*pts[0]))
                for x, y in pts[1:]:
                    path.lineTo(x, y)
                web.addPath(path)
        if rings:
            for segs in self.ring_segs:
                for sg in segs:
                    path = QPainterPath(QPointF(*sg[0]))
                    for x, y in sg[1:]:
                        path.lineTo(x, y)
                    web.addPath(path)
        return web

    def _stroke_cracks(self, p: QPainter, web: QPainterPath) -> None:
        """A crack in glass: the dark split, the coloured glow through it, a
        hot edge and a white-hot core."""
        s = self.scale
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 170), 3.0 * s))
        p.drawPath(web)
        p.setCompositionMode(PLUS)
        p.setPen(QPen(rgba(fx.ACCENT, 0.30), 7 * s))
        p.drawPath(web)
        p.setPen(QPen(rgba(fx.HOT, 0.85), 1.6 * s))
        p.drawPath(web)
        p.setPen(QPen(rgba(fx.CORE_WHITE, 0.9), 1.0))
        p.drawPath(web)
        p.restore()

    def _shard_pose(self, sh: Shard, t: float) -> tuple[float, float, float, float, float]:
        """(dx, dy, angle, scale, home) — home 0 = adrift, 1 = back in place."""
        tau = max(0.0, t - self.T_SHATTER)
        drift = (1 - math.exp(-tau * 1.6)) / 1.6
        wob = math.sin(tau * 1.3 + sh.wobble)
        dx = sh.vel[0] * drift + wob * 4 * self.scale
        dy = sh.vel[1] * drift + math.cos(tau * 1.1 + sh.wobble) * 4 * self.scale
        ang = sh.spin * drift
        sc = 1.0 - sh.depth * ease_out_cubic(seg(t, self.T_SHATTER, self.T_SHATTER + 1.0))
        start = self.T_BOLT + sh.proj * 0.34
        home = ease_out_back(seg(t, start, start + 0.42), 1.1) if t >= self.T_BOLT else 0.0
        return dx * (1 - home), dy * (1 - home), ang * (1 - home), lerp(sc, 1.0, home), home

    def _draw_shards(self, p: QPainter, t: float) -> None:
        s = self.scale
        settled = seg(t, self.T_BOLT + 0.8, self.T_BOLT + 1.0)
        if settled > 0 and self.new_snap is not None:     # under the seams once home
            p.setOpacity(settled)
            p.drawPixmap(self.snap_rect, self.new_snap, QRectF(self.new_snap.rect()))
            p.setOpacity(1.0)
        # energy leaking between the shards
        cx, cy = self.center
        leak = (0.7 + 0.3 * math.sin(t * 6)) * (1 - seg(t, self.T_BOLT + 0.6, self.T_BOLT + 1.2))
        fx.draw_glow(p, cx, cy, math.hypot(self.W, self.H) * 0.32, 0.55 * leak)
        if t < self.T_BOLT + 0.6:
            self._draw_cracks(p, t, 0.45 * leak)
        dim = seg(t, self.T_SHATTER + 0.3, self.T_TERM)       # old colours draining away
        rotate = self._rotate
        for sh in self.shards:
            dx, dy, ang, sc, home = self._shard_pose(sh, t)
            c = sh.centroid
            p.save()
            if rotate and (ang or sc != 1.0):
                p.translate(c.x() + dx, c.y() + dy)
                p.rotate(ang)
                p.scale(sc, sc)
                p.translate(-c.x(), -c.y())
            else:                                       # medium / low: a plain blit
                p.translate(round(dx), round(dy))
            drift = 1 - home
            # bilinear filtering only while drifting slowly; in flight it is not seen
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, home <= 0.0)
            looks = ((sh.glass, drift), (sh.new, home)) if dim >= 1 else                 ((sh.old, (1 - dim) * drift), (sh.glass, dim * drift), (sh.new, home))
            for look, a_ in looks:
                if look is not None and a_ > 0.01:
                    pm, r = look
                    p.setOpacity(a_)
                    p.drawPixmap(r.topLeft(), pm)
            edge = drift * 0.9 + 0.6 * home * (1 - seg(t, self.T_SWEEP, self.T_SWEEP + 0.8))
            if sh.edge is not None and edge > 0.01:
                pm, r = sh.edge
                p.setOpacity(edge)
                p.setCompositionMode(PLUS)
                p.drawPixmap(r.topLeft(), pm)
            p.restore()
        # the reflection sweeping across the rebuilt interface
        k = seg(t, self.T_SWEEP, self.T_SWEEP + 0.8)
        if 0 < k < 1:
            x = lerp(-0.3 * self.W, 1.3 * self.W, ease_in_out_cubic(k))
            w = 0.18 * self.W
            g = QLinearGradient(QPointF(x - w, 0), QPointF(x + w, self.H * 0.25))
            g.setColorAt(0.0, rgba(fx.ACCENT, 0.0))
            g.setColorAt(0.45, rgba(fx.ACCENT, 0.12))
            g.setColorAt(0.5, rgba(fx.CORE_WHITE, 0.22))
            g.setColorAt(0.55, rgba(fx.ACCENT, 0.12))
            g.setColorAt(1.0, rgba(fx.ACCENT, 0.0))
            p.save()
            p.setCompositionMode(PLUS)
            p.fillRect(self.full_rect(), QBrush(g))
            p.restore()

    def _draw_terminal(self, p: QPainter, t: float) -> None:
        s, W, H = self.scale, self.W, self.H
        op = seg(t, self.T_TERM, self.T_TERM + 0.3)
        cl = seg(t, self.T_BOLT - 0.25, self.T_BOLT)
        if op <= 0 or cl >= 1:
            return
        w = min(600 * s, W * 0.6)
        line_h = 28 * s
        h = 46 * s + len(self.lines) * line_h + 96 * s
        x0, y0 = (W - w) / 2, H * 0.47 - h / 2
        hf = ease_out_cubic(seg(op, 0.35, 1.0)) * (1 - ease_in_cubic(cl))
        wf = ease_out_cubic(seg(op, 0.0, 0.5))
        rw, rh = max(2.0, w * wf), max(2.0, h * hf)
        rect = QRectF(W / 2 - rw / 2, y0 + h / 2 - rh / 2, rw, rh)
        p.save()
        p.fillRect(rect, rgba(fx.panel_bg(), 0.92))
        p.setPen(QPen(rgba(fx.ACCENT, 0.7), max(1.0, s)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)
        fx.draw_corner_brackets(p, rect.adjusted(-3 * s, -3 * s, 3 * s, 3 * s), 16 * s, 1.0, 2 * s)
        if hf < 0.5:
            p.restore()
            return
        p.setClipRect(rect)
        fh = fx.font(fx.FONT_HEAD, 22 * s, QFont.Weight.Bold, 12 * s)
        fx.draw_glow_text(p, QRectF(x0, y0 + 4 * s, w, 40 * s), _ALIGN_C, self.name, fh, fx.ACCENT, 1.0, 0.8)
        f = fx.font(fx.FONT_MONO, 13 * s)
        for i, line in enumerate(self.lines):
            st = self.T_TERM + 0.25 + i * 0.3
            if t < st:
                break
            n = min(len(line), int((t - st) * 70) + 1)
            fx.draw_glow_text(p, QRectF(x0 + 26 * s, y0 + 48 * s + i * line_h, w - 52 * s, line_h), _ALIGN_LV,
                              line[:n], f, "#ffffff" if i == 0 else fx.TEXT, 1.0, 0.6 if i == 0 else 0.3)
        hk = seg(t, self.T_TERM + 0.25 + len(self.lines) * 0.3, self.T_TERM + 0.6 + len(self.lines) * 0.3)
        if hk > 0:
            e = ease_out_cubic(hk)
            fb = fx.font(fx.FONT_HEAD, 24 * s, QFont.Weight.Bold, lerp(14 * s, 5 * s, e))
            by = y0 + 52 * s + len(self.lines) * line_h
            fx.draw_glow_text(p, QRectF(x0, by, w, 34 * s), _ALIGN_C, f"{self.label} INITIALIZED",
                              fb, fx.ACCENT, e, 1.0, spread=2.2)
            fsm = fx.font(fx.FONT_MONO, 10 * s, QFont.Weight.Bold, 3 * s)
            fx.draw_glow_text(p, QRectF(x0, by + 34 * s, w, 18 * s), _ALIGN_C, "ALL SYSTEMS ONLINE",
                              fsm, fx.soft(), e, 0.3)
        fx.draw_scanlines(p, rect, 0.5)
        fx.draw_scan_bar(p, rect, ((t - self.T_TERM) / 0.9) % 1.0, 40 * s, 0.7)
        p.restore()

    def _draw_pulse(self, p: QPainter, t: float) -> None:
        k = seg(t, self.T_PULSE, self.T_PULSE + 0.65)
        if 0 < k < 1 and self.core_point is not None:
            cp, s = self.core_point, self.scale
            fx.draw_ring(p, cp.x(), cp.y(), 40 * s + 140 * s * ease_out_expo(k), 2.2 * s, (1 - k) ** 1.4)
            fx.draw_glow(p, cp.x(), cp.y(), 110 * s, bell(k) * 0.9)

    # reduced motion ─────────────────────────────────────────────────────────
    def _draw_reduced(self, p: QPainter) -> None:
        t, s = self.t, self.scale
        full = self.full_rect()
        r = self.REDUCED
        p.fillRect(full, QColor(VOID))
        p.drawPixmap(self.snap_rect, self.snap, QRectF(self.snap.rect()))
        k = ease_in_out_cubic(seg(t, r["grab"] + 0.05, r["end"] - 0.25))
        if k > 0 and self.new_snap is not None:
            p.setOpacity(k)
            p.drawPixmap(self.snap_rect, self.new_snap, QRectF(self.new_snap.rect()))
            p.setOpacity(1.0)
        fx.draw_vignette(p, full, 0.6 * bell(seg(t, 0.0, r["end"])), fx.ACCENT, 0.5)
        if 0 < k < 1:
            fx.draw_scan_bar(p, full, k, 80 * s, 0.6)
