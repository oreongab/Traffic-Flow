FROM ubuntu:22.04

# ป้องกันไม่ให้ apt ถามคำถามระหว่างลง
ENV DEBIAN_FRONTEND=noninteractive

# ติดตั้ง SUMO เวอร์ชันล่าสุดจาก PPA อย่างเป็นทางการ เพื่อให้รองรับไฟล์ Network (.net.xml) รุ่นใหม่ๆ
RUN apt-get update && apt-get install -y software-properties-common python3-pip && \
    add-apt-repository ppa:sumo/stable -y && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    sumo \
    sumo-tools \
    libgl1 \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ตั้งค่า python3 ให้เป็นคำสั่ง python หลัก
RUN ln -s /usr/bin/python3 /usr/bin/python || true

ENV SUMO_HOME=/usr/share/sumo

WORKDIR /app

COPY backend/requirements.txt ./backend/
RUN pip3 install --no-cache-dir -r backend/requirements.txt

COPY . /app/
WORKDIR /app/backend

CMD ["python3", "app.py"]
