"""
SUMO Traffic Signal Environment — Gymnasium-compatible for RL training.

Observation: per-junction state vector (queue, wait, speed, phase, etc.)
Action: next phase index for each controlled junction
Reward: negative waiting time + throughput bonus
"""

import numpy as np
from typing import TYPE_CHECKING, Any

try:
    import gymnasium as gym  # type: ignore[reportMissingImports]
    from gymnasium import spaces  # type: ignore[reportMissingImports]
except ImportError:
    import gym  # type: ignore[reportMissingImports]
    from gym import spaces  # type: ignore[reportMissingImports]

if TYPE_CHECKING:
    class GymEnv:
        metadata: dict[str, list[str]]

        def __init__(self, *args, **kwargs) -> None: ...

        def reset(self, *args, **kwargs) -> Any: ...
else:
    GymEnv = gym.Env

from ai.config import AIConfig
from ai.pipeline import build_pipeline_snapshot, snapshot_to_observation


def _to_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


class SumoTrafficEnv(GymEnv):
    """
    Gymnasium environment wrapping SUMO for traffic signal RL.

    Usage:
        env = SumoTrafficEnv(junction_ids=["j1", "j2"])
        obs, info = env.reset()
        while not done:
            action = agent.predict(obs)
            obs, reward, terminated, truncated, info = env.step(action)
    """

    metadata = {"render_modes": ["human"]}

    def __init__(self, junction_ids=None, sumo_cmd=None, render_mode=None):
        super().__init__()
        self.render_mode = render_mode
        self.sumo_cmd = sumo_cmd
        self.junction_ids = junction_ids or []
        self.n_junctions = len(self.junction_ids)
        self.current_step = 0
        self.traci = None

        # Determine number of phases per junction (set during reset)
        self._n_phases = {}  # junction_id -> num_phases
        self._current_phase = {}
        self._phase_duration = {}

        # State: 7 features per junction (see AIConfig.STATE_FEATURES)
        n_features = len(AIConfig.STATE_FEATURES)
        obs_size = self.n_junctions * n_features
        self.observation_space = spaces.Box(
            low=0.0, high=1.0, shape=(obs_size,), dtype=np.float32
        )

        # Action: multi-discrete — one phase choice per junction
        # Placeholder; actual sizes set in reset()
        self.action_space = spaces.MultiDiscrete([4] * self.n_junctions)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.current_step = 0

        # Start SUMO
        import traci as traci_module
        self.traci = traci_module

        if self.sumo_cmd:
            try:
                self.traci.close()
            except Exception:
                pass
            self.traci.start(self.sumo_cmd)

        # Discover phases
        for jid in self.junction_ids:
            try:
                programs = self.traci.trafficlight.getAllProgramLogics(jid)
                if programs:
                    self._n_phases[jid] = len(programs[0].phases)
                else:
                    self._n_phases[jid] = 4
            except Exception:
                self._n_phases[jid] = 4
            self._current_phase[jid] = 0
            self._phase_duration[jid] = 0

        # Update action space with actual phase counts
        self.action_space = spaces.MultiDiscrete(
            [self._n_phases.get(jid, 4) for jid in self.junction_ids]
        )

        obs = self._get_observation()
        info = {"step": 0}
        return obs, info

    def step(self, action):
        """Execute one AI decision (may be multiple SUMO steps)."""
        traci = self.traci
        if traci is None:
            obs = np.zeros(self.observation_space.shape, dtype=np.float32)
            return obs, 0.0, False, True, {"step": self.current_step, "error": "traci not initialized"}

        # Apply actions — set signal phases
        for i, jid in enumerate(self.junction_ids):
            phase_idx = int(action[i]) if i < len(action) else 0
            n_phases = self._n_phases.get(jid, 4)
            phase_idx = phase_idx % n_phases
            try:
                traci.trafficlight.setPhase(jid, phase_idx)
                self._current_phase[jid] = phase_idx
                self._phase_duration[jid] = 0
            except Exception:
                pass

        # Run ACTION_INTERVAL simulation steps
        total_wait_before = self._total_waiting_time()
        vehicles_before = set(traci.vehicle.getIDList())

        for _ in range(AIConfig.ACTION_INTERVAL):
            traci.simulationStep()
            self.current_step += 1
            for jid in self.junction_ids:
                self._phase_duration[jid] += 1

        total_wait_after = self._total_waiting_time()
        vehicles_after = set(traci.vehicle.getIDList())

        # Calculate reward
        reward = self._compute_reward(
            total_wait_before, total_wait_after,
            vehicles_before, vehicles_after,
        )

        obs = self._get_observation()
        terminated = False
        truncated = self.current_step >= AIConfig.MAX_EPISODE_STEPS
        info = {
            "step": self.current_step,
            "total_waiting_time": total_wait_after,
            "vehicle_count": len(vehicles_after),
        }

        return obs, reward, terminated, truncated, info

    def _get_observation(self):
        """Build normalized observation vector via the shared pipeline contract."""
        if self.traci is None:
            return np.zeros(self.observation_space.shape, dtype=np.float32)
        snapshot = build_pipeline_snapshot(
            self.traci,
            self.junction_ids,
            phase_duration_map=self._phase_duration,
        )
        return snapshot_to_observation(snapshot)

    def _total_waiting_time(self):
        """Sum waiting time across all vehicles."""
        total = 0.0
        traci = self.traci
        if traci is None:
            return total
        try:
            for vid in traci.vehicle.getIDList():
                total += _to_float(traci.vehicle.getWaitingTime(vid))
        except Exception:
            pass
        return total

    def _compute_reward(self, wait_before, wait_after, veh_before, veh_after):
        """Compute reward signal."""
        w = AIConfig.REWARD_WEIGHTS
        traci = self.traci
        if traci is None:
            return 0.0

        # Waiting time reduction (positive if waiting decreased)
        wait_delta = wait_before - wait_after
        r_wait = w["waiting_time"] * (wait_after / max(1, len(veh_after) or 1))

        # Throughput: vehicles that left the network
        departed = len(veh_before - veh_after)
        r_throughput = w["throughput"] * departed

        # Average speed
        speeds = []
        try:
            for vid in traci.vehicle.getIDList():
                speeds.append(_to_float(traci.vehicle.getSpeed(vid)))
        except Exception:
            pass
        avg_spd = np.mean(speeds) if speeds else 0.0
        r_speed = w["avg_speed"] * (avg_spd / 13.89)  # Normalize by 50 km/h

        # Queue penalty
        total_queue = 0
        for jid in self.junction_ids:
            try:
                lanes = list(set(traci.trafficlight.getControlledLanes(jid)))
                total_queue += sum(_to_float(traci.lane.getLastStepHaltingNumber(l)) for l in lanes)
            except Exception:
                pass
        r_queue = w["queue_length"] * (total_queue / max(1, self.n_junctions))

        # Emergency penalty for extreme waits
        r_emergency = 0
        try:
            for vid in traci.vehicle.getIDList():
                if _to_float(traci.vehicle.getWaitingTime(vid)) > 120:
                    r_emergency += w["emergency_penalty"]
        except Exception:
            pass

        return r_wait + r_throughput + r_speed + r_queue + r_emergency

    def close(self):
        traci = self.traci
        if traci is None:
            return
        try:
            traci.close()
        except Exception:
            pass

    def render(self):
        pass  # SUMO GUI handles rendering
