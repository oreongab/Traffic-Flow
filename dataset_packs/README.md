# Dataset packs (สำหรับเปลี่ยน Dataset ของ SUMO แบบ “คัดลอกแล้วรันได้”) 

โฟลเดอร์นี้รวมชุด Dataset หลายแบบเพื่อสลับ “จำนวนรถที่ปล่อย” ได้ง่าย และลดโอกาสเพื่อนสับสนเวลาเปลี่ยนไฟล์

## เวลาเปลี่ยน Dataset จริง ๆ ต้องเปลี่ยนไฟล์อะไรบ้าง?

ในโปรเจกต์นี้ `Pathumwan/osm.sumocfg` ชี้ `route-files` ไปที่ `osm.dataset.rou.xml` ดังนั้นการเปลี่ยน dataset ต้องทำให้ไฟล์ route นี้เปลี่ยนตามด้วย

ไฟล์ที่เกี่ยวข้องมี 2 กลุ่ม:

**1) Source (แก้/สลับตัวต้นทาง)**
- `Pathumwan/data/Dataset.csv`

**2) Derived (ต้อง regenerate หรือ copy มาทับเพื่อให้ SUMO เปลี่ยนตาม)**
- `Pathumwan/osm.dataset.trips.xml`
- `Pathumwan/osm.dataset.rou.xml`
- `Pathumwan/data/dataset_route_mapping.generated.json`

> ถ้าเปลี่ยนแค่ `Dataset.csv` แล้วไม่ regenerate/copy ไฟล์ derived — SUMO จะยังใช้ route เดิม (ไม่เปลี่ยน)

## วิธีใช้งานที่แนะนำ (กันพังสุด)

### วิธี A: ใช้สคริปต์ให้ทำให้ครบ (copy + rebuild)
จากโฟลเดอร์ `dataset_packs/`:
 `.\use_pack.bat 03_data_1pct build`

หรือ PowerShell ตรง ๆ:

สิ่งที่สคริปต์ทำ:
- คัดลอก pack ไปทับ `Pathumwan/data/Dataset.csv`
- แล้ว regenerate `osm.dataset.trips.xml` + `osm.dataset.rou.xml` + mapping ใหม่แบบ **ไม่ pause**

### วิธี B: คัดลอกแบบไม่ต้อง build (ใช้ไฟล์ derived ที่ pack เตรียมไว้)
เหมาะกับกรณีที่ไม่อยากตั้งค่าเครื่องให้ build หรืออยากให้เพื่อน “ก็อปแล้วรัน”

ให้คัดลอกไฟล์จาก `dataset_packs/<PACK>/Pathumwan/` ไปทับใน `Pathumwan/` ตามนี้:
- `dataset_packs/<PACK>/Pathumwan/data/Dataset.csv`  -> `Pathumwan/data/Dataset.csv`
- `dataset_packs/<PACK>/Pathumwan/osm.dataset.trips.xml` -> `Pathumwan/osm.dataset.trips.xml`
- `dataset_packs/<PACK>/Pathumwan/osm.dataset.rou.xml`   -> `Pathumwan/osm.dataset.rou.xml`
- `dataset_packs/<PACK>/Pathumwan/data/dataset_route_mapping.generated.json` -> `Pathumwan/data/dataset_route_mapping.generated.json`

## ถ้าใช้ “ลิงก์ (symlink)” ได้ไหม?
ทำได้ แต่โดยทั่วไปไม่แนะนำสำหรับเพื่อนที่ไม่ถนัด เพราะ:
- Windows บางเครื่องต้องใช้สิทธิ์ Admin/Developer Mode ถึงสร้าง symlink ได้
- ต่อให้ลิงก์ `Dataset.csv` แล้ว ก็ยังต้อง regenerate/copy ไฟล์ derived เพื่อให้ SUMO เปลี่ยนตามอยู่ดี

## ทำไม backend / detection ถึงเปลี่ยนตาม dataset ด้วย?
เพราะ backend รัน SUMO ด้วย `Pathumwan/osm.sumocfg` ซึ่งโหลด `Pathumwan/osm.dataset.rou.xml` ตลอด
ดังนั้นเมื่อคุณ rebuild/copy route ใหม่แล้ว:
- ปริมาณ/การกระจายรถใน TraCI จะเปลี่ยน
- endpoint ที่นับรถ/เรนเดอร์กล้อง/ทำ detection จะเห็นสภาพการจราจรใหม่ตาม dataset นั้น

## รายการ packs
- `01_data_67_100pct` : ต้นฉบับปี 67 (100%)
- `02_data_700veh` : ประมาณ 700 คัน
- `03_data_1pct` : 1% ของปี 67 (≈ 5,976 คัน)
- `04_data_3pct` : 3% ของปี 67 (≈ 17,929 คัน)
- `05_data_350veh` : ประมาณ 350 คัน
