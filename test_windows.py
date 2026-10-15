import pywinauto

def list_windows():
    app = pywinauto.Desktop(backend="win32")
    windows = app.windows()
    for w in windows:
        if w.window_text():
            print(f"Title: {w.window_text()}")

if __name__ == "__main__":
    list_windows()
