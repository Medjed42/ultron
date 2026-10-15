import json
import subprocess
import sys
import time
from pathlib import Path
import requests
from core.security import redact

PLUGIN = {"name":"omni_agent_acquire_clients", "description":"Research a restaurant using Gemini and source evidence; create a website brief/demo only after provenance checks. Unverified information goes to review. Does not send outreach emails.", "behavior":"NON_BLOCKING", "parameters":{"type":"OBJECT","properties":{"target_restaurant":{"type":"STRING"}},"required":[]}}

def run(parameters, player=None, session_memory=None):
    base = Path(__file__).resolve().parents[1] / "Projects/Ultron_Omni_Agent"
    url = "http://127.0.0.1:8766"
    try:
        try:
            ready = requests.get(url+"/health",timeout=2).json().get("service") == "ultron-research"
        except requests.RequestException:
            ready = False
        if not ready:
            subprocess.Popen([sys.executable,"-X","utf8",str(base/"main.py")],cwd=base,
                             creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            for _ in range(20):
                time.sleep(.5)
                try:
                    if requests.get(url+"/health",timeout=1).json().get("service") == "ultron-research":
                        ready=True; break
                except requests.RequestException: pass
        if not ready: return json.dumps({"status":"error","message":"Research service unavailable or port occupied"})
        # Submit once. Never replay a long-running POST after timeout.
        response=requests.post(url+"/workflows/acquire_clients",json={"target":parameters.get("target_restaurant", ""),"source":"ultron"},timeout=900)
        response.raise_for_status()
        return json.dumps(response.json(),ensure_ascii=False)
    except requests.Timeout:
        return json.dumps({"status":"unknown","message":"Research timed out; inspect saved lead state before retrying."})
    except Exception as exc:
        return json.dumps({"status":"error","message":redact(exc)})
