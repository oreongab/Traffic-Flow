# Pathumwan Implementation Update

## Revision Scope

เอกสารนี้อัปเดตให้ตรงกับสถานะงานปัจจุบันของโปรเจกต์ และตัดข้อความ proposal เก่าที่ไม่ใช่ source of truth ออกแล้ว

งานที่ทำใน revision นี้มี 3 เรื่องหลัก:

1. ปรับภาพ CCTV จากมุมมองแผนที่ด้านบนให้เป็นมุมกล้องแบบ roadside analytics มากขึ้น
2. เพิ่ม mini-map ใน feed เพื่อยืนยันว่ากล้องตรงกับพิกัดจริงบนแผนที่
3. อัปเดตเอกสารฐานข้อมูลในไฟล์เดิมทั้งหมดให้ตรงกับ `backend/database/models.py`

งานที่อัปเดตเพิ่มในรอบ incident fix นี้มี 4 เรื่องหลัก:

4. ซ่อม inventory กล้องใน PostgreSQL จากอาการ DB ยุบเหลือ 13 rows / 2 active rows ให้กลับมาตรงกับ `osm.net.xml`
5. ทำให้ `/api/cameras`, runtime status, และ camera catalog ซ่อม inventory อัตโนมัติเมื่อ DB drift
6. ป้องกัน camera sync เขียนทับ `camera_id` เดิมเมื่อ match ด้วย `sumo_tls_id` เพื่อไม่ให้ FK ใน `approaches` พัง
7. รีเฟรช `data/camera_runtime_config.generated.json` จาก DB state ล่าสุดให้ตรงกับ inventory 55 กล้อง

## Completed In This Revision

### 1. CCTV Rendering / Camera Position Readability

- `backend/cctv_renderer.py`
  - เปลี่ยนจากภาพ top-down map style เป็น perspective view แบบกล้องจราจร
  - ใช้ SUMO road geometry เดิมในการคำนวณทิศกล้องและแนวถนน จึงยังยึดพิกัดจริงของกล้องเหมือนเดิม
  - เพิ่ม lane guide, field-of-view line, crosshair และ HUD เพื่อให้ผู้ใช้เข้าใจมุมกล้องง่ายขึ้น
  - เพิ่ม overlay แสดง `GPS`, ระยะครอบคลุม (`RANGE`) และทิศกล้อง (`HDG`)

- `frontend-next/src/components/CctvFeed.tsx`
  - เปิดใช้งาน mini-map inset บน feed โดยใช้พิกัดกล้องจริงจาก API
  - แสดงพิกัดละติจูด/ลองจิจูดบนหัว feed เพื่อเทียบกับ marker บนแผนที่หลักได้ทันที

- `frontend-next/src/components/CctvMiniMap.tsx`
  - ปรับ mini-map ให้เห็นจุดติดตั้งกล้องชัดขึ้น
  - เพิ่มวง coverage รอบกล้องเพื่อสื่อช่วงพื้นที่ที่ feed กำลังเก็บข้อมูล

### 2. Database Documentation

- ไม่สร้าง `db_documentation.md` ใหม่
- อัปเดตเอกสารเดิมแทน เพื่อไม่ให้เกิดหลายไฟล์ที่อธิบาย schema ชุดเดียวกัน

ไฟล์ที่อัปเดต:

- `DB_DIAGRAM.md`
- `DB_TABLES_REFERENCE.md`
- `DB_DIAGRAM.mmd`
- `DB_DIAGRAM.dbml`
- `README.md`

### 3. Camera Inventory / DB / Frontend Consistency Fix

- `backend/services/camera_sync.py`
  - เพิ่ม `repair_camera_inventory_if_needed()` เพื่อเช็คจำนวนกล้องใน DB เทียบกับ inventory จาก `osm.net.xml`
  - ถ้า DB หดเหลือ subset เก่า ระบบจะ sync กลับขึ้นมาเป็น inventory เต็มอัตโนมัติ
  - sync ปัจจุบัน preserve `camera_id` เดิมเมื่อเจอ row เดิมด้วย `sumo_tls_id` จึงไม่ทำให้ FK ใน `approaches` แตก
  - ระหว่าง sync จะเติม `approaches` สำหรับกล้องที่ยังไม่มี mapping ให้ครบกับ junction/road ที่ถูกต้อง

