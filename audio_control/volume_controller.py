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