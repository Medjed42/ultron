import comtypes
from comtypes import GUID, COMMETHOD, IUnknown, HRESULT
import ctypes

CLSIDs = [
    "{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}",
    "{294935CE-F637-4E7C-A41B-AB255460B862}",
    "{4300A8AE-511B-4DBF-81FA-69168C222467}",
    "{F8679F50-850A-41CE-981A-260F341BB648}"
]

IIDs = [
    "{F8679F50-850A-41CE-981A-260F341BB648}", # Win 10
    "{CA286FC3-91FD-42C3-8E9B-CAAFA66242E3}", # Win 11
    "{568B9108-44BF-40B4-9006-86AFE1B5A620}", # Win 7
]

def test():
    comtypes.CoInitialize()
    for clsid in CLSIDs:
        for iid in IIDs:
            class IPolicyConfig(IUnknown):
                _iid_ = GUID(iid)
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
            
            try:
                pc = comtypes.CoCreateInstance(
                    GUID(clsid),
                    interface=IPolicyConfig,
                    clsctx=comtypes.CLSCTX_INPROC_SERVER
                )
                print(f"SUCCESS! CLSID: {clsid}, IID: {iid}")
                return
            except Exception as e:
                pass
    print("Failed all")

if __name__ == '__main__':
    test()
