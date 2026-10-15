"""
core/cinematic/welcome_cues.py — where things are said in the welcome recording.

The welcome animation follows the recording, never a guessed script, so its
cue times come from analysing the file itself:

    * ffmpeg decodes the MP3 to 16 kHz mono;
    * the project's Vosk model (vosk_model/, already used for the wake word)
      returns a timestamp for every recognised word;
    * each scene cue is found by looking for its keyword in order ("hello",
      "allow", "am" → the next word is "Ultron", "artificial", … "mode"), so a
      repeated word ("voice", "intelligence") always resolves to the right one;
    * a 50 ms RMS envelope of the same audio lets the visuals breathe with the
      actual voice and sound effects.

The result is stored next to the MP3 (ultron_welcome.cues.json) together with
the MP3's SHA-256. At run time load() only reads that file — no speech
recognition at startup. If the MP3 is ever swapped for a different recording,
load() notices the hash mismatch, scales the cues to the new length as a stop-
gap, and says so; running this module re-measures it:

    python -m core.cinematic.welcome_cues assets/audio/ultron_welcome.mp3
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WELCOME_MP3 = ROOT / "assets" / "audio" / "ultron_welcome.mp3"
VOSK_MODEL = ROOT / "vosk_model" / "model"
ENVELOPE_HZ = 20

# (cue, keyword, which time) — searched in order through the recognised words.
#   start / end           the keyword's own start / end
#   phrase_start / _end   the spoken phrase containing it (pauses > 0.28 s split phrases)
#   next_start            the word after the keyword (the name after "I am")
ANCHORS: list[tuple[str, str, str]] = [
    ("hello", "hello", "start"),
    ("allow", "allow", "start"),
    ("i_am", "am", "phrase_start"),
    ("ultron", "am", "next_start"),
    ("ultron_end", "am", "phrase_end"),
    ("ai", "artificial", "phrase_start"),
    ("think", "think", "start"),
    ("learn", "learn", "start"),
    ("evolve", "evolve", "start"),
    ("surpasses", "surpasses", "start"),
    ("limitations", "limitations", "start"),
    ("understanding_end", "understanding", "end"),
    ("sleep", "sleep", "phrase_start"),
    ("sleep_word", "sleep", "start"),
    ("forget", "forget", "phrase_start"),
    ("forget_word", "forget", "start"),
    ("always", "always", "phrase_start"),
    ("watching", "watching", "start"),
    ("watching_end", "watching", "end"),
    ("booting", "system", "start"),
    ("booting_end", "system", "phrase_end"),
    ("standby", "stand", "phrase_start"),
    ("shortly_end", "shortly", "end"),
    ("natural", "natural", "phrase_start"),
    ("natural_end", "natural", "phrase_end"),
    ("transform", "transformation", "phrase_start"),
    ("transform_end", "transformation", "end"),
    ("human_mode", "switching", "start"),
    ("mode", "mode", "start"),
    ("speech_end", "mode", "end"),
]

# What analysis produced for the shipped recording (33.83 s), used only if the
# cue file itself is missing.
MEASURED = {
    "hello": 0.06, "allow": 1.35, "i_am": 3.33, "ultron": 4.02, "ultron_end": 4.68,
    "ai": 5.07, "think": 7.59, "learn": 8.52, "evolve": 9.57, "surpasses": 11.34,
    "limitations": 12.03, "understanding_end": 13.92, "sleep": 14.31, "sleep_word": 14.76,
    "forget": 15.66, "forget_word": 16.14, "always": 17.04, "watching": 18.54,
    "watching_end": 19.08, "booting": 19.59, "booting_end": 20.61, "standby": 21.0,
    "shortly_end": 23.76, "natural": 24.12, "natural_end": 26.91, "transform": 27.3,
    "transform_end": 29.16, "human_mode": 29.58, "mode": 30.87, "speech_end": 31.23,
}
MEASURED_DURATION = 33.83
PHRASE_GAP = 0.28


@dataclass
class Cues:
    times: dict[str, float]
    duration: float
    envelope: list[float] = field(default_factory=list)     # 0…1 at ENVELOPE_HZ
    source: str = "measured"                                 # measured | scaled | builtin

    def __getitem__(self, key: str) -> float:
        return self.times[key]

    def level(self, t: float) -> float:
        """The recording's loudness (0…1) at time t, interpolated."""
        env = self.envelope
        if not env:
            return 0.0
        f = max(0.0, t) * ENVELOPE_HZ
        i = int(f)
        if i >= len(env) - 1:
            return env[-1] if i < len(env) + 4 else 0.0
        r = f - i
        return env[i] * (1 - r) + env[i + 1] * r


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def cue_path(mp3: Path) -> Path:
    return mp3.with_suffix(".cues.json")


