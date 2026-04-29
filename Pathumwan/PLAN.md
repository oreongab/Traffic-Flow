# TraffixFlow Pathumwan — คู่มือ Onboarding สำหรับผู้พัฒนาต่อ (โดยเฉพาะส่วน AI)

> เอกสารนี้เขียนขึ้นเพื่อให้เพื่อน/ทีมที่จะเข้ามาพัฒนาส่วน AI ของระบบอ่านแล้วทำงานต่อได้ทันที
> ภายในเล่มนี้จะอธิบายทุกอย่างที่คุณต้องรู้ก่อนแก้โค้ด ตั้งแต่ตัวโปรเจคทำอะไร ใช้เทคโนโลยีอะไร
> ไฟล์แต่ละไฟล์ทำหน้าที่อะไร มี API ตัวไหน หน้าเว็บใช้ API ไหน และจะต่อส่วน AI เข้ากับระบบยังไง

---

## 1. สรุปโปรเจค (What & Why)

**TraffixFlow Pathumwan** คือระบบ **Traffic Management Dashboard** สำหรับเขตปทุมวัน (10 ถนนหลัก)
ที่รวม Simulation + Computer Vision + ฐานข้อมูล + เว็บแอดมิน เข้าด้วยกัน เพื่อให้ผู้ใช้เห็น
สถานการณ์จราจรแบบ real-time และสามารถควบคุม/จำลองสัญญาณไฟได้

### ปัญหาที่โปรเจคนี้พยายามแก้

- **ข้อมูลจราจร กทม. กระจัดกระจาย** — กล้อง CCTV ของหน่วยงานต่างๆ ไม่มีจุดศูนย์กลางรวบรวมและแสดงผล
- **ยังไม่มีเครื่องมือทดลองปรับสัญญาณไฟแบบ Adaptive** — งานวิจัย RL ด้านสัญญาณไฟต้องมีสภาพแวดล้อมจำลอง
  ที่ใกล้ของจริง พร้อม API ให้ agent ลอง-ผิด-ลองใหม่ได้
- **ต้องการแสดงผลแบบ dashboard เดียวจบ** — ให้เห็นแผนที่ + ตัวเลขดัชนีจราจร + สถิติย้อนหลัง +
  ภาพกล้อง CCTV และสามารถสลับโหมด manual/AI ควบคุมไฟจราจรได้

### กลุ่มผู้ใช้งาน

1. **ผู้ดูแลเขตปทุมวัน / เจ้าหน้าที่จราจร** — ดู dashboard, ปรับสัญญาณไฟ manual
2. **นักวิจัย AI** — plug-in โมเดล RL เข้า pipeline แล้วดูผล live ผ่าน Control page
3. **ผู้บริหาร** — ดูดัชนี/สถิติรายวัน-รายปีเพื่อตัดสินใจเชิงนโยบาย

---

## 2. เครื่องมือที่เลือกใช้ + เหตุผล

| เทคโนโลยี | ตัวเลือกเดิมที่พิจารณา | ที่เลือกเพราะ |
|---|---|---|
| **SUMO (Simulation of Urban Mobility)** | AIMSUN, VISSIM, CARLA | Open-source, import OSM ได้ตรงเขต, มี TraCI API ให้ Python พูดคุยแบบ real-time, support `traci.trafficlight.setPhase()` (คำสั่งพื้นฐานของ RL agent) |
| **YOLO12n (Ultralytics)** | YOLO11, Faster R-CNN | 12n ยังเบาพอสำหรับ 55 กล้อง × ทุก 5s, pretrained COCO มี vehicle classes (2 car, 3 motorcycle, 5 bus, 7 truck) ครบ, API สวยและสลับ backbone ได้ง่าย |
| **Flask 3** | FastAPI, Django | Lightweight, ไม่ติด framework convention, รองรับ MJPEG streaming + JSON + background threads ในตัวเดียวได้สบาย |
| **Next.js 16 App Router + React 19** | Vite SPA, plain React | Hot-reload ดี, file-based routing ช่วย prototype หน้าเว็บหลาย ๆ หน้าเร็ว, รองรับ dynamic import (สำหรับ Leaflet ที่ต้อง window) |
| **Neon PostgreSQL (+SQLite fallback)** | MySQL, MongoDB | Postgres native JSON columns สำหรับ `vehicle_counts`, `zones`, `phase_durations`, รองรับ concurrent writes จาก 6 background threads, Neon มี free tier พร้อม auto-scaling |
| **SQLAlchemy 2.x** | asyncpg, Django ORM | Sync API ตรงกับ thread-based design ของระบบ |
| **Leaflet + react-leaflet** | Mapbox GL, Google Maps | ฟรี ไม่ต้อง API key, OSM tile server ของตัวเองได้ถ้าจำเป็น, การวาด Polyline / Marker ง่ายกว่า Mapbox GL ใน use case นี้ |
| **Socket.IO (ยังไม่ใช้เต็มระบบ)** | Native WebSocket, SSE | รองรับ auto-reconnect ในตัว เตรียมไว้สำหรับ push event แบบ low-latency ในอนาคต (เช่น AI decision ที่ส่งออก) |
| **OpenCV + sumolib** | Pillow + ฟังก์ชันเอง | sumolib อ่าน `osm.net.xml` เป็น graph ได้ + OpenCV วาด top-down renderer ของ CCTV (`cctv_renderer.py`) พร้อมซ้อน YOLO bbox |

---

## 3. โครงสร้างไฟล์ (แยกตามบทบาท)

### 3.1 Root ของ repo

```
Pathumwan/
├── PLAN.md                     ← ไฟล์นี้
├── README.md                   ← วิธีเริ่มใช้งาน
├── DB_DIAGRAM.md               ← schema แบบ human-readable
├── DB_DIAGRAM.dbml             ← dbdiagram.io source
├── DB_TABLES_REFERENCE.md      ← schema + DDL + JSON schema + ตัวอย่าง query
├── RUNTIME_CAMERA_OPERATIONS.md← วิธี calibrate กล้อง / สร้าง zone polygon
├── implementation_plan.md      ← แผนงานย่อยของการ refactor
├── start.bat / stop.bat        ← เริ่ม/หยุด backend+frontend บน Windows
├── build.bat                   ← build Next.js (production)
├── osm.net.xml                 ← SUMO network (เขตปทุมวัน) จาก OSM
├── osm.sumocfg                 ← entry point ให้ SUMO โหลด network + routes
├── osm.*.rou.xml               ← routes (passenger/motorcycle/bus/truck)
├── osm.poly.xml                ← อาคาร/สวน เพื่อใช้ใน renderer
└── data/                       ← JSON seed + profile ข้อมูลฐาน
```

### 3.2 backend/ (Flask + SUMO + YOLO pipeline)

