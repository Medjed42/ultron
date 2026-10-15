"""
core/cinematic/sound.py — ULTRON's cinematic audio manager.

Every effect is addressed by its sound ID — the names of the ULTRON cinematic
pack (01_core_wakeup … 13_digital_glitch) — and resolved, once, to the best
file available:

    1. assets/audio/sfx/<id>.wav            the pack's own recording, if present
    2. assets/audio/sfx/v2/<file>.wav       the closest match from the installed
                                            ULTRON V2 pack (see V2_MATCHES)
    3. a synthesised stand-in               sines, sweeps and filtered noise,
                                            written once to the temp folder

so dropping the real pack into assets/audio/sfx upgrades every cinematic with no
code change, and a missing file never silences one.

Loudness is capped three times: clips are peak-limited, the user's effects
volume (default 35 %) is clamped to 60 %, and the priority rules duck effects
under speech — the welcome narration silences them, ULTRON's live voice halves
them. Nothing here is ever fatal: no numpy, no QtMultimedia, or sound switched
off, and play() is a silent no-op.
"""
from __future__ import annotations

import tempfile
import threading
import time
import wave
from pathlib import Path

from PyQt6.QtCore import QObject, QUrl, pyqtSignal

try:
    import numpy as np
except Exception:          # pragma: no cover — numpy is in requirements
    np = None

try:
    from PyQt6.QtMultimedia import QSoundEffect
except Exception:          # stripped-down Qt install: no sound, never fatal
    QSoundEffect = None

SR = 44100
VERSION = 2
CACHE_DIR = Path(tempfile.gettempdir()) / f"ultron_cinematic_sfx_v{VERSION}"
ASSET_DIR = Path(__file__).resolve().parents[2] / "assets" / "audio" / "sfx"
DEFAULT_VOLUME = 0.35
MAX_VOLUME = 0.60

SOUND_IDS = (
    "01_core_wakeup", "02_electric_spark", "03_energy_charge", "04_lightning_strike",
    "05_screen_fracture", "06_holographic_shatter", "07_system_scan",
    "08_color_transform_whoosh", "09_shutdown_electric_slash", "10_power_down",
    "11_system_online_chime", "12_ui_confirm_tick", "13_digital_glitch",
)

# Sounds of the installed ULTRON V2 pack that genuinely fit an ID (by the
# pack's own descriptions). Everything else is synthesised until the real file
# is dropped in.
V2_MATCHES = {
    "01_core_wakeup": "16_system_boot.wav",            # "signature cinematic startup"
    "07_system_scan": "06_graph_focus.wav",            # "energy sweep"
    "11_system_online_chime": "07_provider_connected.wav",   # "ascending success"
    "12_ui_confirm_tick": "02_ui_click.wav",           # "mechanical-digital press"
}


# ── DSP helpers ───────────────────────────────────────────────────────────────

def _t(dur: float):
    return np.arange(int(dur * SR), dtype=np.float64) / SR


def _lowpass(x, fc: float):
    """One-pole low-pass as a convolution with its (truncated) impulse response
    — vectorised, so no per-sample Python loop."""
    tau = 1.0 / (2 * np.pi * max(10.0, fc))
    n = max(2, int(tau * SR * 5))
    k = np.exp(-np.arange(n) / (tau * SR))
    k /= k.sum()
    return np.convolve(x, k)[: len(x)]


def _highpass(x, fc: float):
    return x - _lowpass(x, fc)


def _sweep(f0: float, f1: float, dur: float, n: int | None = None):
    t = _t(dur) if n is None else np.arange(n) / SR
    f = f0 * (f1 / f0) ** np.clip(t / max(1e-6, dur), 0, 1)
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def _env(n: int, attack: float, decay_tau: float):
    t = np.arange(n) / SR
    a = np.clip(t / max(1e-4, attack), 0, 1)
    return a * np.exp(-np.maximum(0, t - attack) / max(1e-4, decay_tau))


def _fade(x, fin: float = 0.004, fout: float = 0.03):
    n = len(x)
    i, o = min(n, int(fin * SR)), min(n, int(fout * SR))
    if i:
        x[:i] *= np.linspace(0, 1, i)
    if o:
        x[-o:] *= np.linspace(1, 0, o)
    return x


