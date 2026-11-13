@echo off
rem Read-only: download KIS per-stock investor flows for the KR flow event study (resumable).
rem Pauses automatically during the live US schedule. Output: data\kr_flow\kis\ , log: logs\collect_kis_flows.log
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
echo Collecting... this takes several hours. You can close this window to stop; run again to resume.
"C:\Users\Quiruri\Anaconda3\envs\qt\python.exe" -u scripts\kr_flow\collect_kis.py >> logs\collect_kis_flows.log 2>&1
echo Done. See logs\collect_kis_flows.log
pause
