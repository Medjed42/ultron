import json
import math
import sys
import threading
import functools

_CONFIG_LOCK = threading.RLock()
from pathlib import Path

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR    = get_base_dir()
CONFIG_DIR  = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "api_keys.json"

# Load existing credentials by absolute path, independent of launcher directory.
#
# Only ULTRON's own .env. The Omni Agent's .env used to be loaded here too,
# which put its ANTHROPIC_API_KEY into ULTRON's whole process environment.
# Claude Code gives that variable precedence over the claude.ai login, so every
# Claude call ULTRON made authenticated with that key ("Invalid API key") and
# the claude.ai MCP connectors were disabled. The Omni Agent loads its own .env
# in Projects/Ultron_Omni_Agent/config.py when its service starts.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env", override=False)
except ImportError:
    pass

def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

def config_exists() -> bool:
    return CONFIG_FILE.exists()

def save_api_keys(gemini_api_key: str) -> None:
    ensure_config_dir()

    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    data["gemini_api_key"] = gemini_api_key.strip()

    _write_config(data)

def load_api_keys() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Failed to load api_keys.json: {e}")
        return {}

def get_gemini_key() -> str | None:
    import os
    return load_api_keys().get("gemini_api_key") or os.getenv("GEMINI_API_KEY")

def is_configured() -> bool:
    key = get_gemini_key()
    return bool(key and len(key) > 15)


def get_assistant_name() -> str:
    """Return the configured assistant name, or 'Ultron' if not set."""
    return load_api_keys().get("assistant_name", "Ultron") or "Ultron"


def get_user_name() -> str:
    """Return the configured user name for addressing."""
    return load_api_keys().get("user_name", "")


def save_assistant_config(assistant_name: str, user_name: str) -> None:
    """Persist assistant name and user name to config."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["assistant_name"] = assistant_name.strip() or "Ultron"
    data["user_name"] = user_name.strip()
    _write_config(data)


# ── Assistant voice ──────────────────────────────────────────────────────────
# Gemini Live prebuilt voices. Names are proper nouns — identical in every
# language, so this list is safe to show verbatim in any locale.
AVAILABLE_VOICES = ["Charon", "Puck", "Kore", "Fenrir", "Aoede"]
DEFAULT_VOICE    = "Charon"


def get_voice() -> str:
    """Return the configured Live voice, falling back to the default if unset
    or if the stored value is not a voice we recognise."""
    v = load_api_keys().get("voice_name", DEFAULT_VOICE) or DEFAULT_VOICE
    return v if v in AVAILABLE_VOICES else DEFAULT_VOICE


def save_voice(voice_name: str) -> None:
    """Persist the chosen Live voice. Unknown names collapse to the default so a
    bad value can never reach the API and break the session."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    v = (voice_name or "").strip()
    data["voice_name"] = v if v in AVAILABLE_VOICES else DEFAULT_VOICE
    _write_config(data)


def get_wake_word_enabled() -> bool:
    """Whether local wake-word gating is on (assistant sleeps until 'Hey Ultron')."""
    return load_api_keys().get("wake_word_enabled", True)


def save_wake_word_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["wake_word_enabled"] = bool(enabled)
    _write_config(data)


def get_push_to_talk_enabled() -> bool:
    """Hold-a-key-to-speak. When on, the mic is closed unless the chord is held."""
    return load_api_keys().get("push_to_talk_enabled", False)


def save_push_to_talk_enabled(enabled: bool) -> None:
    _save_flag("push_to_talk_enabled", enabled)


def get_wake_on_clap_enabled() -> bool:
    """Whether acoustic wake-on-clap is on (double clap wakes, single clap sleeps)."""
    return bool(load_api_keys().get("wake_on_clap_enabled", False))


def save_wake_on_clap_enabled(enabled: bool) -> None:
    _save_flag("wake_on_clap_enabled", bool(enabled))


def get_clap_threshold() -> int | None:
    """An explicit minimum clap peak (int16) saved by the user, else None.

    The default used to be a fixed 12000 (about -9 dBFS) — louder than most
    claps reach at a desk microphone, so wake on clap rarely fired. Without an
    explicit value the clap sensitivity preset decides (see core/clap_detector)."""
    try:
        value = load_api_keys().get("clap_threshold")
        return int(value) if value else None
    except (ValueError, TypeError):
        return None


CLAP_SENSITIVITIES = ("low", "medium", "high")


def get_clap_sensitivity() -> str:
    value = str(load_api_keys().get("clap_sensitivity", "medium")).lower()
    return value if value in CLAP_SENSITIVITIES else "medium"


