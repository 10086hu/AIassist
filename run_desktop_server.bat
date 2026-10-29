@echo off
setlocal
cd /d %~dp0
set "AIASSIST_BACKEND_URL=http://10.60.150.179:8000/api"
dotnet run --project desktop-wpf\AiReportDesktop.csproj -c Release
