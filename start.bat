@echo off
setlocal

cd /d "%~dp0"

echo Starting Conductor platform...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" -Open
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" (
  echo Start failed with exit code %EXIT_CODE%.
) else (
  echo Start command completed.
)
echo.
pause
exit /b %EXIT_CODE%
