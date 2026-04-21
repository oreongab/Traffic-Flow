"""
AI Trainer — Script to train the RL agent on SUMO environment.

Usage:
    cd backend
    python -m ai.trainer --algorithm PPO --timesteps 500000
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.config import AIConfig


def main():
    parser = argparse.ArgumentParser(description="Train traffic signal RL agent")
    parser.add_argument("--algorithm", default=AIConfig.ALGORITHM, choices=["PPO", "DQN"])
    parser.add_argument("--timesteps", type=int, default=AIConfig.TOTAL_TIMESTEPS)
    parser.add_argument("--junction-ids", nargs="+", default=None,
                        help="Junction IDs to control. If not given, auto-discovers from SUMO.")
    args = parser.parse_args()

    # Discover junctions from SUMO if not provided
    junction_ids = args.junction_ids
    if not junction_ids:
        print("Auto-discovering junctions from SUMO network...")
        from simulation import get_sumo_command
        import traci
        sumo_cmd = get_sumo_command()
        traci.start(sumo_cmd)
        junction_ids = list(traci.trafficlight.getIDList())[:10]  # Top 10 junctions
        traci.close()
        print(f"Found {len(junction_ids)} junctions: {junction_ids}")

    if not junction_ids:
        print("No junctions found. Check SUMO configuration.")
        return

    # Create environment
    from ai.environment import SumoTrafficEnv
    from simulation import get_sumo_command

    env = SumoTrafficEnv(
        junction_ids=junction_ids,
        sumo_cmd=get_sumo_command(),
    )

    # Create and train agent
    from ai.agent import TrafficAgent
    agent = TrafficAgent(env=env, algorithm=args.algorithm)

    print(f"Training {args.algorithm} agent for {args.timesteps} timesteps...")
    print(f"Junctions: {junction_ids}")

    success = agent.train(total_timesteps=args.timesteps)
    if success:
        agent.save()
        print("Training complete! Model saved.")
    else:
        print("Training failed. Check dependencies.")

    env.close()


if __name__ == "__main__":
    main()
