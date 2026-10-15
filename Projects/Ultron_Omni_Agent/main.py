"""
Ultron Omni Agent API
Made by Pedja — https://github.com/Medjed42/ultron
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from agents.omni_intelligence import OmniIntelligenceAgent
import uvicorn
import threading
_workflow_lock = threading.Lock()
from workflows.client_acquisition import run_restaurant_acquisition_workflow

app = FastAPI(title="Ultron Research API", description="Made by Pedja (https://github.com/Medjed42/ultron)", version="2.0.0")
orchestrator = OmniIntelligenceAgent()

class TaskRequest(BaseModel):
    task_description: str
    preferred_agent: str = None  

class WorkflowRequest(BaseModel):
    target: str = ""
    source: str = "ultron"

@app.post("/execute")
def execute_task(request: TaskRequest):
    try:
        result = orchestrator.execute_task(request.task_description, [])
        if result["status"] == "error":
            raise HTTPException(status_code=500, detail=result["message"])
        return {"status": "success", "result": result["result"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/workflows/acquire_clients")
def acquire_clients(request: WorkflowRequest):
    if not _workflow_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A research workflow is already running; no duplicate started")
    try:
        return run_restaurant_acquisition_workflow(target=request.target, source=request.source)
    except Exception as e:
        from core.security import redact
        raise HTTPException(status_code=500, detail=redact(e))
    finally:
        _workflow_lock.release()

@app.get("/health")
async def health_check():
    return {"status": "ok", "service":"ultron-research"}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8766, reload=False)
