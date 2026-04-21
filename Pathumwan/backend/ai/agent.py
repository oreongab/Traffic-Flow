"""
RL Agent — PPO/DQN wrapper for traffic signal optimization.
Uses Stable-Baselines3 when available, falls back to simple rule-based agent.
"""

import os
from importlib import import_module
import numpy as np
from ai.config import AIConfig


def _load_sb3_class(name):
    """Load Stable-Baselines3 symbols lazily so optional installs don't break import-time analysis."""
    module = import_module("stable_baselines3")
    return getattr(module, name)


class TrafficAgent:
    """RL Agent for traffic signal control."""

    def __init__(self, env=None, algorithm=None):
        self.env = env
        self.algorithm = algorithm or AIConfig.ALGORITHM
        self.model = None

    def train(self, total_timesteps=None):
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
                    max_grad_norm=AIConfig.MAX_GRAD_NORM,
                    verbose=1,
                    tensorboard_log=AIConfig.LOG_DIR,
                )
            elif self.algorithm == "DQN":
                DQN = _load_sb3_class("DQN")
                self.model = DQN(
                    "MlpPolicy",
                    self.env,
                    learning_rate=AIConfig.LEARNING_RATE,
                    gamma=AIConfig.GAMMA,
                    batch_size=AIConfig.BATCH_SIZE,
                    verbose=1,
                    tensorboard_log=AIConfig.LOG_DIR,
                )
            else:
                raise ValueError(f"Unknown algorithm: {self.algorithm}")

            self.model.learn(total_timesteps=total_timesteps)
            print(f"✓ Training complete ({total_timesteps} steps)")
            return True

        except ImportError:
            print("⚠ stable-baselines3 not installed. Run: pip install stable-baselines3")
            return False

    def predict(self, observation):
        """Predict action from observation."""
        if self.model is not None:
            action, _ = self.model.predict(observation, deterministic=True)
            return action

        # Fallback: rule-based action (choose phase with longest queue)
        return self._rule_based_action(observation)

    def _rule_based_action(self, observation):
        """Simple rule-based fallback when no trained model."""
        n_features = len(AIConfig.STATE_FEATURES)
        n_junctions = len(observation) // n_features if n_features > 0 else 1
        actions = []
        for j in range(n_junctions):
            offset = j * n_features
            queue = observation[offset] if offset < len(observation) else 0
            # If queue is high, switch phase
            current_phase = observation[offset + 4] if offset + 4 < len(observation) else 0
            phase_dur = observation[offset + 5] if offset + 5 < len(observation) else 0

            if phase_dur > 0.5 and queue > 0.3:  # Phase been active too long with high queue
                actions.append(int(current_phase * 3 + 1) % 4)
            else:
                actions.append(int(current_phase * 3) % 4)

        return np.array(actions)

    def save(self, path=None):
        """Save trained model."""
        if self.model is None:
            print("No model to save")
            return
        path = path or os.path.join(AIConfig.MODEL_DIR, f"{self.algorithm.lower()}_traffic")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.model.save(path)
        print(f"✓ Model saved to {path}")

    def load(self, path=None):
        """Load a trained model."""
        path = path or os.path.join(AIConfig.MODEL_DIR, f"{self.algorithm.lower()}_traffic")
        try:
            if self.algorithm == "PPO":
                PPO = _load_sb3_class("PPO")
                self.model = PPO.load(path, env=self.env)
            elif self.algorithm == "DQN":
                DQN = _load_sb3_class("DQN")
                self.model = DQN.load(path, env=self.env)
            print(f"✓ Model loaded from {path}")
            return True
        except Exception as e:
            print(f"⚠ Failed to load model: {e}")
            return False
