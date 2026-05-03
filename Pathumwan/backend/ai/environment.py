"""
SUMO Traffic Signal Environment — Gymnasium-compatible for RL training.

Observation: per-junction state vector (queue, wait, speed, phase, etc.)
Action: next phase index for each controlled junction
Reward: weighted combination from reward.py (waiting time, throughput, speed, queue)
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
from ai.reward import combined_reward


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
        self._program_ids = {}
        self._phase_states = {}  # junction_id -> list of phase state strings (cached)
        self._controlled_lanes = {}  # junction_id -> set of lane ids (cached)

        # Tracking for metrics
        self._episode_total_wait = 0.0
        self._episode_throughput = 0
        self._episode_reward = 0.0

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
        self._episode_total_wait = 0.0
        self._episode_throughput = 0
        self._episode_reward = 0.0

        # Start SUMO with a unique label to avoid port conflicts
        try:
            import libsumo as traci_module
            # libsumo does not need switch/label
            traci_module.start(self.sumo_cmd)
        except Exception as e:
            print(f"⚠ Falling back to traci. libsumo import failed: {e}")
            import traci as traci_module
            self._traci_label = f"training_{id(self)}"
            if self.sumo_cmd:
                # Close previous connection if any
                try:
                    traci_module.switch(self._traci_label)
                    traci_module.close()
                except Exception:
                    pass
                traci_module.start(self.sumo_cmd, label=self._traci_label)
                traci_module.switch(self._traci_label)

        self.traci = traci_module

        # Discover and reprogram phases to Thai-style (one direction at a time)
        for jid in self.junction_ids:
            self._reprogram_thai_style(jid)
            self._current_phase[jid] = 0
            self._phase_duration[jid] = 0

            # Cache controlled lanes per junction
            try:
                self._controlled_lanes[jid] = set(
                    self.traci.trafficlight.getControlledLanes(jid)
                )
            except Exception:
                self._controlled_lanes[jid] = set()

        # Update action space with actual phase counts (minimum 2)
        self.action_space = spaces.MultiDiscrete(
            [max(2, self._n_phases.get(jid, 4)) for jid in self.junction_ids]
        )

        obs = self._get_observation()
        info = {"step": 0}
        return obs, info

    def _reprogram_thai_style(self, jid):
        """Reprogram a junction's traffic light to Thai-style: one direction green at a time.

        Thai traffic signals typically give green to only ONE incoming direction,
        not opposing directions simultaneously. This method:
        1. Groups signal indices by incoming edge (direction)
        2. Creates one green phase per direction (all others red)
        3. Adds yellow transition phases between them
        """
        traci = self.traci
        try:
            controlled_links = traci.trafficlight.getControlledLinks(jid)
            programs = traci.trafficlight.getAllProgramLogics(jid)
            if not programs or not controlled_links:
                self._n_phases[jid] = 4
                self._program_ids[jid] = "0"
                self._phase_states[jid] = []
                return

            # Get the total number of signal indices (= length of phase state string)
            n_signals = len(programs[0].phases[0].state)

            # Group signal indices by incoming edge (= direction)
            edge_groups = {}  # edge_id -> list of signal indices
            for idx, link_group in enumerate(controlled_links):
                if idx >= n_signals:
                    break
                for link in link_group:
                    if not link:
                        continue
                    in_lane = str(link[0])
                    # Extract edge ID from lane ID (e.g., "edge_123_0" -> "edge_123")
                    edge_id = "_".join(in_lane.rsplit("_", 1)[:-1]) if "_" in in_lane else in_lane
                    if edge_id not in edge_groups:
                        edge_groups[edge_id] = set()
                    edge_groups[edge_id].add(idx)

            if not edge_groups:
                self._n_phases[jid] = max(2, len(programs[0].phases))
                self._program_ids[jid] = programs[0].programID
                self._phase_states[jid] = [p.state for p in programs[0].phases]
                return

            # Build Thai-style phases: one direction green at a time
            import traci.constants as tc
            try:
                from sumolib.net import TLSProgram
            except ImportError:
                TLSProgram = None

            direction_list = sorted(edge_groups.keys())
            new_phases = []
            green_states = []

            for edge_id in direction_list:
                indices = edge_groups[edge_id]
                # Green phase: this direction = G, all others = r
                state = list("r" * n_signals)
                for idx in indices:
                    state[idx] = "G"
                green_state = "".join(state)
                green_states.append(green_state)

                # Yellow phase: this direction = y, all others = r
                yellow_state = green_state.replace("G", "y").replace("g", "y")

                # Add green phase (default 30s duration, AI will override)
                new_phases.append((green_state, AIConfig.MAX_GREEN_TIME))
                # Add yellow transition
                new_phases.append((yellow_state, AIConfig.YELLOW_TIME))

            # Apply the new program via TraCI
            program = programs[0]
            logic_phases = []
            for state_str, duration in new_phases:
                # Create Phase objects compatible with TraCI
                logic_phases.append(
                    traci.trafficlight.Phase(
                        duration=duration,
                        state=state_str,
                        minDur=duration,
                        maxDur=duration,
                    )
                )

            new_program_id = "thai_style"
            logic = traci.trafficlight.Logic(
                programID=new_program_id,
                type=0,
                currentPhaseIndex=0,
                phases=logic_phases,
            )
            traci.trafficlight.setProgramLogic(jid, logic)
            traci.trafficlight.setProgram(jid, new_program_id)

            # Only count green phases (not yellow) as AI-selectable actions
            n_green = len(direction_list)
            self._n_phases[jid] = max(2, n_green)
            self._program_ids[jid] = new_program_id
            self._phase_states[jid] = green_states  # Only green states for yellow transition logic
            self._thai_phase_mapping = getattr(self, '_thai_phase_mapping', {})
            # Map AI action index -> actual phase index in the program (skip yellow phases)
            self._thai_phase_mapping[jid] = [i * 2 for i in range(n_green)]

            print(f"  ✓ {jid}: reprogrammed to {n_green}-direction Thai-style "
                  f"({len(new_phases)} phases total incl. yellow)")

        except Exception as e:
            print(f"  ⚠ {jid}: could not reprogram, using default phases ({e})")
            try:
                programs = traci.trafficlight.getAllProgramLogics(jid)
                if programs and programs[0].phases:
                    self._n_phases[jid] = max(2, len(programs[0].phases))
                    self._program_ids[jid] = programs[0].programID
                    self._phase_states[jid] = [p.state for p in programs[0].phases]
                else:
                    self._n_phases[jid] = 4
                    self._program_ids[jid] = "0"
                    self._phase_states[jid] = []
            except Exception:
                self._n_phases[jid] = 4
                self._program_ids[jid] = "0"
                self._phase_states[jid] = []

    def step(self, action):
        """Execute one AI decision (may be multiple SUMO steps)."""
        traci = self.traci
        if traci is None:
            obs = np.zeros(self.observation_space.shape, dtype=np.float32)
            return obs, 0.0, False, True, {"step": self.current_step, "error": "traci not initialized"}

        # Record old phases for phase-switch penalty
        old_phases = [self._current_phase.get(jid, 0) for jid in self.junction_ids]

        # Apply actions — set signal phases with yellow transition
        new_phases = []
        junctions_changing = []

        thai_map = getattr(self, '_thai_phase_mapping', {})

        for i, jid in enumerate(self.junction_ids):
            phase_idx = int(action[i]) if i < len(action) else 0
            n_phases = self._n_phases.get(jid, 4)
            phase_idx = phase_idx % n_phases
            new_phases.append(phase_idx)

            current = self._current_phase.get(jid, 0)
            if phase_idx != current:
                junctions_changing.append((jid, phase_idx))
                # Set yellow state for this junction
                cached_states = self._phase_states.get(jid, [])
                if cached_states and current < len(cached_states):
                    current_state = cached_states[current]
                    yellow_state = current_state.replace("G", "y").replace("g", "y")
                    try:
                        traci.trafficlight.setRedYellowGreenState(jid, yellow_state)
                    except Exception:
                        pass

        # Run yellow steps ONCE for ALL junctions simultaneously
        if junctions_changing:
            for _ in range(AIConfig.YELLOW_TIME):
                traci.simulationStep()
                self.current_step += 1

            # Now set all target phases after yellow completes
            for jid, phase_idx in junctions_changing:
                try:
                    if jid in self._program_ids:
                        traci.trafficlight.setProgram(jid, self._program_ids[jid])
                    # Map AI action to actual SUMO phase index (skip yellow phases)
                    actual_phase = phase_idx
                    if jid in thai_map and phase_idx < len(thai_map[jid]):
                        actual_phase = thai_map[jid][phase_idx]
                    traci.trafficlight.setPhase(jid, actual_phase)
                    self._phase_duration[jid] = 0
                except Exception:
                    pass

        # Update current phases for all junctions
        for i, jid in enumerate(self.junction_ids):
            phase_idx = int(action[i]) if i < len(action) else 0
            n_phases = self._n_phases.get(jid, 4)
            self._current_phase[jid] = phase_idx % n_phases

        # Track throughput using arrived count (no expensive getIDList)
        arrived_count = 0

        # Run ACTION_INTERVAL simulation steps (green phase)
        for _ in range(AIConfig.ACTION_INTERVAL):
            traci.simulationStep()
            self.current_step += 1
            arrived_count += traci.simulation.getArrivedNumber()
            for jid in self.junction_ids:
                self._phase_duration[jid] += 1

        # Calculate reward using the unified reward module
        # Pass cached controlled lanes to avoid redundant TraCI calls
        reward = combined_reward(
            traci, self.junction_ids,
            arrived_count,
            old_phases=old_phases, new_phases=new_phases,
            controlled_lanes_cache=self._controlled_lanes,
        )

        # Track episode metrics
        self._episode_reward += reward
        self._episode_throughput += arrived_count
        self._episode_total_wait += self._total_waiting_time()  # accumulate, not snapshot

        # Get current vehicle count cheaply
        try:
            current_vehicle_count = traci.vehicle.getIDCount()
        except Exception:
            current_vehicle_count = 0

        obs = self._get_observation()
        terminated = False
        truncated = self.current_step >= AIConfig.MAX_EPISODE_STEPS
        info = {
            "step": self.current_step,
            "total_waiting_time": self._episode_total_wait,
            "vehicle_count": current_vehicle_count,
            "episode_throughput": self._episode_throughput,
            "episode_reward": self._episode_reward,
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
        """Sum waiting time across all controlled lanes at this step.
        
        Called every step and accumulated into _episode_total_wait,
        so the episode total reflects the integrated waiting time over the full episode,
        not just a snapshot at the end.
        """
        total = 0.0
        traci = self.traci
        if traci is None:
            return total
        try:
            for jid in self.junction_ids:
                for lane in self._controlled_lanes.get(jid, set()):
                    total += _to_float(traci.lane.getWaitingTime(lane))
        except Exception:
            pass
        return total

    def close(self):
        traci = self.traci
        if traci is None:
            return
        try:
            if hasattr(self, '_traci_label'):
                traci.switch(self._traci_label)
            traci.close()
        except Exception:
            pass

    def render(self):
        pass  # SUMO GUI handles rendering
