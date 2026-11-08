# core/interfaces.py
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class DetectionResult:
    x: float
    y: float
    width: float
    height: float
    confidence: float
    class_name: str


class ICaptureEngine(ABC):
    @abstractmethod
    def get_latest_frame(self) -> np.ndarray | None:
        """Retorna o frame mais recente sem alocar cópias adicionais na memória."""

    @abstractmethod
    def get_dimensions(self) -> tuple[int, int]:
        """Retorna largura e altura da janela capturada."""


class IInferenceEngine(ABC):
    @abstractmethod
    def infer(self, frame: np.ndarray) -> list[DetectionResult]:
        """Executa a inferência de visão computacional."""


class IInputDriver(ABC):
    @abstractmethod
    def tap_key(self, key: str, duration: float = 0.01) -> bool:
        pass

    @abstractmethod
    def send_relative_mouse(self, dx: int, dy: int) -> bool:
        pass

    # ------------------------------------------------------------------
    # Capacidades estendidas (aditivas).
    #
    # Possuem implementação padrão para NÃO quebrar implementações
    # existentes de IInputDriver nem o contrato de core/interfaces.py.
    # Implementações concretas (ex.: InterceptionDriver) devem sobrescrever
    # para habilitar roaming / ações que exigem tecla mantida e mouse.
    # ------------------------------------------------------------------

    def hold_key(self, key: str, duration: float) -> bool:
        """Mantém uma tecla pressionada por `duration` segundos.

        Default: degrada para um simples tap (comportamento conservador).
        """
        return self.tap_key(key)

    def key_down(self, key: str) -> bool:
        """Mantém uma tecla pressionada até ``key_up`` (caminhada fluida).

        Default: degrada para um tap (comportamento conservador).
        """
        return self.tap_key(key)

    def key_up(self, key: str) -> bool:
        """Solta uma tecla mantida por ``key_down``.

        Default: sem efeito (retorna True), indicando não-suportado.
        """
        return True

    def hold_mouse(self, button: str = "right", down: bool = True) -> bool:
        """Pressiona (down=True) ou solta (down=False) um botão do mouse,
        SEM soltar automaticamente — usado para arrastar a câmera no roaming.

        Default: sem efeito (retorna False), indicando não-suportado.
        """
        return False

    def move_mouse_relative(
        self, dx: int, dy: int, steps: int = 1, duration: float = 0.0
    ) -> bool:
        """Move o mouse por deltas relativos (pan de câmera).

        Default: reutiliza send_relative_mouse com um único passo.
        """
        return self.send_relative_mouse(dx, dy)

    def focus(self) -> bool:
        """Traz a janela alvo para foreground (best-effort).

        Default: sem efeito (retorna True para não interromper o fluxo).
        """
        return True

    def lock_cursor(self) -> bool:
        """Confina o cursor à janela do jogo (ClipCursor).

        Default: sem efeito (retorna True), indicando não-suportado.
        """
        return True

    def unlock_cursor(self) -> bool:
        """Libera o cursor confinado (ClipCursor(None)).

        Default: sem efeito (retorna True), indicando não-suportado.
        """
        return True

    def click_at(self, x: int, y: int, button: str = "left") -> bool:
        """Clica em coordenadas absolutas (x, y) na tela.

        Usado para selecionar alvos (ex.: nós de recurso para auto-pathing).

        Default: sem efeito (retorna False), indicando não-suportado.
        Implementações concretas (ex.: InterceptionDriver) devem sobrescrever.
        """
        return False
