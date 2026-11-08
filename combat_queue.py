"""Combat Queue System - Prevents double key presses and tracks combat actions.

This module provides:
- A thread-safe queue for tracking combat inputs
- Prevention of duplicate key presses within a cooldown window
- History of recent actions for UI display
- Priority-based action scheduling
"""
import time
import threading
from typing import Dict, List, Optional, Callable, Tuple
from dataclasses import dataclass, field
from collections import deque
from enum import Enum
from loguru import logger


class ActionType(Enum):
    """Types of combat actions."""
    BASIC_ATTACK = "basic_attack"
    SKILL = "skill"
    COMBO = "combo"
    LOOT = "loot"
    MOVEMENT = "movement"
    CAMERA = "camera"


class ActionStatus(Enum):
    """Status of queued actions."""
    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"  # Blocked due to duplicate prevention


@dataclass
class CombatAction:
    """Represents a single combat action."""
    action_type: ActionType
    key: str
    timestamp: float = field(default_factory=time.time)
    status: ActionStatus = ActionStatus.PENDING
    priority: int = 0  # Higher = more urgent
    source: str = ""  # Who requested this action (e.g., "combo", "planner")
    execution_time: Optional[float] = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for UI display."""
        return {
            "type": self.action_type.value,
            "key": self.key,
            "timestamp": self.timestamp,
            "status": self.status.value,
            "priority": self.priority,
            "source": self.source,
            "execution_time": self.execution_time,
            "age_ms": int((time.time() - self.timestamp) * 1000)
        }


class CombatQueue:
    """Thread-safe combat action queue with duplicate prevention."""
    
    # Minimum time (seconds) between same key presses
    DUPLICATE_PREVENTION_WINDOW = 0.15  # 150ms - prevents double key presses
    
    # Maximum queue size
    MAX_QUEUE_SIZE = 50
    
    # History size for UI display
    MAX_HISTORY_SIZE = 100
    
    def __init__(self):
        self._lock = threading.RLock()
        
        # Pending actions queue (priority queue would be better but deque is simpler)
        self._queue: deque[CombatAction] = deque(maxlen=self.MAX_QUEUE_SIZE)
        
        # Recently executed actions (for duplicate prevention)
        self._recent_keys: Dict[str, float] = {}  # key -> last execution time
        
        # Action history for UI display
        self._history: deque[CombatAction] = deque(maxlen=self.MAX_HISTORY_SIZE)
        
        # Currently executing action
        self._current_action: Optional[CombatAction] = None
        
        # Statistics
        self._stats = {
            "total_queued": 0,
            "total_executed": 0,
            "total_blocked": 0,
            "total_cancelled": 0,
        }
        
        # Callbacks for UI updates
        self._on_queue_update: Optional[Callable] = None
        self._on_action_executed: Optional[Callable] = None
        
        logger.info("✓ Combat Queue initialized")
    
    def set_callbacks(self, on_queue_update: Callable = None, on_action_executed: Callable = None):
        """Set callbacks for UI updates."""
        self._on_queue_update = on_queue_update
        self._on_action_executed = on_action_executed
    
    def can_execute_key(self, key: str) -> bool:
        """Check if a key can be executed (not a duplicate)."""
        key_lower = key.lower()
        with self._lock:
            last_time = self._recent_keys.get(key_lower, 0)
            elapsed = time.time() - last_time
            return elapsed >= self.DUPLICATE_PREVENTION_WINDOW
    
    def queue_action(self, action_type: ActionType, key: str, priority: int = 0, source: str = "") -> Tuple[bool, str]:
        """Queue a combat action.
        
        Returns:
            (success, reason) - True if queued, False if blocked
        """
        key_lower = key.lower()
        
        with self._lock:
            # Check for duplicate prevention
            if not self.can_execute_key(key_lower):
                elapsed = time.time() - self._recent_keys.get(key_lower, 0)
                remaining = self.DUPLICATE_PREVENTION_WINDOW - elapsed
                
                action = CombatAction(
                    action_type=action_type,
                    key=key_lower,
                    priority=priority,
                    source=source,
                    status=ActionStatus.BLOCKED
                )
                self._history.appendleft(action)
                self._stats["total_blocked"] += 1
                
                logger.debug(f"CombatQueue: BLOCKED duplicate '{key}' (wait {remaining*1000:.0f}ms)")
                self._notify_update()
                return False, f"Duplicate blocked (wait {remaining*1000:.0f}ms)"
            
            # Create and queue the action
            action = CombatAction(
                action_type=action_type,
                key=key_lower,
                priority=priority,
                source=source
            )
            
            # Insert based on priority (higher priority first)
            inserted = False
            for i, existing in enumerate(self._queue):
                if action.priority > existing.priority:
                    self._queue.insert(i, action)
                    inserted = True
                    break
            
            if not inserted:
                self._queue.append(action)
            
            self._stats["total_queued"] += 1
            logger.debug(f"CombatQueue: queued '{key}' ({action_type.value}) priority={priority}")
            self._notify_update()
            return True, "Queued"
    
    def get_next_action(self) -> Optional[CombatAction]:
        """Get the next action to execute (does not remove it)."""
        with self._lock:
            if not self._queue:
                return None
            return self._queue[0]
    
    def pop_next_action(self) -> Optional[CombatAction]:
        """Remove and return the next action to execute."""
        with self._lock:
            if not self._queue:
                return None
            
            action = self._queue.popleft()
            action.status = ActionStatus.EXECUTING
            self._current_action = action
            self._notify_update()
            return action
    
    def mark_executed(self, action: CombatAction, success: bool = True):
        """Mark an action as executed and record it."""
        with self._lock:
            action.execution_time = time.time()
            action.status = ActionStatus.COMPLETED if success else ActionStatus.CANCELLED
            
            # Record for duplicate prevention
            self._recent_keys[action.key] = time.time()
            
            # Add to history
            self._history.appendleft(action)
            
            self._stats["total_executed"] += 1
            self._current_action = None
            
            logger.debug(f"CombatQueue: executed '{action.key}' ({action.action_type.value})")
            self._notify_update()
            
            if self._on_action_executed:
                try:
                    self._on_action_executed(action)
                except Exception:
                    pass
    
    def execute_immediate(self, key: str, action_type: ActionType = ActionType.SKILL, source: str = "") -> bool:
        """Execute a key immediately if not a duplicate.
        
        This is a convenience method that checks, queues, executes, and marks complete.
        Returns True if the key was executed, False if blocked.
        """
        key_lower = key.lower()
        
        with self._lock:
            if not self.can_execute_key(key_lower):
                elapsed = time.time() - self._recent_keys.get(key_lower, 0)
                remaining = self.DUPLICATE_PREVENTION_WINDOW - elapsed
                
                # Record the blocked attempt
                action = CombatAction(
                    action_type=action_type,
                    key=key_lower,
                    source=source,
                    status=ActionStatus.BLOCKED
                )
                self._history.appendleft(action)
                self._stats["total_blocked"] += 1
                
                logger.debug(f"CombatQueue: BLOCKED immediate '{key}' (wait {remaining*1000:.0f}ms)")
                self._notify_update()
                return False
            
            # Record that we're executing this key
            self._recent_keys[key_lower] = time.time()
            
            # Create action record
            action = CombatAction(
                action_type=action_type,
                key=key_lower,
                source=source,
                status=ActionStatus.COMPLETED,
                execution_time=time.time()
            )
            self._history.appendleft(action)
            self._stats["total_executed"] += 1
            
            self._notify_update()
            return True
    
    def clear_queue(self):
        """Clear all pending actions."""
        with self._lock:
            cancelled = len(self._queue)
            for action in self._queue:
                action.status = ActionStatus.CANCELLED
                self._history.appendleft(action)
            self._queue.clear()
            self._stats["total_cancelled"] += cancelled
            logger.info(f"CombatQueue: cleared {cancelled} pending actions")
            self._notify_update()
    
    def get_queue_state(self) -> Dict:
        """Get current queue state for UI display."""
        with self._lock:
            return {
                "pending": [a.to_dict() for a in self._queue],
                "current": self._current_action.to_dict() if self._current_action else None,
                "history": [a.to_dict() for a in list(self._history)[:20]],  # Last 20
                "stats": dict(self._stats),
                "queue_size": len(self._queue),
                "history_size": len(self._history),
            }
    
    def get_stats(self) -> Dict:
        """Get queue statistics."""
        with self._lock:
            return dict(self._stats)
    
    def _notify_update(self):
        """Notify UI of queue update."""
        if self._on_queue_update:
            try:
                self._on_queue_update(self.get_queue_state())
            except Exception:
                pass


# Global singleton instance
_combat_queue: Optional[CombatQueue] = None


def get_combat_queue() -> CombatQueue:
    """Get the global combat queue instance."""
    global _combat_queue
    if _combat_queue is None:
        _combat_queue = CombatQueue()
    return _combat_queue


def reset_combat_queue():
    """Reset the global combat queue."""
    global _combat_queue
    if _combat_queue:
        _combat_queue.clear_queue()
    _combat_queue = CombatQueue()
    return _combat_queue
