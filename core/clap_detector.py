"""
core/clap_detector.py — acoustic hand-clap detection for ULTRON and its launcher.

One detector, two users:
  • main.py (wake on clap): double clap wakes ULTRON, single clap puts it to sleep.
  • car_launcher.py (tray launcher): a burst of exactly N claps launches ULTRON.

WHY THIS WAS REWRITTEN
    The previous version judged a whole 64 ms microphone block at once with a
    fixed peak threshold of 12000 (about -9 dBFS). Three things followed:
      - A normal clap a metre or two from a desk or headset microphone peaks
        well below -9 dBFS, so it was never even considered.
      - A 5-10 ms clap inside a 64 ms block of quiet room has a very high
        crest factor; the "> 14 means keyboard click" rule rejected exactly
        the clean claps in a quiet room.
      - Peak / crest / zero-crossing over a whole block cannot tell a clap from
        a loud plosive in speech, so the checks were both too strict and too
        loose at once.
    Callbacks also ran on the real-time audio thread while holding a lock.

HOW A CLAP IS RECOGNISED (10 ms frames, decided ~130 ms after the onset)
    Onset contrast uses the loudest 2.5 ms inside a frame, because a clap is
    an impulse: averaged over 10 ms it sits ~17 dB below its own peak and
    would vanish under a fan or quiet talking.
    1. Loud      sample peak >= min_peak, and the 2.5 ms energy is >= rise_db
                 above the adaptive background level of the room.
    2. Sudden    >= attack_db above the energy 20-30 ms earlier.
    3. Bright    high-frequency share (first-difference energy / energy)
                 between min_brightness and max_brightness: claps are
                 broadband around 1-4 kHz; voices, knocks and thumps are darker,
                 keyboard clicks brighter.
    4. Short     at most max_width_frames within 9 dB of the peak, and
                 40-120 ms later the energy has fallen by >= decay_db.
                 Speech syllables, music notes and coughs are sustained.
                 Effective duration >= 0.35 ms rejects sub-millisecond clicks.
    5. Alone     the preceding ~100 ms were >= isolation_db quieter, so a
                 transient inside continuous sound (music, talking) is ignored.
    6. Context   other sudden sounds that were judged NOT to be claps (key
                 clicks, drum hits, plosives) taint the burst they fall in:
                 0.6 s before its first clap up to its end. Typing, music and
                 talking produce a stream of such sounds; a deliberate clap in a
                 calm moment does not. A tainted burst fires nothing.

PATTERNS
    Impulses closer than min_gap (0.12 s) are one clap (echo). Claps up to
    max_gap (0.8 s) apart form a burst. Events:
      on_clap(info)        every recognised clap (meters, tests)
      on_double_clap()     immediately on the 2nd clap of a burst
      on_single_clap()     when a burst ends with exactly one clap
      on_pattern(n)        when a burst ends with exactly `claps_required`
                           claps (exact, so applause or a drum fill with more
                           impulses does not count)
    Timing uses the audio stream's own sample clock, not wall time, so callback
    jitter cannot stretch or shrink a gap. Callbacks run on one dispatcher
    thread, never on the audio thread.
"""
from __future__ import annotations

import queue
import threading
from typing import Callable, Optional

import numpy as np

# Sensitivity presets. min_peak is in int16 units (32767 = 0 dBFS).
SENSITIVITY: dict[str, dict] = {
    "low":    {"min_peak": 5000, "rise_db": 18.0, "attack_db": 12.0},   # about -16 dBFS
    "medium": {"min_peak": 1600, "rise_db": 15.0, "attack_db": 10.0},   # about -26 dBFS
    "high":   {"min_peak": 600,  "rise_db": 12.0, "attack_db": 8.0},    # about -35 dBFS
}
DEFAULT_SENSITIVITY = "medium"

_EPS = 1e-9
_MIN_FLOOR_DB = 26.0          # int16 RMS 20, about -64 dBFS: digital-silence guard


def _db(x: float) -> float:
    return 10.0 * float(np.log10(x + _EPS))


