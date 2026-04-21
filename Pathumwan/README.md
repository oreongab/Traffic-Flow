# TraffixFlow: Smart Area Traffic Analytics

**TraffixFlow** is a comprehensive, hybrid intelligent traffic analysis and simulation workspace focused explicitly on the **Pathumwan** district in Bangkok. It combines real-time YOLOv8 AI computer vision with Eclipse SUMO simulation data to create accurate, location-aware traffic indices and CCTV control surfaces.

## 🚀 Core Features Matrix

| Feature | Real-Time Mode (`SYSTEM_MODE=real`) | Simulation Mode (`SYSTEM_MODE=sim`) |
| :--- | :--- | :--- |
| **Data Source** | Actual RTSP/MJPEG camera streams | SUMO / TraCI Headless Server |
| **Traffic Engine** | YOLOv8 + DeepSORT Tracking | TraCI edge induction loops |
| **Index Fallback** | Historical decay logic applied on YOLO blindness | Pure simulation geometry averages |
| **CCTV Surface** | Authentic detection feed with object boxes | TraCI snapshot mapped onto Leaflet Mini-Map |

---

## 🛠️ Technology Stack

- **Frontend Environment**: Next.js 16 (React 19), TailwindCSS Vanilla, Chart.js, Leaflet JS.
- **Backend Environment**: Python 3.10+, Flask, SQLAlchemy (PostgreSQL / SQLite).
- **AI & Analytics**: YOLOv8 (Ultralytics), OpenCV, DeepSORT Tracking.
- **Simulation**: Eclipse SUMO, `osm.net.xml`, TraCI connector.

---

## 🧩 Pipeline Architecture

The system is designed around 4 major asynchronous pipelines running within `backend/app.py`:

### Camera Inventory Truth

- `data/pathumwan_roads.json` is now treated as a **label/metadata seed** for named Pathumwan cameras, not the full inventory.
- The canonical camera inventory is derived from **all traffic-light junctions in `osm.net.xml`**. In the current Pathumwan network this yields **55 active cameras**.
- `backend/services/camera_sync.py` exposes `repair_camera_inventory_if_needed()` so API/admin flows can auto-heal the database if the `cameras` table collapses to a stale subset (for example, 13 total rows / 2 active rows).
- Sync is now **foreign-key safe**: when a DB row is matched by `sumo_tls_id`, the existing `camera_id` is preserved instead of being rewritten, so `approaches.camera_id` references do not break.
- `data/camera_runtime_config.generated.json` is generated from the repaired DB inventory and now exports **55 cameras**, matching backend runtime status and the frontend camera list.

### 1. 📷 Ingestion & AI Pipeline (Real-Mode)
- `services/rtsp_ingest.py`: Fetches real-world camera streams (or fallbacks to loop feeds to prevent crashes).
- `detection/tracker_service.py`: Passes frames through YOLOv8. Acts as the **Single Source of Truth** for real-world speed (`avg_speed`), counts (`vehicle_count`), and spatial mapping (`occupancy_ratio`).
- `detection/detector_service.py`: Caches stream buffers, drawing bounding boxes for the frontend CCTV view over MJPEG.

### 2. 🚦 Simulation Pipeline (Sim-Mode)
- `simulation.py`: Runs a headless SUMO instance. Extrapolates real-time map data mathematically.
- `services/traffic_index.py`: Gathers intersection density by summing all vehicles inside bounding radii.
- `cctv_renderer.py` / `CctvMiniMap`: Renders cars physically mapped onto an exact Cartesian scale and pushes coordinates to a Leaflet GUI module.

### 3. 🧠 Traffic Index Strategy
- Data from either YOLO or SUMO is aggregated.
- **YOLO Blindness Fix (Zero-Value Fallback):** If traffic is heavily congested and YOLO fails to draw boxes (yielding `count=0`), the system dynamically queries `data/pathumwan_traffic_profile.json` (a historical profile for Pathumwan 2022-2023). It injects the historical `speed_avg` and `vc_avg` into the hourly block so the dashboard does not crash back down to `0.0` arbitrarily.
- Produces `Area Index` (0.0 to 10.0 scale) and stores it in `TrafficIndex` & `TrafficIndexRoad`.

### 4. 📊 Statistics Aggregation
- `services/aggregation.py`: Sums metrics hourly. Mathematically enforces `Total = Car + Bus + Truck + Motorcycle` to avoid mismatched DB states.
- `services/daily_stats.py`: Summarizes data for the `/app/statistics` routing.