def _finish(x, peak: float = 0.5):
    x = np.tanh(x * 1.2)                       # soft limiter — no hard clipping
    m = float(np.max(np.abs(x))) or 1.0
    return _fade(x / m * peak).astype(np.float32)


def _crackle(n: int, density, rng, bright: float = 3000.0):
    """Sparse electrical discharges; `density` is impulses/second (scalar or
    per-sample array)."""
    dens = np.broadcast_to(np.asarray(density, dtype=np.float64), (n,))
    hits = rng.random(n) < dens / SR
    imp = np.where(hits, rng.uniform(-1, 1, n), 0.0)
    k = np.exp(-np.arange(int(0.004 * SR)) / (0.0009 * SR))
    burst = np.convolve(imp, k)[:n] * rng.uniform(-1, 1, n)
    return _highpass(burst, bright)


# ── The effects ───────────────────────────────────────────────────────────────

def _boot(rng):
    t = _t(2.6); n = len(t)
    rise = np.clip(t / 2.1, 0, 1) ** 2
    drone = (0.55 * np.sin(2 * np.pi * 55 * t) + 0.32 * np.sin(2 * np.pi * 110.6 * t)
             + 0.16 * np.sin(2 * np.pi * 166.2 * t))
    sweep = _sweep(160, 900, 2.4, n) * 0.10 * rise
    crack = _crackle(n, 4 + 90 * rise, rng) * 0.9 * (0.3 + rise)
    blips = np.zeros(n)
    for at, f in ((0.45, 1320), (0.78, 1760), (1.05, 1480), (1.28, 2093), (1.5, 1760), (1.66, 2637)):
        i = int(at * SR); m = int(0.035 * SR)
        if i + m < n:
            seg = np.sign(np.sin(2 * np.pi * f * np.arange(m) / SR)) * np.exp(-np.arange(m) / (0.012 * SR))
            blips[i:i + m] += _lowpass(seg, 5000) * 0.18
    x = drone * (0.15 + 0.85 * rise) * 0.55 + sweep + crack + blips
    x *= np.clip((2.6 - t) / 0.35, 0, 1)
    return _finish(x, 0.42)


def _strike(rng):
    t = _t(1.9); n = len(t)
    noise = rng.uniform(-1, 1, n)
    crack = _highpass(noise, 1800) * np.exp(-t / 0.045)
    body = _lowpass(noise, 900) * np.exp(-t / 0.22) * 1.6
    brown = np.cumsum(rng.uniform(-1, 1, n)); brown = _highpass(brown / (np.max(np.abs(brown)) or 1), 25)
    rumble = _lowpass(brown, 140) * np.exp(-t / 0.7) * 3.0
    boom = np.sin(2 * np.pi * np.cumsum(62 * (35 / 62) ** np.clip(t / 0.6, 0, 1)) / SR) * np.exp(-t / 0.5)
    saw = ((t * 98) % 1.0 - 0.5) * 2
    buzz = _lowpass(saw, 2400) * np.exp(-t / 0.28) * 0.35 * (0.6 + 0.4 * rng.random(n))
    x = crack * 1.1 + body + rumble + boom * 0.9 + buzz + _crackle(n, 260 * np.exp(-t / 0.4), rng) * 0.7
    return _finish(x, 0.5)


def _tick(rng):
    t = _t(0.06)
    x = (np.sin(2 * np.pi * 2600 * t) + 0.35 * np.sin(2 * np.pi * 5200 * t)) * np.exp(-t / 0.009)
    return _finish(x, 0.22)


def _zap(rng):
    t = _t(0.28); n = len(t)
    x = _sweep(3800, 600, 0.22, n) * np.exp(-t / 0.07)
    x += _highpass(rng.uniform(-1, 1, n), 2500) * np.exp(-t / 0.03) * 0.8
    x += _crackle(n, 500 * np.exp(-t / 0.08), rng) * 0.6
    return _finish(x, 0.34)


