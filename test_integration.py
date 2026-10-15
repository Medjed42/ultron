from actions.computer_control import handler
import json

print("Testing computer_control handler...")
res = handler({"action": "observe"})
parsed = json.loads(res)
if parsed.get("success"):
    print("SUCCESS! Observation result:")
    print(json.dumps(parsed, indent=2))
else:
    print("FAILED:", res)
