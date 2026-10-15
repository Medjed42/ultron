"""
Samsung SmartThings & TV Integration Plugin for Ultron 2.0.

Enables voice control for Samsung SmartThings devices, smart TVs, and multi-monitor window management:
  • Turn lights/switches/plugs on or off
  • Adjust brightness / dim levels
  • Full Samsung TV Control:
      - Turn on/off TV ("upali TV", "ugasi TV")
      - Volume up/down/set level ("pojačaj TV", "smanji TV", "postavi jačinu tona na 15")
      - Mute/Unmute ("isključi ton na TV-u", "uključi ton")
      - Switch input sources ("prebaci TV na kablovsku/televiziju", "prebaci na HDMI 1", "prebaci na PC")
      - Turn on PC mode on TV ("upali PC opciju na televizoru", "prebaci TV na PC")
  • Move ULTRON between screens on voice request:
      - Move to main PC monitor ("prebaci se na moj glavni ekran", "move to main screen")
      - Move to TV screen ("prebaci se na TV", "move to TV")
  • Automatic boot option: Turns on TV and fullscreens ULTRON on the TV screen at launch
"""
from __future__ import annotations

import difflib
import os
import subprocess
import requests
from typing import Any, Optional

from memory.config_manager import get_plugin_setting, save_plugin_config

API_BASE = "https://api.smartthings.com/v1"

PLUGIN = {
    "name": "smartthings_control",
    "description": (
        "Controls Samsung SmartThings smart home devices (lights, switches, plugs) and smart TVs, "
        "AND moves the ULTRON window between screens (TV screen vs main PC monitor). "
        "CRITICAL: Use this tool whenever the user asks to:\n"
        "1. Control TV power: 'upali TV', 'ugasi TV', 'turn on TV', 'turn off TV'.\n"
        "2. Control TV volume/mute: 'pojačaj TV', 'smanji TV', 'mute TV', 'unmute TV', 'postavi ton na 20'.\n"
        "3. Switch TV input or PC mode: 'upali PC opciju na televizoru', 'prebaci TV na PC', 'prebaci TV na televiziju/kablovsku', 'switch TV to HDMI'.\n"
        "4. Move ULTRON window between screens: 'prebaci se na moj glavni ekran', 'move to main screen', 'prebaci se na TV', 'move to TV', 'fullscreen on TV'.\n"
        "5. Control smart lights/plugs or scenes: 'ugasi svetlo', 'upali klimu', 'pokreni scenu'.\n"
        "Do NOT use computer_settings for TV controls or smart devices."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "Action to perform: "
                    "'tv_pc_mode' (turn on TV, switch to PC input, extend display & fullscreen ULTRON on TV), "
                    "'move_window' (move ULTRON between main PC monitor and TV screen), "
                    "'tv_power' (turn TV on or off), "
                    "'tv_volume' (volume up, volume down, set volume level, mute, unmute), "
                    "'set_tv_input' (change TV input source, e.g. PC, TV, HDMI1), "
                    "'turn_on', 'turn_off', 'set_level', 'run_scene', 'list_devices', 'status'"
                ),
            },
            "target_screen": {
                "type": "STRING",
                "description": "Screen target for 'move_window' action: 'main' (primary PC monitor) or 'tv' (TV screen).",
            },
            "power": {
                "type": "STRING",
                "description": "Power state for 'tv_power': 'on' or 'off'.",
            },
            "volume_action": {
                "type": "STRING",
                "description": "Volume action for 'tv_volume': 'up', 'down', 'set', 'mute', 'unmute'.",
            },
            "volume_level": {
                "type": "INTEGER",
                "description": "Target volume percentage (0-100) if volume_action is 'set'.",
            },
            "device_name": {
                "type": "STRING",
                "description": "Name or room label of the target device or TV (e.g. 'TV', 'televizor', 'living room light').",
            },
            "input_source": {
                "type": "STRING",
                "description": "Target input source for TV (e.g. 'PC', 'TV', 'HDMI1', 'HDMI2', 'HDMI3', 'HDMI4').",
            },
            "level": {
                "type": "INTEGER",
                "description": "Brightness or dimmer percentage level (0 to 100) if action is 'set_level'.",
            },
            "scene_name": {
                "type": "STRING",
                "description": "Name of the SmartThings scene to execute if action is 'run_scene'.",
            },
            "fullscreen": {
                "type": "BOOLEAN",
                "description": "Whether window should be fullscreen when moved (defaults to true for TV, false for main screen).",
            },
        },
        "required": ["action"],
    },
}

