@echo off
echo ============================================
echo   Pathumwan Traffic Simulation — Build Dataset Routes
echo ============================================
echo.

REM Resolve Python command (prefer python.exe, fallback to py launcher)
set "PYTHON_CMD=python"
where python >nul 2>nul || (
	where py >nul 2>nul && set "PYTHON_CMD=py"
)

if "%SUMO_HOME%"=="" (
	echo SUMO_HOME is not set.
	exit /b 1
)

set "DUAROUTER_CMD=%SUMO_HOME%\bin\duarouter.exe"
if not exist "%DUAROUTER_CMD%" set "DUAROUTER_CMD=duarouter"

"%PYTHON_CMD%" generate_dataset_routes.py --dataset data\Dataset.csv --net osm.net.xml --output-trips osm.dataset.trips.xml --output-mapping data\dataset_route_mapping.generated.json
if errorlevel 1 exit /b 1

"%DUAROUTER_CMD%" -n osm.net.xml --route-files osm.dataset.trips.xml -o osm.dataset.rou.xml.gz --alternatives-output NUL --remove-loops true --ignore-errors true --no-step-log true
if errorlevel 1 exit /b 1

echo.
echo Build complete!
pause
