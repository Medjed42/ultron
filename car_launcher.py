"""
car_launcher.py — ULTRON's tray auto-launcher.

Sits in the system tray and starts ULTRON when you call for it. Two activation
methods, chosen in the tray menu (Activation mode) and saved immediately:

  • Clap detection  — a double (or triple) clap launches ULTRON.
  • Name calling    — saying the assistant's name ("Ultron") launches it.

Only the selected method listens. Both share ONE microphone stream; switching
modes swaps the detector on that stream, so there is never a second listener
or a second stream. While ULTRON itself is running the launcher releases the
microphone entirely (ULTRON has its own wake on clap / wake word), and takes it
back when ULTRON exits.

Flow:  microphone (sounddevice, 16 kHz mono)
         → ActivationListener._callback (audio thread, cheap)
         → ClapDetector (core/clap_detector.py)    or   WakeWordDetector (core/wake_word.py)
         → ActivationListener._fire (debounced)
         → Qt signal → CarLauncherApp._on_activate (GUI thread)
         → launch_ultron.py (which itself refuses to start a second ULTRON)

What was broken before this rewrite: the triple-clap detector existed but was
never fed any audio; name matching used substrings such as "tar", "car" and
"sorry" and did not include "ultron" at all; tray notifications were raised
from the audio thread; the "is ULTRON running" check looked for "ultron" in the
folder path and so never matched (…\\Jarvis); every phrase heard was appended
to vosk_log.txt; and two launchers could run at once.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Status lines carry emoji; a legacy Windows console (cp1252) would raise
# UnicodeEncodeError mid mode-switch. Same guard main.py uses. Under pythonw
# there is no console and the streams are None.
for _stream in ("stdout", "stderr"):
    try:
        _s = getattr(sys, _stream, None)
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from core.clap_detector import ClapDetector  # noqa: E402

SAMPLE_RATE = 16000
BLOCK = 1024                      # 64 ms
ACTIVATION_DEBOUNCE_S = 3.0       # one clap pattern / one utterance = one activation
LAUNCH_COOLDOWN_S = 25.0          # time ULTRON gets to appear before another launch
SILENCE_WARN_S = 4.0              # all-zero audio this long = muted / blocked microphone


# ── ULTRON process checks / launching ─────────────────────────────────────────

def is_car_running() -> bool:
    """True when ULTRON's main.py is running (same check launch_ultron.py uses,
    which resolves the script path instead of guessing from the folder name)."""
    try:
        import launch_ultron
        return launch_ultron.running("main.py")
    except Exception:
        return False


def launch_ultron_process() -> None:
    """Start ULTRON the way run.bat / start_ultron.bat do, without a console."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw if pythonw.exists() else sys.executable)
    env = dict(os.environ, PYTHONUTF8="1")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen([exe, str(BASE_DIR / "launch_ultron.py")], cwd=str(BASE_DIR), env=env,
                     creationflags=flags, close_fds=True)


# ── Microphone listener (Qt-free, testable) ───────────────────────────────────

