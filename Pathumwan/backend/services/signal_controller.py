"""Source-agnostic signal control facade for sim and future real controllers."""

from __future__ import annotations

import time
from threading import Lock
from typing import Any

from config import Config
from services.live_state import get_latest_junction_state

_mode_lock = Lock()
_signal_mode = "manual"

# Most recent AI decision per junction so /admin/ai-status can surface it.
_ai_decision_lock = Lock()
_ai_last_decisions: dict[str, dict[str, Any]] = {}


def get_ai_last_decisions() -> list[dict[str, Any]]:
    with _ai_decision_lock:
        return list(_ai_last_decisions.values())


def _record_ai_decisions(decisions: list[dict[str, Any]]) -> None:
    if not decisions:
        return
    now = time.time()
    with _ai_decision_lock:
        for d in decisions:
            jid = str(d.get("junction_id") or "")
            if not jid:
                continue
            _ai_last_decisions[jid] = {**d, "timestamp": now}


def get_signal_mode() -> str:
    with _mode_lock:
        return _signal_mode


def set_signal_mode(mode: str) -> str:
    normalized = str(mode or "manual").strip().lower()
    if normalized not in {"ai", "manual"}:
        raise ValueError("Mode must be 'ai' or 'manual'")
    with _mode_lock:
        global _signal_mode
        _signal_mode = normalized
    return normalized


def get_runtime_backends() -> dict[str, str]:
    return {
        "system_mode": Config.SYSTEM_MODE,
        "signal_backend": Config.SIGNAL_BACKEND,
        "camera_backend": Config.CAMERA_BACKEND,
        "ai_backend": Config.AI_BACKEND,
        "signal_mode": get_signal_mode(),
    }


