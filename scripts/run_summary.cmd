@echo off
rem Daily summary for Task Scheduler. Extra args are passed on, e.g. run_summary.cmd --dry-run
cd /d "%~dp0.."
if not exist data\logs mkdir data\logs
echo ===== %date% %time% ===== >> data\logs\summary.log
uv run python summary.py %* >> data\logs\summary.log 2>&1
exit /b %errorlevel%
