"""
core/cinematic/settings.py — what the user chose, and what the machine can take.

AnimationSettings is a snapshot of the persisted preferences (see
memory/config_manager.get_animation_settings) plus the two things that are not
stored but decided at run time:

  * reduced motion — "auto" follows the Windows "Show animations in Windows"
    switch (SPI_GETCLIENTAREAANIMATION). When it is on, sequences keep their
    story but drop the shake, the flashes, the glitches and the big movement.
  * the quality profile — the per-frame budget of every effect. The overlays
    lower it on their own if frames keep running slow, so "ultra" on a weak
    laptop degrades instead of stuttering.
"""
from __future__ import annotations

import platform
from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class QualityProfile:
    name: str
    frame_ms: int           # timer interval
    particles: int          # live particle cap
    bolt_detail: int        # midpoint-displacement generations per bolt
    branch_depth: int       # how deep bolts may fork
    glow_passes: int        # 1 = core only … 3 = core + halo + bloom
    glitch_slices: int      # displaced bands per glitch frame
    motion_blur: int        # ghost copies behind fast-moving halves
    distortion_strips: int  # heat-wave strips during the shockwave (0 = off)
    rgb_split: bool         # chromatic aberration copies of the frame


QUALITY: dict[str, QualityProfile] = {
    "low":    QualityProfile("low",    33, 90,  5, 1, 1, 4,  0, 0,  False),
    "medium": QualityProfile("medium", 16, 220, 6, 2, 2, 8,  1, 12, True),
    "ultra":  QualityProfile("ultra",  16, 460, 7, 3, 3, 14, 2, 24, True),
}
_ORDER = ("low", "medium", "ultra")


def lower_quality(name: str) -> str:
    i = _ORDER.index(name) if name in _ORDER else 0
    return _ORDER[max(0, i - 1)]


_SYS_REDUCED: bool | None = None


def system_prefers_reduced_motion() -> bool:
    """True when Windows has client-area animations switched off."""
    global _SYS_REDUCED
    if _SYS_REDUCED is not None:
        return _SYS_REDUCED
    reduced = False
    if platform.system() == "Windows":
        try:
            import ctypes
            from ctypes import wintypes
            SPI_GETCLIENTAREAANIMATION = 0x1042
            on = wintypes.BOOL(True)
            if ctypes.windll.user32.SystemParametersInfoW(
                    SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(on), 0):
                reduced = not bool(on.value)
        except Exception:
            reduced = False
    _SYS_REDUCED = reduced
    return reduced


@dataclass
class AnimationSettings:
    startup: bool = True
    shutdown: bool = True
    quality: str = "ultra"
    lightning: bool = True
    glitch: bool = True
    sound: bool = True
    speed: float = 1.0
    motion: bool = True
    skip_intro: bool = False
    reduced_motion: str = "auto"
    welcome: bool = True
    voice_handoff: bool = True
    color_transform: bool = True
    sfx_volume: float = 0.35
    narration_volume: float = 0.85
    launch_fullscreen: bool = True
    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "AnimationSettings":
        try:
            from memory.config_manager import get_animation_settings
            return cls.from_dict(get_animation_settings())
        except Exception:
            return cls()

    @classmethod
    def from_dict(cls, d: dict) -> "AnimationSettings":
        known = {k: d[k] for k in (
            "startup", "shutdown", "quality", "lightning", "glitch", "sound",
            "speed", "motion", "skip_intro", "reduced_motion", "welcome", "voice_handoff",
            "color_transform", "sfx_volume", "narration_volume", "launch_fullscreen") if k in d}
        return cls(**known)

    def to_dict(self) -> dict:
        return {
            "startup": self.startup, "shutdown": self.shutdown,
            "quality": self.quality, "lightning": self.lightning,
            "glitch": self.glitch, "sound": self.sound, "speed": self.speed,
            "motion": self.motion, "skip_intro": self.skip_intro,
            "reduced_motion": self.reduced_motion, "welcome": self.welcome,
            "voice_handoff": self.voice_handoff, "color_transform": self.color_transform,
            "sfx_volume": self.sfx_volume, "narration_volume": self.narration_volume,
            "launch_fullscreen": self.launch_fullscreen,
        }

    def save(self) -> None:
        from memory.config_manager import save_animation_settings
        save_animation_settings(self.to_dict())

    # ── derived ──────────────────────────────────────────────────────────────
    @property
    def reduced(self) -> bool:
        if self.reduced_motion == "on":
            return True
        if self.reduced_motion == "off":
            return False
        return system_prefers_reduced_motion()

    @property
    def speed_factor(self) -> float:
        try:
            s = float(self.speed)
        except (TypeError, ValueError):
            s = 1.0
        s = min(2.0, max(0.5, s))
        # Reduced motion keeps the sequence but does not linger on it.
        return s * (1.35 if self.reduced else 1.0)

    @property
    def use_lightning(self) -> bool:
        return bool(self.lightning)

    @property
    def use_glitch(self) -> bool:
        return bool(self.glitch) and not self.reduced

    @property
    def use_shake(self) -> bool:
        return not self.reduced

    @property
    def flash_scale(self) -> float:
        """Ceiling for full-screen flashes. Never a strobe: each sequence has
        one big flash at most, and reduced motion takes it down to a glow."""
        return 0.25 if self.reduced else 1.0

    def profile(self) -> QualityProfile:
        return QUALITY.get(self.quality, QUALITY["medium"])

    def copy(self, **changes) -> "AnimationSettings":
        return replace(self, **changes)
