"""
AI Trainer — Script to train the RL agent on SUMO environment.

The training uses REAL traffic data from Pathumwan district:
  - Vehicle routes from calibrated .rou.xml files (generated from otp_66_05.csv BMA data)
  - Traffic profile from data/pathumwan_traffic_profile.json (7 sensor stations, hourly)
  - Road network from osm.net.xml (OpenStreetMap Pathumwan extract)

Usage:
    cd backend
    python -m ai.trainer --algorithm PPO --timesteps 100000

Results are saved to:
    ai/models/              — trained model weights (.zip)
    ai/logs/                — TensorBoard training logs
    data/training_metrics_<ALGO>_<TIMESTAMP>.json — episode-level metrics for reporting
"""

import argparse
import json
import os
import sys
import time

# Fix Windows console encoding for emojis
if sys.platform == "win32":
    stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(stdout_reconfigure):
        stdout_reconfigure(encoding="utf-8", errors="replace")
    stderr_reconfigure = getattr(sys.stderr, "reconfigure", None)
    if callable(stderr_reconfigure):
        stderr_reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Ensure SUMO tools (traci) are importable
_sumo_home = os.environ.get("SUMO_HOME", "")
if _sumo_home:
    sys.path.insert(0, os.path.join(_sumo_home, "tools"))

from ai.config import AIConfig

PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")


