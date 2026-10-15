from pycaw.pycaw import AudioUtilities

class DeviceSwitcher:
    def set_default(self, device_id: str) -> bool:
        try:
            AudioUtilities.SetDefaultDevice(device_id)
            return True
        except Exception:
            return False