# Dataset pack: Pathumwan ปี 67 (ต้นฉบับ)

โฟลเดอร์นี้มีไฟล์ `Dataset.csv` ต้นฉบับ (ปี 67) สำหรับใช้กับการ generate trips/routes ของ SUMO แบบ dataset-driven

## ไฟล์ที่มี
ไฟล์ในโฟลเดอร์นี้ถูกจัดให้ “พาธเหมือนของจริง” เพื่อคัดลอกไปทับใน `Pathumwan/` ได้ตรง ๆ

- `Pathumwan/data/Dataset.csv`
- `Pathumwan/osm.dataset.trips.xml`
- `Pathumwan/osm.dataset.rou.xml`
- `Pathumwan/data/dataset_route_mapping.generated.json`

## วิธีใช้งาน
ตัวเลือกที่แนะนำ: ใช้สคริปต์จาก `dataset_packs/`
- `.\use_pack.bat 01_data_67_100pct build`

หรือถ้าจะคัดลอกแบบไม่ build ให้คัดลอกไฟล์ใน `Pathumwan/` ของ pack นี้ไปทับ `Pathumwan/` ของโปรเจกต์ (ดูรายละเอียดใน `dataset_packs/README.md`)

## สรุปจำนวนรถ (คำนวณจาก Dataset)
นิยาม “จำนวนรถที่ปล่อยรวม” = \(\sum (vph \times duration\_hours)\) โดย `duration_hours = (วินาทีสิ้นสุด - วินาทีเริ่มต้น) / 3600`

- Total VPH (ผลรวม `ปริมาณรถ(คัน/ชม.)`): 197,940.000
- Total vehicles (รวมทั้งวันตามช่วงเวลาในไฟล์): 597,625.000
- เทียบกับปี 67: 100.000000%
