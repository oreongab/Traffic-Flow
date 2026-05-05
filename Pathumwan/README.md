# TraffixFlow: Smart Area Traffic Analytics

**TraffixFlow** is a comprehensive, hybrid intelligent traffic analysis and simulation workspace focused explicitly on the **Pathumwan** district in Bangkok. It combines real-time YOLOv12 AI computer vision with Eclipse SUMO simulation data to create accurate, location-aware traffic indices and CCTV control surfaces.

## 🚀 Core Features Matrix

| Feature | Real-Time Mode (`SYSTEM_MODE=real`) | Simulation Mode (`SYSTEM_MODE=sim`) |
| :--- | :--- | :--- |
| **Data Source** | Actual RTSP/MJPEG camera streams | SUMO / TraCI Headless Server |
| **Traffic Engine** | YOLOv12 + DeepSORT Tracking | TraCI edge induction loops |
| **Motion Sensing** | Sparse LK Optical Flow (2 fps worker) | n/a — SUMO ทราบ velocity ตรงจาก TraCI |
| **Index Fallback** | Historical decay logic applied on YOLO blindness | Pure simulation geometry averages |
| **CCTV Surface** | Authentic detection feed with object boxes | TraCI snapshot mapped onto Leaflet Mini-Map |

---

## 🛠️ Technology Stack

- **Frontend Environment**: Next.js 16 (React 19), TailwindCSS Vanilla, Chart.js, Leaflet JS.
- **Backend Environment**: Python 3.10+, Flask, SQLAlchemy (PostgreSQL / SQLite).
- **AI & Analytics**: YOLOv12 (Ultralytics), OpenCV (Sparse Lucas-Kanade Optical Flow), DeepSORT Tracking.
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
- `detection/tracker_service.py`: Passes frames through YOLOv12. Acts as the **Single Source of Truth** for real-world speed (`avg_speed`), counts (`vehicle_count`), and spatial mapping (`occupancy_ratio`).
- `detection/detector_service.py`: Caches stream buffers, drawing bounding boxes for the frontend CCTV view over MJPEG.

### 1.5 🌊 Optical Flow Augmentation (Real-Mode)

`services/optical_flow.py` รัน worker thread อิสระ ใช้ `cv2.calcOpticalFlowPyrLK` (Sparse Lucas-Kanade)
เสริม 4 จุดอ่อนของ YOLO-only pipeline โดยไม่กระทบ pipeline เดิมเลย:

- **YOLO Blindness Fallback**: เมื่อ YOLO ตรวจไม่เจอ (รถซ้อน/กลางคืน/ฝน) → scene flow magnitude
  บอก "ยังเคลื่อนที่" vs "นิ่งจริง" — `extra_metadata.flow_active = True` แทนที่ count = 0
- **Speed Accuracy**: bbox-center diff ที่ 5s gap (รถ 60 km/h ขยับ ~83m, noise สูง) →
  blended กับ LK velocity ที่ 0.5s gap (motion 3-15 px) ด้วยสูตร `0.7 * lk + 0.3 * centroid`
- **Tracker Association**: ใช้ flow vector ทำนาย bbox position ใน frame ถัดไป (5s) →
  `_match_track` matched ID ได้แม่นขึ้น โดยเฉพาะมอเตอร์ไซค์ที่วิ่งเร็ว
- **Stop-and-go Detection**: flow magnitude < `OPTICAL_FLOW_QUEUE_MAGNITUDE_THRESHOLD_PX` (1.5 px)
  ในกรอบ bbox → ตรวจจับรถจอด/ติด ที่ speed estimate noisy

**ระบบไม่เซฟภาพลงดิสก์เลย** — เป็น read-only consumer ของ `rtsp_ingest._frame_cache` (RAM)
decode JPEG → BGR → gray ใน memory แล้วทิ้ง buffer; เก็บแค่ `prev_gray` (~75 KB/cam × 55 cams ≈ 4 MB)
ทุกอย่าง in-memory — **ไม่แตะ database schema** ปิด/เปิดผ่าน env:
`OPTICAL_FLOW_ENABLED=0/1`, `OPTICAL_FLOW_FPS_TARGET=2.0`, `OPTICAL_FLOW_CAMERA_ALLOWLIST=cam1,cam2`