```
backend/
├── app.py                 ← entry point — bootstrap DB, ลงทะเบียน blueprints,
│                            start 6-7 background threads, รัน Flask
├── config.py              ← โหลด .env → Config class (DATABASE_URI, SUMO_NET_FILE,
│                            YOLO_MODEL_PATH, DETECTION_INTERVAL, INDEX_INTERVAL, ...)
├── simulation.py          ← TraCI loop 20Hz (sim_active, step, sim_lock),
│                            road_mapping (road_code → edge_ids),
│                            capture_cctv_frame(cam_id) → JPEG bytes
├── cctv.py                ← discover cameras จาก SUMO TLS junction + merge DB
├── cctv_renderer.py       ← top-down renderer (sumolib + OpenCV) ที่แปลง
│                            SUMO coords → pixel → JPEG พร้อม overlay bbox
├── utils.py               ← sumo_xy_to_latlng(), helpers
├── migrate_runtime_schema.py  ← additive schema migrator (เพิ่ม column ไม่ลบ)
├── check_tables.py        ← CLI ตรวจ DB ว่ามี table ครบไหม
├── detection/
│   ├── yolo_detector.py   ← ★ จุดเสียบ AI-Vision หลัก — wrap ultralytics YOLO,
│   │                         module-level singleton (get_shared_detector())
│   ├── detector_service.py← ★ pipeline loop — render frame → detect → save
│   │                         ลง traffic_detections ทุก DETECTION_INTERVAL วินาที
│   └── tracker_service.py ← (โครง) ByteTrack/DeepSORT — ยังไม่ integrate
├── ai/                    ← ★ RL framework (โครงอย่างเดียว ยังไม่เชื่อม)
│   ├── agent.py           ← policy / Q-net
│   ├── environment.py     ← state (density, signal, queue) → action (phase idx)
│   ├── reward.py          ← คำนวณ reward จาก throughput/wait
│   ├── trainer.py         ← training loop
│   └── pipeline.py        ← inference ที่ให้ signal_controller เรียก
├── services/
│   ├── traffic_index.py   ← สูตรดัชนีจราจร (0-10) — speed-based + VC ratio
│   ├── density.py         ← รวม SUMO + YOLO count → density per road
│   ├── signal_controller.py ← facade ทำ manual color / phase plan
│   │                          ผ่าน TraCI; มี SimSignalController + RealSignalController
│   ├── live_state.py      ← aggregated junction / road / camera snapshot
│   ├── mapping.py         ← catalog (camera/junction/approach/zone) + Thai name lookup
│   ├── aggregation.py     ← hourly vehicle counts → hourly_vehicle_counts
│   ├── daily_stats.py     ← daily historical_stats + retention policy (purge)
│   ├── camera_sync.py     ← sync JSON inventory ↔ DB
│   ├── camera_runtime.py  ← calibration + zone CRUD
│   ├── rtsp_ingest.py     ← (โครง) RTSP real-camera ingest
│   └── ai_pipeline.py     ← (โครง) bridge ai/pipeline → signal_controller
├── routes/
│   ├── auth.py            ← /api/auth/* (login, refresh, me, users)
│   ├── traffic.py         ← /api/vehicles, /api/traffic-index, /api/road-density,
│   │                         /api/traffic-lights, /api/roads/geometry
│   ├── cameras.py         ← /api/cameras/*, /<id>/stream, /<id>/frame, /<id>/counts
│   ├── admin.py           ← /api/admin/signal/*, /admin/ai-status,
│   │                         /admin/camera-runtime/*, /admin/users, /admin/logs
│   └── stats.py           ← /api/stats/* (realtime, index-today, yearly, daily, top10)
└── database/
    ├── connection.py      ← get_session() + engine factory (Neon/SQLite)
    ├── models.py          ← 16 SQLAlchemy models (ดูหัวข้อ 4)
    └── reference_data.py  ← ensure_road(), ensure_junction() upserts
```

### 3.3 frontend-next/ (Next.js 16 App Router)

```
frontend-next/
├── AGENTS.md / CLAUDE.md   ← rule: "This is NOT the Next.js you know"
├── package.json
├── next.config.ts
└── src/
    ├── app/
    │   ├── page.tsx            ← redirect → /login หรือ /dashboard
    │   ├── login/page.tsx
    │   ├── dashboard/page.tsx  ← แผนที่หลัก + badge ดัชนี + ค้นหา + popup stream
    │   ├── cameras/page.tsx    ← grid กล้อง (1/2/4/6/9) + MJPEG
    │   ├── control/page.tsx    ← signal control + AI toggle + phase editor
    │   ├── statistics/page.tsx ← realtime / index / yearly / daily / top10 / download
    │   ├── density/page.tsx    ← list ถนน + focus map on click
    │   └── admin/page.tsx      ← users + logs + camera calibration
    ├── components/
    │   ├── MapView.tsx         ← Leaflet map + markers + polyline overlay
    │   ├── CctvFeed.tsx        ← <img src=MJPEG> renderer with auto-reconnect
    │   ├── Navbar.tsx
    │   └── ProtectedRoute.tsx  ← client-side auth guard
    └── lib/
        ├── api.ts              ← Axios client + getCached() TTL layer
        ├── socket.ts           ← Socket.IO (ยังไม่ใช้เต็ม)
        └── types.ts            ← TS interfaces สำหรับทุก response
```

### 3.4 data/ (seed + profile)

- `pathumwan_roads.json` — 10 ถนน + 13 กล้อง seed + 13 แยกหลัก (id, name_th, sumo_tls_id, lat, lng, roads)
- `pathumwan_traffic_profile.json` — baseline `average_hourly_profile` (speed_avg, vc_avg ต่อชั่วโมง) ใช้ fallback เวลา SUMO ปิด
- `camera_runtime_config.generated.json` — export calibration + zones ล่าสุด (เกิดจาก `/admin/camera-runtime/export`)

---

## 4. Database (Neon PostgreSQL) — 16 Tables

ตารางทั้งหมดถูกนิยามใน `backend/database/models.py` รายละเอียด schema ดูได้ที่
[DB_TABLES_REFERENCE.md](DB_TABLES_REFERENCE.md) / [DB_DIAGRAM.md](DB_DIAGRAM.md)

### แบ่งตามบทบาท

| กลุ่ม | ตาราง | บทบาท |
|---|---|---|
| **Auth** | `users` | username/email/password_hash, role (user/admin) |
| **Reference** | `roads`, `junctions`, `approaches`, `cameras` | static/semi-static catalog |
| **Live detection (write-heavy)** | `traffic_detections` | YOLO count per camera ทุก 5s — retention 7 วัน |
| **Derived real-time** | `traffic_index`, `road_density` | ทุก INDEX_INTERVAL (30s default) — retention 30 วัน |
| **Signal control** | `signal_timings`, `signal_states`, `signal_controllers`, `ai_decisions` | เก็บ audit trail ของการเปลี่ยนไฟ + AI log — 30 วัน |
| **Audit/ops** | `system_logs`, `runtime_config` | bootstrap log, config snapshot — 30 วัน |
| **Aggregate (เก็บถาวร)** | `hourly_vehicle_counts`, `historical_stats` | เก็บ forever (ไม่ prune) |

Retention policy รันจาก `services/daily_stats.py::purge_old_raw_data()` ทุก 1 ชั่วโมง

---

## 5. Pipeline (ASCII diagram)

```
┌────────────┐     20Hz TraCI step      ┌─────────────────┐
│  SUMO Net  │ ─────────────────────▶   │ simulation.py   │
│ (osm.*.xml)│                          │  - sim_active   │
└────────────┘                          │  - camera_points│
                                        └────────┬────────┘
                                                 │ sim_lock
                      ┌──────────────────────────┼──────────────────────────┐
                      ▼                          ▼                          ▼
            ┌─────────────────┐       ┌─────────────────┐        ┌──────────────────┐
            │ camera capture  │       │ index calc loop │        │ signal apply loop│
            │ (~5-7 FPS × 55) │       │ (30s)           │        │ (2s) ★           │
            │  → cctv_renderer│       │ SUMO + detection│        │ ดึง signal_timings│
            │  → JPEG cache   │       │ → traffic_index │        │ + set_manual_color│
            └────┬────────────┘       │ → road_density  │        │ / set_phase_plan  │
                 │                    └────────┬────────┘        └──────────────────┘
                 │                             │
                 ▼ JPEG bytes                  ▼
       ┌─────────────────────┐       ┌────────────────────┐
       │ detector_service    │       │ /api/traffic-index │
       │ (5s)                │       │ /api/road-density  │
       │ YOLODetector.detect │       │ (source + freshness)│
       │ → traffic_detections│       └─────────┬──────────┘
       │  (vehicle_counts)   │                 │
       └────────┬────────────┘                 │
                │                              │
                ▼                              ▼
       ┌─────────────────────┐       ┌────────────────────┐
       │ aggregation (hourly)│       │   Next.js pages    │
       │ → hourly_vehicle_   │       │ dashboard/cameras/ │
       │   counts            │       │ control/statistics │
       └────────┬────────────┘       │ /density/admin     │
                ▼                    └────────────────────┘
       ┌─────────────────────┐
       │ daily_stats (hourly)│
       │ → historical_stats  │
       │ + purge_old_raw_data│ ← retention policy
       └─────────────────────┘
```

★ = thread ที่เพิ่มใหม่ในรอบล่าสุด (ก่อนหน้านี้ manual phase override ไม่ถูก apply ไป SUMO จริง)

---

## 6. API Endpoints (ทั้งหมด)

### Auth — `/api/auth/*`
| Method | Path | Body/Query | ใครใช้ |
|---|---|---|---|
| POST | `/api/auth/login` | `{username, password}` | หน้า login |
| POST | `/api/auth/register` | `{username, email, password}` | หน้า register |
| GET | `/api/auth/me` | — (header Bearer) | ทุกหน้าที่ต้องล็อกอิน |
| POST | `/api/auth/refresh` | — | axios interceptor |

