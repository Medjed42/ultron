"""
Ultron 2.0 — Personal AI Assistant
Made by Pedja
GitHub: https://github.com/Medjed42/ultron
"""
import platform as _platform
import subprocess as _subprocess

# ── Nuclear: force CREATE_NO_WINDOW on EVERY subprocess call on Windows ───────
# This patches Popen itself, so no per-file flag is needed anywhere.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
            kw.pop("startupinfo", None)   # drop any stale/shared STARTUPINFO
            super().__init__(args, **                       kw)

    _subprocess.Popen = _Popen


# ── Console must survive non-UTF-8 code pages ────────────────────────────────
# Every status line in this file carries an emoji, and on a legacy Windows
# console the active code page is the system one — cp1254 in Turkey, cp1251 in
# Russia, cp932 in Japan. Printing an emoji there raises UnicodeEncodeError, and
# because most of these prints sit inside the receive loop it takes the session
# down on startup. Reconfiguring to UTF-8 with a replacement fallback costs
# nothing and makes the app launch the same way in every locale.
import sys as _sys

for _stream in ("stdout", "stderr"):
    try:
        _s = getattr(_sys, _stream, None)
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass          # pythonw / redirected pipes / anything exotic — never fatal

# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import os
import re
import threading
import time
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from core.security import install_redaction
install_redaction()
from core import runtime_status

import sounddevice as sd
import numpy as np
from google import genai
from google.genai import types
from ui import CarUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    save_session_summary, pop_last_session,
    search_memory, set_trim_notifier,
)

# The file-backed tools (open_app, web_search, browser_control, …) are no longer
# imported or declared here — they self-describe via a TOOL dict in their own
# actions/*.py file and are auto-discovered by core.action_loader at startup.
# Only tools that are tied to live-session state stay inline in this file
# (screen_process, close_camera, save_memory, manage_monitor, shutdown_car,
# system_status).
from actions.screen_processor  import _capture_camera, _capture_screen
from actions.system_monitor    import SystemMonitor, get_system_status
from actions.background_monitor import (
    add_monitor, remove_monitor, list_monitors, check_all as monitor_check_all,
)
from actions.web_search        import _news as _fetch_news_sync
from core.theme                import parse_command as parse_theme_command
from memory.config_manager     import (
    get_brief_enabled, get_media_resolution, get_proactive_audio_enabled,
    get_push_to_talk_enabled, get_thinking_enabled, get_turn_tuning, get_voice,
    get_wake_word_enabled, save_wake_word_enabled,    get_input_device, get_output_device,
    get_wake_on_clap_enabled, save_wake_on_clap_enabled, get_clap_threshold,
    get_clap_sensitivity,
)
from core                     import gemini as _gemini
from core.plugin_loader        import discover_plugins
from core                      import undo as undo_stack
from core                      import confirm as confirm_gate
from core                      import audio_devices
from core.action_loader        import discover_actions
from core.echo                 import EchoGuard
from core.viseme               import VisemeStream
from core.wake_word            import (
    WakeWordDetector, is_ready as wake_is_ready, install_and_download as wake_install,
)
from core.clap_detector        import ClapDetector
from core                      import connections as _connections
from core                      import browser_policy
from core.command_router       import CommandRouter, set_router as _set_router

# How long the assistant stays awake with no user speech before it auto-sleeps
# again (wake-word mode only).
WAKE_SLEEP_TIMEOUT = 120.0   # seconds (2 minutes)

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
# The conversation's model. A NAME, not a decision: the ladder lives in
# core/gemini.py and this is only whichever rung is currently in use, kept here
# as a module attribute because plugins read it (chat_takeover asks main for it
# so that upgrading the assistant upgrades the plugin too).
#
# It is reassigned on every connect, so a model that runs out of quota is
# stepped over and the assistant keeps talking instead of failing to start.
LIVE_MODEL          = "models/gemini-3.1-flash-live-preview"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

# RMS below which 16-bit PCM is treated as room silence; above _LEVEL_FULL it
# reads as a full-height waveform. Tuned so ordinary speech lands mid-range and
# the bars still move for a quiet talker — language- and device-independent.
_LEVEL_FLOOR = 60.0
_LEVEL_FULL  = 2600.0


def _pcm_level(samples) -> float:
    """Map a block of int16 PCM samples to a 0.0–1.0 loudness level for the HUD
    waveform. Returns 0.0 on empty/invalid input so it can never raise."""
    try:
        x = np.asarray(samples, dtype=np.float32)
        if x.size == 0:
            return 0.0
        rms = float(np.sqrt(np.mean(x * x)))
    except Exception:
        return 0.0
    if rms <= _LEVEL_FLOOR:
        return 0.0
    return min(1.0, (rms - _LEVEL_FLOOR) / (_LEVEL_FULL - _LEVEL_FLOOR))


# ── Viseme extraction ─────────────────────────────────────────────────────────
# The avatar's mouth used to be driven by one RMS value per ~200 ms write batch,
# which is five updates a second averaged over a fifth of a second — it could
# only ever flap. These read the *shape* of each 20 ms slice straight from the
# spectrum of the audio being played, so no transcript, no forced alignment and
# no language assumption: it works the same for Turkish and English.
#
# Two numbers come out. Openness tracks the first formant — F1 climbs as the jaw
# drops, so /a/ reads open and /i/ or /u/ read closed. Width tracks the second —
# F2 is high for spread vowels (/i/, /e/) and low for rounded ones (/u/, /o/).
# Extra time beyond the device's reported output latency before the microphone
# is trusted again: covers room decay and the speaker's own settling.
_TAIL_MARGIN = 0.25

_VIS_WIN = 1024        # ~43 ms analysis window at 24 kHz: enough for formants
_VIS_HOP = 480         # 20 ms between frames, i.e. 50 shapes a second

# Delay from handing the first bytes of a reply to an already-running output
# stream to hearing them: one callback period, plus whatever the DAC adds.
_FIRST_SOUND = CHUNK_SIZE / RECEIVE_SAMPLE_RATE      # ~43 ms
# How far past the device's own buffer the mouth's timeline may drift before it
# is re-anchored. The buffer is the hard limit on how much audio can be queued
# ahead, so anything beyond it plus a margin for clock error is impossible.
_CURSOR_SLACK = 0.15

# Erring early is the safe direction. A viewer tolerates a mouth that moves
# slightly before the sound far better than one that moves after it — the
# broadcast limits are about 45 ms of lag against 125 ms of lead — so where
# this is uncertain it is biased to lead.


def _pcm_visemes(samples, sr: int = 24000):
    """Slice a PCM block into (level, openness, width) frames, one per 20 ms.

    Returns [] on anything unexpected — the mouth falls back to loudness-only
    articulation rather than the caller having to handle an error.
    """
    try:
        x = np.asarray(samples, dtype=np.float32)
        if x.size < _VIS_WIN:
            return []
        win = np.hanning(_VIS_WIN).astype(np.float32)
        freqs = np.fft.rfftfreq(_VIS_WIN, 1.0 / sr)
        b_f1_lo = (freqs >= 150) & (freqs < 450)     # F1 of close vowels
        b_f1_hi = (freqs >= 450) & (freqs < 1100)    # F1 of open vowels
        b_f2_bk = (freqs >= 600) & (freqs < 1300)    # F2 of rounded vowels
        b_f2_fr = (freqs >= 1700) & (freqs < 3200)   # F2 of spread vowels
        b_hiss = (freqs >= 3800) & (freqs < 8000)    # fricatives

        # One frame per hop across the *whole* block. Stepping only while a full
        # window fits stopped 1024 - 480 samples short of the end, so a 200 ms
        # batch yielded 160 ms of schedule: the mouth ran out of frames before
        # the audio ran out of sound, and each batch no longer lined up with the
        # end of the one before it. Losing 20 % of every batch is most of why
        # the mouth did not track the words.
        out = []
        for start in range(0, x.size, _VIS_HOP):
            # The level gates closures, so it is measured over exactly this
            # 20 ms and never looks ahead. The spectrum needs a longer window
            # to resolve formants and may be short-filled at the very end.
            level = _pcm_level(x[start:start + _VIS_HOP])
            seg = x[start:start + _VIS_WIN]
            if seg.size < _VIS_WIN:
                seg = np.concatenate([seg, np.zeros(_VIS_WIN - seg.size,
                                                    dtype=np.float32)])
            if level <= 0.0:
                out.append((0.0, 0.0, 0.0))
                continue
            mag = np.abs(np.fft.rfft((seg - seg.mean()) * win))
            f1l, f1h = float(mag[b_f1_lo].sum()), float(mag[b_f1_hi].sum())
            f2b, f2f = float(mag[b_f2_bk].sum()), float(mag[b_f2_fr].sum())
            hiss = float(mag[b_hiss].sum())

            openness = f1h / (f1l + f1h + 1e-6)
            width = (f2f - f2b) / (f2f + f2b + 1e-6)
            # A wide-open jaw physically cannot purse, so openness damps width.
            # /a/ has a low enough F2 to read as "rounded" on the bands alone;
            # letting openness suppress the width term is what keeps an open
            # vowel from pursing.
            width *= (1.0 - openness) ** 0.8
            # Fricatives are formed with a nearly closed mouth.
            h = hiss / (f1l + f1h + f2b + f2f + hiss + 1e-6)
            openness *= 1.0 - 0.65 * min(1.0, h * 2.5)
            out.append((level,
                        float(min(1.0, max(0.0, openness))),
                        float(min(1.0, max(-1.0, width)))))
        return out
    except Exception:
        return []


def _describe_tools(declarations) -> str:
    """One line per capability, straight from the live tool declarations.

    Derived rather than written down: the action and plugin registries are
    discovered at startup, so whatever the user has installed is what the model
    is told it can do. Adding a plugin extends this by itself, and removing one
    stops the model from claiming an ability it no longer has.
    """
    lines = []
    for d in declarations or ():
        try:
            name = d.get("name") if isinstance(d, dict) else getattr(d, "name", None)
            desc = (d.get("description") if isinstance(d, dict)
                    else getattr(d, "description", "")) or ""
        except Exception:
            continue
        if not name:
            continue
        desc = " ".join(str(desc).split())
        lines.append(f"- {name}: {desc[:150]}" if desc else f"- {name}")
    return "\n".join(lines)


def _describe_limits(has_vision: bool, has_mic: bool) -> str:
    """The other half of self-knowledge: what is out of reach, and why.

    Derived from how the program is actually built, not from a list of refusals.
    A model that knows its boundaries stops improvising around them, and stating
    them as architecture rather than as rules keeps the answer honest in any
    language.
    """
    out = [
        "- Anything not listed above is outside your reach. Say so in one clause "
        "and offer the nearest thing you can actually do — never mime an action "
        "you cannot take, and never report a result you did not get.",
        "- You act on this machine only. You cannot reach the user's other "
        "devices, accounts or hardware except through the tools listed above.",
        "- You remember what is in the memory block and what has been said this "
        "session. Anything else you were told before is gone unless it was saved.",
    ]
    if has_vision:
        out.append(
            "- Your sight is not continuous. You see nothing until you call a "
            "vision tool, and then only that single frame at that moment — you "
            "cannot watch, monitor or notice something changing on screen.")
    else:
        out.append("- You have no sight at all in this build.")
    if has_mic:
        out.append(
            "- You hear nothing while the microphone is muted, and you cannot "
            "unmute it yourself.")
    return "\n".join(out)


def _render_prompt(template: str, values: dict) -> str:
    """Fill {tokens} in the prompt template.

    A plain replace rather than str.format: the file is meant to be edited by
    hand, and a stray brace in someone's own wording must never take the app
    down at startup.
    """
    out = template or ""
    for key, val in values.items():
        out = out.replace("{" + key + "}", str(val))
    return out


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are ULTRON, a personal AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

# Transcript chunks shorter than this may legitimately repeat ("evet, evet"),
# so only longer ones are treated as duplicates.
_REPEAT_MIN = 12


def _is_repeat_chunk(txt: str, buf: list) -> bool:
    """True if this transcript chunk has already been seen this turn.

    Guards against the API re-sending the tail of a response across the several
    turn_completes a tool-using turn produces.
    """
    if len(txt) < _REPEAT_MIN:
        return bool(buf) and txt == buf[-1]
    joined = " ".join(buf)
    return txt in joined

def _clean_transcript(text: str) -> str:
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

