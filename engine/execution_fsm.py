"""execution_fsm.py - Execution Finite State Machine
Bounded state machine for bot combat exploration and skill rotation.
"""

from __future__ import annotations

from enum import auto, Enum


class BotState(Enum):
    """Bot state enumeration used by the execution FSM."""
    STANDBY = auto()
    ROAMING = auto()
    EXPLORATION = auto()  # Alias for ROAMING, used by test suite
    COMBAT = auto()
    COOLDOWN = auto()

    # Convenience aliases for test compatibility
    EXPLORE = EXPLORATION


__all__ = ["BotState"]