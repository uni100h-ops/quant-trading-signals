@echo off
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -Command "Get-Content -LiteralPath 'log.txt' -Tail 30 -Wait"
pause
