@echo off
setlocal EnableExtensions

pushd "%~dp0"

REM Keep in sync with start.bat
set "BACKEND_PORT=5000"
set "FRONTEND_PORT=3000"

echo ============================================
echo   TraffixFlow — Stop Dev Servers
echo ============================================
echo.

echo Stopping listeners on ports %FRONTEND_PORT% and %BACKEND_PORT%...

REM Use PowerShell for reliable port->PID lookup
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ports=@(%FRONTEND_PORT%,%BACKEND_PORT%); $conns=Get-NetTCPConnection -State Listen -LocalPort $ports -ErrorAction SilentlyContinue; $pids=@($conns | Select-Object -ExpandProperty OwningProcess -Unique); if($pids.Count -eq 0){ Write-Host 'No listening processes found.'; exit 0 }; Write-Host ('Stopping PIDs: ' + ($pids -join ', ')); Stop-Process -Id $pids -Force -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 200; $left=Get-NetTCPConnection -State Listen -LocalPort $ports -ErrorAction SilentlyContinue; if($left){ Write-Host 'Some listeners remain:'; $left | Select-Object LocalAddress,LocalPort,OwningProcess | Format-Table -AutoSize } else { Write-Host 'Stopped.' }"

echo.
popd
pause
