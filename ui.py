from __future__ import annotations

import json
import math
import os
import platform
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

# Qt's video backend prints the ffmpeg stream banner — codec, bitrate, the
# whole signed googlevideo URL — to the console for every stream it opens. That
# is two screenfuls per video, it looks like an error to anyone reading the log,
# and it puts a URL carrying the viewer's IP address into a terminal people
# paste into bug reports. Silenced here, before Qt initialises its logging.
# An explicit setting from the user is left alone.
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")

from PyQt6.QtCore import (
    QEasingCurve, QLineF, QMimeData, QObject, QParallelAnimationGroup, QPointF,
    QPoint, QPropertyAnimation, QRect, QRectF, QSize, QSizeF, Qt, QTimer,
    QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QIcon, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QRadialGradient, QShortcut,
)
# Video playback for the HUD. Part of PyQt6, so it costs no new dependency —
# but the multimedia plugins are a separate piece of the Qt install and can be
# absent on a stripped-down system, so a failure here disables one feature
# rather than stopping ULTRON from starting.
try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem
    HAVE_VIDEO = True
except Exception as _e:            # noqa: BLE001 - reported, never fatal
    QAudioOutput = QMediaPlayer = QGraphicsVideoItem = None
    HAVE_VIDEO = False
    print(f"[Video] playback unavailable ({_e}) — the HUD will not show video.")

# 3D Orbit view (Three.js / 3d-force-graph WebGL engine)
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    HAVE_WEBENGINE = True
except Exception as _e:
    QWebEngineView = QWebEnginePage = QWebEngineSettings = None
    HAVE_WEBENGINE = False
    print(f"[Orbit] WebEngine unavailable ({_e})")

from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QGraphicsScene, QGraphicsView,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QProgressBar,
)

try:
    if hasattr(Qt, "ApplicationAttribute") and hasattr(Qt.ApplicationAttribute, "AA_ShareOpenGLContexts"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
except Exception:
    pass


try:
    from core.avatar import HoloAvatar
except Exception:      # pragma: no cover — HUD must never die over cosmetics
    HoloAvatar = None

from PyQt6.QtWidgets import QCompleter
from PyQt6.QtCore import QStringListModel
from core.ui_command_center import (
    AICore, ConnectionRow, GraphPanel, NodeInspector, StageStrip, ToggleSwitch,
    CATS, FONT_HEAD, FONT_MONO, FONT_UI, build_graph, icon_pixmap, section_label,
    status_color, STATUS_TEXT,
)

# Startup / shutdown cinematics. Cosmetic like the avatar: if the package fails
# to import, ULTRON starts and closes exactly as it did before it existed.
try:
    from core import cinematic as _cine
except Exception as _e:            # noqa: BLE001 - reported, never fatal
    _cine = None
    print(f"[Cinematic] animations unavailable ({_e})")
from core import theme as _theme


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"


def _read_full_config() -> dict:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# Single source of truth for the release name — the window title, the header
# badge and the readme must never disagree again.
APP_NAME     = "Ultron 2.0"
APP_CREDIT   = "Ultron 2.0"
APP_VERSION  = f"{APP_NAME}  /  {APP_CREDIT}"
APP_PROTOCOL = "ULTRON / 2.0"

_DEFAULT_W, _DEFAULT_H = 1440, 880
_MIN_W,     _MIN_H     = 1000, 660
_LEFT_W  = 252
_RIGHT_W = 304

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    # Command-center palette: near-black navy surfaces, electric-cyan accent.
    BG        = "#03070d"
    PANEL     = "#06101b"
    PANEL2    = "#081524"
    BORDER    = "#10263a"
    BORDER_B  = "#1a3954"
    BORDER_A  = "#142e46"
    PRI       = "#22e1ff"
    PRI_DIM   = "#0f7f99"
    PRI_GHO   = "#06202e"
    ACC       = "#ff0055"
    ACC2      = "#ffaa00"
    GREEN     = "#00ff9d"
    GREEN_D   = "#00b36b"
    RED       = "#ff1a40"
    MUTED_C   = "#ff1a40"
    TEXT      = "#e2f3ff"
    TEXT_DIM  = "#55738c"
    TEXT_MED  = "#86a6bf"
    WHITE     = "#ffffff"
    DARK      = "#04080f"
    BAR_BG    = "#04182a"


# Keys tied to the accent colour — status colours (ACC, GREEN, RED…) stay fixed
_HUE_LINKED = (
    "BG", "PANEL", "PANEL2", "BORDER", "BORDER_B", "BORDER_A",
    "PRI", "PRI_DIM", "PRI_GHO", "TEXT", "TEXT_DIM", "TEXT_MED",
    "WHITE", "DARK", "BAR_BG",
)
_PALETTE_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _HUE_LINKED}

DEFAULT_UI_COLOR = _PALETTE_DEFAULTS["PRI"]

# The stylesheet half of every theme change (see core/theme.py), and the accent
# it was last asked for — C.PRI alone cannot say "white" (it stays saturated).
_CSS_THEME = _theme.CssTheme(_PALETTE_DEFAULTS)
_ACCENT = {"hex": DEFAULT_UI_COLOR}


def apply_ui_accent(accent_hex: str) -> bool:
    """
    Re-derives the whole teal-family palette from the chosen accent colour
    (hue shift — brightness/saturation ratios are preserved, design stays intact).
    Painted elements (HUD, waveform, metrics) pick up the new colour on the next
    frame; stylesheet-based panels pick it up when they are rebuilt.
    """
    import colorsys

    accent_hex = (accent_hex or "").strip().lower()
    if not (accent_hex.startswith("#") and len(accent_hex) == 7):
        return False
    try:
        int(accent_hex[1:], 16)
    except ValueError:
        return False

    def _hsv(h: str) -> tuple[float, float, float]:
        r = int(h[1:3], 16) / 255
        g = int(h[3:5], 16) / 255
        b = int(h[5:7], 16) / 255
        return colorsys.rgb_to_hsv(r, g, b)

    base_h            = _hsv(_PALETTE_DEFAULTS["PRI"])[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh   = acc_h - base_h
    grey = acc_s < 0.08   # near-grey accent → the whole theme is desaturated

    for key, hex0 in _PALETTE_DEFAULTS.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        setattr(C, key, "#{:02x}{:02x}{:02x}".format(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)))
    _ACCENT["hex"] = accent_hex
    _CSS_THEME.remember(current_palette())
    return True


def current_palette() -> dict[str, str]:
    """A snapshot of the accent-linked colours currently on class C."""
    return {k: getattr(C, k) for k in _HUE_LINKED}


def retheme_all_widgets(old: dict[str, str], new: dict[str, str]) -> None:
    """
    LIVE full theme change: every widget's stylesheet is recoloured for the
    accent last passed to apply_ui_accent and repainted — panels, buttons,
    borders, literal colours included — no restart needed.

    Each stylesheet is first normalised back to the default palette, then the
    whole accent family is shifted to the new hue (core/theme.CssTheme), so a
    change is exact and repeatable however many themes came before it, and
    semantic colours (green = connected, red = error) are never touched.
    """
    _CSS_THEME.remember(old)
    _CSS_THEME.remember(new)
    app = QApplication.instance()
    if app is None:
        return
    target = _ACCENT["hex"]
    for w in app.allWidgets():
        try:
            _CSS_THEME.restyle(w, target)
            w.update()
        except Exception:
            pass


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


# ── Windows GPU via NVML DLL (no subprocess, no console window) ──────────────
_nvml_lib: object = None   # cached ctypes DLL
_nvml_ok:  object = None   # None=untested, True=works, False=unavailable


