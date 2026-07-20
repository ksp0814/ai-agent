@echo off
setlocal
cd /d "%~dp0"
set "WORKSPACE=%CD%"

if not exist ".venv\Scripts\pythonw.exe" (
    echo Personal Agent desktop launcher is not installed in .venv.
    echo Run:
    echo   python -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -e ".[desktop]"
    pause
    exit /b 1
)

wscript.exe //nologo "%~dp0run-agent-desktop.vbs"
