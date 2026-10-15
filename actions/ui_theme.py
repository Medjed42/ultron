"""
actions/ui_theme.py
Voice-controlled interface colour. Auto-discovered by core.action_loader.

The Live voice model calls this for any colour request in any language; the
interface then cracks, shatters and is rebuilt in the new colour by lightning
(core/cinematic/transform.py). The tool answers immediately — the transition
takes about seven seconds — and the application tells the model when it has
finished, so the closing line ("Blue protocol initialized…") is said at the
right moment, not before.
"""
from __future__ import annotations

from core.theme import CUSTOM, THEMES, resolve, spoken_name


def change_ui_color(parameters=None, player=None, **kwargs) -> str:
    """Action handler called by core.action_loader and Gemini Live."""
    if isinstance(parameters, dict):
        color = parameters.get("color", kwargs.get("color", ""))
    else:
        color = parameters or kwargs.get("color", "")
    color = str(color or "").strip()
    if player is None or not hasattr(player, "change_theme"):
        return "The interface is not available right now."
    spec = resolve(color)
    if spec is None:
        names = ", ".join(THEMES)
        return (f"'{color}' is not a colour I can apply. Supported: {names}, RGB (colour cycling), "
                "original (ULTRON red), or a hex code such as #00FFFF. Ask the user which one.")
    name = spoken_name(spec)
    if spec.name == CUSTOM:
        name = f"custom {spec.hex.upper()}"
    result = player.change_theme(spec.name if spec.is_rgb else spec.hex, source="voice")
    if result == "started":
        return (f"The {spec.label} colour transformation is now playing on screen (about seven seconds). "
                f"Say exactly this, nothing else: \"Understood. Initializing {name} energy system.\" "
                "Do not say it is finished — you will be told when it is.")
    if result == "queued":
        return (f"Another transition is still on screen; {spec.label} follows it automatically. "
                f"Tell the user in one short sentence that {name} comes right after.")
    if result == "same":
        return f"The interface is already {name}. Say so in one short sentence."
    if result == "unavailable":
        return "ULTRON is shutting down, so the colour was not changed."
    return "The colour could not be changed."


TOOL = {
    "name": "change_ui_color",
    "description": (
        "Changes the colour theme of Ultron's whole interface, with a cinematic crack-and-rebuild "
        "transition. Call it whenever the user asks to change, switch, activate or set Ultron's "
        "colour, theme or interface colour, in any language — e.g. 'change your color to blue', "
        "'activate red', 'switch to purple', 'turn everything green', 'change your interface to "
        "cyan', 'activate RGB mode', 'return to your original color', 'set your theme to #00FFFF', "
        "or in Serbian 'promeni boju u crvenu', 'prebaci se na plavu', 'aktiviraj ljubičastu', "
        "'vrati originalnu boju'. Not for smart-home lights."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "color": {
                "type": "STRING",
                "description": (
                    "The colour: red, blue, purple, green, cyan, gold, pink, orange, white, "
                    "'rgb' (animated colour cycling), 'original' (Ultron's own red), or a hex code "
                    "like #00FFFF. Translate other languages to these English names."
                ),
            }
        },
        "required": ["color"],
    },
    "handler": change_ui_color,
}
