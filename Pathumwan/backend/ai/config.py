"""
AI/RL Configuration for Traffic Signal Optimization
"""

from config import Config


class AIConfig:
    # Reinforcement Learning
    ALGORITHM = Config.AI_ALGORITHM  # PPO or DQN
    LEARNING_RATE = 3e-4
    GAMMA = 0.99           # Discount factor
    GAE_LAMBDA = 0.95      # GAE parameter
    CLIP_RANGE = 0.2       # PPO clip
    N_STEPS = 2048         # Steps per update
    BATCH_SIZE = 256       # Larger batch for multi-junction stability
    N_EPOCHS = 10
    ENTROPY_COEF = 0.02    # Slightly higher entropy for better exploration
    MAX_GRAD_NORM = 0.5
    VF_COEF = 0.5          # Value function coefficient

    # Environment
    SIM_STEP_LENGTH = 1.0  # Seconds per SUMO step
    ACTION_INTERVAL = 10   # Steps between AI decisions
    MAX_EPISODE_STEPS = 3600  # 1 hour simulation
    YELLOW_TIME = 3        # Yellow phase duration (seconds)

    # State space
    STATE_FEATURES = [
        "queue_length",        # Vehicles waiting at red
        "waiting_time",        # Cumulative waiting time
        "vehicle_count",       # Vehicles on approaching lanes
        "avg_speed",           # Average speed on lane
        "current_phase",       # Current signal phase (normalized)
        "phase_duration",      # How long current phase has been active
        "time_of_day",         # Normalized hour (0-1)
    ]

    # Action space
    # Each junction: choose next phase index (discrete)
    # Multi-junction: product of individual action spaces
    MIN_GREEN_TIME = 10    # Minimum green phase duration (seconds)
    MAX_GREEN_TIME = 60    # Maximum green phase duration (seconds)

    # Fixed-Time baseline — equal green time per phase, no adaptation
    FIXED_GREEN_TIME = 30  # Seconds of green per phase (timer-based baseline)

    # Reward weights — positive values for things we WANT,
    # negative values for things we want to MINIMIZE.
    # Each reward component returns a magnitude in [0, 1].
    REWARD_WEIGHTS = {
        "waiting_time": -0.4,       # Penalize total waiting time
        "throughput": 1.0,          # Reward vehicles passing through
        "avg_speed": 0.3,           # Reward higher average speeds
        "queue_length": -0.3,       # Penalize long queues
        "phase_switch": -0.1,       # Penalize unnecessary phase changes
        "emergency_penalty": -2.0,  # Penalize vehicles stuck > 120s
    }

    # Training
    TOTAL_TIMESTEPS = 500_000
    EVAL_FREQ = 10_000
    SAVE_FREQ = 50_000
    LOG_DIR = "ai/logs"
    MODEL_DIR = "ai/models"
