# TraffixFlow

ระบบวิเคราะห์และจำลองการจราจรย่านปทุมวัน กรุงเทพมหานคร โดยรวมข้อมูลจาก
กล้องจราจร, YOLO computer vision, SUMO/TraCI simulation, traffic index,
statistics dashboard และระบบควบคุมสัญญาณไฟจราจรไว้ในโปรเจกต์เดียว

โปรเจกต์นี้ออกแบบให้รันได้ 2 รูปแบบหลัก:

| Mode | ใช้เมื่อ | แหล่งข้อมูล | สิ่งที่ระบบทำ |
| --- | --- | --- | --- |
| `sim` | พัฒนา, demo, ทดลอง AI signal control | SUMO network และ route files | จำลองรถ, เรนเดอร์ CCTV จำลอง, คำนวณ density/index, ควบคุมไฟผ่าน TraCI |
| `real` | ต่อกับกล้องจริง | RTSP/MJPEG camera streams | อ่าน frame จริง, ตรวจจับรถด้วย YOLO, tracking, optical flow และส่งผลขึ้น dashboard |

> ค่าเริ่มต้นของระบบคือ `SYSTEM_MODE=sim` และ `CAMERA_BACKEND=sumo`
> เพื่อให้ clone แล้วรัน demo ได้ง่ายที่สุด

---

## สารบัญ

