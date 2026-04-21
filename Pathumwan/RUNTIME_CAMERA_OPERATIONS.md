# Runtime Camera Operations Guide

## Purpose

This guide covers the practical workflow for:

- running the backend with PostgreSQL
- understanding what `python app.py` writes to the database
- importing/exporting camera calibration and zone config
- verifying CCTV, dashboard, density, and camera runtime status
- resetting only the tables you actually need to clear

The current codebase supports both modes:

- `SYSTEM_MODE=sim`: CCTV stream comes from SUMO-rendered moving traffic
- `SYSTEM_MODE=real`: CCTV stream comes from `camera_streams.stream_url` through RTSP/HTTP ingest

## Current Truth About CCTV

### Backend stream source

- `GET /api/cameras/<camera_id>/stream`
	- in `sim` mode: returns live MJPEG from SUMO camera rendering
	- in `real` mode: returns live MJPEG from cached RTSP/HTTP ingest frames
- `GET /api/cameras/<camera_id>/detect/stream`
	- in `sim` mode: returns SUMO-rendered feed with detection overlay
	- in `real` mode: returns the latest annotated detection frame from the tracker loop

### Frontend behavior after this update

- `frontend-next/src/components/CctvFeed.tsx` now uses the backend stream endpoints as the main image
- the small map is now only an inset for context, not the main CCTV surface
- this means the cameras page and dashboard popup now show the backend stream directly

### Important limitation

If a real camera has no `stream_url` configured in `camera_streams`, the backend serves an offline placeholder. That is expected behavior, not a frontend bug.

## What `python app.py` Does To PostgreSQL

When you run:

```powershell
cd Pathumwan/backend
python app.py
```

the backend does the following during bootstrap:

1. Calls `init_db()` to create missing tables if they do not already exist.
2. Calls the camera seed helper from `data/pathumwan_roads.json`.
3. Registers Flask routes.
4. Starts background threads depending on runtime mode.

### What it does not do

- it does **not** truncate runtime tables automatically
- it does **not** wipe existing calibration or zone config
- it does **not** reset users
- it does **not** fully overwrite all camera rows blindly

### What it may update safely on repeated runs

The camera seed is an upsert-style sync for the named cameras in `data/pathumwan_roads.json`.

Repeated runs may update:

- `cameras.name`
- `cameras.road`
- `cameras.junction`
- `cameras.status`
- `cameras.lat/lng` only when DB values are missing in the seed path

Repeated runs do not automatically clear:

- `traffic_detections`
- `traffic_index`
- `road_density`
- `live_vehicle_tracks`
- `live_approach_metrics`
- `camera_calibrations`
- `camera_zones`
- `signal_states`

So if you stop the server and start it again, PostgreSQL is reused. The data remains unless you explicitly delete or truncate it.

## Camera Runtime Config Workflow

There are now two supported workflows.

### 1. Admin API workflow

These endpoints now exist:

- `GET /api/admin/camera-runtime`
- `GET /api/admin/camera-runtime/<camera_id>`
- `PUT /api/admin/camera-runtime/<camera_id>/calibration`
- `PUT /api/admin/camera-runtime/<camera_id>/zones`
- `GET /api/admin/camera-runtime/export`
- `POST /api/admin/camera-runtime/import`

Use this when:

- you want to inspect readiness per camera
- you want to update a single camera without reseeding all cameras
- you want frontend/admin integrations later

### 2. Seed JSON workflow

Use this when:

- you want a version-controlled config file
- you want to move calibration/zone config between machines
- you want repeatable deployment and backup

Main files:

- `data/camera_runtime_config.json`: minimal template
- `data/camera_runtime_config.generated.json`: exported runtime snapshot from the current DB camera inventory
- `backend/seed_camera_runtime.py`: import/export helper script

## Export A Tuning Template

Export the current active camera inventory from PostgreSQL to JSON:

```powershell
cd Pathumwan/backend
python seed_camera_runtime.py --export-template ..\data\camera_runtime_config.generated.json
```

Use this file for camera-by-camera tuning.

## Import Runtime Config Back Into PostgreSQL

Import calibration only:

```powershell
cd Pathumwan/backend
python seed_camera_runtime.py --file ..\data\camera_runtime_config.generated.json
```

Import and replace zones for the cameras included in the file:

```powershell
cd Pathumwan/backend
python seed_camera_runtime.py --file ..\data\camera_runtime_config.generated.json --replace-zones
```

Dry-run validation without writing:

```powershell
cd Pathumwan/backend
python seed_camera_runtime.py --file ..\data\camera_runtime_config.generated.json --dry-run
```

## What Must Be Filled For Reliable Real Metrics

For each camera, reliable mapped metrics need:

### Calibration

At least one of these must be valid:

- homography matrix
- anchor lat/lng + image size + pixels_per_meter

Recommended fields:

```json
{
	"image_width": 1280,
	"image_height": 720,
	"anchor_lat": 13.7466,
	"anchor_lng": 100.5291,
	"bearing_deg": 0,
	"pixels_per_meter": 15.0,
	"homography_matrix": []
}
```

### Zones

Each camera should define at least:

- one `presence` zone
- one `queue` or `stopline` zone near the stop line
- optional `flow` counting line for throughput quality

Example shape:

```json
{
	"zone_id": "north_presence",
	"junction_id": "pathumwan",
	"approach_id": "north",
	"road_id": "RAMA1",
	"zone_type": "presence",
	"enabled": true,
	"polygon_points": [[100, 120], [420, 110], [430, 300], [110, 320]],
	"line_points": []
}
```

