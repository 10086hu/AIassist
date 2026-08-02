@echo off
setlocal

cd /d %~dp0backend

set "AIASSIST_DATA_DIR=%TEMP%\AIassist"
if not exist "%AIASSIST_DATA_DIR%" mkdir "%AIASSIST_DATA_DIR%"
set "DATABASE_URL=sqlite:///%AIASSIST_DATA_DIR:\=/%/app.db"

if not exist ".venv\Scripts\python.exe" (
    echo [AI Assist] Creating Python virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [AI Assist] Failed to create virtual environment. Please check Python installation.
        pause
        exit /b 1
    )

    echo [AI Assist] Installing backend dependencies...
    call ".venv\Scripts\activate.bat"
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [AI Assist] Failed to install dependencies.
        pause
        exit /b 1
    )
) else (
    call ".venv\Scripts\activate.bat"
)

echo [AI Assist] Starting FastAPI backend at http://127.0.0.1:8000
echo [AI Assist] Database: %AIASSIST_DATA_DIR%\app.db
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

pause
