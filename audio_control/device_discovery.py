from pycaw.pycaw import AudioUtilities, IMMDevice
from pycaw.constants import EDataFlow, DEVICE_STATE

class DeviceDiscovery:
    def list_active_outputs(self) -> list:
        # Filter for active playback endpoints (eRender)
        devices = AudioUtilities.GetAllDevices(data_flow=EDataFlow.eRender.value)
        return [d for d in devices if d.state.value == 1]

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