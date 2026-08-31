@echo off
setlocal

cd /d %~dp0backend

set "AIASSIST_DATA_DIR=%TEMP%\AIassist"
if not exist "%AIASSIST_DATA_DIR%" mkdir "%AIASSIST_DATA_DIR%"
set "DATABASE_URL=sqlite:///%AIASSIST_DATA_DIR:\=/%/app.db"

if "%DEEPSEEK_API_KEY%"=="" for /f "tokens=2,*" %%A in ('reg query HKCU\Environment /v DEEPSEEK_API_KEY 2^>nul') do set "DEEPSEEK_API_KEY=%%B"
if "%DEEPSEEK_API_URL%"=="" for /f "tokens=2,*" %%A in ('reg query HKCU\Environment /v DEEPSEEK_API_URL 2^>nul') do set "DEEPSEEK_API_URL=%%B"
if "%DEEPSEEK_API_BASE_URL%"=="" for /f "tokens=2,*" %%A in ('reg query HKCU\Environment /v DEEPSEEK_API_BASE_URL 2^>nul') do set "DEEPSEEK_API_BASE_URL=%%B"
if "%DEEPSEEK_MODEL%"=="" for /f "tokens=2,*" %%A in ('reg query HKCU\Environment /v DEEPSEEK_MODEL 2^>nul') do set "DEEPSEEK_MODEL=%%B"
if "%LLM_API_KEY%"=="" for /f "tokens=2,*" %%A in ('reg query HKCU\Environment /v LLM_API_KEY 2^>nul') do set "LLM_API_KEY=%%B"
if "%LLM_BASE_URL%"=="" for /f "tokens=2,*" %%A in ('reg query HKCU\Environment /v LLM_BASE_URL 2^>nul') do set "LLM_BASE_URL=%%B"
if "%LLM_MODEL%"=="" for /f "tokens=2,*" %%A in ('reg query HKCU\Environment /v LLM_MODEL 2^>nul') do set "LLM_MODEL=%%B"

if "%DEEPSEEK_API_URL%"=="" set "DEEPSEEK_API_URL=https://llmapi.tongji.edu.cn/v1"
if "%DEEPSEEK_API_BASE_URL%"=="" set "DEEPSEEK_API_BASE_URL=https://llmapi.tongji.edu.cn/v1"
if "%DEEPSEEK_MODEL%"=="" set "DEEPSEEK_MODEL=DeepSeek-R1"
if "%LLM_API_KEY%"=="" set "LLM_API_KEY=%DEEPSEEK_API_KEY%"
if "%LLM_BASE_URL%"=="" set "LLM_BASE_URL=%DEEPSEEK_API_BASE_URL%"
if "%LLM_MODEL%"=="" set "LLM_MODEL=%DEEPSEEK_MODEL%"
if "%DUPLICATE_LLM_TIMEOUT_SECONDS%"=="" set "DUPLICATE_LLM_TIMEOUT_SECONDS=180"
if "%DUPLICATE_LLM_MAX_TOKENS%"=="" set "DUPLICATE_LLM_MAX_TOKENS=6000"

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

python -c "import uvicorn" >nul 2>nul
if errorlevel 1 (
    echo [AI Assist] Installing missing backend dependencies...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [AI Assist] Failed to install dependencies.
        pause
        exit /b 1
    )
)

echo [AI Assist] Starting FastAPI backend at http://127.0.0.1:8000
echo [AI Assist] Database: %AIASSIST_DATA_DIR%\app.db
echo [AI Assist] LLM base URL: %DEEPSEEK_API_BASE_URL%
echo [AI Assist] LLM model: %DEEPSEEK_MODEL%
if "%DEEPSEEK_API_KEY%"=="" (
    echo [AI Assist] LLM API key is not configured. Run configure_llm_api.bat first if you need model features.
)
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

pause
