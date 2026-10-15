import sys
from PyQt6.QtWidgets import QApplication, QWidget, QMainWindow
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush
from PyQt6.QtCore import Qt, QRectF
import math, random

class TestCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self._amp_disp = 0.5
        self.speaking = True
        self.state = "THINKING"
        self._assistant_name = "CAR"
        
    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#030406"))
        cx, cy = self.width() / 2, self.height() / 2
        r = 150
        W, H = self.width(), self.height()
        _main = QColor("#00f0ff")
        _acc = QColor("#ff0055")
        
        self._paint_earth(p, cx, cy, r, W, H, _main, _acc)
        
    def _paint_earth(self, p, cx, cy, r, W, H, main_col, acc_col):
        if not hasattr(self, '_asteroids'):
            self._asteroids = []
            for _ in range(30):
                dist = random.uniform(1.15, 2.2)
                angle = random.uniform(0, 360)
                speed = random.uniform(0.5, 2.5) * random.choice([-1, 1])
                size = random.uniform(2, 5)
                self._asteroids.append([dist, angle, speed, size])
            self._earth_phase = 0.0

        amp = self._amp_disp
        live = self.speaking or self.state in ("THINKING", "PROCESSING")
        
        pulse_r = r + (r * 0.15 * amp)
        
        halo_glow = QColor(acc_col)
        halo_glow.setAlpha(int(20 + 80 * amp))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(halo_glow))
        p.drawEllipse(QRectF(cx - pulse_r*1.15, cy - pulse_r*1.15, pulse_r*2.3, pulse_r*2.3))
        
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(main_col.red(), main_col.green(), main_col.blue(), 100), 1.2))
        for i in range(-5, 6):
            h_y = pulse_r * (i / 6.0)
            val = pulse_r*pulse_r - h_y*h_y
            if val > 0:
                w_slice = math.sqrt(val)
                p.drawEllipse(QRectF(cx - w_slice, cy + h_y - w_slice*0.2, w_slice*2, w_slice*0.4))

        speed_mult = 3.0 if self.speaking else (5.0 if self.state in ("THINKING", "PROCESSING") else 0.5)
        self._earth_phase += speed_mult
        
        for i in range(12):
            angle_rad = math.radians(self._earth_phase + i * 30.0)
            cos_a = math.cos(angle_rad)
            sin_a = math.sin(angle_rad)
            w = pulse_r * cos_a
            alpha = int(180) if sin_a > 0 else int(30)
            p.setPen(QPen(QColor(main_col.red(), main_col.green(), main_col.blue(), alpha), 1.5))
            p.drawEllipse(QRectF(cx - abs(w), cy - pulse_r, abs(w)*2, pulse_r*2))

        p.setPen(Qt.PenStyle.NoPen)
        for ast in self._asteroids:
            dist_mult, angle, speed, size = ast
            ast[1] = (angle + speed * speed_mult) % 360
            ast_r = pulse_r * dist_mult
            rad = math.radians(ast[1])
            ax = cx + ast_r * math.cos(rad)
            ay = cy + ast_r * math.sin(rad) * 0.3
            is_front = math.sin(rad) > 0
            ast_alpha = 255 if is_front else 60
            ast_size = size * (1.2 if is_front else 0.6)
            p.setBrush(QBrush(QColor(acc_col.red(), acc_col.green(), acc_col.blue(), ast_alpha)))
            p.drawEllipse(QRectF(ax - ast_size/2, ay - ast_size/2, ast_size, ast_size))
            
        name = self._assistant_name or "CAR"
        space = max(1.0, r * 0.018)
        fsz = max(8, int(min(r * 0.105, (r * 1.75) / max(1, len(name)) * 1.6 - space)))
        from PyQt6.QtGui import QFont
        f = QFont("Courier New", fsz, QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, space)
        p.setFont(f)
        p.setPen(QPen(QColor("#ffffff"), 1))
        p.drawText(QRectF(cx - r, cy + pulse_r + 20, r * 2, fsz * 2), Qt.AlignmentFlag.AlignCenter, name)

app = QApplication(sys.argv)
win = QMainWindow()
canvas = TestCanvas()
win.setCentralWidget(canvas)
win.show()
print("Starting UI...")
# don't exec, just let python print "Done" if it doesn't crash during show
win.repaint()
print("Repaint done!")
