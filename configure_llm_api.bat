@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0configure_llm_api.ps1"

if errorlevel 1 (
    echo.
    echo [AI Assist] LLM API configuration failed.
    pause
    exit /b 1
)

echo.
echo [AI Assist] LLM API configuration finished.
pause
