"""Source-agnostic signal control facade for sim and future real controllers."""

from __future__ import annotations

import time
from threading import Lock
from typing import Any

from config import Config
from services.live_state import get_latest_junction_state

_mode_lock = Lock()
_signal_mode = "manual"
_active_ai_algorithm = Config.AI_ALGORITHM
_runtime_state_loaded = False

# Most recent AI decision per junction so /admin/ai-status can surface it.
_ai_decision_lock = Lock()
_ai_last_decisions: dict[str, dict[str, Any]] = {}

# Recent manual overrides / phase plans, kept in memory instead of persisting
# to an audit table that the runtime no longer depends on.
_manual_override_lock = Lock()
_manual_overrides: dict[str, dict[str, Any]] = {}
_MANUAL_OVERRIDE_TTL_SECONDS = 600.0


def _load_runtime_state_if_needed() -> None:
    global _runtime_state_loaded, _signal_mode, _active_ai_algorithm
    if _runtime_state_loaded:
        return

    try:
        from database.connection import get_session
        from database.models import RuntimeConfig

        session = get_session()
        try:
            rows = session.query(RuntimeConfig).filter(
                RuntimeConfig.config_key.in_(["signal_mode", "active_ai_algorithm"])
            ).all()
            for row in rows:
                key = str(row.config_key or "")
                value = str(row.config_value or "").strip()
                if key == "signal_mode" and value in {"ai", "manual"}:
                    _signal_mode = value
                if key == "active_ai_algorithm" and value:
                    _active_ai_algorithm = value.upper()
        finally:
            session.close()
    except Exception:
        pass

    _runtime_state_loaded = True


def _persist_runtime_value(key: str, value: str) -> None:
    try:
        from database.connection import get_session
        from database.models import RuntimeConfig

        session = get_session()
        try:
            row = session.query(RuntimeConfig).filter(RuntimeConfig.config_key == key).first()
            if row is None:
                session.add(RuntimeConfig(config_key=key, config_value=value))
            else:
                row.config_value = value
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()
    except Exception:
        pass


def record_manual_override(
    junction_id: str,
    *,
    color: str | None = None,
    direction: str = "all",
    phase_durations: list[dict[str, Any]] | None = None,
) -> None:
    normalized_junction_id = str(junction_id or "").strip()
    if not normalized_junction_id:
        return

    payload: dict[str, Any] = {
        "updated_at": time.time(),
        "direction": str(direction or "all").strip().lower() or "all",
    }
    if color:
        payload["color"] = str(color).strip().lower()
    if phase_durations is not None:
        payload["phase_durations"] = [
            {
                "index": int(item.get("index", 0)),
                "duration": float(item.get("duration", 0)),
            }
            for item in phase_durations
            if isinstance(item, dict) and "index" in item and "duration" in item
        ]

    with _manual_override_lock:
        _manual_overrides[normalized_junction_id] = payload


def get_recent_manual_overrides(ttl_seconds: float = _MANUAL_OVERRIDE_TTL_SECONDS) -> dict[str, dict[str, Any]]:
    now = time.time()
    active: dict[str, dict[str, Any]] = {}
    expired: list[str] = []

    with _manual_override_lock:
        for junction_id, payload in _manual_overrides.items():
            updated_at = float(payload.get("updated_at") or 0.0)
            if updated_at <= 0 or (now - updated_at) > ttl_seconds:
                expired.append(junction_id)
                continue
            active[junction_id] = {
                **payload,
                "phase_durations": [dict(item) for item in payload.get("phase_durations", []) or []],
            }

        for junction_id in expired:
            _manual_overrides.pop(junction_id, None)

    return active


def clear_manual_overrides(junction_id: str | None = None) -> None:
    with _manual_override_lock:
        if junction_id is None:
            _manual_overrides.clear()
            return
        _manual_overrides.pop(str(junction_id or "").strip(), None)


def get_ai_last_decisions() -> list[dict[str, Any]]:
    with _ai_decision_lock:
        return sorted(
            _ai_last_decisions.values(),
            key=lambda item: float(item.get("timestamp") or 0.0),
            reverse=True,
        )


