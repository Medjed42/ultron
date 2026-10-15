from pathlib import Path
import os
os.chdir(Path(__file__).resolve().parent)

import os
from pathlib import Path

BASE = Path(r".\audio_control")

def write(subpath: str, content: str):
    p = BASE / subpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content.strip(), encoding="utf-8")

write("__init__.py", """
from .plugin import TOOL, run
""")

write("plugin.py", """
import json
from .audio_manager import AudioManager

_manager = AudioManager()

TOOL = {
    "name": "audio_control",
    "description": (
        "Controls Windows audio output devices, volume, and routing. "
        "Use this to switch audio to a TV, headphones, speakers, or Bluetooth. "
        "Also used for muting, unmuting, getting volume, and setting aliases."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "One of: list_outputs, get_current, switch_output, switch_previous, set_volume, mute, unmute, set_alias"
            },
            "device_name": {
                "type": "STRING",
                "description": "Target device name or alias (e.g. 'televizor', 'slušalice', 'LG TV'). Optional."
            },
            "value": {
                "type": "STRING",
                "description": "Additional value, like volume percentage or alias name. Optional."
            }
        },
        "required": ["action"]
    }
}

def run(parameters: dict, player=None, session_memory=None) -> str:
    action = parameters.get("action", "").lower()
    device = parameters.get("device_name")
    value = parameters.get("value")
    
    try:
        result = _manager.handle_action(action, device, value)
        if player and result.get("message"):
            player.write_log(f"ULTRON: {result['message']}")
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})
""")

write("audio_manager.py", """
from .device_discovery import DeviceDiscovery
from .device_switcher import DeviceSwitcher
from .device_matcher import DeviceMatcher
from .device_aliases import DeviceAliases
from .volume_controller import VolumeController
from .audio_state import AudioState

class AudioManager:
    def __init__(self):
        self.discovery = DeviceDiscovery()
        self.switcher = DeviceSwitcher()
        self.aliases = DeviceAliases()
        self.matcher = DeviceMatcher(self.discovery, self.aliases)
        self.volume = VolumeController(self.matcher)
        self.state = AudioState()

    def handle_action(self, action: str, device: str = None, value: str = None) -> dict:
        if action == "list_outputs":
            devices = self.discovery.list_active_outputs()
            return {"success": True, "devices": [d.FriendlyName for d in devices]}
            
        elif action == "get_current":
            current = self.discovery.get_default_output()
            return {"success": True, "current_device": current.FriendlyName if current else None}
            
        elif action == "switch_output":
            if not device: return {"success": False, "error": "device_name required"}
            target = self.matcher.match_device(device)
            if not target: return {"success": False, "error": f"Device '{device}' not found or not active."}
            
            current = self.discovery.get_default_output()
            if current:
                self.state.set_previous(current.id)
                
            success = self.switcher.set_default(target.id)
            if success:
                return {
                    "success": True, 
                    "previous_device": current.FriendlyName if current else None,
                    "current_device": target.FriendlyName,
                    "verified": True,
                    "message": f"Zvuk je prebačen na {target.FriendlyName}."
                }
            return {"success": False, "error": "Switch failed"}
            
        elif action == "switch_previous":
            prev_id = self.state.get_previous()
            if not prev_id: return {"success": False, "error": "No previous device recorded."}
            
            current = self.discovery.get_default_output()
            success = self.switcher.set_default(prev_id)
            if success:
                target = self.discovery.get_device_by_id(prev_id)
                if current: self.state.set_previous(current.id)
                name = target.FriendlyName if target else "previous device"
                return {"success": True, "current_device": name, "message": f"Zvuk je prebačen na {name}."}
            return {"success": False, "error": "Switch failed"}
            
        elif action == "set_volume":
            try: vol = float(value)
            except: return {"success": False, "error": "value must be percentage"}
            return self.volume.set_volume(device, vol)
            
        elif action == "mute":
            return self.volume.set_mute(device, True)
            
        elif action == "unmute":
            return self.volume.set_mute(device, False)
            
        elif action == "set_alias":
            if not device or not value: return {"success": False, "error": "device_name and value required"}
            # device_name is the alias, value is the actual device name target
            target = self.matcher.match_device(value)
            if not target: return {"success": False, "error": f"Device '{value}' not found."}
            self.aliases.set_alias(device, target.FriendlyName)
            return {"success": True, "alias": device, "target": target.FriendlyName}
            
        return {"success": False, "error": f"Unknown action: {action}"}
""")

