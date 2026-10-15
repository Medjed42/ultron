import ctypes
import ctypes.wintypes

EnumWindows = ctypes.windll.user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
GetWindowText = ctypes.windll.user32.GetWindowTextW
GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW

def list_all_windows():
    titles = []
    def foreach_window(hwnd, lParam):
        length = GetWindowTextLength(hwnd)
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            GetWindowText(hwnd, buff, length + 1)
            titles.append((hwnd, buff.value))
        return True
    
    EnumWindows(EnumWindowsProc(foreach_window), 0)
    return titles

if __name__ == '__main__':
    wins = list_all_windows()
    for hwnd, title in wins:
        print(f"{hwnd}: {title}")
    if not wins:
        print("No titled windows found at all.")
