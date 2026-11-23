@echo off
setlocal
if not defined AIASSIST_BACKEND_URL (
    echo Enter the server API address, for example http://server:8000/api
    set /p "AIASSIST_BACKEND_URL=Server API URL: "
)
if not defined AIASSIST_BACKEND_URL (
    echo [AI Assist] No server address supplied.
    exit /b 1
)
call "%~dp0run_desktop.bat"
