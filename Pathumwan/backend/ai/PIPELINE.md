# AI Signal Pipeline

ไฟล์นี้อธิบาย pipeline การนำข้อมูลจราจรเข้า AI ของโปรเจกต์ Pathumwan แบบที่ต่อจากระบบปัจจุบันได้ทันที

## เป้าหมาย

AI ต้องตอบคำถามนี้ให้ได้ทุก `ACTION_INTERVAL` วินาที:

- แยกไหนควรปล่อยรถก่อน
- ควรสลับไป phase ไหน
- ควรค้างไฟเขียวนานเท่าไรในกรอบที่ปลอดภัย

ดังนั้น input ของ AI ต้องไม่ใช่แค่ "จำนวนรถรวมทั้งเมือง" แต่ต้องแยกเป็นราย junction และรายทิศทางเข้าแยก

## แหล่งข้อมูลที่มีอยู่แล้วในระบบ

### 1. SUMO live state

ใช้สำหรับข้อมูลควบคุมสัญญาณโดยตรง เพราะ SUMO รู้ lane และ phase จริง

แหล่งโค้ด:
- [backend/simulation.py](c:/Users/Pattama/OneDrive/Documents/capstone/Pathumwan/backend/simulation.py)
- [backend/ai/environment.py](c:/Users/Pattama/OneDrive/Documents/capstone/Pathumwan/backend/ai/environment.py)

ข้อมูลที่ดึงได้:
- queue length ต่อ lane
- waiting time ต่อ lane
- vehicle count ต่อ lane
- mean speed ต่อ lane
- current phase ของ traffic light
- phase program / จำนวน phase

### 2. CCTV / detection

ใช้เป็น traffic demand signal เพิ่มเติม โดยเฉพาะจุดที่อยากสะท้อนสภาพจริงจากกล้อง

แหล่งโค้ด:
- [backend/detection/detector_service.py](c:/Users/Pattama/OneDrive/Documents/capstone/Pathumwan/backend/detection/detector_service.py)
- [backend/services/density.py](c:/Users/Pattama/OneDrive/Documents/capstone/Pathumwan/backend/services/density.py)
- [backend/routes/cameras.py](c:/Users/Pattama/OneDrive/Documents/capstone/Pathumwan/backend/routes/cameras.py)

ข้อมูลที่มี:
- car / motorcycle / bus / truck / total ต่อ camera
- timestamp ล่าสุดของ detection
- road mapping ของ camera

### 3. Aggregated stats

ใช้สำหรับ dashboard, monitoring, offline analysis, model validation

แหล่งโค้ด:
- [backend/routes/stats.py](c:/Users/Pattama/OneDrive/Documents/capstone/Pathumwan/backend/routes/stats.py)
- [backend/services/aggregation.py](c:/Users/Pattama/OneDrive/Documents/capstone/Pathumwan/backend/services/aggregation.py)
- [backend/services/traffic_index.py](c:/Users/Pattama/OneDrive/Documents/capstone/Pathumwan/backend/services/traffic_index.py)

## ไฟล์ pipeline ที่เพิ่มให้

ไฟล์ใหม่:
- [backend/ai/pipeline.py](c:/Users/Pattama/OneDrive/Documents/capstone/Pathumwan/backend/ai/pipeline.py)

หน้าที่หลักของไฟล์นี้:
- ดึงข้อมูล live จาก SUMO และ map เข้ากับ junction
- แปลงข้อมูลเป็น structured snapshot
- normalize เป็น observation vector สำหรับ model
- แปลง action ของ model ให้เป็น control plan ที่ log ได้
- export training records สำหรับ offline training / imitation learning / analytics

## ควรกำหนดถนนหรือเส้นทางก่อนมั้ย

ต้องกำหนดอย่างน้อย 2 ชั้น

### ชั้นที่ 1: Junction-centric

AI ควบคุม "แยก" ดังนั้นหน่วยหลักต้องเป็น `junction_id`

แต่ละ junction ต้องรู้:
- inbound lanes อะไรบ้าง
- outbound lanes อะไรบ้าง
- road ไหน feed เข้ามา
- กล้องตัวไหนดู junction นี้อยู่
- phase ปัจจุบันคืออะไร

### ชั้นที่ 2: Road / approach-centric

