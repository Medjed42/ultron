"""
core/cinematic/fx.py — the reusable effects every ULTRON cinematic is built from.

    easing / timing     clamp, seg, ease_* — every sequence is a timeline of
                        normalised segments, never frame counts, so a slow frame
                        never stretches the animation and the speed setting is a
                        single multiplier.
    lightning           bolt_channels() grows a bolt by midpoint displacement and
                        forks it recursively; draw_bolt() renders it in
                        additive passes (halo → body → white-hot core) and can
                        draw a bolt partially, so a strike can *travel*.
    particles           Particles: sparks (streaks with gravity and drag) and
                        embers (soft glow sprites that drift and breathe).
    rings               draw_ring(): shockwaves and energy rings, optionally with
                        a chromatic (RGB) split.
    frame post-fx       glitch_slices(), rgb_split(), wave_distort() work on a
                        rendered pixmap, so they apply to the real UI as easily
                        as to a black screen.
    surface             scanlines, scan bar, hazard stripes, vignette, bloom.
    wordmark            LogoArt: the U L T R O N letters as vector paths with
                        pre-baked per-letter bloom.

Everything is QPainter. Expensive things (glow sprites, bloom, brushes, the
letters) are rendered once and cached; per frame the engine only blits and
strokes, which is what keeps a full-window effect inside a 16 ms budget on the
raster engine.
"""
from __future__ import annotations

import math
import random

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontMetricsF, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QRadialGradient, QTransform,
)

# ── Palette ───────────────────────────────────────────────────────────────────
# The accent is the active theme colour (core/theme.py), not a constant: every
# effect reads these module globals when it draws, and set_accent() swaps them,
# so lightning, particles, rings, text glow and the shutdown follow the theme —
# frame by frame in RGB mode.
CRIMSON = "#ff163c"       # ULTRON red, the original theme
VOID = "#050507"          # background
ACCENT = CRIMSON          # primary: bolts, rings, particles, glows
ACCENT_DEEP = "#8b0000"   # secondary: the dark end of every gradient
HOT = "#ff6b80"           # accent pushed toward white — the halo of a bolt core
CORE_WHITE = "#fff3f5"    # the white-hot centre of anything electric
TEXT = "#f6eef0"          # white with a breath of the accent in it
TEXT_DIM = "#9a6a72"
SPLIT_RED = "#ff0022"     # the two halves of an RGB split
SPLIT_CYAN = "#22e6ff"

PLUS = QPainter.CompositionMode.CompositionMode_Plus
OVER = QPainter.CompositionMode.CompositionMode_SourceOver
MULTIPLY = QPainter.CompositionMode.CompositionMode_Multiply
DEST_IN = QPainter.CompositionMode.CompositionMode_DestinationIn

FONT_HEAD = "Bahnschrift"
FONT_MONO = "Consolas"


_COLORS: dict[tuple[str, int], QColor] = {}


def rgba(hex_: str, a: float = 1.0) -> QColor:
    """Cached: effects ask for thousands of colours a frame, from a palette of
    a dozen, so alpha is quantised to 8 bits and the QColor is reused."""
    ai = 0 if a <= 0 else 255 if a >= 1 else int(a * 255 + 0.5)
    key = (hex_, ai)
    c = _COLORS.get(key)
    if c is None:
        if len(_COLORS) > 4096:
            _COLORS.clear()
        c = QColor(hex_)
        c.setAlpha(ai)
        _COLORS[key] = c
    return c


def mix(a: str, b: str, t: float) -> str:
    """Blend two hex colours; t=0 → a, t=1 → b."""
    t = clamp(t)
    ca, cb = QColor(a), QColor(b)
    return "#{:02x}{:02x}{:02x}".format(
        int(ca.red() + (cb.red() - ca.red()) * t),
        int(ca.green() + (cb.green() - ca.green()) * t),
        int(ca.blue() + (cb.blue() - ca.blue()) * t))


def set_accent(hex_color: str) -> None:
    """Point every effect at a theme colour. Dark custom colours are lifted
    (core.theme.vivid) so lightning can always glow on black."""
    global ACCENT, ACCENT_DEEP, HOT, CORE_WHITE, TEXT, TEXT_DIM
    from core.theme import hex_to_hsv, hsv_to_hex, vivid
    a = vivid(hex_color)
    h, s, v = hex_to_hsv(a)
    ACCENT = a
    ACCENT_DEEP = hsv_to_hex(h, min(1.0, s + 0.05), v * 0.55)
    HOT = mix(a, "#ffffff", 0.42)
    CORE_WHITE = mix("#ffffff", a, 0.05)
    TEXT = mix("#ffffff", a, 0.06)
    TEXT_DIM = hsv_to_hex(h, min(0.35, s), 0.62)