def _whoosh(rng):
    t = _t(0.55); n = len(t)
    env = np.sin(np.pi * np.clip(t / 0.55, 0, 1)) ** 2
    air = _lowpass(_highpass(rng.uniform(-1, 1, n), 500), 3500) * env
    x = air * 1.4 + _sweep(260, 1400, 0.5, n) * env * 0.25 + _crackle(n, 300 * env, rng) * 0.5
    return _finish(x, 0.36)


def _online(rng):
    t = _t(2.0); n = len(t)
    chord = sum(a * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28))
                for f, a in ((110, 0.5), (164.8, 0.32), (220, 0.34), (261.6, 0.2), (329.6, 0.18), (440, 0.08)))
    env = np.clip(t / 0.06, 0, 1) * np.exp(-t / 0.9)
    shimmer = np.sin(2 * np.pi * 1760 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 9 * t)) * np.exp(-t / 0.5) * 0.06
    thump = np.sin(2 * np.pi * np.cumsum(90 * (40 / 90) ** np.clip(t / 0.25, 0, 1)) / SR) * np.exp(-t / 0.18)
    x = chord * env * 0.6 + shimmer + thump * 0.8 + _crackle(n, 120 * np.exp(-t / 0.3), rng) * 0.4
    return _finish(x, 0.42)




def _slash(rng):
    t = _t(0.85); n = len(t)
    x = _sweep(5200, 240, 0.6, n) * np.exp(-t / 0.22)
    x += _highpass(rng.uniform(-1, 1, n), 3000) * np.exp(-t / 0.05) * 1.2
    x += _lowpass(rng.uniform(-1, 1, n), 500) * np.exp(-t / 0.25) * 1.2
    x += _crackle(n, 700 * np.exp(-t / 0.3), rng)
    return _finish(x, 0.5)




def _power_off(rng):
    t = _t(2.3); n = len(t)
    s = _sweep(520, 36, 2.1, n)
    x = (s + 0.12 * np.sign(s)) * np.exp(-t / 1.0) * np.clip(t / 0.02, 0, 1)   # a little square-wave grit
    x += _lowpass(rng.uniform(-1, 1, n), 300) * np.exp(-t / 0.6) * 0.3
    x = _lowpass(x, 3000)
    return _finish(x, 0.42)


def _charge(rng):
    """Energy building: a rising hum, a climbing whine and thickening crackle."""
    t = _t(1.4); n = len(t)
    rise = np.clip(t / 1.25, 0, 1) ** 1.6
    hum = (np.sin(2 * np.pi * 70 * t) + 0.5 * np.sin(2 * np.pi * 140.5 * t)) * (0.2 + 0.8 * rise)
    whine = _sweep(220, 1900, 1.3, n) * 0.22 * rise
    x = hum * 0.5 + whine + _crackle(n, 20 + 520 * rise, rng) * (0.4 + rise)
    x *= np.clip((1.4 - t) / 0.12, 0, 1)
    return _finish(x, 0.42)


def _fracture(rng):
    """Glass under load giving way: a sharp crack, ringing partials, a dull thud."""
    t = _t(0.9); n = len(t)
    crack = _highpass(rng.uniform(-1, 1, n), 2600) * np.exp(-t / 0.012) * 1.6
    rings = sum(a * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28)) * np.exp(-t / d)
                for f, a, d in ((2093, 0.35, 0.25), (3136, 0.28, 0.18), (4699, 0.2, 0.12), (1567, 0.22, 0.3)))
    thud = np.sin(2 * np.pi * np.cumsum(140 * (60 / 140) ** np.clip(t / 0.15, 0, 1)) / SR) * np.exp(-t / 0.09)
    splinter = _crackle(n, 900 * np.exp(-t / 0.15), rng, bright=4500) * 0.8
    return _finish(crack + rings * 0.5 + thud * 0.7 + splinter, 0.46)