class TrainingMetricsLogger:
    """Collects per-episode metrics during BOTH training and evaluation for reporting."""

    def __init__(self, output_path, algorithm_name="PPO"):
        self.output_path = output_path
        self._algorithm_name = algorithm_name
        self.training_episodes = []
        self.evaluation_episodes = []
        self._training_start_time = None
        self._training_end_time = None
        self._total_timesteps = 0

    def set_training_time(self, start, end, total_timesteps):
        self._training_start_time = start
        self._training_end_time = end
        self._total_timesteps = total_timesteps

    def on_training_episode_end(self, episode_num, info):
        """Called after each episode completes DURING training."""
        record = {
            "episode": episode_num,
            "phase": "training",
            "timestamp": time.time(),
            "steps": info.get("step", 0),
            "total_waiting_time": round(info.get("total_waiting_time", 0), 2),
            "vehicle_count": info.get("vehicle_count", 0),
            "episode_throughput": info.get("episode_throughput", 0),
            "episode_reward": round(info.get("episode_reward", 0), 4),
        }
        self.training_episodes.append(record)
        # Auto-save every 5 training episodes to avoid data loss
        if len(self.training_episodes) % 5 == 0:
            self._save()
        return record

    def on_eval_episode_end(self, episode_num, info):
        """Called after each evaluation episode AFTER training."""
        record = {
            "episode": episode_num,
            "phase": "evaluation",
            "timestamp": time.time(),
            "steps": info.get("step", 0),
            "total_waiting_time": round(info.get("total_waiting_time", 0), 2),
            "vehicle_count": info.get("vehicle_count", 0),
            "episode_throughput": info.get("episode_throughput", 0),
            "episode_reward": round(info.get("episode_reward", 0), 4),
        }
        self.evaluation_episodes.append(record)
        self._save()
        return record

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.output_path), exist_ok=True)

            # Load traffic profile for context
            profile_data = {}
            try:
                profile_path = os.path.join(DATA_DIR, "pathumwan_traffic_profile.json")
                with open(profile_path, encoding="utf-8") as f:
                    p = json.load(f)
                    profile_data = {
                        "description": p.get("description", ""),
                        "vehicle_proportions": p.get("vehicle_proportions", {}),
                        "station_count": len(p.get("stations", {})),
                    }
            except Exception:
                pass

            # Compute summary statistics
            training_summary = self._compute_summary(self.training_episodes)
            eval_summary = self._compute_summary(self.evaluation_episodes)

            training_elapsed = 0.0
            if self._training_start_time:
                end = self._training_end_time or time.time()
                training_elapsed = round(end - self._training_start_time, 1)

            with open(self.output_path, "w", encoding="utf-8") as f:
                json.dump({
                    "algorithm": self._algorithm_name,
                    "total_timesteps": self._total_timesteps,
                    "training_time_seconds": training_elapsed,
                    "data_source": profile_data,
                    "reward_weights": AIConfig.REWARD_WEIGHTS,
                    "hyperparameters": {
                        "learning_rate": AIConfig.LEARNING_RATE,
                        "gamma": AIConfig.GAMMA,
                        "batch_size": AIConfig.BATCH_SIZE,
                        "n_steps": AIConfig.N_STEPS,
                        "n_epochs": AIConfig.N_EPOCHS,
                        "entropy_coef": AIConfig.ENTROPY_COEF,
                        "action_interval": AIConfig.ACTION_INTERVAL,
                        "yellow_time": AIConfig.YELLOW_TIME,
                        "max_episode_steps": AIConfig.MAX_EPISODE_STEPS,
                    },
                    "training_summary": training_summary,
                    "evaluation_summary": eval_summary,
                    "training_episodes": self.training_episodes,
                    "evaluation_episodes": self.evaluation_episodes,
                }, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"  ⚠ Failed to save metrics: {e}")

    @staticmethod
    def _compute_summary(episodes):
        if not episodes:
            return {}
        rewards = [ep["episode_reward"] for ep in episodes]
        throughputs = [ep["episode_throughput"] for ep in episodes]
        waits = [ep["total_waiting_time"] for ep in episodes]
        n = min(10, len(episodes))
        first_n_rewards = [ep["episode_reward"] for ep in episodes[:n]]
        last_n_rewards = [ep["episode_reward"] for ep in episodes[-n:]]

        improvement_pct = None
        avg_first = sum(first_n_rewards) / len(first_n_rewards)
        avg_last = sum(last_n_rewards) / len(last_n_rewards)
        if avg_first != 0:
            improvement_pct = round(((avg_last - avg_first) / abs(avg_first)) * 100, 1)

        return {
            "total_episodes": len(episodes),
            "avg_reward": round(sum(rewards) / len(rewards), 4),
            "best_reward": round(max(rewards), 4),
            "worst_reward": round(min(rewards), 4),
            "avg_reward_first_n": round(avg_first, 4),
            "avg_reward_last_n": round(avg_last, 4),
            "reward_improvement_pct": improvement_pct,
            "avg_throughput": round(sum(throughputs) / len(throughputs), 1),
            "avg_waiting_time": round(sum(waits) / len(waits), 1),
            "best_throughput": max(throughputs),
            "worst_waiting_time": round(max(waits), 1),
        }

    def summary(self):
        train_s = self._compute_summary(self.training_episodes)
        eval_s = self._compute_summary(self.evaluation_episodes)

        lines = [
            f"\n{'='*60}",
            f"  📊 Training Results — {self._algorithm_name}",
            f"{'='*60}",
        ]

        if train_s:
            imp = ""
            if train_s.get("reward_improvement_pct") is not None:
                imp = f" ({train_s['reward_improvement_pct']:+.1f}%)"
            lines += [
                f"  Training episodes:  {train_s['total_episodes']}",
                f"  Avg reward:         {train_s['avg_reward']:.2f}{imp}",
                f"  Best reward:        {train_s['best_reward']:.2f}",
                f"  Avg throughput:     {train_s['avg_throughput']:.0f} vehicles/ep",
                f"  Avg wait time:      {train_s['avg_waiting_time']:.0f}s",
            ]

        if eval_s:
            lines += [
                f"  {'─'*40}",
                f"  Eval episodes:      {eval_s['total_episodes']}",
                f"  Eval avg reward:    {eval_s['avg_reward']:.2f}",
                f"  Eval avg throughput:{eval_s['avg_throughput']:.0f} vehicles/ep",
                f"  Eval avg wait:      {eval_s['avg_waiting_time']:.0f}s",
            ]

        if self._training_start_time and self._training_end_time:
            elapsed = self._training_end_time - self._training_start_time
            lines.append(f"  Training time:      {elapsed:.0f}s")

        lines.append(f"{'='*60}")
        return "\n".join(lines)


