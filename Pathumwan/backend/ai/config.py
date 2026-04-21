"""
AI/RL Configuration for Traffic Signal Optimization
"""


class AIConfig:
    # Reinforcement Learning
    ALGORITHM = "PPO"  # PPO or DQN
    LEARNING_RATE = 3e-4
    GAMMA = 0.99           # Discount factor
    GAE_LAMBDA = 0.95      # GAE parameter
    CLIP_RANGE = 0.2       # PPO clip
    N_STEPS = 2048         # Steps per update
    BATCH_SIZE = 64
    N_EPOCHS = 10
    ENTROPY_COEF = 0.01
    MAX_GRAD_NORM = 0.5

    # Environment
    SIM_STEP_LENGTH = 1.0  # Seconds per SUMO step
    ACTION_INTERVAL = 10   # Steps between AI decisions
    MAX_EPISODE_STEPS = 3600  # 1 hour simulation

    # State space
    STATE_FEATURES = [
        "queue_length",        # Vehicles waiting at red
        "waiting_time",        # Cumulative waiting time
        "vehicle_count",       # Vehicles on approaching lanes
        "avg_speed",           # Average speed on lane
        "current_phase",       # Current signal phase (one-hot)
        "phase_duration",      # How long current phase has been active
        "time_of_day",         # Normalized hour (0-1)
    ]

    # Action space
    # Each junction: choose next phase index (discrete)
    # Multi-junction: product of individual action spaces
    MIN_GREEN_TIME = 10    # Minimum green phase duration (seconds)
    MAX_GREEN_TIME = 60    # Maximum green phase duration (seconds)
    YELLOW_TIME = 3        # Yellow phase duration (seconds)

    # Reward weights
    REWARD_WEIGHTS = {
        "waiting_time": -0.5,     # Penalize total waiting time
        "throughput": 1.0,        # Reward vehicles passing
        "avg_speed": 0.3,         # Reward higher speeds
        "queue_length": -0.3,     # Penalize long queues
        "emergency_penalty": -5.0, # Penalty for very long waits
    }

    # Training
    TOTAL_TIMESTEPS = 500_000
    EVAL_FREQ = 10_000
    SAVE_FREQ = 50_000
    LOG_DIR = "ai/logs"
    MODEL_DIR = "ai/models"