def save_clap_sensitivity(value: str) -> None:
    value = str(value).lower()
    if value not in CLAP_SENSITIVITIES:
        raise ValueError(f"clap sensitivity must be one of {CLAP_SENSITIVITIES}")
    _patch_config(clap_sensitivity=value)


# ── Tray launcher activation (car_launcher.py) ────────────────────────────────
LAUNCHER_MODES = ("clap", "name")


def get_launcher_activation() -> str:
    """"clap" — a clap pattern launches ULTRON; "name" — saying its name does."""
    value = str(load_api_keys().get("launcher_activation", "clap")).lower()
    return value if value in LAUNCHER_MODES else "clap"


def save_launcher_activation(mode: str) -> None:
    mode = str(mode).lower()
    if mode not in LAUNCHER_MODES:
        raise ValueError(f"launcher activation must be one of {LAUNCHER_MODES}")
    _patch_config(launcher_activation=mode)


def get_launcher_clap_count() -> int:
    try:
        value = int(load_api_keys().get("launcher_clap_count", 2))
    except (ValueError, TypeError):
        value = 2
    return value if value in (2, 3) else 2


def save_launcher_clap_count(count: int) -> None:
    count = int(count)
    if count not in (2, 3):
        raise ValueError("launcher clap count must be 2 or 3")
    _patch_config(launcher_clap_count=count)


def save_clap_threshold(threshold: int) -> None:
    ensure_config_dir()
    data = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["clap_threshold"] = int(threshold)
    _write_config(data)


HUD_STYLES = ("orb", "face", "core")


def get_hud_style() -> str:
    """Which centrepiece the HUD draws: the animated head, or the reactor core.

    Taste, not capability — both render in the same software painter and cost
    about the same. Defaults to the head because that is what the previous release shipped
    with; anyone who preferred the older look can switch back in ⚙ and the
    choice survives a restart.
    """
    v = str(load_api_keys().get("hud_style", "orb")).strip().lower()
    return v if v in HUD_STYLES else "face"


def save_hud_style(style: str) -> None:
    s = str(style or "").strip().lower()
    _save_flag("hud_style", s if s in HUD_STYLES else "face")


# ── Live-session tuning ──────────────────────────────────────────────────────
# Everything here is optional and has a working default, so an untouched
# config behaves exactly like a configured one. Each value is also a way out:
# if a future model dislikes one of these, set it back and nothing else changes.

def get_thinking_enabled() -> bool:
    """Whether the Live model may spend tokens thinking before it answers.

    Off by default. A voice assistant is judged on how fast it starts talking,
    and the reasoning that actually needs deliberation in this app is delegated
    to the planning tools, which run on a separate non-Live model.
    """
    return bool(load_api_keys().get("thinking_enabled", False))


def save_thinking_enabled(enabled: bool) -> None:
    _save_flag("thinking_enabled", enabled)


def get_turn_tuning() -> dict:
    """How eagerly the server decides you have stopped speaking.

    OFF by default, and that default was earned. Cutting turns shorter looks
    like a free speed win and is not: proactive audio has to judge whether an
    utterance was even addressed to the assistant, and a turn clipped early
    gives it less to judge, so it stays quiet — and the reply to your first
    sentence only arrives once your second one has given it enough context.
    That reads as the assistant being a turn behind, which is far worse than
    the fraction of a second the tuning saves.

    Turn it on with "turn_tuning": {"enabled": true} if your own microphone and
    speaking pace suit it. `silence_ms` is the one that is felt: the pause the
    server sits through before accepting your turn is over.
    """
    cfg = load_api_keys().get("turn_tuning")
    cfg = cfg if isinstance(cfg, dict) else {}

    def _int(key, default, lo, hi):
        try:
            return max(lo, min(hi, int(cfg.get(key, default))))
        except (TypeError, ValueError):
            return default

    return {
        "enabled":    bool(cfg.get("enabled", False)),
        "silence_ms": _int("silence_ms", 550, 200, 3000),
        "prefix_ms":  _int("prefix_ms", 150, 0, 1000),
        # "high" = quicker to decide speech has ended.
        "end_sensitivity":   str(cfg.get("end_sensitivity", "high")).lower(),
        "start_sensitivity": str(cfg.get("start_sensitivity", "default")).lower(),
    }


def save_turn_tuning(values: dict) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    cur = data.get("turn_tuning")
    cur = dict(cur) if isinstance(cur, dict) else {}
    cur.update(values or {})
    data["turn_tuning"] = cur
    _write_config(data)


def get_proactive_audio_enabled() -> bool:
    """Whether the model gets to decide an utterance was not aimed at it and
    stay quiet.

    On by default — it is what stops the assistant answering the room. But it
    is also the first thing to switch off if replies ever seem to arrive a turn
    late: what looks like lag is usually the model having judged your previous
    sentence as not addressed to it, and only changing its mind once the next
    one arrives.
    """
    return bool(load_api_keys().get("proactive_audio", True))


