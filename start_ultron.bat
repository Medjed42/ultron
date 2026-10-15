@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
start "Ultron" pythonw.exe "%~dp0launch_ultron.py"
