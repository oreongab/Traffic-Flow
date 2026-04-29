# Dataset pack: Scaled ~350 vehicles

โฟลเดอร์นี้เป็น Dataset ที่ “สเกลลงทั้งไฟล์” โดยคูณค่า `ปริมาณรถ(คัน/ชม.)` ทุกแถวด้วยค่าคงที่เดียวกัน เพื่อให้จำนวนรถที่ปล่อยรวมอยู่ราว ๆ 350 คัน (คงสัดส่วนเดิมทุกทางแยก/ช่วงเวลา/ประเภทรถ)

## ไฟล์ที่มี
ไฟล์ในโฟลเดอร์นี้ถูกจัดให้ “พาธเหมือนของจริง” เพื่อคัดลอกไปทับใน `Pathumwan/` ได้ตรง ๆ

- `Pathumwan/data/Dataset.csv`
- `Pathumwan/osm.dataset.trips.xml`
- `Pathumwan/osm.dataset.rou.xml.gz`
- `Pathumwan/data/dataset_route_mapping.generated.json`

## วิธีใช้งาน
ตัวเลือกที่แนะนำ: ใช้สคริปต์จาก `dataset_packs/`
- `.\use_pack.bat 05_data_350veh build`

หรือถ้าจะคัดลอกแบบไม่ build ให้คัดลอกไฟล์ใน `Pathumwan/` ของ pack นี้ไปทับ `Pathumwan/` ของโปรเจกต์ (ดูรายละเอียดใน `dataset_packs/README.md`)

## สรุปการสเกล
นิยาม “จำนวนรถที่ปล่อยรวม” = \(\sum (vph \times duration\_hours)\) โดย `duration_hours = (วินาทีสิ้นสุด - วินาทีเริ่มต้น) / 3600`

- Scaling factor (คูณกับ `ปริมาณรถ(คัน/ชม.)` ทุกแถว): 0.0005857034
- Total VPH หลังสเกล: 115.933
- Total vehicles หลังสเกล: 350.000
- เทียบกับปี 67: 0.058565%

หมายเหตุ: ค่า VPH ในไฟล์ถูกปัดเศษเป็นทศนิยม 3 ตำแหน่ง
