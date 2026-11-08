"""Utilitários de geometria para detecções e aproximação de alvos.

Funções puras de cálculo geométrico extraídas da FSM e TargetSelector para
melhor separação de responsabilidades. Todas as funções são stateless e
operam sobre DetectionResult (dataclass com coordenadas x,y do canto superior
esquerdo e dimensões width,height).
"""
from __future__ import annotations

import math

from core.interfaces import DetectionResult


def area_ratio(det: DetectionResult, screen_w: float, screen_h: float) -> float:
    """Calcula razão de área da detecção em relação à tela.
    
    Args:
        det: Detecção com bbox (x, y, width, height)
        screen_w, screen_h: Dimensões da tela
    
    Returns:
        Razão área_detecção / área_tela (0.0 a 1.0, tipicamente < 0.1)
    """
    area = max(0.0, det.width) * max(0.0, det.height)
    screen_area = max(1.0, screen_w * screen_h)
    return area / screen_area


def bbox_base_y(det: DetectionResult) -> float:
    """Retorna coordenada Y da base do bbox (y + height).
    
    A base do bbox representa a "linha dos pés" da entidade detectada,
    útil para determinar proximidade ao jogador em jogos de perspectiva.
    """
    return det.y + det.height


def is_at_feet(
    det: DetectionResult, screen_h: float, y_ratio: float = 0.75
) -> bool:
    """True se base do bbox cruzou linha dos 'pés' do personagem.
    
    Args:
        det: Detecção a verificar
        screen_h: Altura da tela
        y_ratio: Fração da altura da tela considerada "aos pés" (0.0-1.0)
    
    Returns:
        True se base do bbox >= screen_h * y_ratio
    """
    return bbox_base_y(det) >= screen_h * y_ratio


def center_point(det: DetectionResult) -> tuple[float, float]:
    """Retorna centro (cx, cy) da detecção.
    
    Calcula o ponto central do bounding box, útil para cálculos de
    distância e alinhamento de câmera.
    """
    return det.x + det.width / 2.0, det.y + det.height / 2.0


def euclidean_distance(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Distância euclidiana entre dois pontos (x,y).
    
    Args:
        p1, p2: Tuplas (x, y) de coordenadas
    
    Returns:
        Distância em pixels
    """
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])