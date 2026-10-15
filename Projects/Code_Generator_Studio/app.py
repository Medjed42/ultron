"""
Ultron Code Generation Studio
Made by Pedja — https://github.com/Medjed42/ultron
"""
"""
Ultron Code Generation Studio - Web Server & Execution Engine
Powered by Antigravity Smart Engine for Ultron
"""

import os
import sys
import time
import socket
import tempfile
import subprocess
import webbrowser
import threading
from flask import Flask, render_template, request, jsonify

from generator_engine import get_all_templates, get_template, generate_custom_code
from code_analyzer import analyze_python_code

app = Flask(__name__)
DEFAULT_PORT = 5050


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_available_port(start_port: int = 5050) -> int:
    port = start_port
    while is_port_in_use(port) and port < 5100:
        port += 1
    return port


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/templates", methods=["GET"])
def list_templates():
    templates = get_all_templates()
    return jsonify({"templates": templates})


@app.route("/api/template/<template_id>", methods=["GET"])
def fetch_template(template_id):
    tpl = get_template(template_id)
    return jsonify({"template": tpl})


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.get_json() or {}
    task_type = data.get("task_type", "Data Processing Engine")
    language = data.get("language", "python")
    architecture = data.get("architecture", "Modular Controller")
    features = data.get("features", ["Logging", "Error Handling"])
    user_prompt = data.get("user_prompt", "")

    generated = generate_custom_code(
        task_type=task_type,
        language=language,
        architecture=architecture,
        features=features,
        user_prompt=user_prompt
    )
    return jsonify({"success": True, "result": generated})


@app.route("/api/analyze", methods=["POST"])
def analyze():
    data = request.get_json() or {}
    code = data.get("code", "")
    metrics = analyze_python_code(code)
    return jsonify({"metrics": metrics})


@app.route("/api/run", methods=["POST"])
def run_code():
    data = request.get_json() or {}
    code = data.get("code", "")
    timeout_sec = 10

    if not code.strip():
        return jsonify({
            "success": False,
            "stdout": "",
            "stderr": "Error: Empty code buffer provided.",
            "duration_ms": 0,
            "exit_code": 1
        })

    # Write code to a temporary file to execute via python
    temp_file = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            temp_file = f.name

        start_time = time.perf_counter()
        process = subprocess.run(
            [sys.executable, temp_file],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

        return jsonify({
            "success": process.returncode == 0,
            "stdout": process.stdout,
            "stderr": process.stderr,
            "duration_ms": duration_ms,
            "exit_code": process.returncode
        })

    except subprocess.TimeoutExpired:
        return jsonify({
            "success": False,
            "stdout": "",
            "stderr": f"Execution Timed Out (exceeded {timeout_sec}s safety limit).",
            "duration_ms": timeout_sec * 1000,
            "exit_code": -1
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "stdout": "",
            "stderr": f"Subprocess Error: {str(e)}",
            "duration_ms": 0,
            "exit_code": -1
        })
    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass


def open_browser_delayed(url: str):
    time.sleep(1.2)
    webbrowser.open(url)


if __name__ == "__main__":
    port = find_available_port(DEFAULT_PORT)
    url = f"http://127.0.0.1:{port}"
    print("=" * 65)
    print(f"[*] Ultron Code Generation Studio is online!")
    print(f"[*] Server Address: {url}")
    print(f"[*] Backed by Antigravity Smart Coding Engine")
    print("=" * 65)

    if "--no-browser" not in sys.argv:
        threading.Thread(target=open_browser_delayed, args=(url,), daemon=True).start()

    app.run(host="127.0.0.1", port=port, debug=False)