def _nvml_gpu_windows() -> float:
    """Return NVIDIA GPU utilisation % using nvml.dll directly — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        # Probe caches — GPU (NVML) and temperature (WMI) are the expensive
        # queries; initialise their handles once and reuse them instead of
        # rebuilding a connection on every poll.
        self._slow_tick = 0            # gpu/temp refreshed every 3rd cycle
        self._pynvml    = None         # cached pynvml module + device handle
        self._pynvml_h  = None
        self._pynvml_ok = None         # None=untested, False=unavailable here
        self._nv_unix   = None         # cached (lib, dev) for Linux/macOS NVML
        self._wmi_conn  = None         # cached WMI connection (creating one is slow)
        self._wmi_ok    = None         # None=untested, False=unavailable here
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(2.0)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        # GPU and temperature change slowly and are the most expensive probes
        # (NVML / WMI) — refresh them every 3rd cycle (~6 s) instead of every
        # cycle, reusing the previous reading in between.
        self._slow_tick = (self._slow_tick + 1) % 3
        if self._slow_tick == 1:
            gpu = self._get_gpu()
            tmp = self._get_temp()
        else:
            gpu = self.gpu
            tmp = self.tmp

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # pynvml — subprocess-free; initialise once and reuse the handle.
        # Re-initialising NVML on every poll is slow, so cache it and stop
        # retrying pynvml entirely once it proves unavailable here.
        if self._pynvml_ok is not False:
            try:
                if self._pynvml_h is None:
                    import pynvml  # type: ignore
                    pynvml.nvmlInit()
                    self._pynvml    = pynvml
                    self._pynvml_h  = pynvml.nvmlDeviceGetHandleByIndex(0)
                    self._pynvml_ok = True
                return float(self._pynvml.nvmlDeviceGetUtilizationRates(self._pynvml_h).gpu)
            except Exception:
                self._pynvml_ok = False

        # Windows: nvml.dll via ctypes (already cached in _nvml_gpu_windows)
        if _OS == "Windows":
            return _nvml_gpu_windows()

        # Linux / macOS: libnvidia-ml shared lib via ctypes — init once, reuse
        try:
            import ctypes

            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            if self._nv_unix is None:
                _lib = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"
                nv = ctypes.CDLL(_lib)
                nv.nvmlInit_v2()
                dev = ctypes.c_void_p()
                nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
                self._nv_unix = (nv, dev)

            nv, dev = self._nv_unix
            u = _Util()
            nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
            return float(u.gpu)
        except Exception:
            pass

        return -1.0   # N/A — zero subprocess on all platforms

    def _get_temp(self) -> float:
        # psutil — works on Linux; occasionally Windows with driver support
        try:
            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        # Windows: wmi module (pure Python COM, zero subprocess). Reuse a single
        # connection — building a fresh wmi.WMI() on every poll spins up a COM
        # connection each time and is very slow. Give up after one failure.
        if _OS == "Windows" and self._wmi_ok is not False:
            try:
                if self._wmi_conn is None:
                    import wmi  # type: ignore
                    self._wmi_conn = wmi.WMI(namespace="root/wmi")
                tz = self._wmi_conn.MSAcpi_ThermalZoneTemperature()
                if tz:
                    return (tz[0].CurrentTemperature / 10.0) - 273.15
            except Exception:
                self._wmi_ok   = False
                self._wmi_conn = None

        return -1.0   # N/A — zero subprocess on all platforms

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

class HudCanvas(QWidget):
    def __init__(self, face_path: str, assistant_name: str = "ULTRON", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"
        self._assistant_name = assistant_name

        # The holographic head that fills the HUD. If it could not be imported
        # we fall back to the old glowing core so the panel is never empty.
        self._avatar = None
        if HoloAvatar is not None:
            try:
                self._avatar = HoloAvatar()
            except Exception:
                self._avatar = None

        # Which centrepiece to draw. Read once here and changed live by the
        # settings toggle; the avatar object is kept either way so switching
        # back is instant and costs no reload.
        try:
            from memory.config_manager import get_hud_style
            self.hud_style = get_hud_style()
        except Exception:
            self.hud_style = "orb"
        self._core_phase = 0.0
        from core.ultron_orb import UltronOrb
        self._orb = UltronOrb()
        self._drag_pos = None

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._step_t     = time.time()
        self._blink      = True
        self._blink_tick = 0

        # Rescaled-face cache: the smooth rescale is expensive, so we keep the
        # last result and only rebuild it when the (quantised) size changes.

        # Static grid-dot layer, pre-rendered once per size/theme into a pixmap
        # so paintEvent blits it in one call instead of thousands of drawPoint()s.
        self._grid_cache: QPixmap | None = None
        self._grid_key = None
        # Repaint throttle counter (idle frames drop to ~20 Hz — see _step()).
        self._paint_tick = 0

        # Live audio reactivity: _live_amp is written from the audio threads
        # (0.0–1.0), _amp_disp is the smoothed value the paint code reads.
        self._live_amp  = 0.0
        self._amp_disp  = 0.0
        # (frames, start_time, hop) posted by the playback thread — see
        # push_visemes(). None means "no schedule; use the plain level".
        self._visemes = None
        self._vis_i = None        # first schedule frame not yet handed to the mouth
        self._base_scale = 1.0    # slow "breathing" target; amp is added per-frame
        self._base_halo  = 55.0

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(8)

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            if self._avatar is not None:
                self._avatar.glance(dx, dy, hold)
        except Exception:
            pass

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: hand over a schedule of (level, openness, width) frames.

        The playback thread writes up to 200 ms of audio in one go, so a single
        averaged level would only move the mouth five times a second — enough to
        flap, nowhere near enough to articulate. It instead posts the whole
        slice's worth of 20 ms frames here and `_step()` plays them out against
        the wall clock, in step with the audio going to the speakers.

        `at` is the wall-clock time this batch will *begin to sound*, which the
        caller tracks as a playback cursor. It is not the time of the call, and
        the difference is the whole point: `stream.write` returns once the buffer
        accepts the samples, so consecutive batches are handed over far faster
        than they play. Anchoring each one to "now" made every batch start while
        its predecessor was still sounding, so each schedule replaced the last
        after a couple of frames and the mouth only ever played the opening
        instant of every 200 ms — the reason it did not match the words.

        Successive batches are therefore *appended* into one continuous
        timeline, not swapped in. A paragraph is one schedule; the mouth stops
        falling into a gap at every chunk boundary and having to climb back out.
        """
        try:
            if not frames:
                return
            hop = max(1e-3, float(hop))
            at = float(at)
            new = list(frames)
            cur = self._visemes
            if cur is not None:
                old, t0, ohop = cur
                if abs(ohop - hop) < 1e-6:
                    # Where in the existing timeline does this batch land?
                    i = int(round((at - t0) / hop))
                    if 0 <= i <= len(old) + 1:
                        # Continues (or slightly overlaps) what is already
                        # queued: extend rather than restart. Drop whatever has
                        # already been played so the list cannot grow without
                        # bound over a long reply.
                        merged = old[:i] + new
                        played = int((time.time() - t0) / hop) - 2
                        if played > 60:
                            merged = merged[played:]
                            t0 += played * hop
                            if self._vis_i is not None:
                                self._vis_i = max(0, self._vis_i - played)
                        self._visemes = (merged, t0, hop)
                        return
            self._visemes = (new, at, hop)
            self._vis_i = None
        except Exception:
            pass

    def set_audio_level(self, level: float) -> None:
        """Thread-safe entry point for the audio threads. Stores the louder of
        the incoming level and the current value so brief gaps between chunks
        don't make the waveform stutter; _step() decays it back down."""
        try:
            lv = float(level)
        except (TypeError, ValueError):
            return
        if lv < 0.0:
            lv = 0.0
        elif lv > 1.0:
            lv = 1.0
        if lv > self._live_amp:
            self._live_amp = lv

    def _make_grid(self, W: int, H: int) -> QPixmap:
        """Pre-render the static grid-dot background into a transparent pixmap so
        paintEvent can blit it once per frame instead of running a nested
        drawPoint() loop across the whole widget every 16 ms."""
        pm = QPixmap(max(1, W), max(1, H))
        pm.fill(Qt.GlobalColor.transparent)
        gp = QPainter(pm)
        gp.setPen(QPen(qcol(C.PRI_GHO), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                gp.drawPoint(x, y)
        gp.end()
        return pm

    def _step(self):
        self._tick += 1
        now = time.time()

        # ── Live audio reactivity ────────────────────────────────────────────
        # A viseme schedule, if one is playing, gives both the level and the
        # mouth shape for this exact instant; otherwise fall back to the peak
        # level the audio threads pushed in.
        v_open = v_wide = v_level = None
        v_seq = None
        sched = self._visemes
        if sched is not None:
            frames, t0, hop = sched
            i = int((now - t0) / hop)
            if 0 <= i < len(frames):
                # Hand over *every* frame since the last tick, not just the one
                # under the cursor. This timer runs at 60 Hz but the paint is
                # throttled and the machine may be busy, so a tick can span two
                # or three 20 ms frames — and a consonant closure is only two
                # frames long. Sampling one and discarding the rest is how the
                # closures between words went missing.
                j = self._vis_i if self._vis_i is not None else i
                v_seq = frames[max(0, j):i + 1]
                self._vis_i = max(j, i + 1)
                v_level, v_open, v_wide = frames[i]
                if v_seq:
                    peak = max(f[0] for f in v_seq)
                    if peak > self._live_amp:
                        self._live_amp = peak
            elif i >= len(frames):
                self._visemes = None        # schedule spent
                self._vis_i = None

        # Audio threads push peaks into _live_amp; decay it toward silence so
        # gaps between chunks fade out instead of freezing, then smooth it.
        self._live_amp *= 0.86
        self._amp_disp += (self._live_amp - self._amp_disp) * 0.45
        amp = self._amp_disp

        # The avatar animates off the very same smoothed level the waveform
        # uses — one audio source, so the mouth can never drift out of sync.
        dt = now - self._step_t
        self._step_t = now
        # Integrated, not derived from absolute time: multiplying wall-clock by
        # a rate that changes with state jumps the rings the instant ULTRON
        # starts talking. Same lesson the head's sway taught.
        self._core_phase += min(0.10, max(0.0, dt))

        if self._avatar is not None and self.hud_style == "face":
            self._avatar.step(dt, amp, speaking=self.speaking,
                              muted=self.muted, state=self.state,
                              v_open=v_open, v_wide=v_wide or 0.0,
                              v_level=v_level, v_seq=v_seq,
                              v_hop=(sched[2] if sched is not None else 0.02))
        else:
            # Fallback core: slow "breathing" base target, lifted by the level.
            if now - self._last_t > (0.12 if self.speaking else 0.5):
                if self.speaking:
                    self._base_scale = 1.03
                    self._base_halo  = 122.0
                elif self.muted:
                    self._base_scale = random.uniform(0.998, 1.002)
                    self._base_halo  = random.uniform(15, 28)
                else:
                    self._base_scale = random.uniform(1.001, 1.008)
                    self._base_halo  = random.uniform(48, 68)
                self._last_t = now

            if self.muted:
                self._tgt_scale, self._tgt_halo = self._base_scale, self._base_halo
            elif self.speaking:
                self._tgt_scale = self._base_scale + amp * 0.13
                self._tgt_halo  = self._base_halo  + amp * 95.0
            else:
                self._tgt_scale = self._base_scale + amp * 0.06
                self._tgt_halo  = self._base_halo  + amp * 75.0

            sp = 0.38 if self.speaking else (0.30 if amp > 0.02 else 0.15)
            self._scale += (self._tgt_scale - self._scale) * sp
            self._halo  += (self._tgt_halo  - self._halo)  * sp

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
            _blinked = True
        else:
            _blinked = False

        # Repaint throttling — advancing the animation state above is cheap at
        # 60 Hz, but the paint is heavy. Active (speaking, audio, thinking) runs
        # at ~30 Hz, which is the frame rate animation has used for talking
        # characters forever and is indistinguishable here; idle drops to ~20 Hz
        # so a sleeping HUD stops pinning a CPU core. The visuals stay smooth
        # either way because the animation state keeps stepping at 60 Hz.
        self._paint_tick = (self._paint_tick + 1) % 6
        active = (self.speaking or amp > 0.02
                  or self.state in ("THINKING", "PROCESSING"))
        if _blinked or (self._paint_tick % 2 == 0 if active
                        else self._paint_tick % 3 == 0):
            # Nothing is on screen when the window is hidden or minimised, so
            # rendering the avatar into it is pure waste — and this app is meant
            # to sit running all day. The animation state above keeps stepping,
            # so it picks up mid-motion instead of snapping when you come back.
            if self._on_screen():
                self.update()

    def _on_screen(self) -> bool:
        """True only when this canvas can actually be seen by the user."""
        try:
            if not self.isVisible():
                return False
            win = self.window()
            return not (win.isMinimized() or win.isHidden())
        except Exception:
            return True      # never let a visibility check stop the HUD drawing

    # ── reactor core ─────────────────────────────────────────────────────────
    # The centrepiece for anyone who did not want a face looking back at them.
    # Built from the same budget as the head — software QPainter, no GPU — and
    # from the same principle: everything on it means something. The rings turn
    # at a rate the state sets, the spectrum ring is the real audio level, and
    # the core brightens with the voice. Nothing here is decoration that moves
    # for its own sake, which is what made the old glowing orb feel dead.

    def _core_colours(self):
        if self.muted:
            return qcol(C.MUTED_C), qcol(C.MUTED_C)
        if self.speaking:
            return qcol(C.PRI), qcol(C.ACC)
        if self.state in ("THINKING", "PROCESSING"):
            return qcol(C.PRI), qcol(C.ACC2)
        if self.state == "LISTENING":
            return qcol(C.PRI), qcol(C.GREEN)
        return qcol(C.PRI), qcol(C.PRI_DIM)

    def _paint_core(self, p: QPainter, cx: float, cy: float, r: float,
                    W: float = 0.0, H: float = 0.0):
        """Draw the reactor at (cx, cy) with outer radius r, using the whole
        canvas (W x H) for the marks that frame it."""
        main, acc = self._core_colours()
        bg = qcol(C.BG)
        amp = self._amp_disp
        t = self._core_phase
        live = (self.speaking or amp > 0.04) and not self.muted

        def blend(col: QColor, a: float) -> QColor:
            """Pre-mix onto the background instead of asking Qt to composite.
            The raster engine's opaque path is several times faster than its
            translucent one, and everything here is a line or an arc."""
            k = max(0.0, min(1.0, a))
            return QColor(int(bg.red()   + (col.red()   - bg.red())   * k),
                          int(bg.green() + (col.green() - bg.green()) * k),
                          int(bg.blue()  + (col.blue()  - bg.blue())  * k))

        p.setBrush(Qt.BrushStyle.NoBrush)

        # 1. The atmosphere. One radial gradient doing what a stack of discs did
        #    badly: a wide, soft body of light that gives the thing presence
        #    before any detail is read. This single element decides whether the
        #    HUD looks vast or looks small, so it is drawn first and drawn big.
        # Concentrated rather than spread: a gradient reaching the outer rim
        # washes the whole disc a flat dim blue and reads as fog. Ending it at
        # two thirds leaves it a body of light with somewhere to fall off to,
        # which is what makes it look lit rather than tinted.
        lift = 1.0 + 0.55 * amp + (0.18 if self.speaking else 0.0)
        p.setPen(Qt.PenStyle.NoPen)
        for gr, a0, a1 in ((r * 0.70, 0.30, 0.0), (r * 0.34, 0.34, 0.0)):
            g = QRadialGradient(cx, cy, gr)
            g.setColorAt(0.00, blend(main, min(0.95, a0 * lift)))
            g.setColorAt(0.45, blend(main, min(0.95, a0 * lift * 0.52)))
            g.setColorAt(0.78, blend(main, min(0.95, a0 * lift * 0.18)))
            g.setColorAt(1.00, blend(main, a1))
            p.setBrush(QBrush(g))
            p.drawEllipse(QRectF(cx - gr, cy - gr, gr * 2, gr * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # 2. Frame marks at the corners of the whole canvas, not of the circle.
        #    They are what set the scale: the eye reads the reactor as filling
        #    the room rather than sitting in the middle of it.
        if W > 40 and H > 40:
            m, arm = min(W, H) * 0.035, min(W, H) * 0.055
            p.setPen(QPen(blend(main, 0.45), 1.4))
            for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                x = cx + sx * (W / 2 - m)
                y = cy + sy * (H / 2 - m)
                p.drawLine(QLineF(x, y, x - sx * arm, y))
                p.drawLine(QLineF(x, y, x, y - sy * arm))

        # 3. Crosshair across the full canvas, broken around the core so it
        #    frames the reactor rather than crossing it.
        p.setPen(QPen(blend(main, 0.16), 1))
        gap = r * 0.62
        if W > 40:
            p.drawLine(QLineF(cx - W / 2, cy, cx - gap, cy))
            p.drawLine(QLineF(cx + gap, cy, cx + W / 2, cy))
        if H > 40:
            p.drawLine(QLineF(cx, cy - H / 2, cx, cy - gap))
            p.drawLine(QLineF(cx, cy + gap, cx, cy + H / 2))

        # 4. Two thin outer circles. Sparse on purpose — a dense ring reads as a
        #    grey band at this size, and restraint is what made the original
        #    look expensive.
        for rr, a in ((1.00, 0.34), (0.93, 0.16)):
            rad = r * rr
            p.setPen(QPen(blend(main, a), 1))
            p.drawEllipse(QRectF(cx - rad, cy - rad, rad * 2, rad * 2))

        # 5. Long, sparse graduations: 24 majors reaching well in from the rim,
        #    with shorter minors between them.
        major, minor = [], []
        for i in range(72):
            a = math.radians(i * 5.0)
            ca, sa = math.cos(a), math.sin(a)
            if i % 3 == 0:
                major.append(QLineF(cx + ca * r * 0.885, cy + sa * r * 0.885,
                                    cx + ca * r * 0.985, cy + sa * r * 0.985))
            else:
                minor.append(QLineF(cx + ca * r * 0.945, cy + sa * r * 0.945,
                                    cx + ca * r * 0.985, cy + sa * r * 0.985))
        p.setPen(QPen(blend(main, 0.42), 1.3))
        p.drawLines(major)
        p.setPen(QPen(blend(main, 0.18), 1))
        p.drawLines(minor)

        # 6. Sweeping arcs. Long spans, not dashes — the original's grandeur
        #    came from a few big strokes. Speed is the state: idle drifts,
        #    thinking hurries, speaking runs.
        rate = 1.0 + (1.9 if self.state in ("THINKING", "PROCESSING") else 0.0) \
                   + (1.2 if self.speaking else 0.0)
        for k, (rr, span, count, dirn, col, a, wid) in enumerate((
                (0.955, 118, 2, +1, acc,  0.75, 2.0),
                (0.845, 82,  3, -1, main, 0.38, 1.3),
                (0.760, 150, 1, +1, acc,  0.45, 1.6),
                (0.660, 64,  4, -1, main, 0.26, 1.1),
                (0.545, 128, 2, +1, main, 0.30, 1.2))):
            rad = r * rr
            p.setPen(QPen(blend(col, a), wid))
            box = QRectF(cx - rad, cy - rad, rad * 2, rad * 2)
            base = (t * rate * (9 + k * 6) * dirn) % 360.0
            for sgm in range(count):
                p.drawArc(box, int((base + sgm * (360.0 / count)) * 16),
                          int(span * 16))

        # 7. The voice, as a ring of graduations that grow with it. Kept out at
        #    a wide radius so it never crowds the middle.
        n = 60
        ring = r * 0.415
        spikes = []
        for i in range(n):
            a = math.radians(i * (360.0 / n))
            ca, sa = math.cos(a), math.sin(a)
            wob = 0.5 + 0.5 * math.sin(t * 2.3 + i * 0.42)
            idle = 0.018 + 0.012 * math.sin(t * 1.2 + i * 0.7)
            h = r * (idle + (amp * 0.20 * wob if live else 0.0))
            spikes.append(QLineF(cx + ca * ring, cy + sa * ring,
                                 cx + ca * (ring + h), cy + sa * (ring + h)))
        p.setPen(QPen(blend(acc if live else main, 0.25 + 0.5 * amp), 1.6))
        p.drawLines(spikes)

        # 8. The inner ring the name sits in.
        inner = r * 0.355
        p.setPen(QPen(blend(acc, 0.30 + 0.45 * amp), 1.5))
        p.drawEllipse(QRectF(cx - inner, cy - inner, inner * 2, inner * 2))

        # 9. The name, sized from the string rather than from the radius alone:
        #    "ULTRON" and a name someone renamed to "MAX" are very
        #    different widths, and a fixed fraction of r spills one of them past
        #    the ring it is supposed to sit inside.
        name = self._assistant_name or ""
        if name:
            space = max(1.0, r * 0.018)
            fsz = max(8, int(min(r * 0.105,
                                 (inner * 1.75) / max(1, len(name)) * 1.6 - space)))
            f = QFont("Consolas", fsz, QFont.Weight.Bold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, space)
            p.setFont(f)
            p.setPen(QPen(blend(qcol(C.WHITE), 0.6 + 0.4 * min(1.0, amp * 2)), 1))
            p.drawText(QRectF(cx - r, cy - fsz, r * 2, fsz * 2),
                       Qt.AlignmentFlag.AlignCenter, name)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.position()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None:
            delta = event.position() - self._drag_pos
            self._orb.rotate(delta.x(), delta.y())
            self._drag_pos = event.position()
            self.update()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        self._orb.reset()
        self.update()

    def wheelEvent(self, event):
        self._orb.zoom = max(.65, min(1.2, self._orb.zoom + event.angleDelta().y()/2400))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():      # device not ready (e.g. 0-size during layout) — skip cleanly
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.BG))

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        # grid dots — blitted from a cached layer; rebuilt only when the size
        # or the theme's ghost colour changes (so live re-theming still works).
        _gkey = (W, H, C.PRI_GHO)
        if self._grid_cache is None or self._grid_key != _gkey:
            self._grid_cache = self._make_grid(W, H)
            self._grid_key   = _gkey
        p.drawPixmap(0, 0, self._grid_cache)

        # ── holographic head ────────────────────────────────────────────────
        # Sized to the band between the top of the canvas and the status line,
        # capped by width, so it fills the HUD at any window size — including
        # fullscreen — without ever colliding with the status text below.
        _sy_status = cy + fw * 0.40
        if self.hud_style == "orb":
            band_h = max(60.0, _sy_status - 35.0)
            self._orb.paint(p, cx, band_h / 2 + 12, min(W*.40, band_h*.43),
                            self._core_phase, self._amp_disp,
                            C.MUTED_C if self.muted else C.PRI, self.state,
                            speaking=self.speaking, muted=self.muted)
        elif self._avatar is not None and self.hud_style == "face":
            _band_t = 12.0
            _band_h = max(60.0, _sy_status - 12.0 - _band_t)
            _r_head = min(fw * 0.355, _band_h / (self._avatar.SPAN + 0.08))
            _head_cy = _band_t + (_band_h - self._avatar.SPAN * _r_head) / 2.0 + _r_head

            if self.muted:
                _main = _acc = qcol(C.MUTED_C)
            else:
                _main = qcol(C.PRI)
                if self.speaking:
                    _acc = qcol(C.ACC)
                elif self.state in ("THINKING", "PROCESSING"):
                    _acc = qcol(C.ACC2)
                elif self.state == "LISTENING":
                    _acc = qcol(C.GREEN)
                else:
                    _acc = qcol(C.PRI)
            self._avatar.paint(p, cx, _head_cy, _r_head, _main, _acc, qcol(C.BG))

        # reactor core — the other centrepiece, and the fallback if the head
        # could not be built. There is no third path: the old face.png branch
        # was unreachable (no such file ships) and the bare orb it fell through
        # to is what this replaces.
        else:
            _band_t = 12.0
            _band_h = max(60.0, _sy_status - 12.0 - _band_t)
            _r = min(W * 0.46, _band_h / 2.0)
            self._paint_core(p, cx, _band_t + _band_h / 2.0, _r, W, _band_h)

        # status text
        sy = _sy_status
        if self.muted:
            txt, col = "⊘  MUTED",     qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "●  SPEAKING",  qcol(C.ACC)
        elif self.state == "THINKING":
            sym = "◈" if self._blink else "◇"
            txt, col = f"{sym}  THINKING",   qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym = "▷" if self._blink else "▶"
            txt, col = f"{sym}  PROCESSING", qcol(C.ACC2)
        elif self.state == "LISTENING":
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  LISTENING",  qcol(C.GREEN)
        else:
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  {self.state}", qcol(C.PRI)

        p.setPen(QPen(col, 1))
        p.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        # waveform — reacts to the real audio level (mic while listening,
        # ULTRON's own voice while speaking). Falls back to a gentle idle
        # ripple when there's no sound. _amp_disp is the smoothed 0–1 level.
        wy = sy + 30
        N, bw = 36, 8
        wx0 = (W - N * bw) / 2
        amp = self._amp_disp
        mid = (N - 1) / 2.0
        for i in range(N):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            else:
                env     = (1.0 - abs(i - mid) / mid) ** 0.7      # center-weighted hump
                shimmer = 0.55 + 0.45 * math.sin(self._tick * 0.18 + i * 0.7)
                idle    = 3.0 + 2.0 * math.sin(self._tick * 0.09 + i * 0.6)
                hgt     = int(max(2, min(24, idle + amp * 22.0 * env * shimmer)))
                if amp > 0.05:
                    cl = qcol(C.PRI) if hgt > 12 else qcol(C.PRI_DIM)
                else:
                    cl = qcol(C.BORDER_B)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)

        p.end()   # end deterministically so the backing store never flushes an active painter

class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        v = max(0.0, min(100.0, pct))
        if v == self._value and text == self._text:
            return          # unchanged — skip the repaint
        self._value = v
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

        p.end()

class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        # Cap scrollback so an hours-long session can't grow the document
        # without bound — keeps memory flat and every insert cheap. Oldest
        # lines drop off the top automatically.
        self.document().setMaximumBlockCount(600)
        self.setFont(QFont("Consolas", 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._ai_name_lc = (_read_full_config().get("assistant_name") or "ULTRON").strip().lower()
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        from core.security import redact
        text = redact(text)
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        # "ULTRON:" and "ULTRON [Claude]:" are the assistant; "ERR:" and
        # "ERR [Claude]:" are errors. Matching "err" anywhere used to paint any
        # line containing "server" or "error-free" red.
        _head = tl.split(":", 1)[0].split("[", 1)[0].strip()
        if   tl.startswith("you:"):                              self._tag = "you"
        elif _head in (self._ai_name_lc, "ultron", "car") and ":" in tl:
                                                                 self._tag = "ai"
        elif tl.startswith("file:"):                             self._tag = "file"
        elif tl.startswith(("err", "net:", "alert:")) or tl.startswith("error"):
                                                                 self._tag = "err"
        else:                                                    self._tag = "sys"
        # Long replies type in chunks so a 2,000-character answer does not
        # take twelve seconds to appear.
        self._chunk = max(1, len(self._text) // 160)
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos:self._pos + getattr(self, "_chunk", 1)]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                # SYS lines are the bulk of the log. Amber fought the cyan HUD
                # and, being a fixed status colour rather than a hue-linked one,
                # stayed amber even after the accent picker retinted everything
                # else. TEXT_MED follows the theme and drops the contrast to a
                # level you can read past.
                "sys":  qcol(C.TEXT_MED),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += len(ch)
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(8)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        # The marching-ants dashed border is only meaningful while the user is
        # hovering or dragging a file over the zone. When idle, skip the repaint
        # entirely instead of redrawing the whole zone 25×/s forever — that idle
        # repaint held the GIL and stole time from the audio/response threads.
        if not (self._hovering or self._drag_over):
            return
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for ULTRON", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

        p.end()

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Consolas", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont("Consolas", 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Consolas", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Consolas", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Consolas", 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class _CameraPreview(QWidget):
    """Floating overlay that briefly shows what the camera captured."""

    _W, _H = 244, 188

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            _CameraPreview {{
                background: rgba(0, 6, 10, 242);
                border: 1px solid {C.PRI};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("◈  VISUAL INPUT")
        title.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setFont(QFont("Consolas", 8))
        close_btn.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._img_lbl)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.hide()

    def show_frame(self, img_bytes: bytes) -> None:
        px = QPixmap()
        px.loadFromData(img_bytes)
        if not px.isNull():
            max_w = self._W - 12
            scaled = px.scaled(
                max_w, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setFixedSize(scaled.width(), scaled.height())
            self.adjustSize()
        self.show()
        self.raise_()
        self._timer.start(6_000)   # auto-dismiss after 6 s


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Consolas", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure ULTRON. before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont("Consolas", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        init_btn.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


class HueWheel(QWidget):
    """
    Circular colour picker. The user drags the handle (small white circle)
    around the wheel to choose from ALL hues. The filled circle in the centre
    is a live preview of the selected colour.
    """

    hue_picked    = pyqtSignal(str)   # while dragging (live)
    hue_committed = pyqtSignal(str)   # when the handle is released

    _RING = 16   # ring thickness (px)

    def __init__(self, initial_hex: str = DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setFixedSize(148, 148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue  = 0.53
        self._drag = False
        self.set_color(initial_hex)

    # ── API ──────────────────────────────────────────────────────────────────
    def color(self) -> str:
        return QColor.fromHsvF(self._hue, 1.0, 1.0).name()

    def set_color(self, hex_str: str):
        c = QColor((hex_str or "").strip())
        if c.isValid() and c.hsvHueF() >= 0:
            self._hue = c.hsvHueF()
            self.update()

    # ── geometry helpers ─────────────────────────────────────────────────────
    def _ring_rect(self) -> QRectF:
        m = self._RING / 2 + 3
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _hue_from_pos(self, pos: QPointF) -> float:
        c  = QRectF(self.rect()).center()
        dx = pos.x() - c.x()
        dy = c.y() - pos.y()          # screen y goes down — flip to math axis
        ang = math.atan2(dy, dx)      # [-π, π], counter-clockwise
        return (ang / (2 * math.pi)) % 1.0

    # ── drawing ──────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect   = self._ring_rect()
        center = rect.center()

        grad = QConicalGradient(center, 0)
        for i in range(0, 361, 20):
            grad.setColorAt(i / 360.0, QColor.fromHsvF((i % 360) / 360.0, 1.0, 1.0))
        p.setPen(QPen(QBrush(grad), self._RING))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        # centre preview circle
        preview = QColor.fromHsvF(self._hue, 1.0, 1.0)
        inner   = rect.adjusted(30, 30, -30, -30)
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.setBrush(QBrush(preview))
        p.drawEllipse(inner)

        # draggable handle
        r   = rect.width() / 2
        ang = self._hue * 2 * math.pi
        hx  = center.x() + r * math.cos(ang)
        hy  = center.y() - r * math.sin(ang)
        p.setPen(QPen(QColor("#00060a"), 2))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(QPointF(hx, hy), 7.5, 7.5)
        p.end()

    # ── fare ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        self._drag = True
        self._hue  = self._hue_from_pos(e.position())
        self.update()
        self.hue_picked.emit(self.color())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._hue = self._hue_from_pos(e.position())
            self.update()
            self.hue_picked.emit(self.color())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.hue_committed.emit(self.color())


class CustomizeOverlay(QWidget):
    """Floating overlay — change assistant name, user name, UI colour and voice."""

    saved = pyqtSignal(str, str, str, str)   # assistant_name, user_name, ui_color, voice
    _OW, _OH = 400, 588

    def __init__(self, assistant_name="ULTRON", user_name="",
                 ui_color=DEFAULT_UI_COLOR, voice="", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            CustomizeOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(8)

        def _lbl(txt, fs=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt); w.setAlignment(align)
            w.setFont(QFont("Consolas", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        _fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
               f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
               f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        lay.addWidget(_lbl("⚙  CUSTOMISE ASSISTANT", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_lbl("ASSISTANT NAME", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._name_input = QLineEdit(assistant_name)
        self._name_input.setFont(QFont("Consolas", 10))
        self._name_input.setFixedHeight(32)
        self._name_input.setStyleSheet(_fs)
        lay.addWidget(self._name_input)

        lay.addSpacing(4)
        lay.addWidget(_lbl("YOUR NAME  (leave blank for default sir / efendim)", 8,
                            color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._user_input = QLineEdit(user_name)
        self._user_input.setPlaceholderText("e.g.  Tony   (leave blank for auto)")
        self._user_input.setFont(QFont("Consolas", 10))
        self._user_input.setFixedHeight(32)
        self._user_input.setStyleSheet(_fs)
        lay.addWidget(self._user_input)

        # ── Assistant voice — Gemini prebuilt voices ─────────────────────────
        # Names are language-neutral proper nouns, so the row reads the same in
        # every locale. Selecting one and applying rebuilds the Live session.
        from memory.config_manager import AVAILABLE_VOICES, DEFAULT_VOICE
        lay.addSpacing(4)
        lay.addWidget(_lbl("ASSISTANT VOICE", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._sel_voice   = (voice or DEFAULT_VOICE)
        if self._sel_voice not in AVAILABLE_VOICES:
            self._sel_voice = DEFAULT_VOICE
        self._voice_btns: dict[str, QPushButton] = {}
        voice_row = QHBoxLayout(); voice_row.setSpacing(4)
        for _v in AVAILABLE_VOICES:
            b = QPushButton(_v)
            b.setCheckable(True)
            b.setFixedHeight(28)
            b.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, name=_v: self._on_voice_pick(name))
            self._voice_btns[_v] = b
            voice_row.addWidget(b)
        lay.addLayout(voice_row)
        self._refresh_voice_btns()

        # ── UI colour — colour wheel ─────────────────────────────────────────
        lay.addSpacing(4)
        clr_hdr = QHBoxLayout()
        clr_hdr.addWidget(_lbl("UI COLOUR  —  drag the handle", 8,
                               color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        clr_hdr.addStretch()
        df_btn = QPushButton("DEFAULT")
        df_btn.setFixedSize(64, 20)
        df_btn.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        df_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        df_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        df_btn.clicked.connect(lambda: self._set_color(DEFAULT_UI_COLOR))
        clr_hdr.addWidget(df_btn)
        lay.addLayout(clr_hdr)

        self._initial_color = (ui_color or DEFAULT_UI_COLOR).strip().lower()
        self._sel_color     = self._initial_color
        self.on_preview     = None   # callable(hex) — live preview; MainWindow wires it

        self._wheel = HueWheel(self._sel_color)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch(); wheel_row.addWidget(self._wheel); wheel_row.addStretch()
        lay.addLayout(wheel_row)
        self._wheel.hue_picked.connect(self._on_wheel_pick)
        self._wheel.hue_committed.connect(self._on_wheel_commit)

        self._hex_input = QLineEdit(self._sel_color)
        self._hex_input.setPlaceholderText("#00d4ff   (custom hex colour)")
        self._hex_input.setFont(QFont("Consolas", 10))
        self._hex_input.setFixedHeight(28)
        self._hex_input.setStyleSheet(_fs)
        self._hex_input.textEdited.connect(self._on_hex_edited)
        lay.addWidget(self._hex_input)

        lay.addSpacing(6)
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        save_btn = QPushButton("▸  APPLY CHANGES")
        save_btn.setFixedHeight(34)
        save_btn.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setFont(QFont("Consolas", 9))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    # ── voice selection ──────────────────────────────────────────────────────
    def _on_voice_pick(self, name: str):
        self._sel_voice = name
        self._refresh_voice_btns()

    def _refresh_voice_btns(self):
        """Highlight the selected voice pill; dim the rest."""
        for name, b in self._voice_btns.items():
            on = (name == self._sel_voice)
            b.setChecked(on)
            if on:
                b.setStyleSheet(f"""
                    QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                        border: 1px solid {C.PRI}; border-radius: 3px; }}
                """)
            else:
                b.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
                """)

    # ── colour flow ──────────────────────────────────────────────────────────
    def _set_color(self, hx: str, update_wheel: bool = True, preview: bool = True):
        """Updates the selected colour; hex box + wheel stay in sync, theme is live-previewed."""
        self._sel_color = hx.strip().lower()
        self._hex_input.blockSignals(True)
        self._hex_input.setText(self._sel_color)
        self._hex_input.blockSignals(False)
        if update_wheel:
            self._wheel.set_color(self._sel_color)
        if preview and self.on_preview:
            self.on_preview(self._sel_color)

    def _on_wheel_pick(self, hx: str):
        # While dragging: update the hex box, don't apply the theme yet
        self._sel_color = hx
        self._hex_input.blockSignals(True)
        self._hex_input.setText(hx)
        self._hex_input.blockSignals(False)

    def _on_wheel_commit(self, hx: str):
        # Handle released → live-preview the whole interface
        self._set_color(hx, update_wheel=False)

    def _on_hex_edited(self, text: str):
        t = text.strip().lower()
        if t.startswith("#") and len(t) == 7:
            try:
                int(t[1:], 16)
            except ValueError:
                return
            self._set_color(t, update_wheel=True, preview=True)

    def _cancel(self):
        # If a preview was applied, revert to the colour from launch
        if self.on_preview and self._sel_color != self._initial_color:
            self.on_preview(self._initial_color)
        self.hide()

    def _save(self):
        name = self._name_input.text().strip() or "ULTRON"
        user = self._user_input.text().strip()
        self.saved.emit(name, user, self._sel_color or DEFAULT_UI_COLOR, self._sel_voice)
        self.hide()


class PluginManagerOverlay(QWidget):
    """Floating overlay — lists discovered plugins with per-plugin ON/OFF toggles."""

    _OW = 420

    def __init__(self, plugins: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginManagerOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🧩  PLUGIN MANAGER")
        hdr.setFont(QFont("Consolas", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        if not plugins:
            empty = QLabel("No plugins found in /plugins.")
            empty.setFont(QFont("Consolas", 8))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(empty)

        for p in plugins:
            lay.addLayout(self._build_row(p))

        lay.addSpacing(4)
        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(30)
        close_btn.setFont(QFont("Consolas", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        lay.addWidget(close_btn)
        self.adjustSize()

    def _build_row(self, p: dict) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)

        label_text = p["name"] if p["valid"] else f"{p['name']}  (⚠ {p['file']})"
        lbl = QLabel(label_text)
        lbl.setFont(QFont("Consolas", 8))
        lbl.setStyleSheet(f"color: {C.TEXT if p['valid'] else C.TEXT_DIM}; background: transparent;")
        lbl.setToolTip(p["description"] if p["valid"] else p["error"])
        lbl.setWordWrap(False)
        row.addWidget(lbl, stretch=1)

        btn = QPushButton()
        btn.setFixedSize(72, 24)
        btn.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        if not p["valid"]:
            btn.setText("BROKEN")
            btn.setEnabled(False)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
            """)
        else:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._style_toggle(btn, p["enabled"])
            btn.clicked.connect(lambda _, name=p["name"], b=btn: self._toggle(name, b))
        row.addWidget(btn)
        return row

    def _style_toggle(self, btn: QPushButton, enabled: bool):
        if enabled:
            btn.setText("ON")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            btn.setText("OFF")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
            """)

    def _toggle(self, name: str, btn: QPushButton):
        from memory.config_manager import get_plugin_enabled, save_plugin_enabled
        new_val = not get_plugin_enabled(name)
        save_plugin_enabled(name, new_val)
        self._style_toggle(btn, new_val)
        from core.runtime_status import refresh_plugins
        refresh_plugins()


class _HudOverlay(QWidget):
    """Base for the floating panels placed by hand over the HUD.

    They are children of the central widget but sit in no layout, so Qt never
    invalidates the region they occupy when they hide or shrink: the HUD keeps
    painting around them and their last frame stays on screen as a ghost. Any
    overlay positioned with _centre_overlay needs this."""

    def hideEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            # Repaint exactly what we were covering, before we stop covering it.
            p.update(self.geometry())
        super().hideEvent(e)

    def closeEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            p.update(self.geometry())
        super().closeEvent(e)


class ConfirmBanner(_HudOverlay):
    """The gate in front of an action that cannot be taken back.

    The old confirmation was a tool parameter the model filled in itself, which
    means it confirmed its own shutdown requests. This is the interface asking,
    and the answer travels from a human finger to core/confirm.py without the
    model in the loop. Nothing blocks while it is up: the assistant keeps
    talking, so this costs no latency — unlike the old gate, which spent two
    tool round trips on every power command."""

    answered = pyqtSignal(bool)
    _OW = 430

    def __init__(self, title: str, detail: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ConfirmBanner {{
                background: rgba(14, 3, 0, 250);
                border: 1px solid {C.ACC};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)

        hdr = QLabel("⚠  CONFIRM")
        hdr.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(hdr)

        ttl = QLabel(title)
        ttl.setTextFormat(Qt.TextFormat.PlainText)
        ttl.setWordWrap(True)
        ttl.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(ttl)

        if detail:
            dtl = QLabel(detail)
            dtl.setTextFormat(Qt.TextFormat.PlainText)
            dtl.setWordWrap(True)
            dtl.setFont(QFont("Consolas", 8))
            dtl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            lay.addWidget(dtl)

        row = QHBoxLayout(); row.setSpacing(8)

        yes = QPushButton("▸  CONFIRM")
        yes.setFixedHeight(32)
        yes.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        yes.setCursor(Qt.CursorShape.PointingHandCursor)
        yes.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.ACC};
                border: 1px solid {C.ACC}; border-radius: 3px; }}
            QPushButton:hover {{ background: rgba(255,107,0,40); }}
        """)
        yes.clicked.connect(lambda: self.answered.emit(True))
        row.addWidget(yes)

        no = QPushButton("CANCEL")
        no.setFixedHeight(32)
        no.setFont(QFont("Consolas", 9))
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        no.clicked.connect(lambda: self.answered.emit(False))
        row.addWidget(no)
        lay.addLayout(row)

        # Default focus on CANCEL: if someone hits Enter without reading, the
        # safe answer wins.
        no.setDefault(True)
        no.setFocus()


class ScreenSourcePickerOverlay(_HudOverlay):
    """
    Discord-style modal picker for choosing an entire display (Screen 1, Screen 2)
    or a specific application window / tab (Chrome, YouTube, Discord, VS Code, etc.).
    """
    source_picked = pyqtSignal(dict)
    _OW = 560

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ScreenSourcePickerOverlay {{
                background: rgba(3, 7, 13, 252);
                border: 1px solid {C.PRI};
                border-radius: 8px;
            }}
        """)
        self.setFixedWidth(self._OW)

        self._selected_source = None
        self._all_buttons: list[tuple[QPushButton, dict]] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        # ── Header ──────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr_icon = QLabel("🖥")
        hdr_icon.setFont(QFont("Consolas", 13))
        hdr_icon.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(hdr_icon)

        hdr_title = QLabel("SHARE YOUR SCREEN OR APPLICATION")
        hdr_title.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        hdr_title.setStyleSheet(f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        hdr.addWidget(hdr_title)
        hdr.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM}; border: none; font-size: 14px; }}
            QPushButton:hover {{ color: {C.WHITE}; }}
        """)
        close_btn.clicked.connect(self.close)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        sub = QLabel("Select an entire screen or a specific window for Ultron to see and control:")
        sub.setFont(QFont("Consolas", 8))
        sub.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(sub)

        # ── Discord-Style Segmented Tabs ────────────────────────────────────
        tabs_lay = QHBoxLayout()
        tabs_lay.setSpacing(8)

        self._btn_screens = QPushButton("🖥  SCREENS")
        self._btn_screens.setFixedHeight(30)
        self._btn_screens.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        self._btn_screens.setCursor(Qt.CursorShape.PointingHandCursor)

        self._btn_windows = QPushButton("🗔  APPLICATIONS & TABS")
        self._btn_windows.setFixedHeight(30)
        self._btn_windows.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        self._btn_windows.setCursor(Qt.CursorShape.PointingHandCursor)

        self._tab_style_active = f"""
            QPushButton {{
                background: {C.PRI_GHO}; color: {C.PRI};
                border: 1px solid {C.PRI}; border-radius: 4px; padding: 4px 14px;
            }}
        """
        self._tab_style_idle = f"""
            QPushButton {{
                background: #000c14; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 4px; padding: 4px 14px;
            }}
            QPushButton:hover {{ color: {C.WHITE}; border-color: {C.BORDER_B}; background: #011422; }}
        """

        self._btn_screens.setStyleSheet(self._tab_style_active)
        self._btn_windows.setStyleSheet(self._tab_style_idle)

        self._btn_screens.clicked.connect(self._show_screens_tab)
        self._btn_windows.clicked.connect(self._show_windows_tab)

        tabs_lay.addWidget(self._btn_screens)
        tabs_lay.addWidget(self._btn_windows)
        tabs_lay.addStretch()

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.setFixedHeight(30)
        refresh_btn.setFont(QFont("Consolas", 8))
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.setStyleSheet(self._tab_style_idle)
        refresh_btn.clicked.connect(self._load_sources)
        tabs_lay.addWidget(refresh_btn)
        lay.addLayout(tabs_lay)

        # ── Stacked Pages ───────────────────────────────────────────────────
        self._stack = QStackedWidget()
        self._stack.setFixedHeight(230)
        self._stack.setStyleSheet(f"border: 1px solid {C.BORDER}; border-radius: 4px; background: #000810;")

        # Page 0: Screens
        self._screens_page = QWidget()
        self._screens_layout = QVBoxLayout(self._screens_page)
        self._screens_layout.setContentsMargins(8, 8, 8, 8)
        self._screens_layout.setSpacing(6)
        self._screens_scroll = QScrollArea()
        self._screens_scroll.setWidgetResizable(True)
        self._screens_scroll.setStyleSheet("background: transparent; border: none;")
        self._screens_scroll.setWidget(self._screens_page)
        self._stack.addWidget(self._screens_scroll)

        # Page 1: Windows
        self._windows_page = QWidget()
        self._windows_layout = QVBoxLayout(self._windows_page)
        self._windows_layout.setContentsMargins(8, 8, 8, 8)
        self._windows_layout.setSpacing(6)
        self._windows_scroll = QScrollArea()
        self._windows_scroll.setWidgetResizable(True)
        self._windows_scroll.setStyleSheet("background: transparent; border: none;")
        self._windows_scroll.setWidget(self._windows_page)
        self._stack.addWidget(self._windows_scroll)

        lay.addWidget(self._stack)

        # ── Bottom Bar ──────────────────────────────────────────────────────
        self._selected_label = QLabel("Selected: Primary Screen")
        self._selected_label.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        self._selected_label.setStyleSheet(f"color: {C.PRI}; background: transparent; border: none;")
        lay.addWidget(self._selected_label)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch()

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setFixedHeight(32)
        cancel_btn.setFixedWidth(90)
        cancel_btn.setFont(QFont("Consolas", 8))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(self._tab_style_idle)
        cancel_btn.clicked.connect(self.close)
        actions.addWidget(cancel_btn)

        self._share_btn = QPushButton("🖥  START SHARING")
        self._share_btn.setFixedHeight(32)
        self._share_btn.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        self._share_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._share_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI}; color: #000a12;
                border: 1px solid {C.PRI}; border-radius: 4px; padding: 4px 18px;
            }}
            QPushButton:hover {{ background: #40f7ff; border-color: #40f7ff; }}
        """)
        self._share_btn.clicked.connect(self._on_confirm_share)
        actions.addWidget(self._share_btn)

        lay.addLayout(actions)
        self._load_sources()

    def _show_screens_tab(self):
        self._stack.setCurrentIndex(0)
        self._btn_screens.setStyleSheet(self._tab_style_active)
        self._btn_windows.setStyleSheet(self._tab_style_idle)

    def _show_windows_tab(self):
        self._stack.setCurrentIndex(1)
        self._btn_windows.setStyleSheet(self._tab_style_active)
        self._btn_screens.setStyleSheet(self._tab_style_idle)

    def _load_sources(self):
        from core.screen_share import screen_share_manager
        self._all_buttons.clear()

        # Clear existing
        while self._screens_layout.count():
            item = self._screens_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        while self._windows_layout.count():
            item = self._windows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        sources = screen_share_manager.get_available_sources()
        screens = sources.get("screens", [])
        windows = sources.get("windows", [])

        # Populate Screens
        first_screen_data = None
        for scr in screens:
            btn = QPushButton(f"🖥  {scr['name']}   ({scr['width']}x{scr['height']})")
            btn.setFixedHeight(40)
            btn.setFont(QFont("Consolas", 8))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            data = {"type": "screen", "index": scr["index"], "name": scr["name"]}
            if first_screen_data is None:
                first_screen_data = (data, btn)
            btn.clicked.connect(lambda _, d=data, b=btn: self._select_source(d, b))
            self._screens_layout.addWidget(btn)
            self._all_buttons.append((btn, data))
        self._screens_layout.addStretch()

        # Populate Windows
        for win in windows:
            title = win["title"]
            if len(title) > 48:
                title = title[:45] + "..."
            pname = (win.get("process") or "").upper()
            btn = QPushButton(f"🗔  {title}\n    [{pname}]  ({win['width']}x{win['height']})")
            btn.setFixedHeight(46)
            btn.setFont(QFont("Consolas", 8))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            data = {
                "type": "window",
                "hwnd": win["hwnd"],
                "title": win["title"],
                "process": win.get("process", ""),
                "name": win["title"]
            }
            btn.clicked.connect(lambda _, d=data, b=btn: self._select_source(d, b))
            self._windows_layout.addWidget(btn)
            self._all_buttons.append((btn, data))

        if not windows:
            empty_lbl = QLabel("No active external application windows found.\n(Open Chrome, Discord, or YouTube and click Refresh)")
            empty_lbl.setFont(QFont("Consolas", 8))
            empty_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; border: none; padding: 20px;")
            self._windows_layout.addWidget(empty_lbl)
        self._windows_layout.addStretch()

        if first_screen_data:
            self._select_source(first_screen_data[0], first_screen_data[1])

    def _select_source(self, data: dict, active_btn: QPushButton = None):
        self._selected_source = data
        name = data.get("name") or data.get("title") or "Source"
        self._selected_label.setText(f"Selected: {name}")

        card_idle = f"""
            QPushButton {{
                text-align: left; padding: 6px 12px;
                background: #011422; border: 1px solid {C.BORDER};
                border-radius: 4px; color: {C.TEXT};
            }}
            QPushButton:hover {{ border-color: {C.BORDER_B}; background: #011e33; color: {C.WHITE}; }}
        """
        card_sel = f"""
            QPushButton {{
                text-align: left; padding: 6px 12px;
                background: #01283d; border: 1px solid {C.PRI};
                border-radius: 4px; color: {C.WHITE}; font-weight: bold;
            }}
        """
        for b, _ in self._all_buttons:
            try:
                b.setStyleSheet(card_sel if b is active_btn else card_idle)
            except Exception:
                pass

    def _on_confirm_share(self):
        if self._selected_source:
            self.source_picked.emit(self._selected_source)
        self.close()


class AudioDeviceOverlay(_HudOverlay):
    """Choose which microphone ULTRON listens to and which speakers it uses.

    Both audio streams used to open with no `device=` at all, so they always
    took the OS default — which on Windows moves by itself the moment a headset
    is plugged in. 'ULTRON can't hear me' is usually 'ULTRON is listening to the
    webcam'."""

    picked = pyqtSignal()      # emitted after Apply, when something changed
    _OW = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        from core.audio_devices import list_devices, DEFAULT_LABEL
        from memory.config_manager import get_input_device, get_output_device

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            AudioDeviceOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🎧  AUDIO DEVICES")
        hdr.setFont(QFont("Consolas", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        _combo_css = (
            f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
            f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
            f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
            f"selection-background-color: {C.PRI_GHO}; border: 1px solid {C.BORDER}; }}"
        )

        def _row(label: str, kind: str, current: str) -> QComboBox:
            cap = QLabel(label)
            cap.setFont(QFont("Consolas", 8))
            cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(cap)

            box = QComboBox()
            box.setFont(QFont("Consolas", 9))
            box.setFixedHeight(30)
            box.setStyleSheet(_combo_css)
            # The list is served from a cache warmed on a background thread at
            # startup, so opening this panel never blocks the Qt thread on the
            # host audio API.
            box.addItem(DEFAULT_LABEL, "")
            for name in list_devices(kind):
                box.addItem(name, name)
            idx = box.findData(current) if current else 0
            box.setCurrentIndex(idx if idx >= 0 else 0)
            if current and idx < 0:
                # Saved device is not plugged in right now. Show it rather than
                # silently resetting the user's choice to default.
                box.addItem(f"{current}  (not connected)", current)
                box.setCurrentIndex(box.count() - 1)
            lay.addWidget(box)
            return box

        self._in_box  = _row("MICROPHONE — what ULTRON hears you with",
                             "input", get_input_device())
        lay.addSpacing(4)
        self._out_box = _row("SPEAKERS — what ULTRON talks through",
                             "output", get_output_device())

        note = QLabel("Applying reconnects the session. Your conversation is kept.")
        note.setWordWrap(True)
        note.setFont(QFont("Consolas", 7))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addSpacing(6)
        lay.addWidget(note)

        row = QHBoxLayout(); row.setSpacing(8)
        ok = QPushButton("▸  APPLY")
        ok.setFixedHeight(32)
        ok.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        ok.clicked.connect(self._apply)
        row.addWidget(ok)

        cancel = QPushButton("CLOSE")
        cancel.setFixedHeight(32)
        cancel.setFont(QFont("Consolas", 9))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel.clicked.connect(self.hide)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _apply(self):
        from memory.config_manager import (
            get_input_device, get_output_device,
            save_input_device, save_output_device,
        )
        new_in  = self._in_box.currentData()  or ""
        new_out = self._out_box.currentData() or ""
        changed = (new_in != get_input_device()) or (new_out != get_output_device())
        save_input_device(new_in)
        save_output_device(new_out)
        self.hide()
        # Only rebuild the session if something actually moved — a no-op Apply
        # should not cost a reconnect.
        if changed:
            self.picked.emit()


class AnimationSettingsOverlay(_HudOverlay):
    """Cinematics, colour theme and sound — all switchable, all persisted.

    Every change is saved the moment it is made (config/api_keys.json →
    "animations", "ui_theme") and applied live, so there is no Apply button to
    forget and the replay / preview buttons always play what the switches say."""

    changed = pyqtSignal()
    preview = pyqtSignal(str)          # "welcome" | "boot" | "shutdown"
    theme = pyqtSignal(str)            # a theme name, "rgb", "original" or "#rrggbb"
    _OW = 860

    _CINEMATICS = (
        ("welcome",         "WELCOME NARRATION",    "Your recorded introduction at launch (else the classic boot)"),
        ("startup",         "STARTUP ANIMATION",    "Play an intro at all when ULTRON launches"),
        ("skip_intro",      "SKIP INTRO ON LAUNCH", "Go straight to the dashboard"),
        ("voice_handoff",   "VOICE HANDOFF",        "After the welcome, ULTRON speaks once in its live voice"),
        ("color_transform", "COLOR TRANSFORMATION", "Crack, shatter and lightning when the colour changes"),
        ("shutdown",        "SHUTDOWN ANIMATION",   "Electric slash that cuts the screen in half on exit"),
    )
    _EFFECTS = (
        ("lightning", "LIGHTNING EFFECTS", "Branching bolts, arcs and strikes"),
        ("glitch",    "GLITCH EFFECTS",    "RGB split, torn scanlines, digital interference"),
        ("sound",     "SOUND EFFECTS",     "Ducked under speech, never louder than ULTRON's voice"),
        ("motion",    "UI MOTION EFFECTS", "AI core breathing, listening rings, speech arcs"),
    )
    _WINDOW = (
        ("launch_fullscreen", "LAUNCH IN FULL SCREEN", "Open full screen every time ULTRON starts · F11 toggles"),
    )

    def __init__(self, settings, current_theme=None, parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QGridLayout, QSlider
        self._QSlider = QSlider
        self._s = settings
        self._current = current_theme
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            AnimationSettingsOverlay {{
                background: rgba(0, 6, 10, 246);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(6)
        hdr = QLabel("✦  ANIMATIONS & THEME")
        hdr.setFont(QFont("Consolas", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        outer.addWidget(hdr)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        outer.addWidget(sep)

        cols = QHBoxLayout(); cols.setSpacing(22)
        left = QVBoxLayout(); left.setSpacing(5)
        right = QVBoxLayout(); right.setSpacing(6)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        outer.addLayout(cols)

        self._switches: dict[str, ToggleSwitch] = {}
        left.addWidget(self._section("CINEMATICS"))
        for key, title, hint in self._CINEMATICS:
            left.addLayout(self._toggle_row(key, title, hint))
        left.addSpacing(6)
        left.addWidget(self._section("EFFECTS"))
        for key, title, hint in self._EFFECTS:
            left.addLayout(self._toggle_row(key, title, hint))
        left.addSpacing(6)
        left.addWidget(self._section("WINDOW"))
        for key, title, hint in self._WINDOW:
            left.addLayout(self._toggle_row(key, title, hint))
        left.addStretch(1)

        # theme picker: one swatch per named colour, RGB, and a custom hex
        right.addWidget(self._section("COLOR THEME  ·  also by voice: \"Ultron, switch to blue\""))
        grid = QGridLayout(); grid.setSpacing(6)
        from core.theme import THEMES
        entries = [(name, label, hx) for name, (label, hx) in THEMES.items()] + [("rgb", "RGB", "")]
        for i, (name, label, hx) in enumerate(entries):
            b = QPushButton(label)
            b.setFixedHeight(26)
            b.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setProperty("_ultron_no_theme", True)       # a swatch shows its own colour, always
            active = current_theme is not None and current_theme.name == name
            ring = "#ffffff" if active else "rgba(255,255,255,40)"
            if name == "rgb":
                fill = ("qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #ff163c, stop:0.25 #ffd700, "
                        "stop:0.5 #00ff88, stop:0.75 #0088ff, stop:1 #a020f0)")
                ink = "#000000"
            else:
                fill = hx
                ink = "#000000" if name in ("gold", "white", "cyan", "green") else "#ffffff"
            b.setStyleSheet(f"QPushButton {{ background: {fill}; color: {ink}; border: 2px solid {ring}; "
                            f"border-radius: 4px; }} QPushButton:hover {{ border-color: #ffffff; }}")
            b.clicked.connect(lambda _c=False, n=name: self._pick_theme(n))
            grid.addWidget(b, i // 5, i % 5)
        right.addLayout(grid)
        hx_row = QHBoxLayout(); hx_row.setSpacing(8)
        self._hex = QLineEdit()
        self._hex.setPlaceholderText("#00FFFF — custom colour")
        self._hex.setMaxLength(7)
        self._hex.setFixedHeight(28)
        self._hex.setFont(QFont("Consolas", 9))
        self._hex.setStyleSheet(f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
                                f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 2px 8px; }}")
        self._hex.returnPressed.connect(self._apply_hex)
        hx_row.addWidget(self._hex, 1)
        hx_btn = self._btn("APPLY HEX", True)
        hx_btn.setFixedHeight(28)
        hx_btn.clicked.connect(self._apply_hex)
        hx_row.addWidget(hx_btn)
        orig = self._btn("ORIGINAL", False)
        orig.setFixedHeight(28)
        orig.setToolTip("ULTRON red — what \"return to your original colour\" means")
        orig.clicked.connect(lambda: self._pick_theme("original"))
        hx_row.addWidget(orig)
        right.addLayout(hx_row)
        self._hex_note = QLabel("")
        self._hex_note.setFont(QFont("Consolas", 7))
        self._hex_note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        right.addWidget(self._hex_note)

        right.addWidget(self._section("PERFORMANCE & MOTION"))
        self._seg_rows: dict[str, list[tuple[QPushButton, str]]] = {}
        self._segmented(right, "quality", "QUALITY",
                        (("low", "LOW"), ("medium", "MEDIUM"), ("ultra", "ULTRA")))
        self._segmented(right, "reduced_motion", "REDUCED MOTION",
                        (("auto", "AUTO"), ("on", "ON"), ("off", "OFF")))
        self._sliders: dict[str, tuple] = {}
        self._slider(right, "speed", "ANIMATION SPEED", 50, 200, lambda v: f"{v / 100:.2f}×")
        self._slider(right, "sfx_volume", "EFFECTS VOLUME", 0, 100, lambda v: f"{v}%")
        self._slider(right, "narration_volume", "NARRATION VOLUME", 0, 100, lambda v: f"{v}%")
        note = QLabel("Reduced motion drops shake, flashes, glitches and large movement; AUTO follows "
                      "Windows › Accessibility › Animation effects. ESC skips any intro.")
        note.setWordWrap(True)
        note.setFont(QFont("Consolas", 7))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        right.addWidget(note)
        right.addStretch(1)

        row = QHBoxLayout(); row.setSpacing(8)
        for label, kind, tip in (("▶  REPLAY WELCOME", "welcome", "Your narrated introduction"),
                                 ("▶  PREVIEW BOOT", "boot", "The classic boot, without narration"),
                                 ("▶  PREVIEW SHUTDOWN", "shutdown",
                                  "Plays the shutdown sequence, then returns — ULTRON keeps running")):
            b = self._btn(label, True)
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, k=kind: self.preview.emit(k))
            row.addWidget(b)
        close = self._btn("CLOSE", False)
        close.clicked.connect(self.hide)
        row.addWidget(close)
        outer.addSpacing(6)
        outer.addLayout(row)

    # ── building blocks ──────────────────────────────────────────────────────
    @staticmethod
    def _section(text: str) -> QLabel:
        l = QLabel(text)
        l.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        l.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; padding-top: 2px;")
        return l

    def _btn(self, text: str, primary: bool) -> QPushButton:
        b = QPushButton(text)
        b.setFixedHeight(32)
        b.setFont(QFont("Consolas", 9, QFont.Weight.Bold if primary else QFont.Weight.Normal))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        if primary:
            b.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; padding: 0 10px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
            """)
        else:
            b.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.TEXT_MED};
                    border: 1px solid {C.BORDER}; border-radius: 3px; padding: 0 10px; }}
                QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
            """)
        return b

    def _toggle_row(self, key: str, title: str, hint: str) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(10)
        col = QVBoxLayout(); col.setSpacing(0)
        t = QLabel(title)
        t.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        h = QLabel(hint)
        h.setFont(QFont("Consolas", 7))
        h.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        col.addWidget(t); col.addWidget(h)
        row.addLayout(col, 1)
        sw = ToggleSwitch(bool(getattr(self._s, key)), C.PRI)
        sw.setToolTip(hint)
        sw.toggled.connect(lambda on, k=key: self._set(k, bool(on)))
        row.addWidget(sw, 0, Qt.AlignmentFlag.AlignVCenter)
        self._switches[key] = sw
        return row

    def _segmented(self, lay, key: str, title: str, options) -> None:
        row = QHBoxLayout(); row.setSpacing(0)
        cap = QLabel(title)
        cap.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        cap.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        cap.setFixedWidth(140)
        row.addWidget(cap)
        buttons = []
        for i, (value, label) in enumerate(options):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setFixedHeight(24)
            b.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            left = "3px" if i == 0 else "0px"
            right = "3px" if i == len(options) - 1 else "0px"
            b.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-top-left-radius: {left};
                    border-bottom-left-radius: {left}; border-top-right-radius: {right};
                    border-bottom-right-radius: {right}; padding: 0 10px; }}
                QPushButton:hover {{ color: {C.TEXT}; }}
                QPushButton:checked {{ background: {C.PRI_GHO}; color: {C.PRI}; border-color: {C.PRI_DIM}; }}
            """)
            b.setChecked(str(getattr(self._s, key)) == value)
            b.clicked.connect(lambda _c=False, k=key, v=value: self._pick(k, v))
            buttons.append((b, value))
            row.addWidget(b, 1)
        self._seg_rows[key] = buttons
        lay.addLayout(row)

    def _slider(self, lay, key: str, title: str, lo: int, hi: int, fmt) -> None:
        row = QHBoxLayout(); row.setSpacing(10)
        lbl = QLabel()
        lbl.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lbl.setFixedWidth(190)
        row.addWidget(lbl)
        sl = self._QSlider(Qt.Orientation.Horizontal)
        sl.setRange(lo, hi)
        sl.setSingleStep(5)
        sl.setPageStep(10)
        sl.setValue(int(round(float(getattr(self._s, key)) * 100)))
        sl.setStyleSheet(f"""
            QSlider::groove:horizontal {{ height: 4px; background: {C.BORDER}; border-radius: 2px; }}
            QSlider::sub-page:horizontal {{ background: {C.PRI_DIM}; border-radius: 2px; }}
            QSlider::handle:horizontal {{ background: {C.PRI}; width: 12px; margin: -5px 0; border-radius: 6px; }}
        """)
        sl.valueChanged.connect(lambda v, k=key: self._on_slider(k, v))
        sl.sliderReleased.connect(self._save)
        row.addWidget(sl, 1)
        lay.addLayout(row)
        self._sliders[key] = (sl, lbl, title, fmt)
        lbl.setText(f"{title}  {fmt(sl.value())}")

    # ── actions ──────────────────────────────────────────────────────────────
    def _pick(self, key: str, value: str) -> None:
        for b, v in self._seg_rows[key]:
            b.setChecked(v == value)
        self._set(key, value)

    def _pick_theme(self, name: str) -> None:
        self.hide()                     # the transformation needs the whole screen
        self.theme.emit(name)

    def _apply_hex(self) -> None:
        from core.theme import resolve
        txt = self._hex.text().strip()
        spec = resolve(txt if txt.startswith("#") else "#" + txt)
        if spec is None:
            self._hex_note.setText("Not a colour — use #RRGGBB, e.g. #00FFFF.")
            return
        self._hex_note.setText("")
        self._pick_theme(spec.hex)

    def _on_slider(self, key: str, v: int) -> None:
        sl, lbl, title, fmt = self._sliders[key]
        setattr(self._s, key, round(v / 100.0, 2))
        lbl.setText(f"{title}  {fmt(v)}")
        if not sl.isSliderDown():
            self._save()

    def _set(self, key: str, value) -> None:
        setattr(self._s, key, value)
        self._save()

    def _save(self) -> None:
        try:
            self._s.save()
        except Exception as e:
            print(f"[Cinematic] could not save animation settings: {e}")
        self.changed.emit()


class MemoryOverlay(_HudOverlay):
    """Everything ULTRON has stored about you, and when it learned it.

    Memory used to be a 2200-character store that deleted its oldest entries
    when full and mentioned it only on stdout. The cap is gone; this panel is
    the other half of that change — a memory you cannot inspect is a memory you
    cannot trust, and 'delete' has to be something the person can do."""

    _OW = 520

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            MemoryOverlay {{
                background: rgba(0, 6, 10, 246);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(20, 16, 20, 16)
        self._lay.setSpacing(5)
        self._rebuild()

    def _clear_layout(self):
        """Take every item out of the layout and detach it from the widget tree
        in this call.

        deleteLater() on its own is not enough: it queues destruction for the
        next event-loop pass, and until then the old rows are still children of
        this widget and still paint — which is what drew half of the previous
        panel over the new one. setParent(None) removes them from the tree now;
        deleteLater() then frees them safely."""
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # hide() stops it painting in this frame; deleteLater() frees it
                # safely afterwards. setParent(None) would also stop the paint,
                # but it turns the widget into a top-level window for the moment
                # between the two calls, which is not something to leave lying
                # around inside a click handler.
                w.hide()
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                while sub.count():
                    si = sub.takeAt(0)
                    sw = si.widget()
                    if sw is not None:
                        sw.hide()
                        sw.deleteLater()
                sub.deleteLater()

    def _settle(self, before):
        """Size the panel to its content, re-centre it, and repaint what the old
        size covered.

        The re-size has to happen here rather than at the end of _rebuild
        because Qt has not polished the freshly-created children at that point,
        so the size hint it would read is the empty-layout one. Measured: a
        first adjustSize() returned 32 px for a panel whose content needed 155,
        and a second call — after the same widgets had been through the event
        loop — returned 155. So this runs twice: once now, once on the next
        turn, from _rebuild.

        The re-centre and the repaint are needed because the overlay is placed
        by hand and is in no layout: shrinking it leaves it off-centre and
        leaves its former pixels on screen, since nothing tells the parent that
        region changed. The repaint has to cover the union of the old and new
        rectangles."""
        self._lay.invalidate()
        self._lay.activate()
        self.updateGeometry()
        self.adjustSize()

        p = self.parentWidget()
        if p is None:
            self.update()
            return
        self.move(max(0, (p.width()  - self.width())  // 2),
                  max(0, (p.height() - self.height()) // 2))
        p.update(before.united(self.geometry()))
        self.update()

    def _rebuild(self):
        before = self.geometry()
        self._clear_layout()

        from memory.memory_manager import all_entries_for_ui

        hdr = QLabel("🧠  WHAT ULTRON REMEMBERS")
        hdr.setFont(QFont("Consolas", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        self._lay.addWidget(sep)

        rows = all_entries_for_ui()

        cap = QLabel(f"{len(rows)} stored facts — newest first. "
                     f"Nothing here is sent anywhere; it lives in "
                     f"memory/long_term.json on this machine.")
        cap.setWordWrap(True)
        cap.setFont(QFont("Consolas", 7))
        cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._lay.addWidget(cap)

        if not rows:
            empty = QLabel("Nothing stored yet.")
            empty.setFont(QFont("Consolas", 9))
            empty.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            self._lay.addWidget(empty)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFixedHeight(min(420, 34 * len(rows) + 10))
            scroll.setStyleSheet(
                f"QScrollArea {{ border: 1px solid {C.BORDER}; border-radius: 3px; "
                f"background: transparent; }}"
            )
            inner = QWidget()
            ilay  = QVBoxLayout(inner)
            ilay.setContentsMargins(6, 6, 6, 6)
            ilay.setSpacing(3)

            for r in rows:
                line = QHBoxLayout(); line.setSpacing(6)
                txt = QLabel(f"<b>{r['key'].replace('_', ' ')}</b> "
                             f"<span style='color:{C.TEXT_MED}'>— {r['value']}</span>")
                txt.setWordWrap(True)
                txt.setFont(QFont("Consolas", 8))
                txt.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
                line.addWidget(txt, 1)

                meta = QLabel(f"{r['category'][:4]} · {r['updated'] or '—'}")
                meta.setFont(QFont("Consolas", 7))
                meta.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
                line.addWidget(meta)

                rm = QPushButton("✕")
                rm.setFixedSize(20, 20)
                rm.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
                rm.setCursor(Qt.CursorShape.PointingHandCursor)
                rm.setToolTip("Forget this")
                rm.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.RED}; border-color: {C.RED}; }}
                """)
                rm.clicked.connect(
                    lambda _=False, c=r["category"], k=r["key"]: self._forget(c, k))
                line.addWidget(rm)

                holder = QWidget()
                holder.setLayout(line)
                ilay.addWidget(holder)

            ilay.addStretch()
            scroll.setWidget(inner)
            self._lay.addWidget(scroll)

        close = QPushButton("CLOSE")
        close.setFixedHeight(30)
        close.setFont(QFont("Consolas", 9))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close.clicked.connect(self.hide)
        self._lay.addWidget(close)

        self._settle(before)
        # …and again once Qt has polished the new children, because the size
        # hint is not final until then. Harmless when the first pass already
        # got it right: _settle is idempotent.
        QTimer.singleShot(0, lambda g=before: self._settle(g))

    def _forget(self, category: str, key: str):
        from memory.memory_manager import forget
        forget(key, category)
        # Rebuild on the NEXT event-loop turn, not inside this click handler.
        # The rebuild destroys the very ✕ button that emitted this signal, and
        # Qt is entitled to touch the sender after a slot returns; tearing it
        # down mid-emission is how a widget ends up half-alive on screen.
        QTimer.singleShot(0, self._rebuild)


class ClipboardPanel(QWidget):
    """Floating panel shown when text is copied — offers quick Ultron actions."""

    action_requested = pyqtSignal(str)
    _W, _H = 326, 112

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ClipboardPanel {{
                background: rgba(0, 8, 14, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)
        self._clip_text = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 7)
        lay.setSpacing(4)

        hdr = QHBoxLayout(); hdr.setSpacing(4)
        icon_lbl = QLabel("◈  CLIPBOARD DETECTED")
        icon_lbl.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        icon_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        hdr.addWidget(icon_lbl); hdr.addStretch()
        x_btn = QPushButton("✕")
        x_btn.setFixedSize(16, 16)
        x_btn.setFont(QFont("Consolas", 8))
        x_btn.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.clicked.connect(self.hide)
        hdr.addWidget(x_btn)
        lay.addLayout(hdr)

        self._preview = QLabel()
        self._preview.setFont(QFont("Consolas", 8))
        self._preview.setStyleSheet(f"""
            color: {C.TEXT}; background: {C.PANEL2};
            border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 6px;
        """)
        self._preview.setWordWrap(False)
        self._preview.setFixedHeight(28)
        lay.addWidget(self._preview)

        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        _bs = (f"QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED}; "
               f"border: 1px solid {C.BORDER}; border-radius: 2px; }}"
               f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}")
        for label, cmd_fmt in [
            ("TRANSLATE", "Translate this text to English: {text}"),
            ("SUMMARISE", "Summarise this: {text}"),
            ("EXPLAIN",   "Explain this: {text}"),
            ("FIX",       "Fix grammar and spelling: {text}"),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_bs)
            b.clicked.connect(lambda _, c=cmd_fmt: self._trigger(c))
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.hide)
        self.hide()

    def _trigger(self, cmd_fmt: str):
        if self._clip_text:
            self.action_requested.emit(cmd_fmt.format(text=self._clip_text[:800]))
        self.hide()

    def show_clipboard(self, text: str):
        self._clip_text = text
        preview = text[:58].replace('\n', ' ')
        if len(text) > 58:
            preview += "…"
        self._preview.setText(f'"{preview}"')
        self.show(); self.raise_()
        self._dismiss_timer.start(8000)


class PluginSettingsOverlay(QWidget):
    """Floating overlay — renders per-plugin settings forms.

    Fully generic: it iterates the settings schemas a plugin declared via its
    PLUGIN_SETTINGS constant (delivered by PluginRegistry.settings_schemas) and
    builds a form for each. It knows NOTHING about any specific plugin, so the
    core stays clean and plugins remain pure drop-in — install a plugin that
    declares fields (e.g. the 3D-printer suite) and its section appears here;
    install none and this panel simply says there's nothing to configure.
    """

    _test_done = pyqtSignal(str, bool, str)   # namespace, ok, message
    _OW = 460

    def __init__(self, sections: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginSettingsOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self._sections = sections or []
        self._widgets: dict[tuple, object] = {}    # (namespace, key) -> input widget
        self._types:   dict[tuple, str]    = {}     # (namespace, key) -> field type
        self._status_labels: dict[str, QLabel] = {} # namespace -> status QLabel
        self._test_done.connect(self._on_test_done)

        self._fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
                    f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(8)

        root.addWidget(self._lbl("⚙  PLUGIN SETTINGS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        root.addWidget(sep)

        if not self._sections:
            root.addWidget(self._lbl(
                "No configurable plugins are installed.\nDrop a plugin that needs "
                "settings (like the 3D-printer suite) into the plugins folder and "
                "it will show up here.", 9, color=C.TEXT_DIM))
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setStyleSheet("QScrollArea { background: transparent; }")
            inner = QWidget()
            inner.setStyleSheet("background: transparent;")
            form = QVBoxLayout(inner)
            form.setContentsMargins(0, 0, 6, 0)
            form.setSpacing(6)
            for sec in self._sections:
                self._build_section(form, sec)
            form.addStretch(1)
            scroll.setWidget(inner)
            root.addWidget(scroll, 1)

        # ── bottom buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        if self._sections:
            save_btn = QPushButton("▸  SAVE")
            save_btn.setFixedHeight(34)
            save_btn.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
            save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            save_btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
            """)
            save_btn.clicked.connect(self._save_all)
            btn_row.addWidget(save_btn)

        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(34)
        close_btn.setFont(QFont("Consolas", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _lbl(self, txt, fs=9, bold=False, color=C.PRI,
             align=Qt.AlignmentFlag.AlignLeft):
        w = QLabel(txt); w.setAlignment(align); w.setWordWrap(True)
        w.setFont(QFont("Consolas", fs,
                        QFont.Weight.Bold if bold else QFont.Weight.Normal))
        w.setStyleSheet(f"color: {color}; background: transparent;")
        return w

    def _build_section(self, form: QVBoxLayout, sec: dict):
        ns     = sec.get("namespace") or sec.get("plugin") or "plugin"
        title  = sec.get("title") or ns
        fields = sec.get("fields") or []
        values = sec.get("values") or {}

        form.addSpacing(4)
        form.addWidget(self._lbl(title, 10, True, C.PRI))

        for field in fields:
            if not isinstance(field, dict) or not field.get("key"):
                continue
            key   = field["key"]
            ftype = (field.get("type") or "text").lower()
            label = field.get("label") or key
            default = field.get("default")
            stored  = values.get(key, default)

            form.addWidget(self._lbl(label.upper(), 8, color=C.TEXT_DIM))

            if ftype == "choice":
                w = QComboBox()
                w.addItems([str(o) for o in field.get("options", [])])
                w.setFont(QFont("Consolas", 9))
                w.setFixedHeight(30)
                w.setStyleSheet(
                    f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 2px 8px; }}"
                    f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
                    f"selection-background-color: {C.PRI_GHO}; }}")
                if stored is not None:
                    w.setCurrentText(str(stored))
            elif ftype == "toggle":
                w = QPushButton()
                w.setCheckable(True)
                w.setChecked(bool(stored))
                w.setFixedHeight(28)
                w.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
                w.setCursor(Qt.CursorShape.PointingHandCursor)
                self._style_toggle(w)
                w.toggled.connect(lambda _=False, b=w: self._style_toggle(b))
            else:  # text / password
                w = QLineEdit("" if stored is None else str(stored))
                w.setFont(QFont("Consolas", 10))
                w.setFixedHeight(30)
                w.setStyleSheet(self._fs)
                if field.get("placeholder"):
                    w.setPlaceholderText(str(field["placeholder"]))
                if ftype == "password":
                    w.setEchoMode(QLineEdit.EchoMode.Password)

            self._widgets[(ns, key)] = w
            self._types[(ns, key)]   = ftype
            form.addWidget(w)

        # optional test/connect action button + status line
        action = sec.get("action")
        if isinstance(action, dict) and callable(action.get("run")):
            form.addSpacing(2)
            ab = QPushButton(str(action.get("label") or "TEST"))
            ab.setFixedHeight(30)
            ab.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
            ab.setCursor(Qt.CursorShape.PointingHandCursor)
            ab.setStyleSheet(f"""
                QPushButton {{ background: #00091a; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
            """)
            ab.clicked.connect(lambda _=False, n=ns: self._run_action(n))
            form.addWidget(ab)

        status = self._lbl("", 8, color=C.TEXT_DIM)
        self._status_labels[ns] = status
        form.addWidget(status)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {C.BORDER}; margin: 4px 0;")
        form.addWidget(line)

    def _style_toggle(self, btn: QPushButton):
        on = btn.isChecked()
        btn.setText("ON" if on else "OFF")
        if on:
            btn.setStyleSheet(f"QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI}; "
                              f"border: 1px solid {C.PRI}; border-radius: 3px; }}")
        else:
            btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; "
                              f"border: 1px solid {C.BORDER}; border-radius: 3px; }}")

    # ── data ──────────────────────────────────────────────────────────────────
    def _gather(self, ns: str) -> dict:
        out = {}
        for (n, key), w in self._widgets.items():
            if n != ns:
                continue
            t = self._types.get((n, key), "text")
            if t == "choice":
                out[key] = w.currentText()
            elif t == "toggle":
                out[key] = w.isChecked()
            else:
                out[key] = w.text().strip()
        return out

    def _save_ns(self, ns: str):
        from memory.config_manager import save_plugin_config
        save_plugin_config(ns, self._gather(ns))

    def _save_all(self):
        for sec in self._sections:
            ns = sec.get("namespace") or sec.get("plugin")
            if ns:
                self._save_ns(ns)
                lbl = self._status_labels.get(ns)
                if lbl:
                    lbl.setText("Saved ✓")
                    lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")

    def _run_action(self, ns: str):
        sec = next((s for s in self._sections
                    if (s.get("namespace") or s.get("plugin")) == ns), None)
        if not sec:
            return
        run_fn = (sec.get("action") or {}).get("run")
        if not callable(run_fn):
            return
        self._save_ns(ns)                 # persist what the user typed before testing
        values = self._gather(ns)
        lbl = self._status_labels.get(ns)
        if lbl:
            lbl.setText("Testing…")
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")

        def worker():
            try:
                res = run_fn(values)
                if isinstance(res, tuple) and len(res) == 2:
                    ok, msg = bool(res[0]), str(res[1])
                else:
                    ok, msg = bool(res), str(res)
            except Exception as e:
                ok, msg = False, str(e)
            self._test_done.emit(ns, ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _on_test_done(self, ns: str, ok: bool, msg: str):
        lbl = self._status_labels.get(ns)
        if not lbl:
            return
        lbl.setText(msg)
        color = C.PRI if ok else "#ff6b6b"
        lbl.setStyleSheet(f"color: {color}; background: transparent;")


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for instant phone pairing + manual key fallback."""

    closed = pyqtSignal()

    _OW, _OH = 400, 465

    def __init__(self, url: str, key: str, auto_login_url: str = "",
                 manual_url: str = "", expiry_secs: int = 600, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: rgba(0, 4, 12, 0.95);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self._expiry          = time.time() + expiry_secs
        self._on_new_key      = None
        self._auto_login_url  = auto_login_url
        self._manual_url      = manual_url or url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(txt, fs=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Consolas", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  REMOTE ACCESS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        # ── QR code ───────────────────────────────────────────────────────────
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 10px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        self._update_qr(auto_login_url)

        lay.addWidget(_lbl("Scan with phone camera to connect instantly", 8, color=C.TEXT_DIM))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_lbl("Or enter manually:", 7, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(QFont("Consolas", 8))
        self._url_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(QFont("Consolas", 28, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.ACC};
            background: {C.PANEL2};
            border: 1px solid {C.BORDER_B};
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(QFont("Consolas", 8))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        new_btn = QPushButton("NEW KEY")
        new_btn.setFixedHeight(32)
        new_btn.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("DISMISS")
        close_btn.setFixedHeight(32)
        close_btn.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(
                box_size=5, border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(QFont("Consolas", 8))
            self._qr_label.setStyleSheet(
                "color: #888; background: white; border-radius: 10px; padding: 4px;"
            )
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(QFont("Consolas", 7))
            self._qr_label.setStyleSheet(
                f"color: {C.PRI}; background: white; border-radius: 10px; padding: 4px;"
            )

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        """Call from any thread when a phone successfully connects."""
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(34,197,94,0.08);
            border: 2px solid rgba(34,197,94,0.4);
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(QFont("Consolas", 54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            "color: #00ff88; background: #001a0d; border-radius: 10px;"
        )
        self._timer_lbl.setText("Phone connected — ULTRON ready")
        self._timer_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url    = result[0]
                key    = result[1]
                auto   = result[2] if len(result) >= 3 else ""
                manual = result[3] if len(result) >= 4 else url
                self._manual_url     = manual or url
                self._url_lbl.setText(self._manual_url)
                self._key_lbl.setText(key)
                self._auto_login_url = auto
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.ACC};
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 8px;
                    padding: 6px 4px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.TEXT_MED}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


class MainWindow(QMainWindow):
    _integration_sig = pyqtSignal(str, object)
    _log_sig        = pyqtSignal(str)
    _state_sig      = pyqtSignal(str)
    _content_sig    = pyqtSignal(str, str)   # (title, text) — thread-safe content display
    _reconfig_sig   = pyqtSignal()           # trigger setup overlay from any thread
    _camera_sig     = pyqtSignal(bytes)      # show camera frame preview (small overlay)
    _cam_stream_sig = pyqtSignal(bool)       # True=start live stream, False=stop
    _cam_frame_sig  = pyqtSignal(bytes)      # live camera frame → HUD area
    _video_open_sig  = pyqtSignal(str, str, bool, str)  # video, title, muted, audio
    _wake_btns_sig   = pyqtSignal()          # wake state resolved off-thread
    _video_close_sig = pyqtSignal()
    _video_mute_sig  = pyqtSignal(bool)
    _clipboard_sig  = pyqtSignal(str)        # clipboard text changed (thread-safe)
    _confirm_sig    = pyqtSignal(str, str, str)   # (title, detail) — irreversible-action gate
    _confirm_hide_sig = pyqtSignal()
    _wake_dl_sig    = pyqtSignal(bool, str)  # wake-word install finished (ok, message)
    _quiz_sig       = pyqtSignal(str, object, object)  # (topic, questions, grader)
    _quiz_hide_sig  = pyqtSignal()
    _review_sig     = pyqtSignal(str, str, object, object)  # document review payload
    _screen_move_sig = pyqtSignal(str, bool)                # (target_screen, fullscreen)
    _screen_share_sig = pyqtSignal(bool)                    # (active)
    _pointer_sig     = pyqtSignal(int, int, str, object, float)  # (x, y, label, bbox, duration)
    _orbit_sig       = pyqtSignal(bool)                      # (active) 3D orbit constellation view
    _holo_sig        = pyqtSignal(bool)                      # (active) 3D Holo Hands gestures view
    _conn_sig        = pyqtSignal(object)                    # connections snapshot (any thread)
    _route_done_sig  = pyqtSignal(object)                    # router result for a UI button
    _fx_shutdown_sig = pyqtSignal(str)                       # play the shutdown cinematic (any thread)
    _theme_sig       = pyqtSignal(str, str, str)             # (theme name, accent hex, source) any thread

    def __init__(self, face_path: str):
        super().__init__()
        self._face_path = face_path

        # ── Cinematic lifecycle (core/cinematic) ─────────────────────────────
        # Set before anything else so a close request at any moment finds it.
        self._fx_settings = _cine.AnimationSettings.load() if _cine else None
        self._lifecycle = _cine.Lifecycle(self) if _cine else None
        self._fx_boot = None              # live BootSequence overlay
        self._fx_boot_started = False
        self._fx_shutdown = None          # live ShutdownSequence overlay
        self._fx_preview = None           # a preview playing from the settings panel
        self._fx_shutdown_started = False
        self._fx_shutdown_done = False
        self._fx_shutdown_source = "ui"
        self._fx_shutdown_event = threading.Event()   # set when the cinematic has ended
        self._fx_session_ending = False   # Windows is logging off: no time for cinema
        self._fx_sfx = None
        self._anim_overlay = None
        # ── Theme + welcome state ────────────────────────────────────────────
        self._icon_reg: list = []         # (target, kind, base colour, size, mode)
        self._theme = None                # current core.theme.ThemeSpec
        self._theme_seq = None            # live ColorTransformSequence
        self._theme_seq_spec = None
        self._theme_pending = None        # (spec, source) requested while busy
        self._rgb_timer = None
        self._rgb_queue: list = []
        self._rgb_bucket = None
        self._intro_active = False        # the welcome narration is playing
        self.request_say = None           # callable(instruction) → live voice, set by CarLive
        self.on_intro_finished = None     # callable(reason), set by CarLive

        # Load customization from config
        _cfg = _read_full_config()
        self._assistant_name: str = (_cfg.get("assistant_name") or "ULTRON").strip()
        _display = self._assistant_name.upper()

        # Apply the saved UI colour BEFORE panels/stylesheets are built
        _ui_color = (_cfg.get("ui_color") or "").strip()
        if _ui_color and _ui_color.lower() != DEFAULT_UI_COLOR:
            apply_ui_accent(_ui_color)

        self.setWindowTitle(f"{_display} 2.0 — {APP_CREDIT}")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command   = None
        self.on_shutdown       = None
        self.is_backend_running = None  # callable: () -> bool, set by CarLive
        self._integration_hub  = None
        self._integration_sig.connect(self._present_integration)
        from core.integrations.common import bind_panel
        bind_panel(self._integration_sig.emit)
        self.on_remote_clicked = None   # callable: () -> (url, key) | None
        self.on_interrupt      = None   # callable: () -> None — stop ULTRON mid-speech
        self.on_voice_change   = None   # callable: () -> None — rebuild session with new voice
        self.on_audio_device_change = None  # callable: () -> None — reopen audio streams
        self._confirm_overlay  = None   # live ConfirmBanner, if one is on screen
        self.get_plugins       = None   # callable: () -> list[dict], set by CarLive
        self.get_plugin_settings = None # callable: () -> list[dict] settings schemas, set by CarLive
        self.on_wake_toggle    = None   # callable: (enable: bool) -> str, set by CarLive
        self.on_clap_toggle    = None   # callable: (enable: bool) -> bool, set by CarLive
        self.on_wake_manual    = None   # callable: () -> None — manual sleep/wake
        self.on_push_to_talk   = None   # callable: (enable: bool) -> str scope
        self.ptt_hold          = None   # callable: (held: bool) -> None — windowed chord
        self.wake_get_state    = None   # callable: () -> dict {enabled, awake, ready}
        self._muted            = False
        self._current_file: str | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._customize_overlay: CustomizeOverlay | None = None
        self._active_pointer_overlay = None
        self._screen_share_sig.connect(self._on_screen_share_state_change)
        self._pointer_sig.connect(self._on_show_pointer)

        # ── Command-center wiring (filled in by CarLive once the backend runs) ─
        self.on_user_command = None     # callable(text): typed commands → router
        self.router = None              # core.command_router.CommandRouter
        self.connections = None         # core.connections.ConnectionManager
        self.get_tool_catalog = None    # callable() -> list[dict] of tool declarations
        self._conn_snapshot: dict = {}
        self._hud_state = "INITIALISING"
        self._graph_dirty = True
        self._conn_sig.connect(self._apply_connections)
        self._route_done_sig.connect(self._show_route_result)

        central = QWidget()
        central.setObjectName("Central")
        central.setStyleSheet(f"QWidget#Central {{ background: {C.BG}; }}")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # The graph is created first: the header search, the explorer and the
        # inspector all talk to it.
        self.graph_panel = GraphPanel(C)
        self.graph = self.graph_panel.graph
        self.graph.node_selected.connect(self._on_graph_select)

        self._header_w = self._build_header()
        root.addWidget(self._header_w)

        body = QHBoxLayout()
        body.setContentsMargins(10, 10, 10, 6)
        body.setSpacing(10)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)

        # Center column: HUD + resizable content panel via QSplitter
        self.hud = HudCanvas(face_path, _display)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_panel = self._build_content_panel()
        self._quiz_panel = self._build_quiz_panel()

        # Live camera container — replaces HUD when camera stream is active
        _cam_cont = QWidget()
        _cam_cont.setStyleSheet("background: #000308;")
        _cam_v = QVBoxLayout(_cam_cont)
        _cam_v.setContentsMargins(0, 0, 0, 0)
        _cam_v.setSpacing(0)
        _cam_hdr = QHBoxLayout()
        _cam_hdr.setContentsMargins(8, 5, 8, 5)
        _cam_title = QLabel("◈  CAMERA FEED  [NONSTOP]")
        _cam_title.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        _cam_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _cam_hdr.addWidget(_cam_title)
        _cam_hdr.addStretch()

        self._cam_holo_btn = QPushButton("🖐️  HOLO HANDS: ISKLJUČENO")
        self._cam_holo_btn.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        self._cam_holo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cam_holo_btn.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.15); border-radius: 4px; padding: 2px 10px;
            }}
            QPushButton:hover {{ color: #7dffd9; border-color: #7dffd9; }}
        """)
        self._cam_holo_btn.clicked.connect(self.toggle_holo_hands)
        _cam_hdr.addWidget(self._cam_holo_btn)

        _cam_deck_btn = QPushButton("🌐  3D DECK")
        _cam_deck_btn.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        _cam_deck_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_deck_btn.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: 1px solid rgba(255, 255, 255, 0.15); border-radius: 4px; padding: 2px 8px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI}; }}
        """)
        _cam_deck_btn.clicked.connect(self._open_holo_browser)
        _cam_hdr.addWidget(_cam_deck_btn)

        _cam_x = QPushButton("✕  CLOSE")
        _cam_x.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        _cam_x.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_x.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        _cam_x.clicked.connect(self.stop_camera_stream)
        _cam_hdr.addWidget(_cam_x)
        _cam_v.addLayout(_cam_hdr)
        self._cam_live_lbl = QLabel()
        self._cam_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_live_lbl.setStyleSheet("background: transparent;")
        self._cam_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _cam_v.addWidget(self._cam_live_lbl, stretch=1)

        # ── Video surface ────────────────────────────────────────────────
        # Built exactly like the camera page above and stacked beside it,
        # because it is the same idea: something takes the centre of the HUD
        # for a while and then gives it back. Sharing the stack rather than
        # inventing a second mechanism means the avatar, the camera and a video
        # can never be on screen at once.
        self._video_split = False        # is the sound a separate stream?
        self._video_auto_muted = False   # did ULTRON close the mic, or the user?
        # A plain flag rather than reading the widget. video_is_playing() is
        # called from plugin threads, and reading a widget's state from one is
        # not something to rely on; an attribute is.
        self._video_on = False
        self._video_cont = QWidget()
        self._video_cont.setStyleSheet(f"background: {C.BG};")
        _vid_v = QVBoxLayout(self._video_cont)
        _vid_v.setContentsMargins(8, 6, 8, 8)
        _vid_v.setSpacing(4)

        _vid_hdr = QHBoxLayout()
        self._video_title = QLabel("▶  VIDEO")
        self._video_title.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        self._video_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _vid_hdr.addWidget(self._video_title)
        _vid_hdr.addStretch()

        def _vid_btn(text: str) -> QPushButton:
            b = QPushButton(text)
            b.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{
                    color: {C.TEXT_DIM}; background: transparent;
                    border: none; padding: 2px 6px;
                }}
                QPushButton:hover {{ color: {C.PRI}; }}
            """)
            return b

        # Muted is the default and the button says so, because a soundtrack
        # talking over ULTRON is the one way this feature could make the
        # assistant worse rather than better.
        self._video_mute_btn = _vid_btn("🔇  SOUND OFF")
        self._video_mute_btn.clicked.connect(self._toggle_video_mute)
        _vid_hdr.addWidget(self._video_mute_btn)

        _vid_x = _vid_btn("✕  CLOSE")
        _vid_x.clicked.connect(self.stop_video)
        _vid_hdr.addWidget(_vid_x)
        _vid_v.addLayout(_vid_hdr)

        if HAVE_VIDEO:
            # A GRAPHICS ITEM, NOT A QVideoWidget.
            #
            # QVideoWidget gets a native window of its own, and a native window
            # is painted by the compositor above every ordinary widget that
            # shares the screen with it — so the settings drawer opened
            # *underneath* the video and raise_() could not help, because the
            # two are not in the same painting order at all. A video item drawn
            # into a QGraphicsView goes through Qt's own painter like any other
            # widget, and anything laid over it stays over it.
            self._video_scene = QGraphicsScene(self)
            self._video_item = QGraphicsVideoItem()
            self._video_scene.addItem(self._video_item)
            self._video_widget = QGraphicsView(self._video_scene)
            self._video_widget.setStyleSheet("background: #000; border: none;")
            self._video_widget.setFrameShape(QGraphicsView.Shape.NoFrame)
            self._video_widget.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._video_widget.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._video_widget.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            _vid_v.addWidget(self._video_widget, stretch=1)

            self._video_audio = QAudioOutput()
            self._video_audio.setMuted(True)
            self._video_player = QMediaPlayer()
            self._video_player.setVideoOutput(self._video_item)
            # Keep the picture filling the view as the window is resized.
            self._video_item.nativeSizeChanged.connect(self._fit_video)
            self._video_player.setAudioOutput(self._video_audio)
            self._video_player.errorOccurred.connect(self._on_video_error)

            # A SECOND player, for sound that arrives separately.
            #
            # YouTube no longer serves a single stream carrying both picture and
            # sound — not for new uploads and not for old ones; checked against
            # three videos including the oldest on the site, and every one of
            # them offered zero combined formats. Picture and sound are two
            # URLs, so they are two players, started together and nudged back
            # into line by the timer below.
            #
            # A local file or a direct video URL still uses the first player
            # alone: one stream, one player, nothing to synchronise.
            self._video_sound = QMediaPlayer()
            self._video_sound_out = QAudioOutput()
            self._video_sound_out.setMuted(True)
            self._video_sound.setAudioOutput(self._video_sound_out)

            self._video_sync = QTimer(self)
            self._video_sync.setInterval(1000)
            self._video_sync.timeout.connect(self._sync_video_sound)
        else:
            self._video_scene = None
            self._video_item = None
            self._video_widget = None
            self._video_player = None
            self._video_audio = None
            self._video_sound = None
            self._video_sound_out = None
            self._video_sync = None
            _miss = QLabel("Video playback is not available in this Qt install.")
            _miss.setAlignment(Qt.AlignmentFlag.AlignCenter)
            _miss.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            _vid_v.addWidget(_miss, stretch=1)

        # Home page: the neural graph by default, the avatar HUD one toggle away.
        # It sits at index 0 of the main stack so every existing "return to the
        # HUD" (camera / video / orbit closing) lands on whichever the user chose.
        self._home_stack = QStackedWidget()
        self._home_stack.addWidget(self.graph_panel)   # 0 — neural graph
        self._home_stack.addWidget(self.hud)           # 1 — avatar HUD

        # Stack: 0 = home (graph | avatar), 1 = live camera, 2 = video, 3 = orbit view
        self._orbit_cont = self._build_orbit_widget()
        self._hud_cam_stack = QStackedWidget()
        self._hud_cam_stack.addWidget(self._home_stack)
        self._hud_cam_stack.addWidget(_cam_cont)
        self._hud_cam_stack.addWidget(self._video_cont)
        self._hud_cam_stack.addWidget(self._orbit_cont)

        self._center_split = QSplitter(Qt.Orientation.Vertical)
        self._center_split.setStyleSheet(f"""
            QSplitter::handle {{
                background: {C.BORDER};
                height: 4px;
            }}
            QSplitter::handle:hover {{
                background: {C.PRI_DIM};
            }}
        """)
        self._chat_dock = self._build_chat_dock()
        self._center_split.addWidget(self._hud_cam_stack)
        self._center_split.addWidget(self._content_panel)
        self._center_split.addWidget(self._quiz_panel)
        self._center_split.addWidget(self._chat_dock)
        self._center_split.setStretchFactor(0, 3)
        self._center_split.setStretchFactor(1, 1)
        self._center_split.setStretchFactor(3, 0)
        self._center_split.setCollapsible(0, False)
        self._center_split.setCollapsible(3, False)
        self._center_split.setHandleWidth(8)
        self._center_split.setSizes([560, 0, 0, 230])
        body.addWidget(self._center_split, stretch=5)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=0)

        root.addLayout(body, stretch=1)
        self._footer_w = self._build_footer()
        root.addWidget(self._footer_w)

        # Quick-access drawer (floating overlay, built after central widget layout is done)
        self._quick_drawer = self._build_quick_drawer()
        self._ctrl_drawer = self._build_controls_drawer()
        self._warm_wake_state()
        self._update_autostart_btn(self._check_autostart())
        from memory.config_manager import get_brief_enabled as _gbe
        self._update_brief_btn(_gbe())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metric update timer
        self._t_started = time.monotonic()
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        # The graph is rebuilt from real state: soon after any connection
        # change (throttled), and every 20 s for memory / monitors / plugins.
        self._graph_tmr = QTimer(self)
        self._graph_tmr.timeout.connect(self._refresh_graph_if_dirty)
        self._graph_tmr.start(1500)
        self._graph_full_tmr = QTimer(self)
        self._graph_full_tmr.timeout.connect(lambda: setattr(self, "_graph_dirty", True))
        self._graph_full_tmr.start(20000)
        QTimer.singleShot(150, self._initial_connections)

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._content_sig.connect(self._show_content)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._show_camera_frame)
        self._confirm_sig.connect(self._show_confirm_banner)
        self._confirm_hide_sig.connect(self._hide_confirm_banner)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._cam_frame_sig.connect(self._on_cam_frame)
        self._wake_btns_sig.connect(self._refresh_wake_btns)
        self._video_open_sig.connect(self._on_video_open)
        self._video_close_sig.connect(self._on_video_close)
        self._video_mute_sig.connect(self._on_video_mute)
        self._clipboard_sig.connect(self._show_clipboard_panel)
        self._wake_dl_sig.connect(self._on_wake_install_done)
        self._quiz_sig.connect(self._show_quiz)
        self._quiz_hide_sig.connect(self._hide_quiz)
        self._review_sig.connect(self._show_review)
        self._screen_move_sig.connect(self._on_screen_move)
        self._orbit_sig.connect(self._toggle_orbit_view)
        self._holo_sig.connect(self._toggle_holo_view)
        self._cam_stop = threading.Event()

        # Camera preview overlay (child of central widget, positioned in resizeEvent)
        self._cam_preview = _CameraPreview(self.centralWidget())

        # Clipboard panel (child of central widget, bottom-center)
        self._clipboard_panel = ClipboardPanel(self.centralWidget())
        self._clipboard_panel.action_requested.connect(self._on_clipboard_action)
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_intr = QShortcut(QKeySequence("Escape"), self)
        sc_intr.activated.connect(self._do_interrupt)

        self._fx_setup()

        # Auto-boot to TV screen if configured
        try:
            from memory.config_manager import get_plugin_setting
            if not os.getenv("ULTRON_SKIP_DEVICE_AUTOSTART") and get_plugin_setting("smartthings", "tv_auto_boot", True):
                QTimer.singleShot(1000, self._auto_boot_to_tv)
        except Exception:
            pass

    def _show_camera_frame(self, img_bytes: bytes):
        """Slot — display camera preview overlay (main thread)."""
        self._cam_preview.show_frame(img_bytes)
        cw = self.centralWidget()
        pw = _CameraPreview._W
        ph = self._cam_preview.height()
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )

    # --- Live camera stream in HUD area ------------------------------------
    def _on_cam_stream(self, start: bool) -> None:
        if start:
            self._hud_cam_stack.setCurrentIndex(1)
        else:
            self._hud_cam_stack.setCurrentIndex(0)
            self._cam_live_lbl.clear()

    def _on_cam_frame(self, data: bytes) -> None:
        px = QPixmap()
        px.loadFromData(data)
        if not px.isNull():
            w, h = self._cam_live_lbl.width(), self._cam_live_lbl.height()
            if w > 1 and h > 1:
                self._cam_live_lbl.setPixmap(
                    px.scaled(w, h,
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
                )

    def start_camera_stream(self) -> None:
        self._cam_stop.clear()
        self._cam_stream_sig.emit(True)
        if hasattr(self, '_cam_thread') and self._cam_thread and self._cam_thread.is_alive():
            return
        self._cam_thread = threading.Thread(target=self._cam_loop, daemon=True, name="cam-stream")
        self._cam_thread.start()

    def _cam_loop(self) -> None:
        try:
            import cv2
            # Reuse camera index detected by screen_processor (cached in api_keys.json)
            cam_idx = 0
            try:
                import json as _j
                cfg = _j.loads((CONFIG_DIR / "api_keys.json").read_text())
                cam_idx = int(cfg.get("camera_index", 0))
            except Exception:
                pass
            try:
                backend = cv2.CAP_DSHOW if _OS == "Windows" else cv2.CAP_ANY
            except AttributeError:
                backend = 0
            cap = cv2.VideoCapture(cam_idx, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return
            # warm-up frames
            for _ in range(5):
                cap.read()
            while not self._cam_stop.wait(0.033) and cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    if getattr(self, "_holo_hands_active", False):
                        frame = self._render_holo_hands(frame)
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    self._cam_frame_sig.emit(buf.tobytes())
            cap.release()
        except Exception as e:
            print(f"[Camera] Stream error: {e}")
        finally:
            self._cam_stream_sig.emit(False)

    def stop_camera_stream(self) -> None:
        self._cam_stop.set()

    # --- Video in the HUD area ---------------------------------------------
    #
    # Everything below runs on the Qt thread. Plugins and the assistant reach it
    # through the signals above, the same way every other panel is driven, so a
    # background thread never touches a widget.
    def _on_video_open(self, source: str, title: str, muted: bool,
                       audio_source: str = "") -> None:
        if not HAVE_VIDEO or not self._video_player:
            self.write_log("SYS: Video playback is not available in this Qt "
                           "install.")
            return
        # A video and the live camera cannot share the centre of the HUD.
        self._cam_stop.set()

        self._video_title.setText(f"▶  {(title or 'VIDEO')[:44].upper()}")
        self._video_split = bool(audio_source)
        self._set_video_muted(bool(muted))

        url = (QUrl.fromLocalFile(source) if Path(source).exists()
               else QUrl(source))
        self._video_player.setSource(url)
        if self._video_split:
            self._video_sound.setSource(QUrl(audio_source))
        self._hud_cam_stack.setCurrentIndex(2)
        self._video_on = True
        # Re-run now that the video counts as playing: _set_video_muted ran
        # before this line and saw no video, so its mic check was a no-op.
        self._sync_mic_for_video()
        self._video_player.play()
        if self._video_split:
            self._video_sound.play()
            self._video_sync.start()

    def _fit_video(self, *_a) -> None:
        """Size the picture to the panel, keeping its shape."""
        if not (self._video_item and self._video_widget):
            return
        try:
            native = self._video_item.nativeSize()
            if native.isEmpty():
                return
            view = self._video_widget.viewport().size()
            scale = min(view.width() / native.width(),
                        view.height() / native.height())
            w, h = native.width() * scale, native.height() * scale
            self._video_item.setSize(QSizeF(w, h))
            self._video_scene.setSceneRect(0, 0, w, h)
            self._video_widget.centerOn(self._video_item)
        except Exception:
            pass

    def _on_video_close(self) -> None:
        if self._video_sync:
            self._video_sync.stop()
        for p in (self._video_player, self._video_sound):
            if p:
                p.stop()
                p.setSource(QUrl())
        self._video_split = False
        self._video_on = False
        self._hud_cam_stack.setCurrentIndex(0)
        self._sync_mic_for_video()      # gives the microphone back

    def _build_orbit_widget(self) -> QWidget:
        """Construct the 3D animated Orbit constellation WebGL canvas."""
        container = QWidget()
        container.setStyleSheet(f"background: {C.BG};")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        if HAVE_WEBENGINE and QWebEngineView is not None:
            self._orbit_web = QWebEngineView()
            self._orbit_web.setStyleSheet("background: #020306;")
            self._orbit_web.page().setBackgroundColor(QColor("#020306"))

            if QWebEngineSettings is not None:
                settings = self._orbit_web.settings()
                settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
                settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
                settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
                settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
                settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
                settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)

            orbit_html_path = BASE_DIR / "assets" / "orbit" / "orbit.html"
            if orbit_html_path.exists():
                self._orbit_web.setUrl(QUrl.fromLocalFile(str(orbit_html_path.resolve())))
            else:
                self._orbit_web.setHtml("<h1>Orbit asset missing</h1>")

            def on_title_changed(title: str):
                if title in ("JARVIS_ORBIT_CLOSE", "ULTRON_ORBIT_CLOSE"):
                    self._toggle_orbit_view(False)
                elif title == "JARVIS_ORBIT_TOGGLE_EXPAND":
                    self._toggle_orbit_expanded()

            def on_load_finished(ok: bool):
                if ok and self._orbit_web is not None:
                    try:
                        import json
                        conn_data = json.dumps(self._get_orbit_connection_status())
                        self._orbit_web.page().runJavaScript(f"if (window.setConnections) window.setConnections({conn_data});")
                        self._orbit_web.page().runJavaScript(f"if (window.setAssistantName) window.setAssistantName('{self._assistant_name}');")
                        self._orbit_web.page().runJavaScript("if (window.refreshDimensions) window.refreshDimensions();")
                    except Exception:
                        pass

            self._orbit_web.titleChanged.connect(on_title_changed)
            self._orbit_web.loadFinished.connect(on_load_finished)
            lay.addWidget(self._orbit_web)
        else:
            self._orbit_web = None
            lbl = QLabel("3D Orbit view requires QtWebEngine.")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
            lay.addWidget(lbl)

        return container

    def _toggle_orbit_expanded(self) -> None:
        """Toggle full-width immersive mode for Orbit by hiding/showing side panels."""
        if not hasattr(self, "_orbit_expanded"):
            self._orbit_expanded = False
        self._orbit_expanded = not self._orbit_expanded
        if hasattr(self, "_left_panel") and self._left_panel:
            self._left_panel.setVisible(not self._orbit_expanded)
        if hasattr(self, "_right_panel") and self._right_panel:
            self._right_panel.setVisible(not self._orbit_expanded)
        if hasattr(self, "_orbit_web") and self._orbit_web is not None:
            QTimer.singleShot(60, lambda: self._orbit_web.page().runJavaScript("if (window.refreshDimensions) window.refreshDimensions();"))

    def _get_orbit_connection_status(self) -> dict:
        import json
        gmail_file = BASE_DIR / "config" / "integrations" / "google_gmail.bin"
        gdrive_file = BASE_DIR / "config" / "integrations" / "google_drive.bin"
        telegram_file = BASE_DIR / "telegram_temp" / "chat_id.txt"
        api_keys_file = BASE_DIR / "config" / "api_keys.json"

        statuses = {
            "conn_mail": gmail_file.exists(),
            "conn_gdrive": gdrive_file.exists(),
            "conn_youtube": True,
            "conn_smartthings": False,
            "conn_telegram": telegram_file.exists(),
            "conn_gmaps": True,
            "conn_torrent": True
        }
        try:
            if api_keys_file.exists():
                with open(api_keys_file, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    tok = cfg.get("plugin_config", {}).get("smartthings", {}).get("token", "")
                    if tok and len(tok) > 5:
                        statuses["conn_smartthings"] = True
        except Exception:
            pass
        return statuses

    def _toggle_orbit_view(self, show: bool) -> None:
        """Switch between standard HUD and the 3D Orbit Constellation."""
        if show:
            self._hud_cam_stack.setCurrentIndex(3)
            if hasattr(self, "_orbit_web") and self._orbit_web is not None:
                st = getattr(self.hud, "state", "IDLE")
                self._orbit_web.page().runJavaScript(f"if (window.setOrbitState) window.setOrbitState('{st}');")
                self._orbit_web.page().runJavaScript(f"if (window.setAssistantName) window.setAssistantName('{self._assistant_name}');")
                try:
                    import json
                    conn_data = json.dumps(self._get_orbit_connection_status())
                    self._orbit_web.page().runJavaScript(f"if (window.setConnections) window.setConnections({conn_data});")
                except Exception:
                    pass
                # Trigger dimension refresh after widget becomes visible
                QTimer.singleShot(50, lambda: self._orbit_web.page().runJavaScript("if (window.refreshDimensions) window.refreshDimensions();"))
                QTimer.singleShot(250, lambda: self._orbit_web.page().runJavaScript("if (window.refreshDimensions) window.refreshDimensions();"))
            self._log.append_log("SYS: Prikaz orbite je aktiviran.")
        else:
            if getattr(self, "_orbit_expanded", False):
                self._orbit_expanded = False
                if hasattr(self, "_left_panel") and self._left_panel:
                    self._left_panel.show()
                if hasattr(self, "_right_panel") and self._right_panel:
                    self._right_panel.show()
            if self._hud_cam_stack.currentIndex() == 3:
                self._hud_cam_stack.setCurrentIndex(0)
                self._log.append_log("SYS: Prikaz je vraćen na standardni HUD.")

    def _toggle_holo_view(self, show: bool) -> None:
        """Switch between standard camera view and Holo Hands mode."""
        if show:
            if self._hud_cam_stack.currentIndex() != 1:
                self.start_camera_stream()
            self.toggle_holo_hands(True)
        else:
            self.toggle_holo_hands(False)
            if self._hud_cam_stack.currentIndex() == 1:
                self.stop_camera_stream()

    def _render_holo_hands(self, frame):
        """Render high-tech holographic hand skeleton, joints, repulsors and gestures on the camera feed."""
        try:
            import cv2
            import math
            import numpy as np

            if not hasattr(self, "_holo_detector") or self._holo_detector is None:
                try:
                    import mediapipe as mp
                    from mediapipe.tasks import python
                    from mediapipe.tasks.python import vision
                    model_path = BASE_DIR / "assets" / "holo" / "vendor" / "hand_landmarker.task"
                    if model_path.exists():
                        base_options = python.BaseOptions(model_asset_path=str(model_path))
                        options = vision.HandLandmarkerOptions(base_options=base_options, num_hands=2)
                        self._holo_detector = vision.HandLandmarker.create_from_options(options)
                    else:
                        self._holo_detector = False
                except Exception as e:
                    self._holo_detector = False

            h, w, _ = frame.shape
            # Holographic top banner
            cv2.rectangle(frame, (16, 14), (290, 42), (10, 18, 24), -1)
            cv2.rectangle(frame, (16, 14), (290, 42), (125, 255, 217), 1)
            cv2.putText(frame, "◈ ULTRON HOLO-HANDS: ACTIVE", (24, 33),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (125, 255, 217), 1, cv2.LINE_AA)

            if getattr(self, "_holo_detector", False):
                import mediapipe as mp
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                res = self._holo_detector.detect(mp_img)

                CONNECTIONS = [
                    (0, 1), (1, 2), (2, 3), (3, 4),
                    (0, 5), (5, 6), (6, 7), (7, 8),
                    (5, 9), (9, 10), (10, 11), (11, 12),
                    (9, 13), (13, 14), (14, 15), (15, 16),
                    (13, 17), (17, 18), (18, 19), (19, 20),
                    (0, 17)
                ]
                TIPS = [4, 8, 12, 16, 20]

                if res and res.hand_landmarks:
                    for lm in res.hand_landmarks:
                        pts = [(int(p.x * w), int(p.y * h)) for p in lm]
                        # Futuristic cyan glowing bones
                        for a, b in CONNECTIONS:
                            cv2.line(frame, pts[a], pts[b], (217, 255, 125), 2, cv2.LINE_AA)
                            cv2.line(frame, pts[a], pts[b], (255, 255, 220), 1, cv2.LINE_AA)
                        # Joints & Fingertips
                        for i, pt in enumerate(pts):
                            if i in TIPS:
                                cv2.circle(frame, pt, 8, (125, 255, 217), 1, cv2.LINE_AA)
                                cv2.circle(frame, pt, 3, (255, 255, 255), -1, cv2.LINE_AA)
                            else:
                                cv2.circle(frame, pt, 4, (217, 255, 125), -1, cv2.LINE_AA)
                        # Palm Center - Iron Man style repulsor reticle
                        p_cx = int((pts[0][0] + pts[5][0] + pts[9][0] + pts[17][0]) / 4)
                        p_cy = int((pts[0][1] + pts[5][1] + pts[9][1] + pts[17][1]) / 4)
                        cv2.circle(frame, (p_cx, p_cy), 18, (125, 255, 217), 1, cv2.LINE_AA)
                        cv2.circle(frame, (p_cx, p_cy), 25, (100, 200, 255), 1, cv2.LINE_AA)
                        cv2.line(frame, (p_cx - 30, p_cy), (p_cx - 20, p_cy), (125, 255, 217), 1, cv2.LINE_AA)
                        cv2.line(frame, (p_cx + 20, p_cy), (p_cx + 30, p_cy), (125, 255, 217), 1, cv2.LINE_AA)
                        cv2.line(frame, (p_cx, p_cy - 30), (p_cx, p_cy - 20), (125, 255, 217), 1, cv2.LINE_AA)
                        cv2.line(frame, (p_cx, p_cy + 20), (p_cx, p_cy + 30), (125, 255, 217), 1, cv2.LINE_AA)

                        # Pinch gesture tracking
                        d_pinch = math.hypot(pts[4][0] - pts[8][0], pts[4][1] - pts[8][1])
                        if d_pinch < 40:
                            cv2.line(frame, pts[4], pts[8], (100, 220, 255), 3, cv2.LINE_AA)
                            cv2.circle(frame, (int((pts[4][0]+pts[8][0])/2), int((pts[4][1]+pts[8][1])/2)), 6, (100, 220, 255), -1, cv2.LINE_AA)
                            cv2.putText(frame, "PINCH", (pts[8][0] + 16, pts[8][1]),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 220, 255), 1, cv2.LINE_AA)
        except Exception:
            pass
        return frame

    def toggle_holo_hands(self, enable: bool | None = None) -> None:
        """Toggle Holo Hands tracking inside the camera view."""
        if enable is None:
            self._holo_hands_active = not getattr(self, "_holo_hands_active", False)
        else:
            self._holo_hands_active = bool(enable)

        if hasattr(self, "_cam_holo_btn") and self._cam_holo_btn is not None:
            if self._holo_hands_active:
                self._cam_holo_btn.setText("🖐️  HOLO HANDS: UKLJUČENO")
                self._cam_holo_btn.setStyleSheet("""
                    QPushButton {
                        color: #7dffd9; background: rgba(125, 255, 217, 0.22);
                        border: 1px solid #7dffd9; border-radius: 4px; padding: 2px 10px;
                        font-weight: bold;
                    }
                    QPushButton:hover { background: rgba(125, 255, 217, 0.35); }
                """)
                self.write_log("SYS: Holo Hands praćenje pokreta ruku je UKLJUČENO u kameri.")
            else:
                self._cam_holo_btn.setText("🖐️  HOLO HANDS: ISKLJUČENO")
                self._cam_holo_btn.setStyleSheet(f"""
                    QPushButton {{
                        color: {C.TEXT_DIM}; background: rgba(255, 255, 255, 0.06);
                        border: 1px solid rgba(255, 255, 255, 0.15); border-radius: 4px; padding: 2px 10px;
                    }}
                    QPushButton:hover {{ color: #7dffd9; border-color: #7dffd9; }}
                """)
                self.write_log("SYS: Holo Hands praćenje je ISKLJUČENO.")

    def _open_holo_browser(self) -> None:
        try:
            from core.holo_service import get_holo_service
            get_holo_service().open_browser()
            self.write_log("SYS: 3D Holo Gestures otvoren u spoljnom pretraživaču.")
        except Exception as e:
            print(f"[HOLO] Browser launch error: {e}")

    def _toggle_camera_stream_btn(self) -> None:
        if self._hud_cam_stack.currentIndex() == 1:
            self.stop_camera_stream()
        else:
            self.start_camera_stream()

    def _set_video_muted(self, muted: bool) -> None:
        """Silence whichever output is carrying the sound for this video."""
        muted = bool(muted)
        if self._video_audio:
            # When the sound is a separate stream this player has none, but
            # muting it too costs nothing and keeps the two paths identical.
            self._video_audio.setMuted(muted)
        if self._video_sound_out:
            self._video_sound_out.setMuted(muted)
        self._sync_video_mute_btn()
        self._sync_mic_for_video()

    def _on_video_mute(self, muted: bool) -> None:
        self._set_video_muted(muted)

    def _sync_mic_for_video(self) -> None:
        """Close the microphone while the video is making sound.

        ULTRON subtracts its OWN output from the microphone — that is what
        core/echo.py does — but a video plays through a different output
        entirely, so the guard has never heard of it and the assistant answers
        the film. There is no arrangement in which an open microphone and a
        loudspeaker in the same room do not do that.

        So the microphone closes for exactly as long as the sound is on, and
        opens again by itself the moment it goes off or the video is closed.
        Only if ULTRON closed it: a microphone the user muted themselves stays
        muted, and pressing the mute key during a video hands the decision back
        to them for good.
        """
        sound_on = bool(self._video_on and self._video_sound_out
                        and not self._video_sound_out.isMuted())
        if sound_on and not self._muted:
            self._video_auto_muted = True
            self._set_muted(True, "The video's sound is on — silence it, close "
                                  "it, or press F4 to talk. Typing still works.")
        elif not sound_on and self._video_auto_muted:
            self._video_auto_muted = False
            self._set_muted(False, "The video is quiet again.")

    def _sync_video_sound(self) -> None:
        """Keep the separate soundtrack in step with the picture.

        Two players started at the same moment do not stay together: they buffer
        independently, and measured on a real stream they were about half a
        second apart after nine seconds. So the sound is nudged back to the
        picture whenever it drifts far enough to hear — and only then, because
        correcting a smaller gap is itself audible.
        """
        if not (self._video_split and self._video_sound and self._video_player):
            return
        try:
            if self._video_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                return
            drift = self._video_sound.position() - self._video_player.position()
            if abs(drift) > 300:
                self._video_sound.setPosition(self._video_player.position())
        except Exception:
            pass

    def _sync_video_mute_btn(self) -> None:
        out = self._video_sound_out if self._video_split else self._video_audio
        muted = bool(out and out.isMuted())
        self._video_mute_btn.setText("🔇  SOUND OFF" if muted else "🔊  SOUND ON")

    def _toggle_video_mute(self) -> None:
        out = self._video_sound_out if self._video_split else self._video_audio
        if out:
            self._set_video_muted(not out.isMuted())

    def _on_video_error(self, *_a) -> None:
        err = ""
        try:
            err = self._video_player.errorString()
        except Exception:
            pass
        self.write_log(f"SYS: The video could not be played{(' — ' + err) if err else ''}.")
        self._on_video_close()

    def stop_video(self) -> None:
        self._video_close_sig.emit()

    def video_is_playing(self) -> bool:
        return bool(self._video_on)

    # ------------------------------------------------------------------
    # Icon generation — arc-reactor style, rendered with Pillow
    # ------------------------------------------------------------------
    @staticmethod
    def _build_car_icon(out_path: Path) -> bool:
        """
        Render a ULTRON arc-reactor icon at 4× resolution and downsample
        for crisp results at all sizes. Saves a multi-res .ico to out_path.
        Returns True on success.
        """
        try:
            import math
            import PIL.Image
            import PIL.ImageDraw
            import PIL.ImageFilter
        except ImportError:
            return False

        CYAN   = (0, 212, 255)
        DIM    = (0, 100, 140)
        DARK   = (0, 6, 10)
        GLOW   = (0, 160, 200)
        WHITE  = (220, 240, 255)

        def _render(sz: int) -> PIL.Image.Image:
            S  = sz * 4                     # draw at 4× then downscale
            img = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d   = PIL.ImageDraw.Draw(img)
            cx = cy = S // 2

            # ── filled background circle ──────────────────────────────────
            R = S // 2 - 2
            d.ellipse([cx-R, cy-R, cx+R, cy+R], fill=(*DARK, 255))

            # ── outer border ring ─────────────────────────────────────────
            lw = max(2, S // 40)
            d.ellipse([cx-R, cy-R, cx+R, cy+R],
                      outline=(*CYAN, 220), width=lw)

            # ── mid decorative ring ───────────────────────────────────────
            R2 = int(R * 0.72)
            d.ellipse([cx-R2, cy-R2, cx+R2, cy+R2],
                      outline=(*DIM, 180), width=max(1, lw // 2))

            # ── 6 radial spokes (hex bolt) ────────────────────────────────
            R_inner = int(R * 0.30)
            R_outer = int(R * 0.62)
            spoke_w = max(1, S // 80)
            for i in range(6):
                angle = math.radians(i * 60 - 30)
                x1 = cx + int(R_inner * math.cos(angle))
                y1 = cy + int(R_inner * math.sin(angle))
                x2 = cx + int(R_outer * math.cos(angle))
                y2 = cy + int(R_outer * math.sin(angle))
                d.line([x1, y1, x2, y2], fill=(*GLOW, 200), width=spoke_w)

            # ── 6 tick marks on outer ring ────────────────────────────────
            for i in range(6):
                angle = math.radians(i * 60)
                for dr in range(lw * 2):
                    rx = (R - lw - dr)
                    d.point(
                        [cx + int(rx * math.cos(angle)),
                         cy + int(rx * math.sin(angle))],
                        fill=(*WHITE, 220),
                    )

            # ── inner glowing ring ────────────────────────────────────────
            Ri = int(R * 0.26)
            d.ellipse([cx-Ri, cy-Ri, cx+Ri, cy+Ri],
                      outline=(*CYAN, 255), width=max(2, lw))

            # ── bright glow soft blur applied before core ─────────────────
            # (draw a slightly larger cyan circle on a separate layer)
            glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            gd = PIL.ImageDraw.Draw(glow_layer)
            Rc = int(R * 0.13)
            gd.ellipse([cx-Rc*2, cy-Rc*2, cx+Rc*2, cy+Rc*2],
                       fill=(*CYAN, 110))
            glow_layer = glow_layer.filter(PIL.ImageFilter.GaussianBlur(S // 14))
            img = PIL.Image.alpha_composite(img, glow_layer)
            d   = PIL.ImageDraw.Draw(img)

            # ── core dot ──────────────────────────────────────────────────
            d.ellipse([cx-Rc, cy-Rc, cx+Rc, cy+Rc], fill=(*WHITE, 255))

            # ── downscale to target size ──────────────────────────────────
            return img.resize((sz, sz), PIL.Image.LANCZOS)

        try:
            sizes  = [256, 128, 64, 48, 32, 16]
            frames = [_render(s) for s in sizes]
            frames[0].save(
                out_path,
                format="ICO",
                append_images=frames[1:],
                sizes=[(s, s) for s in sizes],
            )
            return True
        except Exception as e:
            print(f"[Shortcut] ⚠️  Icon generation failed: {e}")
            return False

    @staticmethod
    def _create_lnk_windows(lnk: str, target: str, args: str,
                             work_dir: str, icon_loc: str) -> None:
        """
        Create a Windows .lnk shortcut WITHOUT launching PowerShell or cmd.
        Tries win32com (pywin32) first; falls back to wscript.exe + VBScript.
        wscript.exe is a GUI-mode host — it never opens a console window.
        """
        # ── Option 1: pywin32 (pure Python COM, zero subprocess) ──────────
        try:
            from win32com.client import Dispatch   # type: ignore
            sh = Dispatch("WScript.Shell")
            sc = sh.CreateShortCut(lnk)
            sc.TargetPath       = target
            sc.Arguments        = f'"{args}"'
            sc.WorkingDirectory = work_dir
            sc.Description      = "ULTRON AI Assistant"
            sc.IconLocation     = icon_loc
            sc.save()
            return
        except ImportError:
            pass

        # ── Option 2: wscript.exe + VBScript (always available on Windows,
        #    GUI-mode executable — never opens a console window) ────────────
        vbs = "\n".join([
            'Set ws = CreateObject("WScript.Shell")',
            f'Set sc = ws.CreateShortcut("{lnk}")',
            f'sc.TargetPath = "{target}"',
            f'sc.Arguments = Chr(34) & "{args}" & Chr(34)',
            f'sc.WorkingDirectory = "{work_dir}"',
            'sc.Description = "ULTRON AI Assistant"',
            f'sc.IconLocation = "{icon_loc}"',
            'sc.Save',
        ])
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".vbs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vbs)
            proc = subprocess.Popen(
                ["wscript.exe", "/nologo", tmp],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=10)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    @staticmethod
    def _get_desktop_dir() -> Path:
        """
        Resolve the user's REAL desktop directory instead of assuming
        ~/Desktop, which breaks when:
          • OneDrive "Known Folder Move" relocates the desktop
            (C:/Users/x/OneDrive/Desktop) — very common on Win 10/11;
          • the XDG desktop is localized on Linux (~/Masaüstü,
            ~/Schreibtisch, ~/Bureau, …).
        Falls back to ~/Desktop only as a last resort.
        """
        home = Path.home()
        _os = platform.system()

        if _os == "Windows":
            # ── 1) SHGetKnownFolderPath(FOLDERID_Desktop) — the canonical
            #       answer; follows OneDrive redirection. No dependencies. ──
            try:
                import ctypes
                from ctypes import wintypes

                class _GUID(ctypes.Structure):
                    _fields_ = [("Data1", wintypes.DWORD),
                                ("Data2", wintypes.WORD),
                                ("Data3", wintypes.WORD),
                                ("Data4", ctypes.c_ubyte * 8)]

                # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
                fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                            (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                                 0x9A, 0x87, 0xC6, 0x41))
                buf = ctypes.c_wchar_p()
                if ctypes.windll.shell32.SHGetKnownFolderPath(
                        ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                    p = Path(buf.value)
                    ctypes.windll.ole32.CoTaskMemFree(buf)
                    if p.is_dir():
                        return p
            except Exception:
                pass

            # ── 2) Registry: User Shell Folders (may contain %VARS%) ──────
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Explorer\User Shell Folders") as key:
                    val, _t = winreg.QueryValueEx(key, "Desktop")
                p = Path(os.path.expandvars(val))
                if p.is_dir():
                    return p
            except Exception:
                pass

        elif _os == "Linux":
            # ── xdg-user-dir honours localized names (~/Masaüstü, …) ──────
            try:
                out = subprocess.run(["xdg-user-dir", "DESKTOP"],
                                     capture_output=True, text=True, timeout=5)
                p = Path(out.stdout.strip())
                if out.stdout.strip() and p != home and p.is_dir():
                    return p
            except Exception:
                pass
            try:
                cfg = home / ".config" / "user-dirs.dirs"
                for line in cfg.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("XDG_DESKTOP_DIR"):
                        val = line.split("=", 1)[1].strip().strip('"')
                        p = Path(val.replace("$HOME", str(home)))
                        if p != home and p.is_dir():
                            return p
            except Exception:
                pass

        # macOS: ~/Desktop is always the real path (localization is
        # display-only). Everything else lands here as a last resort.
        return home / "Desktop"

    def _create_desktop_shortcut(self):
        """
        Create a desktop shortcut on Windows / macOS / Linux.
        Never opens a terminal, console, or PowerShell window on any platform.
        """
        import stat as _stat
        script  = Path(__file__).resolve().parent / "main.py"
        python  = Path(sys.executable)
        desktop = self._get_desktop_dir()

        # Shared application artwork, also exported as PNG for Linux/macOS.
        ico_path = Path(__file__).resolve().parent / "config" / "ultron.ico"
        if not ico_path.exists():
            self._build_car_icon(ico_path)

        try:
            _os = platform.system()

            # ── Windows ───────────────────────────────────────────────────────
            if _os == "Windows":
                pythonw  = python.parent / "pythonw.exe"
                target   = str(pythonw if pythonw.exists() else python)
                lnk      = str(desktop / "ULTRON.lnk")
                icon_loc = str(ico_path) if ico_path.exists() else f"{target},0"
                self._create_lnk_windows(lnk, target, str(script),
                                         str(script.parent), icon_loc)

            # ── macOS — proper .app bundle (no Terminal window) ───────────────
            elif _os == "Darwin":
                app     = desktop / "ULTRON.app"
                mac_dir = app / "Contents" / "MacOS"
                res_dir = app / "Contents" / "Resources"
                mac_dir.mkdir(parents=True, exist_ok=True)
                res_dir.mkdir(exist_ok=True)

                # Launcher executable (bash — runs as background process,
                # macOS does NOT open Terminal for executables inside .app bundles)
                launcher = mac_dir / "ULTRON"
                launcher.write_text(
                    "#!/usr/bin/env bash\n"
                    f'cd "{script.parent}"\n'
                    f'exec "{python}" "{script}"\n'
                )
                launcher.chmod(launcher.stat().st_mode
                               | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)

                # Minimal Info.plist (required for .app recognition)
                (app / "Contents" / "Info.plist").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    '  <key>CFBundleExecutable</key><string>ULTRON</string>\n'
                    '  <key>CFBundleIdentifier</key>'
                    '<string>com.car.assistant</string>\n'
                    '  <key>CFBundleName</key><string>ULTRON</string>\n'
                    '  <key>CFBundlePackageType</key><string>APPL</string>\n'
                    '  <key>CFBundleVersion</key><string>1.0</string>\n'
                    '</dict></plist>\n'
                )

                # Optional: copy icon as .icns (skip silently if Pillow is missing)
                try:
                    import PIL.Image
                    icns = res_dir / "AppIcon.icns"
                    PIL.Image.open(ico_path).save(icns, format="ICNS")
                    # Inject icon reference into plist
                    plist = app / "Contents" / "Info.plist"
                    txt = plist.read_text()
                    plist.write_text(
                        txt.replace(
                            '</dict></plist>',
                            '  <key>CFBundleIconFile</key>'
                            '<string>AppIcon</string>\n</dict></plist>\n',
                        )
                    )
                except Exception:
                    pass  # icon is optional

            # ── Linux — .desktop file (Terminal=false, no console) ────────────
            else:
                # Export .ico → .png for better desktop integration
                png_path = ico_path.with_suffix(".png")
                if not png_path.exists() and ico_path.exists():
                    try:
                        import PIL.Image
                        PIL.Image.open(ico_path).resize(
                            (256, 256), PIL.Image.LANCZOS
                        ).save(png_path, format="PNG")
                    except Exception:
                        png_path = ico_path  # fallback to .ico

                icon_line = f"Icon={png_path}\n" if png_path.exists() else ""
                desk = desktop / "ULTRON.desktop"
                desk.write_text(
                    "[Desktop Entry]\n"
                    "Name=ULTRON\n"
                    f"Exec={python} {script}\n"
                    f"Path={script.parent}\n"
                    "Type=Application\n"
                    "Terminal=false\n"
                    "Categories=Utility;\n"
                    + icon_line
                )
                desk.chmod(desk.stat().st_mode | 0o755)

            self._log.append_log("SYS: Desktop shortcut created.")
        except Exception as e:
            self._log.append_log(f"ERR: Shortcut failed — {e}")

    def show_at_launch(self) -> None:
        """Full screen when Settings › Window › Launch in full screen is on (the
        default), the normal centred window otherwise. F11 switches either way;
        leaving full screen returns to the centred 1440×880 window."""
        st = self._fx_settings
        if st is None or st.launch_fullscreen:
            self.showFullScreen()
        else:
            self.show()

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _on_screen_move(self, target: str, fullscreen: bool = True):
        """Move the window to the requested screen ('tv' or 'main'/'primary') and set fullscreen state."""
        app = QApplication.instance()
        if not app:
            return
        screens = app.screens()
        primary = app.primaryScreen()

        target_lower = (target or "").lower().strip()
        tv_screen = None
        for s in screens:
            if s != primary or "samsung" in s.name().lower() or "tv" in s.name().lower():
                tv_screen = s
                break

        if target_lower in ("tv", "television", "samsung", "secondary"):
            if not tv_screen:
                screens = app.screens()
                for s in screens:
                    if s != primary:
                        tv_screen = s
                        break
            if tv_screen:
                self.showNormal()
                geom = tv_screen.geometry()
                self.move(geom.x() + 50, geom.y() + 50)
                if self.windowHandle():
                    self.windowHandle().setScreen(tv_screen)
                if fullscreen:
                    self.showFullScreen()
                else:
                    self.showNormal()
                self._log.append_log(f"SYS: Moved to TV screen ({tv_screen.name()}).")
        elif target_lower in ("main", "primary", "pc", "monitor", "glavni"):
            if primary:
                self.showNormal()
                geom = primary.geometry()
                self.move(
                    geom.x() + (geom.width() - _DEFAULT_W) // 2,
                    geom.y() + (geom.height() - _DEFAULT_H) // 2,
                )
                if self.windowHandle():
                    self.windowHandle().setScreen(primary)
                if fullscreen:
                    self.showFullScreen()
                else:
                    self.showNormal()
                self._log.append_log(f"SYS: Moved to primary monitor ({primary.name()}).")

    def _auto_boot_to_tv(self):
        """At launch: check if PC display state is Extended (Win+P); if not extend it,
        turn on TV, set PC mode, and fullscreen ULTRON on TV."""
        def _bg():
            try:
                import plugins.smartthings as st
                # Check if PC display state is on extended (WIN+P), if not extend it
                was_not_extended = st.ensure_display_extended()
                if was_not_extended:
                    self._log.append_log("SYS: Windows display switched to Extend mode.")
                st.run({"action": "tv_pc_mode", "silent": True}, player=None)
            except Exception:
                pass
        threading.Thread(target=_bg, daemon=True).start()

        def _place():
            self._on_screen_move("tv", fullscreen=True)

        QTimer.singleShot(1500, _place)
        QTimer.singleShot(4000, _place)

    def closeEvent(self, event):
        # ULTRON never just vanishes. The first close request — title-bar X,
        # Alt+F4, the ⏻ button, app.quit() — plays the shutdown sequence (or
        # skips straight past it when animations are off) and the window really
        # closes when that has finished; requests in between are no-ops.
        if not self._fx_shutdown_done:
            event.ignore()
            try:
                self.request_shutdown("ui")
            except Exception:
                import traceback
                traceback.print_exc()
                QTimer.singleShot(0, self._fx_force_close)
            return
        # Stop the voice loop first and finish closing once it has wound down.
        # Closing the window in the same breath as the audio / Live teardown
        # aborted the whole process (0xC0000409) and skipped the cleanup that
        # runs after the event loop. The window disappears immediately; the
        # real close follows within ~4 s at most.
        running = getattr(self, "is_backend_running", None)
        if self.on_shutdown and running and running() and not getattr(self, "_closing", False):
            self._closing = True
            self.on_shutdown()
            event.ignore()
            self.hide()
            self._close_deadline = time.monotonic() + 4.0
            QTimer.singleShot(100, self._finish_close)
            return
        if self.on_shutdown and not getattr(self, "_closing", False):
            self.on_shutdown()
        from core.integrations.common import bind_panel
        bind_panel(None)
        """On exit: turn off TV and switch Windows display back to PC screen only."""
        try:
            import plugins.smartthings as st
            st.on_app_exit()
        except Exception:
            pass
        super().closeEvent(event)

    def _finish_close(self):
        running = getattr(self, "is_backend_running", None)
        if running and running() and time.monotonic() < self._close_deadline:
            QTimer.singleShot(100, self._finish_close)
            return
        self.close()
        # The window is already hidden, so Qt's quit-on-last-window-closed does
        # not fire; closing ULTRON's main window has always meant exiting.
        app = QApplication.instance()
        if app is not None:
            app.quit()

    # ── Cinematics: OFFLINE → BOOTING → INITIALIZING → ONLINE → SHUTTING_DOWN → OFFLINE ──

    def _fx_setup(self) -> None:
        """Sound, theme, the AI core's motion settings, and the hooks the
        cinematics need."""
        self._fx_shutdown_sig.connect(self.request_shutdown)
        self._theme_sig.connect(self._on_theme_sig)
        self._log_sig.connect(self._fx_on_log)
        app = QApplication.instance()
        if app is not None:
            try:
                # Windows is logging off / shutting down: close at once.
                app.commitDataRequest.connect(self._fx_on_session_end)
            except Exception:
                pass
        self._theme_init()
        st = self._fx_settings
        if st is None:
            return
        try:
            self._fx_sfx = _cine.AudioManager(enabled=st.sound, volume=st.sfx_volume, parent=self)
        except Exception as e:
            print(f"[Cinematic] sound unavailable: {e}")
            self._fx_sfx = None
        self.core.fx.configure(st.motion, st.glitch, st.reduced)
        if self._fx_intro_wanted():
            self.core.fx.set_dormant()      # the core is the last thing to come on

    def _fx_intro_wanted(self) -> bool:
        st = self._fx_settings
        return bool(st is not None and st.startup and not st.skip_intro
                    and not os.getenv("ULTRON_SKIP_INTRO"))

    def _fx_on_session_end(self, *_a) -> None:
        self._fx_session_ending = True

    def showEvent(self, event):
        super().showEvent(event)
        if not self._fx_boot_started:
            self._fx_boot_started = True
            self._fx_start_boot()

    def _fx_go(self, state: str) -> None:
        if self._lifecycle is not None:
            try:
                self._lifecycle.go(getattr(_cine, state))
            except Exception:
                pass

    def _fx_start_boot(self) -> None:
        try:
            if not self._fx_intro_wanted():
                self._fx_go("ONLINE")
                QTimer.singleShot(300, self.core.power_on)
                return
            self._fx_go("BOOTING")
            ov = self._make_intro()
            ov.phase_changed.connect(self._fx_boot_phase)
            ov.finished.connect(self._fx_boot_done)
            self._fx_boot = ov
            ov.start()
        except Exception:
            import traceback
            traceback.print_exc()
            self._fx_boot_done()

    def _welcome_available(self) -> bool:
        st = self._fx_settings
        try:
            from core.cinematic.welcome_cues import WELCOME_MP3
            return bool(st is not None and st.welcome and WELCOME_MP3.is_file())
        except Exception:
            return False

    def _make_intro(self, force_welcome: bool = False):
        """The narrated welcome when its recording is installed and enabled,
        the classic boot otherwise."""
        if force_welcome or self._welcome_available():
            try:
                from memory.config_manager import get_output_device
                device = get_output_device()
            except Exception:
                device = ""
            ov = _cine.WelcomeSequence(self, self._fx_settings, self._fx_sfx, self._assistant_name,
                                       dashboard=self._fx_dashboard_snapshot,
                                       readiness=self._fx_readiness,
                                       volume=self._fx_settings.narration_volume, device_hint=device)
            ov.audio_finished.connect(self._intro_audio_done)
            self._intro_begin()
            return ov
        return _cine.BootSequence(self, self._fx_settings, self._fx_sfx,
                                  self._assistant_name, dashboard=self._fx_dashboard_snapshot)

    def _intro_begin(self) -> None:
        """The narration has the floor: effects silent, and (via CarUI.intro_active)
        the microphone closed and ULTRON's live voice held until it ends."""
        self._intro_active = True
        if self._fx_sfx is not None:
            self._fx_sfx.duck("narration", 0.0)

    def _intro_audio_done(self, reason: str) -> None:
        was = self._intro_active
        self._intro_active = False
        if self._fx_sfx is not None:
            self._fx_sfx.unduck("narration")
        cb = self.on_intro_finished
        if was and cb is not None:
            try:
                cb(reason)
            except Exception:
                import traceback
                traceback.print_exc()

    def _fx_readiness(self) -> dict:
        """What the welcome's terminal reports: real state, never a script."""
        snap = self._conn_snapshot or {}
        live = bool((snap.get("gemini_live") or {}).get("connected"))
        c, g = snap.get("claude") or {}, snap.get("gemini") or {}
        names = [n for n, st_ in (("CLAUDE", c), ("GEMINI", g)) if st_.get("state") == "CONNECTED"]
        if names:
            ai = (True, " + ".join(names))
        elif not snap or "CHECKING" in (c.get("state"), g.get("state")):
            ai = (None, "CHECKING")
        elif c.get("usable") or g.get("usable"):
            ai = (None, "READY")
        else:
            ai = (False, "OFFLINE")
        try:
            tools = len(self.get_tool_catalog() or []) if self.get_tool_catalog else 0
        except Exception:
            tools = 0
        try:
            from memory.config_manager import get_output_device, get_voice
            device = (get_output_device() or "SYSTEM DEFAULT").upper()
            voice = (get_voice() or "LIVE VOICE").upper()
        except Exception:
            device, voice = "SYSTEM DEFAULT", "LIVE VOICE"
        if len(device) > 24:
            device = device[:23] + "…"
        return {
            "core": (True, "ONLINE"),
            "neural": (True, f"{tools} TOOLS") if tools else (None, "LOADING"),
            "ai": ai,
            "interface": (True, "SYNCED"),
            "live": (True, "LIVE") if live else (None, "STANDBY"),
            "voice_init": (True, "OK"),
            "audio": (True, device),
            "voice": (True, f"{voice} · LIVE") if live else (None, f"{voice} · STANDBY"),
        }

    def _fx_boot_phase(self, phase: str) -> None:
        if phase in ("INITIALIZING", "BOOTING"):
            self._fx_go("INITIALIZING")

    def _fx_boot_done(self) -> None:
        """The intro ended (finished, skipped or failed): show the real UI."""
        ov, self._fx_boot = self._fx_boot, None
        if self._intro_active:
            self._intro_audio_done("stopped")
        try:
            if ov is not None:
                ov.hide()
                ov.deleteLater()
            if self._lifecycle is not None and self._lifecycle.is_shutting_down:
                return
            self._fx_go("ONLINE")
            self.core.power_on()
            self._input.setFocus()
        except Exception:
            import traceback
            traceback.print_exc()
        self._theme_run_pending()

    def _fx_rect(self, w) -> QRectF:
        tl = w.mapTo(self, QPoint(0, 0))
        return QRectF(tl.x(), tl.y(), w.width(), w.height())

    def _fx_dashboard_snapshot(self):
        """What the boot's entrance assembles: a picture of the real dashboard
        and where its panels are, so it can bring them in one by one."""
        cw = self.centralWidget()
        pm = cw.grab()
        pieces = []
        for name, w, direction in (("left", self._left_panel, (-1, 0)),
                                   ("right", self._right_panel, (1, 0)),
                                   ("center", self._center_split, (0, 1)),
                                   ("footer", self._footer_w, (0, 1)),
                                   ("header", self._header_w, (0, -1))):
            if w is not None and w.isVisible() and w.width() > 2 and w.height() > 2:
                pieces.append((name, self._fx_rect(w), direction))
        title = self._fx_rect(self._title_lbl) if self._title_lbl.isVisible() else None
        return pm, self._fx_rect(cw), pieces, title

    def _fx_secondary_pieces(self) -> list:
        """Panels that power off one by one before the slash."""
        out = []
        for name, w in (("explorer", self._left_panel), ("content", self._content_panel),
                        ("quiz", self._quiz_panel), ("chat", self._chat_dock),
                        ("footer", self._footer_w)):
            if w is not None and w.isVisible() and w.width() > 4 and w.height() > 4:
                out.append((name, self._fx_rect(w)))
        return out

    def request_shutdown(self, source: str = "ui") -> bool:
        """Begin shutting down: play the electric-slash cinematic, then close.

        Safe to call any number of times from the UI thread (the ⏻ button, the
        window's X, a voice command via _fx_shutdown_sig); only the first does
        anything. `source="voice"` means main.py's shutdown task owns the
        backend and the exit — the window only plays the sequence and signals
        `_fx_shutdown_event` when it is over.

        Completion never depends on the animation: if it is off, fails, or
        stalls, a watchdog finishes the shutdown anyway."""
        if self._fx_shutdown_started:
            return False
        self._fx_shutdown_started = True
        self._fx_shutdown_source = source
        if self._lifecycle is not None:
            try:
                self._lifecycle.begin_shutdown()
            except Exception:
                pass
        self._theme_pending = None
        self._theme_abort()
        self._rgb_stop()
        if self._intro_active:
            self._intro_audio_done("stopped")
        for attr in ("_fx_boot", "_fx_preview"):
            ov = getattr(self, attr)
            if ov is not None:
                setattr(self, attr, None)
                try:
                    ov.finished.disconnect()
                except Exception:
                    pass
                (getattr(ov, "halt", None) or ov.finish)()    # the welcome: silence now
                ov.hide()
                ov.deleteLater()
        if source != "voice":
            self._fx_stop_backend()

        st = self._fx_settings
        animate = bool(_cine is not None and st is not None and st.shutdown
                       and self.isVisible() and not self.isMinimized()
                       and not self._fx_session_ending)
        if animate:
            try:
                self._close_setup()
                self._close_controls()
                ov = _cine.ShutdownSequence(self, st, self._fx_sfx, self._assistant_name,
                                            snapshot=self.grab(),
                                            secondary=self._fx_secondary_pieces())
                ov.finished.connect(self._fx_shutdown_finished)
                self._fx_shutdown = ov
                ov.start()
            except Exception:
                import traceback
                traceback.print_exc()
                animate = False
        if not animate:
            QTimer.singleShot(0, self._fx_shutdown_finished)
            return True
        budget = _cine.ShutdownSequence.DURATION / max(0.25, st.speed_factor) + 3.0
        QTimer.singleShot(int(budget * 1000), self._fx_shutdown_finished)
        return True

    def _fx_stop_backend(self) -> None:
        """Stop the voice loop now, so it winds down while the cinematic plays."""
        if self.on_shutdown and not getattr(self, "_closing", False):
            self._closing = True
            try:
                self.on_shutdown()
            except Exception:
                import traceback
                traceback.print_exc()

    def _fx_shutdown_finished(self) -> None:
        if self._fx_shutdown_done:
            return
        self._fx_shutdown_done = True
        self._fx_go("OFFLINE")
        self._fx_shutdown_event.set()
        if self._fx_shutdown_source == "voice":
            # main.py's shutdown task exits the process as soon as it sees the
            # event. Should it not, close the ordinary way.
            if self._fx_shutdown is not None:
                self.hide()             # the cinematic ended on black
            QTimer.singleShot(6000, self._fx_force_close)
            return
        self._fx_force_close()

    def _fx_force_close(self) -> None:
        if getattr(self, "_fx_force_closing", False):
            return
        self._fx_force_closing = True
        self._fx_shutdown_done = True
        self._fx_stop_backend()
        self.hide()
        self._close_deadline = time.monotonic() + 4.0
        QTimer.singleShot(100, self._finish_close)

    def _fx_on_log(self, text: str) -> None:
        """Errors give the AI core a short glitch and a warning pulse."""
        if str(text).startswith(("ERR", "ERROR")):
            try:
                self.core.glitch()
            except Exception:
                pass

    # ── Animation settings panel ─────────────────────────────────────────────

    def _open_animation_settings(self):
        self._close_setup()
        if self._fx_settings is None:
            self._log.append_log("ERR: Animations are unavailable in this install.")
            return
        if self._anim_overlay is not None:
            self._anim_overlay.deleteLater()
        ov = AnimationSettingsOverlay(self._fx_settings, self._theme, parent=self.centralWidget())
        ov.changed.connect(self._fx_apply_settings)
        ov.preview.connect(self._fx_play_preview)
        ov.theme.connect(lambda v: self.change_theme(v, "ui"))
        self._centre_overlay(ov)
        self._anim_overlay = ov

    def _fx_apply_settings(self) -> None:
        st = self._fx_settings
        if self._fx_sfx is not None:
            self._fx_sfx.set_enabled(st.sound)
            self._fx_sfx.set_volume(st.sfx_volume)
        self.core.fx.configure(st.motion, st.glitch, st.reduced)
        if not st.motion:
            self.core.power_on()

    def _fx_play_preview(self, kind: str) -> None:
        """Play a cinematic from the settings panel without touching the
        lifecycle: a shutdown preview returns to the dashboard instead of
        exiting, "welcome" replays the narrated introduction."""
        if (self._fx_preview is not None or self._fx_boot is not None or self._theme_seq is not None
                or self._fx_shutdown_started or _cine is None):
            return
        ov_panel = self._anim_overlay
        if ov_panel is not None:
            ov_panel.hide()
        try:
            if kind == "shutdown":
                ov = _cine.ShutdownSequence(self, self._fx_settings, self._fx_sfx,
                                            self._assistant_name, snapshot=self.grab(),
                                            secondary=self._fx_secondary_pieces(), preview=True)
            elif kind == "welcome":
                ov = self._make_intro(force_welcome=True)
            else:
                ov = _cine.BootSequence(self, self._fx_settings, self._fx_sfx,
                                        self._assistant_name,
                                        dashboard=self._fx_dashboard_snapshot)
            ov.finished.connect(self._fx_preview_done)
            self._fx_preview = ov
            ov.start()
        except Exception:
            import traceback
            traceback.print_exc()
            self._fx_preview_done()

    def _fx_preview_done(self) -> None:
        ov, self._fx_preview = self._fx_preview, None
        if self._intro_active:
            self._intro_audio_done("stopped")
        if ov is not None:
            ov.hide()
            ov.deleteLater()
        self.core.power_on()
        if self._anim_overlay is not None and not self._fx_shutdown_started:
            self._centre_overlay(self._anim_overlay)
        self._theme_run_pending()

    # ── Theme controller (core/theme.py) ─────────────────────────────────────
    # One place applies a colour: the voice tool, typed commands, the settings
    # panel and Customise all end here. Stylesheets, painted widgets, icons,
    # the AI core, the orb and every cinematic effect follow it, and it is saved.

    def _themed_icon(self, target, kind: str, color: str, size: int, mode: str = "icon"):
        """icon_pixmap() that follows the theme: remembered and re-tinted on
        every colour change."""
        base = _CSS_THEME.normalize(color.lower())
        self._icon_reg.append((target, kind, base, size, mode))
        self._set_icon(target, kind, _CSS_THEME.shift(base, _ACCENT["hex"]), size, mode)
        return target

    @staticmethod
    def _set_icon(target, kind: str, color: str, size: int, mode: str) -> None:
        ic = icon_pixmap(kind, color, size)
        if mode == "pixmap":
            target.setPixmap(ic.pixmap(size, size))
        else:
            target.setIcon(ic)

    def _retint_icons(self, accent: str) -> None:
        keep = []
        for target, kind, base, size, mode in self._icon_reg:
            try:
                self._set_icon(target, kind, _CSS_THEME.shift(base, accent), size, mode)
                keep.append((target, kind, base, size, mode))
            except RuntimeError:          # its widget is gone
                pass
        self._icon_reg = keep

    def _theme_init(self) -> None:
        """Bring the saved theme back — including the literal colours the
        palette alone never reached."""
        saved = {}
        try:
            from memory.config_manager import get_ui_theme
            saved = get_ui_theme()
        except Exception:
            pass
        spec = None
        if saved.get("name") == _theme.RGB:
            spec = _theme.resolve(_theme.RGB)
        elif saved.get("hex"):
            spec = _theme.resolve(saved["hex"])
        if spec is None:
            spec = _theme.ThemeSpec("default", DEFAULT_UI_COLOR, "DEFAULT")
        self._theme_apply(spec, persist=False)

    def _theme_apply(self, spec, persist: bool = True) -> None:
        """Instant: palette, every stylesheet, every painted element; saved."""
        self._rgb_stop()
        old = current_palette()
        apply_ui_accent(spec.hex)
        retheme_all_widgets(old, current_palette())
        self._theme_paint(spec.hex, full=True)
        self._theme = spec
        if spec.is_rgb:
            self._rgb_start()
        if persist:
            try:
                from memory.config_manager import save_ui_theme
                save_ui_theme(spec.name, spec.hex)
            except Exception as e:
                self._log.append_log(f"ERR: Theme not saved — {e}")

    def _theme_paint(self, accent: str, full: bool = True) -> None:
        """The painted half of a theme: everything that is not a stylesheet."""
        vivid = _theme.vivid(accent)
        if _cine is not None and self._theme_seq is None:
            _cine.fx.set_accent(accent)
        try:
            self.core.set_accent(vivid)
            CATS["core"] = (CATS["core"][0], vivid)
            orb = getattr(self.hud, "_orb", None)
            if orb is not None:
                orb.set_accent(accent)
            if hasattr(self, "_bar_cpu"):
                self._bar_cpu._color = C.PRI
                self._bar_cpu.update()
            if full:
                self.hud._grid_cache = None
                self._retint_icons(accent)
                app = QApplication.instance()
                for w in (app.allWidgets() if app else ()):
                    if isinstance(w, ToggleSwitch):
                        base = w.property("_ultron_base_color")
                        if base is None:
                            base = _CSS_THEME.normalize(w._color.lower())
                            w.setProperty("_ultron_base_color", base)
                        w._color = _CSS_THEME.shift(base, accent)
                        w.update()
                self._graph_dirty = True
                self._update_core_state()
            self.hud.update()
            self.graph.update()
        except Exception:
            import traceback
            traceback.print_exc()

    def _rgb_start(self) -> None:
        if self._rgb_timer is None:
            self._rgb_timer = QTimer(self)
            self._rgb_timer.timeout.connect(self._rgb_tick)
        self._rgb_t0 = time.monotonic()
        self._rgb_queue = []
        self._rgb_timer.start(40)

    def _rgb_stop(self) -> None:
        if self._rgb_timer is not None:
            self._rgb_timer.stop()

    def _rgb_tick(self) -> None:
        """RGB mode: painted elements follow the hue every tick; stylesheets
        are recoloured a few widgets per tick, so the cost per frame stays
        small however large the interface is."""
        if (self._theme_seq is not None or self._fx_shutdown is not None
                or self._fx_boot is not None or self._fx_preview is not None):
            return          # an overlay covers the interface: nothing to recolour yet
        t = time.monotonic() - self._rgb_t0
        deg = int(t / _theme.RGB_PERIOD * 360.0) // 2 * 2 % 360       # 2° steps
        hx = _theme.hsv_to_hex(deg / 360.0, 0.92, 1.0)
        apply_ui_accent(hx)
        bucket = deg // 30
        self._theme_paint(hx, full=(bucket != self._rgb_bucket))
        self._rgb_bucket = bucket
        if not self._rgb_queue:
            app = QApplication.instance()
            self._rgb_queue = [w for w in (app.allWidgets() if app else ()) if w.styleSheet()]
        for _ in range(8):
            if not self._rgb_queue:
                break
            w = self._rgb_queue.pop()
            try:
                _CSS_THEME.restyle(w, hx)
            except RuntimeError:
                pass

    def _on_theme_sig(self, name: str, hex_color: str, source: str) -> None:
        spec = _theme.resolve(_theme.RGB) if name == _theme.RGB else _theme.resolve(hex_color)
        if spec is not None:
            self.change_theme(spec, source)

    def change_theme(self, value, source: str = "ui") -> str:
        """Change the interface colour (UI thread). `value` is a ThemeSpec or
        anything core.theme.resolve() accepts. Returns "started", "applied",
        "queued", "same", "invalid" or "unavailable"."""
        spec = value if isinstance(value, _theme.ThemeSpec) else _theme.resolve(str(value))
        if spec is None:
            return "invalid"
        if self._fx_shutdown_started:
            return "unavailable"
        busy = (self._theme_seq is not None or self._fx_boot is not None
                or self._fx_preview is not None)
        if busy:
            # Rapid commands: the newest one wins and runs when this one ends.
            self._theme_pending = (spec, source)
            return "queued"
        cur = self._theme
        if cur is not None and cur.name == spec.name and cur.hex == spec.hex:
            return "same"
        st = self._fx_settings
        animate = bool(_cine is not None and st is not None and st.color_transform
                       and self.isVisible() and not self.isMinimized())
        if not animate:
            self._theme_apply(spec)
            self._theme_announce(spec, source)
            return "applied"
        try:
            self._close_setup()
            self._close_controls()
            snap, rect = self._fx_window_snapshot()
            if not spec.is_rgb:
                _cine.fx.set_accent(spec.hex)
            accent_at = (lambda t: _theme.rgb_hex_at(t, 3.0)) if spec.is_rgb else None
            ov = _cine.ColorTransformSequence(
                self, st, self._fx_sfx, name=self._assistant_name, spec=spec, snapshot=snap,
                snap_rect=rect, apply_theme=lambda: self._theme_apply(spec),
                grab_new=self._fx_window_snapshot, core_point=self._core_point(),
                lines=_theme.protocol_lines(spec), accent_at=accent_at)
            ov.finished.connect(lambda: self._theme_transform_done(ov))
            self._theme_seq, self._theme_seq_spec = ov, spec
            self._theme_source = source
            ov.start()
            budget = ov.duration / max(0.25, st.speed_factor) + 3.0
            QTimer.singleShot(int(budget * 1000), lambda: self._theme_transform_done(ov))
        except Exception:
            import traceback
            traceback.print_exc()
            self._theme_seq = None
            self._theme_apply(spec)
            self._theme_announce(spec, source)
            return "applied"
        return "started"

    def _theme_transform_done(self, ov) -> None:
        if self._theme_seq is not ov:
            return
        spec, source = self._theme_seq_spec, getattr(self, "_theme_source", "ui")
        self._theme_seq, self._theme_seq_spec = None, None
        if not ov.applied:
            self._theme_apply(spec)        # the colour always lands, animation or not
        elif _cine is not None and not spec.is_rgb:
            _cine.fx.set_accent(spec.hex)
        try:
            ov.hide()
            ov.deleteLater()
        except RuntimeError:
            pass
        self.core.pulse()
        self._theme_announce(spec, source)
        self._theme_run_pending()

    def _theme_abort(self) -> None:
        """Shutdown during a colour change: land the colour, drop the show."""
        ov, spec = self._theme_seq, self._theme_seq_spec
        if ov is None:
            return
        self._theme_seq, self._theme_seq_spec = None, None
        try:
            ov.finished.disconnect()
        except Exception:
            pass
        ov.finish()
        ov.hide()
        ov.deleteLater()
        if not ov.applied:
            self._theme_apply(spec)

    def _theme_run_pending(self) -> None:
        if self._theme_pending is not None and self._theme_seq is None and not self._fx_shutdown_started:
            spec, source = self._theme_pending
            self._theme_pending = None
            QTimer.singleShot(150, lambda: self.change_theme(spec, source))

    def _theme_announce(self, spec, source: str) -> None:
        name = "RGB" if spec.is_rgb else ("Custom" if spec.name == _theme.CUSTOM
                                          else spec.label.title())
        line = f"{name} protocol initialized. All systems operational."
        self._log.append_log(f"SYS: {line}")
        if source == "voice" and self.request_say is not None:
            try:
                self.request_say("[THEME_READY] The interface colour change you started has finished "
                                 f"on screen. Say exactly this, nothing else: \"{line}\" "
                                 "(in the user's language if they are speaking another).")
            except Exception:
                pass

    def _fx_window_snapshot(self):
        """A picture of the real interface (without any overlay) and where it is."""
        cw = self.centralWidget()
        return cw.grab(), self._fx_rect(cw)

    def _core_point(self) -> QPointF:
        try:
            r = self._fx_rect(self.core)
            return r.center() if self.core.isVisible() else QPointF(self.width() * 0.82, self.height() * 0.25)
        except Exception:
            return QPointF(self.width() * 0.82, self.height() * 0.25)

    def _apply_responsive(self):
        """Compact header, chips and legend when the window is small."""
        w = self.width()
        compact = w < 1240
        if hasattr(self, "_sub_lbl"):
            self._sub_lbl.setVisible(w >= 1180)
            for btn, full, short in ((self._workspace_btn, "WORKSPACE", "▦"),
                                     (self._cam_toggle_btn, "📷  KAMERA", "📷")):
                btn.setText(short if compact else full)
            if not str(self._screen_share_btn.text()).startswith("●"):
                self._screen_share_btn.setText("🖥" if compact else "🖥  SHARE SCREEN")
            self._header_search.setMinimumWidth(150 if compact else 260)
        if hasattr(self, "_chat_chips"):
            narrow = self._chat_dock.width() < 640
            for b, full in self._chat_chips:
                b.setText("" if narrow else full)
        if hasattr(self, "graph_panel"):
            self.graph_panel.legend.setVisible(self.graph_panel.width() >= 620)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._apply_responsive)
        cw = self.centralWidget()
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 390
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._remote_overlay and self._remote_overlay.isVisible():
            ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
            self._remote_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._customize_overlay and self._customize_overlay.isVisible():
            ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
            self._customize_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        # Camera preview — bottom-right corner of the center/HUD area
        pw = _CameraPreview._W
        ph = self._cam_preview.height() or _CameraPreview._H
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )
        # Clipboard panel — bottom-center
        if hasattr(self, '_clipboard_panel') and self._clipboard_panel.isVisible():
            self._position_clipboard_panel()
        # Quick drawer — reposition if open
        if hasattr(self, '_quick_drawer') and self._quick_drawer.isVisible():
            self._position_quick_drawer()
        if hasattr(self, '_ctrl_drawer') and self._ctrl_drawer.isVisible():
            self._position_ctrl_drawer()
        self._fit_video()

    def _update_metrics(self):
        snap = _metrics.snapshot()
        from core.runtime_status import snapshot
        status = snapshot()
        if hasattr(self, "_sys_labels"):
            self._sys_labels["backend"].setText("Gemini Live · connected" if status["connected"]
                                                else "Gemini Live · not connected")
            self._sys_labels["backend"].setStyleSheet(
                f"color: {'#22c55e' if status['connected'] else '#f5b942'};")
            self._sys_labels["model"].setText(str(status["model"]))
            self._sys_labels["tool"].setText(str(status["tool"])[:28])
            self._sys_labels["tool"].setToolTip(status["last_result"])
            records = self.get_plugins() if self.get_plugins else []
            enabled = sum(bool(x.get("enabled")) for x in records)
            failed = sum(not x.get("valid", False) for x in records)
            self._sys_labels["plugins"].setText(f"{enabled} enabled · {failed} errors" if records else "loading…")
            up = int(time.monotonic() - self._t_started)
            self._sys_labels["uptime"].setText(f"{up // 3600}h {up % 3600 // 60:02d}m")
        self._update_core_state()


        # CPU
        cpu = snap["cpu"]
        self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")

        # MEM
        mem = snap["mem"]
        self._bar_mem.set_value(mem, f"{mem:.0f}%")

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        self._bar_net.set_value(net_pct, net_str)

        # GPU
        gpu = snap["gpu"]
        if gpu >= 0:
            self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._bar_gpu.set_value(0, "N/A")

        # TMP
        tmp = snap["tmp"]
        if tmp >= 0:
            tmp_pct = min(100, (tmp / 100) * 100)
            self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
        else:
            self._bar_tmp.set_value(0, "N/A")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("UP  --:--")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(f"PROC  {proc_count}")
        except Exception:
            self._proc_lbl.setText("PROC  --")



    def write_log(self, text: str):
        self._log_sig.emit(str(text))

    def _on_screen_share_state_change(self, active: bool):
        try:
            from core.screen_share import screen_share_manager
            info = screen_share_manager.get_target_info()
            target_name = info.get("title") or "Ekran"
            if len(target_name) > 18:
                short_t = target_name[:15] + "…"
            else:
                short_t = target_name

            btn_txt = f"● {short_t}" if active else "🖥  SHARE SCREEN"
            tooltip = f"Aktivno deljenje: {target_name} — Ultron vidi i kontroliše prozor" if active else "Podeli ekran ili prozor sa Ultron-om"

            if hasattr(self, "_screen_share_btn"):
                self._screen_share_btn.setText(btn_txt)
                self._screen_share_btn.setToolTip(tooltip)
                if active:
                    self._screen_share_btn.setStyleSheet("""
                        QPushButton {
                            background: rgba(255, 40, 70, 0.22); color: #ff3355;
                            border: 1px solid #ff3355; border-radius: 4px; padding: 2px 8px;
                            font-weight: bold;
                        }
                        QPushButton:hover { background: rgba(255, 40, 70, 0.35); }
                    """)
                else:
                    drawer_style = self._drawer_btn.styleSheet() if hasattr(self, "_drawer_btn") else ""
                    self._screen_share_btn.setStyleSheet(drawer_style)

            if hasattr(self, "_ctrl_screen_share_btn"):
                self._ctrl_screen_share_btn.setText(f"● {short_t} [ON]" if active else "🖥  SCREEN SHARE")
                if active:
                    self._ctrl_screen_share_btn.setStyleSheet("color: #ff3355; border-color: #ff3355; background: rgba(255, 40, 70, 0.18); font-weight: bold;")
                else:
                    btn_dim = getattr(self, "_BTN_DIM", "color: #00f0ff; background: transparent; border: 1px solid #00f0ff;")
                    self._ctrl_screen_share_btn.setStyleSheet(btn_dim)
        except Exception as e:
            print(f"[ScreenShare] State change error: {e}")

    def _toggle_screen_share(self):
        try:
            from core.screen_share import screen_share_manager
            if screen_share_manager.is_active():
                new_state = screen_share_manager.set_active(False)
                self._on_screen_share_state_change(new_state)
                self.write_log("SYS: Deljenje ekrana ISKLJUČENO.")
            else:
                self._open_screen_picker()
        except Exception as e:
            print(f"[ScreenShare] Toggle error: {e}")

    def _open_screen_picker(self):
        try:
            ov = ScreenSourcePickerOverlay(parent=self.centralWidget())
            ov.source_picked.connect(self._on_screen_source_picked)
            self._centre_overlay(ov)
            self._screen_picker_overlay = ov
        except Exception as e:
            print(f"[ScreenShare] Failed to open picker: {e}")

    def _on_screen_source_picked(self, src: dict):
        try:
            from core.screen_share import screen_share_manager
            stype = src.get("type", "screen")
            if stype == "window":
                screen_share_manager.set_target(
                    target_type="window",
                    hwnd=src.get("hwnd"),
                    title=src.get("title", ""),
                    process=src.get("process", "")
                )
            else:
                screen_share_manager.set_target(
                    target_type="screen",
                    screen_index=src.get("index", 0),
                    title=src.get("name", "Screen")
                )
            screen_share_manager.set_active(True)
            self._on_screen_share_state_change(True)
            t_name = src.get("name") or src.get("title") or "Ekran"
            self.write_log(f"SYS: Deljenje ekrana aktivirano za: {t_name}")
        except Exception as e:
            print(f"[ScreenShare] Source selection error: {e}")

    def _on_show_pointer(self, x: int, y: int, label: str, bbox: object, duration: float):
        from core.screen_overlay import AnimatedScreenArrowOverlay
        if self._active_pointer_overlay is not None:
            try:
                self._active_pointer_overlay.close()
                self._active_pointer_overlay.deleteLater()
            except Exception:
                pass
        self._active_pointer_overlay = AnimatedScreenArrowOverlay(
            target_x=x, target_y=y, label=label, bounding_box=bbox, duration=duration
        )
        self._active_pointer_overlay.show()

    def show_screen_pointer(self, x: int, y: int, label: str = "", bbox=None, duration: float = 5.0):
        self._pointer_sig.emit(int(x), int(y), str(label), bbox, float(duration))

    def set_screen_share_active(self, active: bool):
        self._screen_share_sig.emit(bool(active))

    # ── Command-center layout ────────────────────────────────────────────────

    _CARD_SS = ("QFrame#Card { background: rgba(6,15,26,0.94); border: 1px solid #10263a; "
                "border-radius: 12px; } QLabel { background: transparent; border: none; }")

    _CHIP_SS = ("QPushButton {{ background: rgba(9,22,37,0.9); color: #b9d4e8; border: 1px solid #17324a; "
                "border-radius: 7px; padding: 3px 10px; font-family: '{ui}'; font-size: 8pt; }}"
                "QPushButton:hover {{ color: #ffffff; border-color: {pri}; }}"
                "QPushButton:checked {{ color: {pri}; border-color: {pri}; background: {gho}; }}")

    def _chip_ss(self) -> str:
        return self._CHIP_SS.format(ui=FONT_UI, pri=C.PRI, gho=C.PRI_GHO)

    def _card(self, title: str = "") -> tuple:
        f = QFrame()
        f.setObjectName("Card")
        f.setStyleSheet(self._CARD_SS)
        lay = QVBoxLayout(f)
        lay.setContentsMargins(11, 10, 11, 11)
        lay.setSpacing(6)
        if title:
            lay.addWidget(section_label(title))
        return f, lay

    def _action_btn(self, text: str, slot, icon: str = "", h: int = 30) -> QPushButton:
        b = QPushButton(("  " + text) if icon else text)
        if icon:
            self._themed_icon(b, icon, "#7fdcf5", 16)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFixedHeight(h)
        b.setStyleSheet(f"QPushButton {{ background: rgba(8,24,40,0.9); color: #cfeaff; "
                        f"border: 1px solid #1a4560; border-radius: 8px; font-family: '{FONT_UI}'; "
                        f"font-size: 9pt; }} QPushButton:hover {{ border-color: {C.PRI}; color: #ffffff; "
                        f"background: {C.PRI_GHO}; }} QPushButton:pressed {{ background: #0b2e40; }}")
        b.clicked.connect(slot)
        return b

    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setObjectName("Header")
        w.setFixedHeight(60)
        w.setStyleSheet("QWidget#Header { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                        "stop:0 #061222, stop:1 #03070d); border-bottom: 1px solid #10263a; }"
                        "QLabel { background: transparent; }")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(8)

        logo = QLabel()
        self._themed_icon(logo, "core", C.PRI, 30, "pixmap")
        lay.addWidget(logo)
        _disp = self._assistant_name.upper()
        self._title_lbl = QLabel(_disp)
        tf = QFont(FONT_HEAD, 18, QFont.Weight.Bold)
        tf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 4)
        self._title_lbl.setFont(tf)
        self._title_lbl.setStyleSheet(f"color: {C.PRI};")
        lay.addWidget(self._title_lbl)
        lay.addSpacing(10)
        vline = QFrame()
        vline.setFixedSize(1, 22)
        vline.setStyleSheet("background: #1a3954;")
        lay.addWidget(vline)
        lay.addSpacing(6)
        self._sub_lbl = QLabel(f"{APP_NAME.upper()}  •  COMMAND CENTER")
        sf = QFont(FONT_UI, 8, QFont.Weight.DemiBold)
        sf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1)
        self._sub_lbl.setFont(sf)
        self._sub_lbl.setStyleSheet("color: #b9cfe0;")
        lay.addWidget(self._sub_lbl)
        lay.addSpacing(12)

        chip = self._chip_ss()
        self._drawer_btn = QPushButton("⚙")
        self._drawer_btn.setFixedSize(30, 28)
        self._drawer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._drawer_btn.setStyleSheet(chip)
        self._drawer_btn.setCheckable(True)
        self._drawer_btn.setToolTip("Setup — things you set once")
        self._drawer_btn.clicked.connect(self._toggle_drawer)
        lay.addWidget(self._drawer_btn)

        # A SECOND drawer, and the split is by how often a thing is touched:
        # setup behind ⚙, everyday switches behind 🎛.
        self._ctrl_btn = QPushButton("🎛")
        self._ctrl_btn.setFixedSize(30, 28)
        self._ctrl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ctrl_btn.setToolTip("Controls — the everyday switches")
        self._ctrl_btn.setStyleSheet(chip)
        self._ctrl_btn.setCheckable(True)
        self._ctrl_btn.clicked.connect(self._toggle_controls)
        lay.addWidget(self._ctrl_btn)
        self._header_full_text: dict = {}
        workspace_btn = QPushButton("WORKSPACE")
        workspace_btn.setFixedHeight(28)
        workspace_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        workspace_btn.setToolTip("AI Workspace · maps, stays, research, mail & files, MCP Hub")
        workspace_btn.setStyleSheet(chip)
        workspace_btn.clicked.connect(lambda: self._open_workspace())
        lay.addWidget(workspace_btn)
        self._workspace_btn = workspace_btn
        self._screen_share_btn = QPushButton("🖥  SHARE SCREEN")
        self._screen_share_btn.setFixedHeight(28)
        self._screen_share_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._screen_share_btn.setToolTip("Podeli ekran sa Ultron-om (omogućava mu da vidi i kontroliše ekran)")
        self._screen_share_btn.setStyleSheet(chip)
        self._screen_share_btn.clicked.connect(self._toggle_screen_share)
        lay.addWidget(self._screen_share_btn)
        self._cam_toggle_btn = QPushButton("📷  KAMERA")
        self._cam_toggle_btn.setFixedHeight(28)
        self._cam_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cam_toggle_btn.setToolTip("Uključi ili isključi prikaz kamere nonstop")
        self._cam_toggle_btn.setStyleSheet(chip)
        self._cam_toggle_btn.clicked.connect(self._toggle_camera_stream_btn)
        lay.addWidget(self._cam_toggle_btn)
        lay.addStretch(1)

        self._search_model = QStringListModel([])
        self._search_index: dict[str, str] = {}
        self._header_search = self._make_search_field(
            "Search ULTRON…  (commands, tools, modules, memory)  — Enter runs a command")
        self._header_search.setMinimumWidth(260)
        self._header_search.setMaximumWidth(460)
        lay.addWidget(self._header_search, 2)
        lay.addStretch(1)

        self._bell_btn = QPushButton("🔔")
        self._bell_btn.setFixedHeight(28)
        self._bell_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bell_btn.setToolTip("Connection errors")
        self._bell_btn.setStyleSheet(chip)
        self._bell_btn.clicked.connect(lambda: self._run_manual("errors"))
        lay.addWidget(self._bell_btn)
        for attr, txt, tip in (("_left_toggle", "◧", "Show / hide the explorer"),
                               ("_right_toggle", "◨", "Show / hide the AI core panel")):
            b = QPushButton(txt)
            b.setFixedSize(30, 28)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip)
            b.setStyleSheet(chip)
            setattr(self, attr, b)
            lay.addWidget(b)
        self._left_toggle.clicked.connect(lambda: self._left_panel.setVisible(not self._left_panel.isVisible()))
        self._right_toggle.clicked.connect(lambda: self._right_panel.setVisible(not self._right_panel.isVisible()))
        # Power: the same path as the window's close button — the shutdown
        # cinematic, then a clean exit.
        self._power_btn = QPushButton("⏻")
        self._power_btn.setFixedSize(30, 28)
        self._power_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._power_btn.setToolTip(f"Shut down {_disp}")
        self._power_btn.setStyleSheet(
            chip + f"QPushButton {{ color: {C.RED}; font-size: 12pt; }}"
                   f"QPushButton:hover {{ color: #ffffff; border-color: {C.RED}; background: #2a0710; }}")
        self._power_btn.clicked.connect(self.close)
        lay.addWidget(self._power_btn)
        lay.addSpacing(6)

        right_col = QVBoxLayout()
        right_col.setSpacing(0)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont(FONT_HEAD, 14, QFont.Weight.Bold))
        self._clock_lbl.setStyleSheet(f"color: {C.PRI};")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont(FONT_UI, 7))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._date_lbl)
        lay.addLayout(right_col)
        return w

    def _make_search_field(self, placeholder: str) -> QLineEdit:
        e = QLineEdit()
        e.setPlaceholderText(placeholder)
        e.setClearButtonEnabled(True)
        act = e.addAction(icon_pixmap("search", "#7f9bb3", 14), QLineEdit.ActionPosition.LeadingPosition)
        self._themed_icon(act, "search", "#7f9bb3", 14)
        e.setFixedHeight(32)
        e.setStyleSheet(f"QLineEdit {{ color: #e6f4ff; background: rgba(8,20,34,0.92); "
                        f"border: 1px solid #17324a; border-radius: 9px; padding: 2px 8px; "
                        f"font-family: '{FONT_UI}'; font-size: 9pt; }}"
                        f"QLineEdit:focus {{ border-color: {C.PRI}; }}")
        comp = QCompleter(self._search_model, e)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        comp.setMaxVisibleItems(10)
        comp.popup().setStyleSheet(f"QListView {{ background: #07121f; color: #d8eefc; border: 1px solid #17324a; "
                                   f"font-family: '{FONT_UI}'; font-size: 9pt; }}"
                                   f"QListView::item:selected {{ background: #0d2236; color: {C.PRI}; }}")
        comp.activated[str].connect(self._search_pick)
        e.setCompleter(comp)
        e.textChanged.connect(self.graph.set_search)
        e.returnPressed.connect(lambda ed=e: self._search_enter(ed))
        return e

    def _search_pick(self, text: str):
        nid = self._search_index.get(text)
        if nid:
            self._show_home(graph=True)
            self.graph.focus_node(nid)

    def _search_enter(self, edit: QLineEdit):
        text = edit.text().strip()
        if not text:
            return
        if text in self._search_index:
            self._search_pick(text)
            return
        hits = self.graph.matches(text, 1)
        if hits and edit is not self._header_search:
            self._show_home(graph=True)
            self.graph.focus_node(hits[0].id)
            return
        if edit is self._header_search:
            if hits and hits[0].label.lower() == text.lower():
                self._show_home(graph=True)
                self.graph.focus_node(hits[0].id)
                return
            edit.clear()
            self._submit_command(text)

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))
        if hasattr(self, "_chat_time"):
            self._chat_time.setText(time.strftime("Today %H:%M"))

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("LeftCol")
        w.setStyleSheet("QWidget#LeftCol { background: transparent; }")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(10)

        card, cl = self._card("ULTRON Explorer")
        cl.setSpacing(2)
        self._nav_btns: dict[str, QPushButton] = {}
        nav_ss = (f"QPushButton {{ text-align: left; padding: 6px 10px; color: #c6d9e8; background: transparent; "
                  f"border: 1px solid transparent; border-radius: 8px; font-family: '{FONT_UI}'; font-size: 9pt; }}"
                  f"QPushButton:hover {{ background: #0b1c2e; color: #ffffff; }}"
                  f"QPushButton:checked {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0b3550, "
                  f"stop:1 #071a2a); border: 1px solid {C.PRI_DIM}; color: #ffffff; }}")
        for key, label, icon in (("home", "Home · Neural graph", "home"), ("chat", "Chat", "chat"),
                                 ("avatar", "Avatar HUD", "avatar"), ("orbit", "Orbit view", "orbit"),
                                 ("tools", "Tools", "tools"), ("memory", "Memory", "memory"),
                                 ("plugins", "Plugins", "plugins"), ("workspace", "Workspace", "workspace"),
                                 ("settings", "Settings", "settings")):
            b = QPushButton("  " + label)
            self._themed_icon(b, icon, "#9fd8f0", 16)
            b.setIconSize(QSize(16, 16))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(nav_ss)
            b.setCheckable(key in ("home", "avatar", "orbit"))
            b.clicked.connect(lambda _c=False, k=key: self._nav(k))
            cl.addWidget(b)
            self._nav_btns[key] = b
        self._nav_btns["home"].setChecked(True)
        cl.addSpacing(6)
        self._left_search = self._make_search_field("Search modules, tools, features…")
        cl.addWidget(self._left_search)
        lay.addWidget(card)

        card, cl = self._card("Node Inspector")
        self._inspector = NodeInspector()
        self._inspector.action.connect(self._inspector_action)
        cl.addWidget(self._inspector)
        lay.addWidget(card)

        card, cl = self._card("Top Modules")
        self._modules_box = QVBoxLayout()
        self._modules_box.setSpacing(1)
        cl.addLayout(self._modules_box)
        lay.addWidget(card)

        card, cl = self._card("AI Connections")
        self._left_rows: dict[str, ConnectionRow] = {}
        for key, title, icon, colr in (("claude", "Claude", "claude", "#f5b942"),
                                       ("gemini", "Gemini", "gemini", "#8ab4ff"),
                                       ("mcp", "MCP Servers", "mcp", "#2ee6a6")):
            r = ConnectionRow(key, title, icon, colr, compact=True)
            r.clicked.connect(self._conn_row_clicked)
            cl.addWidget(r)
            self._left_rows[key] = r
        lay.addWidget(card)

        card, cl = self._card("Graph Controls")

        def trow(label: str, checked: bool, fn) -> ToggleSwitch:
            h = QHBoxLayout()
            lab = QLabel(label)
            lab.setFont(QFont(FONT_UI, 8))
            lab.setStyleSheet("color: #c6d9e8;")
            t = ToggleSwitch(checked, C.PRI)
            t.toggled.connect(fn)
            h.addWidget(lab)
            h.addStretch()
            h.addWidget(t)
            cl.addLayout(h)
            return t

        def _set(attr, on):
            setattr(self.graph, attr, on)
            self.graph.update()

        self._tg_edges = trow("Show connections", True, lambda on: _set("show_edges", on))
        self._tg_tools = trow("Show tool & agent nodes", True, lambda on: self._set_leaf(("tool", "agent"), on))
        self._tg_mcpt = trow("Show MCP tool nodes", True, lambda on: self._set_leaf(("mcp_tool",), on))
        self._tg_misc = trow("Show plugin / memory nodes", True,
                             lambda on: self._set_leaf(("plugin", "memory", "automation", "files"), on))
        self._tg_stat = trow("Show status indicators", True, lambda on: _set("show_status", on))
        self._tg_rot = trow("Auto-rotate", True, lambda on: _set("auto_rotate", on))
        self._view_combo = QComboBox()
        self._view_combo.addItems(["3D View", "2D View"])
        self._view_combo.setStyleSheet(self._combo_ss())
        self._view_combo.currentIndexChanged.connect(lambda i: self.graph_panel.mode_btn.setChecked(i == 0))
        self.graph_panel.mode_btn.toggled.connect(
            lambda on: self._view_combo.setCurrentIndex(0 if on else 1))
        cl.addWidget(self._view_combo)
        lay.addWidget(card)

        card, cl = self._card("Quick Commands")
        for title, command in [("Claude limits & brain", "__OPEN_CLAUDE_BRAIN__"),
                               ("Chrome · observe", "Observe my existing Chrome window and tell me what is open."),
                               ("Audio · outputs", "List my available audio outputs."),
                               ("Mail · inbox", "Check my latest five inbox emails."),
                               ("News · briefing", "Give me a short world news briefing with sources.")]:
            button = QPushButton(title)
            button.setFixedHeight(28)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(self._chip_ss() + "QPushButton { text-align: left; padding-left: 10px; }")
            button.clicked.connect(lambda checked=False, cmd=command: self._quick_command(cmd))
            cl.addWidget(button)
        lay.addWidget(card)
        lay.addStretch()

        scroll = QScrollArea()
        scroll.setFixedWidth(_LEFT_W)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }"
                             "QScrollBar:vertical { width: 6px; background: transparent; }"
                             "QScrollBar::handle:vertical { background: #1a3954; border-radius: 3px; min-height: 24px; }"
                             "QScrollBar::add-line, QScrollBar::sub-line { height: 0; }")
        scroll.viewport().setStyleSheet("background: transparent;")
        scroll.setWidget(w)
        return scroll

    def _combo_ss(self) -> str:
        return (f"QComboBox {{ color: #d8eefc; background: rgba(8,20,34,0.92); border: 1px solid #17324a; "
                f"border-radius: 7px; padding: 4px 8px; font-family: '{FONT_UI}'; font-size: 8pt; }}"
                f"QComboBox:hover {{ border-color: {C.PRI}; }} QComboBox::drop-down {{ border: none; width: 18px; }}"
                f"QComboBox QAbstractItemView {{ background: #07121f; color: #d8eefc; "
                f"selection-background-color: #0d2236; border: 1px solid #17324a; }}")

    def _quick_command(self, command):
        if command == "__OPEN_CLAUDE_BRAIN__":
            self._open_workspace("settings")
            return
        self._submit_command(command)

    def _submit_command(self, text: str):
        """A user command from the chat box, header search or a quick button."""
        text = (text or "").strip()
        if not text:
            return
        self._log.append_log("You: " + text)
        target = self.on_user_command or self.on_text_command
        if target:
            threading.Thread(target=target, args=(text,), daemon=True).start()
        else:
            self._log.append_log("SYS: The assistant backend is still starting — try again in a moment.")

    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("RightCol")
        w.setStyleSheet("QWidget#RightCol { background: transparent; }")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 0, 0, 0)
        lay.setSpacing(10)

        card, cl = self._card()
        hdr = QHBoxLayout()
        ic = QLabel()
        self._themed_icon(ic, "live", C.PRI, 18, "pixmap")
        hdr.addWidget(ic)
        t = QLabel(f"{self._assistant_name.upper()} AI CORE")
        tf = QFont(FONT_HEAD, 11, QFont.Weight.Bold)
        tf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1)
        t.setFont(tf)
        t.setStyleSheet(f"color: {C.PRI};")
        self._core_title = t
        hdr.addWidget(t)
        hdr.addStretch()
        self._core_badge = QLabel("● INITIALISING")
        self._core_badge.setFont(QFont(FONT_UI, 8, QFont.Weight.DemiBold))
        self._core_badge.setStyleSheet("color: #94a3b8;")
        hdr.addWidget(self._core_badge)
        cl.addLayout(hdr)
        self.core = AICore(self._assistant_name)
        cl.addWidget(self.core)
        self._core_sub = QLabel("Checking connections…")
        self._core_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._core_sub.setWordWrap(True)
        self._core_sub.setFont(QFont(FONT_UI, 8))
        self._core_sub.setStyleSheet("color: #7f9bb3;")
        self._core_sub.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        cl.addWidget(self._core_sub)
        lay.addWidget(card)

        card, cl = self._card("Connection Status")
        self._right_rows: dict[str, ConnectionRow] = {}
        for key, title, icon, colr in (("claude", "Claude", "claude", "#f5b942"),
                                       ("gemini", "Gemini", "gemini", "#8ab4ff"),
                                       ("mcp", "MCP Server", "mcp", "#2ee6a6")):
            r = ConnectionRow(key, title, icon, colr)
            r.clicked.connect(self._conn_row_clicked)
            cl.addWidget(r)
            self._right_rows[key] = r
        lay.addWidget(card)

        card, cl = self._card("Active Provider")
        self._active_lbl = QLabel("—")
        self._active_lbl.setFont(QFont(FONT_UI, 10, QFont.Weight.DemiBold))
        self._active_lbl.setStyleSheet("color: #eaf6ff;")
        self._active_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        cl.addWidget(self._active_lbl)
        self._order_combo = QComboBox()
        self._order_combo.addItems(["Claude first, then Gemini", "Gemini first, then Claude",
                                    "Claude only", "Gemini only"])
        self._order_combo.setStyleSheet(self._combo_ss())
        self._order_combo.setToolTip("Which provider the command router checks first for typed commands")
        cl.addWidget(self._order_combo)
        self._browser_combo = QComboBox()
        self._browser_combo.addItems(["Browser: ask me before opening", "Browser: only when I say so"])
        self._browser_combo.setStyleSheet(self._combo_ss())
        self._browser_combo.setToolTip("ULTRON never opens a browser as a fallback. This decides whether "
                                       "it may ask you on screen first.")
        cl.addWidget(self._browser_combo)
        self._load_routing_controls()
        self._order_combo.currentIndexChanged.connect(self._save_routing_controls)
        self._browser_combo.currentIndexChanged.connect(self._save_routing_controls)
        lay.addWidget(card)

        card, cl = self._card("Available Tools")
        row = QHBoxLayout()
        self._tools_count = QLabel("—")
        self._tools_count.setFont(QFont(FONT_HEAD, 22, QFont.Weight.Bold))
        self._tools_count.setStyleSheet(f"color: {C.PRI};")
        row.addWidget(self._tools_count)
        row.addStretch()
        _vb = self._action_btn("View MCP Tools", lambda: self._run_manual("mcp_tools"), h=28)
        _vb.setMinimumWidth(118)
        row.addWidget(_vb)
        cl.addLayout(row)
        self._tools_breakdown = QLabel("")
        self._tools_breakdown.setFont(QFont(FONT_UI, 7))
        self._tools_breakdown.setStyleSheet("color: #7f9bb3;")
        self._tools_breakdown.setWordWrap(True)
        self._tools_breakdown.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        cl.addWidget(self._tools_breakdown)
        lay.addWidget(card)

        card, cl = self._card("Connection Controls")
        for label, key, icon in (("Check Connections", "check", "search"),
                                 ("Reconnect Claude", "claude", "claude"),
                                 ("Reconnect Gemini", "gemini", "gemini"),
                                 ("Reconnect MCP", "mcp", "mcp"),
                                 ("View Connection Errors", "errors", "settings")):
            cl.addWidget(self._action_btn(label, lambda _c=False, k=key: self._run_manual(k), icon))
        lay.addWidget(card)

        card, cl = self._card("System Info")
        self._sys_labels: dict[str, QLabel] = {}
        for key, label in (("ui", "UI version"), ("backend", "Voice backend"), ("model", "Live model"),
                           ("tool", "Last tool"), ("plugins", "Plugins"), ("uptime", "App uptime")):
            h = QHBoxLayout()
            k = QLabel(label)
            k.setFont(QFont(FONT_UI, 8))
            k.setStyleSheet("color: #7f9bb3;")
            v = QLabel("—")
            v.setFont(QFont(FONT_UI, 8))
            v.setStyleSheet("color: #dbeafe;")
            v.setAlignment(Qt.AlignmentFlag.AlignRight)
            v.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            h.addWidget(k)
            h.addStretch()
            h.addWidget(v)
            cl.addLayout(h)
            self._sys_labels[key] = v
        self._sys_labels["ui"].setText("3.0 · Command Center")
        self._bar_cpu = MetricBar("CPU", C.PRI)
        self._bar_mem = MetricBar("MEM", C.ACC2)
        self._bar_net = MetricBar("NET", C.GREEN)
        self._bar_gpu = MetricBar("GPU", C.ACC)
        self._bar_tmp = MetricBar("TMP", "#ff6688")
        for bar in (self._bar_cpu, self._bar_mem, self._bar_net, self._bar_gpu, self._bar_tmp):
            cl.addWidget(bar)
        info = QHBoxLayout()
        self._uptime_lbl = QLabel("UP  --:--")
        self._proc_lbl = QLabel("PROC  --")
        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"OS  {os_name}")
        for lab, col in ((self._uptime_lbl, C.GREEN), (self._proc_lbl, C.TEXT_MED), (os_lbl, C.ACC2)):
            lab.setFont(QFont(FONT_MONO, 8))
            lab.setStyleSheet(f"color: {col};")
            info.addWidget(lab)
        cl.addLayout(info)
        lay.addWidget(card)

        card, cl = self._card("File Upload")
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        cl.addWidget(self._drop_zone)
        self._file_hint = QLabel("No file loaded — drop or click above to upload")
        self._file_hint.setFont(QFont(FONT_UI, 7))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_MED};")
        self._file_hint.setWordWrap(True)
        self._file_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        cl.addWidget(self._file_hint)
        lay.addWidget(card)
        lay.addStretch()

        scroll = QScrollArea()
        scroll.setFixedWidth(_RIGHT_W)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }"
                             "QScrollBar:vertical { width: 6px; background: transparent; }"
                             "QScrollBar::handle:vertical { background: #1a3954; border-radius: 3px; min-height: 24px; }"
                             "QScrollBar::add-line, QScrollBar::sub-line { height: 0; }")
        scroll.viewport().setStyleSheet("background: transparent;")
        scroll.setWidget(w)
        return scroll

    def _build_chat_dock(self) -> QWidget:
        f = QFrame()
        f.setObjectName("Card")
        f.setStyleSheet(self._CARD_SS)
        f.setMinimumHeight(200)
        lay = QVBoxLayout(f)
        lay.setContentsMargins(12, 9, 12, 10)
        lay.setSpacing(6)
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        ic = QLabel()
        self._themed_icon(ic, "core", C.PRI, 20, "pixmap")
        hdr.addWidget(ic)
        nm = QLabel(self._assistant_name.upper())
        nm.setFont(QFont(FONT_HEAD, 10, QFont.Weight.Bold))
        nm.setStyleSheet("color: #eaf6ff;")
        self._chat_name = nm
        hdr.addWidget(nm)
        self._chat_time = QLabel(time.strftime("Today %H:%M"))
        self._chat_time.setFont(QFont(FONT_UI, 8))
        self._chat_time.setStyleSheet("color: #5f7d96;")
        hdr.addWidget(self._chat_time)
        hdr.addSpacing(12)
        self._stage = StageStrip()
        hdr.addWidget(self._stage, 1)
        lay.addLayout(hdr)

        self._log = LogWidget()
        self._log.setStyleSheet(f"""
            QTextEdit {{
                background: rgba(4, 12, 21, 0.75); color: {C.TEXT};
                border: 1px solid #0f2236; border-radius: 9px; padding: 6px 8px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{ background: transparent; width: 6px; border: none; }}
            QScrollBar::handle:vertical {{ background: #1a3954; border-radius: 3px; min-height: 20px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        self._log.setFont(QFont(FONT_UI, 9))
        lay.addWidget(self._log, 1)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self._chat_chips: list = []
        for label, icon, fn in (("Check AI connections", "search", lambda: self._run_manual("check")),
                                ("Show MCP tools", "mcp", lambda: self._run_manual("mcp_tools")),
                                ("Explore the graph", "home", lambda: (self._show_home(graph=True),
                                                                       self.graph.reset_view())),
                                ("Open settings", "settings", lambda: self._drawer_btn.click())):
            b = QPushButton("  " + label)
            self._themed_icon(b, icon, "#7fdcf5", 14)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(26)
            b.setStyleSheet(self._chip_ss())
            b.setToolTip(label)
            b.clicked.connect(fn)
            chips.addWidget(b)
            self._chat_chips.append((b, "  " + label))
        chips.addStretch()
        lay.addLayout(chips)
        lay.addLayout(self._build_input_row())
        return f

    # ── Connections, graph, core state ───────────────────────────────────────

    def _load_routing_controls(self):
        try:
            from core.connections import routing_settings
            rs = routing_settings()
        except Exception:
            return
        order = rs.get("provider_order") or ["claude", "gemini"]
        if order == ["claude"]:
            idx = 2
        elif order == ["gemini"]:
            idx = 3
        elif order and order[0] == "gemini":
            idx = 1
        else:
            idx = 0
        for combo, i in ((self._order_combo, idx),
                         (self._browser_combo, 0 if rs.get("browser_policy") == "ask" else 1)):
            combo.blockSignals(True)
            combo.setCurrentIndex(i)
            combo.blockSignals(False)

    def _save_routing_controls(self, *_a):
        orders = {0: (["claude", "gemini"], True), 1: (["gemini", "claude"], True),
                  2: (["claude"], False), 3: (["gemini"], False)}
        order, fallback = orders.get(self._order_combo.currentIndex(), (["claude", "gemini"], True))
        policy = "ask" if self._browser_combo.currentIndex() == 0 else "explicit_only"
        try:
            from core.connections import save_routing_settings
            save_routing_settings(provider_order=order, allow_provider_fallback=fallback,
                                  browser_policy=policy)
            self._log.append_log(f"SYS: Routing saved — {' → '.join(o.title() for o in order)}; "
                                 f"browser {'asks first' if policy == 'ask' else 'only on explicit request'}.")
        except Exception as e:
            self._log.append_log(f"ERR: Could not save routing settings — {e}")
        if self.connections is not None:
            self._apply_connections(self.connections.snapshot())

    def _initial_connections(self):
        """Show the cached (unverified) picture until the backend checks run."""
        if self.connections is None:
            try:
                from core.connections import manager
                self._apply_connections(manager().snapshot())
            except Exception as e:
                print(f"[UI] connections: {e}")
        self._graph_dirty = True
        self._refresh_graph_if_dirty()

    def _apply_connections(self, snap: dict):
        if not isinstance(snap, dict):
            return
        self._conn_snapshot = snap
        c, g = snap.get("claude") or {}, snap.get("gemini") or {}
        live = snap.get("gemini_live") or {}
        ms = snap.get("mcp_summary") or {}
        csub = " · ".join(x for x in (c.get("model") or "", c.get("integration") or "") if x)
        ctip = (c.get("detail") or "") + (f"\nLast error: {c['last_error']}" if c.get("last_error") else "")
        gsub = ("Live connected" if live.get("connected") else "Live not connected") + (
            f" · {(live.get('model') or g.get('model') or '').removeprefix('models/')}"
            if (live.get("model") or g.get("model")) else "")
        gtip = (g.get("detail") or "") + (f"\nLast error: {g['last_error']}" if g.get("last_error") else "") + (
            f"\nLive: {live['error']}" if live.get("error") else "")
        if not ms.get("configured"):
            mstate = "NOT CONFIGURED"
        elif ms.get("connected") == ms.get("configured"):
            mstate = "CONNECTED"
        elif ms.get("connected"):
            mstate = "READY"
        else:
            mstate = "DISCONNECTED"
        msub = f"{ms.get('connected', 0)}/{ms.get('configured', 0)} servers · {ms.get('tools', 0)} tools"
        mtip = "\n".join(f"{m['label']}: {m['state']}" + (f" — {m['last_error']}" if m.get("last_error") else "")
                         for m in snap.get("mcp") or [])
        for rows in (getattr(self, "_left_rows", {}), getattr(self, "_right_rows", {})):
            if rows:
                rows["claude"].set_status(c.get("state", "CHECKING"), csub, ctip)
                rows["gemini"].set_status(g.get("state", "CHECKING"), gsub, gtip)
                rows["mcp"].set_status(mstate, msub, mtip)
        if hasattr(self, "_active_lbl"):
            act = snap.get("active_provider") or ""
            name = {"claude": "Claude", "gemini": "Gemini"}.get(act, "None available")
            detail = c.get("model") if act == "claude" else (
                (live.get("model") or g.get("model") or "").removeprefix("models/") if act == "gemini" else "")
            self._active_lbl.setText(f"{name}" + (f"  ·  {detail}" if detail else ""))
            self._active_lbl.setStyleSheet(f"color: {'#eaf6ff' if act else '#ef4444'};")
        if hasattr(self, "_tools_count"):
            native = len(self.get_tool_catalog()) if self.get_tool_catalog else 0
            mcp_tools = ms.get("tools", 0)
            self._tools_count.setText(str(native + mcp_tools) if (native or mcp_tools) else "—")
            self._tools_breakdown.setText(f"{native} native (tools + plugins) · {mcp_tools} MCP via Claude"
                                          if native else f"{mcp_tools} MCP via Claude · native tools load with the backend")
        errs = sum(1 for k in ("claude", "gemini") if (snap.get(k) or {}).get("last_error")) + sum(
            1 for m in snap.get("mcp") or [] if m.get("last_error"))
        if hasattr(self, "_bell_btn"):
            self._bell_btn.setText(f"🔔 {errs}" if errs else "🔔")
        if hasattr(self, "_stage"):
            self._stage.update_task(snap.get("task") or {})
        self._graph_dirty = True
        self._update_core_state()

    def _update_core_state(self):
        if not hasattr(self, "core"):
            return
        snap = self._conn_snapshot or {}
        live = bool((snap.get("gemini_live") or {}).get("connected"))
        task = snap.get("task") or {}
        hs = (self._hud_state or "").upper()
        c, g = snap.get("claude") or {}, snap.get("gemini") or {}
        verified = [n for n, s in (("Claude", c), ("Gemini", g)) if s.get("state") == "CONNECTED"]
        usable = live or c.get("usable") or g.get("usable")
        processing = (task.get("state") == "PROCESSING" and time.time() - float(task.get("updated") or 0) < 120)
        if processing:
            st = "PROCESSING"
        elif hs == "SPEAKING":
            st = "RESPONDING"
        elif hs == "THINKING" and live:
            st = "THINKING"
        elif self._muted:
            st = "MUTED"
        elif hs == "LISTENING" and live:
            st = "LISTENING"
        elif hs == "SLEEPING" and live:
            st = "SLEEPING"
        elif snap and c.get("state") == "ERROR" and g.get("state") == "ERROR" and not live:
            st = "ERROR"
        elif snap and not usable and c.get("state") not in ("CHECKING",) and g.get("state") not in ("CHECKING",):
            st = "DISCONNECTED"
        else:
            st = "IDLE"
        parts = []
        parts.append("Voice: Live connected" if live else "Voice: not connected")
        if verified:
            parts.append("CONNECTED · " + " + ".join(verified))
        elif usable:
            parts.append("Providers ready (unverified)")
        elif snap:
            parts.append("No AI provider connected")
        self.core.set_state(st)
        col = {"PROCESSING": "#f5b942", "RESPONDING": "#38e1ff", "THINKING": "#a78bfa",
               "LISTENING": "#2ee6a6", "ERROR": "#ef4444", "DISCONNECTED": "#ef4444",
               "MUTED": "#94a3b8", "SLEEPING": "#5b8bb0"}.get(st, "#22c55e")
        if self.core.accent and st in self.core.THEMED:
            col = self.core.state_color(st)
        self._core_badge.setText(f"● {st}")
        self._core_badge.setStyleSheet(f"color: {col};")
        tail = ""
        if processing and task.get("stage"):
            tail = f"\n{task.get('stage')}"
        self._core_sub.setText("  ·  ".join(parts) + tail)

    def _refresh_graph_if_dirty(self):
        if not self._graph_dirty:
            return
        self._graph_dirty = False
        try:
            decl = self.get_tool_catalog() if self.get_tool_catalog else []
        except Exception:
            decl = []
        try:
            plugins = self.get_plugins() if self.get_plugins else []
        except Exception:
            plugins = []
        try:
            from memory.memory_manager import load_memory
            memory = load_memory()
        except Exception:
            memory = None
        try:
            from actions.background_monitor import list_monitors
            monitors = list_monitors()
        except Exception:
            monitors = []
        current = ""
        try:
            current = self._drop_zone.current_file() or ""
        except Exception:
            pass
        nodes, edges = build_graph(self._conn_snapshot or {}, decl, plugins, memory, monitors,
                                   current, self._assistant_name)
        self.graph.set_graph(nodes, edges)
        self.graph_panel.update_legend()
        self._update_modules()
        idx = {}
        for n in nodes.values():
            key = f"{n.label}  ·  {CATS.get(n.cat, ('', ''))[0]}"
            idx.setdefault(key, n.id)
        self._search_index = idx
        self._search_model.setStringList(sorted(idx))
        if self.graph.selected:
            self._on_graph_select(self.graph.selected, focus=False)

    def _update_modules(self):
        if not hasattr(self, "_modules_box"):
            return
        while self._modules_box.count():
            it = self._modules_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        counts = self.graph.counts()
        nodes = self.graph.nodes
        rows = [("provider", "claude", "AI Providers", sum(1 for n in nodes.values() if n.hub and n.cat == "provider")),
                ("mcp", "mcp", "MCP Servers", sum(1 for n in nodes.values() if n.cat == "mcp" and not n.hub)),
                ("mcp_tool", "mcp", "MCP Tools", counts.get("mcp_tool", 0)),
                ("tool", "tools", "Native Tools", counts.get("tool", 0)),
                ("agent", "agents", "Agents", counts.get("agent", 0)),
                ("plugin", "plugins", "Plugins", counts.get("plugin", 0)),
                ("memory", "memory", "Memory System", sum(1 for n in nodes.values() if n.id.startswith("mem:"))),
                ("automation", "automation", "Automation", counts.get("automation", 0)),
                ("files", "files", "File Manager", counts.get("files", 0))]
        for cat, hub, label, cnt in rows:
            if hub not in nodes:
                continue
            b = QPushButton()
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(22)
            b.setStyleSheet("QPushButton { background: transparent; border: none; text-align: left; }"
                            "QPushButton:hover { background: #0b1c2e; border-radius: 5px; }")
            h = QHBoxLayout(b)
            h.setContentsMargins(4, 0, 4, 0)
            l1 = QLabel(f"<span style='color:{CATS[cat][1]}'>●</span>&nbsp;&nbsp;{label}")
            l1.setFont(QFont(FONT_UI, 8))
            l1.setStyleSheet("color: #c6d9e8;")
            l2 = QLabel(str(cnt))
            l2.setFont(QFont(FONT_UI, 8))
            l2.setStyleSheet("color: #e6f4ff;")
            for lab in (l1, l2):
                lab.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            l1.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            h.addWidget(l1, 1)
            h.addWidget(l2)
            b.clicked.connect(lambda _c=False, nid=hub: (self._show_home(graph=True), self.graph.focus_node(nid)))
            self._modules_box.addWidget(b)

    def _set_leaf(self, cats: tuple, on: bool):
        for c_ in cats:
            self.graph.show_leaf[c_] = on
        self.graph.update()

    def _show_home(self, graph: bool = True):
        if self._hud_cam_stack.currentIndex() == 3:
            self._toggle_orbit_view(False)
        if self._hud_cam_stack.currentIndex() == 0:
            self._home_stack.setCurrentIndex(0 if graph else 1)
        else:
            # camera / video keep the centre until closed; remember the choice
            self._home_stack.setCurrentIndex(0 if graph else 1)
        for k in ("home", "avatar", "orbit"):
            self._nav_btns[k].setChecked((k == "home") == graph if k != "orbit" else False)

    def _nav(self, key: str):
        if key == "home":
            self._show_home(graph=True)
        elif key == "avatar":
            self._show_home(graph=False)
        elif key == "orbit":
            self._toggle_orbit_view(True)
            for k in ("home", "avatar"):
                self._nav_btns[k].setChecked(False)
            self._nav_btns["orbit"].setChecked(self._hud_cam_stack.currentIndex() == 3)
        elif key == "chat":
            sizes = self._center_split.sizes()
            if sizes and sizes[-1] < 200:
                sizes[-1] = 260
                self._center_split.setSizes(sizes)
            self._input.setFocus()
        elif key == "tools":
            self._show_home(graph=True)
            self.graph.focus_node("tools" if "tools" in self.graph.nodes else "core")
        elif key == "memory":
            self._open_memory_panel()
        elif key == "plugins":
            self._open_plugin_manager()
        elif key == "workspace":
            self._open_workspace()
        elif key == "settings":
            self._drawer_btn.click()

    def _on_graph_select(self, nid: str, focus: bool = True):
        n = self.graph.nodes.get(nid) if nid else None
        if n is None:
            self._inspector.show_node(None, [], [])
            return
        neigh = self.graph.neighbours(nid)
        acts = [("focus", "Focus")]
        if nid in ("claude", "claude.cli"):
            acts = [("check_claude", "Check now"), ("focus", "Focus")]
        elif nid.startswith("gemini"):
            acts = [("check_gemini", "Check now"), ("reconnect_gemini", "Reconnect")]
        elif nid == "mcp" or nid.startswith("mcp:"):
            acts = [("check_mcp", "Recheck MCP"), ("mcp_tools", "List tools")]
        elif nid.startswith("mem") or nid == "memory":
            acts = [("memory", "Open memory"), ("focus", "Focus")]
        elif nid.startswith("plugin") or nid == "plugins":
            acts = [("plugins", "Plugin manager"), ("plugin_settings", "Settings")]
        elif nid.startswith("tool:") or nid in ("tools", "agents"):
            acts = [("describe", "Describe"), ("focus", "Focus")]
        elif nid.startswith("file") or nid == "files":
            acts = [("attach", "Upload file")]
        elif nid == "automation" or nid.startswith("mon:"):
            acts = [("describe", "Describe")]
        self._inspector.show_node(n, neigh, acts)

    def _inspector_action(self, act: str, nid: str):
        n = self.graph.nodes.get(nid)
        if act == "focus":
            self.graph.focus_node(nid)
        elif act == "check_claude":
            self._run_manual("claude")
        elif act in ("check_gemini", "reconnect_gemini"):
            self._run_manual("gemini")
        elif act == "check_mcp":
            self._run_manual("mcp")
        elif act == "mcp_tools":
            self._run_manual("mcp_tools")
        elif act == "memory":
            self._open_memory_panel()
        elif act == "plugins":
            self._open_plugin_manager()
        elif act == "plugin_settings":
            self._open_plugin_settings()
        elif act == "attach":
            self._drop_zone._browse()
        elif act == "describe" and n is not None:
            self._log.append_log(f"SYS: {n.label} — {n.desc or n.sub or 'no description'}")

    def _conn_row_clicked(self, key: str):
        self._show_home(graph=True)
        self.graph.focus_node(key if key in self.graph.nodes else "core")
        snap = self._conn_snapshot or {}
        if key == "mcp":
            lines = [f"{m['label']}: {m['state']} ({len(m.get('tools') or [])} tools)"
                     + (f" — {m['last_error']}" if m.get("last_error") else "") for m in snap.get("mcp") or []]
            self._log.append_log("SYS: MCP — " + ("; ".join(lines) if lines else "no servers checked yet"))
        else:
            s = snap.get(key) or {}
            self._log.append_log(f"SYS: {s.get('label', key.title())} — {s.get('state', '?')}"
                                 + (f" · {s.get('integration')}" if s.get("integration") else "")
                                 + (f" · {s.get('detail')}" if s.get("detail") else "")
                                 + (f" · last error: {s.get('last_error')}" if s.get("last_error") else ""))

    _MANUAL_LABELS = {"check": "Checking all AI connections", "claude": "Reconnecting Claude",
                      "gemini": "Reconnecting Gemini", "mcp": "Rechecking MCP servers",
                      "mcp_tools": "Listing MCP tools", "errors": "Collecting connection errors"}

    def _run_manual(self, key: str):
        """Connection buttons — real checks, off the Qt thread."""
        self._log.append_log(f"SYS: {self._MANUAL_LABELS.get(key, key)}…")

        def work():
            try:
                router = self.router
                if router is None:
                    from core import command_router, connections
                    router = command_router.CommandRouter(
                        lambda n, a: "The assistant backend is not running yet.",
                        lambda: (self.get_tool_catalog() if self.get_tool_catalog else []),
                        lambda t: False, manager=connections.manager())
                self._route_done_sig.emit(router.manual(key))
            except Exception as e:
                self._route_done_sig.emit(e)
        threading.Thread(target=work, daemon=True, name=f"manual-{key}").start()

    def _show_route_result(self, res):
        if isinstance(res, Exception):
            self._log.append_log(f"ERR: {res}")
            return
        text = res.chat_text() or ("Done." if res.ok else "Failed.")
        self._log.append_log(f"{self._assistant_name.upper()}: {text}" if res.ok else f"ERR: {text}")
        if self.connections is not None:
            self._apply_connections(self.connections.snapshot())

    def _build_quick_drawer(self) -> QWidget:
        """Floating overlay panel shown when the ⚙ header button is toggled."""
        _BTN_STYLE_PRI = f"""
            QPushButton {{
                background: #00091a; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """
        _BTN_STYLE_DIM = f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}
        """

        self._BTN_PRI, self._BTN_DIM = _BTN_STYLE_PRI, _BTN_STYLE_DIM
        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(f"""
            QWidget#QuickDrawer {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-top: none;
                border-radius: 0 0 6px 6px;
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QLabel("◈ SETUP")
        hdr.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        remote_btn = QPushButton("◉  REMOTE CONTROL")
        remote_btn.setFixedHeight(30)
        remote_btn.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        remote_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remote_btn.setStyleSheet(_BTN_STYLE_PRI)
        remote_btn.clicked.connect(self._open_remote)
        lay.addWidget(remote_btn)

        sc_btn = QPushButton("⊞  CREATE DESKTOP SHORTCUT")
        sc_btn.setFixedHeight(26)
        sc_btn.setFont(QFont("Consolas", 7))
        sc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sc_btn.setStyleSheet(_BTN_STYLE_DIM)
        sc_btn.clicked.connect(self._create_desktop_shortcut)
        lay.addWidget(sc_btn)

        self._autostart_btn = QPushButton("◉  AUTO-START: OFF")
        self._autostart_btn.setFixedHeight(26)
        self._autostart_btn.setFont(QFont("Consolas", 7))
        self._autostart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._autostart_btn.clicked.connect(self._toggle_autostart)
        lay.addWidget(self._autostart_btn)

        cust_btn = QPushButton("⚙  CUSTOMISE ASSISTANT")
        cust_btn.setFixedHeight(26)
        cust_btn.setFont(QFont("Consolas", 7))
        cust_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cust_btn.setStyleSheet(_BTN_STYLE_DIM)
        cust_btn.clicked.connect(self._open_customize)
        lay.addWidget(cust_btn)

        audio_btn = QPushButton("🎧  AUDIO DEVICES")
        audio_btn.setFixedHeight(26)
        audio_btn.setFont(QFont("Consolas", 7))
        audio_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        audio_btn.setStyleSheet(_BTN_STYLE_DIM)
        audio_btn.clicked.connect(self._open_audio_devices)
        lay.addWidget(audio_btn)

        anim_btn = QPushButton("✦  ANIMATIONS")
        anim_btn.setFixedHeight(26)
        anim_btn.setFont(QFont("Consolas", 7))
        anim_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        anim_btn.setStyleSheet(_BTN_STYLE_DIM)
        anim_btn.setToolTip("Startup / shutdown cinematics, quality, sound, reduced motion")
        anim_btn.clicked.connect(self._open_animation_settings)
        lay.addWidget(anim_btn)

        mem_btn = QPushButton("🧠  MEMORY")
        mem_btn.setFixedHeight(26)
        mem_btn.setFont(QFont("Consolas", 7))
        mem_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mem_btn.setStyleSheet(_BTN_STYLE_DIM)
        mem_btn.clicked.connect(self._open_memory_panel)
        lay.addWidget(mem_btn)

        plugin_btn = QPushButton("🧩  PLUGINS")
        plugin_btn.setFixedHeight(26)
        plugin_btn.setFont(QFont("Consolas", 7))
        plugin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        plugin_btn.setStyleSheet(_BTN_STYLE_DIM)
        plugin_btn.clicked.connect(self._open_plugin_manager)
        lay.addWidget(plugin_btn)

        settings_btn = QPushButton("⚙  PLUGIN SETTINGS")
        settings_btn.setFixedHeight(26)
        settings_btn.setFont(QFont("Consolas", 7))
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.setStyleSheet(_BTN_STYLE_DIM)
        settings_btn.clicked.connect(self._open_plugin_settings)
        lay.addWidget(settings_btn)

        w.adjustSize()
        return w

    def _build_controls_drawer(self) -> QWidget:
        """The switches that get flicked every day, in their own panel."""
        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(self._quick_drawer.styleSheet())
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QLabel("◈ CONTROLS")
        hdr.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        def _row(button, style=None, height=26):
            button.setFixedHeight(height)
            button.setFont(QFont("Consolas", 7))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if style:
                button.setStyleSheet(style)
            lay.addWidget(button)
            return button

        fs_btn = _row(QPushButton("⛶  FULLSCREEN  [F11]"), self._BTN_DIM)
        fs_btn.clicked.connect(self._toggle_fullscreen)

        self._brief_btn = _row(QPushButton())
        self._brief_btn.clicked.connect(self._toggle_brief)

        self._wake_btn = _row(QPushButton())
        self._wake_btn.clicked.connect(self._toggle_wake_word)
        self._wake_sleep_btn = _row(QPushButton())
        self._wake_sleep_btn.clicked.connect(self._tap_wake_manual)
        # Neutral until the real state lands from the background warm-up.
        self._wake_btn.setText("🎙  WAKE WORD")
        self._wake_btn.setStyleSheet(self._BTN_DIM)
        self._wake_sleep_btn.hide()

        self._clap_btn = _row(QPushButton())
        self._clap_btn.clicked.connect(self._toggle_clap)
        self._refresh_clap_btn()

        self._ptt_btn = _row(QPushButton())
        self._ptt_btn.clicked.connect(self._toggle_ptt)
        self._refresh_talk_btns()

        self._hud_btn = _row(QPushButton())
        self._hud_btn.clicked.connect(self._toggle_hud_style)
        self._refresh_hud_btn()
        self._ctrl_screen_share_btn = _row(QPushButton("🖥  SCREEN SHARE"), self._BTN_DIM)
        self._ctrl_screen_share_btn.clicked.connect(self._toggle_screen_share)

        w.adjustSize()
        return w

    def _warm_wake_state(self) -> None:
        """Work out the wake-word state off the UI thread, once.

        It used to be resolved when the drawer opened, and that made opening the
        drawer take two seconds the first time — measured at 2.10s, all of it
        `import openwakeword` dragging in onnxruntime behind it. The button
        count had nothing to do with it; the Qt thread was simply waiting on an
        import. Afterwards the same check costs about a millisecond, so it only
        ever needed to happen somewhere other than in front of the user.
        """
        def work():
            try:
                self._wake_state()
            except Exception:
                pass
            self._wake_btns_sig.emit()
        threading.Thread(target=work, daemon=True, name="wake-state-warm").start()

    def _toggle_drawer(self, checked: bool):
        if checked:
            self._close_controls()
            self._position_quick_drawer()
            self._quick_drawer.show()
            self._quick_drawer.raise_()
        else:
            self._quick_drawer.hide()

    def _toggle_controls(self, checked: bool):
        if checked:
            self._close_setup()
            self._refresh_wake_btns()   # cheap now: the state was warmed at boot
            self._refresh_clap_btn()
            self._position_ctrl_drawer()
            self._ctrl_drawer.show()
            self._ctrl_drawer.raise_()
        else:
            self._ctrl_drawer.hide()

    def _close_setup(self):
        if hasattr(self, "_quick_drawer"):
            self._quick_drawer.hide()
        if hasattr(self, "_drawer_btn"):
            self._drawer_btn.setChecked(False)

    def _close_controls(self):
        if hasattr(self, "_ctrl_drawer"):
            self._ctrl_drawer.hide()
        if hasattr(self, "_ctrl_btn"):
            self._ctrl_btn.setChecked(False)

    def _place_drawer(self, drawer, left: int):
        _W = 220
        drawer.setFixedWidth(_W)
        drawer.adjustSize()
        drawer.setGeometry(left, 54, _W, drawer.sizeHint().height())

    def _position_quick_drawer(self):
        if hasattr(self, "_quick_drawer"):
            self._place_drawer(self._quick_drawer, 12)

    def _position_ctrl_drawer(self):
        # Under its own header button rather than under the other one's.
        if hasattr(self, "_ctrl_drawer"):
            left = 12
            try:
                left = max(12, self._ctrl_btn.mapTo(self.centralWidget(),
                                                    QPoint(0, 0)).x() - 8)
            except Exception:
                pass
            self._place_drawer(self._ctrl_drawer, left)

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        wrap = QFrame()
        wrap.setObjectName("InputWrap")
        wrap.setStyleSheet(f"QFrame#InputWrap {{ background: rgba(5,16,28,0.95); border: 1px solid #1a3954; "
                           f"border-radius: 11px; }}")
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(6, 4, 6, 4)
        wl.setSpacing(6)
        ib = (f"QPushButton {{ background: transparent; color: #9fd8f0; border: 1px solid transparent; "
              f"border-radius: 8px; font-size: 12pt; }} QPushButton:hover {{ border-color: {C.PRI_DIM}; }}")
        attach = QPushButton("📎")
        attach.setFixedSize(34, 34)
        attach.setToolTip("Attach a file")
        attach.setCursor(Qt.CursorShape.PointingHandCursor)
        attach.setStyleSheet(ib)
        attach.clicked.connect(lambda: self._drop_zone._browse())
        wl.addWidget(attach)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type your message…   (try: check AI connections · show MCP tools)")
        self._input.setFont(QFont(FONT_UI, 10))
        self._input.setFixedHeight(34)
        self._input.setStyleSheet(f"QLineEdit {{ background: transparent; color: {C.WHITE}; border: none; "
                                  f"padding: 2px 4px; }}")
        self._input.returnPressed.connect(self._send)
        wl.addWidget(self._input, 1)
        self._mute_btn = QPushButton()
        self._mute_btn.setFixedSize(34, 34)
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        wl.addWidget(self._mute_btn)
        self._interrupt_btn = QPushButton("✋")
        self._interrupt_btn.setFixedSize(34, 34)
        self._interrupt_btn.setToolTip("Interrupt ULTRON  [Esc]")
        self._interrupt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._interrupt_btn.setStyleSheet(f"QPushButton {{ background: #14040c; color: {C.MUTED_C}; "
                                          f"border: 1px solid #5a1424; border-radius: 8px; font-size: 11pt; }}"
                                          f"QPushButton:hover {{ border-color: #ff6688; }}")
        self._interrupt_btn.clicked.connect(self._do_interrupt)
        wl.addWidget(self._interrupt_btn)
        send = QPushButton("➤")
        send.setFixedSize(38, 34)
        send.setToolTip("Send")
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0b6f8c, "
                           f"stop:1 #0a3d5c); color: #ffffff; border: 1px solid {C.PRI}; border-radius: 9px; "
                           f"font-size: 12pt; }} QPushButton:hover {{ background: #0e86a8; }}")
        send.clicked.connect(self._send)
        wl.addWidget(send)
        row.addWidget(wrap)
        return row

    def _build_content_panel(self) -> QWidget:
        """
        Collapsible panel below the HUD — shows search results, news, briefings.
        Hidden by default; appears when show_content() is called.
        """
        w = QWidget()
        w.setObjectName("ContentPanel")
        w.setStyleSheet(f"""
            QWidget#ContentPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(5)

        # ── header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(6)

        dot = QLabel("◈")
        dot.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._content_title_lbl = QLabel("BRIEFING")
        self._content_title_lbl.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        self._content_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;"
        )
        hdr.addWidget(self._content_title_lbl)
        hdr.addStretch()

        self._content_ts_lbl = QLabel("")
        self._content_ts_lbl.setFont(QFont("Consolas", 7))
        self._content_ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._content_ts_lbl)

        dismiss = QPushButton("DISMISS  ✕")
        dismiss.setFont(QFont("Consolas", 7))
        dismiss.setFixedHeight(18)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        dismiss.clicked.connect(w.hide)
        hdr.addWidget(dismiss)
        lay.addLayout(hdr)

        # ── separator ─────────────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); lay.addWidget(sep)

        # ── text display ──────────────────────────────────────────────────────
        self._content_display = QTextEdit()
        self._content_display.setReadOnly(True)
        self._content_display.setFont(QFont("Consolas", 8))
        self._content_display.setMinimumHeight(60)
        self._content_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._content_display.setStyleSheet(f"""
            QTextEdit {{
                background: {C.DARK};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px 8px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 3px; min-height: 16px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none;
            }}
        """)
        lay.addWidget(self._content_display)

        return w

    def _show_content(self, title: str, text: str):
        """Slot — runs on Qt main thread. Updates and shows the content panel."""
        import time as _time
        # The panel opens below the head, so the head looks down at it. It is a
        # tiny thing that answers "did that land?" before you read a word.
        self.hud.glance(0.0, -0.85, hold=1.3)
        self._content_title_lbl.setText(title.upper()[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setPlainText(text)
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start
        )
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 220, 120), 220])

    # ── document review ──────────────────────────────────────────────────────
    # Rendered as rich text into the content panel that already exists, rather
    # than into a panel of its own. A review is read, not clicked, so QTextEdit
    # gives scrolling, selection and copy for nothing, and the HUD gains no
    # widget it has to lay out. Severity decides colour and order here because
    # that is presentation; the plugin supplies no styling and knows no palette,
    # which is also what lets a re-theme repaint a review correctly.

    # Severity is marked by a symbol and a colour, not by a word. The findings
    # themselves are in the user's language, and "[SERIOUS]" sitting inside a
    # Turkish sentence is the kind of seam this project tries not to have —
    # while translating the tag would mean a table per language, which is worse.
    # A shape carries it in every language, and shape plus colour still reads
    # for someone who cannot separate red from amber. What the marks mean
    # arrives the way everything else does: ULTRON says it out loud.
    _REVIEW_MARKS = {"serious": ("RED", "▲"), "caution": ("ACC2", "●"), "note": ("PRI_DIM", "·")}

    @staticmethod
    def _esc(s) -> str:
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br>"))

    def _show_review(self, title: str, summary: str, findings, unclear):
        """Slot — Qt main thread. Lays a document review into the content panel."""
        e = self._esc
        parts = [f'<div style="color:{C.TEXT}; font-family:Courier New;">']

        if summary:
            parts.append(
                f'<div style="color:{C.WHITE}; border-left:2px solid {C.PRI};'
                f' padding-left:8px; margin-bottom:10px;">{e(summary)}</div>')

        for f in (findings or []):
            key, mark = self._REVIEW_MARKS.get(f.get("severity"), ("PRI_DIM", "·"))
            colour = getattr(C, key)
            parts.append(f'<div style="margin-bottom:11px;">')
            parts.append(
                f'<span style="color:{colour}; font-weight:bold;">{mark}</span> '
                f'<span style="color:{C.WHITE}; font-weight:bold;">'
                f'{e(f.get("heading"))}</span>')
            if f.get("detail"):
                parts.append(f'<div style="margin-left:12px;">{e(f["detail"])}</div>')
            if f.get("quote"):
                # The document's own wording, visually separated from the
                # explanation so the two are never mistaken for each other.
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_DIM};'
                    f' border-left:1px solid {C.BORDER}; padding-left:7px;">'
                    f'&ldquo;{e(f["quote"])}&rdquo;</div>')
            if f.get("suggestion"):
                parts.append(
                    f'<div style="margin-left:12px; color:{C.PRI};">'
                    f'&rarr; {e(f["suggestion"])}</div>')
            parts.append('</div>')

        if unclear:
            parts.append(
                f'<div style="margin-top:6px; border-top:1px solid {C.BORDER};'
                f' padding-top:7px; color:{C.TEXT_MED};">'
                'The document does not settle:</div>')
            for u in unclear:
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_MED};">'
                    f'&middot; {e(u)}</div>')
        parts.append('</div>')

        import time as _time
        self.hud.glance(0.0, -0.85, hold=1.3)
        # Left as written, not upper-cased. The other content-panel titles are
        # the app's own English labels, but this one is the document's name in
        # the user's language, and str.upper() applies English casing rules to
        # it: Turkish "Sözleşmesi" comes back "SÖZLEŞMESI", having lost the
        # dotted capital İ. Python has no locale-aware upper to reach for, and
        # imposing one language's rules on all of them is the bug, not the fix.
        self._content_title_lbl.setText((title or "Document")[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setHtml("".join(parts))
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start)
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 260, 120), 260, 0])

    # ── quiz panel ───────────────────────────────────────────────────────────
    # An interactive twin of the content panel. The plugin only ever hands over
    # questions; everything about asking, marking and reporting happens here,
    # and the finished result is pushed back into the conversation the same way
    # a dropped file is — as a message ULTRON reads and responds to. That keeps
    # the tool call short (it returns the moment the board is up) and leaves the
    # talking to the assistant, in the user's own language.

    def _quiz_btn(self, text: str, primary: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setFont(QFont("Consolas", 8))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setMinimumHeight(24)
        edge = C.BORDER_B if primary else C.BORDER
        col = C.PRI if primary else C.TEXT_MED
        b.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL2}; color: {col};
                border: 1px solid {edge}; border-radius: 2px;
                padding: 3px 9px; text-align: left;
            }}
            QPushButton:hover {{ color: {C.WHITE}; border-color: {C.PRI_DIM}; }}
            QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
        """)
        return b

    def _build_quiz_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("QuizPanel")
        w.setStyleSheet(f"""
            QWidget#QuizPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(6)

        hdr = QHBoxLayout(); hdr.setSpacing(6)
        dot = QLabel("◈")
        dot.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._quiz_title_lbl = QLabel("QUIZ")
        self._quiz_title_lbl.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        self._quiz_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        hdr.addWidget(self._quiz_title_lbl)
        hdr.addStretch()

        self._quiz_count_lbl = QLabel("")
        self._quiz_count_lbl.setFont(QFont("Consolas", 7))
        self._quiz_count_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._quiz_count_lbl)

        quit_btn = QPushButton("DISMISS  ✕")
        quit_btn.setFont(QFont("Consolas", 7))
        quit_btn.setFixedHeight(18)
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        quit_btn.clicked.connect(self._hide_quiz)
        hdr.addWidget(quit_btn)
        lay.addLayout(hdr)

        rule = QFrame(); rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {C.BORDER};")
        lay.addWidget(rule)

        self._quiz_q_lbl = QLabel("")
        self._quiz_q_lbl.setWordWrap(True)
        self._quiz_q_lbl.setFont(QFont("Consolas", 9))
        self._quiz_q_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._quiz_q_lbl)

        self._quiz_answers = QWidget()
        self._quiz_answers.setStyleSheet("background: transparent;")
        self._quiz_answers_lay = QVBoxLayout(self._quiz_answers)
        self._quiz_answers_lay.setContentsMargins(0, 2, 0, 0)
        self._quiz_answers_lay.setSpacing(4)
        lay.addWidget(self._quiz_answers)

        self._quiz_note_lbl = QLabel("")
        self._quiz_note_lbl.setWordWrap(True)
        self._quiz_note_lbl.setFont(QFont("Consolas", 8))
        self._quiz_note_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._quiz_note_lbl.hide()
        lay.addWidget(self._quiz_note_lbl)

        foot = QHBoxLayout()
        foot.addStretch()
        self._quiz_next_btn = self._quiz_btn("NEXT  →", primary=True)
        self._quiz_next_btn.setFixedWidth(110)
        self._quiz_next_btn.clicked.connect(self._quiz_next)
        self._quiz_next_btn.hide()
        foot.addWidget(self._quiz_next_btn)
        lay.addLayout(foot)

        self._quiz = None
        return w

    def _show_quiz(self, topic: str, questions, grader=None):
        """Slot — Qt main thread. Puts a fresh quiz on the board."""
        if not questions:
            return
        self._quiz = {
            "topic": topic or "",
            "questions": list(questions),
            "grader": grader,
            "i": 0,
            "results": [],
            "answered": False,
        }
        self._quiz_title_lbl.setText((topic or "quiz").upper()[:48])
        self.hud.glance(0.0, -0.85, hold=1.3)
        first_show = not self._quiz_panel.isVisible()
        self._quiz_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 250, 120), 0, 250])
        self._quiz_render()

    def _hide_quiz(self):
        self._quiz = None
        self._quiz_panel.hide()

    def _quiz_clear_answers(self):
        while self._quiz_answers_lay.count():
            item = self._quiz_answers_lay.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)
                child.deleteLater()

    def _quiz_render(self):
        q = self._quiz["questions"][self._quiz["i"]]
        n, total = self._quiz["i"] + 1, len(self._quiz["questions"])
        self._quiz_count_lbl.setText(f"{n} / {total}")
        self._quiz_q_lbl.setText(q.get("question", ""))
        self._quiz_note_lbl.hide()
        self._quiz_next_btn.hide()
        self._quiz["answered"] = False
        self._quiz_clear_answers()

        opts = q.get("options") or []
        if opts:
            for text in opts:
                b = self._quiz_btn("   " + text)
                b.clicked.connect(lambda _=False, t=text: self._quiz_submit(t))
                self._quiz_answers_lay.addWidget(b)
        else:
            row = QWidget(); row.setStyleSheet("background: transparent;")
            h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
            field = QLineEdit()
            field.setFont(QFont("Consolas", 9))
            field.setPlaceholderText("your answer")
            field.setStyleSheet(f"""
                QLineEdit {{
                    background: {C.PANEL2}; color: {C.WHITE};
                    border: 1px solid {C.BORDER}; border-radius: 2px; padding: 4px 7px;
                }}
                QLineEdit:focus {{ border-color: {C.PRI_DIM}; }}
            """)
            send = self._quiz_btn("ANSWER", primary=True)
            send.setFixedWidth(90)
            field.returnPressed.connect(lambda: self._quiz_submit(field.text()))
            send.clicked.connect(lambda: self._quiz_submit(field.text()))
            h.addWidget(field, stretch=1)
            h.addWidget(send)
            self._quiz_answers_lay.addWidget(row)
            field.setFocus()

    def _quiz_submit(self, given: str):
        if self._quiz is None or self._quiz["answered"]:
            return
        self._quiz["answered"] = True
        q = self._quiz["questions"][self._quiz["i"]]
        grader = self._quiz.get("grader")
        verdict = None
        if callable(grader):
            try:
                verdict = grader(q, given)
            except Exception:
                verdict = None
        self._quiz["results"].append({
            "question": q.get("question", ""),
            "type": q.get("type", ""),
            "given": str(given or "").strip(),
            "answer": q.get("answer", ""),
            "correct": verdict,
        })

        for i in range(self._quiz_answers_lay.count()):
            wdg = self._quiz_answers_lay.itemAt(i).widget()
            if wdg is not None:
                wdg.setEnabled(False)

        if verdict is True:
            mark, colour = "✓  correct", C.GREEN
        elif verdict is False:
            mark, colour = "✕  " + str(q.get("answer", "")), C.RED
        else:
            # Open answers and near-miss gap-fills are ULTRON's to judge. Saying
            # so is honest; marking it wrong here would be a guess.
            mark, colour = "…  noted — I'll go over this one with you", C.ACC2
        note = q.get("note") or ""
        self._quiz_note_lbl.setText(mark + (("\n" + note) if note else ""))
        self._quiz_note_lbl.setStyleSheet(f"color: {colour}; background: transparent;")
        self._quiz_note_lbl.show()

        last = self._quiz["i"] >= len(self._quiz["questions"]) - 1
        self._quiz_next_btn.setText("FINISH  →" if last else "NEXT  →")
        self._quiz_next_btn.show()
        self._quiz_next_btn.setFocus()

    def _quiz_next(self):
        if self._quiz is None:
            return
        if self._quiz["i"] >= len(self._quiz["questions"]) - 1:
            self._quiz_finish()
        else:
            self._quiz["i"] += 1
            self._quiz_render()

    def _quiz_finish(self):
        if self._quiz is None:
            return
        topic = self._quiz["topic"]
        results = self._quiz["results"]
        right = sum(1 for r in results if r["correct"] is True)
        unsure = sum(1 for r in results if r["correct"] is None)
        total = len(results)
        self._quiz_panel.hide()
        self._quiz = None

        self._log.append_log(f"QUIZ: {topic or 'quiz'} — {right}/{total} correct")

        # Hand it back to ULTRON as a message, not as a tool return: the tool
        # call ended minutes ago. This is the same channel a dropped file uses.
        lines = [f"[QUIZ_DONE] topic={topic or 'general'} | "
                 f"auto-marked {right}/{total} correct"
                 + (f", {unsure} still need your marking" if unsure else "")]
        for i, r in enumerate(results, 1):
            state = ("correct" if r["correct"] is True
                     else "wrong" if r["correct"] is False else "NEEDS MARKING")
            lines.append(
                f"{i}. [{r['type']}] {r['question']} | they answered: "
                f"{r['given'] or '(blank)'} | expected: {r['answer']} | {state}")
        lines.append(
            "Mark every question flagged NEEDS MARKING yourself — accept an answer "
            "that means the same thing. Then tell them how they did in their own "
            "language: the score, what they got wrong and why, in a couple of "
            "sentences. Offer another round only if it fits. "
            "Remember something only if it would still matter next week — that they "
            "are working through a subject, or keep missing the same thing. A score "
            "from one session is not worth a memory, and a memory per quiz would "
            "bury the things that are.")
        msg = "\n".join(lines)
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(22)
        w.setStyleSheet(f"background: {C.DARK}; border-top: 1px solid {C.BORDER};")
        lay = QHBoxLayout(w); lay.setContentsMargins(14, 0, 14, 0)

        def _fl(txt, color=C.TEXT_MED):
            l = QLabel(txt); l.setFont(QFont("Consolas", 7))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_fl("[F4] Mute  ·  [F11] Fullscreen"))
        lay.addStretch()
        lay.addWidget(_fl(APP_CREDIT, C.PRI_DIM))
        return w

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(f"{icon}  {p.name}  ·  {size}  ·  Tell {self._assistant_name} what to do with it")
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        self._graph_dirty = True
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()

    def _open_remote(self):
        if not self.on_remote_clicked:
            self._log.append_log("SYS: Dashboard not running — remote unavailable.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SYS: Could not generate remote key.")
            return
        url    = result[0]
        key    = result[1]
        auto   = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw  = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov  = RemoteKeyOverlay(url, key, auto_login_url=auto, manual_url=manual,
                               expiry_secs=600, parent=cw)
        ov.set_new_key_callback(self.on_remote_clicked)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.closed.connect(lambda: setattr(self, '_remote_overlay', None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SYS: Remote key generated — manual: {manual or url}")

    # ── Auto-start ──────────────────────────────────────────────────────────────

    def _check_autostart(self) -> bool:
        """Returns True if auto-start is currently registered on this OS."""
        try:
            if _OS == "Windows":
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
                try:
                    winreg.QueryValueEx(key, "CAR_AI")
                    return True
                except FileNotFoundError:
                    return False
                finally:
                    winreg.CloseKey(key)
            elif _OS == "Darwin":
                return (Path.home() / "Library" / "LaunchAgents"
                        / "com.car.assistant.plist").exists()
            else:
                return (Path.home() / ".config" / "autostart" / "car.desktop").exists()
        except Exception:
            return False

    def _toggle_autostart(self):
        currently_on = self._check_autostart()
        try:
            script = str(Path(__file__).resolve().parent / "main.py")
            if _OS == "Windows":
                import winreg
                reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
                if currently_on:
                    winreg.DeleteValue(reg, "CAR_AI")
                else:
                    pythonw = Path(sys.executable).parent / "pythonw.exe"
                    exe = str(pythonw if pythonw.exists() else sys.executable)
                    winreg.SetValueEx(reg, "CAR_AI", 0, winreg.REG_SZ,
                                      f'"{exe}" "{script}"')
                winreg.CloseKey(reg)
            elif _OS == "Darwin":
                plist_dir = Path.home() / "Library" / "LaunchAgents"
                plist_dir.mkdir(parents=True, exist_ok=True)
                plist = plist_dir / "com.car.assistant.plist"
                if currently_on:
                    plist.unlink(missing_ok=True)
                else:
                    plist.write_text(
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                        '<plist version="1.0"><dict>\n'
                        '  <key>Label</key><string>com.car.assistant</string>\n'
                        '  <key>ProgramArguments</key><array>\n'
                        f'    <string>{sys.executable}</string>\n'
                        f'    <string>{script}</string>\n'
                        '  </array>\n'
                        '  <key>RunAtLoad</key><true/>\n'
                        '</dict></plist>\n'
                    )
            else:
                desk_dir = Path.home() / ".config" / "autostart"
                desk_dir.mkdir(parents=True, exist_ok=True)
                desk = desk_dir / "car.desktop"
                if currently_on:
                    desk.unlink(missing_ok=True)
                else:
                    desk.write_text(
                        "[Desktop Entry]\n"
                        f"Name={self._assistant_name}\n"
                        f"Exec={sys.executable} {script}\n"
                        "Type=Application\nTerminal=false\n"
                        "X-GNOME-Autostart-enabled=true\n"
                    )
            enabled = not currently_on
            self._update_autostart_btn(enabled)
            self._log.append_log(
                f"SYS: Auto-start {'enabled' if enabled else 'disabled'}.")
        except Exception as e:
            self._log.append_log(f"ERR: Auto-start failed — {e}")

    def _update_autostart_btn(self, enabled: bool):
        if not hasattr(self, '_autostart_btn'):
            return
        if enabled:
            self._autostart_btn.setText("◉  AUTO-START: ON")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._autostart_btn.setText("◉  AUTO-START: OFF")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    def _toggle_brief(self):
        from memory.config_manager import get_brief_enabled, save_brief_enabled
        new_val = not get_brief_enabled()
        save_brief_enabled(new_val)
        self._update_brief_btn(new_val)

    # ── Wake word settings ───────────────────────────────────────────────────

    def _wake_state(self) -> dict:
        """Combined state for the two wake-word buttons. Readiness is a cheap,
        deterministic on-disk check now (see core.wake_word.is_ready), so there
        is nothing to cache — the button never flickers to a stale value."""
        if self.wake_get_state:
            try:
                s = self.wake_get_state()
                return {"ready": bool(s.get("ready")),
                        "enabled": bool(s.get("enabled")),
                        "awake": bool(s.get("awake"))}
            except Exception:
                pass
        # Before CarLive has wired its callback (drawer built at startup).
        ready, enabled = False, False
        try:
            from core.wake_word import is_ready
            from memory.config_manager import get_wake_word_enabled
            ready, enabled = is_ready(), get_wake_word_enabled()
        except Exception:
            pass
        return {"ready": ready, "enabled": enabled, "awake": True}

    def _refresh_wake_btns(self):
        if not hasattr(self, '_wake_btn'):
            return
        st = self._wake_state()
        _on = f"""
            QPushButton {{ background: #001a08; color: {C.GREEN};
                border: 1px solid {C.GREEN_D}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: #002010; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""
        self._wake_btn.setEnabled(True)
        from memory.config_manager import get_wake_on_clap_enabled
        clap_on = get_wake_on_clap_enabled()
        if not st["ready"]:
            self._wake_btn.setText("⬇  WAKE WORD: DOWNLOAD")
            self._wake_btn.setStyleSheet(_off)
        elif st["enabled"]:
            self._wake_btn.setText("🎙  WAKE WORD: ON")
            self._wake_btn.setStyleSheet(_on)
        else:
            self._wake_btn.setText("🎙  WAKE WORD: OFF")
            self._wake_btn.setStyleSheet(_off)

        if st["enabled"] or clap_on:
            self._wake_sleep_btn.show()
            self._wake_sleep_btn.setText("😴  SLEEP NOW" if st["awake"] else "👂  WAKE NOW")
            self._wake_sleep_btn.setStyleSheet(_off)
        else:
            self._wake_sleep_btn.hide()

    def _toggle_clap(self):
        from memory.config_manager import get_wake_on_clap_enabled, save_wake_on_clap_enabled
        want = not get_wake_on_clap_enabled()
        save_wake_on_clap_enabled(want)
        if hasattr(self, "on_clap_toggle") and self.on_clap_toggle:
            try:
                self.on_clap_toggle(want)
            except Exception as e:
                self._log.append_log(f"ERR: Clap toggle failed — {e}")
        self._refresh_clap_btn()
        self._refresh_wake_btns()

    def _refresh_clap_btn(self):
        if not hasattr(self, "_clap_btn") or self._clap_btn is None:
            return
        from memory.config_manager import get_wake_on_clap_enabled
        en = get_wake_on_clap_enabled()
        _on = f"""
            QPushButton {{ background: #001808; color: {C.PRI};
                border: 1px solid {C.PRI}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: #002010; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""
        if en:
            self._clap_btn.setText("👏  WAKE ON CLAP: ON")
            self._clap_btn.setStyleSheet(_on)
        else:
            self._clap_btn.setText("👏  WAKE ON CLAP: OFF")
            self._clap_btn.setStyleSheet(_off)

    def _refresh_talk_btns(self):
        """Repaint the push-to-talk row from the saved setting."""
        if not hasattr(self, "_ptt_btn"):
            return
        from core.hotkey import chord_label
        from memory.config_manager import get_push_to_talk_enabled
        _on = f"""
            QPushButton {{ background: #001a08; color: {C.GREEN};
                border: 1px solid {C.GREEN_D}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: #002010; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""

        ptt = get_push_to_talk_enabled()
        self._ptt_btn.setText(f"🎚  PUSH-TO-TALK: {chord_label()}" if ptt
                              else "🎚  PUSH-TO-TALK: OFF")
        self._ptt_btn.setStyleSheet(_on if ptt else _off)
        self._ptt_btn.setToolTip(
            "Microphone stays closed until you hold the key — nothing is sent "
            "while you are not holding it." if ptt
            else "Hold a key to talk instead of streaming the mic continuously.")


    def _refresh_hud_btn(self):
        from memory.config_manager import get_hud_style
        labels = {"orb":"ULTRON ORB", "face":"ANIMATED FACE", "core":"REACTOR CORE"}
        self._hud_btn.setText("HUD: " + labels[get_hud_style()])
        self._hud_btn.setToolTip("Switch between Ultron orb, animated face and reactor. Drag the orb to rotate; scroll to zoom.")
        self._hud_btn.setStyleSheet(f"color: {C.PRI}; background: {C.PANEL2}; border: 1px solid {C.BORDER}; text-align: left; padding: 0 8px;")

    def _toggle_hud_style(self):
        from memory.config_manager import get_hud_style, save_hud_style, HUD_STYLES
        want = HUD_STYLES[(HUD_STYLES.index(get_hud_style())+1) % len(HUD_STYLES)]
        save_hud_style(want)
        self.hud.hud_style = want
        self.hud.update()
        self._refresh_hud_btn()
        self._log.append_log("SYS: HUD switched to " + want)

    def _toggle_ptt(self):
        from memory.config_manager import (get_push_to_talk_enabled,
                                           save_push_to_talk_enabled)
        want = not get_push_to_talk_enabled()
        save_push_to_talk_enabled(want)
        scope = None
        if self.on_push_to_talk:
            try:
                scope = self.on_push_to_talk(want)
            except Exception as e:
                self._log.append_log(f"ERR: Push-to-talk failed — {e}")
                save_push_to_talk_enabled(False)
                want = False
        self._apply_ptt_shortcut(want and scope != "global")
        self._refresh_talk_btns()

    def _apply_ptt_shortcut(self, needed: bool):
        """Bind the chord inside the window when no global hook is available.

        On macOS and Linux there is no dependency-free way to read global key
        state, so the chord is at least live whenever this window has focus.
        Qt gives no key-release for a QShortcut, so a press latches the mic open
        and a short timer closes it; held down, auto-repeat keeps pushing that
        timer out, which behaves like holding a key.
        """
        from PyQt6.QtGui import QKeySequence, QShortcut
        from core.hotkey import qt_sequence

        if not needed:
            sc = getattr(self, "_ptt_sc", None)
            if sc is not None:
                sc.setEnabled(False)
                self._ptt_sc = None
            self._ptt_hold(False)
            return
        if getattr(self, "_ptt_sc", None) is not None:
            return

        self._ptt_release = QTimer(self)
        self._ptt_release.setSingleShot(True)
        self._ptt_release.setInterval(420)
        self._ptt_release.timeout.connect(lambda: self._ptt_hold(False))

        def _press():
            self._ptt_hold(True)
            self._ptt_release.start()

        self._ptt_sc = QShortcut(QKeySequence(qt_sequence()), self)
        self._ptt_sc.setAutoRepeat(True)
        self._ptt_sc.activated.connect(_press)

    def _ptt_hold(self, held: bool):
        """Report a windowed press/release to whoever owns the microphone."""
        cb = getattr(self, "ptt_hold", None)
        if cb:
            try:
                cb(bool(held))
            except Exception:
                pass

    def _toggle_wake_word(self):
        st = self._wake_state()
        if not st["ready"]:
            # First time: download openwakeword + model in a worker thread.
            self._wake_btn.setText("⬇  DOWNLOADING… (one-time)")
            self._wake_btn.setEnabled(False)
            def _work():
                try:
                    from core.wake_word import install_and_download
                    ok, msg = install_and_download(
                        logger=lambda m: self._log_sig.emit(f"SYS: {m}"))
                except Exception as e:
                    ok, msg = False, str(e)
                if ok and self.on_wake_toggle:
                    try:
                        self.on_wake_toggle(True)   # auto-enable after a successful download
                    except Exception:
                        pass
                self._wake_dl_sig.emit(ok, msg)
            threading.Thread(target=_work, daemon=True).start()
            return
        # Already downloaded → just flip enabled/disabled through CarLive.
        if self.on_wake_toggle:
            try:
                self.on_wake_toggle(not st["enabled"])
            except Exception:
                pass
        self._refresh_wake_btns()

    def _on_wake_install_done(self, ok: bool, msg: str):
        self._log_sig.emit(f"SYS: {'Wake word ready.' if ok else 'Wake word setup failed: ' + msg}")
        self._refresh_wake_btns()

    def _tap_wake_manual(self):
        if self.on_wake_manual:
            try:
                self.on_wake_manual()
            except Exception:
                pass
        self._refresh_wake_btns()

    def _update_brief_btn(self, enabled: bool):
        if not hasattr(self, '_brief_btn'):
            return
        if enabled:
            self._brief_btn.setText("☀  MORNING BRIEF: ON")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._brief_btn.setText("☀  MORNING BRIEF: OFF")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    # ── Customization ────────────────────────────────────────────────────────────

    def _open_customize(self):
        cfg = _read_full_config()
        if self._customize_overlay:
            self._customize_overlay.hide()
        cw = self.centralWidget()
        ov = CustomizeOverlay(
            cfg.get("assistant_name", "ULTRON") or "ULTRON",
            cfg.get("user_name", ""),
            cfg.get("ui_color", "") or DEFAULT_UI_COLOR,
            cfg.get("voice_name", ""),
            parent=cw,
        )
        ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
        oh = min(oh, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.on_preview = self._preview_ui_color
        ov.saved.connect(self._apply_name_update)
        ov.show()
        self._customize_overlay = ov

    def _preview_ui_color(self, hex_color: str):
        """Live preview — paints the whole interface the new colour (does NOT write to config)."""
        spec = _theme.resolve(hex_color)
        if spec is not None:
            self._theme_apply(spec, persist=False)

    def _apply_name_update(self, name: str, user_name: str, ui_color: str = "",
                           voice: str = ""):
        """Update all name/theme-dependent UI elements and persist to config."""
        self._assistant_name = name.strip() or "ULTRON"
        display = self._assistant_name.upper()
        self.setWindowTitle(f"{display} 2.0 — {APP_CREDIT}")
        self._title_lbl.setText(display)
        self._sub_lbl.setText(f"{APP_NAME.upper()}  •  COMMAND CENTER")
        self._log._ai_name_lc = self._assistant_name.lower()
        if hasattr(self, "core"):
            self.core.name = display
            self._core_title.setText(f"{display} AI CORE")
            self._chat_name.setText(display)
        self._graph_dirty = True
        self.hud._assistant_name = display

        color_changed = False
        if ui_color:
            spec = _theme.resolve(ui_color)
            if spec is not None:
                old_pri = C.PRI
                self._theme_apply(spec)      # whole interface, painted + stylesheets, saved
                color_changed = old_pri != C.PRI

        # Voice change → persist and, if it actually changed, rebuild the Live
        # session so the new voice takes effect (it's fixed at connect time).
        voice_changed = False
        if voice:
            from memory.config_manager import get_voice, save_voice
            if voice != get_voice():
                save_voice(voice)
                voice_changed = True

        try:
            data = {}
            data["assistant_name"] = self._assistant_name
            data["user_name"] = user_name.strip()
            if ui_color:
                data["ui_color"] = ui_color.strip().lower()
            from memory.config_manager import _patch_config
            _patch_config(**data)
            self._log.append_log(f"SYS: Identity updated — {display}")
            if color_changed:
                self._log.append_log(f"SYS: UI colour applied — {ui_color}")
            if voice_changed:
                self._log.append_log(f"SYS: Voice set — {voice}")
        except Exception as e:
            self._log.append_log(f"ERR: Config save failed — {e}")

        if voice_changed and self.on_voice_change:
            self.on_voice_change()

    def _centre_overlay(self, ov) -> None:
        """Place a floating overlay in the middle of the HUD and show it."""
        cw = self.centralWidget()
        ov.adjustSize()
        ov.setGeometry(
            max(0, (cw.width()  - ov.width())  // 2),
            max(0, (cw.height() - ov.height()) // 2),
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()

    # ── Audio devices ────────────────────────────────────────────────────────

    def _open_audio_devices(self):
        ov = AudioDeviceOverlay(parent=self.centralWidget())
        ov.picked.connect(self._on_audio_devices_applied)
        self._centre_overlay(ov)
        self._audio_overlay = ov            # keep a reference so it isn't GC'd

    def _on_audio_devices_applied(self):
        self._log.append_log("SYS: Audio devices updated.")
        if self.on_audio_device_change:
            self.on_audio_device_change()

    # ── Memory panel ─────────────────────────────────────────────────────────

    def _open_memory_panel(self):
        ov = MemoryOverlay(parent=self.centralWidget())
        self._centre_overlay(ov)
        self._memory_overlay = ov

    # ── Irreversible-action confirmation ─────────────────────────────────────

    def _show_confirm_banner(self, title: str, detail: str, token: str):
        from core import confirm
        if token != confirm.pending_token():
            return
        if len(detail) > 300 or (self._integration_hub and self._integration_hub.isVisible()):
            # Long change lists get the native review dialog (never the web view).
            from core.integrations.panels import ReviewDialog
            ReviewDialog.open(self._integration_hub if self._integration_hub and self._integration_hub.isVisible()
                              else self, title, detail, token)
            return
        self._hide_confirm_banner()
        ov = ConfirmBanner(title, detail, parent=self.centralWidget())
        ov.answered.connect(lambda accepted: self._on_confirm_answered(accepted, token))
        self._centre_overlay(ov)
        self._confirm_overlay = ov

    def _hide_confirm_banner(self):
        ov = getattr(self, "_confirm_overlay", None)
        if ov is not None:
            ov.hide()
            ov.deleteLater()
            self._confirm_overlay = None

    def _on_confirm_answered(self, accepted: bool, token: str):
        # Tear the banner down first: core.confirm.resolve() may be about to
        # shut the machine down, and a live widget mid-callback is not where you
        # want to be when that happens.
        self._hide_confirm_banner()
        try:
            from core.confirm import resolve
            resolve(bool(accepted), expected_token=token)
        except Exception as e:
            self._log.append_log(f"ERR: Confirmation failed — {e}")

    def _open_plugin_manager(self):
        plugins = self.get_plugins() if self.get_plugins else []
        cw = self.centralWidget()
        ov = PluginManagerOverlay(plugins, parent=cw)
        ov.adjustSize()
        ov.setGeometry(
            (cw.width()  - ov.width())  // 2,
            (cw.height() - ov.height()) // 2,
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()
        self._plugin_manager_overlay = ov   # keep a reference so it isn't GC'd

    def _workspace_window(self):
        """ULTRON Workspace 2.0 (core/workspace), created on first use."""
        if self._integration_hub is None:
            from core.workspace.window import WorkspaceWindow
            self._integration_hub = WorkspaceWindow(self)
        return self._integration_hub

    def _open_workspace(self, view: str = "chat", conversation_id: str = ""):
        try:
            self._workspace_window().open_view(view, conversation_id)
        except Exception as e:
            self._log.append_log(f"ERR: Workspace could not open — {e}")

    def _present_integration(self, panel, result):
        """Results published by voice tools and plugins land in the Workspace."""
        try:
            win = self._workspace_window()
            if panel == "workspace":
                win.open_view("chat", (result or {}).get("conversation_id", ""))
            else:
                win.present(panel, result)
        except Exception as e:
            self._log.append_log(f"ERR: Workspace could not show the result — {e}")

    def prompt_connection_choice(self, service: str = "gmail") -> str:
        """Interactive modal asking user whether to use Claude MCP connection or User OAuth connection."""
        try:
            from core.integrations.panels import ConnectionChoiceDialog
            dialog = ConnectionChoiceDialog(service=service, parent=self)
            dialog.exec()
            return dialog.choice
        except Exception as exc:
            print(f"[UI] prompt_connection_choice error ({exc}) - defaulting to user")
            return "user"

    def _open_plugin_settings(self):
        sections = self.get_plugin_settings() if self.get_plugin_settings else []
        cw = self.centralWidget()
        ov = PluginSettingsOverlay(sections, parent=cw)
        ow = PluginSettingsOverlay._OW
        oh = min(560, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.show()
        ov.raise_()
        self._plugin_settings_overlay = ov   # keep a reference so it isn't GC'd

    # ── Clipboard intelligence ───────────────────────────────────────────────────

    def _on_clipboard_changed(self):
        try:
            text = QApplication.clipboard().text().strip()
            if len(text) >= 10:
                self._clipboard_sig.emit(text)
        except Exception:
            pass

    def _show_clipboard_panel(self, text: str):
        self._clipboard_panel.show_clipboard(text)
        self._position_clipboard_panel()

    def _position_clipboard_panel(self):
        cw = self.centralWidget()
        pw = ClipboardPanel._W
        ph = self._clipboard_panel.sizeHint().height() or ClipboardPanel._H
        x = (cw.width() - pw) // 2
        y = cw.height() - ph - 6
        self._clipboard_panel.setGeometry(x, y, pw, ph)
        self._clipboard_panel.raise_()

    def _on_clipboard_action(self, cmd: str):
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(cmd,), daemon=True).start()

    # ────────────────────────────────────────────────────────────────────────────

    def _do_interrupt(self):
        # ESC is a window shortcut, so it fires before any widget sees the key:
        # while an intro is playing, ESC means "skip", not "stop talking".
        for ov in (self._fx_boot, self._fx_preview):
            if ov is not None and not ov.done and ov.SKIPPABLE:
                ov.skip()
                return
        if self.on_interrupt:
            self.on_interrupt()

    def _toggle_mute(self):
        # A deliberate press settles the question: whatever the video did, or is
        # about to do, the user has now said what they want.
        self._video_auto_muted = False
        self._set_muted(not self._muted)

    def _set_muted(self, muted: bool, note: str = ""):
        muted = bool(muted)
        if muted == self._muted:
            return
        self._muted = muted
        self.hud.muted = muted
        self._style_mute_btn()
        if muted:
            if self.on_interrupt:
                self.on_interrupt()
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted." + (f" {note}" if note else ""))
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active." + (f" {note}" if note else ""))

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇")
            self._mute_btn.setToolTip("Microphone muted — click to unmute  [F4]")
            self._mute_btn.setStyleSheet(f"QPushButton {{ background: #140006; color: {C.MUTED_C}; "
                                         f"border: 1px solid {C.MUTED_C}; border-radius: 8px; font-size: 12pt; }}")
        else:
            self._mute_btn.setText("🎙")
            self._mute_btn.setToolTip("Microphone active — click to mute  [F4]")
            self._mute_btn.setStyleSheet(f"QPushButton {{ background: #00140a; color: {C.GREEN}; "
                                         f"border: 1px solid #17734a; border-radius: 8px; font-size: 12pt; }}"
                                         f"QPushButton:hover {{ border-color: {C.GREEN}; }}")
        self._update_core_state()

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        self._submit_command(txt)

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")
        self._hud_state   = state
        if self._fx_sfx is not None:        # ULTRON's live voice outranks effects
            if state == "SPEAKING":
                self._fx_sfx.duck("voice", 0.45)
            else:
                self._fx_sfx.unduck("voice")
        self._update_core_state()
        if hasattr(self, "_orbit_web") and self._orbit_web is not None:
            try:
                self._orbit_web.page().runJavaScript(f"if (window.setOrbitState) window.setOrbitState('{state}');")
            except Exception:
                pass

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 390
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        from memory.config_manager import _patch_config
        _patch_config(gemini_api_key=key, os_system=os_name)
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._assistant_name = _read_full_config().get("assistant_name", "ULTRON") or "ULTRON"
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. {self._assistant_name} online.")



class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class CarUI:
    def __init__(self, face_path: str = "", size=None):
        if QApplication.instance() is None:
            QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._app.setWindowIcon(QIcon(str(CONFIG_DIR / "ultron.ico")))
        self._win = MainWindow(face_path)
        self.root = _RootShim(self._app)
        self._win.show_at_launch()
        from core.screen_share import screen_share_manager
        screen_share_manager.register_ui_callback(self.set_screen_share_active)
        screen_share_manager.register_pointer_callback(self.show_screen_pointer)

    def show_screen_pointer(self, x: int, y: int, label: str = "", bbox=None, duration: float = 5.0):
        self._win.show_screen_pointer(x, y, label, bbox, duration)

    def set_screen_share_active(self, active: bool):
        self._win.set_screen_share_active(active)

    def is_screen_sharing_active(self) -> bool:
        from core.screen_share import screen_share_manager
        return screen_share_manager.is_active()

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    @property
    def on_voice_change(self):
        return self._win.on_voice_change

    @on_voice_change.setter
    def on_voice_change(self, cb):
        self._win.on_voice_change = cb

    @property
    def on_audio_device_change(self):
        return self._win.on_audio_device_change

    @on_audio_device_change.setter
    def on_audio_device_change(self, cb):
        self._win.on_audio_device_change = cb

    # ── Command center (set by CarLive) ──────────────────────────────────────
    @property
    def on_user_command(self):
        return self._win.on_user_command

    @on_user_command.setter
    def on_user_command(self, cb):
        self._win.on_user_command = cb

    @property
    def router(self):
        return self._win.router

    @router.setter
    def router(self, r):
        self._win.router = r

    @property
    def connections(self):
        return self._win.connections

    @connections.setter
    def connections(self, m):
        self._win.connections = m

    @property
    def tool_catalog(self):
        return self._win.get_tool_catalog

    @tool_catalog.setter
    def tool_catalog(self, cb):
        self._win.get_tool_catalog = cb
        self._win._graph_dirty = True

    def set_connections(self, snapshot: dict) -> None:
        """Thread-safe: connection-manager listeners run on worker threads."""
        self._win._conn_sig.emit(snapshot)

    def show_confirm(self, title: str, detail: str, token: str) -> None:
        """Thread-safe: raise the irreversible-action gate. Called from action
        handlers running in executor threads, so it goes through a signal."""
        self._win._confirm_sig.emit(str(title)[:120], str(detail), token)

    def hide_confirm(self) -> None:
        """Thread-safe: take the gate down."""
        self._win._confirm_hide_sig.emit()

    @property
    def get_plugins(self):
        return self._win.get_plugins

    @get_plugins.setter
    def get_plugins(self, cb):
        self._win.get_plugins = cb

    @property
    def get_plugin_settings(self):
        return self._win.get_plugin_settings

    @get_plugin_settings.setter
    def get_plugin_settings(self, cb):
        self._win.get_plugin_settings = cb

    @property
    def on_wake_toggle(self):
        return self._win.on_wake_toggle

    @on_wake_toggle.setter
    def on_wake_toggle(self, cb):
        self._win.on_wake_toggle = cb

    @property
    def on_clap_toggle(self):
        return self._win.on_clap_toggle

    @on_clap_toggle.setter
    def on_clap_toggle(self, cb):
        self._win.on_clap_toggle = cb

    @property
    def on_wake_manual(self):
        return self._win.on_wake_manual

    @on_wake_manual.setter
    def on_wake_manual(self, cb):
        self._win.on_wake_manual = cb

    @property
    def wake_get_state(self):
        return self._win.wake_get_state

    @wake_get_state.setter
    def wake_get_state(self, cb):
        self._win.wake_get_state = cb

    def set_audio_level(self, level: float) -> None:
        """Thread-safe: feed a 0.0–1.0 live audio level to the HUD waveform.
        Called from the audio threads; a plain float store is atomic under the
        GIL, so no signal/lock is needed for this cosmetic value."""
        try:
            self._win.hud.set_audio_level(level)
            self._win.core.set_level(level)
        except Exception:
            pass

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            if self._avatar is not None:
                self._avatar.glance(dx, dy, hold)
        except Exception:
            pass

    @property
    def ptt_hold(self):
        return self._win.ptt_hold

    @ptt_hold.setter
    def ptt_hold(self, cb):
        self._win.ptt_hold = cb

    @property
    def on_push_to_talk(self):
        return self._win.on_push_to_talk

    @on_push_to_talk.setter
    def on_push_to_talk(self, cb):
        self._win.on_push_to_talk = cb

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: post a schedule of (level, openness, width) mouth frames
        for ULTRON's own speech. `at` is the wall-clock time the batch begins to
        sound, not the time of the call. See HudCanvas.push_visemes()."""
        try:
            self._win.hud.push_visemes(frames, hop, at)
        except Exception:
            pass
        try:
            self._win.core.push_visemes(frames, hop, at)   # speech-reactive core
        except Exception:
            pass

    def begin_shutdown_animation(self) -> None:
        """Thread-safe: start the shutdown cinematic for a voice shutdown.
        The caller keeps ownership of the exit; see wait_shutdown_animation()."""
        self._win._fx_shutdown_sig.emit("voice")

    def wait_shutdown_animation(self, timeout: float = 12.0) -> bool:
        """Block (off the UI thread) until the shutdown cinematic has finished,
        or `timeout` seconds — the exit never waits on an animation for long."""
        return self._win._fx_shutdown_event.wait(timeout)

    # ── Colour theme (voice tool, typed commands) ────────────────────────────
    def change_theme(self, value: str, source: str = "voice") -> str:
        """Thread-safe: change the interface colour. `value` is a theme name in
        English or Serbian, "rgb", "original" or a #RRGGBB hex. Answers at once
        with "started", "queued" (another transition is running — the newest
        request wins), "same", "invalid" or "unavailable"; the transformation
        itself runs on the UI thread."""
        spec = _theme.resolve(value)
        if spec is None:
            return "invalid"
        win = self._win
        if win._fx_shutdown_started:
            return "unavailable"
        busy = win._theme_seq is not None or win._fx_boot is not None or win._fx_preview is not None
        cur = win._theme
        if not busy and cur is not None and cur.name == spec.name and cur.hex == spec.hex:
            return "same"
        win._theme_sig.emit(spec.name, spec.hex, source)
        return "queued" if busy else "started"

    @property
    def theme_name(self) -> str:
        t = self._win._theme
        return t.name if t is not None else ""

    # ── Welcome narration ────────────────────────────────────────────────────
    @property
    def intro_active(self) -> bool:
        """True while the welcome recording plays: the backend keeps the
        microphone closed and holds ULTRON's own voice until it ends."""
        return bool(self._win._intro_active)

    @property
    def on_intro_finished(self):
        return self._win.on_intro_finished

    @on_intro_finished.setter
    def on_intro_finished(self, cb):
        self._win.on_intro_finished = cb

    @property
    def request_say(self):
        return self._win.request_say

    @request_say.setter
    def request_say(self, cb):
        self._win.request_say = cb

    def notify_phone_connected(self) -> None:
        self._win.notify_phone_connected()

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def show_content(self, title: str, text: str):
        """Thread-safe: display content in the panel below the HUD."""
        self._win._content_sig.emit(title[:48], text[:4000])

    def show_quiz(self, topic: str, questions, grade=None) -> None:
        """Thread-safe: put an interactive quiz on the board.

        `grade(question, given)` decides each answer — the plugin supplies it so
        the marking rules live with the questions rather than being duplicated
        here. Returning None from it means "ULTRON should judge this one", which
        is how open answers and near-miss gap-fills are handled.

        Returns immediately: the user answers at their own pace and the finished
        result is delivered back through on_text_command.
        """
        self._win._quiz_sig.emit(str(topic or ""), list(questions or []), grade)

    def hide_quiz(self) -> None:
        """Thread-safe: clear any quiz currently on the board."""
        self._win._quiz_hide_sig.emit()

    def show_review(self, title: str, summary: str, findings, unclear=None) -> None:
        """Thread-safe: lay a document review into the panel below the HUD.

        `findings` is a list of {heading, detail, severity, quote, suggestion};
        severity is one of 'serious' / 'caution' / 'note' and decides colour and
        order here, so the caller supplies no styling of its own.
        """
        self._win._review_sig.emit(str(title or ""), str(summary or ""),
                                   list(findings or []), list(unclear or []))

    def prompt_reconfig(self):
        """Thread-safe: show the API key setup overlay (e.g. after an auth error)."""
        self._win._ready = False
        self._win._reconfig_sig.emit()

    def show_camera_frame(self, img_bytes: bytes):
        """Thread-safe: show a webcam frame in the small overlay (screen captures)."""
        self._win._camera_sig.emit(img_bytes)

    def show_video(self, source: str, title: str = "", muted: bool = True,
                   audio_source: str = "") -> None:
        """Thread-safe: play a video where the avatar normally is.

        `source` is a local path or a direct media URL. `audio_source` is for
        the case where the sound arrives as its own stream — YouTube serves no
        combined format any more — and when it is given the two are played
        together and kept in step.

        Muted by default, and that is a decision rather than a default: a
        soundtrack talking over ULTRON is the one way this could make the
        assistant worse. The user turns sound on from the header button or by
        asking, and closes it the same two ways.
        """
        self._win._video_open_sig.emit(str(source or ""), str(title or ""),
                                       bool(muted), str(audio_source or ""))

    def stop_video(self) -> None:
        """Thread-safe: close the video and give the HUD back to the avatar."""
        self._win._video_close_sig.emit()

    def set_video_muted(self, muted: bool) -> None:
        """Thread-safe: silence or unsilence whatever is playing."""
        self._win._video_mute_sig.emit(bool(muted))

    def video_is_playing(self) -> bool:
        return bool(self._win.video_is_playing())

    def start_camera_stream(self) -> None:
        """Thread-safe: start live camera feed in the full HUD area."""
        self._win.start_camera_stream()

    def stop_camera_stream(self) -> None:
        """Thread-safe: stop the live camera feed."""
        self._win.stop_camera_stream()

    @property
    def assistant_name(self) -> str:
        return self._win._assistant_name

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")

    def move_to_screen(self, target: str = "tv", fullscreen: bool = True) -> bool:
        """Thread-safe: move ULTRON window to target screen ('tv' or 'main') and set fullscreen."""
        self._win._screen_move_sig.emit(str(target or "tv"), bool(fullscreen))
        return True

    def show_orbit(self) -> None:
        """Thread-safe: switch display to 3D Orbit Constellation view."""
        self._win._orbit_sig.emit(True)

    def hide_orbit(self) -> None:
        """Thread-safe: return display back to standard HUD."""
        self._win._orbit_sig.emit(False)

    def is_orbit_active(self) -> bool:
        return self._win._hud_cam_stack.currentIndex() == 3

    def show_holo(self) -> None:
        """Thread-safe: start camera stream with Holo Hands overlay enabled."""
        self._win._holo_sig.emit(True)

    def hide_holo(self) -> None:
        """Thread-safe: disable Holo Hands overlay."""
        self._win._holo_sig.emit(False)

    def is_holo_active(self) -> bool:
        return getattr(self._win, "_holo_hands_active", False)

    def set_holo_hands_active(self, active: bool = True) -> None:
        """Thread-safe: toggle Holo Hands gesture detection overlay in camera."""
        self._win._holo_sig.emit(bool(active))

    def is_holo_hands_active(self) -> bool:
        return getattr(self._win, "_holo_hands_active", False)