def record_ai_decisions(decisions: list[dict[str, Any]]) -> None:
    if not decisions:
        return

    try:
        from services.ai_logger import log_decision_if_changed
    except Exception:
        log_decision_if_changed = None

    now = time.time()
    with _ai_decision_lock:
        for d in decisions:
            jid = str(d.get("junction_id") or "")
            if not jid:
                continue
            decision = {**d, "timestamp": now}
            _ai_last_decisions[jid] = decision

            if log_decision_if_changed is None:
                continue

            try:
                phase = int(d.get("phase") if d.get("phase") is not None else d.get("target_phase") or 0)
                log_decision_if_changed(
                    junction_id=jid,
                    phase=phase,
                    input_data={
                        "queue_length": float(d.get("queue_length") or 0.0),
                        "waiting_time": float(d.get("waiting_time") or 0.0),
                        "avg_speed_kmh": float(d.get("avg_speed_kmh") or 0.0),
                        "cars": int(d.get("cars") or 0),
                        "cameras": int(d.get("cameras") or 0),
                        "current_phase": int(d.get("current_phase") or 0),
                    },
                    output=decision,
                    model_version=str(d.get("algorithm") or d.get("method") or ""),
                )
            except Exception:
                continue

def get_signal_mode() -> str:
    with _mode_lock:
        _load_runtime_state_if_needed()
        return _signal_mode

def set_signal_mode(mode: str) -> str:
    normalized = str(mode or "manual").strip().lower()
    if normalized not in {"ai", "manual"}:
        raise ValueError("Mode must be 'ai' or 'manual'")
    with _mode_lock:
        _load_runtime_state_if_needed()
        global _signal_mode
        _signal_mode = normalized
    if normalized == "ai":
        clear_manual_overrides()
    _persist_runtime_value("signal_mode", normalized)
    return normalized

def get_active_ai_algorithm() -> str:
    with _mode_lock:
        _load_runtime_state_if_needed()
        return _active_ai_algorithm

def set_active_ai_algorithm(algorithm: str) -> str:
    normalized = str(algorithm).strip().upper()
    valid_algorithms = {"PPO", "DQN", "A2C", "RULE_BASED"}
    if normalized not in valid_algorithms:
        raise ValueError(f"Algorithm must be one of {valid_algorithms}")
    with _mode_lock:
        _load_runtime_state_if_needed()
        global _active_ai_algorithm
        _active_ai_algorithm = normalized
    _persist_runtime_value("active_ai_algorithm", normalized)
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
            raise RuntimeError("Runtime ควบคุมสัญญาณยังไม่พร้อม")
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
            raise RuntimeError("Runtime ควบคุมสัญญาณยังไม่พร้อม")
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
            raise RuntimeError("Runtime ควบคุมสัญญาณยังไม่พร้อม")
        traci = self.simulation.get_traci()
        applied: list[dict[str, Any]] = []
        junctions_changing = []

        with self.simulation.sim_lock:
            for action in actions:
                if not self.validate_action(action):
                    continue
                junction_id = str(action.get("junction_id") or "")
                raw_target = int(action.get("target_phase") or 0)
                
                try:
                    programs = traci.trafficlight.getAllProgramLogics(junction_id)
                    if not programs:
                        continue
                    logic = programs[0]
                    
                    # Map AI action to actual green phase
                    green_phases = [i for i, p in enumerate(logic.phases) if "G" in p.state or "g" in p.state]
                    if green_phases:
                        target_phase = green_phases[raw_target % len(green_phases)]
                    else:
                        target_phase = raw_target % max(1, len(logic.phases))
                    
                    current_phase = traci.trafficlight.getPhase(junction_id)
                    
                    if current_phase != target_phase:
                        # Set yellow state using direct state manipulation
                        current_state = traci.trafficlight.getRedYellowGreenState(junction_id)
                        yellow_state = current_state.replace("G", "y").replace("g", "y")
                        traci.trafficlight.setProgram(junction_id, logic.programID)
                        traci.trafficlight.setRedYellowGreenState(junction_id, yellow_state)
                        
                        junctions_changing.append({
                            "junction_id": junction_id,
                            "raw_target": raw_target,
                            "target_phase": target_phase,
                            "program_id": logic.programID
                        })
                    else:
                        applied.append({
                            "junction_id": junction_id,
                            "target_phase": raw_target,
                            "applied": True,
                        })
                except Exception as exc:
                    applied.append({
                        "junction_id": junction_id,
                        "target_phase": raw_target,
                        "applied": False,
                        "error": str(exc),
                    })

        # Wait for yellow light duration (3 seconds) outside of the lock
        # so we don't freeze the simulator or other threads!
        if junctions_changing:
            time.sleep(3.0)

        # Apply target green phases
        if junctions_changing:
            with self.simulation.sim_lock:
                for change in junctions_changing:
                    jid = change["junction_id"]
                    try:
                        traci.trafficlight.setProgram(jid, change["program_id"])
                        traci.trafficlight.setPhase(jid, change["target_phase"])
                        applied.append({
                            "junction_id": jid,
                            "target_phase": change["raw_target"],
                            "applied": True,
                        })
                    except Exception as exc:
                        applied.append({
                            "junction_id": jid,
                            "target_phase": change["raw_target"],
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