def accent() -> str:
    return ACCENT


def panel_bg() -> str:
    """Near-black with a breath of the accent: terminal and warning panels."""
    return mix(VOID, ACCENT, 0.035)


def soft() -> str:
    """The accent softened toward white: status tags and secondary text."""
    return mix(ACCENT, "#ffffff", 0.45)


# ── Timing ────────────────────────────────────────────────────────────────────

def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def seg(t: float, start: float, end: float) -> float:
    """Progress 0…1 of time t through the window [start, end]."""
    if end <= start:
        return 1.0 if t >= end else 0.0
    return clamp((t - start) / (end - start))


def ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def ease_in_cubic(t: float) -> float:
    return t * t * t


def ease_in_out_cubic(t: float) -> float:
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def ease_out_expo(t: float) -> float:
    return 1.0 if t >= 1 else 1 - 2 ** (-10 * t)


def ease_in_expo(t: float) -> float:
    return 0.0 if t <= 0 else 2 ** (10 * t - 10)


def ease_out_back(t: float, s: float = 1.70158) -> float:
    t -= 1
    return 1 + (s + 1) * t * t * t + s * t * t


def smoothstep(t: float) -> float:
    t = clamp(t)
    return t * t * (3 - 2 * t)


def bell(t: float) -> float:
    """0 → 1 → 0 over t in [0, 1]."""
    return math.sin(math.pi * clamp(t))


# ── Glow sprites ──────────────────────────────────────────────────────────────
_SPRITES: dict[tuple, QPixmap] = {}


