"""Controle de roaming (exploração) desacoplado da FSM.

Portado de ``action_planner.ActionPlanner._perform_roam`` para a arquitetura
limpa. Usa a interface estendida ``IInputDriver`` (``hold_key``,
``hold_mouse``, ``move_mouse_relative``, ``focus``) e consulta um callback
``has_monsters`` para abortar a exploração tão logo um inimigo apareça.

Ciclo de exploração (com abort a qualquer momento):
    move para frente (W por 1.5-2.5s) -> giro de câmera de ~90° (alternando
    direita/esquerda, em 6 segmentos) -> pequenas pausas de detecção.

Desatolamento (stuck): quando um ``get_frame`` é injetado, a caminhada é
amostrada periodicamente; se a cena ficar estática enquanto 'W' está pressionada
(colisão com relevo/parede), confirma o travamento e executa
salto -> marcha-ré -> giro de ~180° para reposicionar a trajetória.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

import numpy as np
from loguru import logger

from core.interfaces import IInputDriver

# Pixels de mouse que aproximam um giro de 90° (depende da sensibilidade do jogo).
TURN_90_DEGREES_PIXELS = 450
# Número de segmentos usados para suavizar o giro de câmera.
TURN_SEGMENTS = 6
SEGMENT_DELAY = 0.08


class RoamController:
    """Executa um ciclo de roaming em uma thread separada."""

    def __init__(
        self,
        driver: IInputDriver,
        has_monsters: Callable[[], bool],
        *,
        forward_range: tuple[float, float] = (1.5, 2.5),
        turn_pixels: int = TURN_90_DEGREES_PIXELS,
        max_retries: int = 5,
        get_frame: Callable[[], np.ndarray | None] | None = None,
        sample_interval: float = 1.5,
        mad_threshold: float = 3.0,
        stuck_threshold: float = 3.0,
        reverse_range: tuple[float, float] = (1.0, 1.5),
        hold_chunk: float = 0.25,
    ) -> None:
        self._driver = driver
        self._has_monsters = has_monsters
        self.forward_range = forward_range
        self.turn_pixels = turn_pixels
        self.max_retries = max_retries

        # Desatolamento (stuck). Se ``get_frame`` for None, a detecção fica
        # desabilitada e a caminhada usa o comportamento clássico (bloqueante).
        self._get_frame = get_frame
        self.sample_interval = sample_interval
        self.mad_threshold = mad_threshold
        self.stuck_threshold = stuck_threshold
        self.reverse_range = reverse_range
        self.hold_chunk = hold_chunk

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self.roaming = False

    # ------------------------------------------------------------- estado
    @property
    def is_roaming(self) -> bool:
        return self.roaming

    def start(self) -> bool:
        """Inicia um ciclo de roaming se ainda não estiver roaming."""
        with self._lock:
            if self.roaming:
                return False
            self.roaming = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """Sinaliza para o ciclo atual abortar o mais rápido possível."""
        self._stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # ---------------------------------------------------------------- ciclo
    def _run(self) -> None:
        try:
            for attempt in range(self.max_retries):
                if self._stop_event.is_set() or self._has_monsters():
                    logger.info("[Roam] monstros detectados, abortando roaming")
                    break

                self._move_forward()
                if self._stop_event.is_set() or self._has_monsters():
                    break

                if not self._camera_turn(attempt):
                    break

                if self._has_monsters():
                    logger.info("[Roam] monstros detectados, voltando ao combate")
                    break
        finally:
            self.roaming = False
            logger.debug("[Roam] ciclo de exploração concluído")

    def _move_forward(self) -> None:
        duration = self.forward_range[0] + (
            self.forward_range[1] - self.forward_range[0]
        ) * self._rng_unit()

        # Sem frames disponíveis: comportamento clássico (bloqueia em W).
        if self._get_frame is None:
            self._driver.focus()
            self._driver.hold_key("w", duration)
            logger.debug(f"[Roam] movendo para frente por {duration:.1f}s")
            return

        # Modo com detecção de travamento: caminha em pedaços amostrando a
        # imagem. Se a cena ficar estática enquanto 'W' está pressionada
        # (colisão com relevo/parede), confirma e desatola.
        self._driver.focus()
        # Orçamento estendido permite acumular o tempo estático mínimo.
        budget = max(duration, self.stuck_threshold + self.sample_interval * 2.0)
        walked = 0.0
        since_sample = 0.0
        static_since: float | None = None
        last_patch = self._center_patch(self._get_frame())

        while walked < budget:
            if self._stop_event.is_set() or self._has_monsters():
                break
            self._driver.hold_key("w", self.hold_chunk)
            walked += self.hold_chunk
            since_sample += self.hold_chunk

            if since_sample < self.sample_interval:
                continue
            since_sample = 0.0

            patch = self._center_patch(self._get_frame())
            if patch is None or last_patch is None or patch.shape != last_patch.shape:
                last_patch = patch
                continue

            mad = self._mean_abs_diff(patch, last_patch)
            last_patch = patch
            now = time.perf_counter()
            if mad < self.mad_threshold:
                if static_since is None:
                    static_since = now
                elif now - static_since >= self.stuck_threshold:
                    logger.warning(
                        f"[Roam] Travamento detetado (mad={mad:.2f}) — desatolando"
                    )
                    self._unstuck()
                    return
            else:
                static_since = None
        logger.debug(f"[Roam] caminhada concluída ({walked:.1f}s)")

    def _camera_turn(self, attempt: int) -> bool:
        turn_right = (attempt % 2 == 0)
        direction = 1 if turn_right else -1
        total = self.turn_pixels * direction
        segment_pixels = int(total / TURN_SEGMENTS)

        logger.info(f"[Roam] giro 90° {'DIREITA' if turn_right else 'ESQUERDA'}")
        self._driver.focus()
        self._driver.hold_mouse("right", down=True)
        try:
            for seg in range(TURN_SEGMENTS):
                self._driver.move_mouse_relative(segment_pixels, 0, steps=1, duration=0.02)
                time.sleep(SEGMENT_DELAY)
                if seg == TURN_SEGMENTS // 2 and self._has_monsters():
                    logger.info("[Roam] monstros no meio do giro!")
                    return False
        finally:
            self._driver.hold_mouse("right", down=False)
        return True

    # --------------------------------------------------------- desatolamento
    def _center_patch(self, frame) -> np.ndarray | None:
        """Extrai a área central do frame em grayscale (float32).

        A região evita o HUD superior e a barra de vida inferior. Retorna None
        se o frame for inválido.
        """
        if frame is None or getattr(frame, "ndim", 0) < 2:
            return None
        h, w = frame.shape[:2]
        x0, x1 = int(w * 0.35), int(w * 0.65)
        y0, y1 = int(h * 0.40), int(h * 0.75)
        if x1 <= x0 or y1 <= y0:
            return None
        crop = frame[y0:y1, x0:x1, :3].astype("float32")  # BGR
        # Luminância BT.601 sobre BGRA/BGR.
        return crop[..., 0] * 0.114 + crop[..., 1] * 0.587 + crop[..., 2] * 0.299

    @staticmethod
    def _mean_abs_diff(a: np.ndarray, b: np.ndarray) -> float:
        """Diferença absoluta média entre dois patches de mesma forma."""
        return float(np.abs(a - b).mean())

    def _unstuck(self) -> None:
        """Rotina de desatolamento: salto, marcha-ré e giro de ~180°."""
        self._driver.focus()
        # 1) 'W' já não está pressionado (hold_key expirou). 2) Salto p/ desníveis.
        self._driver.tap_key("space")
        time.sleep(0.1)
        # 3) Marcha-atrás por 1.0-1.5s.
        reverse = self.reverse_range[0] + (
            self.reverse_range[1] - self.reverse_range[0]
        ) * self._rng_unit()
        logger.info(f"[Roam] desatolando: marcha-ré {reverse:.1f}s")
        self._driver.hold_key("s", reverse)
        # 4) Giro de ~180° para reposicionar a trajetória.
        self._camera_turn_degrees(180)

    def _camera_turn_degrees(self, degrees: int) -> None:
        """Gira a câmera `degrees` graus, alternando a direção pelo relógio."""
        direction = 1 if (int(time.time()) % 2 == 0) else -1
        total = int(self.turn_pixels * (degrees / 90.0)) * direction
        segment_pixels = int(total / TURN_SEGMENTS)
        self._driver.focus()
        self._driver.hold_mouse("right", down=True)
        try:
            for _ in range(TURN_SEGMENTS):
                self._driver.move_mouse_relative(
                    segment_pixels, 0, steps=1, duration=0.02
                )
                time.sleep(SEGMENT_DELAY)
                if self._stop_event.is_set():
                    break
        finally:
            self._driver.hold_mouse("right", down=False)

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _rng_unit() -> float:
        import random

        return random.random()
