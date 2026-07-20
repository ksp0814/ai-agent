@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\agent-desktop.exe" (
    echo Personal Agent desktop launcher is not installed in .venv.
    echo Run:
    echo   python -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -e ".[desktop]"
    pause
    exit /b 1
)

".venv\Scripts\agent-desktop.exe" --workspace "%~dp0"
if errorlevel 1 pause
