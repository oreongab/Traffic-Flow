"""
Reward Functions for Traffic Signal RL.
Modular reward components that can be combined.
"""

import numpy as np
from ai.config import AIConfig


def waiting_time_reward(traci, junction_ids):
    """Negative reward proportional to total waiting time."""
    total_wait = 0.0
    for jid in junction_ids:
        try:
            lanes = list(set(traci.trafficlight.getControlledLanes(jid)))
            for lane in lanes:
                total_wait += traci.lane.getWaitingTime(lane)
        except Exception:
            pass
    # Normalize: -1 at 300s total wait
    return max(-1.0, -total_wait / 300.0)


def throughput_reward(vehicles_before, vehicles_after):
    """Positive reward for vehicles completing their trip."""
    departed = len(vehicles_before - vehicles_after)
    return min(departed / 10.0, 1.0)


def speed_reward(traci):
    """Reward proportional to average network speed."""
    speeds = []
    try:
        for vid in traci.vehicle.getIDList():
            speeds.append(traci.vehicle.getSpeed(vid))
    except Exception:
        return 0.0
    if not speeds:
        return 0.0
    avg = np.mean(speeds)
    return min(avg / 13.89, 1.0)  # 50 km/h = 1.0


def queue_penalty(traci, junction_ids):
    """Negative reward for queue length."""
    total_queue = 0
    for jid in junction_ids:
        try:
            lanes = list(set(traci.trafficlight.getControlledLanes(jid)))
            total_queue += sum(traci.lane.getLastStepHaltingNumber(l) for l in lanes)
        except Exception:
            pass
    return max(-1.0, -total_queue / (50.0 * len(junction_ids)))


def combined_reward(traci, junction_ids, vehicles_before, vehicles_after):
    """Weighted combination of all reward components."""
    w = AIConfig.REWARD_WEIGHTS
    r = 0.0
    r += w["waiting_time"] * (-waiting_time_reward(traci, junction_ids))
    r += w["throughput"] * throughput_reward(vehicles_before, vehicles_after)
    r += w["avg_speed"] * speed_reward(traci)
    r += w["queue_length"] * (-queue_penalty(traci, junction_ids))
    return r
