@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-LocalStudio.ps1" -Mode Desktop %*
if errorlevel 1 (
  echo.
  echo Studio startup failed. Check the message above and data\logs.
  pause
  exit /b 1
)
endlocal
