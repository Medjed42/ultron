"""Launch Ultron and its existing paired Telegram bridge without console windows.
Ultron 2.0 — Made by Pedja: https://github.com/Medjed42/ultron
"""
import os
from pathlib import Path
import subprocess
import sys
import psutil

ROOT = Path(__file__).resolve().parent


def running(script):
    expected = (ROOT / script).resolve()
    for proc in psutil.process_iter(["cmdline", "cwd"]):
        try:
            args = proc.info["cmdline"] or []
            for arg in args[1:]:
                if not arg.lower().endswith(".py"):
                    continue
                candidate = Path(arg)
                if not candidate.is_absolute():
                    candidate = Path(proc.info["cwd"] or "") / candidate
                if candidate.resolve() == expected:
                    return True
        except (psutil.Error, OSError):
            continue
    return False


def launch(script):
    if running(script):
        return
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    python = Path(sys.executable).with_name("python.exe")
    with (logs / (Path(script).stem + ".log")).open("a", encoding="utf-8") as log:
        subprocess.Popen([str(python), "-u", "-X", "utf8", str(ROOT / script)], cwd=ROOT,
                         stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    launch("main.py")
    paired = os.getenv("TELEGRAM_ALLOWED_CHAT_ID") or (ROOT / "telegram_temp/chat_id.txt").exists()
    if os.getenv("ULTRON_START_TELEGRAM") == "1" and os.getenv("TELEGRAM_BOT_TOKEN") and paired:
        launch("telegram_bot.py")


if __name__ == "__main__":
    main()
