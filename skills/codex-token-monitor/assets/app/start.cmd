@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 launch.py %*
  if errorlevel 1 pause
  exit /b
)
where python >nul 2>nul
if not errorlevel 1 (
  python launch.py %*
  if errorlevel 1 pause
  exit /b
)
echo Python 3 was not found. Ask Codex to launch this app with an available Python 3 interpreter.
pause
