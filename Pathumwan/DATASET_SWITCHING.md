# Changing SUMO Dataset (Pathumwan)

เอกสารนี้สรุป “เปลี่ยน dataset แล้วให้ SUMO/Backend เปลี่ยนตามจริง”

## SUMO ใช้ไฟล์อะไรตอนรัน?
- SUMO ถูกสั่งรันด้วย [Pathumwan/osm.sumocfg](Pathumwan/osm.sumocfg)
- ในไฟล์นี้ `route-files` ชี้ไปที่ `osm.dataset.rou.xml.gz`

ดังนั้นถ้า `osm.dataset.rou.xml.gz` ไม่เปลี่ยน — SUMO จะไม่เปลี่ยน

## ต้องเปลี่ยนไฟล์อะไรบ้างเมื่อเปลี่ยน dataset?

**Source (ตัวต้นทาง)**
- [Pathumwan/data/Dataset.csv](Pathumwan/data/Dataset.csv)

**Derived (ต้อง regenerate หรือ copy มาทับ)**
- [Pathumwan/osm.dataset.trips.xml](Pathumwan/osm.dataset.trips.xml)
- [Pathumwan/osm.dataset.rou.xml.gz](Pathumwan/osm.dataset.rou.xml.gz)
- [Pathumwan/data/dataset_route_mapping.generated.json](Pathumwan/data/dataset_route_mapping.generated.json)

## วิธีที่แนะนำ: ใช้ dataset packs
ดูรายละเอียดที่ [dataset_packs/README.md](../dataset_packs/README.md)

สั่งใช้งานแบบเร็ว:
- ไปที่โฟลเดอร์ `dataset_packs/`
- รัน `.\use_pack.bat 03_data_1pct build`

## Backend / detection จะเปลี่ยนตาม dataset ไหม?
ตามโค้ดปัจจุบัน backend เรียก SUMO ด้วย `Pathumwan/osm.sumocfg` ตลอด เมื่อ route file เปลี่ยน:
- จำนวน/การกระจายรถใน TraCI เปลี่ยน
- endpoint ที่นับรถ/เรนเดอร์กล้อง/ทำ detection จะเห็นสภาพจราจรใหม่ตามนั้น

หมายเหตุ: pipeline ปัจจุบันสร้าง route หลักเป็น `osm.dataset.rou.xml.gz` และปิดการสร้าง `osm.dataset.rou.alt.xml` เพื่อให้ไฟล์เล็กลงและลดความสับสน

หมายเหตุ: ถ้ามีการเปลี่ยน `osm.net.xml` (โครงข่ายถนน) ต้อง regenerate routes ใหม่เสมอ เพราะ route เดิมอาจไม่ตรงกับ net ใหม่