def glow_sprite(color: str | None = None, hot: bool = True, size: int = 128) -> QPixmap:
    color = color or ACCENT
    key = (color, hot, size, HOT, CORE_WHITE)
    pm = _SPRITES.get(key)
    if pm is not None:
        return pm
    if len(_SPRITES) > 64:           # RGB mode mints a colour a frame — stay bounded
        _SPRITES.clear()
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QRadialGradient(size / 2, size / 2, size / 2)
    if hot:
        g.setColorAt(0.00, rgba(CORE_WHITE, 1.0))
        g.setColorAt(0.10, rgba(HOT, 0.85))
        g.setColorAt(0.32, rgba(color, 0.40))
        g.setColorAt(0.62, rgba(color, 0.10))
        g.setColorAt(1.00, rgba(color, 0.0))
    else:
        g.setColorAt(0.00, rgba(color, 0.75))
        g.setColorAt(0.40, rgba(color, 0.22))
        g.setColorAt(1.00, rgba(color, 0.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(g))
    p.drawEllipse(0, 0, size, size)
    p.end()
    _SPRITES[key] = pm
    return pm


def draw_glow(p: QPainter, x: float, y: float, radius: float, alpha: float = 1.0,
              color: str | None = None, hot: bool = True) -> None:
    if radius <= 0.5 or alpha <= 0.004:
        return
    color = color or ACCENT
    pm = glow_sprite(color, hot)
    p.save()
    p.setCompositionMode(PLUS)
    p.setOpacity(p.opacity() * clamp(alpha))
    p.drawPixmap(QRectF(x - radius, y - radius, radius * 2, radius * 2), pm, QRectF(pm.rect()))
    p.restore()


# ── Lightning ─────────────────────────────────────────────────────────────────
Channel = tuple  # (points: list[(x, y)], strength: float, start_frac: float)


def bolt_channels(ax: float, ay: float, bx: float, by: float, rng: random.Random,
                  detail: int = 6, roughness: float = 0.17, forks: float = 4.0,
                  fork_depth: int = 2, _depth: int = 0, _budget: list | None = None,
                  max_channels: int = 24) -> list[Channel]:
    """Grow a bolt from (ax, ay) to (bx, by).

    Midpoint displacement: every segment is split at a point pushed sideways by
    up to `roughness` × its length, halving the push each generation — so the
    bolt is jagged at every scale, like the real thing, rather than a zig-zag.
    Forks leave the main channel at a sharp angle, are shorter near the end,
    and fork again up to `fork_depth`; `max_channels` caps the whole tree so a
    long bolt can never cost hundreds of strokes.

    Returns [(points, strength, start_frac), …]; index 0 is the main channel,
    `start_frac` is how far along the parent the fork begins (used when a bolt
    is drawn travelling)."""
    if _budget is None:
        _budget = [max(1, max_channels) - 1]
    length = math.hypot(bx - ax, by - ay)
    if length < 2:
        return [([(ax, ay), (bx, by)], 1.0, 0.0)]
    pts = [(ax, ay), (bx, by)]
    off = length * roughness
    for _ in range(max(1, detail)):
        nxt = [pts[0]]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            dx, dy = x1 - x0, y1 - y0
            sl = math.hypot(dx, dy) or 1.0
            d = rng.uniform(-off, off)
            nxt.append(((x0 + x1) * 0.5 - dy / sl * d, (y0 + y1) * 0.5 + dx / sl * d))
            nxt.append((x1, y1))
        pts = nxt
        off *= 0.52
    out: list[Channel] = [(pts, 1.0, 0.0)]
    if _depth < fork_depth and forks > 0:
        n = len(pts)
        prob = forks / max(1, n - 4)
        for i in range(2, n - 2):
            if rng.random() >= prob:
                continue
            if _budget[0] <= 0:
                break
            _budget[0] -= 1
            x, y = pts[i]
            dx = pts[i + 1][0] - pts[i - 1][0]
            dy = pts[i + 1][1] - pts[i - 1][1]
            ang = math.atan2(dy, dx) + rng.choice((-1.0, 1.0)) * rng.uniform(0.3, 0.85)
            frac = i / (n - 1)
            blen = length * rng.uniform(0.10, 0.32) * (1.0 - 0.6 * frac)
            ex, ey = x + math.cos(ang) * blen, y + math.sin(ang) * blen
            for sub, s, sf in bolt_channels(x, y, ex, ey, rng, max(3, detail - 2),
                                            roughness * 1.15, forks * 0.45,
                                            fork_depth, _depth + 1, _budget):
                out.append((sub, s * 0.55, min(0.98, frac + sf * (1 - frac) * 0.3)))
    return out


def _partial_path(points: list, upto: float) -> QPainterPath:
    n = len(points)
    path = QPainterPath(QPointF(*points[0]))
    if upto >= 1.0:
        for x, y in points[1:]:
            path.lineTo(x, y)
        return path
    f = clamp(upto) * (n - 1)
    k = int(f)
    for x, y in points[1:k + 1]:
        path.lineTo(x, y)
    if k + 1 < n:
        (x0, y0), (x1, y1) = points[k], points[k + 1]
        r = f - k
        path.lineTo(x0 + (x1 - x0) * r, y0 + (y1 - y0) * r)
    return path


def point_on(points: list, frac: float) -> tuple[float, float]:
    n = len(points)
    f = clamp(frac) * (n - 1)
    k = min(int(f), n - 2)
    (x0, y0), (x1, y1) = points[k], points[k + 1]
    r = f - k
    return x0 + (x1 - x0) * r, y0 + (y1 - y0) * r


def draw_bolt(p: QPainter, channels: list[Channel], alpha: float = 1.0, width: float = 2.0,
              color: str | None = None, passes: int = 3, progress: float = 1.0,
              core: str | None = None, split: float = 0.0) -> None:
    """Additive multi-pass render: wide faint halo, crimson body, hot inner
    glow, white core. `progress` < 1 draws the bolt only part of the way;
    `split` > 0 adds red / cyan fringes offset sideways (RGB glitch).

    Forks are batched into one path per strength tier and only the main
    channel gets the wide halo — the halo of a fork is invisible next to the
    main channel's, and stroking it per fork was most of a frame's cost."""
    if alpha <= 0.01 or not channels:
        return
    color = color or ACCENT
    core = core or CORE_WHITE
    main = QPainterPath()
    tiers = (QPainterPath(), QPainterPath())       # strong forks, faint forks
    for pts, strength, start in channels:
        if progress < 1.0:
            if progress <= start:
                continue
            local = progress if start <= 0 else clamp((progress - start) / max(0.08, 1 - start) * 1.6)
        else:
            local = 1.0
        path = _partial_path(pts, local)
        if strength >= 0.99:
            main.addPath(path)
        else:
            tiers[0 if strength > 0.4 else 1].addPath(path)

    p.save()
    p.setCompositionMode(PLUS)
    p.setBrush(Qt.BrushStyle.NoBrush)
    pen = QPen()
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)

    def stroke(path: QPainterPath, col: str, a: float, w: float):
        if path.isEmpty() or a <= 0.004:
            return
        # Qt rasterises pens up to 1 px on a fast path roughly ten times
        # cheaper than a real stroke, so anything that would be under ~1.8 px
        # anyway is drawn as a hairline.
        pen.setColor(rgba(col, a))
        pen.setWidthF(1.0 if w < 1.8 else w)
        p.setPen(pen)
        p.drawPath(path)

    if split > 0.4:
        for dx, col, k in ((-split, SPLIT_RED, 0.55), (split, SPLIT_CYAN, 0.40)):
            p.translate(dx, 0)
            stroke(main, col, alpha * k, width * 1.4)
            stroke(tiers[0], col, alpha * k * 0.6, 1.0)
            p.translate(-dx, 0)

    a, w = alpha, width
    if passes >= 3:
        stroke(main, color, 0.07 * a, w * 9)
    if passes >= 2:
        stroke(main, color, 0.22 * a, w * 3.6)
        stroke(tiers[0], color, 0.10 * a, w * 2.2)
    stroke(main, HOT, 0.72 * a, w * 1.7)
    stroke(tiers[0], HOT, 0.40 * a, w * 1.1)
    stroke(tiers[1], HOT, 0.30 * a, 1.0)
    stroke(main, core, min(1.0, 1.1 * a), 1.0)
    stroke(tiers[0], core, min(1.0, 0.65 * a), 1.0)
    p.restore()