def save_proactive_audio_enabled(enabled: bool) -> None:
    _save_flag("proactive_audio", enabled)


MEDIA_RESOLUTIONS = ("default", "low", "medium", "high")


def get_media_resolution() -> str:
    """How finely the model tokenises the screenshots and camera frames it is
    sent. 'medium' keeps on-screen text readable at a fraction of the tokens a
    full-resolution frame costs; 'low' is cheaper still but starts losing small
    text, which is most of what screen captures are for."""
    v = str(load_api_keys().get("media_resolution", "medium")).strip().lower()
    return v if v in MEDIA_RESOLUTIONS else "medium"


def save_media_resolution(value: str) -> None:
    v = str(value or "").strip().lower()
    _save_flag("media_resolution", v if v in MEDIA_RESOLUTIONS else "medium")


def _save_flag(key: str, value) -> None:
    """Read-modify-write one key without disturbing the rest of the config."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data[key] = bool(value) if isinstance(value, bool) else value
    _write_config(data)


def get_brief_enabled() -> bool:
    return load_api_keys().get("morning_brief_enabled", False)


def save_brief_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["morning_brief_enabled"] = enabled
    _write_config(data)


# ── Audio devices ────────────────────────────────────────────────────────────
# Stored as device NAMES, not sounddevice indices. Indices shift every time a
# USB device is plugged in or removed, so a saved index silently starts pointing
# at a different microphone. The empty string means "system default", which is
# both the factory setting and what an unresolvable saved device falls back to —
# so unplugging a headset degrades to the built-in speakers instead of crashing.

def _patch_config(**fields) -> None:
    """Read-modify-write one or more keys in api_keys.json.

    Every setter in this file open-coded this. Collapsing it here means a new
    setting is one line, and there is one place where a corrupt config file is
    handled instead of nine."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.update(fields)
    _write_config(data)


def get_input_device() -> str:
    """Microphone device name, or '' for the system default."""
    return (load_api_keys().get("input_device", "") or "").strip()


def save_input_device(name: str) -> None:
    _patch_config(input_device=(name or "").strip())


def get_output_device() -> str:
    """Speaker device name, or '' for the system default."""
    return (load_api_keys().get("output_device", "") or "").strip()


def save_output_device(name: str) -> None:
    _patch_config(output_device=(name or "").strip())


def get_plugin_enabled(plugin_name: str) -> bool:
    """Plugins are enabled by default the moment they're discovered (opt-out model)."""
    return load_api_keys().get("plugins_enabled", {}).get(plugin_name, True)


# ── Per-plugin settings ("tokens" / connection details) ───────────────────────
# Generic store so a plugin can declare its own config fields (PLUGIN_SETTINGS)
# and the settings UI renders + persists them WITHOUT any core edit — keeping the
# drop-in model intact. Values live under plugin_config[<namespace>][<key>].
# A namespace defaults to the plugin name, but a suite of plugins (e.g. the
# several printer plugins) can share ONE namespace.
def get_plugin_config(namespace: str) -> dict:
    """All stored values for a namespace (empty dict if none set yet)."""
    cfg = load_api_keys().get("plugin_config")
    val = cfg.get(namespace) if isinstance(cfg, dict) else None
    return dict(val) if isinstance(val, dict) else {}


def get_plugin_setting(namespace: str, key: str, default=None):
    """A single value from a namespace, or `default` if unset."""
    return get_plugin_config(namespace).get(key, default)


