"""
Animated Screen Arrow & HUD Overlay for Ultron.

Displays a futuristic, translucent, click-through overlay showing an animated
bouncing neon arrow, pulsing reticle rings, and a target HUD card pointing
directly at any screen coordinate (x, y).

Thread-safe and automatically closes after a specified duration.
"""
from __future__ import annotations

import math
import sys
import time
from typing import Optional, List

from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QPolygonF, QFont,
    QLinearGradient, QPainterPath
)


class AnimatedScreenArrowOverlay(QWidget):
    """
    Transparent full-screen HUD overlay showing an animated bouncing arrow,
    pulsing radar rings, and a target HUD card pointing at (target_x, target_y).
    Automatically disappears after `duration` seconds.
    """
    def __init__(
        self,
        target_x: int,
        target_y: int,
        label: str = "SETTINGS",
        bounding_box: Optional[List[int]] = None,
        duration: float = 5.0,
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self.target_x = float(target_x)
        self.target_y = float(target_y)
        self.label = (label or "TARGET").strip()
        self.bounding_box = bounding_box
        self.duration = max(1.0, float(duration))
        self.start_time = time.monotonic()
        self.phase = 0.0

        # Window styling: transparent, click-through, always on top, tool window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Cover entire screen that contains the target
        self._position_on_screen()

        self.timer = QTimer(self)
        self.timer.setInterval(16)  # ~60 FPS
        self.timer.timeout.connect(self._on_tick)
        self.timer.start()

    def _position_on_screen(self):
        screens = QApplication.screens()
        chosen_screen = QApplication.primaryScreen()
        for scr in screens:
            geom = scr.geometry()
            if geom.contains(int(self.target_x), int(self.target_y)):
                chosen_screen = scr
                break
        if chosen_screen:
            self.setGeometry(chosen_screen.geometry())

    def _on_tick(self):
        self.phase += 0.08
        elapsed = time.monotonic() - self.start_time
        if elapsed >= self.duration:
            self.timer.stop()
            self.close()
            self.deleteLater()
            return
        self.update()

    def paintEvent(self, event):
        elapsed = time.monotonic() - self.start_time
        if elapsed >= self.duration:
            return

        # Alpha animation: smooth fade in (0.3s) and smooth fade out (0.7s)
        if elapsed < 0.35:
            alpha_factor = elapsed / 0.35
        elif elapsed > (self.duration - 0.7):
            alpha_factor = max(0.0, (self.duration - elapsed) / 0.7)
        else:
            alpha_factor = 1.0

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # Convert global coordinates to widget coordinates
        global_target = QPointF(self.target_x, self.target_y)
        widget_target = self.mapFromGlobal(global_target.toPoint())
        tx = float(widget_target.x())
        ty = float(widget_target.y())

        accent_cyan = QColor(0, 240, 255, int(255 * alpha_factor))
        white_glow  = QColor(255, 255, 255, int(245 * alpha_factor))

        # ── 1. Reticle & Target Rings ────────────────────────────────────
        # Expanding radar ripples
        for ring_offset in (0, 20):
            r = 14.0 + ((self.phase * 22.0 + ring_offset) % 45.0)
            ring_alpha = int(alpha_factor * 220 * max(0.0, 1.0 - (r / 55.0)))
            p.setPen(QPen(QColor(0, 240, 255, ring_alpha), 1.8))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(tx, ty), r, r)

        # Center target dot & inner ring
        p.setPen(QPen(accent_cyan, 2.0))
        p.drawEllipse(QPointF(tx, ty), 12.0, 12.0)
        p.setBrush(QBrush(accent_cyan))
        p.drawEllipse(QPointF(tx, ty), 3.0, 3.0)

        # Reticle crosshair ticks
        p.setPen(QPen(accent_cyan, 1.5))
        p.drawLine(QPointF(tx - 22, ty), QPointF(tx - 14, ty))
        p.drawLine(QPointF(tx + 14, ty), QPointF(tx + 22, ty))
        p.drawLine(QPointF(tx, ty - 22), QPointF(tx, ty - 14))
        p.drawLine(QPointF(tx, ty + 14), QPointF(tx, ty + 22))

        # Bounding box brackets (if provided)
        if self.bounding_box and len(self.bounding_box) == 4:
            bx1, by1, bx2, by2 = self.bounding_box
            p1 = self.mapFromGlobal(QPointF(bx1, by1).toPoint())
            p2 = self.mapFromGlobal(QPointF(bx2, by2).toPoint())
            x1, y1 = float(p1.x()), float(p1.y())
            x2, y2 = float(p2.x()), float(p2.y())
            bw = x2 - x1
            bh = y2 - y1
            if bw > 10 and bh > 10:
                p.setPen(QPen(QColor(0, 255, 200, int(210 * alpha_factor)), 1.8))
                corn = min(14.0, min(bw, bh) / 3.0)
                # 4 corners
                p.drawLine(QPointF(x1, y1), QPointF(x1 + corn, y1))
                p.drawLine(QPointF(x1, y1), QPointF(x1, y1 + corn))
                p.drawLine(QPointF(x2, y1), QPointF(x2 - corn, y1))
                p.drawLine(QPointF(x2, y1), QPointF(x2, y1 + corn))
                p.drawLine(QPointF(x1, y2), QPointF(x1 + corn, y2))
                p.drawLine(QPointF(x1, y2), QPointF(x1, y2 - corn))
                p.drawLine(QPointF(x2, y2), QPointF(x2 - corn, y2))
                p.drawLine(QPointF(x2, y2), QPointF(x2, y2 - corn))

        # ── 2. Bouncing Arrow ────────────────────────────────────────────
        points_down = (ty >= 180)
        bounce = abs(math.sin(self.phase * 4.2)) * 15.0

        if points_down:
            tip_x = tx
            tip_y = ty - 24.0 - bounce
            base_y = tip_y - 45.0
            arrow_poly = QPolygonF([
                QPointF(tip_x, tip_y),
                QPointF(tip_x - 18, tip_y - 25),
                QPointF(tip_x - 8, tip_y - 22),
                QPointF(tip_x - 8, base_y),
                QPointF(tip_x + 8, base_y),
                QPointF(tip_x + 8, tip_y - 22),
                QPointF(tip_x + 18, tip_y - 25),
            ])
            card_y = base_y - 52.0
        else:
            tip_x = tx
            tip_y = ty + 24.0 + bounce
            base_y = tip_y + 45.0
            arrow_poly = QPolygonF([
                QPointF(tip_x, tip_y),
                QPointF(tip_x - 18, tip_y + 25),
                QPointF(tip_x - 8, tip_y + 22),
                QPointF(tip_x - 8, base_y),
                QPointF(tip_x + 8, base_y),
                QPointF(tip_x + 8, tip_y + 22),
                QPointF(tip_x + 18, tip_y + 25),
            ])
            card_y = base_y + 8.0

        # Draw dark shadow / glow behind arrow for high contrast
        p.setPen(QPen(QColor(0, 0, 0, int(190 * alpha_factor)), 6))
        p.setBrush(QBrush(QColor(0, 10, 20, int(220 * alpha_factor))))
        p.drawPolygon(arrow_poly)

        # Gradient fill for arrow
        grad = QLinearGradient(QPointF(tip_x, tip_y), QPointF(tip_x, base_y))
        grad.setColorAt(0.0, QColor(0, 240, 255, int(255 * alpha_factor)))
        grad.setColorAt(0.5, QColor(0, 160, 255, int(235 * alpha_factor)))
        grad.setColorAt(1.0, QColor(0, 90, 220, int(210 * alpha_factor)))
        p.setPen(QPen(white_glow, 2))
        p.setBrush(QBrush(grad))
        p.drawPolygon(arrow_poly)

        # ── 3. HUD Target Card ───────────────────────────────────────────
        card_w = 230.0
        card_h = 46.0
        card_x = max(10.0, min(float(self.width() - card_w - 10), tip_x - card_w / 2.0))
        card_rect = QRectF(card_x, card_y, card_w, card_h)

        # Card shadow
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(0, 0, 0, int(170 * alpha_factor))))
        p.drawRoundedRect(card_rect.translated(2, 3), 6, 6)

        # Card body
        p.setBrush(QBrush(QColor(2, 12, 22, int(235 * alpha_factor))))
        p.setPen(QPen(accent_cyan, 1.5))
        p.drawRoundedRect(card_rect, 6, 6)

        # Card header accent bar
        p.setBrush(QBrush(accent_cyan))
        p.drawRect(QRectF(card_x + 6, card_y + 6, 3, card_h - 12))

        # Text inside HUD Card
        p.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        p.setPen(QPen(white_glow))
        title_text = f"◈ ULTRON: {self.label.upper()[:16]}"
        p.drawText(QRectF(card_x + 16, card_y + 7, card_w - 20, 16), Qt.AlignmentFlag.AlignLeft, title_text)

        p.setFont(QFont("Consolas", 7))
        p.setPen(QPen(QColor(0, 210, 255, int(220 * alpha_factor))))
        sub_text = f"TARGET LOCATED  [X:{int(self.target_x)} Y:{int(self.target_y)}]"
        p.drawText(QRectF(card_x + 16, card_y + 24, card_w - 20, 15), Qt.AlignmentFlag.AlignLeft, sub_text)

        p.end()
