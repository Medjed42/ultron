"""
tests/audio_fixtures.py — synthetic and synthesised audio for clap / wake tests.

Claps follow the usual physical model of a hand clap: a broadband noise burst
shaped by the resonance of the cupped hands (roughly 0.8-2.6 kHz), a sub-
millisecond attack, a 3-9 ms exponential decay, and a room reverb tail 15-25 dB
down. Negatives are built to be the hard cases: voiced speech with plosive
bursts, music with drums, keyboard clicks, door knocks, a cough, fan noise.

windows_tts() renders real speech with the Windows speech synthesiser (SAPI)
at 16 kHz mono, so the name-calling path can be tested on genuine speech.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

SR = 16000


def _bandpass(x: np.ndarray, f0: float, q: float, sr: int = SR) -> np.ndarray:
    """Biquad band-pass (RBJ cookbook), constant 0 dB peak gain."""
    w0 = 2 * np.pi * f0 / sr
    alpha = np.sin(w0) / (2 * q)
    b0, b1, b2 = alpha, 0.0, -alpha
    a0, a1, a2 = 1 + alpha, -2 * np.cos(w0), 1 - alpha
    b = np.array([b0, b1, b2]) / a0
    a = np.array([1.0, a1 / a0, a2 / a0])
    y = np.zeros_like(x)
    x1 = x2 = y1 = y2 = 0.0
    for n in range(len(x)):
        xn = x[n]
        yn = b[0] * xn + b[1] * x1 + b[2] * x2 - a[1] * y1 - a[2] * y2
        x2, x1, y2, y1 = x1, xn, y1, yn
        y[n] = yn
    return y


def silence(seconds: float, noise_dbfs: float = -62.0, rng=None) -> np.ndarray:
    rng = rng or np.random.default_rng(0)
    n = int(seconds * SR)
    return rng.standard_normal(n) * (32768 * 10 ** (noise_dbfs / 20))


def clap(peak_dbfs: float = -18.0, rng=None, center_hz=None, tau_ms=None,
         reverb_db=None, rt60=None) -> np.ndarray:
    rng = rng or np.random.default_rng(1)
    center_hz = center_hz or rng.uniform(800, 2600)
    tau = (tau_ms or rng.uniform(3.0, 9.0)) / 1000.0
    reverb_db = reverb_db if reverb_db is not None else rng.uniform(-25, -15)
    rt60 = rt60 or rng.uniform(0.25, 0.6)
    n = int(0.5 * SR)
    t = np.arange(n) / SR
    burst = rng.standard_normal(n) * np.exp(-t / tau)
    body = _bandpass(burst, center_hz, rng.uniform(1.2, 3.0)) + 0.35 * burst
    tail = rng.standard_normal(n) * np.exp(-t * 6.9 / rt60) * 10 ** (reverb_db / 20)
    tail[: int(0.004 * SR)] = 0
    y = body + _bandpass(tail, center_hz, 0.9) * np.max(np.abs(body))
    return y / (np.max(np.abs(y)) + 1e-9) * 32767 * 10 ** (peak_dbfs / 20)


def place(base: np.ndarray, snippet: np.ndarray, at_s: float) -> np.ndarray:
    out = base.copy()
    i = int(at_s * SR)
    j = min(len(out), i + len(snippet))
    out[i:j] += snippet[: j - i]
    return out


def clap_sequence(times, total_s: float, peak_dbfs=-18.0, noise_dbfs=-62.0, seed=3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    y = silence(total_s, noise_dbfs, rng)
    for k, t in enumerate(times):
        lvl = peak_dbfs + rng.uniform(-2, 2) if isinstance(peak_dbfs, (int, float)) else peak_dbfs[k]
        y = place(y, clap(lvl, rng), t)
    return y


def vowel_speech(seconds: float = 3.0, level_dbfs: float = -14.0, seed=5, plosives=True) -> np.ndarray:
    """Formant-synthesised voiced speech (glottal pulses through vowel
    resonances), 4-5 syllables per second, with plosive release bursts."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    out = np.zeros(n)
    vowels = [(730, 1090, 2440), (270, 2290, 3010), (300, 870, 2240), (530, 1840, 2480), (570, 840, 2410)]
    t = 0.05
    while t < seconds - 0.25:
        dur = rng.uniform(0.12, 0.28)
        f0 = rng.uniform(95, 210)
        m = int(dur * SR)
        pulses = np.zeros(m)
        period = int(SR / f0)
        pulses[::period] = 1.0
        f1, f2, f3 = vowels[rng.integers(len(vowels))]
        v = _bandpass(pulses, f1, 5) * 1.0 + _bandpass(pulses, f2, 8) * 0.5 + _bandpass(pulses, f3, 10) * 0.25
        env = np.sin(np.linspace(0, np.pi, m)) ** 0.6
        syl = v * env
        if plosives and rng.random() < 0.6:
            b = int(0.012 * SR)
            burst = rng.standard_normal(b) * np.exp(-np.arange(b) / (0.003 * SR))
            syl[:b] += _bandpass(burst, rng.uniform(1500, 4000), 1.5) * np.max(np.abs(v)) * rng.uniform(0.8, 2.0)
        i = int(t * SR)
        out[i:i + m] += syl[: max(0, min(m, n - i))]
        t += dur + rng.uniform(0.03, 0.12)
    out += silence(seconds, -62, rng)
    return out / (np.max(np.abs(out)) + 1e-9) * 32767 * 10 ** (level_dbfs / 20)


