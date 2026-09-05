@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "YouTube Downloader" ".venv\Scripts\pythonw.exe" main.py
    exit /b
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 main.py
    if errorlevel 1 pause
    exit /b
)
where python >nul 2>nul
if not errorlevel 1 (
    python main.py
    if errorlevel 1 pause
    exit /b
)
echo Python was not found. Install Python 3.10 or newer from python.org.
echo Then run: python -m pip install -r requirements.txt
pause
