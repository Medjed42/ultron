from pycaw.pycaw import AudioUtilities

def list_devices():
    devices = AudioUtilities.GetAllDevices()
    for dev in devices:
        print(f"Device: {dev.FriendlyName}")

def test_set():
    devices = AudioUtilities.GetAllDevices()
    if len(devices) > 1:
        dev = devices[1]
        print(f"Setting default to: {dev.FriendlyName}")
        try:
            AudioUtilities.SetDefaultDevice(dev.id)
            print("Success!")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == '__main__':
    list_devices()
    test_set()