class SimSignalController:
    """Adapter that preserves existing TraCI-based manual/signal behavior."""

    def __init__(self, simulation_module: Any):
        self.simulation = simulation_module

    def is_available(self) -> bool:
        return bool(self.simulation and getattr(self.simulation, "sim_active", False))

    def get_status(self) -> dict[str, Any]:
        simulation = self.simulation
        return {
            "mode": get_signal_mode(),
            "active": bool(simulation and getattr(simulation, "sim_active", False)),
            "simulation_active": bool(simulation and getattr(simulation, "sim_active", False)),
            "step": int(getattr(simulation, "step", 0) or 0),
            "camera_count": len(getattr(simulation, "camera_points", []) or []) if simulation else 0,
            "backends": get_runtime_backends(),
        }

    def get_junction_states(self) -> list[dict[str, object]]:
        junctions = get_latest_junction_state()
        if junctions:
            return junctions
        return []

    def validate_action(self, plan: dict[str, Any]) -> bool:
        junction_id = str(plan.get("junction_id") or "")
        target_phase = plan.get("target_phase")
        return bool(junction_id) and isinstance(target_phase, int)

    def set_manual_color(self, junction_id: str, color: str, direction: str = "all") -> str:
        """Override the TLS state for a junction.

        direction:
          - "all"  → every controlled lane gets ``color`` (legacy behaviour)
          - "ns"/"ew" → only the lanes whose heading falls in the
            corresponding axis are flipped; the remaining lanes keep their
            current state. Useful for "เขียวเฉพาะแนวเหนือ-ใต้".
          - "n"/"e"/"s"/"w" → only the lanes whose approach heading is in
            that 90° quadrant are flipped. Pairs nicely with the
            sequential-4 program (Issue 7) so each direction can be held
            green independently of the others.

        Falls back to "all" if direction filtering can't determine lane
        headings (e.g. sumolib unavailable) so the legacy contract is
        preserved.
        """
        if not self.is_available():
            raise RuntimeError("Simulation ยังไม่พร้อม")
        traci = self.simulation.get_traci()
        state_map = {"red": "r", "yellow": "y", "green": "G"}
        char = state_map[color]
        with self.simulation.sim_lock:
            current = traci.trafficlight.getRedYellowGreenState(junction_id)
            if direction == "all":
                new_state = char * len(current)
            else:
                mask = self._direction_mask(traci, junction_id, direction, len(current))
                if mask is None:
                    new_state = char * len(current)
                else:
                    new_state = "".join(
                        char if mask[i] else current[i] for i in range(len(current))
                    )
            traci.trafficlight.setRedYellowGreenState(junction_id, new_state)
        return new_state

    @staticmethod
    def _direction_mask(traci: Any, junction_id: str, direction: str, length: int) -> list[bool] | None:
        """Return a boolean mask (len == TLS state length) selecting lanes
        whose heading matches ``direction``. Returns None when we can't infer.
        """
        try:
            from sumolib.net import readNet  # type: ignore[import-not-found]
        except Exception:
            return None
        try:
            from config import Config as _Cfg
            net = readNet(_Cfg.SUMO_NET_FILE)
        except Exception:
            return None

        try:
            controlled = traci.trafficlight.getControlledLanes(junction_id)
        except Exception:
            return None
        if len(controlled) != length:
            # SUMO sometimes pads state with extra signal indices; bail safely.
            return None

        def _heading_deg(lane_id: str) -> float | None:
            try:
                lane = net.getLane(lane_id)
                edge = lane.getEdge()
                from_node = edge.getFromNode()
                to_node = edge.getToNode()
                fx, fy = from_node.getCoord()
                tx, ty = to_node.getCoord()
                import math as _math
                return (_math.degrees(_math.atan2(ty - fy, tx - fx)) + 360.0) % 360.0
            except Exception:
                return None

        def _matches(heading: float) -> bool:
            if direction == "n":
                return 45.0 <= heading < 135.0
            if direction == "e":
                return heading < 45.0 or heading >= 315.0
            if direction == "s":
                return 225.0 <= heading < 315.0
            if direction == "w":
                return 135.0 <= heading < 225.0
            if direction == "ns":
                return (45.0 <= heading < 135.0) or (225.0 <= heading < 315.0)
            if direction == "ew":
                return (heading < 45.0 or heading >= 315.0) or (135.0 <= heading < 225.0)
            return False

        mask: list[bool] = []
        for lane_id in controlled:
            h = _heading_deg(lane_id)
            mask.append(False if h is None else _matches(h))
        if not any(mask):
            return None
        return mask

    def set_phase_plan(self, junction_id: str, phase_durations: list[dict[str, Any]]) -> None:
        if not self.is_available():
            raise RuntimeError("Simulation ยังไม่พร้อม")
        traci = self.simulation.get_traci()
        with self.simulation.sim_lock:
            programs = traci.trafficlight.getAllProgramLogics(junction_id)
            if not programs:
                raise LookupError("ไม่พบ program logic")
            logic = programs[0]
            new_phases = []
            for index, phase in enumerate(logic.phases):
                duration = phase.duration
                for item in phase_durations:
                    if int(item.get("index", -1)) == index:
                        duration = max(3, min(120, float(item.get("duration", duration))))
                        break
                new_phases.append(
                    traci.trafficlight.Phase(
                        duration=duration,
                        state=phase.state,
                        minDur=duration * 0.6,
                        maxDur=duration * 1.4,
                    )
                )
            new_logic = traci.trafficlight.Logic(
                programID="manual_edit",
                type=0,
                currentPhaseIndex=0,
                phases=new_phases,
            )
            traci.trafficlight.setProgramLogic(junction_id, new_logic)
            traci.trafficlight.setProgram(junction_id, "manual_edit")

    def apply_ai_actions(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.is_available():
            raise RuntimeError("Simulation ยังไม่พร้อม")
        traci = self.simulation.get_traci()
        applied: list[dict[str, Any]] = []
        with self.simulation.sim_lock:
            for action in actions:
                if not self.validate_action(action):
                    continue
                junction_id = str(action.get("junction_id") or "")
                target_phase = int(action.get("target_phase") or 0)
                try:
                    programs = traci.trafficlight.getAllProgramLogics(junction_id)
                    phase_count = len(programs[0].phases) if programs else 1
                    target_phase = target_phase % max(1, phase_count)
                    traci.trafficlight.setPhase(junction_id, target_phase)
                    applied.append({
                        "junction_id": junction_id,
                        "target_phase": target_phase,
                        "applied": True,
                    })
                except Exception as exc:
                    applied.append({
                        "junction_id": junction_id,
                        "target_phase": target_phase,
                        "applied": False,
                        "error": str(exc),
                    })
        return applied


class RealSignalController:
    """Placeholder adapter for future real-world signal control."""

    def is_available(self) -> bool:
        return False

    def get_status(self) -> dict[str, Any]:
        return {
            "mode": get_signal_mode(),
            "active": False,
            "simulation_active": False,
            "step": 0,
            "camera_count": 0,
            "backends": get_runtime_backends(),
            "message": "Real signal controller is not implemented yet",
        }

    def get_junction_states(self) -> list[dict[str, object]]:
        return get_latest_junction_state()

    def validate_action(self, plan: dict[str, Any]) -> bool:
        junction_id = str(plan.get("junction_id") or "")
        target_phase = plan.get("target_phase")
        return bool(junction_id) and isinstance(target_phase, int)

    def set_manual_color(self, junction_id: str, color: str, direction: str = "all") -> str:
        raise NotImplementedError("Real signal controller is not implemented yet")

    def set_phase_plan(self, junction_id: str, phase_durations: list[dict[str, Any]]) -> None:
        raise NotImplementedError("Real signal controller is not implemented yet")

    def apply_ai_actions(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise NotImplementedError("Real signal controller is not implemented yet")


def get_signal_controller(simulation_module: Any = None) -> Any:
    if Config.SIGNAL_BACKEND == "controller" or Config.SYSTEM_MODE == "real":
        return RealSignalController()
    return SimSignalController(simulation_module)


def decide_ai_actions(simulation_module: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compute (actions, decisions) for AI mode.

    For each TLS junction with active SUMO state, scores each phase by the
    sum of YOLO vehicle counts on cameras whose lanes are GREEN in that
    phase, and picks the highest-scoring phase. Returns:
      - actions: list[{junction_id, target_phase}] — fed to apply_ai_actions
      - decisions: list[{junction_id, phase, score, cars}] — surfaced to UI

    No-ops (returns empty) when sim isn't active or no detections exist.
    """
    if not simulation_module or not getattr(simulation_module, "sim_active", False):
        return [], []

    try:
        from services.live_state import get_latest_approach_metrics
        from services.density import get_camera_road_map
    except Exception:
        return [], []

    metrics = get_latest_approach_metrics() or []
    if not metrics:
        return [], []

    cam_road_map = get_camera_road_map() or {}
    road_mapping = getattr(simulation_module, "road_mapping", {}) or {}

    by_junction: dict[str, list[dict[str, Any]]] = {}
    for m in metrics:
        jid = str(m.get("junction_id") or "")
        if jid:
            by_junction.setdefault(jid, []).append(m)
    if not by_junction:
        return [], []

    actions: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []

    try:
        traci = simulation_module.get_traci()
    except Exception:
        return [], []

    sim_lock = getattr(simulation_module, "sim_lock", None)
    if sim_lock is None:
        return [], []

    with sim_lock:
        for jid, cam_metrics in by_junction.items():
            try:
                controlled_lanes = traci.trafficlight.getControlledLanes(jid)
                programs = traci.trafficlight.getAllProgramLogics(jid)
            except Exception:
                continue
            if not programs or not programs[0].phases:
                continue
            logic = programs[0]

            # Map camera_id -> indices of controlled lanes that belong to its road.
            cam_lane_idx: dict[str, list[int]] = {}
            for m in cam_metrics:
                cid = str(m.get("camera_id") or "")
                if not cid:
                    continue
                rid = cam_road_map.get(cid) or str(m.get("road_id") or "")
                if not rid:
                    continue
                edges = set(road_mapping.get(rid, []) or [])
                if not edges:
                    continue
                indices: list[int] = []
                for idx, lane in enumerate(controlled_lanes):
                    edge_id = str(lane).rsplit("_", 1)[0]
                    if edge_id in edges:
                        indices.append(idx)
                if indices:
                    cam_lane_idx[cid] = indices
            if not cam_lane_idx:
                continue

            best_phase = 0
            best_score = -1.0
            for phase_idx, phase in enumerate(logic.phases):
                state = phase.state or ""
                # Skip yellow/all-red transition phases — never let AI park on those.
                upper = state.upper()
                if "G" not in upper:
                    continue
                score = 0.0
                for m in cam_metrics:
                    cid = str(m.get("camera_id") or "")
                    indices = cam_lane_idx.get(cid)
                    if not indices:
                        continue
                    if any(i < len(state) and state[i] in ("G", "g") for i in indices):
                        score += float(m.get("vehicle_count") or 0)
                if score > best_score:
                    best_score = score
                    best_phase = phase_idx

            if best_score <= 0:
                continue

            actions.append({"junction_id": jid, "target_phase": best_phase})
            decisions.append({
                "junction_id": jid,
                "phase": best_phase,
                "score": best_score,
                "cars": int(best_score),
                "cameras": len(cam_metrics),
            })

    if decisions:
        _record_ai_decisions(decisions)
    return actions, decisions