---

## 📂 Code Structure & Entry Points

**Backend Services:**
- `app.py`: The core daemon, launches threads based on `Config.SYSTEM_MODE`.
- `services/camera_sync.py`: Builds the full traffic-light camera inventory from `osm.net.xml`, repairs DB inventory drift, and keeps `cameras`, `junctions`, and `approaches` aligned.
- `routes/control.py`, `routes/stats.py`: REST interfaces answering to frontend fetches.

**Frontend Services (Next.js):**
- `/dashboard`: High-level aggregated indices, pie charts.
- `/control`: AI traffic light toggles. Camera resolution now falls back by `junction_id` / `sumo_tls_id`, so the selected feed does not jump between cameras when metadata refreshes.
- `/statistics`: Temporal data analysis rendering Chart.js objects.

## 📷 Camera Coverage, YOLO, and Position Consistency

- **Simulation mode:** `backend/simulation.py` collects one camera per SUMO traffic light, so YOLO / detection loops iterate over the same **55-camera** inventory that the map and dashboard expose.
- **Real mode:** runtime catalog helpers (`services/mapping.py`, `services/camera_runtime.py`) now trigger inventory repair before loading active cameras, preventing the real pipeline from silently shrinking back to a 13-camera or 2-camera subset.
- **Coordinate consistency:** camera positions returned by `/api/cameras` prefer live SUMO coordinates, then DB coordinates, then offline network inventory coordinates. This keeps the CCTV marker, mini-map, and backend camera location aligned to the same junction source.
- **Calibration note:** all 55 cameras now exist in the runtime catalog, but precise world-projection accuracy for object tracks still depends on per-camera `calibration_data` / `zones`. Inventory coverage and stream selection are fixed; calibration remains tunable camera-by-camera.

---

## 💻 Getting Started

### Complete Development Run
From the project root on a Windows terminal:
```bat
start.bat
```
*(This starts the Flask REST API on `localhost:5000` and Next.js frontend on `localhost:3000`.)*

### Manual Frontend Build
```powershell
cd Pathumwan\frontend-next
npm install
npm run dev -- -p 3000
```

### Manual Backend Build
```powershell
cd Pathumwan\backend
python -m pip install -r requirements.txt
python app.py
```

### รัน SUMO แบบเห็นหน้าต่าง (SUMO GUI mode)

By default `python app.py` starts SUMO headless. To open the **sumo-gui** window
(useful while iterating on AI logic or manual signal control):

**Option A — inline env var (PowerShell):**
```powershell
cd Pathumwan\backend
$env:SUMO_GUI = "1"
python app.py
```

**Option B — bash / CMD:**
```bat
cd Pathumwan\backend
set SUMO_GUI=1
python app.py
```

**Option C — persist in `backend/.env`:**
```
SUMO_GUI=1
```

After launch:
1. The `sumo-gui` window opens with the Pathumwan network preloaded.
2. Click the green **Play** (▶) button (or press Ctrl+A) to step the simulation. TraCI
   continues to drive the logic — you are only *observing*.
3. Use the TLS drop-down to inspect any junction; when you press manual color/phase in
   `/control`, you should see the TLS flip in sumo-gui within ~2 seconds (Signal Apply
   loop period).
4. To go back to headless mode, remove `SUMO_GUI` from env / `.env` and restart backend.

**Requirements:** `$SUMO_HOME` must point to an Eclipse SUMO install that includes
`bin/sumo-gui.exe` (it ships with the standard SUMO installer on Windows).

### Optional Inventory / Runtime Repair Commands
```powershell
cd Pathumwan\backend
python sync_traffic_light_cameras.py
```

```powershell
cd Pathumwan\backend
python -c "from services.camera_runtime import write_runtime_seed_template; write_runtime_seed_template(r'..\\data\\camera_runtime_config.generated.json')"
```

- The first command backfills / repairs the DB camera inventory from `osm.net.xml`.
- The second command refreshes the generated runtime camera config export from the current DB state.

---

## 📦 Database Schema (16 Tables)

The application normalizes all data through an SQLAlchemy 16-table schema.
- **Documentation:** See `DB_TABLES_REFERENCE.md` for column definitions, or `DB_DIAGRAM.md` for relationships.
- **Migrations:** Modifying classes in `backend/database/models.py` triggers an auto-schema setup using native SQLite constraints if Postgres is unavailable.