**Algorithm — Sparse Lucas-Kanade**:
- Feature detection: `cv2.goodFeaturesToTrack` (Shi-Tomasi corner) — refresh ทุก 5 วิ
- Tracking: 2-level pyramid LK, window 15×15
- Outlier rejection: forward-backward consistency check (drop จุดที่ reverse error > 1.0 px)

**OpenCV APIs called** (อยู่ใน `services/optical_flow.py`):
- `cv2.imdecode` (JPEG → BGR), `cv2.cvtColor` (BGR → gray), `cv2.resize` (downsample 320×scaled)
- `cv2.goodFeaturesToTrack`, `cv2.calcOpticalFlowPyrLK`, `cv2.pointPolygonTest`

**Public API ให้ tracker เรียก**:
| Function | คืนค่า | ใช้ที่ไหน |
|---|---|---|
| `get_camera_scene_flow(camera_id)` | `{magnitude, direction_deg, active_ratio, ts}` | Goal 1 — blindness |
| `get_bbox_flow(camera_id, bbox)` | `{vx_px, vy_px, magnitude_px, n_points, dt_s}` | Goal 2 / 4 — speed / queue |
| `get_predicted_center(cam_id, prev_center, dt_s)` | `(x,y) \| None` | Goal 3 — assoc |
| `get_zone_flow(camera_id, zone_id)` | `{magnitude, n_points}` | future per-approach flow |

**Resource budget**:
- CPU: ~20% ของ 1 core (200 corners × 2-pyramid × 320×240, 28 cams/tick)
- RAM: ~4 MB (prev_gray cache 55 cams)
- Disk: 0 — ทุกอย่างใน memory

**ผลกระทบต่อการเปลี่ยน Dataset**: Optical flow operate บน RTSP frame เท่านั้น —
ไม่แตะ `osm.*`, `dataset_packs/`, หรือ SUMO routes สลับ dataset ผ่าน `use_pack.bat` ได้ปกติ

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

### Docker Compose (CPU vs GPU)

Default profile รัน CPU-only — เหมาะกับโน้ตบุ๊กไม่มี NVIDIA (เช่น Intel Iris Xe):
```bash
docker compose up --build
```

GPU profile รัน CUDA 12.1 PyTorch wheels — ต้องมี NVIDIA GPU + nvidia-container-toolkit:
```bash
docker compose --profile gpu up --build
```
ภายใน YOLO detector จะ auto-detect device (`cuda` → `mps` → `cpu`) และเปิด `half()` (FP16) อัตโนมัติบน CUDA

### Lightning AI deployment (T4 ก็พอ)

Lightning Studios เป็น Ubuntu — รัน SUMO ได้ปกติ:

1. สร้าง Studio + เลือก instance T4 (หรือ L4 ก็ได้, T4 มี VRAM 16 GB เพียงพอ)
2. Clone repo + ติดตั้ง:
   ```bash
   sudo apt-get update && sudo apt-get install -y sumo sumo-tools
   export SUMO_HOME=/usr/share/sumo
   cd Pathumwan && docker compose --profile gpu up --build
   ```
3. Lightning จะให้ public URL อัตโนมัติ — set `NEXT_PUBLIC_API_URL` ตามนั้นใน frontend build args

T4 capacity: YOLO12n @ 480px ≈ 6-8 ms/frame (batched) ≈ ~125 fps headroom; ระบบใช้แค่ ~110 fps (55 cams × 2 Hz) → เพียงพอมีระยะเหลือ

### Environment variables (เพิ่มใหม่ปี 2026-05)

