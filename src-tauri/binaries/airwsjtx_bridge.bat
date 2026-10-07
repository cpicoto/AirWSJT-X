@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%") do set "SCRIPT_DIR=%%~fI"
for %%I in ("%SCRIPT_DIR%..\..") do set "REPO_ROOT=%%~fI"

if defined AIRWSJTX_PYTHON (
  set "PYTHON_BIN=%AIRWSJTX_PYTHON%"
) else (
  where py >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_BIN=py"
  ) else (
    where python >nul 2>nul
    if not errorlevel 1 (
      set "PYTHON_BIN=python"
    ) else (
      echo Python 3 is required to run AirWSJT-X
      exit /b 1
    )
  )
)

if "%PYTHON_BIN%"=="py" (
  py -3 "%REPO_ROOT%\sidecar\airwsjtx_bridge.py" %*
) else (
  "%PYTHON_BIN%" "%REPO_ROOT%\sidecar\airwsjtx_bridge.py" %*
)
