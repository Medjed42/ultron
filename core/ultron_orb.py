"""ULTRON multi-mode particle orb (third-party notices: THIRD_PARTY_NOTICES/README.md).

Native high-performance PyQt6/NumPy adaptation rendered in vibrant orange.
Features:
- Floating 3D particle sphere with multi-harmonic drift physics
- Central attractor force bounding particles into a dynamic sphere
- Dynamic neural connection lines between nearby particles with opacity fading
- High-energy electron sparks travelling along connection pathways during thinking/active states
- State-change tumble transitions with decaying rotational momentum
- 3D Z-depth breathing responsive to assistant states and speech bass frequencies
- Audio reactivity (bass expansion and mid-frequency pulse waves during speech)
- Complete 5-state lifecycle: IDLE, LISTENING, THINKING, SPEAKING, COMPACTING/MUTED
- Radiant fiery orange palette with glowing electric amber transitions
- Interactive 3D drag rotation, mouse wheel zoom, and double-click reset
"""

from __future__ import annotations

import math
import random
import time
import numpy as np
from PyQt6.QtCore import QLineF, QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QColor, QFont, QPainter, QPainterPath, QPen, QRadialGradient,
)


class UltronOrb:
    """Multi-mode particle orb."""

    # Orange Palette (RGB)
    COLOR_IDLE = np.array([255.0, 140.0, 20.0], dtype=np.float32)       # Warm radiant orange
    COLOR_LISTENING = np.array([255.0, 175.0, 35.0], dtype=np.float32)  # Glowing amber orange
    COLOR_THINKING = np.array([255.0, 215.0, 65.0], dtype=np.float32)   # Electric golden orange
    COLOR_SPEAKING = np.array([255.0, 95.0, 0.0], dtype=np.float32)     # Fiery vibrant orange
    COLOR_COMPACTING = np.array([140.0, 55.0, 15.0], dtype=np.float32)  # Dim ember rust

    def __init__(self, num_particles: int = 1200):
        self.N = num_particles
        self.yaw = 0.0
        self.pitch = -0.28
        self.zoom = 1.0

        # Spherical particle distribution
        # r = Math.pow(Math.random(), 0.5) * 25
        theta = np.random.uniform(0.0, 2.0 * math.pi, self.N).astype(np.float32)
        phi = np.arccos(np.random.uniform(-1.0, 1.0, self.N)).astype(np.float32)
        r = (np.random.uniform(0.0, 1.0, self.N) ** 0.5 * 25.0).astype(np.float32)
        sin_phi = np.sin(phi)

        self.pos = np.column_stack([
            r * sin_phi * np.cos(theta),
            r * sin_phi * np.sin(theta),
            r * np.cos(phi)
        ]).astype(np.float32)

        self.vel = np.zeros((self.N, 3), dtype=np.float32)
        self.phase = np.random.uniform(0.0, 1000.0, self.N).astype(np.float32)

        # State and dynamic variables
        self.state = "idle"
        self.last_state = "idle"
        self.target_radius = 28.0
        self.current_radius = 28.0
        self.target_speed = 0.2
        self.current_speed = 0.2
        self.target_bright = 0.50
        self.current_bright = 0.50
        self.target_size = 0.35
        self.current_size = 0.35
        self.line_amount = 0.15
        self.target_line_amount = 0.15
        self.line_distance = 7.5
        self.electron_spawn_rate = 0.0
        self.target_electron_rate = 0.0

        # State tumble energy
        self.spin_x = 0.0
        self.spin_y = 0.0
        self.spin_z = 0.0
        self.transition_energy = 0.0

        # Z-depth breathing
        self.cloud_z = 0.0
        self.cloud_z_vel = 0.0

        # Active electrons traveling along connection lines
        # Dicts: {"sx": float, "sy": float, "sz": float, "ex": float, "ey": float, "ez": float, "t": float, "speed": float}
        self.active_electrons: list[dict] = []
        self.last_electron_spawn = 0.0

        # Active connection endpoints in 3D for electron spawning
        self.active_connections: list[tuple[np.ndarray, np.ndarray]] = []

        # Color interpolation
        self.current_color = self.COLOR_IDLE.copy()
        self._spark = ((255, 130, 20), (255, 210, 60))      # electron halo, corona

    def set_accent(self, hex_color: str | None) -> None:
        """Re-tint the orb for a UI theme: every state colour keeps its
        brightness and saturation relative to the original orange design and
        moves to the theme's hue (a white theme turns it silver). None restores
        the original orange."""
        import colorsys
        base = {
            "COLOR_IDLE": UltronOrb.COLOR_IDLE, "COLOR_LISTENING": UltronOrb.COLOR_LISTENING,
            "COLOR_THINKING": UltronOrb.COLOR_THINKING, "COLOR_SPEAKING": UltronOrb.COLOR_SPEAKING,
            "COLOR_COMPACTING": UltronOrb.COLOR_COMPACTING,
        }
        if not hex_color:
            for k, v in base.items():
                setattr(self, k, v.copy())
            self._spark = ((255, 130, 20), (255, 210, 60))
            return
        h = hex_color.lstrip("#")
        th, ts, _tv = colorsys.rgb_to_hsv(int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)
        ref_h = colorsys.rgb_to_hsv(*(UltronOrb.COLOR_IDLE / 255.0))[0]

        def shift(rgb) -> np.ndarray:
            hh, ss, vv = colorsys.rgb_to_hsv(*(np.asarray(rgb, dtype=np.float64) / 255.0))
            ss = ss * 0.12 if ts < 0.08 else ss
            r, g, b = colorsys.hsv_to_rgb((hh - ref_h + th) % 1.0, ss, vv)
            return np.array([r * 255, g * 255, b * 255], dtype=np.float32)

        for k, v in base.items():
            setattr(self, k, shift(v))
        self._spark = tuple(tuple(int(c) for c in shift(rgb)) for rgb in ((255, 130, 20), (255, 210, 60)))

        # Timing
        self._last_perf = time.perf_counter()
        self._elapsed = 0.0

    def rotate(self, dx: float, dy: float) -> None:
        """Rotate the orb via mouse drag."""
        self.yaw += dx * 0.008
        self.pitch = max(-1.2, min(1.2, self.pitch + dy * 0.008))

    def reset(self) -> None:
        """Reset view orientation and zoom to defaults."""
        self.yaw = 0.0
        self.pitch = -0.28
        self.zoom = 1.0
        self.spin_x = 0.0
        self.spin_y = 0.0
        self.spin_z = 0.0

    def _map_state(self, state_str: str, speaking: bool = False, muted: bool = False) -> str:
        """Map Ultron/Ultron status to the 5 Ethan orb states:
        idle | listening | thinking | speaking | compacting
        """
        if muted:
            return "compacting"
        if speaking:
            return "speaking"
        if not state_str:
            return "idle"

        s = str(state_str).lower().strip()
        if "speak" in s or "talk" in s:
            return "speaking"
        if "think" in s or "process" in s or "search" in s:
            return "thinking"
        if "listen" in s or "hear" in s or "record" in s:
            return "listening"
        if "mute" in s or "compact" in s or "sleep" in s:
            return "compacting"
        return "idle"

    def update_physics(self, dt: float, amplitude: float, state_str: str,
                       speaking: bool = False, muted: bool = False) -> None:
        """Advance the physics simulation."""
        dt = max(0.001, min(0.066, dt))
        self._elapsed += dt
        t = self._elapsed

        st = self._map_state(state_str, speaking, muted)
        self.state = st

        # State targets
        if st == "idle":
            self.target_radius = 28.0
            self.target_speed = 0.20
            self.target_bright = 0.50
            self.target_size = 0.35
            self.target_line_amount = 0.15
            self.target_electron_rate = 0.0
            tgt_col = self.COLOR_IDLE
        elif st == "listening":
            self.target_radius = 22.0
            self.target_speed = 0.30
            self.target_bright = 0.65
            self.target_size = 0.40
            self.target_line_amount = 0.40
            self.target_electron_rate = 0.0
            tgt_col = self.COLOR_LISTENING
        elif st == "thinking":
            self.target_radius = 16.0
            self.target_speed = 0.50
            self.target_bright = 0.70
            self.target_size = 0.30
            self.target_line_amount = 1.00
            self.target_electron_rate = 0.015
            tgt_col = self.COLOR_THINKING
        elif st == "speaking":
            self.target_radius = 18.0
            self.target_speed = 0.22
            self.target_bright = 0.75
            self.target_size = 0.42
            self.target_line_amount = 0.80
            self.target_electron_rate = 0.0
            tgt_col = self.COLOR_SPEAKING
        elif st == "compacting":
            self.target_radius = 12.0
            self.target_speed = 0.08
            self.target_bright = 0.28
            self.target_size = 0.28
            self.target_line_amount = 0.0
            self.target_electron_rate = 0.0
            tgt_col = self.COLOR_COMPACTING
        else:
            tgt_col = self.COLOR_IDLE

        # Damped interpolation
        rate = 0.035
        self.current_radius += (self.target_radius - self.current_radius) * rate
        self.current_speed += (self.target_speed - self.current_speed) * rate
        self.current_bright += (self.target_bright - self.current_bright) * rate
        self.current_size += (self.target_size - self.current_size) * rate
        self.line_amount += (self.target_line_amount - self.line_amount) * rate
        self.electron_spawn_rate += (self.target_electron_rate - self.electron_spawn_rate) * rate
        self.current_color += (tgt_col - self.current_color) * rate

        # Transition tumble impulse
        if st != self.last_state:
            self.transition_energy = 1.0
            self.last_state = st
        self.transition_energy *= 0.985
        if self.transition_energy > 0.05:
            self.spin_x += self.transition_energy * 0.012 * math.sin(t * 1.7)
            self.spin_y += self.transition_energy * 0.015
            self.spin_z += self.transition_energy * 0.008 * math.cos(t * 1.3)

        # Audio frequencies
        amp = max(0.0, min(1.0, float(amplitude)))
        bass = amp * 0.95
        mid = amp * 1.15

        # Depth Z breathing
        if st == "thinking":
            z_target = math.sin(t * 0.3) * 15.0 + math.sin(t * 0.9) * 6.0
        elif st == "speaking":
            z_target = math.sin(t * 0.15) * 6.0 - bass * 10.0
        elif st == "compacting":
            z_target = -20.0 + math.sin(t * 0.08) * 3.0
        else:
            z_target = math.sin(t * 0.12) * 8.0

        self.cloud_z_vel += (z_target - self.cloud_z) * 0.008
        self.cloud_z_vel *= 0.94
        self.cloud_z += self.cloud_z_vel

        # ── Vectorized Particle Update ──
        px = self.phase
        cur_spd = self.current_speed

        self.vel[:, 0] += (np.sin(t * 0.05 + px) * 0.001 * cur_spd +
                           np.sin(t * 0.02 + px * 2.1 + self.pos[:, 1] * 0.1) * 0.0008 * cur_spd)
        self.vel[:, 1] += (np.cos(t * 0.06 + px * 1.3) * 0.001 * cur_spd +
                           np.cos(t * 0.025 + px * 1.7 + self.pos[:, 2] * 0.1) * 0.0008 * cur_spd)
        self.vel[:, 2] += (np.sin(t * 0.055 + px * 0.7) * 0.001 * cur_spd +
                           np.sin(t * 0.022 + px * 0.9 + self.pos[:, 0] * 0.1) * 0.0008 * cur_spd)

        # Distance from origin
        dist = np.linalg.norm(self.pos, axis=1, keepdims=True)
        dist = np.maximum(dist, 0.01)
        unit = self.pos / dist

        # Center pull
        pull = np.maximum(0.0, dist - self.current_radius) * 0.002 + 0.0003
        self.vel -= unit * pull

        # Audio response
        if bass > 0.05:
            self.vel += unit * (bass * 0.02)
        if st == "speaking" and mid > 0.08:
            pulse = np.sin(t * 8.0 + px)[:, None]
            self.vel[:, :2] += unit[:, :2] * (mid * 0.012 * pulse)

        self.vel *= 0.992
        self.pos += self.vel

        # ── Update Connections ──
        self.active_connections = []
        if self.line_amount > 0.01:
            step = max(1, self.N // 300)
            sub = self.pos[::step]
            diff = sub[:, None, :] - sub[None, :, :]
            dist_sq = (diff ** 2).sum(axis=-1)
            max_d = self.line_distance * (1.0 + bass * 0.5)
            max_d_sq = max_d * max_d

            i_idx, j_idx = np.triu_indices(len(sub), k=1)
            mask = (dist_sq[i_idx, j_idx] < max_d_sq) & (dist_sq[i_idx, j_idx] > 0.01)
            sel_i = i_idx[mask]
            sel_j = j_idx[mask]

            if len(sel_i) > 500:
                sel_i = sel_i[:500]
                sel_j = sel_j[:500]

            pts1 = sub[sel_i]
            pts2 = sub[sel_j]
            self.active_connections = list(zip(pts1, pts2))

            # ── Spawn Electrons (Thinking / high activity) ──
            if self.electron_spawn_rate > 0.005 and len(self.active_connections) > 0:
                if len(self.active_electrons) < 4 and (t - self.last_electron_spawn) > 0.8:
                    idx = random.randint(0, len(self.active_connections) - 1)
                    p1, p2 = self.active_connections[idx]
                    self.active_electrons.append({
                        "sx": float(p1[0]), "sy": float(p1[1]), "sz": float(p1[2]),
                        "ex": float(p2[0]), "ey": float(p2[1]), "ez": float(p2[2]),
                        "t": 0.0,
                        "speed": random.uniform(0.008, 0.018),
                    })
                    self.last_electron_spawn = t

        # ── Update Active Electrons ──
        for el in list(self.active_electrons):
            el["t"] += el["speed"]
            if el["t"] >= 1.0:
                self.active_electrons.remove(el)

    def paint(self, p: QPainter, cx: float, cy: float, radius: float,
              phase: float, amplitude: float, color=None, state: str = "idle",
              speaking: bool | None = None, muted: bool | None = None) -> None:
        """Render the 3D orange particle orb onto the Qt painter."""
        # Calculate precise delta time
        now = time.perf_counter()
        dt = now - self._last_perf
        self._last_perf = now

        is_speaking = bool(speaking) or (state.upper() == "SPEAKING")
        is_muted = bool(muted) or (state.upper() in ("MUTED", "COMPACTING"))
        self.update_physics(dt, amplitude, state, is_speaking, is_muted)

        t = self._elapsed
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Target colors from current animated state
        cr, cg, cb = self.current_color[0], self.current_color[1], self.current_color[2]
        bass = min(1.0, float(amplitude) * 1.1)

        # ── 1. Radial Ambient Aura (Warm Deep Orange) ──
        effective_r = radius * 0.95 * self.zoom * (1.0 + bass * 0.06)
        glow_r = effective_r * 1.75
        glow = QRadialGradient(QPointF(cx, cy), glow_r)

        base_alpha = int(max(15, min(160, (45 + 75 * amplitude) * (self.current_bright / 0.5))))
        glow.setColorAt(0.0, QColor(int(cr), int(cg), int(cb), base_alpha))
        # Darker toward the rim, same hue: scaling every channel alike keeps a
        # themed orb from shifting colour in its own glow.
        glow.setColorAt(0.35, QColor(int(cr * 0.85), int(cg * 0.70), int(cb * 0.70), int(base_alpha * 0.55)))
        glow.setColorAt(0.68, QColor(int(cr * 0.60), int(cg * 0.42), int(cb * 0.42), int(base_alpha * 0.18)))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), glow_r, glow_r)

        # ── 2. 3D Rotation Matrix ──
        tot_yaw = self.yaw + self.spin_y + t * 0.035
        tot_pitch = self.pitch + self.spin_x
        tot_roll = self.spin_z

        cy_a, sy_a = math.cos(tot_yaw), math.sin(tot_yaw)
        cp_a, sp_a = math.cos(tot_pitch), math.sin(tot_pitch)
        cr_a, sr_a = math.cos(tot_roll), math.sin(tot_roll)

        R_yaw = np.array([[cy_a, 0, sy_a], [0, 1, 0], [-sy_a, 0, cy_a]], dtype=np.float32)
        R_pitch = np.array([[1, 0, 0], [0, cp_a, -sp_a], [0, sp_a, cp_a]], dtype=np.float32)
        R_roll = np.array([[cr_a, -sr_a, 0], [sr_a, cr_a, 0], [0, 0, 1]], dtype=np.float32)
        rot_mat = R_yaw @ R_pitch @ R_roll

        # Rotate particles
        rot_pos = self.pos @ rot_mat.T

        # Camera bobbing
        cam_x = math.sin(t * 0.02) * 4.5
        cam_y = math.cos(t * 0.03) * 3.0
        cam_z = 80.0 / max(0.2, self.zoom)

        # Perspective projection
        z_eff = cam_z - (rot_pos[:, 2] + self.cloud_z)
        z_eff = np.maximum(z_eff, 12.0)
        scale = (cam_z / z_eff) * (effective_r / 25.0)

        screen_x = cx + (rot_pos[:, 0] - cam_x) * scale
        screen_y = cy + (rot_pos[:, 1] - cam_y) * scale
        depths = rot_pos[:, 2]

        # ── 3. Connection Lines ──
        if self.active_connections and self.line_amount > 0.01:
            line_alpha = int(max(0, min(220, self.line_amount * 0.18 * 255)))
            line_col = QColor(int(cr), int(cg), int(cb), line_alpha)
            p.setPen(QPen(line_col, 1.0))

            q_lines: list[QLineF] = []
            for p1, p2 in self.active_connections:
                p1_rot = p1 @ rot_mat.T
                p2_rot = p2 @ rot_mat.T

                z1 = max(12.0, cam_z - (p1_rot[2] + self.cloud_z))
                z2 = max(12.0, cam_z - (p2_rot[2] + self.cloud_z))

                s1 = (cam_z / z1) * (effective_r / 25.0)
                s2 = (cam_z / z2) * (effective_r / 25.0)

                x1 = cx + (p1_rot[0] - cam_x) * s1
                y1 = cy + (p1_rot[1] - cam_y) * s1
                x2 = cx + (p2_rot[0] - cam_x) * s2
                y2 = cy + (p2_rot[1] - cam_y) * s2

                q_lines.append(QLineF(x1, y1, x2, y2))

            p.drawLines(q_lines)

        # ── 4. Layered Particles (Back and Front) ──
        back_mask = depths < 0.0
        front_mask = ~back_mask

        # Back particles (softer, smaller, dimmer)
        back_alpha = int(max(20, min(160, 110 * self.current_bright)))
        col_back = QColor(int(cr * 0.80), int(cg * 0.72), int(cb * 0.72), back_alpha)
        p.setPen(QPen(col_back, 1.6))
        pts_back = [QPointF(x, y) for x, y in zip(screen_x[back_mask], screen_y[back_mask])]
        p.drawPoints(pts_back)

        # Front particles (brighter, larger, glowing)
        front_alpha = int(max(40, min(255, 235 * self.current_bright + bass * 35)))
        col_front = QColor(int(cr), int(cg), int(cb), front_alpha)
        p.setPen(QPen(col_front, 2.5 + bass * 0.6))
        pts_front = [QPointF(x, y) for x, y in zip(screen_x[front_mask], screen_y[front_mask])]
        p.drawPoints(pts_front)

        # Hot fiery core accents on front particles
        hot_alpha = int(max(20, min(255, 180 * self.current_bright)))
        col_hot = QColor(255, 255, 210, hot_alpha)
        p.setPen(QPen(col_hot, 1.2))
        p.drawPoints(pts_front[::3])

        # ── 5. Active Electron Sparks (Traveling Energy Pulses) ──
        for el in self.active_electrons:
            prog = el["t"]
            # Interpolated 3D position
            px_3d = el["sx"] + (el["ex"] - el["sx"]) * prog
            py_3d = el["sy"] + (el["ey"] - el["sy"]) * prog
            pz_3d = el["sz"] + (el["ez"] - el["sz"]) * prog

            el_rot = np.array([px_3d, py_3d, pz_3d], dtype=np.float32) @ rot_mat.T
            z_el = max(12.0, cam_z - (el_rot[2] + self.cloud_z))
            s_el = (cam_z / z_el) * (effective_r / 25.0)

            ex_2d = cx + (el_rot[0] - cam_x) * s_el
            ey_2d = cy + (el_rot[1] - cam_y) * s_el

            # Draw spark with fiery glow corona
            p.setPen(Qt.PenStyle.NoPen)
            # Outer orange halo
            p.setBrush(QColor(*self._spark[0], 80))
            p.drawEllipse(QPointF(ex_2d, ey_2d), 7.0, 7.0)
            # Golden inner corona
            p.setBrush(QColor(*self._spark[1], 180))
            p.drawEllipse(QPointF(ex_2d, ey_2d), 4.0, 4.0)
            # Pure white spark core
            p.setBrush(QColor(255, 255, 255, 255))
            p.drawEllipse(QPointF(ex_2d, ey_2d), 2.0, 2.0)

        # ── 6. Interactive Hint Text ──
        p.setFont(QFont("Consolas", 8))
        hint_col = QColor(int(cr * 0.70), int(cg * 0.66), int(cb * 0.66), 140)
        p.setPen(hint_col)
        p.drawText(
            QRectF(cx - radius, cy + radius * 0.94, radius * 2, 22),
            Qt.AlignmentFlag.AlignCenter,
            "DRAG · SCROLL TO ZOOM · DOUBLE CLICK TO RESET"
        )

        p.restore()
