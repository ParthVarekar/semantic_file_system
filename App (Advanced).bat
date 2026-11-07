@echo off
rem Opens the advanced developer UI (every detail and setting)
cd /d "%~dp0"
set "VENV=%~dp0.venv"
if exist "%~dp0.venv-gpu\Scripts\pythonw.exe" set "VENV=%~dp0.venv-gpu"
if not exist "%VENV%\Scripts\pythonw.exe" (
  echo The app is not installed yet. Run once: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
  pause
  exit /b 1
)
start "" "%VENV%\Scripts\pythonw.exe" -m lfi.ui.app --advanced
