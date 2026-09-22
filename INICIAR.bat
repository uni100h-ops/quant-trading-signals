@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run INSTALAR.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" agente.py
pause