ถนน/ทางเข้าที่ feed เข้าจุดตัดต้องถูก map เข้ากับ junction

ตัวอย่างแนวคิด:
- ถนนพระราม 1 ขาเข้าแยกสยาม = approach A
- ถนนพญาไท ขาเข้าแยกปทุมวัน = approach B

ถ้ายังไม่ map ระดับ approach อย่างน้อย AI จะรู้แค่ว่า "แยกนี้รถเยอะ" แต่ไม่รู้ว่าเยอะจากฝั่งไหน ทำให้กำหนด phase ได้ไม่แม่น

## Input ที่ AI ควรรับ

ปัจจุบัน pipeline ใช้ feature หลักต่อ junction ดังนี้:
- `queue_length`
- `waiting_time`
- `vehicle_count`
- `avg_speed_kmh`
- `current_phase`
- `phase_duration`
- `time_of_day_norm`

นี่คือ minimum viable state

ถ้าจะให้แม่นขึ้น ควรเพิ่มในรุ่นถัดไป:
- per-approach queue แยกทิศ
- per-class count เช่น bus/truck/motorcycle ratio
- downstream congestion
- camera freshness ว่าข้อมูลกล้องเก่ากี่วินาที
- event flags เช่น rain / accident / road work

## Output ที่ AI ควรส่งกลับ

ไม่ควรส่งแค่ `green/red` แบบลอย ๆ

ควรส่งเป็นโครงสร้างแบบนี้:
- `junction_id`
- `target_phase`
- `hold_seconds`
- `min_green_seconds`
- `max_green_seconds`

ในไฟล์ [backend/ai/pipeline.py](c:/Users/Pattama/OneDrive/Documents/capstone/Pathumwan/backend/ai/pipeline.py) มี `SignalAction` ให้แล้ว

## ควรใส่ logic ไว้ไฟล์ไหน

### 1. ดึง feature และ normalize

ใส่ใน:
- [backend/ai/pipeline.py](c:/Users/Pattama/OneDrive/Documents/capstone/Pathumwan/backend/ai/pipeline.py)

เหตุผล:
- เป็น shared layer ใช้ได้ทั้ง training และ runtime inference
- ไม่ทำให้ logic ซ้ำระหว่าง `environment.py` กับ `predictor.py`

### 2. RL environment

ใส่ใน:
- [backend/ai/environment.py](c:/Users/Pattama/OneDrive/Documents/capstone/Pathumwan/backend/ai/environment.py)

สิ่งที่ควรทำต่อ:
- เปลี่ยน `_get_observation()` ให้เรียก pipeline แทนการคำนวณเองทั้งหมด
- เก็บ `phase_duration_map` เพื่อส่งเข้า `build_pipeline_snapshot`

### 3. Runtime inference

ใส่ใน:
- [backend/ai/predictor.py](c:/Users/Pattama/OneDrive/Documents/capstone/Pathumwan/backend/ai/predictor.py)

สิ่งที่ควรทำต่อ:
- เปลี่ยน `_build_observation()` ให้ใช้ `snapshot_to_observation()` จาก pipeline
- หลัง predict แล้วใช้ `build_signal_actions()` เพื่อแปลง action ก่อน apply จริง

### 4. Logging / dataset export

แนะนำเพิ่มใน:
- [backend/services/ai_logger.py](c:/Users/Pattama/OneDrive/Documents/capstone/Pathumwan/backend/services/ai_logger.py)

ใช้ `export_training_record()` เพื่อเก็บ:
- state
- action
- reward
- metadata ของ junction/road/camera

## Flow ที่แนะนำ

### Training flow

1. SUMO รัน simulation
2. pipeline ดึง state ของทุก junction
3. normalize เป็น observation vector
4. model เลือก action
5. environment apply action ไปยัง traffic light
6. simulation รันต่ออีก `ACTION_INTERVAL`
7. คำนวณ reward
8. เก็บ `(state, action, reward, next_state)`

### Runtime flow

1. backend loop อ่าน live SUMO state ทุก 3-10 วินาที
2. pipeline สร้าง snapshot
3. predictor สร้าง observation
4. model ส่ง `target_phase`
5. convert เป็น `SignalAction`
6. validate safety rules
7. apply ผ่าน TraCI
8. log decision ลง DB

## Safety rules ที่ต้องมี

