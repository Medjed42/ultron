"""Use Ultron's existing Gemini SDK, key lookup and fallback ladder."""
import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path: sys.path.append(str(ROOT))
from core import gemini
from core.security import redact

class GeminiAgent:
    def execute_task(self, prompt, context, require_json=False, max_retries=2):
        try:
            from google.genai import types
            config = types.GenerateContentConfig(response_mime_type="application/json") if require_json else None
            result = gemini.text("\n".join(context or []) + "\n\n" + prompt, tier=gemini.SMART, config=config)
            if not result: return {"status":"error","message":"Gemini did not return a result"}
            if require_json: json.loads(result)
            return {"status":"success","result":result,"provider":"gemini"}
        except Exception as exc:
            return {"status":"error","message":redact(exc)}