class ActivationListener:
    """Owns the launcher's single microphone stream and the active detector."""

    def __init__(self, on_activate: Callable[[str], None],
                 on_status: Callable[[str, str], None],
                 mode: str = "clap", clap_count: int = 2, sensitivity: str = "medium",
                 name: str = "Ultron", device_name: str = "",
                 stream_factory: Optional[Callable] = None,
                 wake_factory: Optional[Callable] = None,
                 logger: Callable[[str], None] = lambda m: None):
        self._on_activate = on_activate
        self._on_status = on_status
        self._log = logger
        self.mode = mode if mode in ("clap", "name") else "clap"
        self.name = name or "Ultron"
        self.device_name = device_name
        self._stream_factory = stream_factory or _default_stream
        self._wake_factory = wake_factory or _default_wake
        self._lock = threading.RLock()
        self._stream = None
        self._wake = None
        self.state = "stopped"            # stopped | listening | paused | error
        self.last_error = ""
        self._last_fire = 0.0
        self._zero_run = 0
        self._silence_warned = False
        self.blocks_seen = 0
        self._peak = 0
        self._rms_min = None
        self.clap = ClapDetector(on_pattern=self._on_clap_pattern, sample_rate=SAMPLE_RATE,
                                 sensitivity=sensitivity, claps_required=clap_count,
                                 enabled=False, logger=logger)

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> bool:
        """Open the microphone (if needed) and start the selected detector."""
        with self._lock:
            if self._stream is None:
                try:
                    self._stream = self._stream_factory(self._callback, self.device_name)
                    self._stream.start()
                except Exception as e:
                    self._close_stream()
                    return self._fail(f"Could not open the microphone: {_friendly(e)}")
            self._zero_run, self._silence_warned = 0, False
            if not self._activate_detector():
                return False
            self.state = "listening"
            self._on_status("listening", self.describe())
            return True

    def stop(self, state: str = "stopped", reason: str = "") -> None:
        """Stop the detector and release the microphone completely."""
        with self._lock:
            self._deactivate_detector()
            self._close_stream()
            self.state = state
        if state == "paused":
            self._on_status("paused", reason or "Paused")

    def pause(self, reason: str) -> None:
        if self.state != "paused":
            self.stop("paused", reason)

    def healthy(self) -> bool:
        with self._lock:
            s = self._stream
            try:
                return s is not None and bool(getattr(s, "active", True))
            except Exception:
                return False

    # ── settings (take effect immediately) ───────────────────────────────
    def set_mode(self, mode: str) -> bool:
        if mode not in ("clap", "name"):
            raise ValueError(mode)
        with self._lock:
            if mode == self.mode and self.state == "listening":
                return True
            self._deactivate_detector()
            self.mode = mode
            if self._stream is None:                      # paused / stopped: applies on start
                return True
            if not self._activate_detector():
                return False
            self.state = "listening"
            self._on_status("listening", self.describe())
            return True

    def set_clap_count(self, count: int) -> None:
        with self._lock:
            self.clap.claps_required = int(count)
            self.clap.reset()
            if self.state == "listening":
                self._on_status("listening", self.describe())

    def set_sensitivity(self, name: str) -> None:
        with self._lock:
            self.clap.set_sensitivity(name)
            self.clap.reset()
            if self.state == "listening":
                self._on_status("listening", self.describe())

    def describe(self) -> str:
        if self.mode == "clap":
            n = {2: "double", 3: "triple"}.get(self.clap.claps_required, f"{self.clap.claps_required}x")
            return f"Listening for a {n} clap ({self.clap.sensitivity} sensitivity)"
        return f"Listening for the name “{self.name}”"

    # ── detectors ────────────────────────────────────────────────────────
    def _activate_detector(self) -> bool:
        if self.mode == "clap":
            self.clap.reset()
            self.clap.enabled = True
            return True
        self.clap.enabled = False
        try:
            self._wake = self._wake_factory(self._on_wake, self.name, self._log)
            ok = self._wake.start()
        except Exception as e:
            ok = False
            self.last_error = str(e)
        if not ok:
            self._wake = None
            return self._fail(f"Name recognition is unavailable (offline model for “{self.name}” "
                              f"could not start). Switch to clap detection or check vosk_model/.")
        return True

    def _deactivate_detector(self) -> None:
        self.clap.enabled = False
        self.clap.reset()
        w, self._wake = self._wake, None
        if w is not None:
            try:
                w.stop()
            except Exception:
                pass

    def _close_stream(self) -> None:
        s, self._stream = self._stream, None
        if s is not None:
            for op in ("stop", "close"):
                try:
                    getattr(s, op)()
                except Exception:
                    pass

    def _fail(self, message: str) -> bool:
        self.state = "error"
        self.last_error = message
        self._on_status("error", message)
        return False

    # ── audio thread ─────────────────────────────────────────────────────
    def _callback(self, indata, frames, time_info, status) -> None:
        try:
            self.blocks_seen += 1
            block = indata[:, 0] if getattr(indata, "ndim", 1) > 1 else indata
            peak = int(np.max(np.abs(block))) if len(block) else 0
            self._peak = max(self._peak, peak)
            rms = float(np.sqrt(np.mean(block.astype(np.float32) ** 2))) if len(block) else 0.0
            self._rms_min = rms if self._rms_min is None else min(self._rms_min, rms)
            if peak == 0:
                self._zero_run += len(block)
                if not self._silence_warned and self._zero_run >= SILENCE_WARN_S * SAMPLE_RATE:
                    self._silence_warned = True
                    self._on_status("warning", "The microphone delivers pure silence — it is muted, or "
                                    "Windows blocks it: Settings → Privacy & security → Microphone → "
                                    "allow desktop apps.")
            else:
                if self._silence_warned:
                    self._silence_warned = False
                    self._on_status("listening", self.describe())
                self._zero_run = 0
            if self.mode == "clap":
                self.clap.feed(indata)
            else:
                w = self._wake
                if w is not None:
                    w.feed(indata)
        except Exception as e:          # never let an exception kill the audio stream
            self._log(f"audio callback error: {e}")

    def _on_clap_pattern(self, count: int) -> None:
        if self.mode == "clap" and self.state == "listening":
            self._fire("clap")

    def _on_wake(self) -> None:
        if self.mode == "name" and self.state == "listening":
            self._fire("name")

    def _fire(self, source: str) -> None:
        now = time.monotonic()
        if now - self._last_fire < ACTIVATION_DEBOUNCE_S:
            return
        self._last_fire = now
        self._on_activate(source)

    # ── diagnostics ──────────────────────────────────────────────────────
    def take_levels(self) -> dict:
        """Loudest peak and quietest block RMS since the last call (dBFS)."""
        peak, rms = self._peak, self._rms_min
        self._peak, self._rms_min = 0, None
        db = lambda v: round(20 * float(np.log10(max(v, 1e-9) / 32768.0)), 1)
        return {"peak_dbfs": db(peak), "floor_dbfs": db(rms or 0.0), "blocks": self.blocks_seen,
                "claps": self.clap.claps_detected,
                "min_clap_dbfs": round(20 * float(np.log10(self.clap.min_peak / 32768.0)), 1)}