ก่อน apply action จริง ควรมี guard เสมอ

- ห้ามเขียวต่ำกว่า `MIN_GREEN_TIME`
- ห้ามยืดเขียวเกิน `MAX_GREEN_TIME`
- ต้องมี yellow/all-red clearance ก่อนเปลี่ยน phase ถ้า program ต้องการ
- ถ้า model ส่ง phase ที่ไม่มีจริง ให้ modulo กับจำนวน phase
- ถ้าข้อมูลขาด ให้ fallback เป็น rule-based policy

## ตัวอย่างการใช้ pipeline ใน runtime

```python
from ai.pipeline import build_pipeline_snapshot, snapshot_to_observation, build_signal_actions

snapshot = build_pipeline_snapshot(
    traci_module=traci,
    junction_ids=junction_ids,
    junction_camera_map=junction_camera_map,
    junction_road_map=junction_road_map,
    phase_duration_map=phase_duration_map,
)
obs = snapshot_to_observation(snapshot)
actions = predictor.get_actions(obs)
plan = build_signal_actions(junction_ids, actions)
```

## การ map กล้องกับ junction ควรทำยังไง

ขั้นต่ำต้องมี mapping แบบนี้:

- `junction_id -> [camera_id...]`
- `junction_id -> [road_id...]`
- `camera_id -> road_id`

แนะนำให้เก็บ mapping เหล่านี้ไว้ใน memory ตอน simulation start จากข้อมูลใน:
- [backend/simulation.py](c:/Users/Pattama/OneDrive/Documents/capstone/Pathumwan/backend/simulation.py)
- [backend/services/camera_sync.py](c:/Users/Pattama/OneDrive/Documents/capstone/Pathumwan/backend/services/camera_sync.py)
- [backend/cctv.py](c:/Users/Pattama/OneDrive/Documents/capstone/Pathumwan/backend/cctv.py)

## ถ้าจะส่ง "ตัวเลข" เข้า AI ต้องส่งยังไง

ส่งเป็น vector ที่เรียงคงที่เสมอ

ตัวอย่างสำหรับ 3 junction และ 7 features ต่อ junction:

```text
[
  j1_queue, j1_wait, j1_count, j1_speed, j1_phase, j1_phase_dur, j1_time,
  j2_queue, j2_wait, j2_count, j2_speed, j2_phase, j2_phase_dur, j2_time,
  j3_queue, j3_wait, j3_count, j3_speed, j3_phase, j3_phase_dur, j3_time,
]
```

ข้อสำคัญ:
- ลำดับ junction ต้องคงที่ทุกครั้ง
- ขนาด vector ต้องเท่าเดิมทุกครั้ง
- normalization ต้องเหมือนกันทั้ง training และ inference

## ถ้าจะทำ AI ต่อเอง แนะนำลำดับงาน

1. รวม feature building ให้ใช้ [backend/ai/pipeline.py](c:/Users/Pattama/OneDrive/Documents/capstone/Pathumwan/backend/ai/pipeline.py) ตัวเดียวทั้ง training/runtime
2. สร้าง `junction_camera_map` และ `junction_road_map` ตอน simulation start
3. เปลี่ยน `environment.py` และ `predictor.py` ให้ใช้ pipeline ตัวเดียวกัน
4. log training records ลงไฟล์หรือ DB
5. validate ก่อนว่า action ที่ model ส่งไม่ผิด safety constraints
6. ค่อยเพิ่ม feature ต่อ approach และ downstream congestion

## คำตอบสั้น ๆ สำหรับคำถามหลัก

- ต้องกำหนดถนน/เส้นทางก่อนมั้ย: ต้องกำหนดอย่างน้อย road-to-junction mapping และถ้าทำได้ควรกำหนดระดับ approach
- input คืออะไร: state vector ต่อ junction จาก queue/wait/count/speed/phase/time
- output คืออะไร: phase target และเวลาคุมไฟในกรอบปลอดภัย
- ควรเขียนไว้ไฟล์ไหน: feature pipeline ใน `backend/ai/pipeline.py`, runtime apply ใน `predictor.py`, training loop ใน `environment.py`
- ตัวแปรต้องเชื่อมกันยังไง: `junction_id` เป็นแกนกลาง เชื่อม road, lane, camera, phase, reward เข้าด้วยกัน
