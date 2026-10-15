from computer_control.desktop.windows_controller import WindowsController

wc = WindowsController()
print("Windows:")
for w in wc.list_windows():
    print(f" - {w}")
