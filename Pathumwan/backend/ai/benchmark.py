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

def main():
    print("🚦 Traffic AI Benchmark Tool")
    print("=========================================")

    models_to_test = ["RULE_BASED", "PPO", "DQN", "A2C"]
    
    # Check which models exist
    available_models = ["RULE_BASED"]
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
    
    # Use auto-discovery for junctions
    print("\n🔍 Auto-discovering junctions...")
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
    
    junction_ids = valid_junctions[:10]
    print(f"Selected {len(junction_ids)} junctions for evaluation.")

    # Create environment
    env = SumoTrafficEnv(junction_ids=junction_ids, sumo_cmd=sumo_cmd)
    
    results = {}
    episodes_per_model = 3
    
    for algo in available_models:
        print(f"\n▶ Evaluating {algo}...")
        agent = TrafficAgent(env=env, algorithm=algo)
        
        # RULE_BASED has no weights to load
        if algo != "RULE_BASED":
            if not agent.load():
                print(f"⚠ Failed to load {algo}, skipping...")
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
            
        results[algo] = {
            "avg_wait_time": float(np.mean(algo_wait_times)),
            "avg_throughput": float(np.mean(algo_throughputs)),
            "avg_reward": float(np.mean(algo_rewards))
        }

    env.close()

    # Print summary
    print("\n\n📊 BENCHMARK RESULTS")
    print("=========================================================")
    print(f"{'Algorithm':<15} | {'Avg Wait (s)':<15} | {'Avg Throughput':<15}")
    print("---------------------------------------------------------")
    for algo, metrics in results.items():
        print(f"{algo:<15} | {metrics['avg_wait_time']:<15.1f} | {metrics['avg_throughput']:<15.1f}")
    print("=========================================================")
    
    # Save to JSON
    out_path = os.path.join(DATA_DIR, "benchmark_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=4)
        
    print(f"\n📁 Saved detailed results to {out_path}")

if __name__ == "__main__":
    main()
