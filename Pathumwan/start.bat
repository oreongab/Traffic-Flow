@echo off
setlocal EnableExtensions

pushd "%~dp0"

REM Standard dev ports (keep frontend + backend consistent)
set "BACKEND_PORT=5000"
set "FRONTEND_PORT=3000"
set "FRONTEND_URL=http://localhost:%FRONTEND_PORT%"
set "NEXT_PUBLIC_API_URL=/api"
set "BACKEND_INTERNAL_URL=http://127.0.0.1:%BACKEND_PORT%"

REM Resolve Python command (prefer python.exe, fallback to py launcher)
set "PYTHON_CMD=python"
where python >nul 2>nul || (
	where py >nul 2>nul && set "PYTHON_CMD=py"
)

echo ============================================
echo   Pathumwan AI Traffic — Starting Dev Server
echo ============================================
echo.

REM Start Next.js frontend in the SAME console so closing this window stops everything.
REM Use npm.cmd to avoid PowerShell execution policy blocking npm.ps1.
echo Starting Next.js dev server...
start "TraffixFlow Frontend (Next.js)" /b cmd /c "cd /d frontend-next && if not exist node_modules (npm.cmd install) && set PORT=%FRONTEND_PORT% && set NEXT_PUBLIC_API_URL=%NEXT_PUBLIC_API_URL% && set BACKEND_INTERNAL_URL=%BACKEND_INTERNAL_URL% && npm.cmd run dev -- -p %FRONTEND_PORT%"

REM Install backend dependencies if needed
echo.
echo Installing backend dependencies (if needed)...
"%PYTHON_CMD%" -m pip install -r backend\requirements.txt --quiet

echo.
echo Starting Flask server...
echo Backend API: http://localhost:%BACKEND_PORT%
echo Web UI (Next.js): http://localhost:%FRONTEND_PORT%
echo.
echo Tip: Closing this window should stop both servers.
echo If any ports remain open, run stop.bat.
echo.
set "FLASK_PORT=%BACKEND_PORT%"
set "FRONTEND_URL=%FRONTEND_URL%"
"%PYTHON_CMD%" backend\app.py

popd
pause
