# Dataset pack: Scaled = 3% ของปี 67

โฟลเดอร์นี้เป็น Dataset ที่ “สเกลลงทั้งไฟล์” โดยคูณค่า `ปริมาณรถ(คัน/ชม.)` ทุกแถวด้วยค่าคงที่เดียวกันให้เหลือ 3% ของ Dataset ปี 67 (คงสัดส่วนเดิมทุกทางแยก/ช่วงเวลา/ประเภทรถ)

## ไฟล์ที่มี
ไฟล์ในโฟลเดอร์นี้ถูกจัดให้ “พาธเหมือนของจริง” เพื่อคัดลอกไปทับใน `Pathumwan/` ได้ตรง ๆ

- `Pathumwan/data/Dataset.csv`
- `Pathumwan/osm.dataset.trips.xml`
- `Pathumwan/osm.dataset.rou.xml`
- `Pathumwan/data/dataset_route_mapping.generated.json`

## วิธีใช้งาน
ตัวเลือกที่แนะนำ: ใช้สคริปต์จาก `dataset_packs/`
- `.\use_pack.bat 04_data_3pct build`

หรือถ้าจะคัดลอกแบบไม่ build ให้คัดลอกไฟล์ใน `Pathumwan/` ของ pack นี้ไปทับ `Pathumwan/` ของโปรเจกต์ (ดูรายละเอียดใน `dataset_packs/README.md`)

## สรุปการสเกล
นิยาม “จำนวนรถที่ปล่อยรวม” = \(\sum (vph \times duration\_hours)\) โดย `duration_hours = (วินาทีสิ้นสุด - วินาทีเริ่มต้น) / 3600`

- Scaling factor (คูณกับ `ปริมาณรถ(คัน/ชม.)` ทุกแถว): 0.0300000000
- Total VPH หลังสเกล: 5,938.200
- Total vehicles หลังสเกล: 17,928.750
- เทียบกับปี 67: 3.000000%

หมายเหตุ: ค่า VPH ในไฟล์ถูกปัดเศษเป็นทศนิยม 3 ตำแหน่ง
