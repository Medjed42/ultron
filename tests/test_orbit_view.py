"""
tests/test_orbit_view.py
Comprehensive automated tests for Ultron Orbit View.
"""
import sys
import os
from pathlib import Path

# Ensure parent directory is in path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QTimer

if hasattr(Qt, "ApplicationAttribute") and hasattr(Qt.ApplicationAttribute, "AA_ShareOpenGLContexts"):
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

# Initialize QApplication once
app = QApplication.instance() or QApplication(sys.argv)

from ui import MainWindow, CarUI
from actions.orbit_view import orbit_view, TOOL


def test_orbit_tool_declaration():
    assert TOOL["name"] == "orbit_view"
    assert "prebaci se na orbitu" in TOOL["description"]
    assert "parameters" in TOOL
    assert callable(TOOL["handler"])


def test_orbit_widget_and_stack():
    win = MainWindow("")
    ui = CarUI(app)
    ui._win = win

    # Check stack has 4 widgets: 0=HUD, 1=cam, 2=video, 3=orbit
    assert win._hud_cam_stack.count() >= 4
    assert win._hud_cam_stack.widget(3) == win._orbit_cont

    # Test initial index is 0
    assert win._hud_cam_stack.currentIndex() == 0

    # Test show_orbit
    ui.show_orbit()
    app.processEvents()
    assert win._hud_cam_stack.currentIndex() == 3
    assert ui.is_orbit_active() is True

    # Test hide_orbit
    ui.hide_orbit()
    app.processEvents()
    assert win._hud_cam_stack.currentIndex() == 0
    assert ui.is_orbit_active() is False

    # Test action tool handler 'open'
    res_open = orbit_view("open", player=ui)
    app.processEvents()
    assert "activated" in res_open.lower()
    assert win._hud_cam_stack.currentIndex() == 3

    # Test action tool handler 'close'
    res_close = orbit_view("close", player=ui)
    app.processEvents()
    assert "closed" in res_close.lower() or "returned" in res_close.lower()
    assert win._hud_cam_stack.currentIndex() == 0

    # Test action tool handler 'toggle'
    orbit_view("toggle", player=ui)
    app.processEvents()
    assert win._hud_cam_stack.currentIndex() == 3

    orbit_view("toggle", player=ui)
    app.processEvents()
    assert win._hud_cam_stack.currentIndex() == 0

    # Test execution through ActionRegistry (exact way main.py calls it)
    from core.action_loader import discover_actions
    reg = discover_actions(BASE_DIR / "actions")
    assert reg.has("orbit_view")
    reg_res = reg.run("orbit_view", {"action": "open"}, {"player": ui})
    app.processEvents()
    assert win._hud_cam_stack.currentIndex() == 3
    assert "activated" in reg_res.lower()

    reg_close = reg.run("orbit_view", {"action": "close"}, {"player": ui})
    app.processEvents()
    assert win._hud_cam_stack.currentIndex() == 0
    assert "closed" in reg_close.lower() or "returned" in reg_close.lower()


def test_orbit_state_propagation():
    win = MainWindow("")
    ui = CarUI(app)
    ui._win = win

    # State update should not crash
    ui.set_state("THINKING")
    app.processEvents()
    assert win.hud.state == "THINKING"

    ui.set_state("SPEAKING")
    app.processEvents()
    assert win.hud.state == "SPEAKING"

    ui.set_state("LISTENING")
    app.processEvents()
    assert win.hud.state == "LISTENING"


if __name__ == "__main__":
    test_orbit_tool_declaration()
    test_orbit_widget_and_stack()
    test_orbit_state_propagation()
    print("All orbit tests passed successfully!")
