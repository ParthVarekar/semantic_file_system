@echo off
rem Local File Intelligence - double-click menu (or: startup.bat app|quick|advanced|test|alltests|demo|shortcuts|status)
setlocal
cd /d "%~dp0"
title Local File Intelligence
rem Prefer the GPU (DirectML) environment when it exists; it falls back to the CPU by itself.
set "VENV=%~dp0.venv"
if exist "%~dp0.venv-gpu\Scripts\python.exe" set "VENV=%~dp0.venv-gpu"
set "PY=%VENV%\Scripts\python.exe"
set "PYW=%VENV%\Scripts\pythonw.exe"
if not exist "%PY%" goto nosetup
if not "%~1"=="" goto %~1

:menu
cls
echo.
echo   Local File Intelligence
echo   -----------------------
echo   1  Open the app
echo   2  Start quick search (Alt+Shift+Space, lives in the tray)
echo   3  Open the advanced tools (every detail and setting)
echo   4  Run the fast tests (about 3 minutes)
echo   5  Run all tests incl. real models and 20,000 files (about 5 minutes)
echo   6  Make a demo folder to try things safely (Documents\lfi-demo)
echo   7  Create Desktop shortcuts (app, advanced, quick search)
echo   8  Show index status
echo   Q  Quit
echo.
choice /c 12345678Q /n /m "  Choose: "
if errorlevel 9 goto :eof
if errorlevel 8 goto status
if errorlevel 7 goto shortcuts
if errorlevel 6 goto demo
if errorlevel 5 goto alltests
if errorlevel 4 goto test
if errorlevel 3 goto advanced
if errorlevel 2 goto quick
goto app

:app
start "" "%PYW%" -m lfi.ui.app
goto :eof

:quick
start "" "%PYW%" -m lfi.ui.quick
goto :eof

:advanced
start "" "%PYW%" -m lfi.ui.app --advanced
goto :eof

:test
"%PY%" -m pytest -q -p no:cacheprovider
goto done

:alltests
"%PY%" -m pytest -q -p no:cacheprovider -m "model or slow or not model"
goto done

:demo
"%PY%" -m lfi.cli demo "%USERPROFILE%\Documents\lfi-demo"
echo   Now open the app, then Settings - Add Folder, and pick: %USERPROFILE%\Documents\lfi-demo
goto done

:shortcuts
"%PY%" -m lfi.shortcuts
goto done

:status
"%PY%" -m lfi.cli status
goto done

:nosetup
echo   The app is not installed yet. Run once:
echo     powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
goto done

:done
echo.
if "%~1"=="" pause
