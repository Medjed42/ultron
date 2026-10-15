"""
core/cinematic — ULTRON's startup / shutdown cinematics and the motion that
keeps the AI core alive in between.

    settings.py   AnimationSettings (persisted in config/api_keys.json under
                  "animations"), quality profiles, reduced-motion detection
    lifecycle.py  OFFLINE → BOOTING → INITIALIZING → ONLINE → SHUTTING_DOWN → OFFLINE
    fx.py         the effect engine: lightning, particles, rings, glitch,
                  distortion, scanlines, bloom, the wordmark
    sound.py      AudioManager: the ULTRON sound IDs, pack → V2 → synthesised
    overlay.py    CinematicOverlay: timeline, cues, adaptive quality, fail-safe
    welcome.py    WelcomeSequence — the narrated welcome, timed by the recording
    welcome_cues.py  measured word timings + loudness envelope of the recording
    boot.py       BootSequence — the classic awakening (no narration)
    transform.py  ColorTransformSequence — crack, shatter, lightning, new colour
    entrance.py   the dashboard assembling at the end of either intro
    shutdown.py   ShutdownSequence — the electric slash
    core_fx.py    CoreFx — idle / listening / thinking / speaking / error motion

Pure QPainter on top of PyQt6 — no new dependency.
"""
from .boot import BootSequence
from .core_fx import CoreFx
from .lifecycle import (BOOTING, INITIALIZING, OFFLINE, ONLINE, SHUTTING_DOWN,
                        Lifecycle)
from .settings import AnimationSettings
from .shutdown import ShutdownSequence
from .sound import AudioManager, SfxPlayer
from .transform import ColorTransformSequence
from .welcome import WelcomeSequence

__all__ = [
    "AnimationSettings", "AudioManager", "BootSequence", "ColorTransformSequence", "CoreFx",
    "Lifecycle", "SfxPlayer", "ShutdownSequence", "WelcomeSequence",
    "OFFLINE", "BOOTING", "INITIALIZING", "ONLINE", "SHUTTING_DOWN",
]
