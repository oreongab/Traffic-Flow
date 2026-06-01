"""
AI Predictor — Loads trained model and provides signal decisions at runtime.
Used by the admin route to apply AI-controlled signal timing.
"""

import os
import numpy as np
from ai.config import AIConfig
from ai.agent import TrafficAgent
from ai.pipeline import build_runtime_pipeline_snapshot, snapshot_to_observation


class TrafficPredictor:
    """Loads a trained RL model and predicts optimal signal phases."""

    def __init__(self, junction_ids=None):
        self.junction_ids = junction_ids or []
        self.agent = TrafficAgent(algorithm=AIConfig.ALGORITHM)
        self.loaded = False

    def load_model(self):
        """Attempt to load a trained model."""
        meta = TrafficAgent.load_metadata(AIConfig.ALGORITHM)
        if meta and meta.get("junction_ids"):
            self.junction_ids = [
                str(junction_id)
                for junction_id in meta["junction_ids"]
                if str(junction_id or "")
            ]

        model_path = os.path.join(
            AIConfig.MODEL_DIR,
            f"{AIConfig.ALGORITHM.lower()}_traffic",
        )
        if os.path.exists(model_path + ".zip"):
            self.loaded = self.agent.load(model_path)
        else:
            print(f"⚠ No trained model at {model_path}. Using rule-based fallback.")
            self.loaded = False
        return self.loaded

    def get_actions(self, observation):
        """Get signal phase actions for all junctions."""
        if not isinstance(observation, np.ndarray):
            observation = np.array(observation, dtype=np.float32)
        return self.agent.predict(observation)

    def decide_signals(self, traci_module=None, junction_ids=None):
        """
        High-level method: read current SUMO state, predict, and apply signal changes.
        Returns list of decisions for logging.
        """
        jids = junction_ids or self.junction_ids
        if not jids:
            return []

        # Build observation
        obs = self._build_observation(traci_module, jids)
        actions = self.get_actions(obs)

        decisions = []
        for i, jid in enumerate(jids):
            phase = int(actions[i]) if i < len(actions) else 0
            if traci_module is None:
                decisions.append({
                    "junction_id": jid,
                    "phase": phase,
                    "method": "trained_model" if self.loaded else "rule_based",
                    "applied": False,
                })
                continue
            try:
                n_phases = len(traci_module.trafficlight.getAllProgramLogics(jid)[0].phases)
                phase = phase % n_phases
                traci_module.trafficlight.setPhase(jid, phase)

                decision = {
                    "junction_id": jid,
                    "phase": phase,
                    "method": "trained_model" if self.loaded else "rule_based",
                    "applied": True,
                }
                decisions.append(decision)

                # Log to DB only when phase actually changes
                try:
                    from services.ai_logger import log_decision_if_changed
                    log_decision_if_changed(
                        junction_id=jid,
                        phase=phase,
                        input_data={"obs_len": len(obs)},
                        output=decision,
                        model_version=AIConfig.ALGORITHM if self.loaded else "rule_based",
                    )
                except Exception:
                    pass

            except Exception as e:
                decisions.append({
                    "junction_id": jid,
                    "error": str(e),
                })

        return decisions

    def _build_observation(self, traci, jids):
        """Build observation vector from unified runtime state."""
        snapshot = build_runtime_pipeline_snapshot(traci, jids)
        return snapshot_to_observation(snapshot)
