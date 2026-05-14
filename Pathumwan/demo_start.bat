@echo off
setlocal EnableExtensions

pushd "%~dp0"

echo ============================================
echo   Pathumwan AI Traffic — Starting Docker
echo ============================================
echo.
echo Starting all services (Database, Backend, Frontend) using Docker Compose...
echo.

docker compose up -d

echo.
echo ============================================
echo   Services are starting in the background!
echo ============================================
echo.
echo Backend API: http://localhost:5000
echo Web UI (Next.js): http://localhost:3000
echo.
echo Tip: To view logs, run: docker compose logs -f
echo Tip: To stop all services, run: docker compose down
echo.

popd
pause