TOOL_DECLARATIONS = [
    # ── Inline tools ─────────────────────────────────────────────────────────
    # These stay here (rather than in an actions/*.py TOOL dict) because their
    # handling is woven into live-session state — vision capture/injection,
    # camera stream, memory writes, the monitor engine, and shutdown. All other
    # tools live in their own action file and are auto-discovered by
    # core.action_loader (see CarLive.__init__).
    {
        "name": "system_status",
        "description": (
            "Returns real-time system metrics: CPU usage, RAM, GPU load, CPU temperature, "
            "uptime, and process count. Use when the user asks about computer performance, "
            "temperature, memory, or resource usage."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures the screen or webcam image and lets you analyze it. "
            "MUST be called when user asks what is on screen, what you see, "
            "look at camera, analyze my screen, etc. "
            "You have NO visual ability without this tool. "
            "After the image is captured it is sent directly to you — describe what you see and answer the user's question. "
            "When using camera: the live view stays open until user says close it or calls close_camera."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "open_camera",
        "description": (
            "Opens the live camera view on screen and keeps it running nonstop/continuously. "
            "MUST be called immediately when the user asks (in ANY language, including Serbian): 'upali kameru', "
            "'uključi kameru', 'otvori kameru', 'start camera', 'turn on camera', 'open camera', 'pokreni kameru'. "
            "The camera feed stays open nonstop until the user explicitly asks to close it (close_camera)."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "close_camera",
        "description": (
            "Closes the live camera view shown on screen. "
            "Call when the user says (in ANY language): ugasi kameru, isključi kameru, zatvori kameru, "
            "close camera, stop camera, turn off camera, etc."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "open_holo_hands",
        "description": (
            "Opens the 3D Holo Hands / Gestures interactive control deck (from zubair-trabzada/holo-gestures) "
            "with hand tracking in mid-air via camera, 3D hologram manipulation, floating cards, repulsor effects, etc. "
            "Call when the user asks (in ANY language): 'upali holo hands', 'otvori holo hands', 'holo gestures', "
            "'hologramske ruke', 'start holo hands', 'pokreni holo gestove', etc."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "in_browser": {"type": "BOOLEAN", "description": "True to open in default browser, False to open directly in Ultron HUD."}
            },
            "required": []
        }
    },
    {
        "name": "close_holo_hands",
        "description": "Closes the 3D Holo Hands gesture deck view and returns to standard HUD.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "manage_monitor",
        "description": (
            "Add, remove, or list background monitoring topics. "
            "ULTRON checks these topics once a day and alerts the user when there is a new development. "
            "Use 'add' when the user says 'monitor X', 'track X', 'follow X'. "
            "Use 'remove' when the user says 'stop monitoring X'. "
            "Use 'list' when the user asks what is being monitored. "
            "Do NOT add crypto, financial, or trading topics."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type":        "STRING",
                    "description": "add | remove | list",
                },
                "topic": {
                    "type":        "STRING",
                    "description": "Topic to monitor or stop monitoring (e.g. 'space exploration', 'AI news')",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "shutdown_car",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Ultron. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Alex, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "recall_memory",
        "description": (
            "Look up a fact you have stored about the user but which is NOT in "
            "the memory block of your system prompt. "
            "The prompt lists the keys it did not have room for under "
            "'[ALSO REMEMBERED]' — if the user asks about anything named there, "
            "call this FIRST. "
            "Also call it before saying you do not know something personal, and "
            "when the user asks what you remember about them (leave query empty "
            "for everything). "
            "This is a local file search: it is instant and costs nothing."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": (
                        "Keyword to search for — a name, a topic, a category "
                        "(e.g. 'ayse', 'coffee', 'projects'). "
                        "Leave empty to list everything stored."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "undo",
        "description": (
            "Reverse the last change YOU made to this computer — a file you "
            "moved, renamed, created or wrote, or a setting you changed such as "
            "volume, brightness, dark mode or WiFi. "
            "Call this whenever the user says undo, revert, take it back, put it "
            "back, cancel that, or tells you that you did the wrong thing, in ANY "
            "language. "
            "Use action='list' when they ask what can be undone. "
            "This only covers your own actions — it is not the Ctrl+Z of whatever "
            "application is on screen (that is computer_settings with action 'undo')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "undo (default) — reverse the last change | list — show what can be undone",
                },
            },
            "required": [],
        },
    },
]

class _ReconnectSignal(Exception):
    """Raised inside the session TaskGroup to force a clean, voluntary reconnect
    (e.g. the user picked a new voice — the voice is fixed at connect time, so
    the session must be rebuilt).

    Carries `keep_context`: True for an ordinary rebuild, where the stored
    resumption handle is replayed and the conversation continues; False when the
    new session must genuinely start clean (see the voice-change note in
    _on_voice_change)."""

    def __init__(self, keep_context: bool = True):
        super().__init__()
        self.keep_context = keep_context


def _is_reconnect_signal(exc: BaseException) -> bool:
    """True if `exc` is a _ReconnectSignal, or a(n) (Base)ExceptionGroup that
    wraps one — TaskGroup bundles child exceptions into a group."""
    if isinstance(exc, _ReconnectSignal):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_is_reconnect_signal(sub) for sub in exc.exceptions)
    return False


def _keep_context_of(exc: BaseException) -> bool:
    """Read `keep_context` off a reconnect signal, unwrapping the group the
    TaskGroup put it in. Defaults to True: an unexpected shape must not silently
    wipe the conversation."""
    if isinstance(exc, _ReconnectSignal):
        return getattr(exc, "keep_context", True)
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            if _is_reconnect_signal(sub):
                return _keep_context_of(sub)
    return True


class CarLive:
    def __init__(self, ui: CarUI):
        self.ui             = ui
        self._asst_name     = "ULTRON"   # updated each session from config
        self.session              = None
        self.audio_in_queue       = None
        self.out_queue            = None
        self._loop                     = None
        self._is_speaking         = False
        self._speaking_lock       = threading.Lock()
        self._phone_active        = False   # True while phone mic is streaming; pauses PC mic
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._vision_cam_active    = False   # True if camera was opened for vision → auto-close after response
        self._vision_close_pending = False   # True after vision injected; next turn_complete closes camera
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        self._interrupted          = False   # True while draining audio after user interrupt
        # Transcript-driven mouth shapes for the avatar. Fed from the receive
        # loop as words arrive, drained by the playback loop against the audio.
        self._visemes              = VisemeStream()
        self._last_out_logged      = ""      # de-dupes a re-sent transcript tail
        # Push-to-talk
        self._ptt_enabled          = False
        self._ptt_held             = False
        self._ptt                  = None    # core.hotkey.PushToTalk
        self._out_level            = 0.0     # level of the audio being played right now
        self._echo                 = EchoGuard()
        # `stream.write()` returns when the buffer accepts the audio, not when the
        # speaker has finished with it, so sound is still in the room after the
        # speaking flag drops. Streaming the microphone during that gap is how an
        # assistant ends up answering itself. Measured from the device rather than
        # guessed; see _play_audio.
        self._out_latency          = 0.20    # seconds, replaced with the real value
        self._tail_until           = 0.0     # monotonic time the echo tail expires
        # Wall-clock time at which the audio written next will begin to sound.
        # The mouth is scheduled against this, never against "now": batches are
        # handed to the device far faster than they play, so "now" ran the lips
        # ahead of the words and cut every schedule short. 0 = nothing playing.
        self._play_cursor          = 0.0
        self.ui.on_push_to_talk   = self.set_push_to_talk
        self.ui.ptt_hold          = self._on_ptt
        self.ui.on_text_command   = self._on_text_command
        self.ui.on_remote_clicked = self._make_remote_key
        self._shutdown_requested = False
        self._runner_task = None
        self.ui._win.on_shutdown = self.shutdown
        # Set when run() has fully wound down; the window waits for it on close.
        self._stopped = threading.Event()
        self.ui._win.is_backend_running = lambda: (self._runner_task is not None
                                                   and not self._stopped.is_set())
        self.ui.on_interrupt      = self.interrupt
        self.ui.on_voice_change   = self._on_voice_change     # voice picker → rebuild session
        self.ui.on_audio_device_change = self._on_audio_device_change
        self.ui.on_intro_finished = self._on_intro_finished
        from core import claude_brain
        def _on_brain_alert(msg: str):
            self.ui.write_log(f"ALERT: {msg}")
            try:
                self.speak(msg)
            except Exception:
                pass
        claude_brain.register_alert_listener(_on_brain_alert)
        self._reconnect_event: asyncio.Event | None = None
        self._reconnect_keep = True   # False → next rebuild drops the resumption handle

        # ── Session resumption ─────────────────────────────────────────
        # The server issues a resumption handle every few seconds and reissues
        # it as the conversation moves on. Before this, session_resumption was
        # switched ON in the config and the update was never read, so the handle
        # was thrown away and EVERY reconnect — a dropped packet, a voice change,
        # switching microphone — started an empty session. "Unlimited sessions"
        # leaked through exactly this hole.
        #
        # Deliberately in RAM only, never written to disk. Persisting it would
        # make a fresh launch continue yesterday's conversation, which sounds
        # appealing but breaks the session-summary flow: _save_session_summary
        # runs at shutdown and the morning briefing pops it the next day. A
        # conversation that never ends never produces a summary, and the
        # "yesterday we talked about…" line silently disappears.
        self._resume_handle: str | None = None
        self._turn_done_event: asyncio.Event | None = None
        self._dashboard     = None
        self._briefing_sent    = False          # morning briefing fires once per process
        self._sys_monitor      = SystemMonitor()  # persistent cooldown state
        self._last_user_speech = time.monotonic()  # updated on every user utterance
        self._session_log: list[str] = []          # conversation turns for end-of-session summary

        self._enhanced_live = True  # proactive audio; auto-disabled if the server rejects it
        self._tuned_live    = True  # turn-taking / media / thinking knobs; same fallback

        _base_dir = Path(__file__).resolve().parent
        _inline_names = {t["name"] for t in TOOL_DECLARATIONS}

        # File-backed tools: every actions/*.py with a TOOL dict, discovered the
        # same way plugins are. Reserved names = the inline tools above, so an
        # action can never shadow one.
        self._action_registry = discover_actions(
            actions_dir=_base_dir / "actions",
            reserved_names=_inline_names,
            logger=lambda msg: print(f"[Actions] {msg}"),
        )

        # Plugins must not collide with either an inline tool or a discovered action.
        _core_names = _inline_names | self._action_registry.names()
        self._plugin_registry = discover_plugins(
            plugins_dir=_base_dir / "plugins",
            core_tool_names=_core_names,
            # Console gets the full boot transcript; the activity log gets only
            # what the user has to know about. Every plugin loading correctly is
            # the expected case and does not belong in their conversation.
            logger=lambda msg: print(f"[Plugins] {msg}"),
            notify=lambda msg: self.ui.write_log(f"SYS: {msg}"),
        )
        self.ui.get_plugins = self._plugin_registry.list_for_ui
        runtime_status.bind_plugin_refresh(lambda: self.request_reconnect(keep_context=True, reason="plugin settings"))
        self.ui.get_plugin_settings = self._plugin_registry.settings_schemas  # ⚙ settings tab
        self.ui.request_say = self.plugin_say   # plugins: mid-task speech channel

        # ── AI-first command routing ─────────────────────────────────────────
        # Typed commands go through the router: manual connection commands,
        # then Claude → Gemini (routing settings), then MCP/native tools. Voice
        # stays on the Live session; its tool calls pass _gate_tool below.
        self._conn = _connections.manager()
        self._router = CommandRouter(
            tool_runner=self.run_tool_sync,
            tool_catalog=self.tool_declarations,
            live_sender=self._send_to_live,
            live_reconnect=lambda: self.request_reconnect(keep_context=True,
                                                          reason="Gemini reconnect"),
            user_name=(self._cfg_user_name() or "the user"),
            recent_context=lambda: "\n".join(self._session_log[-8:]),
            manager=self._conn,
        )
        _set_router(self._router)
        self.ui.on_user_command = self._on_user_command
        self.ui.tool_catalog = self.tool_declarations
        self.ui.router = self._router
        self.ui.connections = self._conn
        self._conn.subscribe(self.ui.set_connections)
        self.ui.set_connections(self._conn.snapshot())

        # ── Wake word & Wake on Clap ─────────────────────────────────────────
        # _awake gates the mic (see _listen_audio) and the background speakers.
        # It is True whenever wake word and wake-on-clap are both OFF, so default behaviour is unchanged.
        self._wake_enabled     = get_wake_word_enabled()
        self._clap_enabled     = get_wake_on_clap_enabled()
        self._awake            = not (self._wake_enabled or self._clap_enabled)
        self._wake_detector: WakeWordDetector | None = None
        self._clap_detector    = ClapDetector(
            on_single_clap=self._on_single_clap_detected,
            on_double_clap=self._on_double_clap_detected,
            threshold=get_clap_threshold(),          # None unless explicitly saved
            sensitivity=get_clap_sensitivity(),
            sample_rate=SEND_SAMPLE_RATE,
            enabled=self._clap_enabled,
            logger=lambda m: print(f"[Clap] {m}"),
        )
        self._wake_sleep_timeout = WAKE_SLEEP_TIMEOUT

        # Restore the saved push-to-talk preference. Doing it here rather than
        # in __init__ means the hotkey thread only exists once there is a
        # session to talk to.
        if get_push_to_talk_enabled():
            try:
                self.set_push_to_talk(True)
            except Exception as e:
                print(f"[ULTRON] ⚠ Push-to-talk unavailable: {e}")
        # UI control surface for the Wake Word settings section.
        self.ui.wake_is_ready    = wake_is_ready          # () -> bool
        self.ui.wake_get_state   = self._wake_state       # () -> dict
        self.ui.on_wake_toggle   = self._ui_wake_toggle   # (enable: bool) -> str
        self.ui.on_clap_toggle   = self._ui_clap_toggle   # (enable: bool) -> bool
        self.ui.on_wake_manual   = self._ui_wake_manual   # () -> toggle awake/asleep
        self.ui.on_wake_install  = self._ui_wake_install  # () -> (ok, msg)

    # ── Wake word & Clap: state machine ──────────────────────────────────────

    def _wake_state(self) -> dict:
        # A loaded, running detector is definitively ready; otherwise fall back
        # to the cheap on-disk model-file check (no Model construction).
        ready = bool(self._wake_detector and self._wake_detector.ready) or wake_is_ready()
        return {"enabled": self._wake_enabled, "awake": self._awake, "ready": ready}

    def _ensure_wake_detector(self) -> bool:
        """Load the detector once (model loads on first start). Idempotent."""
        if self._wake_detector is None:
            self._wake_detector = WakeWordDetector(
                on_detect=self._on_wake_detected,
                logger=lambda m: print(f"[Wake] {m}"),
                notify=lambda m: self.ui.write_log(f"SYS: {m}"),
                name=self._cfg_assistant_name(),
            )
        if not self._wake_detector.ready:
            return self._wake_detector.start()
        return True

    def _on_wake_detected(self) -> None:
        """Called from the detector thread when 'Hey Ultron' is heard."""
        self.wake(reason="wake word")

    def _on_double_clap_detected(self) -> None:
        """Called by ClapDetector when two acoustic claps are heard in rapid succession."""
        if self._is_speaking:
            self.interrupt()
        if not self._awake:
            self.wake(reason="double clap")
        else:
            self._last_user_speech = time.monotonic()
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            self.ui.write_log("SYS: 👏 Double clap — listening for question.")

    def _on_single_clap_detected(self) -> None:
        """Called by ClapDetector when a single acoustic clap is heard with no follow-up."""
        if self._is_speaking:
            self.interrupt()
        if self._awake:
            self.sleep(reason="single clap")

    def _ui_clap_toggle(self, enable: bool) -> bool:
        self._clap_enabled = bool(enable)
        save_wake_on_clap_enabled(self._clap_enabled)
        if self._clap_detector is not None:
            self._clap_detector.enabled = self._clap_enabled
        if self._clap_enabled:
            self.sleep(reason="wake on clap enabled")
            self.ui.write_log("SYS: Wake on clap enabled (Double clap to listen, single clap to stop).")
        else:
            if not self._wake_enabled:
                self.wake(reason="wake on clap disabled")
            self.ui.write_log("SYS: Wake on clap disabled.")
        return self._clap_enabled

    def wake(self, reason: str = "wake word") -> None:
        if self._awake:
            return
        self._awake = True
        self._last_user_speech = time.monotonic()   # start the auto-sleep clock now
        if not self.ui.muted:
            self.ui.set_state("LISTENING")
        self.ui.write_log(f"SYS: Awake — {reason}.")

    def sleep(self, reason: str = "timeout") -> None:
        if not self._awake:
            return
        self._awake = False
        self.set_speaking(False)
        self.ui.set_state("SLEEPING")
        triggers = []
        if self._clap_enabled:
            triggers.append("double clap")
        if self._wake_enabled:
            triggers.append("say 'Hey Ultron'")
        if not triggers:
            triggers.append("tap wake")
        self.ui.write_log(f"SYS: Sleeping — {reason}. {' or '.join(triggers).capitalize()} to wake me.")

    async def _run_sleep_watch(self) -> None:
        """Auto-sleep after the configured silence window (wake-word / clap mode)."""
        while True:
            await asyncio.sleep(5)
            if not (self._wake_enabled or self._clap_enabled) or not self._awake:
                continue
            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue
            if (time.monotonic() - self._last_user_speech) > self._wake_sleep_timeout:
                self.sleep(reason="no speech for 2 minutes")

    # ── Wake word: UI callbacks (called from the Qt thread) ──────────────────

    def _ui_wake_toggle(self, enable: bool) -> str:
        """Enable/disable wake word from the settings UI. Returns a status token:
        'enabled' | 'disabled' | 'need_download'."""
        if enable:
            if not wake_is_ready():
                return "need_download"
            self._wake_enabled = True
            save_wake_word_enabled(True)
            self._ensure_wake_detector()
            self.sleep(reason="wake word enabled")
            return "enabled"
        else:
            self._wake_enabled = False
            save_wake_word_enabled(False)
            if not self._clap_enabled:
                self.wake(reason="wake word disabled")
            return "disabled"

    def _ui_wake_manual(self) -> None:
        """Manual sleep/wake button in the UI."""
        if not (self._wake_enabled or self._clap_enabled):
            return
        if self._awake:
            self.sleep(reason="you tapped sleep")
        else:
            self.wake(reason="you tapped wake")

    def _ui_wake_install(self) -> tuple[bool, str]:
        """Download openwakeword + the model (runs in a UI worker thread)."""
        # Triggered by the user pressing the button, so its progress is exactly
        # what they are waiting to see.
        return wake_install(logger=lambda m: print(f"[Wake] {m}"),
                            notify=lambda m: self.ui.write_log(f"SYS: {m}"))

    def _intro_playing(self) -> bool:
        """True only while the UI reports the welcome recording playing — a
        real True, so a UI without the feature (or a test double) never
        holds the microphone or the voice."""
        try:
            return self.ui.intro_active is True
        except Exception:
            return False

    def _on_intro_finished(self, reason: str) -> None:
        """The welcome recording has finished: hand over to the live voice —
        ULTRON's configured Gemini Live voice, a natural one — with a single
        short line. Skipped if the intro was skipped, if the morning briefing is
        about to greet the user anyway, or if the hand-off is switched off."""
        if reason != "end" or self.ui.muted or not self._awake or get_brief_enabled():
            return
        try:
            from memory.config_manager import get_animation_settings
            if not get_animation_settings().get("voice_handoff", True):
                return
        except Exception:
            pass
        self.plugin_say("[VOICE_HANDOFF] The recorded welcome introduction has just finished "
                        "playing. In ONE short, natural sentence in your own live voice, tell the "
                        "user you are online and ready. Do not repeat the recording.")

    def plugin_say(self, instruction: str) -> None:
        """
        Thread-safe speech channel for plugins: lets a plugin ask ULTRON to
        say something short WHILE its run() is still executing (plugins block
        their executor thread, so they can't speak through the tool response
        until they finish). The instruction is injected into the Live session
        exactly like a proactive check-in; Gemini phrases it naturally in the
        user's language. Silently a no-op when no session is connected.
        """
        loop = getattr(self, "_loop", None)
        if not loop or not self.session or self.ui.muted:
            return

        async def _say():
            if self.ui.muted or not self.session:
                return
            try:
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": instruction}]},
                    turn_complete=True,
                )
            except Exception as e:
                print(f"[PluginSay] {e}")

        try:
            asyncio.run_coroutine_threadsafe(_say(), loop)
        except Exception as e:
            print(f"[PluginSay] {e}")

    def request_reconnect(self, keep_context: bool = True, reason: str = ""):
        """Thread-safe: ask the run loop to tear down and rebuild the Live
        session. Called from the Qt thread. No-op until the async loop and
        reconnect event exist.

        `keep_context=False` drops the resumption handle so the new session
        starts empty — only for changes the server cannot apply to a resumed
        session."""
        loop = getattr(self, "_loop", None)
        ev   = self._reconnect_event
        self._reconnect_keep   = keep_context
        self._reconnect_reason = reason
        if loop and ev is not None:
            loop.call_soon_threadsafe(ev.set)

    def _on_voice_change(self):
        """Voice picker applied.

        The voice is baked into the session at connect time, so a rebuild is
        required. It is rebuilt WITHOUT the resumption handle on purpose:
        resuming restores the server's own session state, and the safe reading
        is that it restores the voice with it — which would make the picker
        appear to do nothing. Losing context here is acceptable because changing
        voice is a deliberate, rare act; losing it on a dropped packet was not."""
        self.request_reconnect(keep_context=False, reason="new voice")

    def _on_audio_device_change(self):
        """Microphone or speaker changed. Both streams are opened inside the
        session TaskGroup, so they can only be re-opened by rebuilding it —
        but the conversation is kept, which is the whole reason resumption
        landed before this feature did."""
        self.request_reconnect(keep_context=True, reason="audio device")

    async def _watch_reconnect(self):
        """Session-scoped task: when a voluntary reconnect is requested, raise a
        signal that unwinds the TaskGroup so the run loop rebuilds the session."""
        assert self._reconnect_event is not None
        await self._reconnect_event.wait()
        self._reconnect_event.clear()
        keep   = self._reconnect_keep
        reason = getattr(self, "_reconnect_reason", "") or "settings"
        self.ui.write_log(
            f"SYS: Applying {reason} — reconnecting"
            + ("..." if keep else " (starting a fresh conversation)...")
        )
        raise _ReconnectSignal(keep_context=keep)

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Optional phone dashboard is disabled. "
                "Enable ULTRON_ENABLE_REMOTE=1 before launch only if you want remote access."
            )
            return None
        key    = self._dashboard.new_key()
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    # ── AI-first routing: typed commands, tool gate, tool runner ─────────────

    def _cfg_assistant_name(self) -> str:
        try:
            return (json.loads(API_CONFIG_PATH.read_text(encoding="utf-8"))
                    .get("assistant_name") or "Ultron").strip()
        except Exception:
            return "Ultron"

    def _cfg_user_name(self) -> str:
        try:
            return (json.loads(API_CONFIG_PATH.read_text(encoding="utf-8"))
                    .get("user_name") or "").strip()
        except Exception:
            return ""

    def tool_declarations(self) -> list[dict]:
        """Every tool ULTRON can run natively, in declaration form."""
        return (list(TOOL_DECLARATIONS)
                + self._action_registry.get_tool_declarations()
                + self._plugin_registry.get_tool_declarations())

    def _gate_tool(self, name: str, args: dict, run) -> str | None:
        """Checks that run before ANY tool executes, voice or typed.

        Returns None to proceed, or the message to hand back instead. `run` is
        the real call, parked behind the HUD confirmation when the browser
        policy asks the user first."""
        policy = _connections.routing_settings()["browser_policy"]
        allowed, msg = browser_policy.gate(name, args, run, policy=policy,
                                           confirm=confirm_gate.request)
        if not allowed:
            self.ui.write_log(f"SYS: Browser not opened automatically — {name}.")
            return msg
        if name == "ask_claude":
            force = str(args.get("force_brain", "auto")).lower()
            if force in ("claude", "auto"):
                st = self._conn.ensure_fresh("claude")
                fallback = _connections.routing_settings()["allow_provider_fallback"]
                if not st.usable and (force == "claude" or not fallback):
                    return (f"Claude is {st.state.lower()}: {st.last_error or st.detail}. "
                            f"Not substituting another provider. The user can run "
                            f"'claude auth login' in a terminal or say 'connect Claude'.")
        return None

    def run_tool_sync(self, name: str, args: dict) -> str:
        """Run one native tool from a worker thread (the command router).

        Same gate as the voice path. Tools that only make sense inside the live
        voice session say so instead of pretending to run."""
        args = dict(args or {})

        def _run() -> str:
            return self._run_tool_now(name, args)

        blocked = self._gate_tool(name, args, _run)
        if blocked:
            return blocked
        runtime_status.update(tool=name, last_result="Running")
        try:
            result = _run()
        except Exception as e:
            traceback.print_exc()
            result = f"Tool '{name}' failed: {e}"
        runtime_status.update(tool=name, last_result=str(result)[:200])
        return result

    def _run_tool_now(self, name: str, args: dict) -> str:
        if name in ("screen_process", "shutdown_car"):
            return (f"'{name}' runs only inside the Gemini Live voice session"
                    + ("" if self.session else ", which is not connected") + ".")
        if name == "save_memory":
            if args.get("key") and args.get("value"):
                update_memory({args.get("category", "notes"):
                               {args["key"]: {"value": args["value"]}}})
            return "Saved."
        if name == "recall_memory":
            return search_memory(args.get("query", ""), limit=8)
        if name == "undo":
            if str(args.get("action", "")).lower().strip() == "list":
                items = undo_stack.history()
                return ("\n".join(f"{i+1}. {t}" for i, t in enumerate(items))
                        or "Nothing to undo.")
            return undo_stack.undo_last()
        if name == "system_status":
            return str(get_system_status())
        if name == "manage_monitor":
            action, topic = args.get("action", "").lower().strip(), args.get("topic", "").strip()
            if action == "add" and topic:
                return add_monitor(topic)
            if action == "remove" and topic:
                return remove_monitor(topic)
            topics = list_monitors()
            return ("Monitoring: " + ", ".join(topics)) if topics else "No topics are being monitored."
        if name == "open_camera":
            self.ui.start_camera_stream()
            return "Camera on."
        if name == "close_camera":
            self.ui.stop_camera_stream()
            return "Camera off."
        if name == "close_holo_hands":
            self.ui.hide_holo()
            return "Holo Hands closed."
        if name == "open_holo_hands":
            from core.holo_service import get_holo_service
            svc = get_holo_service()
            url = svc.ensure_running()
            if args.get("in_browser"):
                svc.open_browser()
                return f"Holo Hands opened in the browser at {url}."
            self.ui.show_holo()
            return "Holo Hands opened on the HUD."
        if self._action_registry.has(name):
            if name == "file_processor" and not args.get("file_path") and self.ui.current_file:
                args["file_path"] = self.ui.current_file
            ctx = {"player": self.ui, "speak": self.speak, "response": None,
                   "session_memory": None}
            return self._action_registry.run(name, args, ctx) or "Done."
        if self._plugin_registry.has(name):
            return self._plugin_registry.run(name, args, player=self.ui,
                                             session_memory=None) or "Done."
        return f"Unknown tool: {name}"

    def _send_to_live(self, text: str) -> bool:
        """Hand a typed command to the Live session. False when not connected."""
        if not self._loop or not self.session:
            return False
        self._interrupted = False
        try:
            asyncio.run_coroutine_threadsafe(
                self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": text}]},
                    turn_complete=True),
                self._loop)
            return True
        except Exception as e:
            print(f"[Router] Live send failed: {e}")
            return False

    def _handle_view_command(self, text: str) -> bool:
        spec = parse_theme_command(text)
        if spec is not None:
            res = self.ui.change_theme(spec.name if spec.is_rgb else spec.hex, source="typed")
            name = "RGB" if spec.is_rgb else spec.label.title()
            self.ui.write_log("SYS: " + {
                "started": f"Understood. Initializing {name} energy system.",
                "queued": f"{name} queued — it runs as soon as the current transition ends.",
                "same": f"{name} is already active.",
                "unavailable": "Shutting down — colour unchanged.",
            }.get(res, "The colour could not be changed."))
            return True
        cmd_lower = text.strip().lower()
        if any(p in cmd_lower for p in [
            "prebaci se na orbitu", "prebaci na orbitu", "idi na orbitu",
            "ukljuci orbitu", "uključi orbitu", "otvori orbitu", "prikazi orbitu", "prikaži orbitu",
            "switch to orbit", "show orbit", "open orbit"
        ]):
            self.ui.show_orbit()
            self.ui.write_log("SYS: Prebačeno na orbitu.")
            return True
        if any(p in cmd_lower for p in [
            "zatvori orbitu", "iskljuci orbitu", "isključi orbitu",
            "vrati se sa orbite", "vrati se na lice", "vrati se nazad",
            "normalan prikaz", "close orbit", "hide orbit"
        ]):
            self.ui.hide_orbit()
            self.ui.write_log("SYS: Vraćeno na standardni HUD.")
            return True
        return False

    def _on_user_command(self, text: str):
        """A command the user typed (chat box or a quick button)."""
        text = (text or "").strip()
        if not text:
            return
        if not self._awake:
            self.ui.write_log("SYS: I'm asleep — clap twice, say 'Hey Ultron', or tap WAKE first.")
            return
        if self._handle_view_command(text):
            return
        self._session_log.append(f"User: {text}")
        res = self._router.route(text)
        if res.path == "gemini_live":
            return          # the answer arrives through the Live transcript
        out = res.chat_text() or ("Done." if res.ok else "Failed.")
        via = {"claude": "Claude", "gemini": "Gemini"}.get(res.provider, "")
        tag = f" [{via}]" if via else ""
        if res.ok:
            self.ui.write_log(f"{self._asst_name}{tag}: {out}")
            self._session_log.append(f"{self._asst_name}: {out[:400]}")
        else:
            self.ui.write_log(f"ERR{tag}: {out}")

    def _on_text_command(self, text: str):
        """Internal messages for the live conversation (quiz results, file
        upload notices, clipboard actions). Typed commands use _on_user_command."""
        if not self._loop or not self.session:
            self.ui.write_log("SYS: Gemini Live is not connected, so this was not sent. "
                              "Say or type 'check AI connections' for details.")
            return
        # Respect wake sleep: a typed command must not be answered while
        # asleep either. Wake first with double clap, "Hey Ultron", or the WAKE button.
        if not self._awake:
            self.ui.write_log("SYS: I'm asleep — clap twice, say 'Hey Ultron', or tap WAKE first.")
            return

        cmd_lower = text.strip().lower()
        if any(p in cmd_lower for p in [
            "prebaci se na orbitu", "prebaci na orbitu", "idi na orbitu",
            "ukljuci orbitu", "uključi orbitu", "otvori orbitu", "prikazi orbitu", "prikaži orbitu",
            "switch to orbit", "show orbit", "open orbit"
        ]):
            self.ui.show_orbit()
            self.ui.write_log("SYS: Prebačeno na orbitu.")
            return
        elif any(p in cmd_lower for p in [
            "zatvori orbitu", "iskljuci orbitu", "isključi orbitu",
            "vrati se sa orbite", "vrati se na lice", "vrati se nazad",
            "normalan prikaz", "close orbit", "hide orbit"
        ]):
            self.ui.hide_orbit()
            self.ui.write_log("SYS: Vraćeno na standardni HUD.")
            return

        self._interrupted = False  # a direct new request may start a new answer
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"role": "user", "parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def _tail_active(self) -> bool:
        """True while the speakers may still be finishing our last sentence."""
        return time.monotonic() < self._tail_until

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self._tail_until = 0.0
        else:
            # Hold the guard open across the device's own output latency plus a
            # margin for the room. The microphone is NOT muted during it — the
            # guard still lets a genuine reply through, so answering instantly
            # still works. Only our own echo is dropped.
            self._tail_until = time.monotonic() + self._out_latency + _TAIL_MARGIN
        if not value:
            # The echo history is deliberately NOT cleared here: the tail above
            # still needs it to recognise our own voice. It is dropped when the
            # tail expires. What the guard learned about the room always stays.
            self._out_level = 0.0
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def set_push_to_talk(self, enabled: bool) -> str:
        """Turn hold-to-talk on or off. Returns the scope actually achieved."""
        from core.hotkey import PushToTalk

        self._ptt_enabled = bool(enabled)
        self._ptt_held = False
        if not enabled:
            if self._ptt is not None:
                self._ptt.stop()
                self._ptt = None
            return "off"

        if self._ptt is None:
            self._ptt = PushToTalk(self._on_ptt)
        scope = self._ptt.start()
        # A window-scoped chord is a real limitation, not a detail — say it once
        # in the log so nobody wonders why it does nothing while another app is
        # focused. Reporting it must never be able to undo the thing it reports.
        try:
            self.ui.write_log(
                f"SYS: Push-to-talk on — hold {self._ptt.label}"
                + ("." if scope == "global"
                   else " (works while this window is focused)."))
        except Exception:
            pass
        return scope

    def _on_ptt(self, held: bool) -> None:
        """Chord pressed or released — may arrive on the hotkey thread."""
        self._ptt_held = held
        if held:
            # Holding the key is also a way to wake it, so push-to-talk works
            # without having to say the wake word first.
            if self._wake_enabled and not self._awake:
                self._awake = True
                self._last_user_speech = time.monotonic()
        try:
            self.ui.set_state("LISTENING" if held else "SLEEPING")
        except Exception:
            pass

    def shutdown(self):
        """Stop live tasks and streams without another reconnect."""
        self._shutdown_requested = True
        loop = getattr(self, "_loop", None)
        if loop and not loop.is_closed() and self._runner_task:
            loop.call_soon_threadsafe(self._runner_task.cancel)

    def interrupt(self) -> None:
        """Stop ULTRON mid-speech: drain queued audio and open mic immediately."""
        self._interrupted = True
        stream = getattr(self, "_speaker_stream", None)
        if stream is not None:
            try:
                stream.abort(ignore_errors=True)
                stream.start()
            except Exception:
                pass
        q = self.audio_in_queue
        if q:
            drained = 0
            while True:
                try:
                    q.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                print(f"[ULTRON] ✋ Interrupted — {drained} audio chunks discarded")
        self.set_speaking(False)
        # The words we were about to mouth are never going to be spoken now.
        self._visemes.reset()
        self._play_cursor = 0.0     # next batch starts a fresh timeline
        if self._turn_done_event:
            self._turn_done_event.clear()
        self.ui.write_log("SYS: Interrupted — listening...")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"role": "user", "parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        # Load customization from config
        try:
            _cfg = json.loads(open(API_CONFIG_PATH, encoding="utf-8").read())
            self._asst_name = (_cfg.get("assistant_name") or "ULTRON").strip()
            _user_name = (_cfg.get("user_name") or "").strip()
        except Exception:
            self._asst_name = "ULTRON"
            _user_name = ""

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        # Identity injection — overrides any hardcoded name in prompt.txt
        # Address form is a property of the language being spoken, so it is
        # stated as a principle rather than a two-language lookup — the model
        # already knows the respectful register of whatever language it is in.
        _addr = (f"ADDRESS: Always call the user '{_user_name}'."
                 if _user_name
                 else 'ADDRESS: Address the user with the ordinary respectful form '
                      'for a superior in the language you are currently speaking — '
                      '"sir" in English, its everyday equivalent in any other '
                      'language. Never an archaic or aristocratic form, and never '
                      'the form from a different language than the one you are '
                      'speaking in this sentence.')
        identity_ctx = (
            f"[IDENTITY]\n"
            f"Your name is {self._asst_name}. "
            f"Always refer to yourself as {self._asst_name}.\n"
            f"{_addr}\n\n"
        )

        # Everything the model is told about *itself* is derived here, not
        # written into prompt.txt: the name comes from config, the platform from
        # the host, the capability list from the registries that were just
        # discovered. Rename the assistant, add a plugin or move to another OS
        # and this follows without anyone editing a prompt.
        _all_decls = (TOOL_DECLARATIONS
                      + self._action_registry.get_tool_declarations()
                      + self._plugin_registry.get_tool_declarations())
        _names = {(d.get("name") if isinstance(d, dict) else getattr(d, "name", ""))
                  for d in _all_decls}
        sys_prompt = _render_prompt(sys_prompt, {
            "assistant_name": self._asst_name,
            "platform": f"{_platform.system()} {_platform.release()}".strip(),
            "capabilities": _describe_tools(_all_decls),
            "limits": _describe_limits(
                has_vision="screen_process" in _names,
                has_mic=True,
            ),
        })

        parts = [time_ctx, identity_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        cfg = dict(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": _all_decls}],
            # Asks the server to issue session-resumption handles. Enabled but
            # NOT yet used: no handle is captured from the resumption update and
            # none is passed back here, so a reconnect starts a fresh session
            # rather than continuing the old one. Left on because the handles
            # cost nothing and are the prerequisite for doing it properly.
            # Hand back the handle captured from the last session_resumption
            # update. `handle=None` is exactly the old behaviour (ask for
            # handles, start fresh), so the first connect of a run is unchanged.
            session_resumption=types.SessionResumptionConfig(
                handle=self._resume_handle
            ),
            # Sliding-window compression: session never dies from a full context
            # window — ULTRON can stay in one conversation for hours
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=get_voice()
                    )
                )
            ),
        )
        if self._enhanced_live:
            # Proactive audio: ULTRON stays silent when speech isn't addressed
            # to it (background chatter, talking to someone else in the room).
            # (Affective dialog was dropped: gemini-3.1-flash-live does not
            #  support it, and it never reliably detected tone in practice.
            #  To restore it on a 2.5 native-audio model, add back:
            #  cfg["enable_affective_dialog"] = True )
            cfg["proactivity"] = types.ProactivityConfig(proactive_audio=False)

        if self._tuned_live:
            cfg.update(self._tuning_config())

        return types.LiveConnectConfig(**cfg)

    def _tuning_config(self) -> dict:
        """The optional knobs, kept apart so one bad field can be dropped wholesale.

        Every one of these is a preview-API field. If a future model release
        stops accepting any of them the connection fails at setup, so the run
        loop turns `_tuned_live` off and reconnects on the plain config rather
        than leaving the user with an assistant that will not start.
        """
        out: dict = {}

        # How long the server waits through a pause before deciding your turn is
        # over. This — not the size of the prompt — is what most of the delay
        # before a reply actually is, and the default has to suit everybody, so
        # it is necessarily cautious.
        turn = get_turn_tuning()
        if turn.get("enabled", True):
            detect = types.AutomaticActivityDetection(
                silence_duration_ms=turn["silence_ms"],
                prefix_padding_ms=turn["prefix_ms"],
            )
            if turn["end_sensitivity"] == "high":
                detect.end_of_speech_sensitivity = types.EndSensitivity.END_SENSITIVITY_HIGH
            elif turn["end_sensitivity"] == "low":
                detect.end_of_speech_sensitivity = types.EndSensitivity.END_SENSITIVITY_LOW
            if turn["start_sensitivity"] == "high":
                detect.start_of_speech_sensitivity = types.StartSensitivity.START_SENSITIVITY_HIGH
            elif turn["start_sensitivity"] == "low":
                detect.start_of_speech_sensitivity = types.StartSensitivity.START_SENSITIVITY_LOW
            out["realtime_input_config"] = types.RealtimeInputConfig(
                automatic_activity_detection=detect)

        # Screenshots and camera frames are tokenised at this resolution and then
        # stay in the session's context. 'medium' keeps on-screen text legible
        # for a fraction of a full-resolution frame.
        res = get_media_resolution()
        if res != "default":
            out["media_resolution"] = {
                "low":    types.MediaResolution.MEDIA_RESOLUTION_LOW,
                "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
                "high":   types.MediaResolution.MEDIA_RESOLUTION_HIGH,
            }[res]

        # Thinking is left at the server default deliberately. Forcing the budget
        # to zero was measured on gemini-3.1-flash-live over interleaved trials
        # and did not make the first word arrive sooner — this model does not
        # appear to deliberate on the Live path, so pinning the field only adds a
        # way for a future release to behave differently. Set "thinking_enabled"
        # in config/api_keys.json to true to let it reason instead.
        if get_thinking_enabled():
            out["thinking_config"] = types.ThinkingConfig(thinking_budget=-1)

        return out

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[ULTRON] Tool: {name}")
        runtime_status.update(tool=name, last_result="Running")
        self.ui.set_state("THINKING")


        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        # Capability and browser checks — the same gate typed commands pass.
        # A blocked call returns its reason to the model instead of running.
        blocked = self._gate_tool(name, args, lambda: self._run_tool_now(name, dict(args)))
        if blocked:
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            runtime_status.update(tool=name, last_result=blocked[:200])
            self._conn.report_task(state="FAILED", provider="gemini", tool=name,
                                   stage="Blocked", result=blocked[:300])
            return types.FunctionResponse(id=fc.id, name=name,
                                          response={"result": blocked})
        self._conn.report_task(state="PROCESSING", provider="gemini", tool=name,
                               stage=f"Executing · {name}")

        try:
            if name == "recall_memory":
                # Local file search: no network, no second model. Kept out of
                # the executor deliberately — it is a dictionary scan over a few
                # hundred short strings, and a thread hop would cost more than
                # the work itself.
                result = search_memory(args.get("query", ""), limit=8)

            elif name == "undo":
                if str(args.get("action", "")).lower().strip() == "list":
                    items = undo_stack.history()
                    result = ("Things I can undo, most recent first:\n"
                              + "\n".join(f"{i+1}. {t}" for i, t in enumerate(items))
                              ) if items else "I have not changed anything I can undo yet."
                else:
                    result = await loop.run_in_executor(None, undo_stack.undo_last)

            elif name == "screen_process":
                import time as _t_mod
                _now = _t_mod.monotonic()
                _cooldown = 4.0  # seconds — covers echo window after speaking ends
                if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
                    _wait = max(0, _cooldown - (_now - self._vision_last_time))
                    print(f"[Vision] ⏳ Cooldown active ({_wait:.1f}s remaining) — ignoring duplicate call")
                    result = "Vision is still processing the previous request. I will not call this again."
                else:
                    self._vision_busy      = True
                    self._vision_last_time = _now
                    angle     = args.get("angle", "screen").lower()
                    user_text = args.get("text", "What do you see?")
                    if angle == "camera":
                        img_b, mime_t = await loop.run_in_executor(None, _capture_camera)
                        self.ui.start_camera_stream()
                        self._vision_cam_active = True
                        print(f"[Vision] 📷 Camera: {len(img_b):,} bytes")
                        _stall = "camera"
                    else:
                        img_b, mime_t = await loop.run_in_executor(None, _capture_screen)
                        print(f"[Vision] 🖥️  Screen: {len(img_b):,} bytes")
                        _stall = "screen"
                    self._pending_vision = (img_b, mime_t, user_text, angle)
                    # The image is attached to this same exchange, so there is
                    # nothing to stall for and nothing to announce. Asking for an
                    # acknowledgement here is what produced two spoken answers —
                    # the model filled that turn by answering the question from
                    # imagination, then answered it again once it could see.
                    result = (
                        f"[VISION_ACTIVE] {_stall.capitalize()} captured and attached to this "
                        f"same exchange. Do not acknowledge and do not answer yet — the image "
                        f"is arriving with this result. Reply once, from what you actually see "
                        f"in it."
                    )

            elif name == "open_camera":
                self.ui.start_camera_stream()
                result = "Kamera je uključena i ostaje upaljena nonstop. Slobodno potvrdi korisniku da kamera radi uživo."

            elif name == "close_camera":
                self.ui.stop_camera_stream()
                result = "Kamera je isključena."

            elif name == "open_holo_hands":
                in_browser = bool(args.get("in_browser", False))
                try:
                    from core.holo_service import get_holo_service
                    svc = get_holo_service()
                    url = svc.ensure_running()
                    if in_browser:
                        svc.open_browser()
                        result = f"Holo Hands deck pokrenut u pretraživaču na adresi {url}."
                    else:
                        if hasattr(self.ui, "show_holo"):
                            self.ui.show_holo()
                            result = f"Holo Hands 3D deck je aktiviran na glavnom ekranu (takođe dostupan na {url})."
                        else:
                            svc.open_browser()
                            result = f"Holo Hands deck pokrenut na {url}."
                except Exception as e:
                    result = f"Greška pri pokretanju Holo Hands: {e}"

            elif name == "close_holo_hands":
                if hasattr(self.ui, "hide_holo"):
                    self.ui.hide_holo()
                result = "Holo Hands 3D prikaz je zatvoren."

            elif name == "system_status":
                r = await loop.run_in_executor(None, get_system_status)
                result = str(r)

            elif name == "manage_monitor":
                action = args.get("action", "").lower().strip()
                topic  = args.get("topic", "").strip()
                if action == "add" and topic:
                    result = await asyncio.to_thread(add_monitor, topic)
                elif action == "remove" and topic:
                    result = await asyncio.to_thread(remove_monitor, topic)
                elif action == "list":
                    topics = await asyncio.to_thread(list_monitors)
                    result = ("Monitoring: " + ", ".join(topics)) if topics else "No topics are being monitored."
                else:
                    result = "Specify action (add/remove/list) and a topic."

            elif name == "shutdown_car":
                self.ui.write_log("SYS: Shutdown requested.")
                async def _do_shutdown():
                    # ULTRON answers first, in its live voice…
                    if self.session:
                        try:
                            await self.session.send_client_content(
                                turns={"role": "user", "parts": [{"text":
                                    "Say exactly this, nothing else: \"Powering down, sir.\""}]},
                                turn_complete=True,
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(1.4)
                    # …then the slash, while the session summary is saved; the
                    # process exits once the cinematic has finished.
                    try:
                        self.ui.begin_shutdown_animation()
                    except Exception:
                        pass
                    await self._save_session_summary()
                    # Bounded: an animation never holds the exit hostage.
                    try:
                        await asyncio.to_thread(self.ui.wait_shutdown_animation, 12.0)
                    except Exception:
                        pass
                    try:
                        import plugins.smartthings as st
                        st.on_app_exit()
                    except Exception:
                        pass
                    try:
                        import requests
                        requests.post("http://localhost:31337/shutdown", timeout=2)
                    except Exception:
                        pass
                    import os as _os
                    _os.system('taskkill /FI "WINDOWTITLE eq Ultron Telegram Bot*" /T /F >nul 2>&1')
                    _os._exit(0)
                asyncio.create_task(_do_shutdown())

            elif self._action_registry.has(name):
                # file_processor: fall back to the currently-uploaded file when none is given
                if name == "file_processor" and not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                _ctx = {"player": self.ui, "speak": self.speak,
                        "response": None, "session_memory": None}
                r = await loop.run_in_executor(None, lambda: self._action_registry.run(name, args, _ctx))
                result = r or "Done."
                # web_search: mirror results to the on-screen content panel
                if (name == "web_search" and r
                        and not r.startswith("No results")
                        and not r.startswith("Search failed")):
                    _mode  = args.get("mode", "search")
                    _query = args.get("query") or ", ".join(args.get("items", []))
                    _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
                    self.ui.show_content(_label, r)

            else:
                if self._plugin_registry.has(name):
                    r = await loop.run_in_executor(
                        None,
                        lambda: self._plugin_registry.run(name, args, player=self.ui, session_memory=None)
                    )
                    result = r or "Done."
                else:
                    result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        runtime_status.update(tool=name, last_result=str(result)[:200])
        _failed = str(result).startswith(("Tool '", "Unknown tool", "[BROWSER_BLOCKED]"))
        self._conn.report_task(state="FAILED" if _failed else "COMPLETED", provider="gemini",
                               tool=name, stage="Failed" if _failed else "Completed",
                               result=str(result)[:300])
        print(f"[ULTRON] Tool returned: {name}")

        # A tool that declared itself NON_BLOCKING also says when its answer may
        # re-enter the conversation. Without this the model finishes whatever it
        # was saying and then reads the result out on top of it — which, for
        # something like a phone call already ringing, is exactly the noise the
        # non-blocking call was meant to avoid. Tools that declared nothing get
        # the API default and behave as they always have.
        _sched = (self._action_registry.scheduling(name)
                  or self._plugin_registry.scheduling(name))
        _extra = {"scheduling": _sched} if _sched else {}
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result},
            **_extra
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            # Gemini 3.x Live rejects the old realtime_input.media_chunks field
            # (what `media=...` maps to) and closes the socket with a 1007. Send
            # mic / phone PCM through the new `audio` field instead. Queue items
            # are {"data": <bytes>, "mime_type": <str>} from _listen_audio and
            # the phone relay.
            await self.session.send_realtime_input(
                audio=types.Blob(
                    data=msg["data"],
                    mime_type=msg.get("mime_type", "audio/pcm"),
                )
            )

    async def _listen_audio(self):
        print("[ULTRON] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            # ── Welcome narration ────────────────────────────────────────────
            # While the recorded welcome plays nothing is streamed, detected or
            # woken: it says "I am Ultron" through the speakers, and its sound
            # effects would read as claps.
            if self._intro_playing():
                return
            # ── Acoustic Clap Detector ───────────────────────────────────────
            # Must run on incoming PCM frames even when asleep or speaking,
            # so double-clap can wake up and single-clap can stop/sleep/interrupt.
            if self._clap_enabled and self._clap_detector is not None:
                self._clap_detector.feed(indata)

            # ── Wake gate (wake word / clap / manual sleep) ──────────────────
            # While asleep, the mic audio NEVER goes to Gemini (nothing is
            # streamed, so ULTRON can't respond to speech not addressed to it and
            # nothing leaves the machine). Frames are instead handed to the local
            # detector if wake word is active.
            if not self._awake:
                if self._wake_enabled:
                    det = self._wake_detector
                    if det is not None:
                        det.feed(indata)
                return
            with self._speaking_lock:
                car_speaking = self._is_speaking

            # ── Barge-in ─────────────────────────────────────────────────────
            # While ULTRON talks the mic is not streamed, but it is still worth
            # listening to locally: if the user starts speaking, cut the answer
            # short the way a person would stop when interrupted.
            #
            # The whole difficulty is echo — on speakers the mic hears ULTRON.
            # So the test is not "is the mic loud" but "is the mic louder than
            # the echo of what we are playing right now", sustained long enough
            # that a cough or a keystroke cannot trigger it.
            if car_speaking:
                # Nothing is streamed while ULTRON talks.
                #
                # Interrupting by voice used to live here: `EchoGuard` can pick a
                # user out from under our own echo, and `core/echo.py` still does
                # that for the tail below. Re-enabling is small — classify each
                # block here and call interrupt() after `required_blocks` of
                # agreement — but it depends on the listener's room, so it stays
                # out until it can be tried on real hardware.
                return

            # ── Echo tail ────────────────────────────────────────────────────
            # The speaking flag has dropped but the speakers have not finished.
            # Sending this to the model is how an assistant hears itself, decides
            # it was addressed, and answers its own last sentence. The microphone
            # stays OPEN — the guard only drops blocks that are our own voice, so
            # replying the instant it stops still works.
            if self._tail_active():
                try:
                    if not self._echo.is_user_speech(
                            indata, SEND_SAMPLE_RATE, _pcm_level(indata)):
                        return
                    self._tail_until = 0.0      # a real voice ends the tail early
                except Exception:
                    return
            elif self._echo._hist:
                self._echo.reset()

            # ── Push-to-talk ─────────────────────────────────────────────────
            # When it is on the microphone is closed by default and the chord
            # opens it, which is the whole point: nothing leaves the machine
            # unless you are holding the key.
            if self._ptt_enabled and not self._ptt_held:
                return

            if not self.ui.muted and not self._phone_active:
                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )
                # Feed the live mic level to the HUD so the waveform reacts to
                # the user's actual voice while listening. Purely cosmetic — any
                # failure here must never disturb the mic.
                try:
                    self.ui.set_audio_level(_pcm_level(indata))
                except Exception:
                    pass

        try:
            def _open_mic(dev):
                return sd.InputStream(
                    samplerate=SEND_SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                    blocksize=CHUNK_SIZE,
                    device=dev,
                    callback=callback,
                )

            # Which microphone. resolve() returns None for "system default" and
            # for a saved device that is no longer present — so a headset
            # unplugged since the last run falls back to the built-in mic
            # instead of raising on startup and taking the session with it.
            _mic_name = get_input_device()
            _mic_dev  = audio_devices.resolve(_mic_name, "input")
            if _mic_dev is not None:
                print(f"[ULTRON] 🎤 Input device: {_mic_name}")
            try:
                _mic_stream = _open_mic(_mic_dev)
            except Exception as _e:
                # A device the picker listed but the driver will not open right
                # now — exclusive mode, a webcam already in use, a virtual mic
                # whose source went away. Chosen hardware failing must never
                # mean the assistant cannot hear at all.
                if _mic_dev is None:
                    raise
                print(f"[ULTRON] ⚠️  Mic '{_mic_name}' failed: {_e} — using default")
                self.ui.write_log(
                    f"SYS: Microphone '{_mic_name}' unavailable — using system default."
                )
                _mic_stream = _open_mic(None)

            with _mic_stream:
                print("[ULTRON] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[ULTRON] ❌ Mic: {e}")
            raise

    async def _flush_pending_vision(self) -> bool:
        """Send a captured frame immediately after its tool response.

        The frame is already in hand by the time `screen_process` returns — the
        capture happened inside the tool call. The old flow still made the model
        speak a turn first and only injected the image on that turn's
        turn_complete, which cost a whole extra round trip AND produced two
        spoken answers: one improvised without the picture, then the real one.
        Sending it here means the model has the tool result and the image before
        it generates anything, so the user gets one answer, sooner.
        """
        if not (self._pending_vision and self.session):
            return False

        import base64 as _b64
        img_b, mime_t, question, angle = self._pending_vision
        self._pending_vision = None
        b64 = _b64.b64encode(img_b).decode("ascii")
        print(f"[Vision] 📤 {len(img_b):,} bytes (angle={angle}) → main session")

        # Label the source. Without it the image arrives carrying nothing but
        # the user's own sentence, and a screenshot of this app — which has a
        # face in the middle of it — got read as a photo of the user. What the
        # label *means* is explained once, in the generated [SELF] block.
        src = ("[IMAGE SOURCE: WEBCAM]" if angle == "camera"
               else "[IMAGE SOURCE: SCREEN CAPTURE]")
        await self.session.send_client_content(
            turns={"role": "user", "parts": [
                {"inline_data": {"mime_type": mime_t, "data": b64}},
                {"text": f"{src}\n\n{question}"},
            ]},
            turn_complete=True,
        )

        if self._vision_cam_active:
            # Camera: keep stream running nonstop, clear vision active flag
            self._vision_cam_active    = False
            self._vision_close_pending = False
            self._vision_busy          = False
        else:
            self._vision_busy = False
        return True

    async def _receive_audio(self):
        print("[ULTRON] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():
                    incoming = getattr(getattr(response, "server_content", None), "input_transcription", None)
                    if incoming and getattr(incoming, "text", None) and not self.ui.muted:
                        self._interrupted = False

                    # ── Session resumption ───────────────────────────────────
                    # The server sends this periodically. `resumable` goes false
                    # while a turn is mid-flight — replaying a handle from that
                    # moment is what the flag exists to prevent — so only
                    # resumable handles are kept. This is three lines and it is
                    # the entire fix for "every reconnect forgets everything".
                    _sru = getattr(response, "session_resumption_update", None)
                    if _sru is not None:
                        if getattr(_sru, "resumable", False) and getattr(_sru, "new_handle", None):
                            if self._resume_handle is None:
                                print("[ULTRON] 🔗 Session resumption armed")
                            self._resume_handle = _sru.new_handle

                    if response.data:
                        if self._interrupted or self.ui.muted:
                            pass  # discard interrupted or muted output
                        else:
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                            # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                            _audio_data = response.data
                            _SLICE = 2400
                            for _i in range(0, len(_audio_data), _SLICE):
                                self.audio_in_queue.put_nowait(_audio_data[_i : _i + _SLICE])

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            # A turn that involves a tool call passes through
                            # several turn_completes, and the API re-sends the
                            # tail of the transcript across them. Comparing only
                            # against the previous chunk missed that — once
                            # out_buf had been flushed and emptied, the repeat
                            # sailed straight back in, which logged the answer
                            # twice AND made the avatar mouth it twice.
                            if txt and not _is_repeat_chunk(txt, out_buf):
                                out_buf.append(txt)
                                # Hand the words to the mouth as they arrive, so
                                # the avatar can form the consonants the audio
                                # alone cannot show. Pure string work — it adds
                                # nothing measurable to the response path.
                                self._visemes.feed_text(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)
                                self._last_user_speech = time.monotonic()
                                # The browser policy reads the user's own words.
                                browser_policy.note_user_fragment(txt)

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # If this turn_complete ends an interrupted response, clear the
                            # flag and skip all further processing for that turn.
                            if self._interrupted:
                                self._interrupted = False
                                in_buf  = []
                                out_buf = []
                                self._visemes.reset()
                                continue

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self._last_out_logged = ""   # new exchange
                                browser_policy.note_user_text(full_in)
                                self.ui.write_log(f"You: {full_in}")
                                self._session_log.append(f"User: {full_in}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            # Second line of defence: even if a repeat slips
                            # into a *fresh* buffer after a flush, never log the
                            # same answer (or a tail of it) twice in a row.
                            if full_out and len(full_out) >= _REPEAT_MIN and self._last_out_logged:
                                if full_out in self._last_out_logged:
                                    full_out = ""
                            if full_out:
                                self._last_out_logged = full_out
                                self.ui.write_log(f"{self._asst_name}: {full_out}")
                                self._session_log.append(f"{self._asst_name}: {full_out}")
                                try:
                                    import os
                                    os.makedirs(BASE_DIR / "telegram_temp", exist_ok=True)
                                    with open(BASE_DIR / "telegram_temp" / "last_speech.txt", "w", encoding="utf-8") as f:
                                        f.write(full_out)
                                except Exception:
                                    pass
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "car",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            out_buf = []

                            if self._vision_close_pending:
                                self._vision_close_pending = False
                                self._vision_busy = False

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[ULTRON] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
                        await self._flush_pending_vision()
        except Exception as e:
            print(f"[ULTRON] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[ULTRON] 🔊 Play started")

        _spk_name = get_output_device()
        _spk_dev  = audio_devices.resolve(_spk_name, "output")
        if _spk_dev is not None:
            print(f"[ULTRON] 🔊 Output device: {_spk_name}")

        def _open_spk(dev):
            st = sd.RawOutputStream(
                samplerate=RECEIVE_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                device=dev,
            )
            st.start()
            return st

        try:
            stream = _open_spk(_spk_dev)
        except Exception as _e:
            # A chosen output that the host API accepts by name but refuses to
            # open (exclusive mode, wrong sample rate, device asleep) must not
            # cost the user their voice. Fall back to the default and say so.
            if _spk_dev is None:
                raise
            print(f"[ULTRON] ⚠️  Output device '{_spk_name}' failed: {_e} — using default")
            self.ui.write_log(f"SYS: Speaker '{_spk_name}' unavailable — using system default.")
            stream = _open_spk(None)
        self._speaker_stream = stream

        # Ask the device how far behind the speakers actually are, rather than
        # assuming. This is what the echo tail is sized from, so a machine with a
        # large audio buffer gets a correspondingly longer guard — and one with a
        # tiny buffer is not penalised with a delay it does not need.
        try:
            lat = float(getattr(stream, "latency", 0.0) or 0.0)
            if 0.0 < lat < 1.0:
                self._out_latency = lat
            print(f"[ULTRON] 🔊 Output latency {self._out_latency*1000:.0f} ms "
                  f"→ echo tail {(self._out_latency + _TAIL_MARGIN)*1000:.0f} ms")
        except Exception:
            pass

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue

                # The welcome narration has the floor: hold this speech until it ends.
                while self._intro_playing() and not self._interrupted:
                    await asyncio.sleep(0.05)
                if self.ui.muted or self._interrupted:
                    continue
                self.set_speaking(True)

                # Batch all immediately-available chunks into one write to reduce
                # thread-pool round-trips (was one asyncio.to_thread per 50ms slice).
                # Cap at ~200 ms so interrupt() still stops audio within ~200 ms.
                batch = bytearray(chunk)
                while len(batch) < 9600:   # 9600 bytes ≈ 200 ms at 24 kHz / 16-bit mono
                    try:
                        batch.extend(self.audio_in_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                # Drive the HUD waveform and the avatar's mouth from ULTRON's
                # own voice. The batch is up to 200 ms long, so we hand over a
                # *schedule* of 20 ms viseme frames instead of a single averaged
                # level and let the HUD play it out in step with the audio.
                try:
                    pcm = np.frombuffer(bytes(batch), dtype=np.int16)
                    hop = _VIS_HOP / RECEIVE_SAMPLE_RATE
                    frames = _pcm_visemes(pcm, sr=RECEIVE_SAMPLE_RATE)
                    # When does this batch become audible? The stream was
                    # started at launch and its callback has been pulling
                    # silence ever since, so the first bytes of a reply reach
                    # the speaker about one callback period later — NOT one
                    # buffer later. `stream.latency` reports the buffer's
                    # capacity, which is how much can be queued ahead, and on
                    # Windows that is commonly 300-500 ms. Anchoring on it put
                    # the entire schedule a buffer late; that is the half second
                    # of lag, and it grew with whatever the device reported.
                    #
                    # After the anchor nothing needs measuring: the device
                    # consumes at exactly realtime, so each batch sounds one
                    # batch-duration after the one before it. The cursor is
                    # re-anchored only when it leaves the range physically
                    # possible — behind `now` means the device drained and this
                    # batch starts a fresh stretch of speech, while further
                    # ahead than the buffer can hold means it has drifted.
                    now = time.time()
                    horizon = self._out_latency + _CURSOR_SLACK
                    if not (now <= self._play_cursor <= now + horizon):
                        self._play_cursor = now + _FIRST_SOUND
                    at = self._play_cursor
                    # Advance by the batch's own duration whether or not it
                    # yielded frames, so a block too short to analyse cannot
                    # shift everything after it out of step with the audio.
                    self._play_cursor += pcm.size / RECEIVE_SAMPLE_RATE
                    if frames:
                        frames = self._visemes.frames(frames, hop)
                        self.ui.push_visemes(frames, hop, at)
                        # Barge-in needs to know what we are playing, not just
                        # how loud: the guard subtracts this from the microphone.
                        self._out_level = max(f[0] for f in frames)
                        self._echo.note_output(pcm, RECEIVE_SAMPLE_RATE,
                                               self._out_level)
                    else:
                        lvl = _pcm_level(pcm)
                        self.ui.set_audio_level(lvl)
                        self._out_level = lvl
                        self._echo.note_output(pcm, RECEIVE_SAMPLE_RATE, lvl)
                except Exception:
                    pass

                try:
                    if not self.ui.muted and not self._interrupted:
                        await asyncio.to_thread(stream.write, bytes(batch))
                except sd.PortAudioError:
                    if self.ui.muted or self._interrupted:
                        continue
                    raise
                except (RuntimeError, asyncio.CancelledError):
                    break   # executor shutting down — exit cleanly
        except Exception as e:
            print(f"[ULTRON] ❌ Play: {e}")
            raise
        finally:
            self._speaker_stream = None
            self.set_speaking(False)
            stream.stop()
            stream.close()

    # ── Morning briefing ────────────────────────────────────────────────────────

    async def _send_startup_briefing(self) -> None:
        """
        Two-phase briefing optimized for speed:
          Phase 1 — instant greeting (no tools) → speech starts in <1s
          Phase 2 — news pre-fetched in a background thread while Phase 1 plays,
                    delivered as ready text (no Gemini tool-call round-trip) and
                    shown on the UI content panel. Waits for turn_complete event
                    instead of a fixed sleep so there is no unnecessary gap.
        """
        while self._intro_playing():             # after the welcome, never over it
            await asyncio.sleep(0.2)
        if self.ui.muted or not self._awake or not get_brief_enabled():
            return
        memory   = load_memory()
        identity = memory.get("identity", {})

        def _val(k: str) -> str:
            e = identity.get(k, {})
            return (e.get("value", "") if isinstance(e, dict) else str(e)).strip()

        lang = _val("language")
        name = _val("name")
        time_str = datetime.now().strftime("%H:%M")

        # Start fetching news immediately — runs in parallel while phase 1 plays
        loop = asyncio.get_event_loop()
        news_future = loop.run_in_executor(None, _fetch_news_sync, "top world news today")

        await asyncio.sleep(0.3)
        if not self.session or self.ui.muted or not self._awake:
            return

        # ── Phase 1: instant greeting ─────────────────────────────────────────
        # The briefing fires before the user has said anything, so the
        # remembered language is the only signal there is. It is a starting
        # point, not a setting: the moment they reply, their language wins.
        lang_clause = (f" Speak this greeting in {lang}, then follow the "
                       f"user's own language from their first reply onward."
                       if lang else "")
        name_clause = f" Address the user as {name}." if name else ""

        # Inject last session context if available — pop removes it so it's never repeated
        last = await asyncio.to_thread(pop_last_session)
        session_clause = ""
        if last:
            try:
                _delta = (datetime.now() - datetime.strptime(last["date"], "%Y-%m-%d")).days
                _when  = "earlier today" if _delta == 0 else ("yesterday" if _delta == 1 else f"{_delta} days ago")
            except Exception:
                _when = "last time"
            session_clause = (
                f" Also briefly and naturally mention that {_when}: {last['summary']}"
            )

        p1 = (
            f"Greet the user warmly, mention it is {time_str}, and say you are fetching today's news now.{session_clause} "
            f"Keep it to 2 short sentences max. Do not call any tools.{lang_clause}{name_clause}"
        )

        # Clear the turn-done event so we can wait for Phase 1 to finish
        if self._turn_done_event:
            self._turn_done_event.clear()

        await self.session.send_client_content(
            turns={"role": "user", "parts": [{"text": p1}]},
            turn_complete=True,
        )
        print("[ULTRON] Briefing phase 1 (greeting) sent.")

        # ── Phase 2: fire as soon as Phase 1 audio is done ───────────────────
        async def _deliver_news():
            try:
                lang_str = (f" Speak in {lang} unless the user has since "
                            f"spoken another language, in which case use theirs."
                            if lang else "")

                # Wait for news fetch (already running) and Phase 1 turn-complete
                # in parallel — whichever takes longer determines the wait time
                news_done   = asyncio.wrap_future(news_future)
                turn_waited = False
                if self._turn_done_event:
                    try:
                        await asyncio.wait_for(self._turn_done_event.wait(), timeout=6.0)
                        turn_waited = True
                    except asyncio.TimeoutError:
                        pass

                # Extra buffer: turn_complete fires when Gemini finishes *generating*
                # Phase 1, but audio may still be playing.  Waiting a beat here
                # prevents Phase 2 audio from arriving while Phase 1 is mid-sentence
                # (which sounds like a "repeated first response" to the user).
                if turn_waited:
                    await asyncio.sleep(0.8)
                else:
                    await asyncio.sleep(1.0)

                try:
                    news_text = await asyncio.wait_for(news_done, timeout=8.0)
                except Exception as e:
                    self.ui.write_log(f"SYS: News fetch timed out/failed: {e!r}")
                    news_text = ""

                if not self.session:
                    return

                failed = (not news_text) or news_text.startswith(
                    ("No news found", "Search failed", "Please provide")
                )
                if not failed:
                    # Show on UI content panel immediately
                    self.ui.show_content("NEWS — top world news today", news_text)

                    p2 = (
                        f"[BRIEFING] Here are today's top news headlines:\n{news_text}\n\n"
                        "Pick ONE headline, summarise it in one sentence, then say the full list "
                        f"is displayed on screen. Do not call any tools.{lang_str}"
                    )
                else:
                    self.ui.write_log(
                        f"SYS: News unavailable — backend returned: {news_text[:120]!r}"
                    )
                    p2 = (
                        "News headlines could not be fetched right now. "
                        f"Let the user know briefly.{lang_str}"
                    )

                if self.ui.muted or not self._awake or not get_brief_enabled():
                    return
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": p2}]},
                    turn_complete=True,
                )
                print("[ULTRON] Briefing phase 2 (news) sent.")
            except Exception as e:
                print(f"[Briefing] Phase 2 error: {e}")
                print(f"[ULTRON] Briefing phase 2 failed: {e}")
                self.ui.write_log("SYS: Could not fetch the news for the briefing.")

        asyncio.create_task(_deliver_news())

    # ── Session memory ──────────────────────────────────────────────────────────

    async def _save_session_summary(self) -> None:
        """Summarise the current session in 1-2 sentences and save to long_term.json."""
        log = self._session_log
        if len(log) < 3:          # need at least one exchange to be worth saving
            return
        self._session_log = []    # reset immediately so the next session starts clean

        memory = load_memory()
        lang_entry = memory.get("identity", {}).get("language", {})
        lang = (lang_entry.get("value", "") if isinstance(lang_entry, dict) else str(lang_entry)).strip()
        lang = lang or "English"

        convo = "\n".join(log[-40:])   # cap at last 40 turns to stay within token budget
        prompt = (
            f"Summarize this conversation in 1-2 sentences in {lang}. "
            "Focus on what the user accomplished or discussed. "
            "Output ONLY the summary text, nothing else:\n\n" + convo
        )
        try:
            from core import gemini
            summary = await asyncio.to_thread(
                gemini.text, prompt, gemini.SMART, None, 30_000,
            )
            if summary:
                save_session_summary(summary, lang)
        except Exception as e:
            print(f"[Memory] ⚠️ Session summary failed: {e}")

    # ── System monitor ──────────────────────────────────────────────────────────

    async def _run_connection_watch(self) -> None:
        """Keep the connection panel honest without spending quota.

        Every 10 minutes: Claude sign-in (`claude auth status`, no request),
        the Gemini key (no request) and MCP server health (`claude mcp list`).
        Paid probes only happen on launch, on a button press, or as part of a
        real task. Failures back off so a dead network is not hammered."""
        delay = 600
        while not self._shutdown_requested:
            await asyncio.sleep(delay)
            try:
                await asyncio.to_thread(self._conn.check_claude, False)
                await asyncio.to_thread(self._conn.check_gemini, False)
                servers = await asyncio.to_thread(self._conn.check_mcp)
                ok = self._conn.get("claude").usable and all(
                    s.state == _connections.CONNECTED for s in servers)
                delay = 600 if ok else min(delay * 2, 3600)
            except Exception as e:
                print(f"[Connections] watch: {e}")
                delay = min(delay * 2, 3600)

    async def _run_system_monitor(self) -> None:
        """Background task: voice alerts when metrics exceed thresholds."""
        while True:
            await asyncio.sleep(10)
            from core.integrations.store import preferences
            if not preferences().get("system_alerts_enabled", False):
                continue
            alert = await asyncio.to_thread(self._sys_monitor.check)
            if not alert or not self.session or not self._awake or self.ui.muted:
                continue
            # Don't interrupt an active conversation
            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking or (time.monotonic() - self._last_user_speech) < 10:
                continue
            try:
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": alert}]},
                    turn_complete=True,
                )
            except Exception as e:
                print(f"[Monitor] ⚠️ Could not send alert: {e}")

    # ── Background monitor ──────────────────────────────────────────────────────

    async def _run_background_monitor(self) -> None:
        """Check user-configured topics once per day; speak alerts when new headlines appear."""
        await asyncio.sleep(300)          # wait 5 min after startup before first check
        while True:
            if self.session and self._awake and not self.ui.muted:
                # Don't interrupt if user spoke recently or ULTRON is mid-sentence
                with self._speaking_lock:
                    speaking = self._is_speaking
                recent_speech = (time.monotonic() - self._last_user_speech) < 30
                if not speaking and not recent_speech:
                    try:
                        alerts = await asyncio.to_thread(monitor_check_all)
                        memory = load_memory()
                        lang_e = memory.get("identity", {}).get("language", {})
                        lang   = (lang_e.get("value", "") if isinstance(lang_e, dict) else str(lang_e)).strip() or "English"
                        for alert in alerts:
                            if self.ui.muted or not self._awake:
                                break
                            msg = (
                                f"{alert}\n\n"
                                f"Inform the user about this development naturally in {lang}. "
                                "One brief sentence only."
                            )
                            await self.session.send_client_content(
                                turns={"role": "user", "parts": [{"text": msg}]},
                                turn_complete=True,
                            )
                            print("[ULTRON] Monitor alert sent.")
                            await asyncio.sleep(6)   # gap between consecutive alerts
                    except Exception as e:
                        print(f"[Monitor] ⚠️ Background check error: {e}")
            await asyncio.sleep(1800)     # check every 30 minutes

    # ── Phone audio relay ────────────────────────────────────────────────────────

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session."""
        q = self._dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            with self._speaking_lock:
                speaking = self._is_speaking
            if not speaking and not self.ui.muted:
                try:
                    self.out_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    # ── dashboard command relay ─────────────────────────────────────────────

    async def _process_dashboard_commands(self) -> None:
        while True:
            try:
                text = await asyncio.wait_for(
                    self._dashboard._command_queue.get(), timeout=0.5
                )
                if not text:
                    continue
                # Wait up to 8s for session to become ready after a wake
                for _ in range(80):
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                if self.session:
                    # A remote command is deliberate control and the phone user
                    # has no desktop WAKE button — so it wakes ULTRON if asleep.
                    if not self._awake:
                        self.wake(reason="remote command")
                    await self.session.send_client_content(
                        turns={"role": "user", "parts": [{"text": text}]},
                        turn_complete=True,
                    )
                    self.ui.write_log(f"[Web]: {text}")
                else:
                    print(f"[Dashboard] Dropped command (no session): {text}")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.5)

    # ── main loop ───────────────────────────────────────────────────────────

    async def run(self):
        self._loop = asyncio.get_event_loop()
        self._runner_task = asyncio.current_task()
        self._reconnect_event = asyncio.Event()

        # ── Wire the shared core services to the interface ───────────────────
        # The confirmation gate is useless without a way to ask, and a memory
        # trim is invisible without a way to say so. Both are bound once here
        # rather than passed down through every action signature.
        confirm_gate.bind(
            show = self.ui.show_confirm,
            hide = self.ui.hide_confirm,
            log  = self.ui.write_log,
            tokenized=True,
        )
        set_trim_notifier(self.ui.write_log)

        # Revalidate every connection on launch — nothing is assumed from the
        # last run. Claude's check is one Haiku request; Gemini's is a model
        # lookup; MCP is `claude mcp list`. All off-thread.
        self._conn.check_all(deep=True)
        asyncio.create_task(self._run_connection_watch())

        # Tell the device picker the exact rates the streams open at, from the
        # constants that actually open them — so it can never list a device that
        # cannot be opened at them.
        audio_devices.configure(SEND_SAMPLE_RATE, RECEIVE_SAMPLE_RATE)

        # Enumerate audio devices off-thread. The settings drawer must never pay
        # for host-API enumeration on the Qt thread.

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography)
        try:
            if os.getenv("ULTRON_DISABLE_REMOTE") == "1" or os.getenv("ULTRON_ENABLE_REMOTE") != "1":
                raise RuntimeError("optional phone dashboard disabled; explicit opt-in required")
            from dashboard.server import DashboardServer
            self._dashboard = DashboardServer()
            self._dashboard.set_connect_callback(self._on_phone_connected)
            asyncio.create_task(self._dashboard.serve())
            # Runs for the whole lifetime, not just inside an active session
            asyncio.create_task(self._process_dashboard_commands())
        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None

        try:
            await asyncio.to_thread(audio_devices.list_devices, "input")
            while not self._shutdown_requested:
                try:
                    print("[ULTRON] Connecting...")
                    self.ui.set_state("THINKING")
                    # Pick the rung to open the conversation on. A model resting
                    # off a quota limit is skipped; the name is published back to
                    # LIVE_MODEL so plugins follow whatever is actually in use.
                    global LIVE_MODEL
                    LIVE_MODEL = _gemini.live_model()
                    runtime_status.update(model=LIVE_MODEL.removeprefix("models/"), connected=False)
                    live_model = LIVE_MODEL
                    print(f"[ULTRON] Live model: {live_model}")

                    _resumed_with = self._resume_handle is not None
                    config = self._build_config()

                    # Fresh client on every reconnect — avoids stale HTTP session state
                    # v1alpha carries proactive audio; if it gets rejected we fall
                    # back to v1beta.
                    client = genai.Client(
                        api_key=_get_api_key(),
                        http_options={"api_version": "v1alpha" if self._enhanced_live else "v1beta"}
                    )

                    async with (
                        client.aio.live.connect(model=live_model, config=config) as session,
                        asyncio.TaskGroup() as tg,
                    ):
                        self.session          = session
                        self.audio_in_queue   = asyncio.Queue()
                        self.out_queue        = asyncio.Queue(maxsize=200)
                        self._turn_done_event = asyncio.Event()

                        # Reset transient state that must not carry over from a previous session
                        self._pending_vision       = None
                        self._vision_cam_active    = False
                        self._vision_close_pending = False
                        self._vision_busy          = False
                        self._vision_last_time     = 0.0
                        self._interrupted          = False

                        print("[ULTRON] Connected.")
                        runtime_status.update(connected=True)
                        self._conn.report_live(True, live_model)
                        if _resumed_with:
                            # Say it plainly: the difference between "it reconnected"
                            # and "it reconnected and still knows what we were doing"
                            # is the whole point, and it is invisible otherwise.
                            self.ui.write_log("SYS: Reconnected — conversation restored.")

                        # Wake word / Clap wake: if enabled, come up ASLEEP (mic gated, silent)
                        # until the user says "Hey Ultron", claps twice, or taps wake in the UI.
                        if self._wake_enabled or self._clap_enabled:
                            if self._wake_enabled:
                                self._ensure_wake_detector()
                            self._awake = False
                            self.ui.set_state("SLEEPING")
                            triggers = []
                            if self._clap_enabled:
                                triggers.append("Clap twice")
                            if self._wake_enabled:
                                triggers.append("say 'Hey Ultron'")
                            self.ui.write_log(f"SYS: ULTRON online — sleeping. {' or '.join(triggers)} to wake me.")
                        else:
                            self._awake = True
                            self.ui.set_state("LISTENING")
                            self.ui.write_log("SYS: ULTRON online.")

                        if self._dashboard:
                            await self._dashboard.broadcast({"type": "status", "state": "active"})

                        self._reconnect_event.clear()  # ignore requests from before this session
                        tg.create_task(self._watch_reconnect())
                        tg.create_task(self._send_realtime())
                        tg.create_task(self._listen_audio())
                        tg.create_task(self._receive_audio())
                        tg.create_task(self._play_audio())
                        tg.create_task(self._run_system_monitor())
                        tg.create_task(self._run_background_monitor())
                        tg.create_task(self._run_sleep_watch())
                        if self._dashboard:
                            tg.create_task(self._relay_phone_audio())

                        # Morning briefing — fires once per process launch (if enabled).
                        # Skipped in wake-word mode: it comes up asleep, and a briefing
                        # would mean talking while "asleep".
                        if not self._briefing_sent and get_brief_enabled() and self._awake and not self.ui.muted:
                            self._briefing_sent = True
                            tg.create_task(self._send_startup_briefing())

                except KeyboardInterrupt:
                    raise
                except SystemExit:
                    raise
                except BaseException as e:
                    if self._shutdown_requested:
                        break
                    # Catches both Exception and BaseExceptionGroup (Python 3.11+
                    # TaskGroup raises BaseExceptionGroup when tasks are cancelled
                    # externally, which `except Exception` would miss, letting the
                    # exception escape the while-loop and causing asyncio.run() to
                    # start shutdown — resulting in "executor after shutdown" errors).
                    # Voluntary reconnect (voice change) — not an error. Rebuild the
                    # session immediately with no backoff and no scary logs.
                    if _is_reconnect_signal(e):
                        print("[ULTRON] Voluntary reconnect requested.")
                        if not _keep_context_of(e):
                            # A deliberate clean slate (voice change) — drop the
                            # handle so the next connect really does start empty.
                            self._resume_handle = None
                        self._conn_backoff = 0
                        continue

                    # A resumption handle the server will not accept — expired, or
                    # belonging to a session it has since dropped. Without this, the
                    # same dead handle would be replayed on every retry and the
                    # assistant would never come back at all: the feature meant to
                    # survive a reconnect would be the thing preventing one. Drop it
                    # once and let the next attempt start clean.
                    if _resumed_with and (
                        "resum" in str(e).lower()
                        or "handle" in str(e).lower()
                        or "INVALID_ARGUMENT" in str(e)
                        or "NOT_FOUND" in str(e)
                    ):
                        print("[ULTRON] 🔗 Resumption handle rejected — starting a fresh session")
                        self.ui.write_log("SYS: Could not restore the conversation — starting fresh.")
                        self._resume_handle = None
                        self._conn_backoff = 0
                        continue

                    err_str = str(e)
                    print(f"[ULTRON] Error ({type(e).__name__}): {e}")
                    traceback.print_exc()
                    self._conn.report_live(False, error=f"{type(e).__name__}: {err_str}")

                    # Out of quota, or this model is not available to this key —
                    # step down the ladder and reconnect straight away. This is the
                    # difference between "ULTRON is quieter today" and "ULTRON does
                    # not start today": one model means one daily limit, and the
                    # limit always arrives mid-conversation.
                    if _gemini.note_live_failure(live_model, err_str):
                        nxt = _gemini.live_model()
                        self.ui.write_log(
                            f"SYS: Switching to {nxt.split('/')[-1]} — the previous "
                            f"model is out of quota."
                            if nxt != live_model else
                            "SYS: Every live model is rate-limited — retrying.")
                        self._conn_backoff = 0 if nxt != live_model else 15
                        if nxt == live_model:
                            await asyncio.sleep(self._conn_backoff)
                        continue

                    # Turn-taking / media / thinking knobs rejected by the server
                    # (preview API drift) — drop them first, because they are the
                    # newest fields and the cheapest to lose. Proactive audio is
                    # tried again on the next pass if the error persists.
                    if self._tuned_live and (
                        "INVALID_ARGUMENT" in err_str
                        or "Unknown name" in err_str
                        or "unexpected keyword" in err_str
                        or "realtime_input" in err_str.lower()
                        or "media_resolution" in err_str.lower()
                        or "thinking" in err_str.lower()
                    ):
                        self._tuned_live = False
                        print("[ULTRON] Live tuning rejected — reconnecting without it.")
                        continue

                    # Proactive audio rejected by the server (preview API drift) —
                    # drop it and reconnect with the plain config.
                    if self._enhanced_live and (
                        "INVALID_ARGUMENT" in err_str
                        or "proactiv" in err_str.lower()
                        or "Unknown name" in err_str
                        or "unexpected keyword" in err_str
                    ):
                        self._enhanced_live = False
                        self.ui.write_log(
                            "SYS: Proactive audio unavailable — reconnecting without it."
                        )
                        continue

                    # Invalid API key — stop hammering the API, prompt re-configuration
                    if "API key not valid" in err_str or "1007" in err_str:
                        self.ui.write_log("ERR: API key invalid — please re-enter your key.")
                        self.ui.set_state("SLEEPING")
                        self.ui.prompt_reconfig()
                        while not self.ui._win._ready:
                            await asyncio.sleep(1)
                        print("[ULTRON] New API key saved — reconnecting...")
                        self._conn_backoff = 3
                        continue

                    # Network / timeout errors — log clearly and back off
                    is_net_err = any(k in err_str for k in (
                        "TimeoutError", "timed out", "getaddrinfo", "CancelledError",
                        "ConnectionRefusedError", "OSError", "Cannot connect",
                    ))
                    if is_net_err:
                        _conn_backoff = min(getattr(self, "_conn_backoff", 3) * 2, 60)
                        self._conn_backoff = _conn_backoff
                        self.ui.write_log(
                            f"NET: Connection failed — retrying in {_conn_backoff}s. "
                            "(a VPN may be required)"
                        )
                    else:
                        self._conn_backoff = 3
                finally:
                    self.session = None
                    # Only save if there was a real conversation (≥3 turns)
                    if len(self._session_log) >= 3:
                        asyncio.create_task(self._save_session_summary())

                self.set_speaking(False)
                self.ui.set_state("SLEEPING")

                if self._dashboard:
                    await self._dashboard.broadcast({"type": "status", "state": "sleeping"})

                delay = getattr(self, "_conn_backoff", 3)
                runtime_status.update(connected=False)
                self._conn.report_live(False)
                print(f"[ULTRON] Reconnecting in {delay}s...")
                await asyncio.sleep(delay)

        except asyncio.CancelledError:
            if not self._shutdown_requested:
                raise
        finally:
            if self._wake_detector:
                self._wake_detector.stop()
            self._plugin_registry.shutdown()
            runtime_status.update(connected=False)
            self._conn.report_live(False)
            print("[ULTRON] Shutdown complete.")
            stopped = getattr(self, "_stopped", None)
            if stopped is not None:
                stopped.set()

def main():
    print("=" * 60)
    print("  ULTRON 2.0 — Personal AI Assistant")
    print("  Made by Pedja | GitHub: https://github.com/Medjed42/ultron")
    print("=" * 60)
    ui = CarUI("face.png")

    def runner():
        ui.wait_for_api_key()
        car = CarLive(ui)
        try:
            asyncio.run(car.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    worker = threading.Thread(target=runner, daemon=True)
    worker.start()
    ui.root.mainloop()
    worker.join(timeout=4)

    # Gasenje WhatsApp servera na izlasku
    try:
        import requests
        requests.post("http://localhost:31337/shutdown", timeout=2)
    except Exception:
        pass

if __name__ == "__main__":
    main()
