"""Monitor de vida (Vitals) + rotação de auto-cura do Clérigo.

Processa o frame BGRA cru da captura, amostra a barra de HP do personagem por
limiar de luminância e decide, por patamares de vida, qual habilidade de cura
disparar. Todas as curas do Clérigo aqui mapeadas são *Instant Cast / Mobile*,
portanto o disparo é imediato (``driver.tap_key``) sem travar movimento.

A região da barra, o limiar de cor e as habilidades (tecla, cooldown, limiar de
HP) são configuráveis via a seção ``HEALING`` de ``skill_combo_config.json``
(ver ``skill_combo_config.get_healing_config``).

Patamares (avaliados do mais crítico ao menos crítico):
    * poção   — HP < 0.30 (cooldown de segurança 15s)
    * cura 7  — HP < 0.50 (cura rápida, recarga curta)
    * radiant — HP < 0.70 (cura principal / purificação)
    * regen   — HP < 0.90 (HoT de manutenção; recast natural pelo cooldown 30s)
"""
from __future__ import annotations

import time
from typing import Any, NamedTuple

import numpy as np

try:
    from loguru import logger
except Exception:  # pragma: no cover - fallback mínimo
    import logging

    logger = logging.getLogger(__name__)


class HealAction(NamedTuple):
    """Ação de cura retornada por ``evaluate_health`` (``None`` se nada a fazer)."""

    key: str
    skill: str


class ManaAction(NamedTuple):
    """Ação de poção de mana retornada por ``evaluate_mana``."""

    key: str
    skill: str = "mp_potion"


