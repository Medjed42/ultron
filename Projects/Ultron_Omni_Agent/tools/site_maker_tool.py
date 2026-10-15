import os
import subprocess
from pathlib import Path

def create_site(project_name: str, prompt: str):
    """
    Uses the Antigravity CLI (agy) to generate a complete project (e.g. a website).
    Instead of just writing hardcoded strings, this triggers an autonomous AI agent
    to build the project iteratively in the target directory.
    """
    root = Path(__file__).resolve().parents[1] / "generated_sites"
    base = (root / project_name).resolve()
    if not base.is_relative_to(root.resolve()):
        return {"status":"error", "message":"Invalid project name"}
    base_dir = str(base)
    
    try:
        os.makedirs(base_dir, exist_ok=True)
        (base / "ULTRON_BRIEF.md").write_text(prompt, encoding="utf-8")
        
        # We use the Antigravity CLI to execute the prompt non-interactively
        # --dangerously-skip-permissions ensures it doesn't block waiting for user confirmation in the background
        command = [
            "agy", 
            "--dangerously-skip-permissions",
            "-p", prompt,
            "--add-dir", base_dir
        ]
        
        # Run the command and capture output
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=base_dir, timeout=600, creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        
        if result.returncode == 0 and ((base / "index.html").exists() or (base / "out/index.html").exists()):
            return {
                "status": "success", 
                "path": str(base / "out") if (base / "out/index.html").exists() else base_dir,
                "antigravity_output": result.stdout
            }
        else:
            return {
                "status": "error", 
                "message": f"Antigravity failed: {result.stderr}"
            }
            
    except Exception as e:
        return {"status": "error", "message": str(e)}
