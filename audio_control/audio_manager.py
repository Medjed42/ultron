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
            observed = self.discovery.get_default_output() if success else None
            if success and observed and observed.id == target.id:
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
            target = self.discovery.get_default_output() if success else None
            if target and target.id == prev_id:
                if current: self.state.set_previous(current.id)
                name = target.FriendlyName if target else "previous device"
                return {"success": True, "verified": True, "current_device": name, "message": f"Zvuk je prebačen na {name}."}
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
