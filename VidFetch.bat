@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\.ready" goto run

echo Setting up VidFetch - first run only, this takes a minute...
where py >nul 2>nul
if %errorlevel%==0 (py -3 -m venv .venv) else (python -m venv .venv)
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Could not create a Python environment.
  echo Install Python 3.10 or newer from python.org, then run this again.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
if errorlevel 1 (
  echo.
  echo Installing yt-dlp failed. Check your internet connection and try again.
  pause
  exit /b 1
)
echo Adding the app window...
".venv\Scripts\python.exe" -m pip install pywebview -q >nul 2>nul || echo Native window not available - VidFetch will open in your browser instead.
type nul > ".venv\.ready"

:run
start "" ".venv\Scripts\pythonw.exe" app.py
