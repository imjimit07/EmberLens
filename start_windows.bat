@echo off
setlocal
cd /d "%~dp0"
echo.
echo ================ EMBERLENS ================
echo Starting the local development server.
echo.
if not exist ".venv\Scripts\python.exe" (
  echo Creating Python environment...
  py -3 -m venv .venv
  if errorlevel 1 (
    echo Python 3 not detected. Install Python 3.10+ and run this again.
    pause
    exit /b 1
  )
)
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Dependency installation failed.
  pause
  exit /b 1
)
if not exist "data\emberlens.sqlite" (
  echo No data loaded. Adding clearly labelled SIMULATED practice observations...
  python scripts\seed_demo.py
)
echo.
echo Open http://127.0.0.1:8000
python -m uvicorn backend.main:app --reload
pause
