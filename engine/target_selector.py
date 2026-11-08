"""Seleção de alvos mais próximos da área do jogador.

Extraído de ``action_planner.ActionPlanner`` (regra ``score_m``) e portado para
a arquitetura limpa: opera sobre ``DetectionResult`` (dataclass com ``x``/``y``
no CANTO superior-esquerdo) e usa exclusivamente a taxonomia oficial em
``core.taxonomy``.

Menor score = melhor alvo (mais próximo do centro inferior da tela e maior).
"""
from __future__ import annotations

import math
from collections.abc import Sequence

from core.interfaces import DetectionResult
from core.taxonomy import ENEMY_TAGS, HOSTILE_TAGS, PLAYER_TAG, TARGET_TAGS

# Fração da altura da janela usada como posição vertical de referência do
# jogador (centro horizontal = w/2). Mantido idêntico ao action_planner legado.
PLAYER_Y_RATIO = 0.70
# Bônus aplicado à área (w*h) do alvo: recompensa alvos maiores/mais próximos.
AREA_BONUS = 0.05


class TargetSelector:
    """Encontra e pontua inimigos com base na distância ao jogador."""

    def _ref_point(self, screen_w: float, screen_h: float) -> tuple[float, float]:
        return screen_w / 2.0, screen_h * PLAYER_Y_RATIO

    def _center(self, d: DetectionResult) -> tuple[float, float]:
        return d.x + d.width / 2.0, d.y + d.height / 2.0

    def score(self, d: DetectionResult, screen_w: float, screen_h: float) -> float:
        """Menor é melhor: distância euclidiana menos bônus de área."""
        px, py = self._ref_point(screen_w, screen_h)
        cx, cy = self._center(d)
        dist = math.hypot(cx - px, cy - py)
        area = max(0.0, d.width) * max(0.0, d.height)
        return dist - (area * AREA_BONUS)

    def enemies(self, detections: Sequence[DetectionResult]) -> list[DetectionResult]:
        """Filtra apenas detecções inimigas (monstros + jogadores hostis)."""
        return [d for d in detections if d.class_name in ENEMY_TAGS]

    def count_enemies(self, detections: Sequence[DetectionResult]) -> int:
        """Conta inimigos sem criar lista intermediária (otimizado)."""
        return sum(1 for d in detections if d.class_name in ENEMY_TAGS)

    def select_best(
        self,
        detections: Sequence[DetectionResult],
        screen_w: float,
        screen_h: float,
    ) -> DetectionResult | None:
        """Retorna o inimigo mais próximo (melhor score) ou ``None``."""
        mobs = self.enemies(detections)
        if not mobs:
            return None
        return min(mobs, key=lambda d: self.score(d, screen_w, screen_h))

    def has_target_lock(self, detections: Sequence[DetectionResult]) -> bool:
        """``True`` se há um alvo travado pelo jogo (UI de lock)."""
        return any(d.class_name in TARGET_TAGS for d in detections)

    def select_target_tag(
        self, detections: Sequence[DetectionResult]
    ) -> DetectionResult | None:
        """Retorna a detecção da UI que representa o alvo travado, se houver."""
        for d in detections:
            if d.class_name in TARGET_TAGS:
                return d
        return None

    def loots(self, detections: Sequence[DetectionResult]) -> list[DetectionResult]:
        from core.taxonomy import LOOT_TAG

        return [d for d in detections if d.class_name == LOOT_TAG]

    def resources(self, detections: Sequence[DetectionResult]) -> list[DetectionResult]:
        """Filtra apenas nós de recurso (``RESOURCE_TAG``)."""
        from core.taxonomy import RESOURCE_TAG

        return [d for d in detections if d.class_name == RESOURCE_TAG]

    def select_nearest_resource(
        self,
        detections: Sequence[DetectionResult],
        screen_w: float,
        screen_h: float,
        max_dist: float | None = None,
    ) -> DetectionResult | None:
        """Retorna o nó de recurso (``RESOURCE_TAG``) mais próximo do jogador.

        Usa o mesmo critério de proximidade/tamanho de ``select_best`` (menor
        ``score`` = melhor). Se ``max_dist`` (em pixels) for informado, ignora
        nós cujo centro esteja além dessa distância do ponto de referência do
        jogador — útil para coletar apenas o que está ao alcance.
        """
        nodes = self.resources(detections)
        if not nodes:
            return None
        if max_dist is not None:
            px, py = self._ref_point(screen_w, screen_h)
            nodes = [
                d
                for d in nodes
                if math.hypot(self._center(d)[0] - px, self._center(d)[1] - py)
                <= max_dist
            ]
            if not nodes:
                return None
        return min(nodes, key=lambda d: self.score(d, screen_w, screen_h))

    def select_nearest_loot(
        self,
        detections: Sequence[DetectionResult],
        screen_w: float,
        screen_h: float,
        max_dist: float | None = None,
    ) -> DetectionResult | None:
        """Retorna o item de loot (``LOOT_TAG``) mais próximo do jogador.

        Usa o mesmo critério de proximidade/tamanho de ``select_best`` (menor
        ``score`` = melhor). Se ``max_dist`` (em pixels) for informado, ignora
        itens cujo centro esteja além dessa distância do ponto de referência do
        jogador — permitindo restringir o saque ao que está ao alcance.
        """
        items = self.loots(detections)
        if not items:
            return None
        if max_dist is not None:
            px, py = self._ref_point(screen_w, screen_h)
            items = [
                d
                for d in items
                if math.hypot(self._center(d)[0] - px, self._center(d)[1] - py)
                <= max_dist
            ]
            if not items:
                return None
        return min(items, key=lambda d: self.score(d, screen_w, screen_h))

    def is_hostile(self, d: DetectionResult) -> bool:
        return d.class_name in HOSTILE_TAGS

    def is_player(self, d: DetectionResult) -> bool:
        return d.class_name == PLAYER_TAG