### Traffic — `/api/*`
| Method | Path | คืนค่าสำคัญ | ใครใช้ |
|---|---|---|---|
| GET | `/api/vehicles` | array ของ vehicles (lat/lng/speed/type/color) | dashboard (เฉพาะ debug) |
| GET | `/api/traffic-index` | `{index, level, color, roads[], timestamp, source, freshness_seconds}` | dashboard badge, statistics, density |
| GET | `/api/road-density` | `{roads: [{road, road_id, density, speed, index, source, ...}]}` | density page |
| GET | `/api/traffic-lights` | array สัญญาณไฟปัจจุบัน | dashboard markers |
| GET | `/api/roads/geometry` | polyline ต่อถนน (cached) | density map polygon |
| GET | `/api/status` | SUMO step / active / error | health UI |

### Cameras — `/api/cameras/*`
| Method | Path | คืนค่าสำคัญ | ใครใช้ |
|---|---|---|---|
| GET | `/api/cameras/` | array กล้อง active (DB ∪ SUMO ∪ offline inventory) | dashboard, cameras, control, density ทั้งหมด |
| GET | `/api/cameras/<id>/frame` | JPEG 1 เฟรม | debug |
| GET | `/api/cameras/<id>/stream` | MJPEG | CctvFeed (ทุกหน้าที่มีกล้อง) |
| GET | `/api/cameras/<id>/detect/stream` | MJPEG + YOLO bbox | cameras page เมื่อเปิด detect mode |
| GET | `/api/cameras/<id>/vehicles` | รถใน radius ของกล้อง | (internal) |
| GET | `/api/cameras/<id>/counts` | vehicle_counts ล่าสุดจาก YOLO | CctvFeed HUD |
| GET | `/api/cameras/search?q=` | กล้องที่ match keyword | search bar |
| GET | `/api/cameras/<id>/detect` | JPEG + bbox (1 frame) | debug |

### Admin/Signal — `/api/admin/*`
| Method | Path | บทบาท | หมายเหตุ |
|---|---|---|---|
| GET | `/api/admin/ai-status` | สถานะ AI + sim active + mode | control page refresh |
| GET | `/api/admin/signal/mode` | อ่าน mode (ai/manual) | |
| POST | `/api/admin/signal/mode` | สลับ mode | |
| POST | `/api/admin/signal/manual` | set ไฟ red/yellow/green ชั่วคราว | ต้องรอ Signal Apply loop reassert |
| POST | `/api/admin/signal/phase` | set phase_durations[] | TraCI setProgramLogic() |
| GET | `/api/admin/signal/junctions` | แยกทั้งหมด + phase + camera match (filter ชื่อไทยแล้ว) | control page list |
| GET | `/api/admin/camera-runtime` | status กล้องทุกตัว | /admin page |
| GET | `/api/admin/camera-runtime/<id>` | calibration + zones | calibration editor |
| PATCH | `/api/admin/camera-runtime/<id>/calibration` | upsert calibration | editor save |
| PATCH | `/api/admin/camera-runtime/<id>/zones` | replace zones | editor save |
| GET | `/api/admin/camera-runtime/export` | dump calibration.json | backup |
| POST | `/api/admin/camera-runtime/import` | load calibration.json | restore |
| GET | `/api/admin/users` | user list | /admin page |
| GET | `/api/admin/logs` | system log (100 ล่าสุด) | /admin page |

### Stats — `/api/stats/*`
| Path | บทบาท |
|---|---|
| `/api/stats/realtime` | snapshot ปัจจุบัน (index + counts + speed) |
| `/api/stats/index-today` | 24 ชั่วโมงล่าสุด ต่อชั่วโมง |
| `/api/stats/yearly` | peak/avg index รายวันทั้งปี |
| `/api/stats/daily-count` | จำนวนรถรายวัน (จาก hourly_vehicle_counts) |
| `/api/stats/top-roads` | ท็อป 10 ถนน (ปี) |
| `/api/stats/hourly-counts` | hourly_vehicle_counts (ดิบ) |
| `/api/stats/road-history/<road>` | กราฟย้อนหลังถนนเดียว |

---

## 7. หน้าเว็บใช้ API ตัวไหน

| หน้า | API ที่เรียก | refresh |
|---|---|---|
| `/dashboard` | `/api/cameras/`, `/api/vehicles`, `/api/traffic-lights`, `/api/traffic-index`, `/api/roads/geometry` | ทุก 3-30s |
| `/cameras` | `/api/cameras/` + `/api/cameras/<id>/stream` (MJPEG) | 1 ครั้งตอน mount |
| `/control` | `/api/admin/ai-status`, `/api/admin/signal/junctions`, `/api/cameras/` + POST `/api/admin/signal/*` | ทุก 5s |
| `/statistics` | `/api/stats/*` ตาม tab | ทุก 5-60s |
| `/density` | `/api/road-density`, `/api/roads/geometry`, `/api/cameras/` | ทุก 5s |
| `/admin` | `/api/admin/users`, `/api/admin/logs`, `/api/admin/camera-runtime` | manual refresh |

---

## 8. ★ ถ้าจะทำส่วน AI ต่อ ต้องทำอะไร (อ่านตรงนี้)

### 8.1 ภาพรวม

ระบบมี "จุดเสียบ AI" 2 จุดใหญ่ ๆ

1. **AI-Vision (YOLO detection)** — เปลี่ยน/ปรับโมเดลตรวจจับรถ
2. **AI-Control (RL signal control)** — ให้ agent ตัดสินใจเลือก phase ของสัญญาณไฟ

ทั้งสองจุดเข้ามาแทรกใน pipeline ที่มีอยู่แล้ว โดยไม่ต้องแก้ route / DB / frontend

### 8.2 ไฟล์หลักที่ต้องแก้

| เป้าหมาย | ไฟล์ | สิ่งที่ต้องทำ |
|---|---|---|
| เปลี่ยน YOLO weight / backbone | `backend/detection/yolo_detector.py` | แก้ `_load_model()`; ใช้ `Config.YOLO_MODEL_PATH` (default `yolo12n.pt`) |
| เพิ่มคลาส (เช่น tuk-tuk, van) | `backend/detection/yolo_detector.py` | แก้ `_CLASS_MAP` + add key ใน `counts` schema ของ `traffic_detections.vehicle_counts` |
| ใช้ ONNX / TensorRT | `backend/detection/yolo_detector.py` | แทนที่ `ultralytics.YOLO` ด้วย `onnxruntime` / `tensorrt` — ต้องคง `detect(frame_bytes) -> list[dict]` interface เดิม |
| เพิ่ม tracking (ByteTrack/DeepSORT) | `backend/detection/tracker_service.py` (โครงมีอยู่) | implement `start_tracker_service_loop()` + เขียนไปยัง `traffic_detections` (เพิ่ม track_id) — อาจต้องเพิ่ม column ใน model ด้วย |
| RL agent | `backend/ai/agent.py` + `environment.py` + `reward.py` | implement ตาม gym-like interface |
| Inference pipeline | `backend/ai/pipeline.py` | ทำ `predict(state) -> action` ที่ signal_controller เรียกใช้ได้ |
| Glue กับ signal control | `backend/services/signal_controller.py::apply_ai_actions()` | มีอยู่แล้ว — แค่เรียกจาก ai pipeline |

### 8.3 Dataflow ของ AI

```
  traffic_detections (YOLO counts ต่อกล้อง/5s)
              +
  signal_states (current_phase, duration)
              +
  road_density (density, speed ต่อถนน)
                     │
                     ▼
              [State Vector]
                     │
                     ▼
     backend/ai/pipeline.py::predict()
                     │
                     ▼
              [Action: target_phase / phase_durations]
                     │
                     ▼
  signal_controller.apply_ai_actions()
  → TraCI setPhase() / setProgramLogic()
                     │
                     ▼
  บันทึก ai_decisions (input_data, action, reward)
```

### 8.4 Signal Apply Loop (thread ใหม่ที่เพิ่งเพิ่ม)

- `app.py::_start_signal_apply_loop()` ดึง latest `SignalTiming` ต่อ junction ทุก 2 วินาที
- ถ้า `mode=manual` + payload เป็น `[{state: "red"}]` → reassert `setRedYellowGreenState` ทุก tick
  (ไม่เช่นนั้น SUMO program ปกติจะเขียนทับภายใน 1 step)
