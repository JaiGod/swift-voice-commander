@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Setting up for the first time. This takes a minute...
    python -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)

".venv\Scripts\python.exe" voice_log.py || goto :error
exit /b 0

:error
echo.
echo Something went wrong. Make sure Python 3 is installed from python.org, then try again.
pause
