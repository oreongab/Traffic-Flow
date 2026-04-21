"""Runtime feature pipeline for traffic-signal AI.

This module turns live SUMO + camera/detection state into structured feature
vectors that can be fed into the existing RL environment/predictor or exported
for supervised/offline training.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import numpy as np

from ai.config import AIConfig
from config import Config
from services.live_state import get_latest_junction_state
from services.mapping import get_junction_approach_map, get_junction_camera_map


@dataclass
class JunctionFeatures:
    junction_id: str
    queue_length: float
    waiting_time: float
    vehicle_count: float
    avg_speed_kmh: float
    current_phase: int
    phase_count: int
    phase_duration: float
    time_of_day_norm: float
    inbound_lanes: list[str]
    outbound_lanes: list[str]
    road_ids: list[str]
    camera_ids: list[str]


@dataclass
class PipelineSnapshot:
    timestamp: float
    junctions: list[JunctionFeatures]
    global_vehicle_count: int
    global_avg_speed_kmh: float


@dataclass
class SignalAction:
    junction_id: str
    target_phase: int
    min_green_seconds: int
    max_green_seconds: int
    hold_seconds: int


@dataclass
class TrainingRecord:
    observation: list[float]
    actions: list[int]
    rewards: list[float]
    metadata: dict[str, Any]


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value or "")
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _normalize_queue(queue_length: float) -> float:
    return min(max(queue_length / 50.0, 0.0), 1.0)


def _normalize_wait(waiting_time: float) -> float:
    return min(max(waiting_time / 300.0, 0.0), 1.0)


def _normalize_vehicle_count(vehicle_count: float) -> float:
    return min(max(vehicle_count / 100.0, 0.0), 1.0)


def _normalize_speed_kmh(avg_speed_kmh: float) -> float:
    return min(max(avg_speed_kmh / 50.0, 0.0), 1.0)


def _normalize_phase(current_phase: int, phase_count: int) -> float:
    return current_phase / max(1, phase_count - 1)


def _normalize_phase_duration(phase_duration: float) -> float:
    return min(max(phase_duration / 60.0, 0.0), 1.0)


def build_pipeline_snapshot(
    traci_module: Any,
    junction_ids: Sequence[str],
    *,
    junction_camera_map: dict[str, list[str]] | None = None,
    junction_road_map: dict[str, list[str]] | None = None,
    phase_duration_map: dict[str, float] | None = None,
) -> PipelineSnapshot:
    """Capture one structured live snapshot from SUMO.

    Parameters
    ----------
    traci_module:
        Live TraCI module/connection.
    junction_ids:
        The traffic-light junction ids that the AI controls.
    junction_camera_map:
        Mapping of junction_id -> camera ids observing that junction.
    junction_road_map:
        Mapping of junction_id -> road ids feeding that junction.
    phase_duration_map:
        Optional runtime phase durations tracked by the caller.
    """

    phase_duration_map = phase_duration_map or {}
    junction_camera_map = junction_camera_map or {}
    junction_road_map = junction_road_map or {}

    current_time = float(traci_module.simulation.getTime())
    time_of_day_norm = (current_time % 86400.0) / 86400.0

    global_speeds: list[float] = []
    global_vehicle_ids = set(str(vid) for vid in traci_module.vehicle.getIDList())
    junction_features: list[JunctionFeatures] = []

    for junction_id in junction_ids:
        inbound_lanes = _unique(traci_module.trafficlight.getControlledLanes(junction_id))
        outbound_lanes: list[str] = []
        try:
            controlled_links = traci_module.trafficlight.getControlledLinks(junction_id)
            for link_group in controlled_links:
                for link in link_group:
                    if not link:
                        continue
                    from_lane, to_lane = link[0], link[1]
                    if from_lane:
                        inbound_lanes.append(str(from_lane))
                    if to_lane:
                        outbound_lanes.append(str(to_lane))
        except Exception:
            pass

        inbound_lanes = _unique(inbound_lanes)
        outbound_lanes = _unique(outbound_lanes)

        queue_length = 0.0
        waiting_time = 0.0
        vehicle_count = 0.0
        lane_speeds_kmh: list[float] = []
        for lane_id in inbound_lanes:
            try:
                queue_length += float(traci_module.lane.getLastStepHaltingNumber(lane_id))
                waiting_time += float(traci_module.lane.getWaitingTime(lane_id))
                vehicle_count += float(traci_module.lane.getLastStepVehicleNumber(lane_id))
                lane_speeds_kmh.append(float(traci_module.lane.getLastStepMeanSpeed(lane_id)) * 3.6)
            except Exception:
                continue

        avg_speed_kmh = float(np.mean(lane_speeds_kmh)) if lane_speeds_kmh else 0.0
        if avg_speed_kmh > 0:
            global_speeds.extend(lane_speeds_kmh)

        current_phase = 0
        phase_count = 4
        try:
            current_phase = int(traci_module.trafficlight.getPhase(junction_id))
            programs = traci_module.trafficlight.getAllProgramLogics(junction_id)
            if programs:
                phase_count = max(1, len(programs[0].phases))
        except Exception:
            pass

        junction_features.append(
            JunctionFeatures(
                junction_id=str(junction_id),
                queue_length=queue_length,
                waiting_time=waiting_time,
                vehicle_count=vehicle_count,
                avg_speed_kmh=avg_speed_kmh,
                current_phase=current_phase,
                phase_count=phase_count,
                phase_duration=float(phase_duration_map.get(str(junction_id), 0.0)),
                time_of_day_norm=time_of_day_norm,
                inbound_lanes=inbound_lanes,
                outbound_lanes=outbound_lanes,
                road_ids=_unique(junction_road_map.get(str(junction_id), [])),
                camera_ids=_unique(junction_camera_map.get(str(junction_id), [])),
            )
        )

    return PipelineSnapshot(
        timestamp=current_time,
        junctions=junction_features,
        global_vehicle_count=len(global_vehicle_ids),
        global_avg_speed_kmh=float(np.mean(global_speeds)) if global_speeds else 0.0,
    )


def build_live_pipeline_snapshot(
    junction_ids: Sequence[str] | None = None,
    *,
    timestamp: float | None = None,
) -> PipelineSnapshot:
    """Capture one structured snapshot from the source-agnostic live-state service."""
    runtime_rows = get_latest_junction_state()
    selected_ids = {str(junction_id) for junction_id in (junction_ids or []) if str(junction_id or "")}
    if selected_ids:
        runtime_rows = [row for row in runtime_rows if str(row.get("junction_id") or "") in selected_ids]

    junction_camera_map = get_junction_camera_map()
    junction_approach_map = get_junction_approach_map()
    now_ts = float(timestamp) if timestamp is not None else datetime.now(timezone.utc).timestamp()
    time_of_day_norm = (now_ts % 86400.0) / 86400.0

    junction_features: list[JunctionFeatures] = []
    total_vehicle_count = 0
    global_speeds: list[float] = []

    for row in runtime_rows:
        junction_id = str(row.get("junction_id") or "")
        if not junction_id:
            continue

        approaches = row.get("approaches") if isinstance(row.get("approaches"), list) else []
        queue_length = float(sum(float(item.get("queue_length") or 0.0) for item in approaches if isinstance(item, dict)))
        vehicle_count = float(sum(float(item.get("vehicle_count") or 0.0) for item in approaches if isinstance(item, dict)))
        waiting_time = float(sum(float(item.get("freshness_seconds") or 0.0) for item in approaches if isinstance(item, dict)))
        speed_values = [
            float(item.get("avg_speed_kmh") or 0.0)
            for item in approaches
            if isinstance(item, dict) and float(item.get("avg_speed_kmh") or 0.0) > 0.0
        ]
        avg_speed_kmh = float(np.mean(speed_values)) if speed_values else float(row.get("avg_speed_kmh") or 0.0)
        if avg_speed_kmh > 0.0:
            global_speeds.extend(speed_values or [avg_speed_kmh])

        road_ids = [
            str(item.get("road_id") or "")
            for item in junction_approach_map.get(junction_id, [])
            if str(item.get("road_id") or "")
        ]

        total_vehicle_count += int(vehicle_count)
        junction_features.append(
            JunctionFeatures(
                junction_id=junction_id,
                queue_length=queue_length,
                waiting_time=waiting_time,
                vehicle_count=vehicle_count,
                avg_speed_kmh=avg_speed_kmh,
                current_phase=int(row.get("current_phase") or 0),
                phase_count=max(1, int(row.get("phase_count") or 1)),
                phase_duration=float(row.get("phase_duration") or 0.0),
                time_of_day_norm=time_of_day_norm,
                inbound_lanes=[],
                outbound_lanes=[],
                road_ids=_unique(road_ids),
                camera_ids=_unique(junction_camera_map.get(junction_id, [])),
            )
        )

    return PipelineSnapshot(
        timestamp=now_ts,
        junctions=junction_features,
        global_vehicle_count=total_vehicle_count,
        global_avg_speed_kmh=float(np.mean(global_speeds)) if global_speeds else 0.0,
    )


def build_runtime_pipeline_snapshot(
    traci_module: Any | None,
    junction_ids: Sequence[str],
    *,
    junction_camera_map: dict[str, list[str]] | None = None,
    junction_road_map: dict[str, list[str]] | None = None,
    phase_duration_map: dict[str, float] | None = None,
) -> PipelineSnapshot:
    """Build a unified snapshot from real/live state when enabled, otherwise SUMO."""
    if Config.SYSTEM_MODE == "real" or traci_module is None:
        return build_live_pipeline_snapshot(junction_ids=junction_ids)
    return build_pipeline_snapshot(
        traci_module,
        junction_ids,
        junction_camera_map=junction_camera_map,
        junction_road_map=junction_road_map,
        phase_duration_map=phase_duration_map,
    )


def snapshot_to_observation(snapshot: PipelineSnapshot) -> np.ndarray:
    """Convert a structured snapshot into the normalized observation vector used by AIConfig.STATE_FEATURES."""
    values: list[float] = []
    for junction in snapshot.junctions:
        values.extend(
            [
                _normalize_queue(junction.queue_length),
                _normalize_wait(junction.waiting_time),
                _normalize_vehicle_count(junction.vehicle_count),
                _normalize_speed_kmh(junction.avg_speed_kmh),
                _normalize_phase(junction.current_phase, max(1, junction.phase_count)),
                _normalize_phase_duration(junction.phase_duration),
                junction.time_of_day_norm,
            ]
        )
    return np.array(values, dtype=np.float32)


def build_signal_actions(
    junction_ids: Sequence[str],
    phase_indices: Sequence[int],
    *,
    min_green_seconds: int | None = None,
    max_green_seconds: int | None = None,
    hold_seconds: int | None = None,
) -> list[SignalAction]:
    """Translate raw model actions into a control plan that can be audited/logged."""
    min_green = int(min_green_seconds or AIConfig.MIN_GREEN_TIME)
    max_green = int(max_green_seconds or AIConfig.MAX_GREEN_TIME)
    hold = int(hold_seconds or AIConfig.ACTION_INTERVAL)

    actions: list[SignalAction] = []
    for index, junction_id in enumerate(junction_ids):
        phase = int(phase_indices[index]) if index < len(phase_indices) else 0
        actions.append(
            SignalAction(
                junction_id=str(junction_id),
                target_phase=phase,
                min_green_seconds=min_green,
                max_green_seconds=max_green,
                hold_seconds=hold,
            )
        )
    return actions


def export_training_record(
    snapshot: PipelineSnapshot,
    actions: Sequence[int],
    rewards: Sequence[float],
    *,
    episode_id: str,
    step: int,
) -> TrainingRecord:
    """Create a serializable record for offline training or dataset storage."""
    return TrainingRecord(
        observation=snapshot_to_observation(snapshot).tolist(),
        actions=[int(value) for value in actions],
        rewards=[float(value) for value in rewards],
        metadata={
            "episode_id": episode_id,
            "step": int(step),
            "timestamp": float(snapshot.timestamp),
            "junctions": [asdict(junction) for junction in snapshot.junctions],
            "global_vehicle_count": int(snapshot.global_vehicle_count),
            "global_avg_speed_kmh": float(snapshot.global_avg_speed_kmh),
            "state_features": list(AIConfig.STATE_FEATURES),
        },
    )
