from audio_control.plugin import run
import json

print("Testing audio_control plugin locally...")
res = run({"action": "list_outputs"})
parsed = json.loads(res)
if parsed.get("success"):
    print("SUCCESS! Devices found:")
    for d in parsed["devices"]:
        print(f" - {d}")
else:
    print("FAILED:", res)
