"""End-to-end simulation harness for autonomous vision engine.

Provides MockInputDriver and SyntheticViewport for E2E tests without
requiring a physical game client or peripherals.
"""

from __future__ import annotations

import os
import sys

# Add project root to path so core/engine modules are discoverable
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

from core.interfaces import IInputDriver


class MockInputDriver(IInputDriver):
    """Mock input driver that records all actions for audit/verification."""

    def __init__(self) -> None:
        self.recorded_actions: List[Dict[str, Any]] = []
        self.pressed_keys: List[str] = []
        self.released_keys: List[str] = []
        self._cursor_locked: bool = False
        self.cursor_region: tuple[int, int, int, int] | None = None

    def tap_key(self, key: str, duration: float = 0.01) -> bool:
        self.recorded_actions.append({"action": "tap_key", "key": key, "duration": duration})
        return True

    def send_relative_mouse(self, dx: int, dy: int) -> bool:
        self.recorded_actions.append({"action": "send_relative_mouse", "dx": dx, "dy": dy})
        return True

    def hold_key(self, key: str, duration: float) -> bool:
        self.recorded_actions.append({"action": "hold_key", "key": key, "duration": duration})
        return True

    def key_down(self, key: str) -> bool:
        self.recorded_actions.append({"action": "key_down", "key": key})
        self.pressed_keys.append(key)
        return True

    def key_up(self, key: str) -> bool:
        self.recorded_actions.append({"action": "key_up", "key": key})
        if key in self.pressed_keys:
            self.pressed_keys.remove(key)
            self.released_keys.append(key)
        return True

    def hold_mouse(self, button: str = "right", down: bool = True) -> bool:
        self.recorded_actions.append({"action": "hold_mouse", "button": button, "down": down})
        if down:
            self._cursor_locked = True
        else:
            self._cursor_locked = False
        return True

    def move_mouse_relative(self, dx: int, dy: int, steps: int = 1, duration: float = 0.0) -> bool:
        self.recorded_actions.append({"action": "move_mouse_relative", "dx": dx, "dy": dy, "steps": steps, "duration": duration})
        return True

    def focus(self) -> bool:
        self.recorded_actions.append({"action": "focus"})
        return True

    def lock_cursor(self) -> bool:
        self._cursor_locked = True
        self.recorded_actions.append({"action": "lock_cursor"})
        return True

    def unlock_cursor(self) -> bool:
        self._cursor_locked = False
        self.recorded_actions.append({"action": "unlock_cursor"})
        return True

    def click_at(self, x: int, y: int, button: str = "left") -> bool:
        self.recorded_actions.append({"action": "click_at", "x": x, "y": y, "button": button})
        return True

    def clear(self) -> None:
        """Reset all recorded state for a fresh test session."""
        self.recorded_actions.clear()
        self.pressed_keys.clear()
        self.released_keys.clear()
        self._cursor_locked = False

    def get_action_count(self, action: str) -> int:
        return sum(1 for a in self.recorded_actions if a.get("action") == action)

    def get_keys_pressed(self) -> List[str]:
        return self.pressed_keys

    def get_keys_released(self) -> List[str]:
        return self.released_keys

    def was_key_pressed(self, key: str) -> bool:
        """Check if a key was pressed (exists in pressed_keys list)."""
        return key in self.pressed_keys


@dataclass
class BoundingBox:
    x: float
    y: float
    w: float
    h: float


class SyntheticTarget:
    def __init__(self, label: str, confidence: float, box: BoundingBox) -> None:
        self.label = label
        self.confidence = confidence
        self.box = box


class SyntheticViewport:
    def __init__(self, width: int = 640, height: int = 480) -> None:
        self.width = width
        self.height = height
        self._targets: List[SyntheticTarget] = []
        self._frame: np.ndarray | None = None

    def generate_frame(self) -> np.ndarray:
        self._frame = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        return self._frame

    def get_latest_frame(self) -> np.ndarray:
        frame = self.generate_frame()
        for t in self._targets:
            self._draw_target(frame, t)
        return frame

    def add_target(self, label: str, confidence: float, box: BoundingBox) -> None:
        self._targets.append(SyntheticTarget(label, confidence, box))

    def remove_target(self, label: str) -> None:
        self._targets = [t for t in self._targets if t.label != label]

    def clear_all_targets(self) -> None:
        self._targets.clear()

    def set_targets(self, targets: List[SyntheticTarget]) -> None:
        self._targets = targets

    def get_targets(self) -> List[SyntheticTarget]:
        return list(self._targets)

    def to_detection_results(self) -> List[Any]:
        from core.interfaces import DetectionResult
        results: list = []
        for t in self._targets:
            results.append(
                DetectionResult(
                    x=float(t.box.x),
                    y=float(t.box.y),
                    width=float(t.box.w),
                    height=float(t.box.h),
                    label=t.label,
                    confidence=t.confidence,
                )
            )
        return results

    @staticmethod
    def _draw_target(frame: np.ndarray, target: SyntheticTarget) -> None:
        x, y, w, h = target.box.x, target.box.y, target.box.w, target.box.h
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(frame.shape[1], x + w), min(frame.shape[0], y + h)
        if x2 <= x1 or y2 <= y1:
            return
        r = hash(target.label) & 0xFF
        g = (hash(target.label) >> 8) & 0xFF
        b = (hash(target.label) >> 16) & 0xFF
        frame[y1:y2, x1:x2] = [b, g, r, 255]


__all__ = ["MockInputDriver", "SyntheticViewport", "SyntheticTarget", "BoundingBox"]