"""core/taxonomy.py

Taxonomia de classes emitida pelo modelo de visão (models/aion.pt).

Centraliza os nomes das classes para que a FSM, o seletor de alvos e
quaisquer outros módulos compartilhem exatamente as mesmas strings,
evitando "magic strings" espalhadas e divergências de nomenclatura.

ATENÇÃO: o modelo aion.pt emite apenas 7 classes baseadas em "tags".
NÃO existem classes como 'monster', 'mob_combat_health', 'oncursor',
'near' ou 'away' (essas eram do modelo/planner legado já arquivado).
"""

from __future__ import annotations


class DetClass:
    """Nomes exatos das classes emitidas pelo modelo aion.pt (id -> nome)."""

    ENEMY = "Enemy_Tag"            # 0 - inimigo NÃO travado (nameplate)
    FRIENDLY = "Friendly_Tag"      # 1 - aliado
    LOOT = "Loot_Tag"              # 2 - item para saquear
    RESOURCE = "Resource_Tag"      # 3 - nó de coleta
    SELF = "Self_Tag"              # 4 - próprio jogador (referência de posição)
    TARGET = "Target_Tag"          # 5 - alvo atualmente travado
    TOP_TARGET = "Top_Target_Tag"  # 6 - alvo principal / atacável agora


# Mapa id -> nome (espelha model.names do aion.pt). Útil para depuração.
MODEL_CLASSES: dict[int, str] = {
    0: DetClass.ENEMY,
    1: DetClass.FRIENDLY,
    2: DetClass.LOOT,
    3: DetClass.RESOURCE,
    4: DetClass.SELF,
    5: DetClass.TARGET,
    6: DetClass.TOP_TARGET,
}


# --- Grupos semânticos (consumidos pela FSM e pelo seletor de alvos) ---

# Tags que indicam um alvo ativo/travado de combate.
TARGET_TAGS: frozenset[str] = frozenset({DetClass.TARGET, DetClass.TOP_TARGET})

# Tags de inimigos hostis NÃO travados (candidatos a "monstro mais próximo").
ENEMY_TAGS: frozenset[str] = frozenset({DetClass.ENEMY})

# Todas as representações de hostis (travado + não travado).
HOSTILE_TAGS: frozenset[str] = TARGET_TAGS | ENEMY_TAGS

# Tag que representa o próprio jogador (referência p/ cálculo de proximidade).
PLAYER_TAG: str = DetClass.SELF

# Tag de loot.
LOOT_TAG: str = DetClass.LOOT

# Tag de nó de recurso (coleta/craft).
RESOURCE_TAG: str = DetClass.RESOURCE
