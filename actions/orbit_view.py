"""
actions/orbit_view.py

Controls the 3D Orbit Constellation visualization on Ultron's central display.
Auto-discovered by core.action_loader.
"""
from __future__ import annotations

def orbit_view(parameters=None, player=None, **kwargs) -> str:
    """Action handler called by core.action_loader and Gemini Live."""
    if isinstance(parameters, str):
        act = parameters
    elif isinstance(parameters, dict):
        act = parameters.get("action", kwargs.get("action", "open"))
    else:
        act = kwargs.get("action", "open")

    act = str(act or "open").strip().lower()
    if player is None:
        return "UI display is not available."

    if act in ("open", "show", "switch", "enable", "start", "on"):
        if hasattr(player, "show_orbit"):
            player.show_orbit()
        return "Orbit view activated on screen. Displaying 3D neural constellation with live animated particle flows."

    elif act in ("close", "hide", "disable", "stop", "off", "back"):
        if hasattr(player, "hide_orbit"):
            player.hide_orbit()
        return "Orbit view closed. Returned to standard HUD display."

    elif act in ("toggle",):
        if hasattr(player, "is_orbit_active") and player.is_orbit_active():
            if hasattr(player, "hide_orbit"):
                player.hide_orbit()
            return "Returned to standard HUD display."
        else:
            if hasattr(player, "show_orbit"):
                player.show_orbit()
            return "Orbit view activated on screen."

    else:
        if hasattr(player, "show_orbit"):
            player.show_orbit()
        return "Orbit view activated on screen."



TOOL = {
    "name": "orbit_view",
    "description": (
        "Controls the 3D Orbit visualization on Ultron's central display. "
        "Call this whenever the user asks to switch to, open, show, or close the orbit view, "
        "such as in Serbian or English: 'prebaci se na orbitu', 'prebaci na orbitu', "
        "'idi na orbitu', 'uključi orbitu', 'otvori orbitu', 'prikaži orbitu', 'aktiviraj orbitu', "
        "'switch to orbit', 'show orbit', 'open orbit', or to close/return: 'zatvori orbitu', "
        "'isključi orbitu', 'vrati se nazad', 'vrati na lice', 'prebaci na početni ekran', 'close orbit'. "
        "The orbit view displays an interactive, animated 3D subsystem network with real-time particle flows."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'open' to switch to orbit view, 'close' to return to normal HUD, 'toggle' to toggle.",
                "enum": ["open", "close", "toggle"]
            }
        },
        "required": ["action"]
    },
    "handler": orbit_view,
}