def _shatter(rng):
    """Holographic glass bursting: a noise burst, then dozens of bright shards
    ringing out and scattering."""
    t = _t(1.6); n = len(t)
    burst = _highpass(rng.uniform(-1, 1, n), 1500) * np.exp(-t / 0.06) * 1.3
    tinkle = np.zeros(n)
    for _ in range(46):
        at = rng.uniform(0.0, 0.9) ** 1.8
        i = int(at * SR)
        m = int(rng.uniform(0.08, 0.35) * SR)
        if i + m >= n:
            continue
        f = rng.uniform(2200, 7800)
        k = np.arange(m) / SR
        tinkle[i:i + m] += np.sin(2 * np.pi * f * k) * np.exp(-k / rng.uniform(0.03, 0.12)) * rng.uniform(0.15, 0.5)
    body = _lowpass(rng.uniform(-1, 1, n), 700) * np.exp(-t / 0.25) * 0.8
    shimmer = _sweep(5200, 1800, 1.4, n) * np.exp(-t / 0.5) * 0.08
    return _finish(burst + tinkle + body + shimmer, 0.48)


def _glitch(rng):
    """Digital interference: a bit-crushed buzz chopped into stutters."""
    t = _t(0.45); n = len(t)
    base = np.sign(np.sin(2 * np.pi * 180 * t)) * 0.5 + rng.uniform(-1, 1, n) * 0.4
    crushed = np.round(base * 4) / 4
    gate = (np.floor(t / 0.028) % 3 != 1).astype(float)
    tone = np.sin(2 * np.pi * np.where((t // 0.06) % 2 == 0, 1200, 760) * t) * 0.3
    x = (crushed + tone) * gate * np.clip((0.45 - t) / 0.08, 0, 1)
    return _finish(_lowpass(x, 6000), 0.3)


def _scan(rng):
    """A terminal scan: a soft sweep with a ladder of data blips."""
    t = _t(1.1); n = len(t)
    env = np.sin(np.pi * np.clip(t / 1.1, 0, 1))
    sweep = _sweep(600, 2600, 1.0, n) * env * 0.12
    blips = np.zeros(n)
    for k in range(9):
        i = int((0.05 + k * 0.11) * SR); m = int(0.03 * SR)
        if i + m < n:
            blips[i:i + m] += np.sin(2 * np.pi * (1400 + 160 * k) * np.arange(m) / SR) * np.exp(-np.arange(m) / (0.01 * SR))
    return _finish(sweep + blips * 0.4, 0.26)


SYNTHS = {
    "01_core_wakeup": _boot,
    "02_electric_spark": _zap,
    "03_energy_charge": _charge,
    "04_lightning_strike": _strike,
    "05_screen_fracture": _fracture,
    "06_holographic_shatter": _shatter,
    "07_system_scan": _scan,
    "08_color_transform_whoosh": _whoosh,
    "09_shutdown_electric_slash": _slash,
    "10_power_down": _power_off,
    "11_system_online_chime": _online,
    "12_ui_confirm_tick": _tick,
    "13_digital_glitch": _glitch,
}


def _write_wav(path: Path, x) -> None:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)
    tmp.replace(path)


def _usable(p: Path) -> bool:
    try:
        return p.is_file() and p.stat().st_size > 44
    except OSError:
        return False


def resolve_sources(asset_dir: Path = ASSET_DIR) -> dict[str, tuple[str, Path | None]]:
    """For every ID: ("pack", path) | ("v2", path) | ("synth", cached path or None)."""
    out: dict[str, tuple[str, Path | None]] = {}
    for sid in SOUND_IDS:
        own = asset_dir / f"{sid}.wav"
        v2 = asset_dir / "v2" / V2_MATCHES[sid] if sid in V2_MATCHES else None
        syn = CACHE_DIR / f"{sid}.wav"
        if _usable(own):
            out[sid] = ("pack", own)
        elif v2 is not None and _usable(v2):
            out[sid] = ("v2", v2)
        else:
            out[sid] = ("synth", syn if _usable(syn) else None)
    return out


def generate_missing(sources: dict) -> dict[str, tuple[str, Path]]:
    """Synthesise every stand-in that is not cached yet."""
    if np is None:
        raise RuntimeError("numpy unavailable")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)          # the same sounds every time
    out = dict(sources)
    for sid, (kind, path) in sources.items():
        if kind == "synth" and path is None:
            p = CACHE_DIR / f"{sid}.wav"
            _write_wav(p, SYNTHS[sid](rng))
            out[sid] = ("synth", p)
    return out


class AudioManager(QObject):
    """Plays the cinematic effects by ID, under the speech-priority rules.

    Construct it early: stand-ins are synthesised once on a background thread
    (well under a second), and every effect is preloaded so a cue lands on the
    frame it is meant for."""

    _ready = pyqtSignal(object)

    THROTTLE = 0.05          # the same effect never restarts within 50 ms

    def __init__(self, enabled: bool = True, volume: float = DEFAULT_VOLUME, parent=None,
                 asset_dir: Path = ASSET_DIR):
        super().__init__(parent)
        self.available = QSoundEffect is not None
        self.enabled = bool(enabled) and self.available
        self._volume = max(0.0, min(1.0, float(volume)))
        self._asset_dir = asset_dir
        self._fx: dict[str, object] = {}
        self._sources: dict[str, tuple[str, Path | None]] = {}
        self._ducks: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._ready.connect(self._load)
        if self.available:
            self._prepare()

    # ── loading ─────────────────────────────────────────────────────────────
    def _prepare(self) -> None:
        sources = resolve_sources(self._asset_dir)
        missing = [s for s, (k, p) in sources.items() if k == "synth" and p is None]
        if missing and np is not None:
            threading.Thread(target=self._synth, args=(sources,), daemon=True,
                             name="cinematic-sfx").start()
        else:
            self._load(sources)

    def _synth(self, sources) -> None:
        try:
            self._ready.emit(generate_missing(sources))
        except Exception as e:      # sound is decoration — never fatal
            print(f"[Audio] sound synthesis unavailable: {e}")
            self._ready.emit(sources)

    def _load(self, sources: dict) -> None:
        self._sources = sources
        for sid, (_kind, path) in sources.items():
            if path is None or QSoundEffect is None:
                continue
            try:
                e = QSoundEffect(self)
                e.setSource(QUrl.fromLocalFile(str(path)))
                e.setVolume(self._effective(1.0))
                self._fx[sid] = e
            except Exception:
                pass
        used = {}
        for sid, (kind, _p) in sources.items():
            used.setdefault(kind, []).append(sid[:2])
        print("[Audio] effects: " + "  ".join(f"{k}={','.join(v)}" for k, v in sorted(used.items())))

    def sources(self) -> dict[str, tuple[str, str]]:
        return {sid: (kind, str(p) if p else "") for sid, (kind, p) in self._sources.items()}

    # ── levels ──────────────────────────────────────────────────────────────
    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on) and self.available
        if not self.enabled:
            self.stop_all()

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, float(volume)))

    @property
    def volume(self) -> float:
        return self._volume

    def duck(self, reason: str, factor: float) -> None:
        """Hold effects at `factor` of their level while `reason` lasts."""
        self._ducks[reason] = max(0.0, min(1.0, float(factor)))

    def unduck(self, reason: str) -> None:
        self._ducks.pop(reason, None)

    def _effective(self, gain: float) -> float:
        duck = min(self._ducks.values()) if self._ducks else 1.0
        return max(0.0, min(MAX_VOLUME, self._volume * gain * duck))

    # ── playback ────────────────────────────────────────────────────────────
    def play(self, sid: str, gain: float = 1.0) -> bool:
        if not self.enabled:
            return False
        e = self._fx.get(sid)
        if e is None:
            return False
        level = self._effective(gain)
        if level <= 0.001:
            return False
        now = time.monotonic()
        if now - self._last.get(sid, 0.0) < self.THROTTLE:
            return False
        self._last[sid] = now
        try:
            e.setVolume(level)
            e.play()
            return True
        except Exception:
            return False

    def stop_all(self) -> None:
        for e in self._fx.values():
            try:
                e.stop()
            except Exception:
                pass

    def release(self) -> None:
        """Stop and free every effect (application exit)."""
        self.stop_all()
        for e in self._fx.values():
            try:
                e.deleteLater()
            except Exception:
                pass
        self._fx.clear()


SfxPlayer = AudioManager       # the name the first version of the cinematics used