def save_plugin_config(namespace: str, values: dict) -> None:
    """Merge `values` into a namespace's stored config (read-modify-write, like
    every other helper here). Only the provided keys are touched."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    pc = data.get("plugin_config")
    if not isinstance(pc, dict):
        pc = {}
    cur = pc.get(namespace)
    if not isinstance(cur, dict):
        cur = {}
    cur.update(values)
    pc[namespace] = cur
    data["plugin_config"] = pc
    _write_config(data)


def save_plugin_enabled(plugin_name: str, enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    plugins_cfg = data.get("plugins_enabled")
    if not isinstance(plugins_cfg, dict):
        plugins_cfg = {}
    plugins_cfg[plugin_name] = enabled
    data["plugins_enabled"] = plugins_cfg
    _write_config(data)

def get_ai_provider() -> str:
    return load_api_keys().get('ai_provider', 'gemini')

def save_ai_provider(provider: str) -> None:
    d = load_api_keys()
    d['ai_provider'] = provider
    _patch_config(**d)

def get_openai_key() -> str:
    return load_api_keys().get('openai_api_key', '')

def save_openai_key(key: str) -> None:
    d = load_api_keys()
    d['openai_api_key'] = key
    _patch_config(**d)

def get_anthropic_key() -> str:
    return load_api_keys().get('anthropic_api_key', '')

def save_anthropic_key(key: str) -> None:
    d = load_api_keys()
    d['anthropic_api_key'] = key
    _patch_config(**d)


# ── Cinematic animations ─────────────────────────────────────────────────────
# One nested dict, so the boot / shutdown sequences can grow new options without
# a new top-level key each time. Every value is validated on the way in and on
# the way out: a hand-edited config can never hand the animation engine a speed
# of 0 or a quality it does not know.

ANIMATION_QUALITIES = ("low", "medium", "ultra")
ANIMATION_REDUCED_MOTION = ("auto", "on", "off")
ANIMATION_DEFAULTS: dict = {
    "startup":        True,     # cinematic boot sequence
    "shutdown":       True,     # electric screen-slash shutdown
    "quality":        "ultra",  # low / medium / ultra (auto-lowers if frames run slow)
    "lightning":      True,
    "glitch":         True,
    "sound":          True,
    "speed":          1.0,      # 0.5 = half speed, 2.0 = twice as fast
    "motion":         True,     # idle / interaction motion on the AI core
    "skip_intro":     False,    # launch straight into the dashboard
    "reduced_motion": "auto",   # auto follows Windows "Show animations"
    "welcome":        True,     # the narrated welcome recording at launch (else the classic boot)
    "voice_handoff":  True,     # after the welcome, ULTRON speaks once in its live voice
    "color_transform": True,    # crack / shatter / lightning when the colour changes
    "sfx_volume":     0.35,     # cinematic effects, 0…1 (hard-capped at 0.6 by the player)
    "narration_volume": 0.85,   # the welcome recording, 0…1
    "launch_fullscreen": True,  # open full screen every time ULTRON starts (F11 toggles)
}


def _clean_animation_settings(raw) -> dict:
    out = dict(ANIMATION_DEFAULTS)
    if not isinstance(raw, dict):
        return out
    for key in ("startup", "shutdown", "lightning", "glitch", "sound", "motion", "skip_intro",
                "welcome", "voice_handoff", "color_transform", "launch_fullscreen"):
        if key in raw:
            out[key] = bool(raw[key])
    q = str(raw.get("quality", out["quality"])).strip().lower()
    out["quality"] = q if q in ANIMATION_QUALITIES else ANIMATION_DEFAULTS["quality"]
    rm = str(raw.get("reduced_motion", out["reduced_motion"])).strip().lower()
    out["reduced_motion"] = rm if rm in ANIMATION_REDUCED_MOTION else "auto"
    try:
        sp = float(raw.get("speed", out["speed"]))
    except (TypeError, ValueError):
        sp = 1.0
    out["speed"] = round(min(2.0, max(0.5, sp)), 2) if math.isfinite(sp) else 1.0
    for key in ("sfx_volume", "narration_volume"):
        try:
            v = float(raw.get(key, out[key]))
        except (TypeError, ValueError):
            v = ANIMATION_DEFAULTS[key]
        out[key] = round(min(1.0, max(0.0, v)), 2) if math.isfinite(v) else ANIMATION_DEFAULTS[key]
    return out


def get_animation_settings() -> dict:
    return _clean_animation_settings(load_api_keys().get("animations"))


def save_animation_settings(values: dict) -> None:
    current = get_animation_settings()
    current.update(values or {})
    _patch_config(animations=_clean_animation_settings(current))


# ── UI colour theme ──────────────────────────────────────────────────────────
# "ui_color" stays the accent hex every older code path reads; "ui_theme" adds
# the theme's name, which is what makes RGB (no single colour) restorable.

def get_ui_theme() -> dict:
    d = load_api_keys()
    name = str(d.get("ui_theme") or "").strip().lower()
    hx = str(d.get("ui_color") or "").strip().lower()
    return {"name": name, "hex": hx}


def save_ui_theme(name: str, hex_color: str) -> None:
    _patch_config(ui_theme=str(name or "").strip().lower(), ui_color=str(hex_color or "").strip().lower())

def _write_config(data):
    ensure_config_dir()
    temporary = CONFIG_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=4), encoding="utf-8")
    temporary.replace(CONFIG_FILE)

def _serialized(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        with _CONFIG_LOCK:
            return function(*args, **kwargs)
    return wrapped

# Serialize all existing setting transactions, preserving the public API.
for _name, _function in list(globals().items()):
    if callable(_function) and (_name.startswith("save_") or _name in {"_patch_config", "_write_config", "load_api_keys"}):
        globals()[_name] = _serialized(_function)