- `backend/routes/cameras.py`
  - `/api/cameras` ไม่พึ่ง active cameras ใน DB อย่างเดียวแล้ว
  - ตอนสร้าง response จะ union ข้อมูลจาก DB, live SUMO camera points, และ offline network inventory
  - ส่ง `junction_id` และ `sumo_tls_id` ไป frontend ด้วย เพื่อให้ UI จับคู่กล้องกับแยกได้เสถียรกว่าเดิม

- `backend/services/mapping.py`
  - `get_camera_catalog()`, `get_camera_approach_map()`, `get_camera_zone_map()`, `get_camera_calibration_map()` เรียก repair inventory ก่อนอ่าน DB
  - ทำให้ real-mode tracker, live-state aggregation, และ runtime mapping ไม่ยุบกลับไปเหลือ subset เก่า

- `backend/services/camera_runtime.py`
  - หน้า admin runtime จะเห็น inventory กล้องเต็ม 55 active cameras แม้ DB เคย drift มาก่อน
  - export runtime seed ใช้ DB state ล่าสุดตรงกับ inventory ที่ซ่อมแล้ว

- `frontend-next/src/app/control/page.tsx`
  - การ resolve กล้องสำหรับแยกไม่พึ่ง `camera_id` อย่างเดียวอีกต่อไป
  - ถ้า `camera_id` เปลี่ยนหรือชื่อ machine name ไม่เสถียร หน้า control ยังจับ feed เดิมได้จาก `junction_id` / `sumo_tls_id`
  - ลดโอกาสที่ feed จะสลับกล้องไปมาเองระหว่าง refresh 5 วินาที

- `data/camera_runtime_config.generated.json`
  - regenerate จาก DB หลัง repair สำเร็จ
  - ปัจจุบัน export กล้องครบ **55 cameras** ตาม runtime catalog

## Current Database Truth

- Persistent schema ปัจจุบันมี **16 ตารางจริงในฐานข้อมูล**
- ชื่ออย่าง `CameraStream`, `CameraCalibration`, `CameraZone` เป็นเพียง Python alias ที่ชี้กลับมาที่ `cameras` ไม่ใช่ table แยก
- ตาราง runtime อย่าง `live_vehicle_tracks` และ `live_approach_metrics` ไม่ถูก persist ลง DB แล้ว ข้อมูล runtime อยู่ใน memory/service layer
- inventory กล้องที่ใช้งานจริงใน Pathumwan ปัจจุบันคือ **55 active cameras** จาก traffic-light inventory ใน `osm.net.xml`
- DB อาจยังมี row เก่าเป็น `inactive` เก็บไว้เชิงประวัติ แต่ frontend/runtime จะใช้ active inventory ชุดเดียวกันเป็นหลัก
- `camera_runtime_config.generated.json` ถูก regenerate จาก DB state ล่าสุดและตรงกับ runtime catalog แล้ว

## Notes On Older Statements Removed From The Previous Version

- ตัดหัวข้อ `Open Questions` ออก เพราะงานรอบนี้ลงมือแก้แล้ว ไม่ใช่ข้อเสนอรออนุมัติ
- ตัดข้อเสนอสร้างไฟล์ `db_documentation.md` ออก เพราะเลือกอัปเดตเอกสาร DB เดิมในที่เดิม
- ตัดถ้อยคำที่ผูกกับอาการชั่วคราวใน runtime บางรอบออก เพื่อไม่ให้แผนกลายเป็นบันทึก incident ที่ล้าสมัย

## Follow-up Verification

1. เปิดหน้า Dashboard หรือ Control เพื่อดูว่า feed ใหม่อ่านทิศถนนและตำแหน่งกล้องได้ง่ายขึ้น
2. เทียบพิกัดใน HUD ของ feed กับ mini-map inset และ marker บนแผนที่หลัก
3. ใช้ `DB_TABLES_REFERENCE.md` เป็นเอกสารหลักเวลาตรวจว่าตารางไหนเก็บอะไรและเชื่อมกับตารางใด
4. เช็ค `GET /api/cameras` หรือ runtime export ว่าคืนกล้อง active ครบ 55 ตัว
5. เช็คหน้า `/control` ว่ากล้องไม่กระโดดสลับ feed เองเมื่อข้อมูล refresh และชื่อแสดงตามตำแหน่งแยกได้ถูกต้องขึ้น
