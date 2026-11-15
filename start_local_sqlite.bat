@echo off
setlocal
cd /d "%~dp0"
rem Force local SQLite even when SUPABASE_DB_URL was configured earlier.
set "SUPABASE_DB_URL="
set "DATABASE_URL="
set "VERCEL="
set "EMBERLENS_ENV=development"
set "EMBERLENS_DB=%CD%\data\emberlens.sqlite"
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 goto error
)
call ".venv\Scripts\activate.bat"
python -m pip install -r requirements.txt
if errorlevel 1 goto error
if not exist "data" mkdir data
echo.
echo EmberLens local SQLite: %EMBERLENS_DB%
echo Website: http://127.0.0.1:8000
echo Press Ctrl+C to stop.
start "" "http://127.0.0.1:8000"
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
pause
exit /b 0
:error
echo Failed to set up Python. Check that Python 3.10+ is installed.
pause
exit /b 1