def music(seconds: float = 4.0, level_dbfs: float = -12.0, seed=7, drums=True) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    chord = sum(np.sin(2 * np.pi * f * t) * (0.6 / (h + 1)) for h, f in enumerate([220, 277.2, 329.6, 440, 554.4]))
    chord *= 0.6 + 0.4 * np.sin(2 * np.pi * 0.5 * t) ** 2
    out = chord
    if drums:
        beat = 0.5
        for k in range(int(seconds / beat)):
            i = int(k * beat * SR)
            m = int(0.25 * SR)
            tt = np.arange(m) / SR
            if k % 2 == 0:   # kick
                kick = np.sin(2 * np.pi * (60 + 90 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 18) * 1.6
                out[i:i + m] += kick[: max(0, min(m, n - i))]
            else:            # snare: tone + noise, ~120 ms decay
                sn = (np.sin(2 * np.pi * 190 * tt) * 0.5 + rng.standard_normal(m) * 0.9) * np.exp(-tt * 22)
                out[i:i + m] += sn[: max(0, min(m, n - i))]
            h = int(0.03 * SR)   # hi-hat on every half beat
            for off in (0, int(beat / 2 * SR)):
                hh = _bandpass(rng.standard_normal(h), 7000, 0.7) * np.exp(-np.arange(h) / (0.006 * SR)) * 0.5
                s = i + off
                out[s:s + h] += hh[: max(0, min(h, n - s))]
    return out / (np.max(np.abs(out)) + 1e-9) * 32767 * 10 ** (level_dbfs / 20)


def typing(seconds: float = 4.0, level_dbfs: float = -24.0, seed=9) -> np.ndarray:
    rng = np.random.default_rng(seed)
    y = silence(seconds, -60, rng)
    t = 0.1
    while t < seconds - 0.05:
        m = int(0.006 * SR)
        click = rng.standard_normal(m) * np.exp(-np.arange(m) / (0.0008 * SR))
        click = _bandpass(click, rng.uniform(2500, 6000), 2.0)
        click = click / (np.max(np.abs(click)) + 1e-9) * 32767 * 10 ** ((level_dbfs + rng.uniform(-4, 2)) / 20)
        y = place(y, click, t)
        t += rng.uniform(0.08, 0.35)
    return y


def knocks(seconds: float = 3.0, level_dbfs: float = -12.0, seed=11) -> np.ndarray:
    rng = np.random.default_rng(seed)
    y = silence(seconds, -62, rng)
    for t in (0.4, 0.65, 1.8, 2.05):
        m = int(0.12 * SR)
        tt = np.arange(m) / SR
        k = np.sin(2 * np.pi * rng.uniform(120, 260) * tt) * np.exp(-tt * 45) + 0.15 * rng.standard_normal(m) * np.exp(-tt * 300)
        y = place(y, k / np.max(np.abs(k)) * 32767 * 10 ** (level_dbfs / 20), t)
    return y


def cough(seconds: float = 2.0, level_dbfs: float = -10.0, seed=13) -> np.ndarray:
    rng = np.random.default_rng(seed)
    y = silence(seconds, -62, rng)
    for t in (0.3, 1.1):
        m = int(0.28 * SR)
        tt = np.arange(m) / SR
        env = (1 - np.exp(-tt * 200)) * np.exp(-tt * 9)
        c = (_bandpass(rng.standard_normal(m), 600, 1.0) + 0.5 * _bandpass(rng.standard_normal(m), 1800, 1.2)) * env
        y = place(y, c / np.max(np.abs(c)) * 32767 * 10 ** (level_dbfs / 20), t)
    return y


def fan_noise(seconds: float = 3.0, level_dbfs: float = -40.0, seed=15) -> np.ndarray:
    rng = np.random.default_rng(seed)
    w = rng.standard_normal(int(seconds * SR))
    pink = np.cumsum(w) * 0.02
    pink -= np.convolve(pink, np.ones(400) / 400, mode="same")
    x = pink + 0.3 * w
    return x / (np.sqrt(np.mean(x ** 2)) + 1e-9) * 32768 * 10 ** (level_dbfs / 20)


def to_int16(x: np.ndarray) -> np.ndarray:
    return np.clip(np.round(x), -32768, 32767).astype(np.int16)


def blocks(x: np.ndarray, size: int = 1024):
    x16 = to_int16(x)
    for i in range(0, len(x16), size):
        yield x16[i:i + size].reshape(-1, 1)


def read_wav(path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert w.getframerate() == SR and w.getsampwidth() == 2
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() > 1:
            data = data.reshape(-1, w.getnchannels())[:, 0]
        return data.astype(np.float64)


_TTS_CACHE = Path(tempfile.gettempdir()) / "ultron_tts_fixtures"


def windows_tts(text: str, rate: int = 0) -> np.ndarray | None:
    """Real synthesised speech (Windows SAPI), 16 kHz mono. None elsewhere."""
    if os.name != "nt":
        return None
    _TTS_CACHE.mkdir(exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in text)[:60]
    path = _TTS_CACHE / f"{safe}_{rate}.wav"
    if not path.exists():
        ps = (
            "Add-Type -AssemblyName System.Speech;"
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"$s.Rate={int(rate)};"
            "$f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,"
            "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
            "[System.Speech.AudioFormat.AudioChannel]::Mono);"
            f"$s.SetOutputToWaveFile('{path}',$f);"
            f"$s.Speak('{text.replace(chr(39), '')}');$s.Dispose()"
        )
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                           timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            return None
    if not path.exists() or path.stat().st_size < 1000:
        return None
    return read_wav(path)