For a counting line:

```json
{
	"zone_id": "north_stopline",
	"junction_id": "pathumwan",
	"approach_id": "north",
	"road_id": "RAMA1",
	"zone_type": "stopline",
	"enabled": true,
	"polygon_points": [],
	"line_points": [[250, 140], [250, 310]]
}
```

## How To Verify The System

### 1. Verify public backend APIs

Expected public endpoints:

- `/api/cameras/`
- `/api/traffic-index`
- `/api/road-density`
- `/api/vehicles`
- `/api/health`

Current checked behavior in this workspace:

- `/api/cameras/` returns active cameras from DB
- `/api/road-density` returns 10 monitored roads
- `/api/traffic-index` now falls back from `traffic_index` to `road_density` when needed
- `/api/vehicles` returns live mapped tracks only when calibration exists; otherwise it stays empty by design

### 2. Verify camera runtime readiness

Use the admin page or API.

Frontend:

- `/admin` now shows a `Camera Runtime Status` section

Backend API:

```powershell
curl http://localhost:5000/api/admin/camera-runtime
```

You should look for:

- `calibration_ready`
- `zone_count`
- `enabled_zone_count`
- `tracking_ready`
- `latest_metric_at`

### 3. Verify cameras page

Expected behavior:

- the main panel shows the backend stream, not only a map snapshot
- `detect mode` uses `/detect/stream`
- the small inset map only shows track markers when live tracks have valid lat/lng
- counts come from backend counts/tracks polling

### 4. Verify dashboard page

Expected behavior:

- camera popup shows live backend stream
- vehicle markers appear only when mapped tracks exist
- traffic index reflects live-state, SUMO, or DB density fallback depending on runtime state

### 5. Verify density page

Expected behavior:

- 10 monitored roads are listed
- if live road metrics exist, they are used
- if not, DB road density fallback is used
- if all road density rows are zero, traffic index may legitimately be `0.0`

## Current Verified State In This Workspace

At the time of writing, checked results were:

- active DB cameras: `47`
- camera runtime status rows: `47`
- calibration-ready cameras: `0`
- zone-configured cameras: `0`
- tracking-ready cameras: `0`

That means the platform is structurally ready, but real mapped vehicle metrics are not yet trustworthy until real calibration and zones are filled per camera.

## Why Vehicle Markers May Still Be Empty

This is expected if one of these is true:

- no RTSP frame is available
- no detections are being produced
- no calibration is configured
- tracks exist but cannot be projected to lat/lng

In that case:

- camera stream can still work
- per-camera YOLO counts can still work
- dashboard map vehicle markers can remain empty intentionally

## Tables You Usually Do Not Need To Reset

Normally, after stopping and rerunning the backend, you do **not** need to reset anything.

Only reset tables if you want to clear stale runtime state or start a new tuning cycle.

## Safe Reset Recipes

### A. Reset only live runtime state

Use this when you want a clean runtime start but keep users, cameras, calibration, zones, and history.

```sql
TRUNCATE TABLE live_vehicle_tracks RESTART IDENTITY;
TRUNCATE TABLE live_approach_metrics RESTART IDENTITY;
TRUNCATE TABLE signal_states RESTART IDENTITY;
```

### B. Reset live runtime state plus transient detections/indexes

Use this when you want a fresh demo run.

```sql
TRUNCATE TABLE live_vehicle_tracks RESTART IDENTITY;
TRUNCATE TABLE live_approach_metrics RESTART IDENTITY;
TRUNCATE TABLE signal_states RESTART IDENTITY;
TRUNCATE TABLE traffic_detections RESTART IDENTITY;
TRUNCATE TABLE traffic_index RESTART IDENTITY;
TRUNCATE TABLE road_density RESTART IDENTITY;
```

These names are singular because they must match the actual SQLAlchemy model table names in `backend/database/models.py`.

### C. Reset camera tuning only

Use this if calibration/zone tuning went wrong and you want to start over.

```sql
TRUNCATE TABLE camera_calibrations RESTART IDENTITY;
TRUNCATE TABLE camera_zones RESTART IDENTITY;
TRUNCATE TABLE live_vehicle_tracks RESTART IDENTITY;
TRUNCATE TABLE live_approach_metrics RESTART IDENTITY;
```

### D. Reset stream config only

Use this when stream URLs are wrong and you want to reconfigure them.

```sql
TRUNCATE TABLE camera_streams RESTART IDENTITY;
```

## Reset Via Python Instead Of SQL

If you prefer Python inside the repo environment:

```python
from database.connection import get_session
from database.models import LiveVehicleTrack, LiveApproachMetric, SignalState

session = get_session()
try:
		session.query(LiveVehicleTrack).delete()
		session.query(LiveApproachMetric).delete()
		session.query(SignalState).delete()
		session.commit()
finally:
		session.close()
```

## Recommended Practical Workflow

1. Export current runtime template.
2. Fill calibration and zones camera by camera.
3. Dry-run the seed file.
4. Import the seed file into PostgreSQL.
5. Start `python app.py`.
6. Check `/admin` camera runtime status.
7. Check `/cameras` for stream and counts.
8. Check `/dashboard` for popups and traffic index.
9. Check `/density` for road-level behavior.

## What Still Requires Real-World Tuning

The code can now support real metrics, but it cannot invent correct geometry.

You still need real per-camera work for:

- calibration quality
- zone placement
- stopline placement
- counting direction validation
- RTSP stream quality and frame rate

Without those inputs, the system will run, but mapped live metrics will stay conservative or empty by design.
