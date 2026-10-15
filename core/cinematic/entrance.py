"""
core/cinematic/entrance.py — the cinematic hands the screen back to the real UI.

Both intros (the classic boot and the narrated welcome) end the same way: the
actual dashboard, captured as a picture a moment earlier, assembles panel by
panel — each sliding in with an accent frame and a scan bar — while a
holographic wipe runs down the window and the wordmark's letters fly into the
header title. When it is complete the overlay disappears over the live UI,
which is pixel-for-pixel what the last frame showed.

`snap` is what MainWindow._fx_dashboard_snapshot returns:
    (pixmap, rect of the pixmap, [(name, rect, (dx, dy)), …], title rect | None)
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen

from . import fx
from .fx import PLUS, VOID, bell, ease_in_out_cubic, ease_out_cubic, lerp, rgba, seg

PIECE_DELAY = {"left": 0.04, "right": 0.12, "center": 0.18, "footer": 0.26, "header": 0.32}


def draw(p: QPainter, snap, k: float, W: float, H: float, s: float, reduced: bool,
         logo: fx.LogoArt | None = None) -> None:
    """Frame `k` (0…1) of the entrance."""
    full = QRectF(0, 0, W, H)
    if snap is None:
        p.fillRect(full, rgba(VOID, 1 - ease_in_out_cubic(k)))
        return
    pm, prect, pieces, title = snap
    p.fillRect(full, QColor(VOID))
    dpr = pm.devicePixelRatio() or 1.0

    def src(r: QRectF) -> QRectF:
        return QRectF((r.left() - prect.left()) * dpr, (r.top() - prect.top()) * dpr,
                      r.width() * dpr, r.height() * dpr)

    base = ease_in_out_cubic(seg(k, 0.45, 1.0))
    if base > 0:
        p.setOpacity(base)
        p.drawPixmap(prect, pm, QRectF(pm.rect()))
        p.setOpacity(1.0)

    for name, r, (ddx, ddy) in pieces:
        d0 = PIECE_DELAY.get(name, 0.2)
        pk = ease_out_cubic(seg(k, d0, d0 + 0.42))
        if pk <= 0:
            continue
        off = 0.0 if reduced else (1 - pk) * 38 * s
        dst = r.translated(ddx * off, ddy * off)
        p.setOpacity(pk)
        p.drawPixmap(dst, pm, src(r))
        p.setOpacity(1.0)
        edge = bell(seg(k, d0, d0 + 0.55)) * 0.85
        if edge > 0.01:
            p.setPen(QPen(rgba(fx.ACCENT, edge), max(1.0, 1.2 * s)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(dst.adjusted(0.5, 0.5, -0.5, -0.5))
            fx.draw_corner_brackets(p, dst.adjusted(-2 * s, -2 * s, 2 * s, 2 * s), 14 * s, edge, 2 * s)
            fx.draw_scan_bar(p, dst, pk, 70 * s, edge)

    # holographic wipe line down the whole window
    wk = seg(k, 0.0, 0.7)
    if 0 < wk < 1:
        y = ease_in_out_cubic(wk) * H
        p.save()
        p.setCompositionMode(PLUS)
        p.fillRect(QRectF(0, y - 1.2 * s, W, 2.4 * s), rgba(fx.HOT, 0.55 * bell(wk)))
        p.restore()

    # The wordmark flies into the header title, each letter to its own slot —
    # the wide-tracked logo and the tight header label only line up if the
    # letters close ranks on the way.
    la = 1 - seg(k, 0.44, 0.66)
    if logo is not None and la > 0 and logo.paths:
        n = len(logo.paths)
        b = logo.bounds
        have_title = title is not None and not title.isEmpty() and b.height() > 0
        sc_end = (title.height() * 0.62) / b.height() if have_title else 1.0
        for i in range(n):
            c = logo.rects[i].center()
            if have_title:
                ex = title.left() + (i + 0.5) / n * title.width()
                ey = title.center().y()
            else:
                ex, ey = c.x(), c.y()
            li = ease_in_out_cubic(seg(k, 0.03 * i, 0.50 + 0.03 * i))   # leading letters leave first
            p.save()
            p.translate(lerp(c.x(), ex, li), lerp(c.y(), ey, li))
            sc = lerp(1.0, sc_end, li)
            p.scale(sc, sc)
            p.translate(-c.x(), -c.y())
            logo.draw_letter(p, i, la, 0.15 * (1 - li), 0.6 * (1 - 0.5 * li))
            p.restore()