write("device_discovery.py", """
from pycaw.pycaw import AudioUtilities, IMMDevice
from pycaw.constants import EDataFlow, DEVICE_STATE

class DeviceDiscovery:
    def list_active_outputs(self) -> list:
        # returns all render devices
        devices = AudioUtilities.GetAllDevices()
        # Filter for active playback endpoints (eRender)
        # pycaw's GetAllDevices already lists endpoints. We can filter active ones.
        return [d for d in devices if d.state == DEVICE_STATE.Active.value]

    def get_default_output(self) -> IMMDevice:
        try:
            return AudioUtilities.GetSpeakers() # Gets default render device
        except:
            return None

    def get_device_by_id(self, device_id: str) -> IMMDevice:
        for d in self.list_active_outputs():
            if d.id == device_id:
                return d
        return None
""")

write("device_switcher.py", """
from pycaw.pycaw import AudioUtilities

class DeviceSwitcher:
    def set_default(self, device_id: str) -> bool:
        try:
            AudioUtilities.SetDefaultDevice(device_id)
            return True
        except Exception:
            return False
""")

write("device_aliases.py", """
import json
from pathlib import Path

class DeviceAliases:
    def __init__(self):
        self.config_file = Path(__file__).resolve().parents[1] / "audio_aliases.json"
        self.aliases = self._load()

    def _load(self):
        if self.config_file.exists():
            try: return json.loads(self.config_file.read_text("utf-8"))
            except: return {}
        return {}

    def _save(self):
        self.config_file.write_text(json.dumps(self.aliases, indent=2), "utf-8")

    def get_target(self, alias: str) -> str:
        return self.aliases.get(alias.lower().strip())

    def set_alias(self, alias: str, target: str):
        self.aliases[alias.lower().strip()] = target
        self._save()
""")

write("device_matcher.py", """
from .device_discovery import DeviceDiscovery
from .device_aliases import DeviceAliases

class DeviceMatcher:
    def __init__(self, discovery: DeviceDiscovery, aliases: DeviceAliases):
        self.discovery = discovery
        self.aliases = aliases

    def match_device(self, query: str):
        if not query: return None
        query = query.lower().strip()
        
        # 1. Alias check
        target_name = self.aliases.get_target(query)
        
        devices = self.discovery.list_active_outputs()
        
        # 2. Exact match on alias target or query
        for d in devices:
            fname = d.FriendlyName.lower()
            if target_name and target_name.lower() == fname:
                return d
            if query == fname:
                return d
                
        # 3. Partial match (e.g. "televizor" matching "LG TV" or "slušalice" matching "Headphones")
        # Handle some built-in serbian translations if aliases aren't set
        translations = {
            "slusalice": "headphone",
            "slušalice": "headphone",
            "zvucnici": "speaker",
            "zvučnici": "speaker",
            "televizor": "tv",
            "monitor": "nvidia"
        }
        
        search_terms = [query]
        if query in translations:
            search_terms.append(translations[query])
            
        for term in search_terms:
            for d in devices:
                if term in d.FriendlyName.lower() or term in d.id.lower():
                    return d
                    
        return None
""")

write("volume_controller.py", """
from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
import ctypes

class VolumeController:
    def __init__(self, matcher):
        self.matcher = matcher

    def _get_endpoint_volume(self, device):
        # We need IAudioEndpointVolume
        from pycaw.pycaw import IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        
        # pycaw provides Activate on device
        try:
            interface = device.Activate(
                IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            return interface.QueryInterface(IAudioEndpointVolume)
        except:
            return None

    def set_volume(self, device_query: str, percent: float) -> dict:
        dev = self.matcher.match_device(device_query) if device_query else self.matcher.discovery.get_default_output()
        if not dev: return {"success": False, "error": "Device not found"}
        
        vol_iface = self._get_endpoint_volume(dev)
        if vol_iface:
            level = max(0.0, min(1.0, percent / 100.0))
            vol_iface.SetMasterVolumeLevelScalar(level, None)
            return {"success": True, "device": dev.FriendlyName, "volume": percent}
        return {"success": False, "error": "Volume control not supported for this device"}

    def set_mute(self, device_query: str, mute: bool) -> dict:
        dev = self.matcher.match_device(device_query) if device_query else self.matcher.discovery.get_default_output()
        if not dev: return {"success": False, "error": "Device not found"}
        
        vol_iface = self._get_endpoint_volume(dev)
        if vol_iface:
            vol_iface.SetMute(1 if mute else 0, None)
            return {"success": True, "device": dev.FriendlyName, "muted": mute}
        return {"success": False, "error": "Mute control not supported"}
""")

write("audio_state.py", """
import json
from pathlib import Path

class AudioState:
    def __init__(self):
        self.state_file = Path(__file__).resolve().parents[1] / "audio_state.json"
        self.previous_id = self._load()

    def _load(self):
        if self.state_file.exists():
            try: return json.loads(self.state_file.read_text("utf-8")).get("previous")
            except: return None
        return None

    def get_previous(self):
        return self.previous_id

    def set_previous(self, dev_id: str):
        self.previous_id = dev_id
        self.state_file.write_text(json.dumps({"previous": dev_id}), "utf-8")
""")

print("Successfully created audio_control structure.")