def _build_sumo_cmd(sumo_cfg_path):
    """Build the SUMO command line for training (headless, no warnings)."""
    from utils import find_sumo_binary
    sumo_binary = find_sumo_binary()
    return [
        sumo_binary,
        "-c", sumo_cfg_path,
        "--no-warnings",
        "--no-step-log",
        "--waiting-time-memory", "300",  # Track waiting times up to 5 min
    ]


def main():
    parser = argparse.ArgumentParser(
        description="Train traffic signal RL agent using Pathumwan traffic data"
    )
    parser.add_argument("--algorithm", default=AIConfig.ALGORITHM, choices=["PPO", "DQN", "A2C", "RULE_BASED", "FIXED_TIME"])
    parser.add_argument("--timesteps", type=int, default=AIConfig.TOTAL_TIMESTEPS)
    parser.add_argument("--junction-ids", nargs="+", default=None,
                        help="Junction IDs to control. If not given, auto-discovers from SUMO.")
    parser.add_argument("--max-junctions", type=int, default=10,
                        help="Max number of junctions to auto-discover (default 10)")
    args = parser.parse_args()

    # Paths
    sumo_cfg = os.path.join(PROJECT_ROOT, "osm.sumocfg")
    if not os.path.exists(sumo_cfg):
        print(f"❌ SUMO config not found at: {sumo_cfg}")
        return

    sumo_cmd = _build_sumo_cmd(sumo_cfg)

    # Print data source info
    print(f"\n{'='*60}")
    print(f"  🚦 TraffixFlow RL Training")
    print(f"{'='*60}")
    try:
        profile_path = os.path.join(DATA_DIR, "pathumwan_traffic_profile.json")
        with open(profile_path, encoding="utf-8") as f:
            profile = json.load(f)
        print(f"  Data source: {profile.get('description', 'N/A')}")
        print(f"  Stations: {len(profile.get('stations', {}))}")
        vp = profile.get("vehicle_proportions", {})
        print(f"  Vehicle mix: car={vp.get('passenger',0):.0%} "
              f"van={vp.get('van',0):.0%} "
              f"bus={vp.get('bus',0):.0%} "
              f"truck={vp.get('truck',0):.0%} "
              f"moto={vp.get('motorcycle',0):.0%}")
        # Peak hour info
        hourly = profile.get("average_hourly_profile", {})
        if hourly:
            peak_hr = max(hourly, key=lambda h: hourly[h].get("volume_avg", 0))
            peak = hourly[peak_hr]
            print(f"  Peak hour: {peak_hr}:00 "
                  f"(vol={peak.get('volume_avg',0)}, "
                  f"speed={peak.get('speed_avg',0)} km/h, "
                  f"V/C={peak.get('vc_avg',0)})")
    except Exception:
        print("  Data source: osm.sumocfg route files")
    print(f"{'='*60}")

    # Discover junctions from SUMO if not provided
    junction_ids = args.junction_ids
    if not junction_ids:
        print("\n🔍 Auto-discovering junctions from SUMO network...")
        import traci
        traci.start(sumo_cmd)
        all_tls = list(traci.trafficlight.getIDList())

        # Filter: only junctions with >= 2 actual phases
        valid_junctions = []
        for tls_id in all_tls:
            try:
                programs = traci.trafficlight.getAllProgramLogics(tls_id)
                if programs and programs[0].phases and len(programs[0].phases) >= 2:
                    valid_junctions.append(tls_id)
            except Exception:
                pass
        traci.close()
        time.sleep(2)  # Let SUMO release the port

        print(f"  Found {len(all_tls)} traffic lights, {len(valid_junctions)} with ≥2 phases")
        junction_ids = valid_junctions[:args.max_junctions]
        print(f"  Selected {len(junction_ids)} junctions for training: {junction_ids}")

    if not junction_ids:
        print("❌ No junctions found. Check SUMO configuration.")
        return

    # Create environment
    from ai.environment import SumoTrafficEnv

    env = SumoTrafficEnv(
        junction_ids=junction_ids,
        sumo_cmd=sumo_cmd,
    )

    # Setup metrics logger
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    metrics_filename = f"training_metrics_{args.algorithm}_{timestamp}.json"
    metrics_logger = TrainingMetricsLogger(
        os.path.join(DATA_DIR, metrics_filename),
        algorithm_name=args.algorithm,
    )

    # Create training callback to capture per-episode metrics DURING training
    # Only import SB3 callback for RL algorithms that actually need it
    callback = None
    if args.algorithm not in ("RULE_BASED", "FIXED_TIME"):
        from stable_baselines3.common.callbacks import BaseCallback

        class EpisodeMetricsCallback(BaseCallback):
            """Captures per-episode metrics during SB3 training loop."""

            def __init__(self, logger, verbose=0):
                super().__init__(verbose)
                self._metrics_logger = logger
                self._episode_count = 0

            def _on_step(self):
                infos = self.locals.get("infos", [])
                dones = self.locals.get("dones", [])

                if dones is not None and infos is not None:
                    for i, done in enumerate(dones):
                        if done and i < len(infos):
                            info = infos[i]
                            self._episode_count += 1
                            self._metrics_logger.on_training_episode_end(
                                self._episode_count, info
                            )
                            if self.verbose > 0:
                                print(f"  📈 Train ep {self._episode_count}: "
                                      f"reward={info.get('episode_reward', 0):.2f}, "
                                      f"throughput={info.get('episode_throughput', 0)}")
                return True

        callback = EpisodeMetricsCallback(metrics_logger, verbose=1)

    # Create and train agent
    from ai.agent import TrafficAgent
    agent = TrafficAgent(env=env, algorithm=args.algorithm)

    print(f"\n  Algorithm:       {args.algorithm}")
    print(f"  Timesteps:       {args.timesteps:,}")
    print(f"  Junctions:       {len(junction_ids)}")
    print(f"  Batch size:      {AIConfig.BATCH_SIZE}")
    print(f"  Action interval: {AIConfig.ACTION_INTERVAL} steps ({AIConfig.ACTION_INTERVAL}s)")
    print(f"  Yellow time:     {AIConfig.YELLOW_TIME}s")
    print(f"  Episode length:  {AIConfig.MAX_EPISODE_STEPS} steps ({AIConfig.MAX_EPISODE_STEPS//60} min)")
    print(f"  Reward weights:  {AIConfig.REWARD_WEIGHTS}")
    print(f"{'='*60}\n")

    t_start = time.time()
    success = agent.train(total_timesteps=args.timesteps, callback=callback)
    t_elapsed = time.time() - t_start

    metrics_logger.set_training_time(t_start, time.time(), args.timesteps)

    if success:
        # Only save model weights for RL algorithms (FIXED_TIME/RULE_BASED have no weights)
        if args.algorithm not in ("RULE_BASED", "FIXED_TIME"):
            agent.save()
        print(f"\n✅ {'Evaluation' if args.algorithm in ('RULE_BASED', 'FIXED_TIME') else 'Training'} complete in {t_elapsed:.0f}s")

        # Run evaluation episodes and collect metrics
        n_eval = 5
        print(f"\n📈 Running {n_eval} evaluation episodes...")
        for ep in range(n_eval):
            obs, info = env.reset()
            total_reward = 0.0
            done = False
            while not done:
                action = agent.predict(obs)
                obs, reward, terminated, truncated, info = env.step(action)
                total_reward += reward
                done = terminated or truncated

            info["episode_reward"] = total_reward
            metrics_logger.on_eval_episode_end(ep, info)
            print(f"  Episode {ep+1}: reward={total_reward:.2f}, "
                  f"throughput={info.get('episode_throughput', 0)}, "
                  f"wait={info.get('total_waiting_time', 0):.1f}s")

        print(metrics_logger.summary())
        print(f"\n📁 Metrics saved to: {os.path.join(DATA_DIR, metrics_filename)}")
        if args.algorithm not in ("RULE_BASED", "FIXED_TIME"):
            print(f"📁 Model saved to:   {os.path.join(AIConfig.MODEL_DIR, args.algorithm.lower() + '_traffic.zip')}")
    else:
        print("❌ Training failed. Check dependencies (pip install stable-baselines3 gymnasium).")

    env.close()


if __name__ == "__main__":
    main()
