import comtypes
from comtypes import GUID, COMMETHOD, IUnknown, HRESULT
import ctypes
import pycaw

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

def list_and_set():
    print("Testing CLSID 294935CE-F637-4E7C-A41B-AB255460B862")
    try:
        policy_config = comtypes.CoCreateInstance(
            GUID("{294935CE-F637-4E7C-A41B-AB255460B862}"),
            interface=IPolicyConfig,
            clsctx=comtypes.CLSCTX_ALL
        )
        print("Successfully instantiated IPolicyConfig with 294935CE!")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    list_and_set()
