"""Source-agnostic signal control facade for sim and future real controllers."""

from __future__ import annotations

from threading import Lock
from typing import Any

from config import Config
from services.live_state import get_latest_junction_state

_mode_lock = Lock()
_signal_mode = "manual"
_active_ai_algorithm = "PPO"  # Default AI algorithm

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

def get_active_ai_algorithm() -> str:
    with _mode_lock:
        return _active_ai_algorithm

def set_active_ai_algorithm(algorithm: str) -> str:
    normalized = str(algorithm).strip().upper()
    valid_algorithms = {"PPO", "DQN", "A2C", "RULE_BASED"}
    if normalized not in valid_algorithms:
        raise ValueError(f"Algorithm must be one of {valid_algorithms}")
    with _mode_lock:
        global _active_ai_algorithm
        _active_ai_algorithm = normalized
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

    def set_manual_color(self, junction_id: str, color: str) -> str:
        if not self.is_available():
            raise RuntimeError("Simulation ยังไม่พร้อม")
        traci = self.simulation.get_traci()
        with self.simulation.sim_lock:
            current = traci.trafficlight.getRedYellowGreenState(junction_id)
            state_map = {"red": "r", "yellow": "y", "green": "G"}
            new_state = state_map[color] * len(current)
            traci.trafficlight.setRedYellowGreenState(junction_id, new_state)
        return new_state

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
                    if programs:
                        traci.trafficlight.setProgram(junction_id, programs[0].programID)
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

    def set_manual_color(self, junction_id: str, color: str) -> str:
        raise NotImplementedError("Real signal controller is not implemented yet")

    def set_phase_plan(self, junction_id: str, phase_durations: list[dict[str, Any]]) -> None:
        raise NotImplementedError("Real signal controller is not implemented yet")

    def apply_ai_actions(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise NotImplementedError("Real signal controller is not implemented yet")


def get_signal_controller(simulation_module: Any = None) -> Any:
    if Config.SIGNAL_BACKEND == "controller" or Config.SYSTEM_MODE == "real":
        return RealSignalController()
    return SimSignalController(simulation_module)