class Bolt:
    """A bolt with a life: it re-forms a few times while it lives (real
    lightning restrikes the same channel with small changes), and fades out."""

    __slots__ = ("a", "b", "born", "life", "width", "detail", "rough", "forks",
                 "depth", "channels", "restrikes", "_next", "travel", "max_channels")

    def __init__(self, a, b, born, life, width, rng, detail=6, rough=0.17, forks=4.0,
                 depth=2, restrikes=2, travel=0.0, max_channels=24):
        self.a, self.b = a, b
        self.born, self.life, self.width = born, life, width
        self.detail, self.rough, self.forks, self.depth = detail, rough, forks, depth
        self.restrikes = restrikes
        self.travel = travel          # seconds the strike takes to reach its target
        self.max_channels = max_channels
        self.channels = self._grow(rng)
        self._next = born + (life / (restrikes + 1) if restrikes else 1e9)

    def _grow(self, rng: random.Random) -> list[Channel]:
        return bolt_channels(self.a[0], self.a[1], self.b[0], self.b[1], rng, self.detail,
                             self.rough, self.forks, self.depth, max_channels=self.max_channels)

    def alive(self, t: float) -> bool:
        return t < self.born + self.life

    def step(self, t: float, rng: random.Random) -> None:
        if self.restrikes and t >= self._next:
            self.restrikes -= 1
            self._next = t + self.life / 3.0
            self.channels = self._grow(rng)

    def draw(self, p: QPainter, t: float, passes: int, alpha: float = 1.0,
             split: float = 0.0) -> None:
        age = t - self.born
        if age < 0:
            return
        k = age / self.life
        prog = clamp(age / self.travel) if self.travel > 0 else 1.0
        # Bright on contact, then a flickering decay — never a strobe: the
        # flicker modulates a fading curve, it does not switch on and off.
        flick = 0.78 + 0.22 * math.sin(age * 71.0)
        a = alpha * (1.0 - ease_in_cubic(clamp(k))) * flick
        draw_bolt(p, self.channels, a, self.width, passes=passes, progress=prog, split=split)


# ── Particles ─────────────────────────────────────────────────────────────────
SPARK, EMBER = 0, 1


class Particles:
    """Sparks streak along their velocity, cool from white to crimson and fall;
    embers are soft glow sprites that drift and breathe."""

    def __init__(self, cap: int, rng: random.Random | None = None):
        self.cap = cap
        self.rng = rng or random.Random()
        # [x, y, vx, vy, life, max_life, size, kind, gravity, drag, wobble]
        self.items: list[list] = []

    def __len__(self) -> int:
        return len(self.items)

    def room(self) -> int:
        return max(0, self.cap - len(self.items))

    def burst(self, x: float, y: float, n: int, speed=(150.0, 600.0), angle: float = 0.0,
              spread: float = math.tau, life=(0.35, 0.9), gravity: float = 700.0,
              drag: float = 1.4, size=(1.0, 2.2), kind: int = SPARK) -> None:
        rng = self.rng
        for _ in range(min(int(n), self.room())):
            a = angle + rng.uniform(-spread / 2, spread / 2)
            s = rng.uniform(*speed)
            L = rng.uniform(*life)
            self.items.append([x, y, math.cos(a) * s, math.sin(a) * s, L, L,
                               rng.uniform(*size), kind, gravity, drag, rng.uniform(0, 6.28)])

    def add(self, x, y, vx, vy, life, size, kind=EMBER, gravity=0.0, drag=0.0) -> None:
        if len(self.items) < self.cap:
            self.items.append([x, y, vx, vy, life, life, size, kind, gravity, drag,
                               self.rng.uniform(0, 6.28)])

    def step(self, dt: float) -> None:
        keep = []
        for it in self.items:
            it[4] -= dt
            if it[4] <= 0:
                continue
            d = max(0.0, 1.0 - it[9] * dt)
            if it[7] == EMBER:
                it[10] += dt * 1.7
                it[2] += math.sin(it[10]) * 6.0 * dt      # lazy sideways drift
            it[2] *= d
            it[3] = it[3] * d + it[8] * dt
            it[0] += it[2] * dt
            it[1] += it[3] * dt
            keep.append(it)
        self.items = keep

    def clear(self) -> None:
        self.items = []

    def draw(self, p: QPainter, alpha: float = 1.0) -> None:
        if not self.items or alpha <= 0.01:
            return
        p.save()
        p.setCompositionMode(PLUS)
        pen = QPen()
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        sprite = glow_sprite(ACCENT, True)
        src = QRectF(sprite.rect())
        base = p.opacity() * alpha
        for x, y, vx, vy, life, mx, size, kind, _g, _d, _w in self.items:
            k = life / mx                                   # 1 → 0 as it ages
            if kind == SPARK:
                p.setOpacity(base)
                tail = 0.012 + 0.016 * k          # streak length = speed × this
                col = CORE_WHITE if k > 0.75 else HOT if k > 0.4 else ACCENT
                pen.setColor(rgba(col, min(1.0, k * 1.8)))
                pen.setWidthF(size * (0.45 + 0.55 * k))
                p.setPen(pen)
                p.drawLine(QPointF(x, y), QPointF(x - vx * tail, y - vy * tail))
            else:
                a = math.sin(math.pi * (1.0 - k)) * (0.65 + 0.35 * math.sin(_w * 2.3))
                if a <= 0.02:
                    continue
                p.setOpacity(base * a)
                r = size * 3.4
                p.drawPixmap(QRectF(x - r, y - r, r * 2, r * 2), sprite, src)
        p.restore()