class VitalsMonitor:
    """Lê a % de vida do frame e retorna a habilidade de cura prioritária."""

    def __init__(self) -> None:
        # Config opcional: degrada com elegância se o módulo/JSON não existir.
        cfg: dict[str, Any] = {}
        try:
            import skill_combo_config as scc

            cfg = scc.get_healing_config() or {}
        except Exception as e:  # pragma: no cover
            logger.warning(f"[Vitals] config de cura indisponível: {e}")

        self.enabled: bool = bool(cfg.get("ENABLED", True))
        # Cada barra tem seu próprio limiar de luminância (HP vermelho vs MP
        # ciano/turquesa claro), lido individualmente do JSON.
        self._hp_bar: dict[str, Any] | None = cfg.get("HP_BAR")
        self._mp_bar: dict[str, Any] | None = cfg.get("MP_BAR")
        self._hp_threshold: float = (
            float(self._hp_bar.get("lum_threshold", 55)) if self._hp_bar else 55.0
        )
        self._mp_threshold: float = (
            float(self._mp_bar.get("lum_threshold", 70)) if self._mp_bar else 70.0
        )
        # Ordem = prioridade (mais crítico primeiro).
        self._skills: dict[str, dict[str, Any]] = cfg.get("SKILLS", {}) or {}
        self._priority = ("potion", "heal", "radiant", "regen")
        # Poção de mana (config separada; integração preparada p/ ações futuras).
        self._mana_potion: dict[str, Any] = (cfg.get("MANA", {}) or {}).get(
            "POTION", {}
        ) or {}
        # Timestamp (perf_counter) da última utilização de cada skill.
        self._last_used: dict[str, float] = {}

    # ------------------------------------------------------------- leitura barras
    def _read_bar(
        self,
        frame: np.ndarray | None,
        bar: dict[str, Any] | None,
        threshold: float,
    ) -> float:
        """Estima a fração preenchida (0.0–1.0) de uma barra por limiar de
        luminância. Reutilizado por ``read_hp`` e ``read_mp`` — cada uma aplica
        seu próprio ``lum_threshold``. Sem região/frame válido, assume cheio
        (1.0) para evitar cura/uso espúrio.
        """
        if frame is None or bar is None:
            return 1.0
        if getattr(frame, "ndim", 0) < 2:
            return 1.0

        h, w = frame.shape[:2]
        x0 = max(0, int(bar.get("x0", 0.0) * w))
        x1 = min(w, int(bar.get("x1", 1.0) * w))
        y0 = max(0, int(bar.get("y0", 0.0) * h))
        y1 = min(h, int(bar.get("y1", 1.0) * h))
        if x1 <= x0 or y1 <= y0:
            return 1.0

        # BGRA -> canais BGR; ignora Alpha se presente.
        crop = frame[y0:y1, x0:x1, :3].astype(np.float32)
        # Luminância (BT.601): 0.114*B + 0.587*G + 0.299*R.
        lum = crop[..., 0] * 0.114 + crop[..., 1] * 0.587 + crop[..., 2] * 0.299
        col_mean = lum.mean(axis=0)  # luminância média por coluna
        filled = int((col_mean > threshold).sum())
        return float(filled) / max(1, col_mean.size)

    def read_hp(self, frame: np.ndarray | None) -> float:
        """Fração de vida (0.0–1.0) da barra de HP (limiar próprio)."""
        return self._read_bar(frame, self._hp_bar, self._hp_threshold)

    def read_mp(self, frame: np.ndarray | None) -> float:
        """Fração de mana (0.0–1.0) da barra de MP (limiar próprio)."""
        return self._read_bar(frame, self._mp_bar, self._mp_threshold)

    # ------------------------------------------------------------- decisão
    def _is_ready(self, skill: str, now: float) -> bool:
        cd = float(self._skills[skill].get("cooldown", 0.0))
        return (now - self._last_used.get(skill, -1e9)) >= cd

    def _ready_since(self, skill: str, cooldown: float, now: float) -> bool:
        """Cooldown pronto para uma skill com recarga arbitrária (genérico)."""
        return (now - self._last_used.get(skill, -1e9)) >= cooldown

    def evaluate_health(self, hp_ratio: float) -> HealAction | None:
        """Retorna a cura prioritária para o HP atual, ou ``None``.

        Avalia do patamar mais crítico ao menos crítico; dispara o primeiro
        cujo limiar de HP foi cruzado E cujo cooldown esteja pronto. Os limiares
        são aninhados (0.30 < 0.50 < 0.70 < 0.90), então em HP crítico com poção
        em recarga a lógica escalona naturalmente para a próxima cura.
        """
        if not self.enabled or not self._skills:
            return None

        now = time.perf_counter()
        for skill in self._priority:
            cfg = self._skills.get(skill)
            if not cfg or not cfg.get("enabled", True):
                continue
            if hp_ratio < float(cfg.get("hp_below", 1.0)) and self._is_ready(skill, now):
                key = str(cfg.get("key", "")).strip()
                if not key or key == "-":
                    # Sem tecla configurada (ex.: poção): indisponível, escala
                    # para a próxima cura em vez de travar a rotação.
                    continue
                self._last_used[skill] = now
                return HealAction(key=key, skill=skill)
        return None

    # ------------------------------------------------------------- mana
    def evaluate_mana(self, mp_ratio: float) -> ManaAction | None:
        """Retorna a poção de mana se MP < limiar e cooldown pronto, senão ``None``.

        Marca o cooldown ao disparar (consistente com ``evaluate_health``). Só
        age se houver uma tecla válida configurada (``MANA.POTION.key``).
        """
        cfg = self._mana_potion
        if not self.enabled or not cfg or not cfg.get("enabled", True):
            return None
        key = str(cfg.get("key", "")).strip()
        if not key or key == "-":
            return None
        now = time.perf_counter()
        cd = float(cfg.get("cooldown", 15.0))
        if mp_ratio < float(cfg.get("mp_below", 0.30)) and self._ready_since(
            "mp_potion", cd, now
        ):
            self._last_used["mp_potion"] = now
            return ManaAction(key=key)
        return None

    def should_use_mp_potion(
        self, mp_ratio: float, threshold: float = 0.30
    ) -> bool:
        """Sinaliza (SEM consumir cooldown) se a poção de mana deveria ser usada.

        Útil para telemetria/decissão externa; quem deve de fato disparar é
        ``evaluate_mana`` (que consome o cooldown).
        """
        cfg = self._mana_potion
        if not self.enabled or not cfg or not cfg.get("enabled", True):
            return False
        key = str(cfg.get("key", "")).strip()
        if not key or key == "-":
            return False
        now = time.perf_counter()
        cd = float(cfg.get("cooldown", 15.0))
        return mp_ratio < float(cfg.get("mp_below", threshold)) and self._ready_since(
            "mp_potion", cd, now
        )