- ถ้า `mode=manual` + payload เป็น `[{index:0, duration:30}, ...]` → apply 1 ครั้ง (TraCI program logic persistent)
- ถ้า `mode=ai` → ให้ `apply_ai_actions` จัดการ (loop นี้ไม่แตะ)

### 8.5 คำถามที่คนทำ AI ถามบ่อย

**Q: YOLO รู้ทิศทาง / ลำดับก่อนหลังของรถไหม?**
A: ไม่รู้โดย default — `traffic_detections.vehicle_counts` เก็บแค่ `{car, motorcycle, bus, truck, total}`
ถ้าต้องการ:
- ทิศทาง (approach) — ต้องกำหนด `cameras.zones` (polygon ต่อ approach) ผ่าน `/admin/camera-runtime`
  แล้ว map bbox center → zone → approach_id (schema อยู่แล้วใน `Camera.zones` JSON column)
- ลำดับก่อนหลัง / wait time / queue length — ต้องเพิ่ม tracking (ByteTrack) ใน `tracker_service.py`
  เพื่อได้ track_id + first_seen_at + last_seen_at

**Q: reward มาจากไหน?**
A: ตัวเลือกที่มีข้อมูลพร้อม:
- `traffic_index` area value → reward = -index (ลดดัชนี = ดี)
- `road_density.avg_speed` → reward = +speed
- ถ้ามี tracking → queue length หรือ wait time → reward = -wait

**Q: train offline ได้ไหม?**
A: ได้ — dump `traffic_detections` + `signal_states` + `road_density` ออกเป็น parquet
แล้ว replay ผ่าน `environment.py::step(action)` แบบ historical — ส่วนนี้ยังไม่ implement

**Q: ถ้าจะเปลี่ยน TLS (SUMO traffic light) ให้เป็นของเราเองล่ะ?**
A: เพิ่ม row ใน `signal_controllers` table (controller_type, endpoint, phase_map) แล้วเขียน
adapter ใหม่ใน `signal_controller.py` (ให้ inherit จาก pattern เดียวกับ `SimSignalController`/`RealSignalController`)

### 8.6 จุดที่ยังไม่ได้ทำ (known limitations)

- `backend/ai/*` เป็นโครงเปล่า — ยังไม่มี model weight / training data / reward function จริง
- Tracking (`tracker_service.py`) ยังไม่ integrate — ถ้า RL ต้องการ queue/wait metric ต้องทำเพิ่ม
- WebRTC/HLS ยังไม่รองรับ — stream ปัจจุบันเป็น MJPEG (`<img>` tag) — เหมาะกับ dev แต่ไม่เหมาะ scale
- `services/rtsp_ingest.py` มีโครง แต่ยังไม่ทดสอบกับกล้อง กทม./ทล. จริง
- `cameras.zones` มีไว้แล้วแต่ยังไม่มี polygon pre-calibrated ทั้ง 55 กล้อง (ต้องทำผ่าน `/admin/camera-runtime`)
- Socket.IO endpoint (`lib/socket.ts`) ยังใช้แค่ handshake — push event ยังไม่ได้ wire

---

## 9. วิธีรันโปรเจคแบบ local

### 9.1 Prerequisites
- Python 3.11+ (SUMO + ultralytics รองรับ 3.10-3.12)
- SUMO 1.20+ ติดตั้งที่ `C:\Program Files (x86)\Eclipse\Sumo\` (หรือ `$SUMO_HOME`)
- Node.js 20+
- Neon PostgreSQL connection string (หรือใช้ SQLite fallback)

### 9.2 ขั้นตอน (Windows)

```bash
# 1. Backend
cd Pathumwan/backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# 2. .env (copy จาก .env.example)
# ตั้ง DATABASE_URI, SYSTEM_MODE=sim, CAMERA_BACKEND=sumo

# 3. Migrate schema (additive, ไม่ลบอะไร)
python migrate_runtime_schema.py

# 4. Run backend (จะเริ่ม SUMO + YOLO + 6-7 background threads)
python app.py

# 5. Frontend (อีก terminal)
cd Pathumwan/frontend-next
npm install
npm run dev   # dev = localhost:3000

# หรือใช้ shortcut:
#   start.bat    → รัน backend + frontend พร้อมกัน
#   stop.bat     → ฆ่า process ทั้งหมด
```

### 9.3 ตรวจสุขภาพ

```
GET http://localhost:5000/api/health
```

ต้องเห็น `"simulation_active": true` + `"camera_count": 55`

---

## 10. Environment Variables (ต้องตั้ง)

| ตัวแปร | Default | ทำอะไร |
|---|---|---|
| `DATABASE_URI` | `sqlite:///traffix.db` | Postgres connection string ของ Neon |
| `NEXT_PUBLIC_API_URL` | `http://localhost:5000/api` | frontend Axios base |
| `SYSTEM_MODE` | `sim` | `sim` = ใช้ SUMO / `real` = ใช้ RTSP |
| `CAMERA_BACKEND` | `sumo` | `sumo` / `rtsp` |
| `SIGNAL_BACKEND` | `sim` | `sim` = TraCI / `controller` = external |
| `AI_BACKEND` | `mock` | `mock` / `torch` / `onnx` |
| `YOLO_MODEL_PATH` | `yolo12n.pt` | path ไปยัง weight |
| `DETECTION_INTERVAL` | `5` | วินาที — YOLO run ต่อรอบ |
| `INDEX_INTERVAL` | `30` | วินาที — density + index calc |
| `STALE_THRESHOLD_SECONDS` | `30` | ถือว่ากล้อง offline เมื่อ freshness เกินค่านี้ |
| `FLASK_HOST` / `FLASK_PORT` | `0.0.0.0` / `5000` | |
| `SECRET_KEY` | (required) | JWT signing |
| `SUMO_NET_FILE` | `../osm.net.xml` | path network |
| `SUMO_CONFIG` | `../osm.sumocfg` | path sumocfg |

---

## 11. คำแนะนำการทำงานต่อ

1. ก่อนแตะ AI code — ลองรัน backend + frontend ให้ผ่าน, เปิด `/control` ทดสอบ set manual signal
   แล้วเปิด SUMO GUI (`osm.sumocfg`) ดู TLS เปลี่ยนจริง ถ้าเห็น = Signal Apply loop ทำงาน
2. ทดลอง YOLO detection — เปิด `/cameras` page toggle "detect" on เห็น bbox แดงบน stream
3. Baseline data — รัน backend ทิ้งไว้อย่างน้อย 30 นาที, query `SELECT COUNT(*) FROM traffic_detections;`
   ควรจะเห็นหลายพัน row ใช้เป็น dataset เริ่มต้น
4. เริ่ม RL — implement `environment.py::reset()/step()` ให้ pull state จาก `services/live_state.py` ได้
5. อย่าลืม retention — ถ้า train นาน ๆ traffic_detections โตเร็ว แต่ถูก purge ทุก 7 วันอัตโนมัติ
   (ถ้าต้อง keep เพื่อ training → export ออกเป็น parquet ก่อนถึง cutoff)

---

## 12. เอกสารอ้างอิงอื่น

- ปัญหา calibration / zone ดู [RUNTIME_CAMERA_OPERATIONS.md](RUNTIME_CAMERA_OPERATIONS.md)
- schema ละเอียด ดู [DB_TABLES_REFERENCE.md](DB_TABLES_REFERENCE.md)
- ER diagram ดู [DB_DIAGRAM.md](DB_DIAGRAM.md)
- Code structure diff ดู [implementation_plan.md](implementation_plan.md)

---

## 13. FAQ — ปัญหา/คำถามจาก operations (2026-04-21)

### 13.1 ทำไม `index = 0.0` ขึ้นทั้ง 10 ถนน?

**สาเหตุเป็น chain failure ของ data freshness:**

1. `detector_service.py` เดิมจะ **save เฉพาะเมื่อค่า count เปลี่ยน** (line 81-82 เดิม)
   → เวลาจริงนาทีที่รถนิ่ง/ถนนว่าง count ไม่เปลี่ยน → ไม่ insert row ใหม่ →
   `traffic_detections.timestamp` แก่ขึ้นเรื่อย ๆ