# ── Rings & shockwaves ────────────────────────────────────────────────────────

def _band(p: QPainter, cx: float, cy: float, radius: float, squash: float, width: float,
          col: str, alpha: float) -> None:
    """A ring `width` wide built from hairline ellipses — a thick stroked
    ellipse costs ~10× more than a 1 px one, and at these alphas the stacked
    hairlines read as one soft band."""
    n = max(1, min(7, int(width / 1.6) + 1))
    a = alpha / max(1.0, n * 0.6)
    pen = QPen(rgba(col, a), 1.0)
    p.setPen(pen)
    for i in range(n):
        off = (i - (n - 1) / 2) * (width / n)
        r = radius + off
        if r > 0.5:
            p.drawEllipse(QPointF(cx, cy), r, r * squash)


def draw_ring(p: QPainter, cx: float, cy: float, radius: float, width: float, alpha: float,
              color: str | None = None, split: float = 0.0, squash: float = 1.0) -> None:
    if alpha <= 0.01 or radius <= 0.5:
        return
    color = color or ACCENT
    p.save()
    p.setCompositionMode(PLUS)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if split > 0.4:
        _band(p, cx - split, cy, radius, squash, width, SPLIT_RED, alpha * 0.45)
        _band(p, cx + split, cy, radius, squash, width * 0.8, SPLIT_CYAN, alpha * 0.28)
    _band(p, cx, cy, radius, squash, width * 3.2, color, alpha * 0.45)
    _band(p, cx, cy, radius, squash, width, HOT, alpha * 0.85)
    p.setPen(QPen(rgba(CORE_WHITE, alpha * 0.55), 1.0))
    p.drawEllipse(QPointF(cx, cy), radius, radius * squash)
    p.restore()


def draw_arc_ring(p: QPainter, cx: float, cy: float, radius: float, width: float,
                  alpha: float, segments: int, rotation: float, span: float = 0.6,
                  color: str | None = None) -> None:
    """A dashed ring of `segments` arcs, rotated by `rotation` degrees."""
    if alpha <= 0.01 or radius <= 1:
        return
    color = color or ACCENT
    p.save()
    p.setCompositionMode(PLUS)
    pen = QPen(rgba(color, alpha), width)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
    step = 360.0 / segments
    for i in range(segments):
        p.drawArc(rect, int((rotation + i * step) * 16), int(step * span * 16))
    p.restore()


# ── Frame post-processing ─────────────────────────────────────────────────────

def tinted(pm: QPixmap, color: str) -> QPixmap:
    """`pm` multiplied by `color` (keeps alpha). Multiplying by pure red keeps
    only the red channel, by cyan only green+blue — the two halves of an RGB
    split; by a warm red it is the 'whole UI glowing red' tint."""
    out = QPixmap(pm.size())
    out.setDevicePixelRatio(pm.devicePixelRatio())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.drawPixmap(0, 0, pm)
    p.setCompositionMode(MULTIPLY)
    dpr = pm.devicePixelRatio() or 1.0
    full = QRectF(0, 0, pm.width() / dpr, pm.height() / dpr)
    p.fillRect(full, QColor(color))
    p.setCompositionMode(DEST_IN)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