def _friendly(e: Exception) -> str:
    msg = str(e) or type(e).__name__
    low = msg.lower()
    if "invalid device" in low or "no default" in low or "-9996" in msg or "-9998" in msg:
        return "no usable input device was found (is a microphone connected?)"
    if "unanticipated host error" in low or "-9999" in msg or "access" in low:
        return msg + " — the device may be in use or blocked by Windows privacy settings"
    return msg


def _default_stream(callback, device_name: str = ""):
    """sounddevice InputStream on the microphone ULTRON is configured to use,
    falling back to the system default if that one cannot be opened."""
    import sounddevice as sd
    device = None
    if device_name:
        try:
            from core import audio_devices
            audio_devices.configure(SAMPLE_RATE, 24000)
            device = audio_devices.resolve(device_name, "input")
        except Exception:
            device = None
    try:
        return sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=BLOCK,
                              device=device, callback=callback)
    except Exception:
        if device is None:
            raise
        return sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=BLOCK,
                              callback=callback)


def _default_wake(on_detect, name, logger):
    from core.wake_word import WakeWordDetector
    return WakeWordDetector(on_detect=on_detect, logger=logger, name=name)


# ── Tray application ──────────────────────────────────────────────────────────

def generate_futuristic_icon_file(filepath: Path) -> None:
    """Draws the launcher's glowing orb icon and saves it to `filepath`."""
    from PyQt6.QtCore import Qt, QPointF
    from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(0, 255, 255, 80), 4))
    painter.drawEllipse(4, 4, 56, 56)
    painter.setPen(QPen(QColor(0, 255, 255, 200), 2))
    painter.drawEllipse(12, 12, 40, 40)
    painter.setBrush(QColor(0, 255, 255, 255))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPolygon(QPolygonF([QPointF(32, 20), QPointF(46, 42), QPointF(18, 42)]))
    painter.end()
    pixmap.save(str(filepath))


