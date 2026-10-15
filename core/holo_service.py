"""Holo Hands Service Manager for Ultron.

Manages the background server for Holo Gestures (zubair-trabzada/holo-gestures),
enabling touchless mid-air hand control, 3D hologram manipulation, floating cards,
and visual effects via camera and MediaPipe.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parents[1]
HOLO_DIR = BASE_DIR / "assets" / "holo"
DEFAULT_PORT = 4890

class HoloService:
    _instance: Optional[HoloService] = None

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self.process: Optional[subprocess.Popen] = None
        self.url = f"http://localhost:{self.port}"

    @classmethod
    def get_instance(cls) -> HoloService:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def is_running(self) -> bool:
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/props")
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    def start_server(self) -> bool:
        if self.is_running():
            return True

        server_script = HOLO_DIR / "server.py"
        if not server_script.exists():
            print(f"[HOLO] Server script not found at {server_script}")
            return False

        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = os.environ.copy()
        env["HOLO_PORT"] = str(self.port)

        try:
            self.process = subprocess.Popen(
                [sys.executable, str(server_script)],
                cwd=str(HOLO_DIR),
                env=env,
                creationflags=flags,
            )
            # Wait for server to respond (up to 3 seconds)
            for _ in range(15):
                time.sleep(0.2)
                if self.is_running():
                    print(f"[HOLO] Server online at {self.url}")
                    return True
            return self.is_running()
        except Exception as e:
            print(f"[HOLO] Failed to start server: {e}")
            return False

    def ensure_running(self) -> str:
        if not self.is_running():
            self.start_server()
        return self.url

    def open_browser(self) -> None:
        self.ensure_running()
        webbrowser.open(self.url)

    def stop(self) -> None:
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None

def get_holo_service() -> HoloService:
    return HoloService.get_instance()
