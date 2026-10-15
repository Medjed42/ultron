import pywinauto
import time

def test_chrome():
    try:
        app = pywinauto.Application(backend="uia").connect(title_re=".*Google Chrome$", timeout=2)
        window = app.top_window()
        print(f"Connected to Chrome: {window.window_text()}")
    except Exception as e:
        print(f"Error connecting to Chrome: {e}")

if __name__ == "__main__":
    test_chrome()