2. `live_state.get_latest_approach_metrics()` กรอง detection ที่ freshness เกิน
   `Config.STALE_THRESHOLD_SECONDS` ทิ้ง (default เดิม **10s** น้อยเกิน)
   → detection ทั้งหมดถูกทิ้ง → `get_latest_road_state()` คืน `[]`
3. `_build_live_index_response()` เจอ empty → fallback ไป `_build_detection_road_data()` → ก็เจอ empty
   → fallback ต่อไป SUMO live path
4. SUMO ถ้ายังไม่เริ่ม / sim ไม่มีรถ (routes หมด / เพิ่ง start) → `vehicle_count=0` ทุกถนน
5. `calculate_road_index(avg_speed=ffs, vc=0, count=0)` → `speed_ratio=1` → **index = 0** ทุกถนน

**fix ที่ทำไปแล้ว (2026-04-21):**
- `backend/detection/detector_service.py` เพิ่ม **heartbeat save**: ถึงแม้ count ไม่เปลี่ยน
  ก็จะ insert row ใหม่ทุก `0.75 * STALE_THRESHOLD_SECONDS` เพื่อให้ freshness ไม่โต
- `backend/config.py` ขึ้น `STALE_THRESHOLD_SECONDS` จาก `10` → `60` (env ยัง override ได้)
- ผลที่คาดหวัง: ดัชนีรายถนนจะสะท้อน detection จริงแม้รถนิ่ง, ไม่ fallback 0 อัตโนมัติ

> **หมายเหตุ:** ถ้ารถใน SUMO เบาบางจริง ๆ (จน YOLO มองไม่เห็นเลย) ก็ถูกต้องตาม logic
> ที่จะเป็น 0 — ดูข้อ 13.2 ถ้าจะแก้ตรงนี้

### 13.2 YOLO ตรวจจับได้น้อยทั้งที่ถนนจริงติด

**root cause:** field-of-view (FOV) ของ `cctv_renderer` อยู่ที่ประมาณ 28 m เส้นผ่านศูนย์กลาง
(`merge_detection_floor` ใช้ `camera_fov_km = 0.028`). กล้องจริงในเมืองครอบคลุมหลัก 100+ m → YOLO ของเรา
จึงเห็นรถได้เพียงหยิบมือเดียว ไม่สะท้อนภาพรวมถนน.

**แนวทางแก้ (เรียงตามเร็ว → ดีที่สุด):**
1. **ขยาย FOV ของ renderer** — แก้ `CCTV_ZOOM_PRESETS` ใน `backend/config.py`
   ให้ใช้ `"wide"` (18000 scale) เป็น default แทน `"near"`; trade-off: วัตถุเล็กลง YOLO อาจ miss
2. **รวมผล detection จากกล้องหลายตัวบนถนนเดียวกัน** — ตอนนี้ `get_detection_counts_by_road()`
   ใช้ **max** ต่อถนน (เพื่อกัน double-count); เปลี่ยนเป็น **weighted sum** ตาม lane coverage
   จะสะท้อนความจริงกว่า
3. **Scale counts ด้วยสัดส่วน FOV:camera_radius:road_length** — ใน `merge_detection_floor`
   มี formula อยู่แล้ว แต่ต้อง tune `lane_factor` + `capacity_per_km` ต่อถนน
4. **ใช้ tracking (ByteTrack)** ไม่ใช่ single-frame detection — รถเดียวจะถูกนับครั้งเดียว
   ระหว่างที่อยู่ใน FOV → ดูข้อ 13.9

### 13.3 กล้องบางตัวบน `/cameras` ขึ้นว่างเปล่า / ไม่พบ

**เดิม:** `CctvFeed` ไม่มี error handling เลย — `<img>` โหลด fail แสดงเป็น broken-image icon
ของ browser เงียบ ๆ; frontend ไม่รู้ว่ากล้องนั้นพัง.

**fix (2026-04-21):** `frontend-next/src/components/CctvFeed.tsx` เพิ่ม `onError` handler
ที่แสดง overlay **"ไม่พบสตรีมของกล้องนี้"** พร้อมปุ่ม **"ลองใหม่"** — ทำให้เห็นชัดเจนว่ากล้องใด offline
แล้วคงไม่สับสนกับคำว่า "cannot found" ใน log (มาจาก HTTP 404 ของ MJPEG endpoint).

**สาเหตุที่อยู่เบื้องหลัง 404:**
- กล้อง TFFxx อยู่ใน `pathumwan_roads.json` แต่ไม่ match กับ TLS junction จริงใน `osm.net.xml`
- `simulation.camera_points` เกิดจาก `cctv.collect_cameras()` ที่ match ด้วย geoloc radius
  → ถ้ากล้องไม่ match junction, ไม่ถูก seed เข้า `simulation.camera_points`
  → `/api/cameras/<id>/stream` 404
- **แก้เพิ่มเติมแนะนำ:** เปิด `/admin/camera-runtime` → calibrate ให้ทุกกล้อง (ดู RUNTIME_CAMERA_OPERATIONS.md)
  หรือ mark กล้องเป็น `"status": "inactive"` ใน `pathumwan_roads.json` ถ้าไม่ใช้จริง

### 13.4 สตรีมกล้องขึ้นช้าในหน้าหลัก / บางครั้งไม่ขึ้น

- `/dashboard` โหลด `<img>` ของ MJPEG แบบ **lazy ตาม viewport**; browser แต่ละตัว
  คุม `max-connections-per-host` ได้ 6 concurrent — ถ้ามีกล้อง 13+ ตัว, เปิดพร้อมกันจะมี queue
- Backend `/api/cameras/<id>/stream` generator ต้อง `simulation.sim_lock` → ถ้า detector,
  signal-apply, aggregation loop แย่ง lock กัน, FPS ลดลง
- **แนะนำ:**
  - จำกัดจำนวนกล้องที่แสดงพร้อมกัน (default `grid = 4` แทนที่จะ 6 หรือ 9)
  - ขยาย connection pool/ใช้ HTTP/2 ที่ Flask-level (ยังไม่ได้ทำ)
  - ระยะยาว: ย้ายไป WebRTC/HLS (ดู known limitations ข้อ 8.6)

### 13.5 หน้าสถิติ & ความหนาแน่นค่าคงที่ (fallback)

**root cause เดียวกับ 13.1** — เมื่อ detection data เก่า, endpoint fallback เป็น `db-cache` / `detection-fallback`
แล้ว level ของทุกถนนกลายเป็น "คล่องตัว" ทั้งหมด ซึ่งดูเหมือน "ค่าคงที่".

หลังใส่ heartbeat + ขยาย STALE_THRESHOLD แล้วค่าจะ refresh ต่อเนื่อง (ตรวจที่ field `source`:
`sumo-live` / `live-state` = real, `db-cache` = stale).

**ดูที่ UI:** `density/page.tsx` แสดง label `sourceLabel(r.source)` อยู่แล้ว — ถ้าเห็น "DB fallback"
หรือ "unknown" = detection loop ไม่ทำงาน, ถ้าเห็น "Live state" หรือ "SUMO live" = ถูกต้อง

### 13.6 "จำนวนรถแต่ละถนนแต่ละชั่วโมง" เวลา 00:00

ใน `frontend-next/src/app/statistics/page.tsx` column "เวลา" render จาก `r.hour` ที่เป็น string
แบบ `"HH:00"`. ถ้า backend ส่ง `hour = ""` / missing → UI แสดง empty/placeholder ไม่ใช่ 00:00.
ที่เจอว่า "00:00" เพราะ `aggregation.py` เขียน `hour = "00:00"` เป็น default เมื่อยังไม่จบชั่วโมง —
แก้โดยดู `backend/services/aggregation.py::aggregate_hourly_counts()` ให้ใช้ `datetime.now().strftime("%H:00")`
แทน fixed string.

### 13.7 หน้า control ทำไมเฟสเป็นแดงหมด + manual/AI ทำอะไร

**ทำไมแดงหมดตอนแรก:**
- TLS program ของ SUMO (Pathumwan 90s cycle) ณ วินาทีแรกของรอบส่วนใหญ่เป็น **all-red clearance**
  (ทุก lane เป็น 'r') — เป็น phase สั้น 2 วินาที แต่ถ้า frontend poll ณ วินาทีนั้นพอดี, ทุกแยกแสดงแดง
