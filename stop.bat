@echo off
setlocal

cd /d "%~dp0"

echo Stopping Conductor platform...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop.ps1"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" (
  echo Stop failed with exit code %EXIT_CODE%.
) else (
  echo Stop command completed.
)
echo.
pause
exit /b %EXIT_CODE%
