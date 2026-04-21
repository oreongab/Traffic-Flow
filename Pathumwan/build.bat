@echo off
echo ============================================
echo   Pathumwan Traffic Simulation — Build Trips
echo ============================================
echo.

REM Resolve Python command (prefer python.exe, fallback to py launcher)
set "PYTHON_CMD=python"
where python >nul 2>nul || (
	where py >nul 2>nul && set "PYTHON_CMD=py"
)

REM Bus trips (insertion-density calibrated from BMA data ~5%% of total traffic)
"%PYTHON_CMD%" "%SUMO_HOME%\tools\randomTrips.py" -n osm.net.xml --fringe-factor 5 --insertion-density 4 -o osm.bus.trips.xml -r osm.bus.rou.xml -b 0 -e 3600 --trip-attributes "departLane=\"best\"" --fringe-start-attributes "departSpeed=\"max\"" --validate --remove-loops --via-edge-types highway.motorway,highway.motorway_link,highway.trunk_link,highway.primary_link,highway.secondary_link,highway.tertiary_link --vehicle-class bus --vclass bus --prefix bus --min-distance 600 --min-distance.fringe 10 --seed 42

REM Motorcycle trips (~15%% of total traffic, short distance)
"%PYTHON_CMD%" "%SUMO_HOME%\tools\randomTrips.py" -n osm.net.xml --fringe-factor 2 --insertion-density 6 -o osm.motorcycle.trips.xml -r osm.motorcycle.rou.xml -b 0 -e 3600 --trip-attributes "departLane=\"best\"" --fringe-start-attributes "departSpeed=\"max\"" --validate --remove-loops --via-edge-types highway.motorway,highway.motorway_link,highway.trunk_link,highway.primary_link,highway.secondary_link,highway.tertiary_link --vehicle-class motorcycle --vclass motorcycle --prefix motorcycle --max-distance 1200 --seed 43

REM Passenger car trips (~60%% of total traffic, high density for Pathumwan)
"%PYTHON_CMD%" "%SUMO_HOME%\tools\randomTrips.py" -n osm.net.xml --fringe-factor 5 --insertion-density 18 -o osm.passenger.trips.xml -r osm.passenger.rou.xml -b 0 -e 3600 --trip-attributes "departLane=\"best\"" --fringe-start-attributes "departSpeed=\"max\"" --validate --remove-loops --via-edge-types highway.motorway,highway.motorway_link,highway.trunk_link,highway.primary_link,highway.secondary_link,highway.tertiary_link --vehicle-class passenger --vclass passenger --prefix veh --min-distance 300 --min-distance.fringe 10 --allow-fringe.min-length 1000 --lanes --seed 44

REM Truck trips (~5%% of total traffic)
"%PYTHON_CMD%" "%SUMO_HOME%\tools\randomTrips.py" -n osm.net.xml --fringe-factor 5 --insertion-density 8 -o osm.truck.trips.xml -r osm.truck.rou.xml -b 0 -e 3600 --trip-attributes "departLane=\"best\"" --fringe-start-attributes "departSpeed=\"max\"" --validate --remove-loops --via-edge-types highway.motorway,highway.motorway_link,highway.trunk_link,highway.primary_link,highway.secondary_link,highway.tertiary_link --vehicle-class truck --vclass truck --prefix truck --min-distance 600 --min-distance.fringe 10 --seed 45

echo.
echo Build complete!
pause