- ฟังก์ชัน `_dominant_light_state()` ใน `backend/routes/traffic.py:255-267` fallback เป็น "red"
  ถ้าไม่เจอ 'g'/'y' ใน state → ใน all-red phase ก็ได้ "red" ถูกต้อง

**วิธีปรับเหลือง/เขียว:**
- หน้า `/control` เลือก junction → กดปุ่ม **เหลือง / เขียว** ใต้ card
- Frontend เรียก `POST /api/admin/signal/manual` body `{junction_id, color: "yellow"|"green"}`
- Backend (`routes/admin.py:80-127`) เรียก `controller.set_manual_color()` ซึ่ง apply
  ผ่าน `traci.trafficlight.setRedYellowGreenState()` + บันทึก `SignalTiming(mode="manual")`
- **Signal apply loop** (`app.py::_start_signal_apply_loop`, every 2s) อ่าน `SignalTiming` ล่าสุด
  ต่อ junction แล้ว **reassert color ซ้ำ** ทุก 2s (กัน SUMO program เขียนทับ) — TTL 10 นาที

**ถ้าสลับเป็น AI mode:**
- Frontend เรียก `POST /api/admin/signal/mode {mode: "ai"}`
- Signal apply loop จะไม่ reassert manual override ต่อ → SUMO กลับไปเดิน program ปกติ
  (ยังไม่มี AI agent จริง — `backend/ai/*` เป็นโครงเปล่า)
- **ใช่, SUMO follow traffic rules อยู่แล้วตาม TLS program** (all-red clearance, yellow transition)
  — AI agent ตอนนี้ถ้ามีจริงจะเพียงเลือก phase index/duration ให้, ไม่ได้ควบคุม traffic rules ระดับแยก

### 13.8 ถ้า AI ของเราทำงาน มันจะปฏิบัติตามกฎจราจรใน SUMO ไหม?

**ใช่ครับ — โดย default.** SUMO มี rules ของตัวเองที่ hardcode ใน network:
- **Right-of-way** ต่อ lane (อ่านจาก `osm.net.xml` connections)
- **TLS program** บังคับ phase sequencing ต่อ junction
- **Junction internal links** คุม vehicle-to-vehicle priority ใน conflict area
- **No right-turn-on-red** / **yield to pedestrian** — ขึ้นกับการ tag ใน OSM

AI agent ของเราเลือกได้แค่:
- `target_phase` (index ใน logic.phases)
- `phase_durations[]` (modify min/max/duration)

**AI ไม่สามารถสั่งให้รถฝ่าไฟแดง** — เพราะ TraCI phase ที่ให้มันเลือกต้องมาจาก `Logic.phases`
ที่ SUMO validate แล้ว (ไม่มี phase ที่ conflict พร้อมกัน); agent เลือก phase ไหน vehicles ก็ไปตาม
right-of-way ของ lane นั้น.

### 13.9 YOLO รู้ทิศทางรถ / ลำดับก่อนหลังไหม? (ต่อจาก 8.5)

**ยังไม่รู้ — แต่มีทางเสริม:**

1. **ทิศทาง (approach_id)** — `cameras.zones` column มีอยู่แล้วใน Postgres;
   schema เป็น JSON array ของ `{approach_id, polygon: [[x,y],...]}`
   - ใน `/admin/camera-runtime` UI มีเครื่องมือวาด polygon แต่ ณ ตอนนี้ **ยังไม่ได้ calibrate 55 กล้อง**
   - ต้องเขียน logic ใน `detector_service.py` เพิ่ม: หา bbox center → check polygon → tag counts ไปที่ approach
   - เมื่อ calibrate แล้ว, endpoint `/api/admin/camera-runtime/<id>/zones` จะบันทึกลง `cameras.zones`
2. **ลำดับก่อนหลัง / wait time / queue length** — ต้อง **tracking** เพราะ single-frame detect ไม่รู้ "คันนี้รอ"
   - `backend/detection/tracker_service.py` มีโครง (module-level, ByteTrack-ready)
   - ต้อง implement `start_tracker_service_loop()` ที่:
     - รัน tracker ต่อกล้อง
     - update `track.first_seen_at`, `last_seen_at` ใน memory
     - คำนวณ queue_length (tracks ที่ speed < threshold อยู่ใน polygon)
   - เปิดใช้โดย `live_state.get_latest_approach_metrics()` เรียก `tracker_service.get_runtime_approach_metrics()`
     (ซึ่ง hook มีอยู่แล้ว line 78-82)

### 13.10 Manual mode ปรับเวลาไฟได้จริงไหม?

**ได้ — แต่ดูแค่ระดับ junction, ไม่ใช่ระดับกล้อง.** ตอนนี้ flow คือ:

1. `/control` → กด "ตั้งค่าเฟส" → frontend แสดง phase editor ของ junction ที่เลือก
2. POST `/api/admin/signal/phase` body `{junction_id, phases: [{index:0, duration:30}, {index:1, duration:3}, ...]}`
3. Backend เรียก `controller.set_phase_plan()` ซึ่ง update `Logic.phases[i].duration` ผ่าน TraCI
4. เก็บลง `SignalTiming(mode="manual", phases=[...])`; Signal apply loop apply 1 ครั้ง
5. TraCI program จะคงค่าไว้จนกว่า mode = ai หรือ restart sim

**ข้อจำกัดที่ต้องรู้:**
- กล้องและ junction ไม่ได้ 1:1 ทุกกรณี — บาง junction มีหลายกล้อง (1 ต่อ approach),
  การปรับเฟสจึงมีผลกับ junction นั้นทั้งแยก ไม่ใช่ต่อ approach
- TTL 10 นาที: ถ้า user ไม่ยืนยันค่าใหม่ภายใน 10 นาที signal apply loop จะไม่ reassert
  (เพราะ row เก่า) — SUMO คืนสู่ program default

### 13.11 SUMO GUI — อยากเห็นหน้าต่างจำลอง

**เพิ่ม env flag ใหม่ `SUMO_GUI=1` (2026-04-21):**
- `backend/config.py::Config.SUMO_GUI` → parse `1/true/yes/on`
- `backend/simulation.py::get_sumo_command()` → เลือก `find_sumo_gui()` แทน `find_sumo_binary()` เมื่อเปิด
- วิธีใช้: ตั้ง `SUMO_GUI=1` ใน `backend/.env` หรือ `set SUMO_GUI=1` ก่อนรัน `python app.py`
- หน้าต่าง sumo-gui จะเปิดมาพร้อม network; กด **Play** (▶) เพื่อเริ่ม step (TraCI จะ control)
- ดูเพิ่ม [README.md](README.md) section "รัน SUMO แบบเห็นหน้าต่าง"

### 13.12 Checklist ปัญหาคงเหลือ (ต้องทำต่อ)

- [ ] ตรวจ `aggregation.py` เรื่อง `hour = "00:00"` → ใช้ชั่วโมงจริง (ยืนยันแล้วว่าใช้ `datetime.now().strftime("%H:00")` ถูกต้อง)
- [ ] Calibrate `cameras.zones` ทั้ง 55 กล้องผ่าน `/admin/camera-runtime`
- [ ] Implement `tracker_service.start_tracker_service_loop()` (โค้ดมีอยู่แล้วแต่ทำงานเฉพาะ `CAMERA_BACKEND=rtsp` หรือ `SYSTEM_MODE=real`)
- [ ] ขยาย CCTV FOV preset หรือทำ multi-zoom capture เพื่อให้ YOLO เห็นมากกว่า 28 m
- [ ] ทำ real RL agent ใน `backend/ai/pipeline.py::predict()` (ดู section 15 ด้านล่างสำหรับ API contract)
- [ ] WebRTC/HLS สำหรับ scale stream เกิน 10 กล้อง

---

## 14) รอบแก้บัครอบสอง (2026-04-21)

รอบก่อนยังแก้ไม่ครบ ผู้ใช้รายงานว่า: ความเร็วถนนติดค่าคงที่ 50, ระดับความหนาแน่นโชว์ "คล่องตัว" ตลอด, มี ghost camera ที่ (13.7471, 100.4996), `⚠ ultralytics not installed` ขึ้นเงียบ ๆ และหน้าความหนาแน่นคลิกถนนแล้ว zoom ผิดตำแหน่ง. ทั้งหมดแก้แล้วในรอบนี้.

