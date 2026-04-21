"""Replay helpers for feeding stored real observations back into simulation/testing.

Updated: LiveApproachMetric removed — replay now uses traffic_detections data.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from database.connection import get_session
from database.models import TrafficDetection


def load_recent_replay_window(minutes: int = 15) -> list[dict[str, object]]:
    """Return recent detection data suitable for replay or offline evaluation."""
    since = datetime.now(timezone.utc) - timedelta(minutes=max(1, int(minutes)))
    session = get_session()
    try:
        rows = session.query(TrafficDetection).filter(
            TrafficDetection.timestamp >= since,
        ).order_by(TrafficDetection.timestamp.asc()).all()
        return [
            {
                "timestamp": row.timestamp,
                "camera_id": row.camera_id,
                "vehicle_counts": row.vehicle_counts or {},
                "edge_id": row.edge_id,
                "confidence_avg": row.confidence_avg,
            }
            for row in rows
        ]
    finally:
        session.close()