def _named_mutex(name: str):
    """A Windows named mutex handle, or None when another process holds it.
    Released by the OS when the process exits, even after a crash."""
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    handle = k32.CreateMutexW(None, False, name)
    if handle and k32.GetLastError() == 183:          # ERROR_ALREADY_EXISTS
        k32.CloseHandle(ctypes.c_void_p(handle))
        return None
    return handle


def _load_settings() -> dict:
    from memory import config_manager as cm
    try:
        name = (cm.load_api_keys().get("assistant_name") or "Ultron").strip() or "Ultron"
    except Exception:
        name = "Ultron"
    return {"mode": cm.get_launcher_activation(), "clap_count": cm.get_launcher_clap_count(),
            "sensitivity": cm.get_clap_sensitivity(), "name": name,
            "device": cm.get_input_device() or ""}


class CarLauncherApp:
    MODE_LABELS = {"clap": "👏  Clap detection", "name": "🗣  Name calling"}
    MUTEX_NAME = r"Local\ULTRON_AutoLauncher"

    def __init__(self, listener_factory: Optional[Callable] = None,
                 ultron_running: Callable[[], bool] = is_car_running,
                 launcher: Callable[[], None] = launch_ultron_process,
                 single_instance: bool = True):
        from PyQt6.QtCore import QLockFile, QObject, QTimer, pyqtSignal
        from PyQt6.QtGui import QActionGroup, QIcon
        from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

        class _Bridge(QObject):          # worker threads → GUI thread
            status = pyqtSignal(str, str)
            activate = pyqtSignal(str)
            message = pyqtSignal(str, str)

        self.app = QApplication.instance() or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.base_dir = BASE_DIR
        self._ultron_running = ultron_running
        self._launcher = launcher
        self._launched_at = 0.0
        self.activations: list[str] = []
        self.status_text = "Starting…"

        self._lock_file = None
        self._mutex = None
        self.duplicate = False
        if single_instance:
            # One launcher = one listener on the microphone.
            if os.name == "nt":
                self._mutex = _named_mutex(self.MUTEX_NAME)
                self.duplicate = self._mutex is None
            else:
                self._lock_file = QLockFile(os.path.join(tempfile.gettempdir(), "ultron_launcher.lock"))
                self._lock_file.setStaleLockTime(0)
                self.duplicate = not self._lock_file.tryLock(200)

        self.bridge = _Bridge()
        self.bridge.status.connect(self._apply_status)
        self.bridge.activate.connect(self._on_activate)
        self.bridge.message.connect(self._show_message)

        self.icon_path = self.base_dir / "launcher_icon.ico"
        if not self.icon_path.exists():
            try:
                generate_futuristic_icon_file(self.icon_path)
            except Exception:
                pass
        launcher_icon = QIcon(str(self.icon_path))
        self.app.setWindowIcon(launcher_icon)
        self.tray_icon = QSystemTrayIcon(launcher_icon, self.app)

        s = _load_settings()
        self.settings = s
        self.menu = QMenu()
        self.status_action = self.menu.addAction("Starting…")
        self.status_action.setEnabled(False)
        self.menu.addSeparator()
        self.launch_action = self.menu.addAction("Launch ULTRON Now")
        self.launch_action.triggered.connect(lambda: self.launch_car(manual=True))
        self.menu.addSeparator()

        self.mode_menu = self.menu.addMenu("Activation mode")
        self.mode_group = QActionGroup(self.mode_menu)
        self.mode_actions = {}
        for mode in ("clap", "name"):
            label = self.MODE_LABELS[mode] + (f" — say “{s['name']}”" if mode == "name" else "")
            act = self.mode_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(mode == s["mode"])
            act.triggered.connect(lambda _c=False, m=mode: self.set_mode(m))
            self.mode_group.addAction(act)
            self.mode_actions[mode] = act

        self.clap_menu = self.menu.addMenu("Clap pattern")
        self.clap_group = QActionGroup(self.clap_menu)
        self.clap_actions = {}
        for count, label in ((2, "Double clap  👏👏"), (3, "Triple clap  👏👏👏")):
            act = self.clap_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(count == s["clap_count"])
            act.triggered.connect(lambda _c=False, n=count: self.set_clap_count(n))
            self.clap_group.addAction(act)
            self.clap_actions[count] = act

        self.sens_menu = self.menu.addMenu("Clap sensitivity")
        self.sens_group = QActionGroup(self.sens_menu)
        self.sens_actions = {}
        for level, label in (("low", "Low — loud claps only"), ("medium", "Medium (recommended)"),
                             ("high", "High — quiet claps / far away")):
            act = self.sens_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(level == s["sensitivity"])
            act.triggered.connect(lambda _c=False, lv=level: self.set_sensitivity(lv))
            self.sens_group.addAction(act)
            self.sens_actions[level] = act

        self.test_action = self.menu.addAction("Test microphone (5 s)")
        self.test_action.triggered.connect(self.test_microphone)
        self.menu.addSeparator()
        self.quit_action = self.menu.addAction("Quit Auto-Launcher")
        self.quit_action.triggered.connect(self.quit_app)
        self.tray_icon.setContextMenu(self.menu)
        self._sync_mode_menus()

        factory = listener_factory or ActivationListener
        self.listener = factory(
            on_activate=lambda src: self.bridge.activate.emit(src),
            on_status=lambda st, text: self.bridge.status.emit(st, text),
            mode=s["mode"], clap_count=s["clap_count"], sensitivity=s["sensitivity"],
            name=s["name"], device_name=s["device"], logger=lambda m: print(f"[Launcher] {m}"))

        self._retry_at = 0.0
        self.watchdog = QTimer()
        self.watchdog.setInterval(3000)
        self.watchdog.timeout.connect(self.check_state)

        if self.duplicate:
            self._apply_status("error", "Another ULTRON launcher is already running.")
        else:
            self.tray_icon.show()
            self.check_state()
            self.watchdog.start()

    # ── settings ─────────────────────────────────────────────────────────
    def set_mode(self, mode: str) -> None:
        from memory import config_manager as cm
        cm.save_launcher_activation(mode)            # saved immediately, survives restarts
        self.settings["mode"] = mode
        self._sync_mode_menus()
        ok = self.listener.set_mode(mode)
        if ok and self.listener.state == "listening":
            self._show_message("ULTRON Auto-Launcher", f"Activation: {self.MODE_LABELS[mode].strip()}")

    def set_clap_count(self, count: int) -> None:
        from memory import config_manager as cm
        cm.save_launcher_clap_count(count)
        self.settings["clap_count"] = count
        self.listener.set_clap_count(count)
        self._sync_mode_menus()

    def set_sensitivity(self, level: str) -> None:
        from memory import config_manager as cm
        cm.save_clap_sensitivity(level)
        self.settings["sensitivity"] = level
        self.listener.set_sensitivity(level)
        self._sync_mode_menus()

    def _sync_mode_menus(self) -> None:
        mode = self.settings["mode"]
        for m, act in self.mode_actions.items():
            act.setChecked(m == mode)
        for n, act in self.clap_actions.items():
            act.setChecked(n == self.settings["clap_count"])
        for lv, act in self.sens_actions.items():
            act.setChecked(lv == self.settings["sensitivity"])
        # clap options only matter in clap mode
        self.clap_menu.setEnabled(mode == "clap")
        self.sens_menu.setEnabled(mode == "clap")

    # ── state machine (GUI thread, every 3 s) ────────────────────────────
    def check_state(self) -> None:
        if self.duplicate:
            return
        if self._ultron_running():
            # ULTRON is up: give it the microphone and save the CPU.
            self.listener.pause("Paused — ULTRON is running")
            return
        if self.listener.state == "paused" and time.monotonic() - self._launched_at < LAUNCH_COOLDOWN_S:
            return                                   # ULTRON is still starting
        if self.listener.state in ("stopped", "paused") or \
                (self.listener.state == "listening" and not self.listener.healthy()):
            if self.listener.state == "listening":
                self.listener.stop()                 # stream died (device unplugged)
            self.listener.start()
        elif self.listener.state == "error" and time.monotonic() >= self._retry_at:
            self._retry_at = time.monotonic() + 10.0  # retry the microphone every 10 s
            self.listener.stop()
            self.listener.start()

    # ── activation ───────────────────────────────────────────────────────
    def _on_activate(self, source: str) -> None:
        if self._ultron_running():
            self.listener.pause("Paused — ULTRON is running")
            return
        if time.monotonic() - self._launched_at < LAUNCH_COOLDOWN_S:
            return                                   # already launching
        self.activations.append(source)
        what = "Clap detected" if source == "clap" else f"Heard “{self.settings['name']}”"
        self._show_message("ULTRON Auto-Launcher", f"{what} — launching ULTRON…")
        self.launch_car()

    def launch_car(self, manual: bool = False) -> None:
        if self._ultron_running():
            self._show_message("ULTRON Auto-Launcher", "ULTRON is already running.")
            return
        self._launched_at = time.monotonic()
        self.listener.pause("Launching ULTRON…")       # release the mic for ULTRON
        try:
            self._launcher()
        except Exception as e:
            self._launched_at = 0.0
            self._show_message("ULTRON Auto-Launcher Error", f"Could not start ULTRON: {e}")

    # ── microphone test ──────────────────────────────────────────────────
    def test_microphone(self) -> None:
        if self.listener.state != "listening":
            self._show_message("Microphone test", f"The launcher is not listening: {self.status_text}")
            return
        self.listener.take_levels()
        claps_before = self.listener.clap.claps_detected
        self._show_message("Microphone test", "Listening for 5 seconds — stay quiet, then clap twice.")

        def done():
            lv = self.listener.take_levels()
            claps = self.listener.clap.claps_detected - claps_before
            lines = [f"Background: {lv['floor_dbfs']} dBFS · loudest sound: {lv['peak_dbfs']} dBFS"]
            if lv["blocks"] == 0 or lv["peak_dbfs"] <= -119:
                lines.append("No audio arrived — the microphone is muted or blocked.")
            elif self.settings["mode"] == "clap":
                lines.append(f"Claps recognised: {claps}.")
                if claps == 0 and lv["peak_dbfs"] < lv["min_clap_dbfs"]:
                    lines.append("Your loudest sound was quieter than this sensitivity accepts — "
                                 "choose a higher Clap sensitivity.")
            self._show_message("Microphone test", "\n".join(lines))
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(5000, done)

    # ── UI helpers ───────────────────────────────────────────────────────
    def _apply_status(self, state: str, text: str) -> None:
        self.status_text = text
        prefix = {"listening": "●", "paused": "‖", "error": "⚠", "warning": "⚠"}.get(state, "•")
        self.status_action.setText(f"{prefix}  {text}"[:120])
        self.tray_icon.setToolTip(f"ULTRON Auto-Launcher\n{text}"[:127])
        if state in ("error", "warning"):
            self._show_message("ULTRON Auto-Launcher", text)

    def _show_message(self, title: str, text: str) -> None:
        try:
            print(f"[Launcher] {title}: {text}")
        except Exception:
            pass
        try:
            self.tray_icon.showMessage(title, text)
        except Exception:
            pass

    def quit_app(self) -> None:
        self.watchdog.stop()
        self.listener.stop()
        try:
            self.listener.clap.close()
        except Exception:
            pass
        if self._lock_file is not None and not self.duplicate:
            self._lock_file.unlock()
        if self._mutex:
            try:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self._mutex))
            except Exception:
                pass
            self._mutex = None
        self.app.quit()

    def run(self) -> int:
        if self.duplicate:
            print("[Launcher] Another ULTRON launcher is already running; exiting.")
            return 0
        return self.app.exec()


if __name__ == "__main__":
    CarLauncherApp().run()