### 14.1 Root cause: โค้ดเคย "แต่งข้อมูล" แทนที่จะยอมรับว่าไม่มีข้อมูล

**บั๊กหลัก** (เส้นทางที่ทำให้ทุกถนนโชว์ `speed=50 / index=0 / level="คล่องตัว"` ทั้งที่ไม่มีข้อมูลจริง):

1. `services/density.py::compute_density_from_sumo` คืน `avg_speed = float(ffs)` เมื่อ `speed_samples == 0`
2. `_build_road_data_from_detections` (ใน `app.py`) คืน `avg_speed = ffs` เมื่อ YOLO count = 0
3. `_index_calculation_loop` empty-state คืน `speed_avg = ffs` ถ้า profile ไม่มีค่า
4. `traffic_index.calculate_road_index(50, 50, ...)` → `speed_ratio = 1.0` → index = 0.0 → level = "คล่องตัว"

**การแก้ (2026-04-21):**
- `density.py` บรรทัด 106: เลิกคืน FFS ตอนไม่มี sample. เพิ่ม field `has_speed_data: bool` ส่งต่อลงทาง pipeline
- `density.py::merge_detection_floor`: เคารพ `has_speed_data` แทนเช็ค `current_speed >= ffs * 0.99`
- `traffic_index.calculate_road_index`: รับ `has_speed_data` และคืน `None` เมื่อไม่มีข้อมูลจริง
- `get_congestion_level(None)` → `"ไม่มีข้อมูล"`; `get_congestion_color(None)` → `#9E9E9E` (เทา)
- `calculate_area_index`: road ที่เป็น no-data ถูกเว้นจาก weighted average (ไม่ดึง index ลง 0 ผิดพลาด)
- ทุก route ใน `routes/traffic.py` ส่งต่อ `level` / `has_data` ให้ frontend
- `frontend-next/src/app/density/page.tsx`: badge "ไม่มีข้อมูล" (สีเทา), index แสดง "—" แทน "0.0" เมื่อ `has_data === false`

**ผลที่คาดหวัง:**
- ถ้า SUMO ยังไม่มีรถ → UI โชว์ "ไม่มีข้อมูล" (ไม่ใช่ "คล่องตัว ความเร็ว 50")
- เมื่อมีรถ + YOLO detect ได้ → UI อัปเดตตามจริง
- Area index ไม่ถูกแปดเปื้อนจาก road ที่ไม่มีข้อมูล

### 14.2 Ghost camera ที่ (13.7471, 100.4996)

**สาเหตุ:** OSM extract (`osm.net.xml` มี 55 TLS) ครอบคลุมพื้นที่กว้างกว่าเขตปทุมวัน — TLS ที่ OSM node `13167186221` (lat=13.7471329, lng=100.4996122) อยู่ใน Samyot/Pom Prap ฝั่งตะวันตก ไม่ใช่ปทุมวัน แต่ `cctv.collect_cameras()` และ `camera_sync._parse_network_tls_points()` ไม่กรอง bbox จึง sync เข้า DB

**การแก้ (2026-04-21):**
- `backend/cctv.py`: เพิ่ม constant `PATHUMWAN_BBOX` (lat 13.720-13.760, lng 100.510-100.555) + helper `is_in_pathumwan(lat, lng)`
- `cctv.collect_cameras()`: skip TLS ที่อยู่นอก bbox
- `services/camera_sync.py::_parse_network_tls_points`: skip TLS ที่อยู่นอก bbox ก่อนเพิ่มเข้า DB
- `services/camera_sync.py::_purge_out_of_bbox_cameras()` (ฟังก์ชันใหม่): mark row เก่าที่อยู่นอก bbox เป็น `status='inactive'` อัตโนมัติตอน `repair_camera_inventory_if_needed()`
- `routes/cameras.py::api_cameras`: filter `is_in_pathumwan` อีกชั้นก่อน return ให้ frontend (defensive)

หากต้องการลบแบบถาวร รัน SQL:
```sql
DELETE FROM cameras WHERE lat NOT BETWEEN 13.720 AND 13.760 OR lng NOT BETWEEN 100.510 AND 100.555;
```

### 14.3 Ultralytics ไม่ได้ติดตั้ง — เงียบเกินไป

`detection/yolo_detector.py::YOLODetector._load_model` เดิม print แค่หนึ่งบรรทัด `⚠ ultralytics not installed` แล้ว set `self.model = None` → ทุกกล้อง detect ได้ 0 รถ, `confidence_avg=0` ไม่มีใครรู้ว่าพัง

**การแก้ (2026-04-21):**
- เพิ่ม module-level `YOLO_STATUS: dict` เก็บ `{available, reason, model_path}`
- `_load_model` update `YOLO_STATUS` ทุก branch + print banner (68 ตัวอักษร 3 บรรทัด) เมื่อ ImportError
- `app.py::/api/health` return field `yolo: {available, reason, model_path}` ให้ frontend / admin ตรวจได้

**วิธีตรวจ:**
```bash
curl http://localhost:5000/api/health | jq .yolo
```

### 14.4 หน้า Density คลิกถนนแล้ว zoom ผิดที่

**สาเหตุ:** `getRoadGeometries()` request แยกจาก `getDensity()` — ถ้าผู้ใช้คลิกถนนก่อน geometry โหลดเสร็จ `geomMap` ว่าง → fallback หา camera ด้วย `camera.name.includes(r.road)` ซึ่ง match substring ไปเจอกล้องคนละถนนได้

**การแก้ (2026-04-21):** `frontend-next/src/app/density/page.tsx`
- ถ้า `geom?.bbox` ว่าง → fit bounds รอบ **ทุกกล้อง** บนถนนนั้นที่ match `road_id` แบบ strict (ไม่ใช้ substring match)
- ถ้ายังไม่มีกล้อง matching → ไม่ zoom (ดีกว่า zoom ผิด)

### 14.5 ประเมินเครื่องมือ DB / backend — ควรเปลี่ยนไหม?

**สถานะปัจจุบัน:** SQLAlchemy 2.x + (Postgres production / SQLite dev) + REST polling ทุก 5-30 วินาทีจาก frontend

**ประเมิน:** ปัญหาที่ผู้ใช้เจอ **ไม่ได้เกิดจาก DB layer**:
- สเกลเล็กมาก (10 ถนน, 55 กล้อง, 16 ตาราง) — SQLite/Postgres เอาอยู่สบาย ๆ
- Write load ประมาณ 55 × 1 det/ms + 1 area_idx/5s + 1 road_density/5s = ~11 writes/sec สูงสุด
- Read load ~1 req/5s ต่อ client — ไม่กดดันเลย
- latency ที่เห็น (ข้อมูลไม่อัปเดต) เกิดจาก logic bug (ฟ้องไปแล้วข้างบน 14.1) ไม่ใช่ DB bottleneck

**ทำไมไม่แนะนำ RxDB:**
- RxDB เป็น frontend reactive DB + local sync — แก้คนละปัญหา
- โครงการปัจจุบันต้องการ "frontend เห็นข้อมูลล่าสุด" ซึ่งแก้ง่ายกว่าด้วย **polling ที่เร็วขึ้น** หรือ **SSE/WebSocket** (ไม่ต้องย้าย DB)
- RxDB จะเพิ่ม sync layer (GraphQL/CouchDB) — เพิ่มความซับซ้อนโดยไม่จำเป็นสำหรับ capstone

**ถ้าต้องการให้ update "รู้สึกสด" กว่าเดิม** (optional, อนาคต):
1. ตั้ง `STALE_THRESHOLD_SECONDS=30` (แก้แล้ว) + heartbeat save (แก้แล้ว) — ข้อมูลจะไม่หาย
2. เปลี่ยน frontend polling จาก 5000ms เป็น 2000ms สำหรับหน้าที่ดู realtime
3. ถ้าหลังจาก 1–2 ยังไม่พอ → เพิ่ม `/api/events` endpoint (SSE) ใน Flask ส่ง push event เมื่อ index คำนวณเสร็จ (ไม่ต้องเปลี่ยน DB)

**สรุป:** **ไม่ต้องเปลี่ยน DB** — SQLAlchemy + REST ปัจจุบันทำงานได้ปกติหลังแก้บั๊ก logic ข้างบน

---

## 15) API Contract สำหรับเพื่อนที่ทำ AI

