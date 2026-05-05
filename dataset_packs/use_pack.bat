@echo off
setlocal

set "PACK=%~1"
set "DO_BUILD=%~2"

if "%PACK%"=="" (
  echo Usage: %~nx0 ^<PACK_FOLDER^> [build]
  echo Example: %~nx0 03_data_1pct build
  exit /b 1
)

set "SCRIPT_DIR=%~dp0"

if /I "%DO_BUILD%"=="build" (
  powershell -ExecutionPolicy Bypass -File "%SCRIPT_DIR%use_pack.ps1" -Pack "%PACK%" -Build
) else (
  powershell -ExecutionPolicy Bypass -File "%SCRIPT_DIR%use_pack.ps1" -Pack "%PACK%"
)
