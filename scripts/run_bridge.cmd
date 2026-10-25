@echo off
rem WhatsApp bridge for Task Scheduler (starts at logon, keeps running). Output goes to data\logs\bridge.log
rem The bridge folder is found from WHATSAPP_SERVER_DIR in .env: it sits next to it, in ..\whatsapp-bridge
set "ROOT=%~dp0.."
for /f "usebackq tokens=1,* delims==" %%a in ("%ROOT%\.env") do if "%%a"=="WHATSAPP_SERVER_DIR" set "SERVER_DIR=%%b"
if not defined SERVER_DIR (
    echo WHATSAPP_SERVER_DIR is missing in .env
    exit /b 1
)
set "BRIDGE_DIR=%SERVER_DIR:/=\%\..\whatsapp-bridge"
set "LOG=%ROOT%\data\logs\bridge.log"
if not exist "%ROOT%\data\logs" mkdir "%ROOT%\data\logs"
rem the bridge keeps its session in store\ relative to its own folder
cd /d "%BRIDGE_DIR%" || exit /b 1
echo ===== %date% %time% ===== >> "%LOG%"
whatsapp-bridge.exe >> "%LOG%" 2>&1