ส่วนนี้คือ **สัญญาเชื่อม** ที่ AI pipeline (RL agent, predictive model, สิ่งใด ๆ ที่ใช้ข้อมูลการจราจรแล้วคืนคำสั่งไฟ) ต้องเข้า/ออกผ่านตัวไหน. ให้ยึดตามนี้ โครงสร้าง backend จะไม่แตก.

### 15.1 ตำแหน่งที่ต้องเขียน AI Logic

**ไฟล์หลัก:** `backend/ai/pipeline.py` (มี stub `predict()` แล้ว)
```python
def predict(junction_state: dict) -> dict:
    """
    Input:  junction_state จาก /api/ai/junction-state (ดูด้านล่าง)
    Output: {"junction_id", "recommended_phase_index", "recommended_rygState",
             "duration_s", "confidence", "reason"}
    """
```
- AI agent รันเป็น thread แยก (`ai/loop.py`) อ่าน live state → เรียก `predict()` → โพสต์ไป `/api/admin/traffic-light/<junction_id>` ด้วย `mode=ai`

### 15.2 API ที่ AI ต้อง **อ่าน** (input)

| Endpoint | Method | คืนอะไร | ความถี่แนะนำ |
|---|---|---|---|
| `/api/traffic-index` | GET | area index + per-road index / speed / vehicle_count / level / has_data | 5s |
| `/api/road-density` | GET | per-road density list (มี has_data) | 5s |
| `/api/vehicles` | GET | live vehicle list (lat/lng/speed/type) | 2s |
| `/api/cameras/<id>/counts` | GET | รถแยกประเภท (car/motorcycle/bus/truck) + confidence + timestamp | 1-2s ต่อกล้อง |
| `/api/cameras/<id>/vehicles` | GET | vehicles track ใกล้กล้อง + queue length | 2s |
| `/api/admin/junction/<jid>/phase` | GET | ข้อมูล TLS: current phase, program, controlled_lanes, phases[], approach_counts | 2s |
| `/api/health` | GET | `yolo.available`, `simulation_active`, `camera_count` — เช็กก่อนเริ่มทุก cycle | 30s |

**หมายเหตุสำคัญ:**
- ตรวจ `has_data === false` ก่อนใช้ค่า `speed`/`index` — ถ้า false แปลว่าไม่มีข้อมูล ต้องข้าม (ห้ามถือว่า index=0 = free flow)
- `yolo.available === false` → ข้อมูลนับรถจะมาจาก SUMO อย่างเดียว (simulation mode) หรือ 0 ทั้งหมด (real mode) — pipeline ควร fallback ไปใช้ historical profile

### 15.3 API ที่ AI ต้อง **เขียน** (output = ควบคุมไฟ)

| Endpoint | Method | Body | ผลลัพธ์ |
|---|---|---|---|
| `/api/admin/traffic-light/<junction_id>` | POST | `{"mode":"ai","phase_index":int,"duration_s":int,"reason":"..."}` | เขียน `ManualSignalOverride` row + signal apply loop ยิงเข้า SUMO ภายใน 2s |
| `/api/admin/traffic-light/<junction_id>/release` | POST | `{}` | เคลียร์ override — SUMO กลับใช้ program default |
| `/api/ai/log` (optional, ต้องสร้าง) | POST | `{"junction_id","decision","observed_metrics","reward"}` | บันทึกประสบการณ์สำหรับ train RL offline |

**Schema ของ override (DB table `manual_signal_overrides`):**
- `junction_id` (FK → `junctions`)
- `mode` ∈ `{"manual","ai"}`
- `phase_index` (int) หรือ `rygState` (ถ้าเขียน state ตรง)
- `duration_s` (int, default 60) — TTL 10 นาทีถ้าไม่ renew
- `decided_by` (string) = `"ai"` หรือ `"user:<username>"`

### 15.4 Rules ที่ AI ต้องปฏิบัติตาม (ไม่มี negotiable)

1. **ห้ามกระโดด phase แบบ R→G ตรง ๆ** — ต้องผ่าน yellow transition (signal_apply_loop enforce อยู่แต่ AI ควรส่งเฉพาะ phase index ที่อยู่ใน `programLogic.phases[]`)
2. **Min green 10 วินาที, Max green 90 วินาที** — ถ้าส่ง < 10 หรือ > 90 ระบบจะ reject
3. **1 junction = 1 decision/second** — ห้ามยิงติดต่อกัน (signal apply loop sample ทุก 2s)
4. **ห้าม override junction ที่ mode != "ai"** — ถ้า user ตั้ง manual ไว้ ให้ respect (check `GET .../phase` ก่อน)
5. **AI ต้อง publish `reason` ทุกครั้ง** เก็บใน log เพื่อ explainability

### 15.5 Event / State ที่ AI ควรใช้ (input features)

สำหรับแต่ละ junction:
- `approach_counts`: {approach_id → vehicle_count} จาก `/api/admin/junction/<jid>/phase`
- `queue_lengths`: {approach_id → halted_count} (เพิ่มได้ใน response)
- `time_of_day`: ชั่วโมงปัจจุบัน → feature สำคัญสำหรับ pattern-aware policy
- `upstream_density`: road density ของถนนที่ไหลเข้า junction (อ่านจาก `/api/road-density`)
- `historical_profile`: `data/pathumwan_traffic_profile.json` ส่วน `average_hourly_profile[hour]` — ใช้ bootstrap RL
- `recent_overrides`: query `ManualSignalOverride` 10 นาทีที่ผ่านมา เพื่อรู้ว่า AI เพิ่งเปลี่ยนอะไรไป

### 15.6 ขั้นตอนเริ่มเขียน AI (onboarding check-list)

```
1. cd Pathumwan/backend && python -m pip install -r requirements.txt
2. ตั้ง DATABASE_URI / SUMO_HOME ใน .env
3. เริ่ม backend: python app.py  (ตรวจ /api/health ต้อง simulation_active=true, yolo.available=true)
4. ทดสอบเรียก API ใน notebook:
     import requests
     r = requests.get("http://localhost:5000/api/traffic-index").json()
     assert all("has_data" in road for road in r["roads"])
5. เขียนหรือแก้ backend/ai/pipeline.py::predict() โดยเริ่มจาก heuristic ก่อน (เช่น "approach ไหนรถเยอะสุด → ให้ green")
6. เปิด AI thread: ตั้ง env var AI_ENABLED=1 (ยังไม่มี — ถ้าเพื่อนทำต้องเพิ่มเอง) หรือสร้าง loop แยก
7. POST ไปที่ /api/admin/traffic-light/<jid> ด้วย mode=ai แล้วเช็กใน sumo-gui ว่าเปลี่ยน phase ภายใน 2s
8. ถ้าเรียกแล้วหน้า frontend /control ยังโชว์ manual → ตรวจว่าส่ง mode="ai" ไม่ใช่ "manual"
```

### 15.7 เครื่องมือ ML/RL ที่แนะนำ (เผื่อเพื่อนเลือก)

| ประเภท policy | ไลบรารี | เหตุผล |
|---|---|---|
| Tabular Q-learning | ไม่ต้องมี dep | prototype เร็ว, interpretable, ใช้ feature discretize |
| DQN | `stable-baselines3` | integrate ง่ายกับ SUMO/traci มี gym wrapper `sumo-rl` |
| Multi-agent DQN | `ray[rllib]` | ถ้าทำ 10+ junctions พร้อมกัน |
| Rule-based / heuristic | pure Python | เริ่มก่อน เพื่อมี baseline comparison |

**แนะนำเริ่มจาก:** heuristic → DQN (single junction) → multi-junction เพราะ RL training ใน SUMO เต็มสเกล 55 junctions ใช้เวลานาน

### 15.8 Debug tips

- ถ้า AI เรียก API แล้ว SUMO ไม่เปลี่ยนไฟ: ตรวจ `signal_apply_loop` log ใน backend console
- ถ้า `/api/admin/traffic-light/<jid>` คืน 404: junction_id ต้องเป็น `canonical_junction_id` (ดู `database/reference_data.py`)
- ถ้า reward คำนวณได้ 0 ตลอด: อาจเป็นเพราะ `has_data=false` — filter road ออกก่อนคำนวณ reward
- `print(...)` ใน pipeline.py จะไปอยู่ใน backend stdout — ดูได้ที่หน้า terminal ที่รัน `python app.py`