def _src(pm: QPixmap, target: QRectF, x: float, y: float, w: float, h: float) -> QRectF:
    """Map a rectangle in target (logical) coordinates to pm's pixel space."""
    sx = pm.width() / max(1e-6, target.width())
    sy = pm.height() / max(1e-6, target.height())
    return QRectF(x * sx, y * sy, w * sx, h * sy)


def glitch_slices(p: QPainter, pm: QPixmap, target: QRectF, rng: random.Random,
                  slices: int, max_shift: float, tint: str | None = None,
                  tint_alpha: float = 0.0) -> None:
    """Redraw random horizontal bands of `pm` displaced sideways — the torn
    scanlines of a corrupted signal."""
    H = target.height()
    for _ in range(max(0, slices)):
        h = rng.uniform(2.0, max(3.0, H * 0.055))
        y = rng.uniform(0.0, max(0.0, H - h))
        dx = rng.uniform(-max_shift, max_shift)
        dst = QRectF(target.left() + dx, target.top() + y, target.width(), h)
        p.drawPixmap(dst, pm, _src(pm, target, 0, y, target.width(), h))
        if tint and tint_alpha > 0 and rng.random() < 0.5:
            p.save()
            p.setCompositionMode(PLUS)
            p.fillRect(dst, rgba(tint, tint_alpha * rng.uniform(0.3, 1.0)))
            p.restore()


def rgb_split(p: QPainter, red_pm: QPixmap, cyan_pm: QPixmap, target: QRectF,
              offset: float, alpha: float) -> None:
    if alpha <= 0.01 or abs(offset) < 0.3:
        return
    p.save()
    p.setCompositionMode(PLUS)
    p.setOpacity(p.opacity() * clamp(alpha))
    p.drawPixmap(target.translated(-offset, 0), red_pm, QRectF(red_pm.rect()))
    p.drawPixmap(target.translated(offset, offset * 0.25), cyan_pm, QRectF(cyan_pm.rect()))
    p.restore()


def wave_distort(p: QPainter, pm: QPixmap, target: QRectF, strips: int, amp: float,
                 phase: float, freq: float = 2.2) -> None:
    """Draw `pm` as horizontal strips each pushed sideways on a sine — a heat
    shimmer / shockwave distortion that costs `strips` blits."""
    strips = max(1, strips)
    h = target.height() / strips
    for i in range(strips):
        y = i * h
        dx = math.sin(phase + (i / strips) * freq * math.tau) * amp
        p.drawPixmap(QRectF(target.left() + dx, target.top() + y, target.width(), h + 1),
                     pm, _src(pm, target, 0, y, target.width(), h + 1))


