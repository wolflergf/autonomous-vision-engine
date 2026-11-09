"""Core interfaces for the autonomous vision engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, List, Optional

import numpy as np


@dataclass
class BoundingBox:
    """Simple bounding box class for DetectionResult.box compatibility."""
    x: float
    y: float
    w: float
    h: float

    @property
    def width(self) -> float:
        return self.w

    @property
    def height(self) -> float:
        return self.h


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Result of a detection (object, target, etc.) in the scene."""
    x: float
    y: float
    width: float
    height: float
    confidence: float
    label: str | None = None
    class_name: str | None = None

    @property
    def box(self) -> BoundingBox:
        """Return BoundingBox with x, y, width, height for test compatibility."""
        return BoundingBox(x=self.x, y=self.y, w=self.width, h=self.height)


class ICaptureEngine(ABC):
    @abstractmethod
    def get_latest_frame(self) -> np.ndarray | None: ...

    @abstractmethod
    def get_dimensions(self) -> tuple[int, int]: ...


class IInferenceEngine(ABC):
    @abstractmethod
    def infer(self, frame: np.ndarray) -> list[DetectionResult]: ...


class IInputDriver(ABC):
    """Interface for input drivers used by the FSM and other engine components."""

    @abstractmethod
    def tap_key(self, key: str, duration: float = 0.01) -> bool: ...

    @abstractmethod
    def send_relative_mouse(self, dx: int, dy: int) -> bool: ...

    @abstractmethod
    def hold_key(self, key: str, duration: float) -> bool: ...

    @abstractmethod
    def key_down(self, key: str) -> bool: ...

    @abstractmethod
    def key_up(self, key: str) -> bool: ...

    @abstractmethod
    def hold_mouse(self, button: str = "right", down: bool = True) -> bool: ...

    @abstractmethod
    def move_mouse_relative(self, dx: int, dy: int, steps: int = 1, duration: float = 0.0) -> bool: ...

    @abstractmethod
    def focus(self) -> bool: ...

    @abstractmethod
    def lock_cursor(self) -> bool: ...

    @abstractmethod
    def unlock_cursor(self) -> bool: ...

    @abstractmethod
    def click_at(self, x: int, y: int, button: str = "left") -> bool: ...