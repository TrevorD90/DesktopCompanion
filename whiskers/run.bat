@echo off
REM Quick launcher for the Whiskers tutor.
REM Double-click this file (or pin a shortcut to your taskbar) to start
REM the app. Close the window or press Ctrl+C to stop.

setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv not found at %~dp0venv
    echo Create it with:  python -m venv venv  ^&^&  venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo Starting Whiskers tutor...
echo Open http://127.0.0.1:5000 in your browser once you see "Speech recognition ready."
echo Press Ctrl+C in this window to stop.
echo.

"venv\Scripts\python.exe" app.py

REM Keep the window open if the server exits unexpectedly so you can read the error.
echo.
echo App exited.
pause
