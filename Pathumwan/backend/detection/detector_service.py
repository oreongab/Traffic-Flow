"""
Detection Service — Periodically runs YOLO detection on rendered CCTV frames
and stores vehicle counts to the database.

Uses the new renderer-based CCTV frames (from cctv_renderer.py) instead of
SUMO-GUI screenshots.  Also falls back to TraCI-based proximity counting
when YOLO is unavailable.
"""

import time
from config import Config


def start_detection_loop():
    """Main detection loop — runs in a background thread."""
    import simulation
    from cctv_renderer import count_vehicles_near_camera, DEFAULT_RADIUS

    # Try to load YOLO detector (reuses the pre-warmed singleton if bootstrap loaded it)
    detector = None
    try:
        from detection.yolo_detector import get_shared_detector
        detector = get_shared_detector()
    except ImportError:
        pass

    if detector:
        print(f"✓ YOLO detection loop started (interval={Config.DETECTION_INTERVAL}s)")
    else:
        print(f"✓ TraCI detection loop started — YOLO not available (interval={Config.DETECTION_INTERVAL}s)")

    interval = Config.DETECTION_INTERVAL
    last_saved_counts: dict[str, dict] = {}
    last_saved_ts: dict[str, float] = {}
    # Heartbeat: even when counts are unchanged, re-save at least this often so
    # downstream freshness checks (STALE_THRESHOLD_SECONDS) don't drop us.
    HEARTBEAT_SECONDS = max(30.0, float(Config.STALE_THRESHOLD_SECONDS) * 0.75)

    while True:
        if not simulation.sim_active:
            time.sleep(2)
            continue

        cameras = simulation.camera_points
        if not cameras:
            time.sleep(interval)
            continue

        now_ts = time.time()
        for cam in cameras:
            cam_id = str(cam.get("camera_id", cam.get("id", "")))
            try:
                if detector:
                    # Render a CCTV frame and run YOLO on it
                    frame_bytes = simulation.capture_cctv_frame(cam_id, show_detection=False)
                    if not frame_bytes:
                        continue
                    detections = detector.detect(frame_bytes)
                    counts = {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0}
                    confidence_values = []
                    for detection in detections:
                        vehicle_class = str(detection.get("class") or "")
                        if vehicle_class in counts:
                            counts[vehicle_class] += 1
                        confidence_values.append(float(detection.get("confidence") or 0.0))
                    confidence_avg = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0

                    # In sim mode YOLO can momentarily miss crowded vehicles on the
                    # rendered top-down view. Keep SUMO proximity counts as a floor
                    # so downstream realtime/historical metrics are not poisoned by
                    # zero-only detection snapshots.
                    with simulation.sim_lock:
                        sim_counts = count_vehicles_near_camera(
                            simulation.get_traci(), cam, DEFAULT_RADIUS,
                        )
                    for key in ("car", "motorcycle", "bus", "truck", "total"):
                        counts[key] = max(int(counts.get(key, 0) or 0), int(sim_counts.get(key, 0) or 0))
                else:
                    # Use TraCI proximity counting (no YOLO needed)
                    with simulation.sim_lock:
                        counts = count_vehicles_near_camera(
                            simulation.get_traci(), cam, DEFAULT_RADIUS,
                        )
                    confidence_avg = 0.0

                # Normalize counts.
                raw = counts or {}
                normalized = {
                    "car": int(raw.get("car", 0) or 0),
                    "motorcycle": int(raw.get("motorcycle", 0) or 0),
                    "bus": int(raw.get("bus", 0) or 0),
                    "truck": int(raw.get("truck", 0) or 0),
                }
                normalized["total"] = int(raw.get("total", sum(normalized.values())) or 0)

                prev = last_saved_counts.get(cam_id)
                prev_ts = last_saved_ts.get(cam_id, 0.0)
                # Skip only if counts unchanged AND heartbeat window not elapsed.
                if prev == normalized and (now_ts - prev_ts) < HEARTBEAT_SECONDS:
                    continue
                _save_detection(cam, normalized, confidence_avg)
                last_saved_counts[cam_id] = normalized
                last_saved_ts[cam_id] = now_ts

            except Exception as e:
                print(f"  ⚠ Detection error [{cam_id}]: {type(e).__name__}: {e}")
                continue

        time.sleep(interval)


def _save_detection(cam, counts, confidence_avg):
    """Save detection counts to the database."""
    try:
        from database.connection import get_session
        from database.models import TrafficDetection

        cam_id = str(cam.get("camera_id", cam.get("id", "")))
        edge_id = str(cam.get("edge_id") or cam.get("road") or "")
        session = get_session()
        try:
            row = TrafficDetection(
                camera_id=cam_id,
                vehicle_counts=counts,
                edge_id=edge_id,
                confidence_avg=float(confidence_avg),
            )
            session.add(row)
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"  ⚠ Detection DB save failed [{cam_id}]: {type(e).__name__}: {e}")
        finally:
            session.close()
    except Exception as e:
        print(f"  ⚠ Detection save setup error: {type(e).__name__}: {e}")
