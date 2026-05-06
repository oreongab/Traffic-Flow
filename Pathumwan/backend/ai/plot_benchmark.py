import os
import sys
import time
import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.config import AIConfig
from ai.agent import TrafficAgent
from ai.environment import SumoTrafficEnv
from ai.trainer import _build_sumo_cmd
import ai.benchmark as benchmark

PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

# Junctions to plot (we pick the first 4 discovered junctions)
TARGET_JUNCTIONS = []

def get_junction_mean_speed(traci, jid):
    """Calculate mean speed of vehicles waiting/arriving at a junction (in m/s)."""
    try:
        lanes = traci.trafficlight.getControlledLanes(jid)
        speeds = []
        for lane in set(lanes):
            speed = traci.lane.getLastStepMeanSpeed(lane)
            # traci returns -1 if no vehicles or error
            if speed >= 0:
                speeds.append(speed)
        if speeds:
            return float(np.mean(speeds))
    except Exception:
        pass
    return 0.0

def main():
    print("🚦 Traffic AI Benchmarking & Plotting")
    print("=========================================")

    available_models = ["FIXED_TIME", "RULE_BASED"]
    for algo in ["PPO", "DQN", "A2C"]:
        model_path = os.path.join(AIConfig.MODEL_DIR, f"{algo.lower()}_traffic.zip")
        if os.path.exists(model_path):
            available_models.append(algo)
            
    print(f"Models to evaluate: {available_models}")

    sumo_cfg = os.path.join(PROJECT_ROOT, "osm.sumocfg")
    sumo_cmd = _build_sumo_cmd(sumo_cfg)

    # Discover junctions
    junction_ids = benchmark._discover_junctions(sumo_cmd, max_junctions=10)
    if not junction_ids:
        print("No junctions found.")
        return

    # Pick top 4 for the 2x2 plot
    target_junctions = junction_ids[:4]
    
    # Dictionary to store history: history[model][junction_id] = list of speeds
    history = {algo: {jid: [] for jid in target_junctions} for algo in available_models}
    timestamps = []

    # Simulation parameters
    # We record every N steps to avoid massive arrays, e.g. every 60 steps (1 minute)
    RECORD_INTERVAL = 60

    for algo in available_models:
        print(f"\n▶ Evaluating {algo} for 1 episode...")
        
        env = benchmark._make_env(sumo_cmd, junction_ids)
        agent = TrafficAgent(env=env, algorithm=algo)
        
        if algo not in ("RULE_BASED", "FIXED_TIME"):
            if not agent.load():
                print(f"⚠ Failed to load {algo}, skipping...")
                env.close()
                continue

        obs, info = env.reset()
        done = False
        
        if len(timestamps) == 0:
            # We only need to build the timestamps array once
            build_timestamps = True
        else:
            build_timestamps = False
            
        current_step = 0
        
        while not done:
            action = agent.predict(obs)
            obs, reward, terminated, truncated, info = env.step(action)
            
            # Since env.step might advance SUMO by ACTION_INTERVAL (10 steps),
            # we record metrics based on the current step.
            if info["step"] - current_step >= RECORD_INTERVAL or current_step == 0:
                current_step = info["step"]
                if build_timestamps:
                    timestamps.append(current_step)
                    
                # Query speeds for the target junctions
                if env.traci:
                    for jid in target_junctions:
                        speed = get_junction_mean_speed(env.traci, jid)
                        history[algo][jid].append(speed)
            
            done = terminated or truncated

        env.close()
        print(f"✓ Finished {algo}")

    # Ensure all arrays are same length (sometimes truncated might differ slightly)
    min_length = min(len(timestamps), *[len(h) for algo_data in history.values() for h in algo_data.values()])
    timestamps = timestamps[:min_length]
    for algo in available_models:
        for jid in target_junctions:
            history[algo][jid] = history[algo][jid][:min_length]

    # Plotting
    print("\n📊 Generating Plots...")
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes = axes.flatten()

    colors = {
        "FIXED_TIME": "gray",
        "RULE_BASED": "black",
        "PPO": "blue",
        "DQN": "orange",
        "A2C": "green"
    }

    for idx, jid in enumerate(target_junctions):
        ax = axes[idx]
        for algo in available_models:
            # Smooth the data using a moving average for better visualization (like in the paper)
            window = max(1, len(history[algo][jid]) // 20)
            smoothed = np.convolve(history[algo][jid], np.ones(window)/window, mode='valid')
            plot_times = timestamps[:len(smoothed)]
            ax.plot(plot_times, smoothed, label=algo, color=colors.get(algo, "red"), linewidth=2)
            
        ax.set_title(f"Junction {jid} Mean Speed")
        ax.set_xlabel("Time Steps (s)")
        ax.set_ylabel("Mean Speed (m/s)")
        ax.grid(True, linestyle='--', alpha=0.7)
        ax.legend()

    plt.tight_layout()
    output_path = os.path.join(DATA_DIR, "benchmark_speed_plot.png")
    plt.savefig(output_path, dpi=300)
    print(f"\n✅ Plot saved successfully to: {output_path}")


if __name__ == "__main__":
    main()