PLUGIN_SETTINGS = {
    "namespace": "smartthings",
    "title": "SAMSUNG SMARTTHINGS & TV",
    "fields": [
        {
            "key": "token",
            "label": "Personal Access Token (PAT)",
            "type": "password",
            "placeholder": "Generate at account.smartthings.com/tokens",
        },
        {
            "key": "tv_auto_boot",
            "label": "Auto-turn on TV & fullscreen ULTRON on TV at launch",
            "type": "toggle",
            "default": True,
        },
        {
            "key": "tv_name",
            "label": "TV Device Name in SmartThings (optional)",
            "type": "text",
            "placeholder": "e.g. Televizor (leave blank for auto-detect)",
        },
        {
            "key": "pc_input",
            "label": "Default PC Input Source on TV",
            "type": "choice",
            "options": ["PC", "HDMI1", "HDMI2", "HDMI3", "HDMI4", "Workspace"],
            "default": "PC",
        },
        {
            "key": "auto_project_display",
            "label": "Auto-extend Windows display to TV",
            "type": "toggle",
            "default": True,
        },
        {
            "key": "tv_auto_shutdown",
            "label": "Auto-turn off TV & revert to PC screen only on exit",
            "type": "toggle",
            "default": True,
        },
    ],
    "action": {
        "label": "TEST CONNECTION",
        "function": "test_connection",
    },
}


def _get_token() -> str:
    """Retrieve the SmartThings PAT from config or environment."""
    stored = get_plugin_setting("smartthings", "token", "")
    if stored and str(stored).strip():
        return str(stored).strip()
    return os.environ.get("SMARTTHINGS_TOKEN", "").strip()


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.smartthings+json;v=1",
        "Content-Type": "application/json",
    }


def test_connection(values: dict) -> tuple[bool, str]:
    """Action callback for the UI 'TEST CONNECTION' button."""
    token = (values.get("token") or "").strip() or _get_token()
    if not token:
        return False, "No Personal Access Token provided."
    try:
        r = requests.get(f"{API_BASE}/devices", headers=_headers(token), timeout=5)
        if r.status_code == 200:
            items = r.json().get("items", [])
            tvs = [d.get("label") or d.get("name") for d in items if _is_tv_device(d)]
            tv_info = f" (Found TV: {', '.join(tvs)})" if tvs else ""
            return True, f"Connected successfully! Found {len(items)} device(s){tv_info}."
        elif r.status_code == 401:
            return False, "Authentication failed. Check your token."
        return False, f"API returned status {r.status_code}: {r.text[:80]}"
    except Exception as e:
        return False, f"Connection error: {e}"


def _fetch_devices(token: str) -> list[dict]:
    r = requests.get(f"{API_BASE}/devices", headers=_headers(token), timeout=8)
    if r.status_code == 200:
        return r.json().get("items", [])
    return []


def _fetch_scenes(token: str) -> list[dict]:
    r = requests.get(f"{API_BASE}/scenes", headers=_headers(token), timeout=8)
    if r.status_code == 200:
        return r.json().get("items", [])
    return []


def _is_tv_device(device: dict) -> bool:
    """Check if a SmartThings device is a television / display."""
    label = (device.get("label") or "").lower()
    name = (device.get("name") or "").lower()
    cats = []
    if device.get("components"):
        for c in device["components"][0].get("categories", []):
            if isinstance(c, dict):
                cats.append(str(c.get("name", "")).lower())
            elif isinstance(c, str):
                cats.append(c.lower())

    if "television" in cats or "tv" in cats:
        return True
    if any(k in label for k in ("tv", "televizor", "samsung 7", "samsung 8", "qled", "oled", "crystal", "the frame")):
        return True
    if any(k in name for k in ("tv", "television", "samsungtv")):
        return True
    return False


