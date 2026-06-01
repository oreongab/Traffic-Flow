"""Check database table row counts — 17 active tables."""
from database.models import (
    User, Camera, Road, Junction, Approach,
    TrafficDetection, TrafficIndex, RoadDensity,
    SignalController, SignalState,
    AIDecision, HistoricalStats,
    HourlyVehicleCount, RuntimeConfig,
)
from database.connection import get_session


def check():
    session = get_session()
    tables = [
        (User, "users"),
        (Camera, "cameras"),
        (Road, "roads"),
        (Junction, "junctions"),
        (Approach, "approaches"),
        (TrafficDetection, "traffic_detections"),
        (TrafficIndex, "traffic_index"),
        (RoadDensity, "road_density"),
        (SignalController, "signal_controllers"),
        (SignalState, "signal_states"),
        (AIDecision, "ai_decisions"),
        (HistoricalStats, "historical_stats"),
        (HourlyVehicleCount, "hourly_vehicle_counts"),
        (RuntimeConfig, "runtime_config"),
    ]
    print(f"{'Table Name':<25} | {'Row Count':<10}")
    print("-" * 40)
    for model, name in tables:
        try:
            count = session.query(model).count()
            print(f"{name:<25} | {count}")
        except Exception as e:
            print(f"{name:<25} | ERROR: {e}")
            session.rollback()
    print("-" * 40)
    print(f"Total tables: {len(tables)}")


if __name__ == "__main__":
    check()
