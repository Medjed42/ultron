"""Local Vosk wake detection. No camera or external speech service is used.
Ultron/altron are accepted as complete words; the previous broad substring
matches (including sorry/star/car) are deliberately removed.
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable
import json
import re

# The installed English model contains "all", "tron" and "ultra", but not
# "ultron". Constrain recognition to phonetic phrases instead of silently
# waiting for an out-of-vocabulary word that can never be returned.
WAKE_PHRASES = ("ultron", "altron", "hey ultra", "hey all tron", "all tron", "ultra on")


def phrases_for(name: str | None = None) -> tuple[str, ...]:
    """Phrases that count as calling the assistant by `name`.

    "Ultron" keeps the phonetic set above (measured against spoken "Ultron":
    the model hears "all tron" or "ultra on"). Any other configured name is
    matched as itself and as "hey <name>"; start() keeps only phrases whose
    every word exists in the offline model."""
    words = re.findall(r"[a-z]+", str(name or "ultron").casefold())
    if not words or words == ["ultron"]:
        return WAKE_PHRASES
    joined = " ".join(words)
    return (joined, "hey " + joined)


def matches_wake(text, phrases=None):
    normalized = " ".join(re.findall(r"[a-z]+", str(text).casefold()))
    return any(re.search(r"(?:^| )" + re.escape(phrase) + r"(?: |$)", normalized)
               for phrase in (phrases or WAKE_PHRASES))


_MODEL = None
_MODEL_LOCK = threading.Lock()


def _model():
    """The Vosk model, loaded once per process (~40 MB, 1-2 s to load)."""
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            from vosk import Model, SetLogLevel
            SetLogLevel(-1)
            _MODEL = Model(str(Path(__file__).resolve().parent.parent / "vosk_model" / "model"))
        return _MODEL

# Vosk needs no special installation if we already pip installed it.
# We expect the model to be at 'vosk_model/model' in the root directory.

def is_installed() -> bool:
    try:
        import vosk
        return True
    except Exception:
        return False

def is_ready() -> bool:
    if not is_installed():
        return False
    # Model should be downloaded by the agent to vosk_model/model
    try:
        models_dir = Path(__file__).resolve().parent.parent / "vosk_model" / "model"
        return models_dir.is_dir()
    except Exception:
        return False

def install_and_download(logger: Callable[[str], None] = print,
                         notify: Callable[[str], None] | None = None) -> tuple[bool, str]:
    # Vosk model was already downloaded via script by the agent.
    # Just in case, return ready.
    if is_ready():
        return True, "Wake word installed and ready."
    return False, "Vosk model not found. Please contact the AI assistant to download it."

class WakeWordDetector:
    def __init__(self, on_detect: Callable[[], None],
                 threshold: float = 0.5,
                 logger: Callable[[str], None] = print,
                 notify: Callable[[str], None] | None = None,
                 name: str | None = None):
        self._on_detect = on_detect
        self.name = name or "Ultron"
        self.phrases: tuple[str, ...] = ()
        self._logger    = logger
        self._notify    = notify or (lambda _msg: None)
        self._queue: queue.Queue = queue.Queue(maxsize=50)
        self._thread: threading.Thread | None = None
        self._running = False
        self._model = None
        self._rec = None
        self._ready = False

    def start(self) -> bool:
        if self._running:
            return True
        try:
            from vosk import KaldiRecognizer
            self._model = _model()
            phrases = [phrase for phrase in phrases_for(self.name)
                       if all(self._model.vosk_model_find_word(word) >= 0 for word in phrase.split())]
            if not phrases:
                self._logger(f"Wake word: '{self.name}' is not in the offline speech model's vocabulary.")
                self._notify(f"The name '{self.name}' cannot be recognised offline.")
                return False
            self.phrases = tuple(phrases)
            self._rec = KaldiRecognizer(self._model, 16000, json.dumps(phrases + ["[unk]"]))
        except Exception as e:
            self._logger(f"Wake word: could not load Vosk model - {e}")
            self._notify("Wake word unavailable - use the WAKE NOW button.")
            return False
        self._drain()

        self._running = True
        self._ready = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="WakeWordThread")
        self._thread.start()
        self._logger(f"Wake word: listening for '{self.name}'.")
        return True

    def stop(self) -> None:
        self._running = False
        self._drain()
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        self._ready = False
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=2.0)
        self._thread = None

    @property
    def ready(self) -> bool:
        return self._ready

    def feed(self, frame_int16) -> None:
        if not self._running:
            return
        try:
            data = frame_int16[:, 0].tobytes() if getattr(frame_int16, "ndim", 1) > 1 else frame_int16.tobytes()
            self._queue.put_nowait(data)
        except queue.Full:
            pass
        except Exception:
            pass

    def _loop(self) -> None:
        while self._running:
            try:
                data = self._queue.get()
                if data is None or not self._running:
                    break

                if self._rec.AcceptWaveform(data):
                    res = json.loads(self._rec.Result())
                    text = res.get("text", "").lower()
                    # Match complete wake-name tokens only.
                    if matches_wake(text, self.phrases):
                        self._drain()
                        try:
                            self._on_detect()
                        except Exception as e:
                            self._logger(f"Wake word: on_detect error - {e}")
            except Exception as e:
                self._logger(f"Wake word: inference error - {e}")

    def _drain(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except Exception:
            pass