class ClapDetector:
    def __init__(
        self,
        on_single_clap: Optional[Callable[[], None]] = None,
        on_double_clap: Optional[Callable[[], None]] = None,
        threshold: Optional[int] = None,
        min_crest: Optional[float] = None,      # accepted for compatibility; unused
        min_zcr: Optional[int] = None,          # accepted for compatibility; unused
        enabled: bool = True,
        logger: Optional[Callable[[str], None]] = None,
        *,
        sample_rate: int = 16000,
        sensitivity: str = DEFAULT_SENSITIVITY,
        claps_required: int = 2,
        on_pattern: Optional[Callable[[int], None]] = None,
        on_clap: Optional[Callable[[dict], None]] = None,
        min_gap: float = 0.12,
        max_gap: float = 0.8,
        min_brightness: float = 0.30,
        max_brightness: float = 2.0,
        min_duration_ms: float = 0.35,
        isolation_db: float = 12.0,
        decay_db: float = 12.0,
        max_width_frames: int = 3,
        context_s: float = 0.6,
    ):
        self.on_single_clap = on_single_clap
        self.on_double_clap = on_double_clap
        self.on_pattern = on_pattern
        self.on_clap = on_clap
        self.enabled = enabled
        self._logger = logger or (lambda m: None)

        self.sample_rate = int(sample_rate)
        self.frame = max(32, self.sample_rate // 100)          # 10 ms
        self.claps_required = max(1, int(claps_required))
        self.min_gap, self.max_gap = float(min_gap), float(max_gap)
        self.min_brightness = float(min_brightness)
        self.max_brightness = float(max_brightness)
        self.min_duration = float(min_duration_ms) * self.sample_rate / 1000.0
        self.isolation_db = float(isolation_db)
        self.decay_db = float(decay_db)
        self.max_width_frames = int(max_width_frames)
        self.context_s = float(context_s)
        self.set_sensitivity(sensitivity)
        if threshold:                                            # explicit override
            self.min_peak = int(threshold)

        self._lock = threading.Lock()
        self._events: "queue.Queue" = queue.Queue()
        self._dispatcher: Optional[threading.Thread] = None
        self.reset()

    # ── configuration ────────────────────────────────────────────────────
    def set_sensitivity(self, name: str) -> None:
        preset = SENSITIVITY.get((name or "").lower(), SENSITIVITY[DEFAULT_SENSITIVITY])
        self.sensitivity = (name or DEFAULT_SENSITIVITY).lower() if (name or "").lower() in SENSITIVITY \
            else DEFAULT_SENSITIVITY
        self.min_peak = int(preset["min_peak"])
        self.rise_db = float(preset["rise_db"])
        self.attack_db = float(preset["attack_db"])

    @property
    def threshold(self) -> int:
        """Compatibility alias for the minimum clap peak (int16 units)."""
        return self.min_peak

    @threshold.setter
    def threshold(self, value: int) -> None:
        self.min_peak = int(value)

    def reset(self) -> None:
        """Forget all audio history and any burst in progress."""
        with getattr(self, "_lock", threading.Lock()):
            self._buf = np.zeros(0, dtype=np.float32)
            self._n = 0                          # frames processed (absolute index)
            self._E: list[float] = []            # per-frame energy (mean square)
            self._S: list[float] = []            # per-frame max 2.5 ms energy
            self._B: list[float] = []            # per-frame brightness
            self._P: list[float] = []            # per-frame absolute peak
            self._base = 0                       # absolute index of _E[0]
            self._floor_db: Optional[float] = None
            self._floor_hist: list[float] = []   # floor at each frame (dB)
            self._pending: list[int] = []        # candidate onset frames awaiting decay
            self._last_clap_t = -1e9
            self._burst: list[float] = []        # clap times (s, stream clock) in burst
            self._burst_fired = False            # double already reported this burst
            self._noise_t: list[float] = []      # times of non-clap impulsive sounds
            self.bursts_rejected = 0
            self.claps_detected = 0
            self.last_peak_dbfs = -120.0
            self.peak_dbfs_window = -120.0

    # ── audio input (real-time audio thread) ─────────────────────────────
    def feed(self, indata) -> bool:
        """Process a block of microphone samples (int16 or float, any length).
        Cheap enough for the audio callback. Returns True if a clap was
        recognised while processing this block."""
        if not self.enabled:
            return False
        x = np.asarray(indata)
        if x.ndim > 1:
            x = x[:, 0]
        if np.issubdtype(x.dtype, np.floating):
            x = x.astype(np.float32) * 32768.0
        else:
            x = x.astype(np.float32)
        with self._lock:
            return self._process(x)

    def _process(self, x: np.ndarray) -> bool:
        buf = np.concatenate((self._buf, x)) if self._buf.size else x
        F = self.frame
        n = buf.size // F
        if n == 0:
            self._buf = buf
            return False
        X = buf[: n * F].reshape(n, F)
        self._buf = buf[n * F:].copy()
        X2 = X * X
        E = np.mean(X2, axis=1)
        q = F // 4
        S = np.max(np.mean(X2[:, : q * 4].reshape(n, 4, q), axis=2), axis=1)
        D = np.diff(X, axis=1)
        B = np.mean(D * D, axis=1) / (E + _EPS)
        P = np.max(np.abs(X), axis=1)
        found = False
        for k in range(n):
            self._push(float(E[k]), float(S[k]), float(B[k]), float(P[k]))
            if self._step():
                found = True
        self._check_burst_end()
        self._trim()
        return found

    def _push(self, e: float, s: float, b: float, p: float) -> None:
        self._E.append(e); self._S.append(s); self._B.append(b); self._P.append(p)
        self.peak_dbfs_window = max(self.peak_dbfs_window, 20 * float(np.log10(p / 32768.0 + _EPS)))
        level = max(_db(e), _MIN_FLOOR_DB)
        if self._floor_db is None:
            self._floor_db = level
        elif level < self._floor_db:
            self._floor_db += (level - self._floor_db) * 0.2      # quiet: follow fast
        else:
            self._floor_db += min(level - self._floor_db, 0.05)    # loud: rise <= 5 dB/s
        self._floor_hist.append(self._floor_db)
        self._n += 1

    def _e(self, i: int) -> float:
        j = i - self._base
        return self._E[j] if 0 <= j < len(self._E) else 0.0

    def _s(self, i: int) -> float:
        j = i - self._base
        return self._S[j] if 0 <= j < len(self._S) else 0.0

    def _step(self) -> bool:
        """New frame appended: maybe open a candidate, maybe decide one."""
        i = self._n - 1
        # 1) onset candidate at frame i
        s = self._s(i)
        floor_db = self._floor_hist[i - self._base - 1] if i - self._base >= 1 else self._floor_db
        ref = max(self._e(i - 2), self._e(i - 3))
        if (not self._pending or i - self._pending[-1] > 3) \
                and _db(s) >= floor_db + self.rise_db \
                and _db(s) >= _db(ref) + self.attack_db:
            self._pending.append(i)
        # 2) decide candidates whose look-ahead window is complete
        found = False
        while self._pending and self._n - 1 >= self._pending[0] + 13:
            c = self._pending.pop(0)
            reason = self._reject_reason(c)
            if reason is None:
                found = True
            elif reason != "echo":
                self._noise_t.append(c * self.frame / self.sample_rate)
                del self._noise_t[:-64]
        return found

    def _is_clap(self, c: int) -> bool:
        return self._reject_reason(c) is None

    def _reject_reason(self, c: int) -> Optional[str]:
        """None when frame c starts a clap (and registers it), else why not."""
        p = c if self._s(c) >= self._s(c + 1) else c + 1
        sp = self._s(p)
        j = p - self._base
        peak = max(self._P[c - self._base], self._P[p - self._base])
        if peak < self.min_peak:
            return "quiet"
        if not (self.min_brightness <= self._B[j] <= self.max_brightness):
            return "spectrum"
        pre = np.mean([self._e(k) for k in range(c - 12, c - 1)])
        floor_lin = 10 ** (self._floor_hist[max(0, c - self._base - 1)] / 10.0)
        if _db(sp) < _db(max(pre, floor_lin)) + self.isolation_db:
            return "not isolated"
        post = np.mean([self._e(k) for k in range(p + 4, p + 12)])
        if _db(sp) < _db(post) + self.decay_db:
            return "sustained"
        width = sum(1 for k in range(c - 1, p + 7) if _db(self._s(k)) >= _db(sp) - 9.0)
        if width > self.max_width_frames:
            return "too long"
        duration = (self._e(p) + self._e(p + 1)) * self.frame / (peak * peak + _EPS)
        if duration < self.min_duration:
            return "click"
        t = p * self.frame / self.sample_rate                     # stream clock
        if t - self._last_clap_t < self.min_gap:
            return "echo"                                         # echo of the same clap
        self._register_clap(t, peak, sp)
        return None

    # ── patterns ─────────────────────────────────────────────────────────
    def _register_clap(self, t: float, peak: float, energy: float) -> None:
        if self._burst and t - self._burst[-1] > self.max_gap:
            self._finish_burst()
        self._last_clap_t = t
        self._burst.append(t)
        self.claps_detected += 1
        self.last_peak_dbfs = 20 * float(np.log10(peak / 32768.0 + _EPS))
        info = {"t": t, "peak_dbfs": round(self.last_peak_dbfs, 1),
                "over_floor_db": round(_db(energy) - (self._floor_db or 0.0), 1),
                "index_in_burst": len(self._burst)}
        self._emit("clap", info)
        if len(self._burst) == 2 and not self._burst_fired:
            self._burst_fired = True
            if not self._busy(self._burst[0] - self.context_s, t):
                self._emit("double", None)

    def _check_burst_end(self) -> None:
        now = self._n * self.frame / self.sample_rate
        # decisions lag the audio by the 130 ms look-ahead
        if self._burst and now - 0.13 - self._burst[-1] > self.max_gap and not self._pending:
            self._finish_burst()

    def _busy(self, start: float, end: float) -> bool:
        return any(start <= t <= end for t in self._noise_t)

    def _finish_burst(self) -> None:
        count, burst = len(self._burst), self._burst
        self._burst, self._burst_fired = [], False
        if self._busy(burst[0] - self.context_s, burst[-1] + self.max_gap):
            self.bursts_rejected += 1
            self._emit("busy", count)
            return
        if count == 1:
            self._emit("single", None)
        if count == self.claps_required:
            self._emit("pattern", count)
        self._emit("burst", count)

    def _trim(self) -> None:
        keep = 48
        extra = len(self._E) - keep
        if extra > 0:
            del self._E[:extra], self._S[:extra], self._B[:extra], self._P[:extra], self._floor_hist[:extra]
            self._base += extra

    # ── callback dispatch (never on the audio thread) ────────────────────
    def _emit(self, kind: str, arg) -> None:
        if self._dispatcher is None or not self._dispatcher.is_alive():
            self._dispatcher = threading.Thread(target=self._dispatch_loop, daemon=True,
                                                name="ClapDispatch")
            self._dispatcher.start()
        self._events.put((kind, arg))

    def _dispatch_loop(self) -> None:
        while True:
            kind, arg = self._events.get()
            if kind == "stop":
                return
            if kind == "mark":
                arg.set()
                continue
            try:
                if kind == "clap" and self.on_clap:
                    self.on_clap(arg)
                elif kind == "double" and self.on_double_clap:
                    self._logger("👏👏 Double clap detected")
                    self.on_double_clap()
                elif kind == "single" and self.on_single_clap:
                    self._logger("👏 Single clap detected")
                    self.on_single_clap()
                elif kind == "pattern" and self.on_pattern:
                    self._logger(f"👏 x{arg} clap pattern detected")
                    self.on_pattern(arg)
            except Exception as e:                                # a callback must never kill us
                self._logger(f"Clap callback error ({kind}): {e}")

    def wait_idle(self, timeout: float = 2.0) -> None:
        """Block until queued callbacks have run (tests and shutdown)."""
        done = threading.Event()
        self._emit("mark", done)
        done.wait(timeout)

    def close(self) -> None:
        self.enabled = False
        if self._dispatcher is not None and self._dispatcher.is_alive():
            self._events.put(("stop", None))

    # ── diagnostics ──────────────────────────────────────────────────────
    def levels(self, reset_peak: bool = True) -> dict:
        """Background level and loudest peak since the last call, in dBFS."""
        with self._lock:
            floor = (self._floor_db or _MIN_FLOOR_DB) - 10 * np.log10(32768.0 ** 2)
            out = {"floor_dbfs": round(float(floor), 1), "peak_dbfs": round(self.peak_dbfs_window, 1),
                   "claps": self.claps_detected, "min_peak_dbfs": round(20 * np.log10(self.min_peak / 32768.0), 1)}
            if reset_peak:
                self.peak_dbfs_window = -120.0
            return out
