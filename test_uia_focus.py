import pywinauto

def test_focus():
    desktop = pywinauto.Desktop(backend="uia")
    windows = desktop.windows()
    for w in windows:
        if w.window_text() and "Chrome" in w.window_text():
            print(f"Found Chrome window: {w.window_text()}")
            try:
                w.set_focus()
                print("Focus successful!")
            except Exception as e:
                print(f"Failed to focus: {e}")

if __name__ == "__main__":
    test_focus()
