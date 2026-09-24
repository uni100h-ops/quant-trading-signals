@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
set "qts_exit=%errorlevel%"
echo.
if not "%qts_exit%"=="0" echo Installation or setup incomplete. Read the reason above. Live mode has not been activated.
pause
exit /b %qts_exit%
