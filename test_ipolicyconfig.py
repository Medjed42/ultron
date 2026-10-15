import comtypes
from comtypes import GUID, COMMETHOD, IUnknown, HRESULT
import ctypes

class IPolicyConfig(IUnknown):
    _iid_ = GUID('{F8679F50-850A-41CE-981A-260F341BB648}') # Win 10+
    _methods_ = [
        COMMETHOD([], HRESULT, 'GetMixFormat'),
        COMMETHOD([], HRESULT, 'GetDeviceFormat'),
        COMMETHOD([], HRESULT, 'ResetDeviceFormat'),
        COMMETHOD([], HRESULT, 'SetDeviceFormat'),
        COMMETHOD([], HRESULT, 'GetProcessingPeriod'),
        COMMETHOD([], HRESULT, 'SetProcessingPeriod'),
        COMMETHOD([], HRESULT, 'GetShareMode'),
        COMMETHOD([], HRESULT, 'SetShareMode'),
        COMMETHOD([], HRESULT, 'GetPropertyValue'),
        COMMETHOD([], HRESULT, 'SetPropertyValue'),
        COMMETHOD([], HRESULT, 'SetDefaultEndpoint',
                  (['in'], ctypes.c_wchar_p, 'wszDeviceId'),
                  (['in'], ctypes.c_int, 'eRole')),
        COMMETHOD([], HRESULT, 'SetEndpointVisibility')
    ]

from pycaw.pycaw import AudioUtilities
import pycaw

def list_and_set():
    devices = AudioUtilities.GetAllDevices()
    for dev in devices:
        print(f"Device: {dev.FriendlyName} - {dev.id}")
        
    try:
        policy_config = comtypes.CoCreateInstance(
            GUID("{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}"),
            interface=IPolicyConfig,
            clsctx=comtypes.CLSCTX_ALL
        )
        print("Successfully instantiated IPolicyConfig")
    except Exception as e:
        print(f"IPolicyConfig error: {e}")

if __name__ == '__main__':
    list_and_set()
