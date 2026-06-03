"""
PostgreSQL ORM Models — TraffixFlow (SQLAlchemy)
16 tables — updated 2026-04-19
(16 active model tables + backward-compatible aliases)
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text, JSON,
    Index, ForeignKey, ForeignKeyConstraint, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def _utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False)
    role = Column(String(20), default="user", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Camera(Base):
    __tablename__ = "cameras"

    id = Column(Integer, primary_key=True, autoincrement=True)
    camera_id = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    road = Column(String(255), default="")
    road_id = Column(String(100), ForeignKey("roads.road_id", ondelete="SET NULL"), index=True)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    junction = Column(String(255), default="")
    junction_id = Column(String(255), ForeignKey("junctions.junction_id", ondelete="SET NULL"), index=True)
    sumo_tls_id = Column(String(255), default="")
    status = Column(String(20), default="active")
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    # ── merged from camera_streams ──
    stream_url = Column(Text, default="")
    stream_type = Column(String(20), default="sim")          # "sim" | "rtsp" | "http"
    stream_enabled = Column(Boolean, default=True)
    fps_target = Column(Integer, default=8)
    last_frame_at = Column(DateTime(timezone=True), index=True)
    stream_status = Column(String(20), default="offline")    # "online" | "offline" | "error"

    # ── merged from camera_calibrations (JSON blob) ──
    calibration_data = Column(JSON, default=dict)  # {image_width, image_height, homography_matrix, anchor_lat, anchor_lng, bearing_deg, pixels_per_meter}

    # ── merged from camera_zones (JSON list) ──
    zones = Column(JSON, default=list)  # [{zone_id, junction_id, approach_id, road_id, zone_type, polygon_json, line_json, enabled}]


class Road(Base):
    __tablename__ = "roads"

    road_id = Column(String(100), primary_key=True)
    road_name = Column(String(255), nullable=False)
    free_flow_speed_kmh = Column(Float, default=50.0)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Junction(Base):
    __tablename__ = "junctions"

    junction_id = Column(String(255), primary_key=True)
    junction_name = Column(String(255), default="")
    sumo_tls_id = Column(String(255), unique=True, index=True)
    lat = Column(Float)
    lng = Column(Float)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Approach(Base):
    __tablename__ = "approaches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    junction_id = Column(
        String(255),
        ForeignKey("junctions.junction_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    approach_id = Column(String(100), nullable=False, index=True)
    road_id = Column(String(100), ForeignKey("roads.road_id", ondelete="SET NULL"), index=True)
    camera_id = Column(String(100), ForeignKey("cameras.camera_id", ondelete="SET NULL"), index=True)
    approach_name = Column(String(255), default="")
    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("junction_id", "approach_id", name="uq_approaches_junction_approach"),
        Index("ix_approach_junction_road", "junction_id", "road_id"),
    )


# ── Backward-compatible aliases (removed tables) ──────────────────
# CameraStream, CameraCalibration, CameraZone columns are now on Camera.
# These aliases let old imports work without immediate breakage.
CameraStream = Camera
CameraCalibration = Camera
CameraZone = Camera


class TrafficDetection(Base):
    __tablename__ = "traffic_detections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    camera_id = Column(
        String(100),
        ForeignKey("cameras.camera_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    timestamp = Column(DateTime(timezone=True), default=_utcnow, index=True)
    vehicle_counts = Column(JSON, default=dict)
    edge_id = Column(String(100), default="")
    confidence_avg = Column(Float, default=0.0)

    __table_args__ = (
        Index("ix_detection_cam_ts", "camera_id", timestamp.desc()),
    )


class TrafficIndex(Base):
    __tablename__ = "traffic_index"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), default=_utcnow, index=True)
    area = Column(String(100), nullable=False, index=True)
    index_value = Column(Float, nullable=False)
    roads = Column(JSON, default=list)

    __table_args__ = (
        Index("ix_ti_area_ts", "area", timestamp.desc()),
    )


class RoadDensity(Base):
    __tablename__ = "road_density"

    id = Column(Integer, primary_key=True, autoincrement=True)
    road_name = Column(String(255), nullable=False)
    road_id = Column(String(100), ForeignKey("roads.road_id"), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), default=_utcnow, index=True)
    vehicle_count = Column(Integer, default=0)
    density_level = Column(String(50), default="")
    avg_speed = Column(Float, default=0.0)
    travel_time = Column(Float, default=0.0)
    vc_ratio = Column(Float, default=0.0)

    __table_args__ = (
        Index("ix_rd_road_ts", "road_id", timestamp.desc()),
    )


class SignalTiming(Base):
    __tablename__ = "signal_timings"
    __table_args__ = {"info": {"optional_runtime_table": True}}

    id = Column(Integer, primary_key=True, autoincrement=True)
    junction_id = Column(String(255), ForeignKey("junctions.junction_id"), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), default=_utcnow)
    phase_durations = Column(JSON, default=list)
    mode = Column(String(20), default="ai")
    decided_by = Column(String(100), default="system")


class SignalController(Base):
    __tablename__ = "signal_controllers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    junction_id = Column(String(255), ForeignKey("junctions.junction_id"), nullable=False, index=True)
    controller_type = Column(String(30), default="mock")
    endpoint = Column(Text, default="")
    auth_config = Column(JSON, default=dict)
    enabled = Column(Boolean, default=True, nullable=False)
    phase_map = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class SignalState(Base):
    __tablename__ = "signal_states"

    id = Column(Integer, primary_key=True, autoincrement=True)
    junction_id = Column(String(255), ForeignKey("junctions.junction_id"), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), default=_utcnow, index=True)
    current_phase = Column(Integer, default=0)
    phase_count = Column(Integer, default=0)
    phase_duration = Column(Float, default=0.0)
    next_switch_eta = Column(Float, default=0.0)
    source = Column(String(20), default="sim")
    raw_state = Column(JSON, default=dict)

    __table_args__ = (
        Index("ix_signal_state_junction_ts", "junction_id", "timestamp"),
    )


class AIDecision(Base):
    __tablename__ = "ai_decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    junction_id = Column(String(255), ForeignKey("junctions.junction_id"), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), default=_utcnow)
    input_data = Column(JSON, default=dict)
    output = Column(JSON, default=dict)
    reward = Column(Float, default=0.0)
    model_version = Column(String(50), default="")


class HistoricalStats(Base):
    __tablename__ = "historical_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(20), nullable=False, index=True)
    year = Column(Integer, nullable=False, index=True)
    total_vehicles = Column(Integer, default=0)
    peak_index = Column(Float, default=0.0)
    peak_time = Column(String(10), default="")
    avg_index = Column(Float, default=0.0)
    road_rankings = Column(JSON, default=list)


class SystemLog(Base):
    __tablename__ = "system_logs"
    __table_args__ = {"info": {"optional_runtime_table": True}}

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), default=_utcnow, index=True)
    event_type = Column(String(100), nullable=False, index=True)
    details = Column(JSON, default=dict)


class HourlyVehicleCount(Base):
    """Hourly aggregated vehicle counts per road from YOLO detection."""
    __tablename__ = "hourly_vehicle_counts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False, index=True)        # "2026-04-11"
    hour = Column(String(5), nullable=False)                      # "08:00"
    road_id = Column(String(100), ForeignKey("roads.road_id"), nullable=False, index=True)
    road_name = Column(String(255), default="")
    car = Column(Integer, default=0)
    motorcycle = Column(Integer, default=0)
    bus = Column(Integer, default=0)
    truck = Column(Integer, default=0)
    total = Column(Integer, default=0)
    avg_speed = Column(Float, default=0.0)
    density_index = Column(Float, default=0.0)
    source = Column(String(20), default="yolo")                   # "yolo" or "sumo"
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_hvc_date_road", "date", "road_id"),
        Index("ix_hvc_date_hour", "date", "hour"),
    )


# ── Backward-compatible aliases (removed tables) ──────────────────
# LiveVehicleTrack and LiveApproachMetric are removed.
# Tracker data should be kept in-memory only (not persisted per-frame).
LiveVehicleTrack = None  # type: ignore[assignment]
LiveApproachMetric = None  # type: ignore[assignment]


class RuntimeConfig(Base):
    __tablename__ = "runtime_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    config_key = Column(String(100), unique=True, nullable=False, index=True)
    config_value = Column(Text, default="")
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
