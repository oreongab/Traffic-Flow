"""
AI Decision Logger — Smart Retention
=====================================
Logs RL / rule-based signal decisions to the `ai_decisions` table,
but only when a phase *actually changes* (not every simulation step).
This keeps DB growth manageable while preserving an audit trail.
"""

from datetime import datetime, timezone
from database.connection import get_session
from database.models import AIDecision
from database.reference_data import ensure_junction

# In-memory cache of the last logged phase per junction.
# Prevents duplicate writes when the phase hasn't changed.
_last_phase: dict = {}  # junction_id -> int


def log_decision_if_changed(junction_id: str, phase: int, input_data: dict,
                            output: dict, reward: float = 0.0,
                            model_version: str = ""):
    """Write an AIDecision row only when the phase differs from the last log."""
    prev = _last_phase.get(junction_id)
    if prev == phase:
        return  # same phase — skip

    _last_phase[junction_id] = phase

    session = get_session()
    try:
        normalized_junction = ensure_junction(session, junction_id)
        row = AIDecision(
            junction_id=str(normalized_junction or junction_id),
            timestamp=datetime.now(timezone.utc),
            input_data=input_data,
            output=output,
            reward=reward,
            model_version=model_version,
        )
        session.add(row)
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()
