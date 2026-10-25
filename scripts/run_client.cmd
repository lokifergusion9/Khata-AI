@echo off
rem Ingestion run for Task Scheduler: WhatsApp -> Sheet. Output goes to data\logs\client.log
cd /d "%~dp0.."
if not exist data\logs mkdir data\logs
echo ===== %date% %time% ===== >> data\logs\client.log
uv run python client.py %* >> data\logs\client.log 2>&1
exit /b %errorlevel%