def _find_tv(devices: list[dict], hint: str = "") -> Optional[dict]:
    """Locate the TV device among devices."""
    if hint:
        target = _find_best_match(hint, devices)
        if target:
            return target
    configured_tv = get_plugin_setting("smartthings", "tv_name", "")
    if configured_tv:
        target = _find_best_match(configured_tv, devices)
        if target:
            return target
    # Fallback to auto-detecting any TV device
    for d in devices:
        if _is_tv_device(d):
            return d
    return None


def _find_best_match(query: str, candidates: list[dict], name_key: str = "label") -> Optional[dict]:
    if not candidates:
        return None
    query_lower = query.lower().strip()

    # 1. Exact or substring match on label or name
    for c in candidates:
        c_label = (c.get(name_key) or c.get("name") or "").lower()
        if query_lower == c_label or query_lower in c_label or c_label in query_lower:
            return c

    # 2. Fuzzy match
    names = [(c.get(name_key) or c.get("name") or "") for c in candidates]
    matches = difflib.get_close_matches(query, names, n=1, cutoff=0.35)
    if matches:
        target_name = matches[0]
        for c in candidates:
            if (c.get(name_key) or c.get("name") or "") == target_name:
                return c
    return None


def is_display_extended() -> bool:
    """Check if Windows projection topology (Win+P) is currently set to Extended."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        QDC_DATABASE_CURRENT = 0x00000004
        path_count = wintypes.UINT()
        mode_count = wintypes.UINT()
        r = user32.GetDisplayConfigBufferSizes(QDC_DATABASE_CURRENT, ctypes.byref(path_count), ctypes.byref(mode_count))
        if r == 0 and path_count.value > 0:
            class LUID(ctypes.Structure):
                _fields_ = [('LowPart', wintypes.DWORD), ('HighPart', wintypes.LONG)]
            class DISPLAYCONFIG_RATIONAL(ctypes.Structure):
                _fields_ = [('Numerator', wintypes.UINT), ('Denominator', wintypes.UINT)]
            class DISPLAYCONFIG_PATH_SOURCE_INFO(ctypes.Structure):
                _fields_ = [('id', LUID), ('idSource', wintypes.UINT), ('modeInfoIdx', wintypes.UINT), ('statusFlags', wintypes.UINT)]
            class DISPLAYCONFIG_PATH_TARGET_INFO(ctypes.Structure):
                _fields_ = [('id', LUID), ('idTarget', wintypes.UINT), ('modeInfoIdx', wintypes.UINT), ('outputTechnology', wintypes.UINT), ('rotation', wintypes.UINT), ('scaling', wintypes.UINT), ('refreshRate', DISPLAYCONFIG_RATIONAL), ('scanLineOrdering', wintypes.UINT), ('targetAvailable', wintypes.BOOL), ('statusFlags', wintypes.UINT)]
            class DISPLAYCONFIG_PATH_INFO(ctypes.Structure):
                _fields_ = [('sourceInfo', DISPLAYCONFIG_PATH_SOURCE_INFO), ('targetInfo', DISPLAYCONFIG_PATH_TARGET_INFO), ('flags', wintypes.UINT)]
            class DISPLAYCONFIG_MODE_INFO(ctypes.Structure):
                _fields_ = [('infoType', wintypes.UINT), ('id', wintypes.UINT), ('adapterId', LUID), ('modeInfo', ctypes.c_byte * 64)]

            paths = (DISPLAYCONFIG_PATH_INFO * path_count.value)()
            modes = (DISPLAYCONFIG_MODE_INFO * mode_count.value)()
            topology = wintypes.UINT()
            if user32.QueryDisplayConfig(QDC_DATABASE_CURRENT, ctypes.byref(path_count), paths, ctypes.byref(mode_count), modes, ctypes.byref(topology)) == 0:
                # 0x04 is SDC_TOPOLOGY_EXTEND
                return topology.value == 0x00000004
    except Exception:
        pass

    try:
        import ctypes
        return ctypes.windll.user32.GetSystemMetrics(80) > 1
    except Exception:
        return False


def ensure_display_extended() -> bool:
    """If PC display topology is not currently set to Extended, sets it to Extended via DisplaySwitch.exe."""
    if not is_display_extended():
        try:
            subprocess.Popen(["DisplaySwitch.exe", "/extend"], shell=True)
            return True
        except Exception:
            pass
    return False


def switch_display_to_internal() -> None:
    """Switches Win+P display projection back to PC screen only."""
    try:
        subprocess.Popen(["DisplaySwitch.exe", "/internal"], shell=True)
    except Exception:
        pass


def turn_off_tv(token: str = None) -> bool:
    """Sends switch: off to the TV."""
    token = token or _get_token()
    if not token:
        return False
    try:
        devices = _fetch_devices(token)
        tv = _find_tv(devices)
        if tv:
            ok, _ = _send_tv_command(token, tv["deviceId"], [
                {"component": "main", "capability": "switch", "command": "off", "arguments": []}
            ])
            return ok
    except Exception:
        pass
    return False


_EXIT_HANDLED = False


def on_app_exit() -> None:
    """Called on program exit: turns off TV and switches Win+P back to PC screen only."""
    global _EXIT_HANDLED
    if _EXIT_HANDLED:
        return
    _EXIT_HANDLED = True

    try:
        from memory.config_manager import get_plugin_setting
        if not get_plugin_setting("smartthings", "tv_auto_shutdown", True):
            return
    except Exception:
        pass

    # 1. Turn off TV
    turn_off_tv()
    # 2. Switch Windows display back to PC screen only
    switch_display_to_internal()


def _activate_windows_projection() -> None:
    """Ensure Windows extends display to external screen / TV."""
    ensure_display_extended()


def _send_tv_command(token: str, device_id: str, commands: list[dict]) -> tuple[bool, str]:
    """Execute a list of commands on a device."""
    url = f"{API_BASE}/devices/{device_id}/commands"
    r = requests.post(url, headers=_headers(token), json={"commands": commands}, timeout=8)
    if r.status_code in (200, 202):
        return True, "OK"
    return False, f"status {r.status_code}: {r.text[:80]}"


def _resolve_tv_input(token: str, device_id: str, requested_input: str = "PC") -> tuple[str, str, str]:
    """
    Resolves the exact capability, input ID, and human-friendly label.
    Example: returns ('samsungvd.mediaInputSource', 'HDMI3', 'PC')
    """
    req_lower = requested_input.lower().strip()
    try:
        url = f"{API_BASE}/devices/{device_id}/status"
        r = requests.get(url, headers=_headers(token), timeout=6)
        if r.status_code == 200:
            main_comp = r.json().get("components", {}).get("main", {})

            # 1. Samsung VD Media Input Source map (maps 'HDMI3' -> 'PC')
            vd_map = main_comp.get("samsungvd.mediaInputSource", {}).get("supportedInputSourcesMap", {}).get("value", [])
            if isinstance(vd_map, list) and vd_map:
                for item in vd_map:
                    i_name = str(item.get("name", "")).lower()
                    i_id = str(item.get("id", "")).lower()
                    if req_lower == i_name or req_lower == i_id:
                        return "samsungvd.mediaInputSource", item["id"], item.get("name", item["id"])

                if "pc" in req_lower:
                    for item in vd_map:
                        if "pc" in str(item.get("name", "")).lower():
                            return "samsungvd.mediaInputSource", item["id"], item.get("name", "PC")

                if any(k in req_lower for k in ("tv", "televizor", "dtv")):
                    for item in vd_map:
                        if "tv" in str(item.get("name", "")).lower() or "dtv" in str(item.get("id", "")).lower():
                            return "samsungvd.mediaInputSource", item["id"], item.get("name", "TV")

            # 2. Standard mediaInputSource list
            supported = main_comp.get("mediaInputSource", {}).get("supportedInputSources", {}).get("value", [])
            if isinstance(supported, list) and supported:
                for s in supported:
                    if str(s).lower() == req_lower:
                        return "mediaInputSource", str(s), str(s)
                if "pc" in req_lower:
                    for s in supported:
                        if "pc" in str(s).lower():
                            return "mediaInputSource", str(s), "PC"
                    for s in supported:
                        if "hdmi" in str(s).lower():
                            return "mediaInputSource", str(s), str(s)
    except Exception:
        pass

    fallback_id = "HDMI3" if "pc" in req_lower else (requested_input or "PC")
    return "samsungvd.mediaInputSource", fallback_id, requested_input or "PC"


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Entrypoint called by Gemini tool execution."""
    action = str(parameters.get("action", "")).lower().strip()
    device_name = str(parameters.get("device_name", "")).strip()
    scene_name = str(parameters.get("scene_name", "")).strip()
    input_source = str(parameters.get("input_source", "")).strip()
    target_screen = str(parameters.get("target_screen", "")).lower().strip()
    power = str(parameters.get("power", "")).lower().strip()
    volume_action = str(parameters.get("volume_action", "")).lower().strip()
    volume_level = parameters.get("volume_level")
    fullscreen_arg = parameters.get("fullscreen")
    level = parameters.get("level")

    token = _get_token()

    # ── MOVE WINDOW BETWEEN SCREENS ───────────────────────────────────────────
    if action == "move_window" or target_screen:
        is_tv_target = any(k in target_screen for k in ("tv", "televizor", "samsung", "secondary"))
        is_main_target = any(k in target_screen for k in ("main", "glavni", "pc", "monitor", "primary"))
        
        target = "tv" if is_tv_target else "main"
        fs = True if (target == "tv" if fullscreen_arg is None else bool(fullscreen_arg)) else False

        if player and hasattr(player, "move_to_screen"):
            player.move_to_screen(target, fullscreen=fs)
            if target == "tv":
                res = "Prebacio sam se na televizor u celom ekranu."
            else:
                res = "Prebacio sam se na tvoj glavni monitor."
            if hasattr(player, "write_log"):
                player.write_log(f"SYS: {res}")
            return res
        return f"Prebacio sam prozor na {target} ekran."

    # ── TV PC MODE & INPUT SWITCHING ──────────────────────────────────────────
    is_pc_action = (
        action in ("tv_pc_mode", "set_tv_input")
        or "pc" in action
        or ("tv" in device_name.lower() and "pc" in str(input_source).lower())
    )
    if is_pc_action:
        # Extend Windows display
        auto_extend = get_plugin_setting("smartthings", "auto_project_display", True)
        if auto_extend:
            _activate_windows_projection()

        if not token:
            msg = (
                "Sir, I have enabled PC display output on Windows. "
                "To also switch your Samsung TV to PC mode automatically, "
                "please enter your SmartThings token in Settings -> Plugin Settings."
            )
            if player and hasattr(player, "write_log"):
                player.write_log(f"SMARTTHINGS: {msg}")
            return msg

        devices = _fetch_devices(token)
        tv = _find_tv(devices, hint=device_name)
        if not tv:
            return "Windows display is active, but I could not find a TV registered in SmartThings."

        device_id = tv["deviceId"]
        display_name = tv.get("label") or tv.get("name") or "Televizor"

        # 1. Turn on TV
        _send_tv_command(token, device_id, [
            {"component": "main", "capability": "switch", "command": "on", "arguments": []}
        ])

        # 2. Determine target input source
        req_target = input_source or get_plugin_setting("smartthings", "pc_input", "PC") or "PC"
        capability_name, target_id, human_label = _resolve_tv_input(token, device_id, requested_input=req_target)

        # 3. Switch input source
        commands = [
            {
                "component": "main",
                "capability": capability_name,
                "command": "setInputSource",
                "arguments": [target_id],
            }
        ]
        ok, detail = _send_tv_command(token, device_id, commands)
        if not ok:
            _send_tv_command(token, device_id, [
                {
                    "component": "main",
                    "capability": "mediaInputSource",
                    "command": "setInputSource",
                    "arguments": [target_id],
                }
            ])

        # 4. If PC mode, also fullscreen ULTRON on the TV screen
        if "pc" in human_label.lower() or "pc" in target_id.lower() or action == "tv_pc_mode":
            if player and hasattr(player, "move_to_screen"):
                player.move_to_screen("tv", fullscreen=True)
            res = f"Upalio sam PC opciju na televizoru {display_name} (ulaz: {human_label} / {target_id}) i prebacio se u pun ekran."
        else:
            res = f"Prebacio sam televizor {display_name} na ulaz {human_label} ({target_id})."

        if player and hasattr(player, "write_log"):
            player.write_log(f"SMARTTHINGS: {res}")
        return res

    # ── TV POWER (ON / OFF) ────────────────────────────────────────────────────
    if action == "tv_power" or (any(k in action for k in ("tv", "televizor")) and power):
        devices = _fetch_devices(token)
        tv = _find_tv(devices, hint=device_name)
        if not tv:
            return "Nisam uspeo da pronađem televizor u SmartThings uređajima."
        device_id = tv["deviceId"]
        display_name = tv.get("label") or tv.get("name") or "Televizor"
        cmd = "off" if power in ("off", "ugasi", "iskljuci") else "on"
        _send_tv_command(token, device_id, [
            {"component": "main", "capability": "switch", "command": cmd, "arguments": []}
        ])
        msg = f"Ugasio sam {display_name}." if cmd == "off" else f"Upalio sam {display_name}."
        if player and hasattr(player, "write_log"):
            player.write_log(f"SMARTTHINGS: {msg}")
        return msg

    # ── TV VOLUME & MUTE ──────────────────────────────────────────────────────
    if action == "tv_volume" or (any(k in action for k in ("tv", "televizor")) and volume_action):
        devices = _fetch_devices(token)
        tv = _find_tv(devices, hint=device_name)
        if not tv:
            return "Nisam uspeo da pronađem televizor u SmartThings uređajima."
        device_id = tv["deviceId"]
        display_name = tv.get("label") or tv.get("name") or "Televizor"

        if volume_action in ("mute", "iskljuci_ton", "iskljuci"):
            _send_tv_command(token, device_id, [
                {"component": "main", "capability": "audioMute", "command": "mute", "arguments": []}
            ])
            res = f"Isključio sam ton na televizoru {display_name}."
        elif volume_action in ("unmute", "ukljuci_ton"):
            _send_tv_command(token, device_id, [
                {"component": "main", "capability": "audioMute", "command": "unmute", "arguments": []}
            ])
            res = f"Uključio sam ton na televizoru {display_name}."
        elif volume_action in ("set", "postavi") and volume_level is not None:
            target_v = max(0, min(100, int(volume_level)))
            _send_tv_command(token, device_id, [
                {"component": "main", "capability": "audioVolume", "command": "setVolume", "arguments": [target_v]}
            ])
            res = f"Postavio sam jačinu tona na televizoru {display_name} na {target_v}%."
        elif volume_action in ("down", "smanji"):
            _send_tv_command(token, device_id, [
                {"component": "main", "capability": "audioVolume", "command": "volumeDown", "arguments": []}
            ])
            res = f"Smanjio sam ton na televizoru {display_name}."
        else: # default: up / pojacaj
            _send_tv_command(token, device_id, [
                {"component": "main", "capability": "audioVolume", "command": "volumeUp", "arguments": []}
            ])
            res = f"Pojačao sam ton na televizoru {display_name}."

        if player and hasattr(player, "write_log"):
            player.write_log(f"SMARTTHINGS: {res}")
        return res

    # ── GENERAL SMARTTHINGS ACTIONS (lights, plugs, switches) ─────────────────
    if not token:
        msg = (
            "SmartThings token is not configured. Please paste your Personal Access Token "
            "in ULTRON Settings under Plugin Settings, or set the SMARTTHINGS_TOKEN environment variable."
        )
        if player and hasattr(player, "write_log"):
            player.write_log(f"SMARTTHINGS: {msg}")
        return msg

    try:
        if action == "list_devices":
            devices = _fetch_devices(token)
            if not devices:
                return "No SmartThings devices found on your account."
            names = [d.get("label") or d.get("name") for d in devices]
            res = f"Found {len(devices)} devices: {', '.join(names[:10])}."
            if player and hasattr(player, "write_log"):
                player.write_log(f"SMARTTHINGS: {res}")
            return res

        if action == "run_scene":
            scenes = _fetch_scenes(token)
            target = _find_best_match(scene_name or device_name, scenes, name_key="sceneName")
            if not target:
                return f"Could not find a SmartThings scene matching '{scene_name or device_name}'."
            scene_id = target["sceneId"]
            display_name = target.get("sceneName", "Scene")
            r = requests.post(f"{API_BASE}/scenes/{scene_id}/execute", headers=_headers(token), timeout=8)
            if r.status_code in (200, 204):
                res = f"Activated scene '{display_name}'."
                if player and hasattr(player, "write_log"):
                    player.write_log(f"SMARTTHINGS: {res}")
                return res
            return f"Failed to execute scene '{display_name}': status {r.status_code}."

        devices = _fetch_devices(token)
        target = _find_best_match(device_name, devices, name_key="label")
        if not target:
            return f"Could not find any SmartThings device matching '{device_name}'."

        device_id = target["deviceId"]
        display_name = target.get("label") or target.get("name") or "device"

        if action in ("turn_on", "turn_off"):
            cmd = "on" if action == "turn_on" else "off"
            body = {"commands": [{"component": "main", "capability": "switch", "command": cmd, "arguments": []}]}
            r = requests.post(f"{API_BASE}/devices/{device_id}/commands", headers=_headers(token), json=body, timeout=8)
            if r.status_code in (200, 202):
                res = f"Turned {cmd} {display_name}."
                if player and hasattr(player, "write_log"):
                    player.write_log(f"SMARTTHINGS: {res}")
                return res
            return f"Failed to turn {cmd} {display_name}: status {r.status_code}."

        elif action == "set_level":
            target_level = max(0, min(100, int(level if level is not None else 50)))
            body = {"commands": [{"component": "main", "capability": "switchLevel", "command": "setLevel", "arguments": [target_level]}]}
            r = requests.post(f"{API_BASE}/devices/{device_id}/commands", headers=_headers(token), json=body, timeout=8)
            if r.status_code in (200, 202):
                res = f"Set {display_name} brightness to {target_level}%."
                if player and hasattr(player, "write_log"):
                    player.write_log(f"SMARTTHINGS: {res}")
                return res
            return f"Failed to adjust {display_name} level: status {r.status_code}."

        elif action == "status":
            r = requests.get(f"{API_BASE}/devices/{device_id}/status", headers=_headers(token), timeout=8)
            if r.status_code == 200:
                components = r.json().get("components", {}).get("main", {})
                switch_state = components.get("switch", {}).get("switch", {}).get("value", "unknown")
                level_val = components.get("switchLevel", {}).get("level", {}).get("value")
                extra = f" at {level_val}% brightness" if level_val is not None else ""
                res = f"{display_name} is currently {switch_state}{extra}."
                if player and hasattr(player, "write_log"):
                    player.write_log(f"SMARTTHINGS: {res}")
                return res
            return f"Could not retrieve status for {display_name}."

        return f"Unknown action '{action}' for SmartThings."

    except Exception as e:
        err = f"SmartThings error: {e}"
        if player and hasattr(player, "write_log"):
            player.write_log(f"SMARTTHINGS: {err}")
        return err
