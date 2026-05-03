import os
import sys
import time
import json
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.config import AIConfig
from ai.agent import TrafficAgent
from ai.environment import SumoTrafficEnv
from ai.trainer import _build_sumo_cmd

PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")


def _discover_junctions(sumo_cmd, max_junctions=10):
    """Open SUMO briefly to discover valid traffic light junctions."""
    try:
        import libsumo as traci
        traci.start(sumo_cmd)
    except Exception as e:
        print(f"⚠ Falling back to traci. libsumo import failed: {e}")
        import traci
        traci.start(sumo_cmd, label="benchmark_init")

    all_tls = list(traci.trafficlight.getIDList())
    valid_junctions = []
    for tls_id in all_tls:
        try:
            programs = traci.trafficlight.getAllProgramLogics(tls_id)
            if programs and programs[0].phases and len(programs[0].phases) >= 2:
                valid_junctions.append(tls_id)
        except Exception:
            pass
    traci.close()
    time.sleep(1)
    return valid_junctions[:max_junctions]


def _make_env(sumo_cmd, junction_ids):
    """Create a fresh SumoTrafficEnv for a given set of junctions."""
    return SumoTrafficEnv(junction_ids=junction_ids, sumo_cmd=sumo_cmd)


def main():
    print("🚦 Traffic AI Benchmark Tool")
    print("=========================================")

    # Check which models exist
    available_models = ["FIXED_TIME", "RULE_BASED"]
    for algo in ["PPO", "DQN", "A2C"]:
        model_path = os.path.join(AIConfig.MODEL_DIR, f"{algo.lower()}_traffic.zip")
        if os.path.exists(model_path):
            available_models.append(algo)

    print(f"Models found to benchmark: {available_models}")

    sumo_cfg = os.path.join(PROJECT_ROOT, "osm.sumocfg")
    if not os.path.exists(sumo_cfg):
        print(f"❌ SUMO config not found at: {sumo_cfg}")
        return

    sumo_cmd = _build_sumo_cmd(sumo_cfg)

    # Discover junctions for RULE_BASED baseline (uses all 10)
    print("\n🔍 Auto-discovering junctions...")
    default_junctions = _discover_junctions(sumo_cmd, max_junctions=10)
    print(f"  Found {len(default_junctions)} junctions for baseline evaluation.")

    results = {}
    episodes_per_model = 3

    for algo in available_models:
        print(f"\n▶ Evaluating {algo}...")

        # Determine junction_ids to use for this model
        if algo not in ("RULE_BASED", "FIXED_TIME"):
            meta = TrafficAgent.load_metadata(algo)
            if meta and meta.get("junction_ids"):
                junction_ids = meta["junction_ids"]
                print(f"  Using {len(junction_ids)} junctions from training metadata.")
            else:
                # No metadata — try to infer junction count from saved model obs space
                inferred_junctions = default_junctions  # fallback
                try:
                    import zipfile, json as _json
                    model_path = os.path.join(AIConfig.MODEL_DIR, f"{algo.lower()}_traffic.zip")
                    with zipfile.ZipFile(model_path, "r") as zf:
                        if "data" in zf.namelist():
                            data = _json.loads(zf.read("data").decode("utf-8"))
                            obs_data = data.get("observation_space", {})
                            shape = obs_data.get("_shape", [])
                            if shape:
                                obs_size = shape[0]
                                n_features = len(AIConfig.STATE_FEATURES)
                                n_junc = obs_size // n_features
                                inferred_junctions = default_junctions[:n_junc]
                                print(f"  Inferred {n_junc} junctions from model obs space (size={obs_size}).")
                except Exception as e:
                    print(f"  ⚠ Could not infer junction count ({e}), using default.")
                junction_ids = inferred_junctions


        else:
            junction_ids = default_junctions

        # Create a fresh env matching this model's training setup
        env = _make_env(sumo_cmd, junction_ids)
        agent = TrafficAgent(env=env, algorithm=algo)

        # RULE_BASED and FIXED_TIME have no weights to load
        if algo not in ("RULE_BASED", "FIXED_TIME"):
            if not agent.load():
                print(f"⚠ Failed to load {algo}, skipping...")
                env.close()
                continue

        algo_wait_times = []
        algo_throughputs = []
        algo_rewards = []

        for ep in range(episodes_per_model):
            obs, info = env.reset()
            total_reward = 0.0
            done = False

            while not done:
                action = agent.predict(obs)
                obs, reward, terminated, truncated, info = env.step(action)
                total_reward += reward
                done = terminated or truncated

            wait_time = info.get("total_waiting_time", 0)
            throughput = info.get("episode_throughput", 0)

            algo_wait_times.append(wait_time)
            algo_throughputs.append(throughput)
            algo_rewards.append(total_reward)

            print(f"  Ep {ep+1}: Wait = {wait_time:.1f}s | Throughput = {throughput} | Reward = {total_reward:.2f}")

        env.close()

        results[algo] = {
            "junction_count": len(junction_ids),
            "avg_wait_time": float(np.mean(algo_wait_times)),
            "avg_throughput": float(np.mean(algo_throughputs)),
            "avg_reward": float(np.mean(algo_rewards)),
        }

    # Print summary
    print("\n\n📊 BENCHMARK RESULTS")
    print("=========================================================")
    print(f"{'Algorithm':<15} | {'Junctions':<10} | {'Avg Wait (s)':<14} | {'Avg Throughput':<15} | {'Avg Reward'}")
    print("-" * 75)
    for algo, metrics in results.items():
        print(
            f"{algo:<15} | {metrics['junction_count']:<10} | "
            f"{metrics['avg_wait_time']:<14.1f} | "
            f"{metrics['avg_throughput']:<15.1f} | "
            f"{metrics['avg_reward']:.2f}"
        )
    print("=========================================================")

    # Save to JSON
    out_path = os.path.join(DATA_DIR, "benchmark_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=4)

    print(f"\n📁 Saved detailed results to {out_path}")


if __name__ == "__main__":
    main()
