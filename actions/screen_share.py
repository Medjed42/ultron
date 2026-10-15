"""
Action for Screen Sharing, Visual Guidance, and Desktop Interaction in Ultron.

Allows Ultron to:
- Share screen with the user (toggle on / off)
- Locate UI elements and point to them with a temporary animated bouncing arrow
  (e.g., when the user asks "gde je ovde settings", "pokaži mi gde je search")
- Control everything on the shared screen (click, double click, right click, type,
  shortcuts, drag, scroll, move mouse)
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional
from core.screen_share import screen_share_manager


def handler(parameters: dict, player: Any = None, speak: Any = None, **kwargs) -> str:
    action = str(parameters.get("action", "")).lower().strip()
    target = parameters.get("target")
    value = parameters.get("value")

    # If player (CarUI) is provided, connect UI methods to manager if not already done
    if player is not None:
        if hasattr(player, "set_screen_share_active"):
            screen_share_manager.register_ui_callback(player.set_screen_share_active)
        if hasattr(player, "show_screen_pointer"):
            screen_share_manager.register_pointer_callback(player.show_screen_pointer)

    # 1. Start / Share Screen
    if action in ("share", "start", "enable", "start_share", "ukljuci", "start_screen_share"):
        screen_share_manager.set_active(True)
        if player and hasattr(player, "set_screen_share_active"):
            player.set_screen_share_active(True)
        msg = "Deljenje ekrana je uključeno. Sada vidim tvoj ekran i spreman sam da ti pomognem i upravljam njime."
        if player and hasattr(player, "write_log"):
            player.write_log("SYS: Screen sharing activated.")
        return msg

    # 2. Stop / Unshare Screen
    elif action in ("stop", "unshare", "disable", "stop_share", "iskljuci", "stop_screen_share"):
        screen_share_manager.set_active(False)
        if player and hasattr(player, "set_screen_share_active"):
            player.set_screen_share_active(False)
        msg = "Deljenje ekrana je isključeno."
        if player and hasattr(player, "write_log"):
            player.write_log("SYS: Screen sharing stopped.")
        return msg

    # 3. Status
    elif action in ("status", "check"):
        active = screen_share_manager.is_active()
        return f"Deljenje ekrana je trenutno {'uključeno' if active else 'isključeno'}."

    # 4. Point to element / Visual Arrow ("gde je ovde settings", "pokazi mi X")
    elif action in ("point_to", "find_element", "locate", "show_where", "show_arrow", "pokazi", "nadji"):
        if not target:
            target = value or "settings"
        result = screen_share_manager.point_to(target, duration=5.0)
        if player and hasattr(player, "write_log"):
            player.write_log(f"POINTER: {target} -> {result.get('description', '')}")
        return result.get("message") or result.get("description") or f"Označio sam {target} na ekranu."

    # 5. Click
    elif action in ("click", "click_element"):
        coords = None
        if target and "," in str(target) and not any(c.isalpha() for c in str(target)):
            try:
                parts = [int(p.strip()) for p in str(target).split(",")]
                if len(parts) >= 2:
                    coords = (parts[0], parts[1])
                    target = None
            except Exception:
                pass
        
        x = coords[0] if coords else None
        y = coords[1] if coords else None
        res = screen_share_manager.click(target=target, x=x, y=y, button="left")
        return res.get("message") or ("Kliknuto." if res.get("success") else str(res.get("error", "Greška pri kliku.")))

    # 6. Double Click
    elif action in ("double_click", "dupli_klik"):
        res = screen_share_manager.double_click(target=target)
        return "Dupli klik izvršen." if res.get("success") else str(res.get("error", "Greška."))

    # 7. Right Click
    elif action in ("right_click", "desni_klik"):
        res = screen_share_manager.right_click(target=target)
        return "Desni klik izvršen." if res.get("success") else str(res.get("error", "Greška."))

    # 8. Type Text
    elif action in ("type_text", "type", "upisi", "kucaj"):
        text_to_type = value or target or ""
        target_field = target if (value and target) else None
        res = screen_share_manager.type_text(text=text_to_type, target=target_field)
        return f"Upisano: '{text_to_type}'" if res.get("success") else str(res.get("error", "Greška pri kucanju."))

    # 9. Press Key / Hotkey
    elif action in ("press_key", "hotkey", "pritisni", "taster"):
        key = value or target or "enter"
        res = screen_share_manager.press_key(key)
        return f"Pritisnut taster: {key}" if res.get("success") else str(res.get("error", "Greška pri pritisku tastera."))

    # 10. Scroll
    elif action in ("scroll", "skroluj", "scroll_down", "scroll_up"):
        direction = "down" if "down" in action or (value and "down" in str(value).lower()) else "up"
        amount = 5
        try:
            if value and str(value).isdigit():
                amount = int(value)
        except Exception:
            pass
        res = screen_share_manager.scroll(direction=direction, amount=amount)
        return f"Skrolovano {direction}." if res.get("success") else str(res.get("error", "Greška pri skrolovanju."))

    # 11. Move mouse
    elif action in ("move_mouse", "pomeri_mis"):
        if target and "," in str(target):
            try:
                parts = [int(p.strip()) for p in str(target).split(",")]
                res = screen_share_manager.move_mouse(parts[0], parts[1])
                return f"Miš pomeren na ({parts[0]}, {parts[1]})."
            except Exception:
                pass
        return "Niste naveli koordinate za miša."

    # 12. Drag & Drop
    elif action in ("drag", "prevuci"):
        # Expect target="x1,y1" value="x2,y2"
        try:
            p1 = [int(p.strip()) for p in str(target).split(",")]
            p2 = [int(p.strip()) for p in str(value).split(",")]
            res = screen_share_manager.drag(p1[0], p1[1], p2[0], p2[1])
            return f"Prevučeno sa ({p1[0]}, {p1[1]}) na ({p2[0]}, {p2[1]})."
        except Exception:
            return "Neispravne koordinate za prevlačenje (očekivano: target='x1,y1', value='x2,y2')."

    return f"Nepoznata akcija deljenja ekrana: '{action}'."


TOOL = {
    "name": "screen_share",
    "description": (
        "Controls screen sharing with Ultron, visual pointer guidance on screen, and screen automation. "
        "MUST be called when: "
        "1. The user asks to share screen or stop sharing screen ('podeli ekran', 'šeruj ekran', 'start screen share', 'stop screen share'). "
        "2. The user asks WHERE something is on screen (e.g. 'e Ultrone gde je ovde settings', 'gde je dugme X', 'pokaži mi gde je search', 'where is settings'). "
        "   Use action='point_to' with target='settings' or the requested element. This draws a temporary animated bouncing arrow pointing right at it and speaks where it is! "
        "3. The user asks Ultron to control or click something on the shared screen ('klikni na settings', 'upiši X', 'skroluj dole', 'pritisni enter', 'click this'). "
        "   Actions available: point_to, click, double_click, right_click, type_text, press_key, scroll, move_mouse, drag."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "The action to execute: 'share' (start screen sharing), 'stop' (stop sharing), "
                    "'status' (check if sharing is active), "
                    "'point_to' (locates element and shows animated pointing arrow on screen), "
                    "'click' (clicks element or coordinates), 'double_click', 'right_click', "
                    "'type_text' (types text into element/field), 'press_key' (presses key or hotkey e.g. enter, esc, ctrl+c), "
                    "'scroll' (scrolls up or down), 'move_mouse', 'drag'."
                )
            },
            "target": {
                "type": "STRING",
                "description": "The element name (e.g. 'settings', 'podešavanja', 'search bar', 'close button') or coordinates ('x,y')."
            },
            "value": {
                "type": "STRING",
                "description": "Additional value such as text to type, direction ('up'/'down'), or secondary coordinates."
            }
        },
        "required": ["action"]
    },
    "handler": handler
}
