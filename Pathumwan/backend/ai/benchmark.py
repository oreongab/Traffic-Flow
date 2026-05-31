import os
import sys
import time
import json
import csv
from datetime import datetime
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
        algo_speeds = []
        algo_queues = []
        algo_episodes = []

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
            avg_speed = info.get("avg_speed_kmh", 0)
            avg_queue = info.get("avg_queue_length", 0)

            algo_wait_times.append(wait_time)
            algo_throughputs.append(throughput)
            algo_rewards.append(total_reward)
            algo_speeds.append(avg_speed)
            algo_queues.append(avg_queue)
            
            ep_record = {
                "episode": ep + 1,
                "wait_time": wait_time,
                "throughput": throughput,
                "reward": total_reward,
                "avg_speed_kmh": avg_speed,
                "avg_queue_length": avg_queue,
                "reward_components": info.get("episode_reward_components", {})
            }
            algo_episodes.append(ep_record)

            print(f"  Ep {ep+1}: Wait = {wait_time:.1f}s | Throughput = {throughput} | Reward = {total_reward:.2f} | Speed = {avg_speed:.1f}km/h")

        env.close()

        results[algo] = {
            "junction_count": len(junction_ids),
            "episodes_run": episodes_per_model,
            "metrics": {
                "wait_time": {
                    "mean": float(np.mean(algo_wait_times)),
                    "std": float(np.std(algo_wait_times)),
                    "min": float(np.min(algo_wait_times)),
                    "max": float(np.max(algo_wait_times)),
                },
                "throughput": {
                    "mean": float(np.mean(algo_throughputs)),
                    "std": float(np.std(algo_throughputs)),
                    "min": float(np.min(algo_throughputs)),
                    "max": float(np.max(algo_throughputs)),
                },
                "reward": {
                    "mean": float(np.mean(algo_rewards)),
                    "std": float(np.std(algo_rewards)),
                    "min": float(np.min(algo_rewards)),
                    "max": float(np.max(algo_rewards)),
                },
                "speed_kmh": {
                    "mean": float(np.mean(algo_speeds)),
                    "std": float(np.std(algo_speeds)),
                },
                "queue_length": {
                    "mean": float(np.mean(algo_queues)),
                    "std": float(np.std(algo_queues)),
                }
            },
            "episodes": algo_episodes
        }
        
    # Calculate % improvement against baseline (FIXED_TIME or RULE_BASED)
    baseline_algo = "FIXED_TIME" if "FIXED_TIME" in results else ("RULE_BASED" if "RULE_BASED" in results else None)
    if baseline_algo:
        base_reward = results[baseline_algo]["metrics"]["reward"]["mean"]
        base_wait = results[baseline_algo]["metrics"]["wait_time"]["mean"]
        for algo in results:
            algo_reward = results[algo]["metrics"]["reward"]["mean"]
            algo_wait = results[algo]["metrics"]["wait_time"]["mean"]
            
            # For reward, higher is better
            if base_reward != 0:
                reward_imp = ((algo_reward - base_reward) / abs(base_reward)) * 100
                results[algo]["metrics"]["reward"]["improvement_pct"] = float(reward_imp)
                
            # For wait time, lower is better
            if base_wait != 0:
                wait_imp = ((base_wait - algo_wait) / base_wait) * 100
                results[algo]["metrics"]["wait_time"]["improvement_pct"] = float(wait_imp)

    # Print summary
    print("\n\n📊 BENCHMARK RESULTS")
    print("=========================================================================================")
    print(f"{'Algorithm':<12} | {'Junctions':<9} | {'Avg Wait (s) ± SD':<18} | {'Throughput ± SD':<16} | {'Reward ± SD'}")
    print("-" * 89)
    for algo, res in results.items():
        m = res["metrics"]
        print(
            f"{algo:<12} | {res['junction_count']:<9} | "
            f"{m['wait_time']['mean']:<8.1f} ± {m['wait_time']['std']:<5.1f} | "
            f"{m['throughput']['mean']:<8.1f} ± {m['throughput']['std']:<4.1f} | "
            f"{m['reward']['mean']:<8.2f} ± {m['reward']['std']:.2f}"
        )
    print("=========================================================================================")

    output_data = {
        "timestamp": datetime.now().isoformat(),
        "episodes_per_model": episodes_per_model,
        "results": results
    }

    # Save to JSON
    out_path = os.path.join(DATA_DIR, "benchmark_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=4)
        
    # Save to CSV
    csv_path = os.path.join(DATA_DIR, "benchmark_results.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Algorithm", "Junctions", "Episodes", 
            "Wait_Mean", "Wait_SD", "Wait_Min", "Wait_Max", "Wait_Imp_%",
            "Throughput_Mean", "Throughput_SD", "Throughput_Min", "Throughput_Max",
            "Reward_Mean", "Reward_SD", "Reward_Min", "Reward_Max", "Reward_Imp_%",
            "Speed_Mean", "Queue_Mean"
        ])
        for algo, res in results.items():
            m = res["metrics"]
            writer.writerow([
                algo, res["junction_count"], res["episodes_run"],
                round(m["wait_time"]["mean"], 2), round(m["wait_time"]["std"], 2), round(m["wait_time"]["min"], 2), round(m["wait_time"]["max"], 2), round(m["wait_time"].get("improvement_pct", 0.0), 2),
                round(m["throughput"]["mean"], 2), round(m["throughput"]["std"], 2), round(m["throughput"]["min"], 2), round(m["throughput"]["max"], 2),
                round(m["reward"]["mean"], 4), round(m["reward"]["std"], 4), round(m["reward"]["min"], 4), round(m["reward"]["max"], 4), round(m["reward"].get("improvement_pct", 0.0), 2),
                round(m["speed_kmh"]["mean"], 2), round(m["queue_length"]["mean"], 2)
            ])

    print(f"\n📁 Saved JSON results to {out_path}")
    print(f"📁 Saved CSV summary to {csv_path}")


if __name__ == "__main__":
    main()
