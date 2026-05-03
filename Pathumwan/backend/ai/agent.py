"""
RL Agent — PPO/DQN wrapper for traffic signal optimization.
Uses Stable-Baselines3 when available, falls back to smart rule-based agent.
"""

import os
from importlib import import_module
import numpy as np
import gymnasium as gym

from ai.config import AIConfig


def _load_sb3_class(name):
    """Load Stable-Baselines3 symbols lazily so optional installs don't break import-time analysis."""
    module = import_module("stable_baselines3")
    return getattr(module, name)


class FlattenActionWrapper(gym.ActionWrapper):
    """Wraps MultiDiscrete action space to Discrete for DQN compatibility."""
    def __init__(self, env):
        super().__init__(env)
        if isinstance(env.action_space, gym.spaces.MultiDiscrete):
            self.nvec = env.action_space.nvec
            # DQN output will be a single integer, which we convert back to MultiDiscrete
            self.action_space = gym.spaces.Discrete(int(np.prod(self.nvec)))
        else:
            self.nvec = None

    def action(self, act):
        if self.nvec is None:
            return act
        res = []
        for n in reversed(self.nvec):
            res.append(act % n)
            act //= n
        return np.array(list(reversed(res)))


class TrafficAgent:
    """RL Agent for traffic signal control."""

    def __init__(self, env=None, algorithm=None):
        self.env = env
        self.algorithm = algorithm or AIConfig.ALGORITHM
        self.model = None

    def train(self, total_timesteps=None, callback=None):
        """Train the agent using Stable-Baselines3."""
        total_timesteps = total_timesteps or AIConfig.TOTAL_TIMESTEPS

        try:
            if self.algorithm == "PPO":
                PPO = _load_sb3_class("PPO")
                self.model = PPO(
                    "MlpPolicy",
                    self.env,
                    learning_rate=AIConfig.LEARNING_RATE,
                    gamma=AIConfig.GAMMA,
                    gae_lambda=AIConfig.GAE_LAMBDA,
                    clip_range=AIConfig.CLIP_RANGE,
                    n_steps=AIConfig.N_STEPS,
                    batch_size=AIConfig.BATCH_SIZE,
                    n_epochs=AIConfig.N_EPOCHS,
                    ent_coef=AIConfig.ENTROPY_COEF,
                    vf_coef=AIConfig.VF_COEF,
                    max_grad_norm=AIConfig.MAX_GRAD_NORM,
                    verbose=1,
                    tensorboard_log=None,  # Set to AIConfig.LOG_DIR if tensorboard installed
                    device="cpu",
                )
            elif self.algorithm == "DQN":
                DQN = _load_sb3_class("DQN")

                # DQN only supports Discrete action space, but env uses MultiDiscrete.
                # Wrap the env if it has a MultiDiscrete space.
                wrapped_env = self.env
                if hasattr(self.env, "action_space") and isinstance(self.env.action_space, gym.spaces.MultiDiscrete):
                    print("ℹ Wrapping MultiDiscrete action space to Discrete for DQN.")
                    wrapped_env = FlattenActionWrapper(self.env)

                total_ts = total_timesteps or AIConfig.TOTAL_TIMESTEPS
                self.model = DQN(
                    "MlpPolicy",
                    wrapped_env,
                    learning_rate=AIConfig.LEARNING_RATE,
                    gamma=AIConfig.GAMMA,
                    batch_size=AIConfig.BATCH_SIZE,
                    # Key fix: start learning after 1000 steps (not 50,000 default)
                    learning_starts=1_000,
                    # Buffer size: keep reasonable to avoid OOM
                    buffer_size=min(50_000, total_ts),
                    # Exploration: start fully random, decay to 5% by 50% of training
                    exploration_initial_eps=1.0,
                    exploration_final_eps=0.05,
                    exploration_fraction=0.5,
                    # Update target network every 500 steps
                    target_update_interval=500,
                    # Train every 4 steps (not every step) — 4x faster than train_freq=1
                    # Standard DQN practice: collect a few transitions before updating
                    train_freq=4,
                    gradient_steps=1,
                    verbose=1,
                    tensorboard_log=None,
                    device="cpu",
                )
            elif self.algorithm == "A2C":
                A2C = _load_sb3_class("A2C")
                self.model = A2C(
                    "MlpPolicy",
                    self.env,
                    learning_rate=AIConfig.LEARNING_RATE,
                    gamma=AIConfig.GAMMA,
                    verbose=1,
                    tensorboard_log=None,
                    device="cpu",
                )
            elif self.algorithm == "RULE_BASED":
                print("✓ Rule-based agent does not require training.")
                self.model = None
                return True
            elif self.algorithm == "FIXED_TIME":
                print("✓ Fixed-time baseline does not require training.")
                self.model = None
                return True
            else:
                raise ValueError(f"Unknown algorithm: {self.algorithm}")
        except ImportError:
            print("⚠ stable-baselines3 not installed. Run: pip install stable-baselines3")
            return False

        try:
            self.model.learn(total_timesteps=total_timesteps, callback=callback)
            print(f"✓ Training complete ({total_timesteps} steps)")
            return True
        except Exception as e:
            print(f"⚠ Training error: {e}")
            import traceback
            traceback.print_exc()
            return False

    def predict(self, observation):
        """Predict action from observation.

        For DQN, the model outputs a flat scalar (from FlattenActionWrapper).
        This method converts it back to a MultiDiscrete array before returning,
        so env.step() always receives the expected array format.
        """
        if self.model is not None:
            action, _ = self.model.predict(observation, deterministic=True)

            # DQN returns a flat integer — convert back to MultiDiscrete array
            if self.algorithm == "DQN" and hasattr(self.env, "action_space"):
                import numpy as np
                import gymnasium as gym
                env_space = self.env.action_space
                if isinstance(env_space, gym.spaces.MultiDiscrete):
                    nvec = env_space.nvec
                    flat = int(action)
                    res = []
                    for n in reversed(nvec):
                        res.append(flat % n)
                        flat //= n
                    action = np.array(list(reversed(res)), dtype=np.int64)

            return action

        # Fixed-time baseline
        if self.algorithm == "FIXED_TIME":
            return self._fixed_time_action(observation)

        # Fallback: rule-based action
        return self._rule_based_action(observation)

    def _rule_based_action(self, observation):
        """Smart rule-based fallback when no trained model.
        
        Strategy:
        - For each junction, look at queue_length and phase_duration.
        - If the current phase has been active too long AND queue is high,
          advance to the next phase to give other directions green time.
        - Otherwise, hold the current phase.
        """
        n_features = len(AIConfig.STATE_FEATURES)
        n_junctions = len(observation) // n_features if n_features > 0 else 1
        actions = []

        for j in range(n_junctions):
            offset = j * n_features
            if offset + n_features > len(observation):
                actions.append(0)
                continue

            queue_norm = observation[offset]          # queue_length (0-1)
            wait_norm = observation[offset + 1]       # waiting_time (0-1)
            phase_norm = observation[offset + 4]      # current_phase (0-1)
            phase_dur_norm = observation[offset + 5]  # phase_duration (0-1)

            # Reconstruct approximate phase index (reverse of normalization)
            # phase_norm = current_phase / max(1, phase_count - 1)
            # Assume 4 phases as default
            n_phases = 4
            current_phase_idx = round(phase_norm * max(1, n_phases - 1))

            # Decision logic:
            # 1. If phase has run for a long time (> 50% of max = 30s)
            #    AND there's significant queuing → switch to next phase
            # 2. If waiting time is very high → force switch
            # 3. Otherwise → hold current phase
            should_switch = False
            if phase_dur_norm > 0.5 and queue_norm > 0.3:
                should_switch = True
            if wait_norm > 0.6:
                should_switch = True

            if should_switch:
                next_phase = (current_phase_idx + 1) % n_phases
                actions.append(next_phase)
            else:
                actions.append(current_phase_idx)

        return np.array(actions)

    def _fixed_time_action(self, observation):
        """Fixed-time baseline: cycle through phases with equal green time.

        Strategy:
        - Each phase gets FIXED_GREEN_TIME seconds of green.
        - When phase_duration exceeds the threshold, advance to next phase.
        - Otherwise, hold the current phase.
        - This mimics traditional timer-based traffic lights with no adaptation.
        """
        n_features = len(AIConfig.STATE_FEATURES)
        n_junctions = len(observation) // n_features if n_features > 0 else 1
        actions = []

        # Convert fixed green time to normalized threshold
        # phase_duration is normalized by MAX_GREEN_TIME in the observation
        green_threshold = AIConfig.FIXED_GREEN_TIME / max(1, AIConfig.MAX_GREEN_TIME)

        for j in range(n_junctions):
            offset = j * n_features
            if offset + n_features > len(observation):
                actions.append(0)
                continue

            phase_norm = observation[offset + 4]      # current_phase (0-1)
            phase_dur_norm = observation[offset + 5]  # phase_duration (0-1)

            # Reconstruct approximate phase index
            n_phases = 4
            current_phase_idx = round(phase_norm * max(1, n_phases - 1))

            # Simple rule: if green has been on >= FIXED_GREEN_TIME, switch
            if phase_dur_norm >= green_threshold:
                next_phase = (current_phase_idx + 1) % n_phases
                actions.append(next_phase)
            else:
                actions.append(current_phase_idx)

        return np.array(actions)

    def save(self, path=None):
        """Save trained model and companion metadata JSON."""
        if self.model is None:
            print("No model to save")
            return
        path = path or os.path.join(AIConfig.MODEL_DIR, f"{self.algorithm.lower()}_traffic")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.model.save(path)

        # Save companion metadata so benchmark can recreate the exact same env
        import json
        meta = {
            "algorithm": self.algorithm,
            "junction_ids": list(getattr(self.env, "junction_ids", [])),
            "n_junctions": getattr(self.env, "n_junctions", 0),
            "obs_shape": list(self.env.observation_space.shape),
            "action_nvec": [int(n) for n in self.env.action_space.nvec]
                if hasattr(self.env.action_space, "nvec") else [],
        }
        meta_path = path + "_meta.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        print(f"✓ Model saved to {path}.zip")
        print(f"✓ Metadata saved to {meta_path}")

    def load(self, path=None):
        """Load a trained model."""
        if self.algorithm in ("RULE_BASED", "FIXED_TIME"):
            self.model = None
            print(f"✓ Loaded {self.algorithm} agent.")
            return True

        path = path or os.path.join(AIConfig.MODEL_DIR, f"{self.algorithm.lower()}_traffic")
        try:
            if self.algorithm == "PPO":
                PPO = _load_sb3_class("PPO")
                self.model = PPO.load(path, env=self.env)
            elif self.algorithm == "DQN":
                DQN = _load_sb3_class("DQN")
                wrapped_env = self.env
                if hasattr(self.env, "action_space") and isinstance(self.env.action_space, gym.spaces.MultiDiscrete):
                    wrapped_env = FlattenActionWrapper(self.env)
                self.model = DQN.load(path, env=wrapped_env)
            elif self.algorithm == "A2C":
                A2C = _load_sb3_class("A2C")
                self.model = A2C.load(path, env=self.env)
            print(f"✓ Model loaded from {path}")
            return True
        except Exception as e:
            print(f"⚠ Failed to load model for {self.algorithm}: {e}")
            self.model = None
            return False

    @staticmethod
    def load_metadata(algorithm, model_dir=None):
        """Load companion metadata JSON for a trained model. Returns dict or None."""
        import json
        model_dir = model_dir or AIConfig.MODEL_DIR
        meta_path = os.path.join(model_dir, f"{algorithm.lower()}_traffic_meta.json")
        if not os.path.exists(meta_path):
            return None
        with open(meta_path, encoding="utf-8") as f:
            return json.load(f)
