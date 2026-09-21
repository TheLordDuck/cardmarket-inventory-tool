@echo off
rem Double-click launcher for the Cardmarket Inventory Tool GUI (Windows).
rem Uses pythonw.exe (no console window) from the project's own .venv, and
rem `start` so this .bat's own window closes immediately instead of staying
rem open for the life of the GUI.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Virtual environment not found. Run setup first:
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m src.gui
