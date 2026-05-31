"""
Reward Functions for Traffic Signal RL.
Modular reward components that can be combined.
"""

import numpy as np
from ai.config import AIConfig


def waiting_time_reward(traci, lanes):
    """Normalized total waiting time (0.0 to 1.0).
    
    Higher value = more waiting = worse traffic.
    Config weight should be negative to penalize.
    """
    total_wait = 0.0
    for lane in lanes:
        try:
            total_wait += traci.lane.getWaitingTime(lane)
        except Exception:
            pass
    return min(total_wait / 300.0, 1.0)


def throughput_reward(arrived_count):
    """Positive reward for vehicles completing their trip (0.0 to 1.0)."""
    return min(arrived_count / 10.0, 1.0)


def speed_reward(traci, lanes):
    """Reward proportional to average speed on controlled lanes (0.0 to 1.0)."""
    speeds = []
    for lane in lanes:
        try:
            speeds.append(traci.lane.getLastStepMeanSpeed(lane))
        except Exception:
            pass
    if not speeds:
        return 0.0
    avg = np.mean(speeds)
    return min(avg / 13.89, 1.0)  # 50 km/h = 1.0


def queue_penalty(traci, lanes, n_junctions):
    """Normalized queue length (0.0 to 1.0).
    
    Higher value = longer queues = worse traffic.
    Config weight should be negative to penalize.
    """
    if not lanes:
        return 0.0
    total_queue = 0
    for lane in lanes:
        try:
            total_queue += traci.lane.getLastStepHaltingNumber(lane)
        except Exception:
            pass
    return min(total_queue / (50.0 * max(1, n_junctions)), 1.0)


def phase_switch_penalty(old_phases, new_phases):
    """Penalty for unnecessary phase switches (0.0 to 1.0).
    
    Discourages the agent from flipping signals every interval
    which would be dangerous and inefficient in real life.
    """
    if not old_phases or not new_phases:
        return 0.0
    switches = sum(1 for o, n in zip(old_phases, new_phases) if o != n)
    return min(switches / max(1, len(old_phases)), 1.0)


def emergency_wait_penalty(traci, lanes, threshold_seconds=120):
    """Count vehicles at junctions waiting longer than threshold (0.0 to 1.0)."""
    count = 0
    total = 0
    for lane in lanes:
        try:
            vehicles = traci.lane.getLastStepVehicleIDs(lane)
            total += len(vehicles)
            for vid in vehicles:
                if traci.vehicle.getWaitingTime(vid) > threshold_seconds:
                    count += 1
        except Exception:
            pass
    if total == 0:
        return 0.0
    return min(count / max(1, total), 1.0)


def combined_reward(traci, junction_ids, arrived_count,
                    old_phases=None, new_phases=None,
                    controlled_lanes_cache=None):
    """Weighted combination of all reward components.
    
    Each component returns a positive magnitude (0.0 to 1.0).
    The sign (penalty vs reward) is controlled by the weights in AIConfig.
    
    Parameters
    ----------
    traci : module
        Active TraCI/libsumo connection.
    junction_ids : list[str]
        Controlled junction IDs.
    arrived_count : int
        Number of vehicles that arrived at destination this step.
    controlled_lanes_cache : dict[str, set], optional
        Pre-cached mapping of junction_id -> set of controlled lane ids.
    """
    w = AIConfig.REWARD_WEIGHTS

    # Build a single flat set of all controlled lanes (use cache if available)
    all_lanes = set()
    if controlled_lanes_cache:
        for jid in junction_ids:
            all_lanes.update(controlled_lanes_cache.get(jid, set()))
    else:
        for jid in junction_ids:
            try:
                all_lanes.update(traci.trafficlight.getControlledLanes(jid))
            except Exception:
                pass

    r = 0.0
    breakdown = {}
    
    val = waiting_time_reward(traci, all_lanes)
    r += w["waiting_time"] * val
    breakdown["waiting_time"] = w["waiting_time"] * val
    
    val = throughput_reward(arrived_count)
    r += w["throughput"] * val
    breakdown["throughput"] = w["throughput"] * val
    
    val = speed_reward(traci, all_lanes)
    r += w["avg_speed"] * val
    breakdown["avg_speed"] = w["avg_speed"] * val
    
    val = queue_penalty(traci, all_lanes, len(junction_ids))
    r += w["queue_length"] * val
    breakdown["queue_length"] = w["queue_length"] * val
    
    val = phase_switch_penalty(old_phases or [], new_phases or [])
    r += w.get("phase_switch", 0.0) * val
    breakdown["phase_switch"] = w.get("phase_switch", 0.0) * val
    
    val = emergency_wait_penalty(traci, all_lanes)
    r += w["emergency_penalty"] * val
    breakdown["emergency_penalty"] = w["emergency_penalty"] * val
    
    return r, breakdown
