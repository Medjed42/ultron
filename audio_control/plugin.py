import json
from .audio_manager import AudioManager

_manager = AudioManager()

def run(parameters: dict, player=None, session_memory=None) -> str:
    action = parameters.get("action", "").lower()
    device = parameters.get("device_name")
    value = parameters.get("value")
    
    import pythoncom
    pythoncom.CoInitialize()
    try:
        result = _manager.handle_action(action, device, value)
        if player and result.get("message"):
            player.write_log(f"ULTRON: {result['message']}")
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})
    finally:
        pythoncom.CoUninitialize()

TOOL = {
    "name": "audio_control",
    "description": (
        "Controls Windows audio output devices, volume, and routing. "
        "Use this to switch audio to a TV, headphones, speakers, or Bluetooth. "
        "Also used for muting, unmuting, getting volume, and setting aliases."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "One of: list_outputs, get_current, switch_output, switch_previous, set_volume, mute, unmute, set_alias"
            },
            "device_name": {
                "type": "STRING",
                "description": "Target device name or alias (e.g. 'televizor', 'slušalice', 'LG TV'). Optional."
            },
            "value": {
                "type": "STRING",
                "description": "Additional value, like volume percentage or alias name. Optional."
            }
        },
        "required": ["action"]
    },
    "handler": run
}