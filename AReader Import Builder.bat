@echo off
rem AReader Import Builder - double-click launcher
cd /d "%~dp0"
start "" pythonw gui.pyw
if errorlevel 1 (
  echo Could not find pythonw. Check that Python is installed.
  pause
)