- [ฟีเจอร์หลัก](#ฟีเจอร์หลัก)
- [Tech stack](#tech-stack)
- [โครงสร้างโปรเจกต์](#โครงสร้างโปรเจกต์)
- [สิ่งที่ต้องติดตั้ง](#สิ่งที่ต้องติดตั้ง)
- [เริ่มรันแบบเร็ว](#เริ่มรันแบบเร็ว)
- [รันแบบ Manual](#รันแบบ-manual)
- [Environment variables](#environment-variables)
- [หน้าจอและ API สำคัญ](#หน้าจอและ-api-สำคัญ)
- [Dataset และ SUMO routes](#dataset-และ-sumo-routes)
- [ตรวจงานก่อน push ขึ้น GitHub](#ตรวจงานก่อน-push-ขึ้น-github)
- [Troubleshooting](#troubleshooting)
- [เอกสารเพิ่มเติม](#เอกสารเพิ่มเติม)

---

## ฟีเจอร์หลัก

- Dashboard แสดง traffic index, density, จำนวนรถ และสถานะระบบแบบ near real-time
- หน้า Cameras สำหรับดูภาพกล้อง, frame, MJPEG stream และภาพที่มี detection overlay
- หน้า Control สำหรับควบคุมสัญญาณไฟแบบ manual หรือ AI-assisted
- หน้า Statistics สำหรับดูสถิติรายชั่วโมง รายวัน รายปี และ top roads
- ระบบ authentication: register, login, profile, reset password
- YOLOv12n vehicle detection สำหรับ `car`, `motorcycle`, `bus`, `truck`
- DeepSORT/tracker service สำหรับจับการเคลื่อนที่และนับรถ
- Sparse Lucas-Kanade optical flow สำหรับช่วยกรณี YOLO ตรวจไม่เจอ, stop-and-go, queue และ speed signal
- SUMO/TraCI simulation สำหรับจำลองเครือข่ายถนนปทุมวันและ traffic lights
- Database schema ผ่าน SQLAlchemy รองรับ PostgreSQL และ fallback เป็น SQLite
- Docker Compose สำหรับรัน frontend, backend และ PostgreSQL พร้อมกัน

---

## Tech stack

| Layer | Technology |
| --- | --- |
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS, Chart.js, Leaflet |
| Backend | Python 3.10+, Flask, Flask-CORS, SQLAlchemy |
| Database | PostgreSQL 15 ใน Docker หรือ SQLite fallback ตอนรัน local |
| Computer Vision | Ultralytics YOLOv12n, OpenCV, DeepSORT-style tracking, Lucas-Kanade optical flow |
| Simulation | Eclipse SUMO, TraCI, `osm.net.xml`, `osm.sumocfg` |
| DevOps | Docker Compose, Windows batch scripts, local `.env` config |

---

## โครงสร้างโปรเจกต์

```text
Traffic-Flow/
├── README.md                         # เอกสารหลักของ repo
├── dataset_packs/                    # ชุด dataset สำหรับสลับปริมาณรถใน SUMO
│   ├── README.md
│   └── use_pack.bat
└── Pathumwan/
    ├── docker-compose.yml            # รัน frontend + backend + PostgreSQL
    ├── start.bat                     # quick start สำหรับ Windows
    ├── stop.bat
    ├── build.bat                     # rebuild SUMO route จาก Dataset.csv
    ├── ARCHITECTURE.md               # Mermaid architecture diagrams
    ├── DB_TABLES_REFERENCE.md        # รายละเอียดตาราง database
    ├── DB_DIAGRAM.md                 # database relationship diagram
    ├── DATASET_SWITCHING.md          # วิธีเปลี่ยน dataset/SUMO route
    ├── osm.net.xml                   # SUMO road network
    ├── osm.sumocfg                   # SUMO config หลัก
    ├── osm.dataset.rou.xml.gz        # route file ที่ SUMO ใช้ตอนรัน
    ├── data/
    │   ├── Dataset.csv
    │   ├── pathumwan_roads.json
    │   └── pathumwan_traffic_profile.json
    ├── backend/
    │   ├── app.py                    # Flask entry point และ background threads
    │   ├── config.py                 # load env/config หลัก
    │   ├── requirements.txt
    │   ├── routes/                   # auth, traffic, cameras, stats, admin
    │   ├── services/                 # aggregation, density, index, camera, signal, optical flow
    │   ├── detection/                # YOLO detector และ tracker service
    │   ├── ai/                       # RL/AI signal pipeline
    │   └── database/                 # SQLAlchemy models และ local SQLite fallback
    └── frontend-next/
        ├── package.json
        ├── next.config.ts            # rewrite /api ไป backend
        └── src/
            ├── app/                  # dashboard, control, statistics, cameras, density, admin
            ├── components/
            └── lib/                  # API client, auth, socket, types
```

---

## สิ่งที่ต้องติดตั้ง

### สำหรับทุกเครื่อง

- Git
- Python 3.10 ขึ้นไป
- Node.js 20 ขึ้นไป และ npm

### ถ้ารันแบบ Docker

- Docker Desktop หรือ Docker Engine พร้อม Docker Compose v2
- ไม่จำเป็นต้องลง PostgreSQL หรือ SUMO แยกเอง เพราะ compose จัดการให้

### ถ้ารัน backend แบบ local โดยไม่ใช้ Docker

- Eclipse SUMO และตั้งค่า `SUMO_HOME`
- Python dependencies จาก `Pathumwan/backend/requirements.txt`
- PostgreSQL เป็น optional ถ้าไม่ตั้ง `DATABASE_URI` ระบบจะ fallback ไป SQLite ที่
  `Pathumwan/backend/database/traffixflow.db`

ตัวอย่าง `SUMO_HOME`:

```bash
# macOS/Linux
export SUMO_HOME=/usr/share/sumo
```

```powershell
# Windows PowerShell
$env:SUMO_HOME = "C:\Program Files (x86)\Eclipse\Sumo"
```

---

## เริ่มรันแบบเร็ว

### วิธีที่ 1: Docker Compose

เหมาะที่สุดสำหรับคนที่ clone โปรเจกต์ใหม่ เพราะได้ backend, frontend และ PostgreSQL พร้อมกัน

```bash
cd Pathumwan
docker compose up --build
```

เปิดใช้งาน:

- Web UI: <http://localhost:3000>
- Backend API: <http://localhost:5000>
- Health check: <http://localhost:5000/api/health>

ถ้ามี NVIDIA GPU และติดตั้ง `nvidia-container-toolkit` แล้ว:

```bash
cd Pathumwan
docker compose --profile gpu up --build
```

### วิธีที่ 2: Windows quick start

```bat
cd Pathumwan
start.bat
```

สคริปต์นี้จะ:

- ติดตั้ง frontend dependencies ถ้ายังไม่มี `node_modules`
- ติดตั้ง backend dependencies จาก `backend/requirements.txt`
- เปิด Next.js ที่ port `3000`
- เปิด Flask API ที่ port `5000`

หยุด server:

```bat
cd Pathumwan
stop.bat
```

---

## รันแบบ Manual

ใช้วิธีนี้เมื่อต้อง debug frontend/backend แยกกัน

### 1. Backend

```bash
cd Pathumwan/backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
```

บน Windows PowerShell:

```powershell
cd Pathumwan\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
```

ตรวจว่า backend ทำงาน:

```bash
curl http://localhost:5000/api/health
```

### 2. Frontend

เปิดอีก terminal:

```bash
cd Pathumwan/frontend-next
npm install
npm run dev -- -p 3000
```

Next.js จะเรียก API ผ่าน `/api/*` และ `next.config.ts` จะ rewrite ไปที่ backend
ตามค่า `BACKEND_INTERNAL_URL` ค่า default คือ `http://127.0.0.1:5000`

---

## Environment variables

ไฟล์ config ที่ backend อ่าน:

1. `Pathumwan/backend/.env`
2. `Pathumwan/.env`

> ห้ามใส่ database password, Neon URI, JWT secret หรือ token จริงลง GitHub
> ให้เก็บไว้ใน `.env` local หรือ secret manager ของ platform ที่ deploy

ตัวอย่าง `.env` สำหรับ development:

```env
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
FRONTEND_URL=http://localhost:3000

# ถ้าไม่ตั้ง DATABASE_URI ระบบจะใช้ SQLite fallback
DATABASE_URI=postgresql://traffix_user:traffix_password@localhost:5432/traffixflow
# หรือใช้ NEON_DATABASE_URI แทน DATABASE_URI ได้

SYSTEM_MODE=sim
CAMERA_BACKEND=sumo
SIGNAL_BACKEND=sim
AI_BACKEND=disabled

SUMO_HOME=/usr/share/sumo
SUMO_GUI=0

YOLO_MODEL_PATH=yolo12n.pt
YOLO_CONFIDENCE=0.25
YOLO_IMGSZ=480

DETECTION_INTERVAL=5
INDEX_INTERVAL=30
AGGREGATION_INTERVAL=30

OPTICAL_FLOW_ENABLED=1
OPTICAL_FLOW_FPS_TARGET=2.0
SIGNAL_PROGRAM_MODE=pair
```

ตัวแปร frontend ที่ใช้บ่อย:

```env
NEXT_PUBLIC_API_URL=/api
BACKEND_INTERNAL_URL=http://127.0.0.1:5000
NEXT_PUBLIC_STREAM_API_URL=http://127.0.0.1:5000/api
```

### โหมดสำคัญ

| Variable | ค่าแนะนำตอน dev | ความหมาย |
| --- | --- | --- |
| `SYSTEM_MODE` | `sim` | เลือก runtime หลัก: `sim` หรือ `real` |
| `CAMERA_BACKEND` | `sumo` | ใช้กล้องจำลองจาก SUMO หรือ `rtsp` สำหรับกล้องจริง |
| `SIGNAL_BACKEND` | `sim` | ควบคุมไฟผ่าน simulation |
| `AI_BACKEND` | `disabled` | เปิด/ปิด AI signal backend |
| `SUMO_GUI` | `0` | ตั้งเป็น `1` ถ้าต้องการเปิด `sumo-gui` |
| `SIGNAL_PROGRAM_MODE` | `pair` | `pair` หรือ `sequential4` |
| `CAMERA_RENDER_FPS` | `4` | จำกัด FPS ของกล้องจำลอง |
| `YOLO_IMGSZ` | `480` | ขนาดภาพสำหรับ YOLO inference |

---

## หน้าจอและ API สำคัญ

### Frontend pages

| Path | หน้าที่ |
| --- | --- |
| `/dashboard` | ภาพรวม traffic index, graph และสถานะระบบ |
| `/cameras` | ดูกล้อง, stream, detection overlay และข้อมูลรถรายกล้อง |
| `/control` | ควบคุมไฟจราจร manual/AI และดู AI decisions |
| `/density` | ดูความหนาแน่นบนแผนที่และ geometry ของถนน |
| `/statistics` | สถิติรายชั่วโมง รายวัน รายปี และ top roads |
| `/admin` | จัดการข้อมูล runtime, users, logs และ camera calibration |
| `/login`, `/register`, `/profile` | ระบบผู้ใช้ |

### Backend endpoints

| Endpoint | รายละเอียด |
| --- | --- |
| `GET /api/health` | ตรวจสถานะ backend, runtime mode, simulation, camera count, YOLO |
| `POST /api/auth/register` | สมัครสมาชิก |
| `POST /api/auth/login` | login และรับ token |
| `GET /api/auth/me` | ข้อมูลผู้ใช้ปัจจุบัน |
| `GET /api/vehicles` | รายการ vehicles จาก runtime |
| `GET /api/traffic-index` | traffic index รวม |
| `GET /api/road-density` | density รายถนน |
| `GET /api/traffic-lights` | สถานะ traffic lights |
| `GET /api/status` | สถานะ simulation/runtime |
| `GET /api/roads/geometry` | geometry ถนนสำหรับแผนที่ |
| `GET /api/cameras` | รายการกล้อง |
| `GET /api/cameras/<camera_id>/frame` | snapshot frame |
| `GET /api/cameras/<camera_id>/stream` | MJPEG stream |
| `GET /api/cameras/<camera_id>/detect` | detection frame |
| `GET /api/cameras/<camera_id>/detect/stream` | detection MJPEG stream |
| `GET /api/stats/index-today` | index วันนี้ |
| `GET /api/stats/yearly/<year>` | สถิติรายปี |
| `GET /api/stats/daily-count/<year>` | จำนวนรถรายวัน |
| `GET /api/admin/ai-status` | สถานะ AI signal decision |
| `POST /api/admin/signal/manual` | ส่งคำสั่ง manual signal |

---

## Dataset และ SUMO routes

SUMO ใช้ไฟล์ route หลัก:

```text
Pathumwan/osm.dataset.rou.xml.gz
```

ถ้าเปลี่ยนแค่ `Pathumwan/data/Dataset.csv` แต่ไม่ rebuild/copy route file
SUMO จะยังใช้ traffic pattern เดิม

วิธีที่แนะนำคือใช้ dataset packs:

```bat
cd dataset_packs
use_pack.bat 03_data_1pct build
```

ตัวอย่าง packs ที่มี:

| Pack | ความหมาย |
| --- | --- |
| `01_data_67_100pct` | ข้อมูลปี 67 เต็ม |
| `02_data_700veh` | ประมาณ 700 คัน |
| `03_data_1pct` | 1% ของปี 67 |
| `04_data_3pct` | 3% ของปี 67 |
| `05_data_350veh` | ประมาณ 350 คัน |

อ่านเพิ่มที่ [dataset_packs/README.md](dataset_packs/README.md)
และ [Pathumwan/DATASET_SWITCHING.md](Pathumwan/DATASET_SWITCHING.md)

---

## ตรวจงานก่อน push ขึ้น GitHub

รายการนี้ช่วยให้ repo อ่านง่ายและคนอื่น clone ไปรันต่อได้

### 1. เช็กไฟล์ที่ไม่ควร commit

ไม่ควร push ไฟล์เหล่านี้ถ้ามี secret หรือเป็น runtime output:

- `.env` ที่มี credential จริง
- `node_modules/`
- `.next/`
- local database เช่น `*.db`, `*.sqlite`
- log files เช่น `*.log`
- model/checkpoint ขนาดใหญ่ที่ไม่ได้ตั้งใจ version control

ตรวจด้วย:

```bash
git status --short
```

### 2. เช็ก backend

```bash
cd Pathumwan/backend
python -m pip install -r requirements.txt
python app.py
```

จากอีก terminal:

```bash
curl http://localhost:5000/api/health
```

ถ้ามี `pytest` ใน environment:

```bash
cd Pathumwan/backend
python -m pytest tests
```

### 3. เช็ก frontend

```bash
cd Pathumwan/frontend-next
npm install
npm run lint
npm run build
```

### 4. เช็ก Docker

```bash
cd Pathumwan
docker compose up --build
```

แล้วเปิด:

- <http://localhost:3000>
- <http://localhost:5000/api/health>

### 5. เช็ก README links

บน GitHub ให้กดลิงก์หลักเหล่านี้:

- [Pathumwan/ARCHITECTURE.md](Pathumwan/ARCHITECTURE.md)
- [Pathumwan/DB_TABLES_REFERENCE.md](Pathumwan/DB_TABLES_REFERENCE.md)
- [Pathumwan/DB_DIAGRAM.md](Pathumwan/DB_DIAGRAM.md)
- [Pathumwan/DATASET_SWITCHING.md](Pathumwan/DATASET_SWITCHING.md)
- [dataset_packs/README.md](dataset_packs/README.md)

---

## Troubleshooting

### Backend เปิดแล้ว port ไม่ตรง

ค่า default ของ README คือ backend port `5000` แต่ไฟล์ `.env` local สามารถ override
ด้วย `FLASK_PORT` ได้ ตรวจค่าที่ใช้จริงจาก log ตอน `python app.py`

### Frontend เรียก API ไม่เจอ

ตรวจ `Pathumwan/frontend-next/next.config.ts` และค่า:

```env
BACKEND_INTERNAL_URL=http://127.0.0.1:5000
NEXT_PUBLIC_API_URL=/api
```

ถ้า backend ใช้ port อื่น ต้องปรับ `BACKEND_INTERNAL_URL` ให้ตรง

### SUMO เปิดไม่ได้

เช็กว่าเครื่องมีคำสั่ง `sumo` และตั้ง `SUMO_HOME` ถูกต้อง:

```bash
sumo --version
```

ถ้าต้องการดูหน้าต่าง simulation:

```env
SUMO_GUI=1
```

แล้ว restart backend

### Database ไม่ใช่ตัวที่คิดไว้

Backend ใช้ลำดับนี้:

1. `DATABASE_URI`
2. `NEON_DATABASE_URI`
3. SQLite fallback ที่ `Pathumwan/backend/database/traffixflow.db`

ถ้า login หรือข้อมูลหาย ให้ตรวจว่า env ที่ backend โหลดเป็น database ตัวเดียวกับที่ต้องการจริง

### กล้องหรือ stream ไม่ขึ้น

- ใน `sim` mode ให้รอ SUMO simulation start ก่อน แล้วดู `/api/status`
- ใน `real` mode ต้องมี camera stream URL จริงใน database/runtime config
- ตรวจ endpoint `/api/cameras` ว่ามีกล้อง active หรือไม่
- detection stream ใช้ `/api/cameras/<camera_id>/detect/stream`

### สถิติยังเป็น 0

ระบบต้องมีข้อมูลจาก SUMO หรือ detection ก่อน aggregation จึงจะมีค่า
ให้รออย่างน้อย `INDEX_INTERVAL` และ `AGGREGATION_INTERVAL`
หรือดู `/api/health` และ `/api/status` ว่า simulation active แล้วหรือยัง

---

## เอกสารเพิ่มเติม

| เอกสาร | ใช้ดูเรื่อง |
| --- | --- |
| [Pathumwan/ARCHITECTURE.md](Pathumwan/ARCHITECTURE.md) | system architecture และ Mermaid diagrams |
| [Pathumwan/DB_TABLES_REFERENCE.md](Pathumwan/DB_TABLES_REFERENCE.md) | รายละเอียด schema/database tables |
| [Pathumwan/DB_DIAGRAM.md](Pathumwan/DB_DIAGRAM.md) | ER/database diagram |
| [Pathumwan/RUNTIME_CAMERA_OPERATIONS.md](Pathumwan/RUNTIME_CAMERA_OPERATIONS.md) | camera runtime และ calibration workflow |
| [Pathumwan/DATASET_SWITCHING.md](Pathumwan/DATASET_SWITCHING.md) | วิธีเปลี่ยน dataset ให้ SUMO เปลี่ยนตามจริง |
| [Pathumwan/backend/ai/PIPELINE.md](Pathumwan/backend/ai/PIPELINE.md) | AI signal pipeline, state, action, reward |
| [dataset_packs/README.md](dataset_packs/README.md) | dataset packs และวิธีสลับ route files |

---

## สรุปสั้น

TraffixFlow คือ full-stack traffic analytics project สำหรับพื้นที่ปทุมวัน:

```text
Next.js UI
  -> Flask REST/MJPEG API
  -> SUMO simulation หรือ RTSP camera streams
  -> YOLO + tracking + optical flow
  -> SQLAlchemy database
  -> dashboard, statistics, camera view และ signal control
```

เริ่มง่ายที่สุด:

```bash
cd Pathumwan
docker compose up --build
```

แล้วเปิด <http://localhost:3000>
