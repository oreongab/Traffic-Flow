FROM ubuntu:22.04

# ป้องกันไม่ให้ apt ถามคำถามระหว่างลง
ENV DEBIAN_FRONTEND=noninteractive

# Build-time toggle: set WITH_CUDA=1 to swap PyTorch CPU wheels for CUDA 12.1
# wheels. Default 0 keeps the image friendly to laptops without NVIDIA GPUs
# (e.g. Intel Iris Xe). Teammates with NVIDIA boxes / Lightning AI T4 build
# with `--build-arg WITH_CUDA=1` and run with the `gpu` compose profile.
ARG WITH_CUDA=0

# ── System packages ──
# ลง python + lib ที่จำเป็นสำหรับ OpenCV; SUMO มาจาก pip (ดูขั้นตอนถัดไป)
# เพื่อให้เวอร์ชันตรงกับ netconvert ที่สร้าง osm.net.xml (1.26.0)
RUN set -eux \
 && echo 'Acquire::Retries "3"; Acquire::http::Timeout "60"; Acquire::https::Timeout "60";' > /etc/apt/apt.conf.d/80-retries \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates curl python3-pip \
    libatomic1 \
        libgl1 libglib2.0-0 libxrender1 libxext6 \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# ── SUMO via pip (eclipse-sumo) ──
# Eclipse SUMO เผยแพร่ binary ผ่าน PyPI ที่ version 1.26 ตรงกับ netconvert ที่
# generate osm.net.xml ของเรา — หลีกเลี่ยงปัญหา PPA ล่ม + Ubuntu repo เก่า.
# NOTE: TraCI (`traci`) และ tooling (`sumolib`) อยู่ใน `$SUMO_HOME/tools`.
# ดังนั้นเราต้อง resolve `SUMO_HOME` แล้วเติม `PYTHONPATH` ไม่งั้น import `traci`
# จะล้มใน runtime.
RUN set -eux \
 && pip3 install --no-cache-dir eclipse-sumo==1.26.0 \
 && python3 -c "import os, sumo; print(os.path.dirname(sumo.__file__))" > /tmp/sumo_home \
 && SUMO_PKG_DIR="$(cat /tmp/sumo_home)" \
 && echo "SUMO_HOME resolved to: $SUMO_PKG_DIR" \
 && test -d "$SUMO_PKG_DIR/tools" \
 && ln -sf "$SUMO_PKG_DIR" /opt/sumo \
 && sumo --version

ENV SUMO_HOME=/opt/sumo
ENV PYTHONPATH=${SUMO_HOME}/tools:${PYTHONPATH}

# ตั้งค่า python3 ให้เป็นคำสั่ง python หลัก
RUN ln -s /usr/bin/python3 /usr/bin/python || true

ENV YOLO_CONFIG_DIR=/tmp/Ultralytics

WORKDIR /app

COPY backend/requirements.txt ./backend/
RUN pip3 install --no-cache-dir -r backend/requirements.txt && \
    if [ "$WITH_CUDA" = "1" ]; then \
        echo "→ Installing CUDA 12.1 PyTorch wheels"; \
        pip3 install --no-cache-dir --upgrade \
            torch torchvision \
            --index-url https://download.pytorch.org/whl/cu121; \
    fi

COPY . /app/
WORKDIR /app/backend

CMD ["python3", "app.py"]