| Variable | Default | คำอธิบาย |
|---|---|---|
| `WITH_CUDA` (Docker build arg) | `0` | `1` เพื่อติดตั้ง torch CUDA wheels (ใช้กับ NVIDIA / Lightning T4) |
| `SIGNAL_PROGRAM_MODE` | `pair` | `pair` (NS/EW เขียวคู่กัน, default) หรือ `sequential4` (เขียวทีละทิศ N→E→S→W) |
| `CAMERA_RENDER_FPS` | `4` | จำกัด FPS ของ camera capture loop เพื่อกัน YOLO+SUMO กิน CPU จนเว็บช้า |
| `YOLO_IMGSZ` | `480` | ความละเอียดที่ใส่ให้ YOLO (320 = เร็วสุด CPU; 640 = ดูรถเล็ก) |
| `AGGREGATION_INTERVAL` | `30` | วินาที — `services/aggregation.py` รันถี่แค่ไหน (default 30s ทำให้ tab `จำนวนรถในแต่ละวัน`/`TOP 10` กระดิกเร็ว, เดิมเป็น 600s) |

### Troubleshooting

- **กล้องขึ้น "ไม่พบสตรีมของกล้องนี้":** ตอนนี้ backend จะส่ง JPEG placeholder ภาษาไทย ("รอ Simulation เริ่มต้น" / "ยังไม่พร้อมใช้งาน") แทน HTTP 5xx เสมอ → `<img>` จะไม่ trigger error overlay สำหรับกล้องใน roster อีก หากเห็น overlay แสดงว่ากล้องอยู่นอก roster หรือ network ผิดพลาด
- **สถิติเป็น 0 ตลอด:** API ส่ง `data_available: false` เมื่อยังไม่มีข้อมูล — Dashboard / Statistics จะแสดง "ไม่มีข้อมูล" แทนเลข 0 ตรวจสอบว่า SUMO simulation `sim_active=true` ผ่าน `/api/status`
- **/control ขึ้น "เกิดข้อผิดพลาด":** API ตอบ 200 + `{applied: false, reason: "sim_inactive"}` เมื่อ sim ยังไม่พร้อม → frontend แสดง "Simulation ยังไม่พร้อม — เริ่ม SUMO ก่อน" ไม่ใช่ generic error
- **อยากให้ไฟเขียวทีละด้าน:** ตั้ง `SIGNAL_PROGRAM_MODE=sequential4` แล้ว restart backend; ที่หน้า /control เลือก direction (เหนือ / ตะวันออก / ใต้ / ตะวันตก / ทุกแนว) ก่อนกดสี เพื่อ override ทิศใดทิศหนึ่ง
- **โหมด AI ตอนนี้ทำงานยังไง:** `_start_signal_apply_loop` เรียก `decide_ai_actions(simulation)` ทุก 5 วินาทีเมื่อ `mode=ai`. heuristic ดู YOLO vehicle count ต่อกล้อง → จับคู่ camera→edge ผ่าน `road_mapping` → เลือก phase ใน TLS program ที่ให้ green กับลานที่รถเยอะที่สุด → `apply_ai_actions(...)` (เรียก `traci.trafficlight.setPhase`). ผลตัดสินใจล่าสุดดูได้ที่ `/api/admin/ai-status` field `last_decisions[]` และโผล่ที่หน้า /control ใต้ toggle. การ override manual (เช่น "เขียวทั้งหมด") ยังคง apply ผ่าน TraCI ทันทีและ reassert ทุก 2s
- **สถิติทั้ง 4 tab ไม่อัปเดต:** หน้า /statistics ตอนนี้ poll API ทุก 30 วินาที (`tab=ดัชนี/ปี/รายวัน/TOP10`) — ตรวจ DevTools network ว่ามีคำขอซ้ำ. ถ้าเลขนิ่งจริง ๆ แปลว่า YOLO/SUMO ยังไม่มี detection ใหม่ใน `traffic_detections` (รอ `INDEX_INTERVAL` + `AGGREGATION_INTERVAL`)
- **กดถนนใน /density แล้วซูมไปนอกเขต:** แก้แล้วใน `_compute_road_geometry` (filter vertex ผ่าน `is_in_pathumwan`). ถ้ายังเจอให้เช็ก `road_mapping` ใน simulation log — edges บางตัวอาจถูก map ผิด

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