"""Cadência de combate: padrão sequencial, alternância de ataques e pânico.

Extraído de ``action_planner.ActionPlanner``. Encapsula o "ritmo" do combate
(timing e escolha da próxima ação) de forma pura — toda emissão de input é
delegada aos callbacks (``emit_attack``, ``emit_skill``, ``emit_combo``,
``emit_defensive``) retornando ``bool``. Assim a classe não conhece
``IInputDriver`` nem ``SkillComboManager``, permanecendo isolada e testável.
"""
from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence

from core.interfaces import DetectionResult
from core.taxonomy import ENEMY_TAGS

# Tipos dos emissores de ação (retornam True se conseguiram executar).
EmitAttack = Callable[[str], bool]   # recebe a tecla ('r'/'t')
EmitSkill = Callable[[], bool]
EmitCombo = Callable[[], bool]
EmitDefensive = Callable[[], bool]


class CombatPattern:
    """Gerencia o padrão de combate: ataque -> skill -> ataque -> combo."""

    def __init__(
        self,
        *,
        emit_attack: EmitAttack,
        emit_skill: EmitSkill,
        emit_combo: EmitCombo,
        emit_defensive: EmitDefensive,
        emit_followup_attack: Callable[[], bool] | None = None,
        attack_interval: tuple[float, float] = (0.15, 0.28),
        panic_attack_interval: tuple[float, float] = (0.08, 0.15),
        pattern_interval: tuple[float, float] = (0.20, 0.40),
        attack_alternation: float = 2.0,
        panic_threshold: int = 3,
        outnumbered_threshold: int = 3,
        defensive_cooldown: float = 8.0,
        patterns: Sequence[str] = (
            "attack", "attack", "skill", "attack", "attack", "combo", "attack",
        ),
        rng: random.Random | None = None,
    ) -> None:
        self._emit_attack = emit_attack
        self._emit_skill = emit_skill
        self._emit_combo = emit_combo
        self._emit_defensive = emit_defensive
        self._emit_followup = emit_followup_attack or (lambda: False)

        self.attack_interval = attack_interval
        self.panic_attack_interval = panic_attack_interval
        self.pattern_interval = pattern_interval
        self.attack_alternation = attack_alternation
        self.panic_threshold = panic_threshold
        self.outnumbered_threshold = outnumbered_threshold
        self.defensive_cooldown = defensive_cooldown
        self.patterns: list[str] = list(patterns)

        self._rng = rng or random.Random()

        # Estado interno
        self.pattern_index = 0
        self.last_attack_type: str | None = None
        self.last_attack_switch = 0.0
        self.last_pattern_action = 0.0
        self.last_attack = 0.0
        self.last_defensive = 0.0
        self.panic_mode = False

        # Mana management
        self._mp_ratio: float = 1.0

    def set_mp_ratio(self, ratio: float) -> None:
        """Define a proporção atual de mana (0.0 a 1.0)."""
        self._mp_ratio = max(0.0, min(1.0, float(ratio)))

    def get_mp_ratio(self) -> float:
        """Retorna a proporção atual de mana."""
        return self._mp_ratio

    def is_mana_low(self) -> bool:
        """True se a mana estiver abaixo do limiar de economia (0.45)."""
        return self._mp_ratio < 0.45

    # ------------------------------------------------------------------ util
    @staticmethod
    def count_enemies(detections: Sequence[DetectionResult]) -> int:
        return sum(1 for d in detections if d.class_name in ENEMY_TAGS)

    def is_panic(self, enemy_count: int) -> bool:
        return enemy_count >= self.panic_threshold

    def reset(self) -> None:
        self.pattern_index = 0
        self.last_pattern_action = 0.0
        self.panic_mode = False

    # ------------------------------------------------------------- core step
    def step(self, detections: Sequence[DetectionResult]) -> str:
        """Executa UMA iteração do ritmo de combate e retorna a ação tomada.

        Retornos possíveis: ``'defensive'``, ``'panic'``, ``'skill'``,
        ``'combo'``, ``'attack'`` ou ``'idle'`` (aguardando intervalo).
        """
        enemy_count = self.count_enemies(detections)
        self.panic_mode = self.is_panic(enemy_count)
        now = time.time()

        # 1) Defensivo quando superado em número (respeita cooldown)
        if enemy_count >= self.outnumbered_threshold and (
            now - self.last_defensive >= self.defensive_cooldown
        ):
            if self._emit_defensive():
                self.last_defensive = now
                self._emit_followup()
                return "defensive"

        # 2) Mana baixa: priorizar regeneração com 'R', suspender skills/combos
        if self.is_mana_low():
            interval = self._rng.uniform(*self.pattern_interval)
            if now - self.last_pattern_action < interval:
                return "idle"
            # Forçar ataque com 'R' para regenerar mana
            if self._emit_attack("r"):
                self.last_pattern_action = now
                self.last_attack = now
                self.pattern_index += 1
                return "attack"
            return "idle"

        # 3) Modo pânico: 70% de chance de disparar skill a cada iteração
        if self.panic_mode and self._rng.random() < 0.70:
            if self._emit_skill():
                self._emit_followup()
                return "panic"

        # 4) Padrão sequencial respeitando intervalo
        interval = self._rng.uniform(*self.pattern_interval)
        if now - self.last_pattern_action < interval:
            return "idle"

        action = self.patterns[self.pattern_index % len(self.patterns)]
        executed = False

        if action == "skill":
            if self._emit_skill():
                executed = True
        elif action == "combo":
            if self._emit_combo():
                executed = True
        else:  # 'attack'
            if self._emit_attack(self._choose_attack_key(now)):
                executed = True

        # Avança o padrão se executou; se skill/combo não estavam prontos,
        # faz um ataque básico como fallback e ainda assim avança.
        if executed:
            self.last_pattern_action = now
            self.last_attack = now
            self.pattern_index += 1
            return action

        if action in ("skill", "combo"):
            if self._emit_attack(self.last_attack_type or self._choose_attack_key(now)):
                self.last_pattern_action = now
                self.last_attack = now
            self.pattern_index += 1
            return "attack"

        # ataque falhou: apenas aguarda próximo ciclo
        return "idle"

    # ------------------------------------------------------------- internals
    def _choose_attack_key(self, now: float) -> str:
        """Alterna entre ataque leve (R) e pesado (T) a cada ``attack_alternation`` s."""
        if self.last_attack_type is None:
            self.last_attack_type = self._rng.choice(["r", "t"])
            self.last_attack_switch = now
        elif now - self.last_attack_switch >= self.attack_alternation:
            self.last_attack_type = "t" if self.last_attack_type == "r" else "r"
            self.last_attack_switch = now
        return self.last_attack_type
