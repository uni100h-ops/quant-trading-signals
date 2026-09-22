@echo off
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1" -SoloDependencias
if errorlevel 1 goto error
".venv\Scripts\python.exe" agente.py --paper
:error
pause