def bloom(pm: QPixmap, factor: int = 6) -> QPixmap:
    """A soft blur by shrinking and re-growing with smooth filtering — QPainter
    has no blur, but area-averaged downscale + bilinear upscale is one."""
    dpr = pm.devicePixelRatio() or 1.0
    w, h = pm.width(), pm.height()
    mode = Qt.TransformationMode.SmoothTransformation
    asp = Qt.AspectRatioMode.IgnoreAspectRatio
    small = pm.scaled(max(1, w // factor), max(1, h // factor), asp, mode)
    out = small.scaled(w, h, asp, mode)
    out.setDevicePixelRatio(dpr)
    return out


# ── Surfaces ──────────────────────────────────────────────────────────────────
_BRUSHES: dict[tuple, QBrush] = {}


def scanline_brush(gap: int = 3, alpha: float = 0.35) -> QBrush:
    key = ("scan", gap, round(alpha, 2))
    b = _BRUSHES.get(key)
    if b is None:
        pm = QPixmap(8, gap)
        pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm)
        q.fillRect(0, 0, 8, 1, QColor(0, 0, 0, int(255 * alpha)))
        q.end()
        b = _BRUSHES[key] = QBrush(pm)
    return b


def hazard_brush(size: int = 24, alpha: float = 0.55, offset: float = 0.0) -> QBrush:
    key = ("hazard", size, round(alpha, 2), ACCENT)
    b = _BRUSHES.get(key)
    if b is None:
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.setPen(QPen(rgba(ACCENT, alpha), size * 0.34))
        for k in (-1, 0, 1):
            q.drawLine(QPointF(0 + k * size, size), QPointF(size + k * size, 0))
        q.end()
        b = _BRUSHES[key] = QBrush(pm)
    if offset:
        b = QBrush(b)
        b.setTransform(QTransform.fromTranslate(offset % size, 0))
    return b


def draw_scanlines(p: QPainter, rect: QRectF, alpha: float = 1.0, gap: int = 3) -> None:
    if alpha <= 0.01:
        return
    p.save()
    p.setOpacity(p.opacity() * clamp(alpha))
    p.fillRect(rect, scanline_brush(gap))
    p.restore()


def draw_scan_bar(p: QPainter, rect: QRectF, pos: float, height: float, alpha: float,
                  color: str | None = None) -> None:
    """A bright band sweeping down `rect`; `pos` 0…1 is the leading edge."""
    if alpha <= 0.01:
        return
    color = color or ACCENT
    y = rect.top() + pos * rect.height()
    g = QLinearGradient(0, y - height, 0, y + 1)
    g.setColorAt(0.0, rgba(color, 0.0))
    g.setColorAt(0.85, rgba(color, 0.16 * alpha))
    g.setColorAt(1.0, rgba(CORE_WHITE, 0.50 * alpha))
    band = QRectF(rect.left(), y - height, rect.width(), height + 1).intersected(rect)
    if band.isEmpty():
        return
    p.save()
    p.setCompositionMode(PLUS)
    p.fillRect(band, QBrush(g))
    p.restore()


def draw_vignette(p: QPainter, rect: QRectF, alpha: float, color: str | None = None,
                  inner: float = 0.55) -> None:
    """Glow creeping in from the edges of the frame."""
    if alpha <= 0.01:
        return
    color = color or ACCENT
    c = rect.center()
    r = math.hypot(rect.width(), rect.height()) / 2
    g = QRadialGradient(c, r)
    g.setColorAt(0.0, rgba(color, 0.0))
    g.setColorAt(inner, rgba(color, 0.0))
    g.setColorAt(0.85, rgba(color, 0.22 * alpha))
    g.setColorAt(1.0, rgba(color, 0.55 * alpha))
    p.save()
    p.setCompositionMode(PLUS)
    p.fillRect(rect, QBrush(g))
    p.restore()


def draw_corner_brackets(p: QPainter, r: QRectF, length: float, alpha: float,
                         width: float = 2.0, color: str | None = None) -> None:
    if alpha <= 0.01:
        return
    color = color or ACCENT
    p.save()
    pen = QPen(rgba(color, alpha), width)
    pen.setCapStyle(Qt.PenCapStyle.SquareCap)
    p.setPen(pen)
    l = length
    for (x, y, sx, sy) in ((r.left(), r.top(), 1, 1), (r.right(), r.top(), -1, 1),
                           (r.left(), r.bottom(), 1, -1), (r.right(), r.bottom(), -1, -1)):
        p.drawLine(QPointF(x, y), QPointF(x + sx * l, y))
        p.drawLine(QPointF(x, y), QPointF(x, y + sy * l))
    p.restore()


def draw_glow_text(p: QPainter, rect: QRectF, flags, text: str, font: QFont, color: str,
                   alpha: float = 1.0, glow: float = 0.6, glow_color: str | None = None,
                   spread: float = 1.6) -> None:
    """Text with a cheap four-tap halo — fine for terminal-sized text."""
    if alpha <= 0.01 or not text:
        return
    glow_color = glow_color or ACCENT
    p.save()
    p.setFont(font)
    if glow > 0.01:
        p.setCompositionMode(PLUS)
        p.setPen(rgba(glow_color, 0.24 * glow * alpha))
        for dx, dy in ((-spread, 0), (spread, 0), (0, -spread), (0, spread)):
            p.drawText(rect.translated(dx, dy), flags, text)
        p.setCompositionMode(OVER)
    p.setPen(rgba(color, alpha))
    p.drawText(rect, flags, text)
    p.restore()


def font(family: str, px: float, weight=QFont.Weight.Normal, spacing: float = 0.0) -> QFont:
    f = QFont(family)
    f.setPixelSize(max(6, int(px)))
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


class Shake:
    """Camera shake: a decaying random offset applied to the whole frame."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.amp = 0.0
        self.left = 0.0
        self.dur = 1.0

    def kick(self, amp: float, dur: float) -> None:
        if amp * 1.0 >= self.current():
            self.amp, self.left, self.dur = amp, dur, max(1e-3, dur)

    def current(self) -> float:
        return self.amp * (self.left / self.dur) ** 2 if self.left > 0 else 0.0

    def step(self, dt: float) -> None:
        self.left = max(0.0, self.left - dt)

    def offset(self) -> tuple[float, float]:
        a = self.current()
        if a < 0.2:
            return 0.0, 0.0
        return self.rng.uniform(-a, a), self.rng.uniform(-a, a) * 0.7


# ── Wordmark ──────────────────────────────────────────────────────────────────

class LogoArt:
    """The U L T R O N wordmark: letters as vector paths (crisp at any size,
    fillable with gradients, sliceable), each with its own pre-baked bloom so
    one letter can blaze while its neighbour is still dark."""

    def __init__(self, text: str = "ULTRON"):
        self.text = (text or "ULTRON").upper()[:12]
        self._key = None
        self.paths: list[QPainterPath] = []
        self.rects: list[QRectF] = []
        self.glows: list[tuple[QRectF, QPixmap, QPixmap]] = []
        self.bounds = QRectF()
        self.size = 0.0

    def layout(self, cx: float, cy: float, size: float, max_width: float, dpr: float = 1.0) -> None:
        f = font(FONT_HEAD, size, QFont.Weight.Bold)
        fm = QFontMetricsF(f)
        n = len(self.text)
        gap = size * 0.40
        widths = [fm.horizontalAdvance(ch) for ch in self.text]
        total = sum(widths) + gap * (n - 1)
        if total > max_width and total > 0:
            k = max_width / total
            size *= k
            f = font(FONT_HEAD, size, QFont.Weight.Bold)
            fm = QFontMetricsF(f)
            gap = size * 0.40
            widths = [fm.horizontalAdvance(ch) for ch in self.text]
            total = sum(widths) + gap * (n - 1)
        key = (round(cx), round(cy), round(size), round(dpr, 2), self.text, ACCENT)
        if key == self._key:
            return
        self._key = key
        self.size = size
        x = cx - total / 2
        baseline = cy + fm.capHeight() / 2
        self.paths, self.rects, self.glows = [], [], []
        bounds = QRectF()
        m = size * 0.45
        for ch, w in zip(self.text, widths):
            path = QPainterPath()
            path.addText(QPointF(x, baseline), f, ch)
            # Variable fonts (Bahnschrift is one) build glyphs from overlapping
            # contours; under Qt's default odd-even fill the overlaps punch
            # holes. Merge them under the winding rule (simplified() merges by
            # the path's own rule) into one clean outline to fill and stroke.
            path.setFillRule(Qt.FillRule.WindingFill)
            path = path.simplified()
            r = path.boundingRect()
            self.paths.append(path)
            self.rects.append(r)
            bounds = bounds.united(r)
            gr = r.adjusted(-m, -m, m, m)
            self.glows.append((gr, self._glow(path, gr, ACCENT, 9, dpr),
                               self._glow(path, gr, CORE_WHITE, 4, dpr)))
            x += w + gap
        self.bounds = bounds

    @staticmethod
    def _glow(path: QPainterPath, r: QRectF, color: str, factor: int, dpr: float) -> QPixmap:
        pm = QPixmap(max(2, int(r.width() * dpr)), max(2, int(r.height() * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.translate(-r.left(), -r.top())
        q.fillPath(path, QColor(color))
        q.setPen(QPen(QColor(color), max(1.0, r.height() * 0.02)))
        q.drawPath(path)
        q.end()
        return bloom(pm, factor)

    def draw_letter(self, p: QPainter, i: int, alpha: float, heat: float = 0.0,
                    glow: float = 0.6, scale: float = 1.0, dx: float = 0.0) -> None:
        if alpha <= 0.01 or i >= len(self.paths):
            return
        path, r = self.paths[i], self.rects[i]
        gr, g_wide, g_tight = self.glows[i]
        p.save()
        if dx:
            p.translate(dx, 0)
        if scale != 1.0:
            c = r.center()
            p.translate(c)
            p.scale(scale, scale)
            p.translate(-c)
        # Bloom behind and over the letter (additive).
        p.setCompositionMode(PLUS)
        p.setOpacity(clamp(alpha * glow))
        p.drawPixmap(gr, g_wide, QRectF(g_wide.rect()))
        if heat > 0.02:
            p.setOpacity(clamp(alpha * heat))
            p.drawPixmap(gr, g_tight, QRectF(g_tight.rect()))
            p.drawPixmap(gr, g_wide, QRectF(g_wide.rect()))
        p.setOpacity(1.0)
        p.setCompositionMode(OVER)
        # Body: white-hot when struck, settling to white over crimson.
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0.0, rgba(mix(mix(ACCENT, "#ffffff", 0.88), "#ffffff", heat), alpha))
        g.setColorAt(0.52, rgba(mix(mix(ACCENT, "#ffffff", 0.48), "#ffffff", heat), alpha))
        g.setColorAt(0.53, rgba(mix(ACCENT, CORE_WHITE, heat), alpha))
        g.setColorAt(1.0, rgba(mix(mix(ACCENT, "#000000", 0.38), mix(ACCENT, "#ffffff", 0.82), heat), alpha))
        p.fillPath(path, QBrush(g))
        p.setPen(QPen(rgba(mix(HOT, "#ffffff", heat), 0.75 * alpha), max(1.0, self.size * 0.012)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.restore()

    def letter_center(self, i: int) -> QPointF:
        return self.rects[i].center() if i < len(self.rects) else self.bounds.center()
