@echo off
setlocal
if not defined AIASSIST_BACKEND_URL set "AIASSIST_BACKEND_URL=http://10.60.150.179:8000/api"
call "%~dp0run_desktop.bat"