def load(mp3: Path = WELCOME_MP3) -> Cues:
    """The cue sheet for `mp3` — measured if available, otherwise a clearly
    reported fallback. Never raises."""
    try:
        data = json.loads(cue_path(mp3).read_text(encoding="utf-8"))
        times = {k: float(v) for k, v in data["cues"].items()}
        env = [max(0.0, min(1.0, v / 100.0)) for v in data.get("envelope", [])]
        dur = float(data["duration"])
        if mp3.exists() and data.get("sha256") and data["sha256"] != sha256(mp3):
            real = _duration(mp3) or dur
            k = real / dur if dur else 1.0
            print(f"[Welcome] {mp3.name} differs from the analysed recording - cues scaled "
                  f"x{k:.2f}; re-run core/cinematic/welcome_cues.py to re-measure.")
            return Cues({n: t * k for n, t in times.items()}, real, [], "scaled")
        return Cues(times, dur, env, "measured")
    except Exception as e:
        print(f"[Welcome] cue file unavailable ({e}) - using the built-in measurements.")
        return Cues(dict(MEASURED), MEASURED_DURATION, [], "builtin")


def _duration(mp3: Path) -> float | None:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", str(mp3)],
                             capture_output=True, text=True, timeout=20)
        return float(out.stdout.strip())
    except Exception:
        return None


# ── Analysis (offline, on demand) ─────────────────────────────────────────────

def _phrases(words: list[dict]) -> list[tuple[int, int]]:
    spans, a = [], 0
    for i in range(1, len(words) + 1):
        if i == len(words) or words[i]["start"] - words[i - 1]["end"] > PHRASE_GAP:
            spans.append((a, i - 1))
            a = i
    return spans


def find_cues(words: list[dict]) -> dict[str, float]:
    phrases = _phrases(words)

    def phrase_of(i: int) -> tuple[int, int]:
        return next(p for p in phrases if p[0] <= i <= p[1])

    cues, pos = {}, 0
    for name, key, which in ANCHORS:
        idx = next((i for i in range(pos, len(words)) if words[i]["word"].lower() == key), None)
        if idx is None:                       # keyword not heard: keep looking from here
            continue
        a, b = phrase_of(idx)
        cues[name] = round({
            "start": words[idx]["start"], "end": words[idx]["end"],
            "phrase_start": words[a]["start"], "phrase_end": words[b]["end"],
            "next_start": words[min(idx + 1, len(words) - 1)]["start"],
        }[which], 2)
        pos = idx
    return cues


def analyze(mp3: Path, model_dir: Path = VOSK_MODEL) -> dict:
    import numpy as np
    from vosk import KaldiRecognizer, Model, SetLogLevel
    SetLogLevel(-1)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "welcome16k.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-ac", "1", "-ar", "16000",
                        "-f", "wav", str(wav)], check=True, timeout=120)
        with wave.open(str(wav), "rb") as wf:
            sr = wf.getframerate()
            pcm = wf.readframes(wf.getnframes())
        rec = KaldiRecognizer(Model(str(model_dir)), sr)
        rec.SetWords(True)
        words: list[dict] = []
        for i in range(0, len(pcm), 8000):
            if rec.AcceptWaveform(pcm[i:i + 8000]):
                words += json.loads(rec.Result()).get("result", [])
        words += json.loads(rec.FinalResult()).get("result", [])
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768.0
    hop = sr // ENVELOPE_HZ
    n = len(x) // hop
    rms = np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-6))
    env = np.clip((db + 50.0) / 40.0, 0.0, 1.0)
    duration = len(x) / sr
    return {
        "source": mp3.name,
        "sha256": sha256(mp3),
        "duration": round(duration, 3),
        "analysis": "ffmpeg 16 kHz mono decode; Vosk word timestamps (vosk_model/model); "
                    f"{1000 // ENVELOPE_HZ} ms RMS envelope mapped -50..-10 dBFS -> 0..100",
        "cues": find_cues(words),
        "words": [{"w": w["word"], "s": round(w["start"], 2), "e": round(w["end"], 2),
                   "c": round(w.get("conf", 1.0), 2)} for w in words],
        "envelope_hz": ENVELOPE_HZ,
        "envelope": [int(round(v * 100)) for v in env],
    }


def main(argv: list[str]) -> int:
    mp3 = Path(argv[1]) if len(argv) > 1 else WELCOME_MP3
    data = analyze(mp3)
    cue_path(mp3).write_text(json.dumps(data, indent=1), encoding="utf-8")
    missing = [n for n, _k, _w in ANCHORS if n not in data["cues"]]
    print(f"{cue_path(mp3)}: {len(data['words'])} words, {len(data['cues'])} cues"
          + (f", not found: {', '.join(missing)}" if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
