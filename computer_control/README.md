# Ultron Computer Control Subsystem

A powerful, modular subsystem for controlling the OS, managing windows, observing the screen, and controlling both existing and managed browsers.

## Architecture
The system is divided into clean domains separating Perception, Desktop interaction, Browser control, and Action Routing.
- `plugin.py`: Tool definition and LLM entry point.
- `action_router.py`: Interprets semantic actions (OBSERVE -> PLAN -> ACT -> VERIFY).
- `state_manager.py`: Short-term context memory (active app, last target).
- `desktop/`: Native OS control (processes, UIA, mouse, keyboard).
- `browser/`: Browser control split into `existing_browser` and `managed_browser`.
- `perception/`: Screen reading, visual fallback, OCR.
- `youtube/`: Specific optimizations for interacting with the YouTube player.

## Existing Chrome Detection
When asked to open or interact with Chrome, the system first consults `ProcessManager` (via `psutil`) to check if `chrome.exe` is already running. If it is, the system locates the window using OS APIs (Windows UI Automation via `pywinauto`) and focuses it instead of blindly launching a new instance. 

## Controlling Manually Opened Chrome
Mode 1 (Existing Browser): Controlled natively using OS accessibility APIs and keyboard shortcuts. For example, to pause YouTube, it focuses the existing Chrome window with "YouTube" in the title and sends the semantic `k` shortcut. It does NOT use a debug port or Selenium, preserving the user's normal profile security.

## Managed Playwright Mode
Mode 2 (Managed Browser): Only instantiated when deep DOM access or background scraping is explicitly required and context doesn't apply to the active desktop session. This uses `playwright` asynchronously to drive a separate browser instance.

## Setup & Dependencies
Dependencies:
- `pywinauto` (Windows UIA)
- `psutil` (Process inspection)
- `pyautogui` (Fallback coordinates/input)
- `playwright` (Managed browser mode)

Install:
`pip install -r requirements.txt`

## Permissions Required
On Windows, this requires standard user permissions. Interacting with elevated applications (like Task Manager) may require running Ultron as Administrator. No external network ports are opened.

## Limitations
- UI Automation relies heavily on app developers exposing accessibility trees.
- Hard fallback to `pyautogui` clicks can be brittle if screen resolution shifts.
- Playwright cannot magically attach to the manually opened Chrome without launching Chrome with remote debugging explicitly enabled (which is a security risk we avoid).

## Future Extensions (Camera/Gesture)
The architecture supports Future Providers:
- `PerceptionProvider`: You can build a `CameraPerceptionProvider` in `perception/camera_analyzer.py` that implements `observe()` to update the `ScreenState` without changing `action_router.py`.
- `InputProvider`: A `HandGestureInputProvider` can translate a swipe into `action_router.route("scroll_down")` seamlessly.
