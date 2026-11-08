"""Máquina de estados de combate (FSM) — arquitetura limpa.

Orquestra três serviços isolados extraídos do legado ``action_planner``:

* ``TargetSelector``  — escolhe o inimigo mais próximo / detecta alvo travado.
* ``CombatPattern``   — ritmo de combate (padrão sequencial, pânico, defensivo).
* ``RoamController``  — exploração do mapa (andar + girar câmera) em thread.

Toda emissão de input flui por ``IInputDriver`` (core.interfaces). As skills,
combos e defensivos são delegados ao ``SkillComboManager`` (injeção do mesmo
driver), cujos métodos retornam ``bool`` e servem de callbacks do CombatPattern.

Concorrência de mouse: o RoamController segura o botão direito do mouse durante
o giro de câmera. Por isso, ao detectar hostis em ROAMING a FSM aborta o roaming
(``stop``) e aguarda a thread finalizar (``join``) ANTES de emitir ações de
combate — garantindo que o mouse esteja livre.

Estados: STANDBY <-> ROAMING <-> COMBAT <-> COOLDOWN.
"""
from __future__ import annotations

import time
from collections.abc import Sequence
from enum import Enum, auto

from loguru import logger

from core.interfaces import DetectionResult, IInputDriver
from core.taxonomy import TARGET_TAGS
from engine.combat_pattern import CombatPattern
from engine.roam_controller import (
    TURN_90_DEGREES_PIXELS,
    TURN_SEGMENTS,
    RoamController,
)
from engine.target_selector import TargetSelector
from engine.vitals_monitor import VitalsMonitor


class BotState(Enum):
    STANDBY = auto()
    ROAMING = auto()
    COMBAT = auto()
    COOLDOWN = auto()


class CombatFSM:
    # Intervalo mínimo (s) entre saques oportunistas intercalados durante o
    # COMBAT. Evita travar a rotação de skills quando o ícone de loot persiste
    # na tela após um respawn. Faixa recomendada: 1.5s a 2.0s.
    _LOOT_COOLDOWN: float = 1.8

    def __init__(
        self,
        driver: IInputDriver,
        screen_size: tuple[int, int] = (1920, 1080),
        mode: str = "HYBRID",
        enable_roam: bool | None = None,
        hwnd: int | None = None,
    ) -> None:
        self.driver = driver
        self.screen_w, self.screen_h = screen_size
        self.mode = mode
        # Roaming é ligado por padrão em FULL_AUTO (apenas modos que navegam pelo mapa).
        self.enable_roam = (
            (mode == "FULL_AUTO") if enable_roam is None else enable_roam
        )
        # Perfil "assistido": HYBRID, ou qualquer perfil com roaming desativado.
        # Nesse perfil o bot NÃO caça monstros soltos (ENEMY_TAGS) — ele só entra
        # em combate quando o jogador trava um alvo manualmente (TARGET_TAGS).
        # FULL_AUTO com roaming ativo mantém a caça autônoma por inimigos soltos.
        self.manual_target_only = (
            (mode == "HYBRID") or (not self.enable_roam)
        )
        self.hwnd = hwnd or 0
        self.state = BotState.STANDBY
        self._hp_ratio: float = 1.0

        # Serviços isolados.
        self.selector = TargetSelector()
        self.pattern = CombatPattern(
            emit_attack=self._emit_attack,
            emit_skill=self._emit_skill,
            emit_combo=self._emit_combo,
            emit_defensive=self._emit_defensive,
            emit_followup_attack=self._emit_followup,
        )
        # has_monsters / get_frame consultam o último frame recebido em update().
        self._last_detections: list[DetectionResult] = []
        self._latest_frame = None
        self.roam = RoamController(
            driver=self.driver,
            has_monsters=self._roam_interrupt,
            get_frame=lambda: self._latest_frame,
        )

        # Skills/combos/defensivos (degradação elegante se indisponível).
        self.skill_manager = self._build_skill_manager()

        # Auto-cura (VitalsMonitor): degradação elegante se indisponível.
        try:
            self.vitals: VitalsMonitor | None = VitalsMonitor()
        except Exception as e:
            logger.warning(f"[FSM] VitalsMonitor indisponível: {e}")
            self.vitals = None

        # Temporizadores de estado (usam perf_counter para monotonicidade).
        self._target_lost_time: float = 0.0

        # Mana ratio (reportado pelo VitalsMonitor)
        self._mp_ratio: float = 1.0
        self._state_deadline: float = 0.0
        self._last_action: float = 0.0
        # Controle do saque oportunista intercalado (loot durante o COMBAT).
        self._last_opportunistic_loot: float = 0.0
        # Autodefesa (reação a dano em STANDBY/ROAMING/COOLDOWN): snapshot do
        # HP por frame + janela rolante de 3s p/ acumular golpes < 5%/frame.
        self._hp_snapshot: float | None = None
        self._dmg_window_hp: float | None = None
        self._dmg_window_t: float = 0.0
        # COMBAT sem inimigos detectados (evita queimar skills no vácuo).
        self._combat_no_enemies_since: float = 0.0

    # ------------------------------------------------------------- skills
    def _build_skill_manager(self):
        """Instancia o SkillComboManager injetando o MESMO driver da FSM.

        Degrada silenciosamente (skill_manager=None) se o módulo/config não
        estiver disponível — nesse caso o CombatPattern faz fallback p/ ataques.
        """
        try:
            from skill_combo_manager import SkillComboManager

            mgr = SkillComboManager(self.hwnd, driver=self.driver)
            logger.info("✓ SkillComboManager integrado à CombatFSM")
            return mgr
        except Exception as e:  # pragma: no cover - degradação
            logger.warning(f"SkillComboManager indisponível na FSM: {e}")
            return None

    # ------------------------------------------------------ input callbacks
    def _emit_attack(self, key: str) -> bool:
        try:
            self.driver.focus()
            return bool(self.driver.tap_key(key))
        except Exception:
            return False

    def _emit_skill(self) -> bool:
        if self.skill_manager is None:
            return False
        try:
            return bool(self.skill_manager.execute_single_skill())
        except Exception:
            return False

    def _emit_combo(self) -> bool:
        if self.skill_manager is None:
            return False
        try:
            return bool(self.skill_manager.try_execute_combos())
        except Exception:
            return False

    def _emit_defensive(self) -> bool:
        if self.skill_manager is None:
            return False
        try:
            return bool(self.skill_manager.execute_defensive_skill())
        except Exception:
            return False

    def _emit_followup(self) -> bool:
        """Ataque leve de seguimento após skill/combo/defensivo."""
        try:
            return bool(self.driver.tap_key("r"))
        except Exception:
            return False

    # ------------------------------------------------------ roam callbacks
    def _has_monsters(self) -> bool:
        """Consulta thread-safe ao último frame para abortar o roaming."""
        dets = list(self._last_detections)
        return (
            self.selector.count_enemies(dets) > 0
            or self.selector.has_target_lock(dets)
        )

    def _roam_interrupt(self) -> bool:
        """Callback thread-safe que interrompe o roaming do ``RoamController``.

        Interrompe quando há alvo travado (``TARGET_TAGS``) ou inimigo
        próximo (``ENEMY_TAGS``).
        """
        dets = list(self._last_detections)
        return any(d.class_name in TARGET_TAGS for d in dets) or self._has_monsters()

    # ------------------------------------------------------------- helpers
    def update_screen_size(self, w: int, h: int) -> None:
        if w > 0 and h > 0:
            self.screen_w = w
            self.screen_h = h

    def _combat_duration(self, enemy_count: int) -> float:
        """Heurística de duração do combate (6–20s), escalada por nº de inimigos.

        Substitui o deadline fixo de 14s pela lógica rica do action_planner.
        """
        return min(20.0, max(6.0, 8.0 + 1.5 * enemy_count))

    def _opportunistic_loot(
        self, now: float, loots: Sequence[DetectionResult]
    ) -> bool:
        """Saque oportunista intercalado durante o COMBAT.

        Recolhe o loot mais próximo do jogador (``LOOT_TAG``) sem transicionar
        para COOLDOWN, permitindo saquear drops em zonas de respawn rápido
        enquanto outros inimigos seguem em tela. Respeita um cooldown interno
        (``_LOOT_COOLDOWN``) para não interromper a rotação de skills caso o
        ícone de loot continue visível.

        Retorna ``True`` se um toque de loot foi emitido neste frame — nesse
        caso o chamador deve adiar a ação de combate para o frame seguinte.
        """
        if not loots:
            return False
        if now - self._last_opportunistic_loot < self._LOOT_COOLDOWN:
            return False

        target = self.selector.select_nearest_loot(
            loots, self.screen_w, self.screen_h
        )
        if target is None:
            return False

        self._last_opportunistic_loot = now
        logger.info("[FSM] Saque oportunista: loot no chão durante COMBAT")
        try:
            self.driver.focus()
            self.driver.tap_key("f")
            time.sleep(0.06)  # pequeno delay entre toques p/ o jogo processar
            self.driver.tap_key("f")
        except Exception:
            return False
        return True

    def _enter_combat(self, now: float, enemy_count: int) -> None:
        self.state = BotState.COMBAT
        self._target_lost_time = 0.0
        self._combat_no_enemies_since = 0.0
        self._state_deadline = now + self._combat_duration(enemy_count)
        self.pattern.reset()
        logger.info(
            f"[FSM] Hostil detectado -> COMBAT ({enemy_count} inimigo(s), "
            f"{self._combat_duration(enemy_count):.1f}s)"
        )

    # ------------------------------------------------------------- shutdown
    def shutdown(self) -> None:
        """Encerra o roaming de forma limpa (idempotente)."""
        try:
            self.roam.stop()
            self.roam.join(timeout=2.5)
        except Exception as e:  # pragma: no cover
            logger.debug(f"[FSM] shutdown: {e}")

    # ---------------------------------------------------------------- update
    def update(self, detections: Sequence[DetectionResult], frame=None) -> None:
        """Processa um frame de detecções e, opcionalmente, o frame BGRA cru.

        ``frame`` (quando fornecido) alimenta o ``VitalsMonitor`` para a rotação
        de auto-cura; se omitido (testes/legado), o monitor é ignorado neste
        frame. A auto-cura é avaliada em TODOS os estados.
        """
        now = time.perf_counter()
        self._last_detections = list(detections)
        self._latest_frame = frame

        # sem travar movimento ou interromper o combate.
        if frame is not None and self.vitals is not None:
            hp = self.vitals.read_hp(frame)
            mp = self.vitals.read_mp(frame)
            self._hp_ratio = hp
            self._mp_ratio = mp
            # Propagate mana ratio to skill manager and combat pattern
            if self.skill_manager is not None:
                self.skill_manager.set_mp_ratio(mp)
            self.pattern.set_mp_ratio(mp)
            # Autodefesa da coleta: queda súbita de HP sinaliza dano recebido.
            action = self.vitals.evaluate_health(hp)
            if action is not None:
                self.driver.tap_key(action.key)
            # Poção de mana (integração preparada; só dispara com tecla válida).
            mp_action = self.vitals.evaluate_mana(mp)
            if mp_action is not None:
                logger.info(f"[VITALS] Poção de mana ({mp*100:.0f}% MP)")
                self.driver.tap_key(mp_action.key)
            # Telemetria de vitals — loga apenas variação >= 5% ou HP crítico (< 30%).
            if not hasattr(self, "_last_hp_log"):
                self._last_hp_log = hp
            if not hasattr(self, "_last_mp_log"):
                self._last_mp_log = mp
            hp_delta = abs(hp - self._last_hp_log)
            mp_delta = abs(mp - self._last_mp_log)
            hp_critical = hp < 0.30
            if hp_delta >= 0.05 or mp_delta >= 0.05 or hp_critical:
                self._last_hp_log = hp
                self._last_mp_log = mp
                logger.info(
                    f"[VITALS] HP: {hp*100:.0f}% (ratio={hp:.2f}) | "
                    f"MP: {mp*100:.0f}% (ratio={mp:.2f})"
                )

        target_locked = self.selector.has_target_lock(detections)
        enemy_count = self.selector.count_enemies(detections)
        # No perfil assistido (manual_target_only) só o alvo travado pelo jogador
        # conta como hostil: monstros soltos (ENEMY_TAGS) são ignorados para
        # iniciar/permanecer em combate. FULL_AUTO caça inimigos soltos normalmente.
        if self.manual_target_only:
            has_hostiles = target_locked
        else:
            has_hostiles = target_locked or enemy_count > 0
        loots = self.selector.loots(detections)

        match self.state:
            case BotState.STANDBY:
                # Autodefesa: HP caiu em repouso (ex.: monstro atrás da câmera)
                # -> caça o agressor em vez de ficar só tomando poção.
                if self._check_damage_reaction(now, enemy_count, target_locked):
                    return
                if has_hostiles:
                    self._enter_combat(now, enemy_count)
                    return
                # Nada a combater: recolhe loot e, se habilitado, inicia roaming.
                if loots and (now - self._last_action >= 0.5):
                    self.driver.tap_key("f")
                    self._last_action = now
                if self.enable_roam and not self.roam.is_roaming:
                    if self.roam.start():
                        self.state = BotState.ROAMING
                        logger.info("[FSM] Sem hostis -> iniciando ROAMING")

            case BotState.ROAMING:
                # Autodefesa: dano recebido durante a exploração -> revidar.
                if self._check_damage_reaction(now, enemy_count, target_locked):
                    return
                # Hostil apareceu (ou alvo travado) durante a exploração.
                if has_hostiles:
                    self._abort_roam()
                    self._enter_combat(now, enemy_count)
                    return
                # Ciclo thread do RoamController concluiu sem hostis -> reinicia
                # IMEDIATAMENTE um novo ciclo (evita o bot parado no lugar).
                if not self.roam.is_roaming:
                    if self.roam.start():
                        logger.info(
                            "[FSM] Ciclo de roaming concluído sem hostis; "
                            "reiniciando exploração"
                        )
                    else:
                        # Falha atípica ao reiniciar: volta ao STANDBY (retry ~2s).
                        logger.warning("[FSM] Falha ao reiniciar roaming; -> STANDBY")
                        self.state = BotState.STANDBY

            case BotState.COMBAT:
                if target_locked:
                    self._target_lost_time = 0.0
                else:
                    if self._target_lost_time == 0.0:
                        self._target_lost_time = now
                    elif now - self._target_lost_time >= 0.35:
                        logger.info("[FSM] Alvo perdido -> COOLDOWN")
                        self.state = BotState.COOLDOWN
                        self._state_deadline = now + 1.2
                        return

                # Alvos detectados = 0 por 2.0s consecutivos -> encerra o combate
                # fantasma (evita queimar skills no vácuo quando o monstro saiu
                # de campo e apenas o lock de alvo persiste).
                if enemy_count == 0:
                    if self._combat_no_enemies_since == 0.0:
                        self._combat_no_enemies_since = now
                    elif now - self._combat_no_enemies_since >= 2.0:
                        logger.info("[FSM] COMBAT sem inimigos por 2.0s -> COOLDOWN")
                        self.state = BotState.COOLDOWN
                        self._state_deadline = now + 1.2
                        return
                else:
                    self._combat_no_enemies_since = 0.0

                if now > self._state_deadline:
                    logger.info("[FSM] Prazo de combate expirado -> COOLDOWN")
                    self.state = BotState.COOLDOWN
                    self._state_deadline = now + 1.2
                    return

                # Saque oportunista intercalado: recolhe loot no chão sem sair
                # de COMBAT (resolve drops em zonas de respawn rápido). Se um
                # toque de loot foi emitido, adia o ataque para o próximo frame.
                if self._opportunistic_loot(now, loots):
                    return

                # Delega o ritmo ao CombatPattern (skills/combos/defensivo).
                if self.skill_manager is not None:
                    self.skill_manager.set_enemy_count(enemy_count)
                action = self.pattern.step(detections)
                if action != "idle":
                    logger.info(
                        f"[COMBAT] Alvo engajado | Inimigos: {enemy_count} | Ação: {action}"
                    )
            case BotState.COOLDOWN:
                # Autodefesa: ainda tomando dano -> o combate não acabou; revida.
                if self._check_damage_reaction(now, enemy_count, target_locked):
                    return
                if target_locked:
                    self._enter_combat(now, enemy_count)
                    return

                if now - self._last_action >= 0.25:
                    self.driver.tap_key("f")
                    self._last_action = now

                if now >= self._state_deadline:
                    logger.info("[FSM] COOLDOWN concluido -> STANDBY")
                    self.state = BotState.STANDBY

    # -------------------------------------------------------- concorrencia
    def _abort_roam(self) -> None:
        """Aborta o roaming e aguarda a thread liberar o mouse antes do combate."""
        if self.roam.is_roaming:
            logger.info("[FSM] Abortando roaming para entrar em combate")
            self.roam.stop()
            self.roam.join(timeout=2.5)

    # ------------------------------------------------------------ autodefesa
    def _check_damage_reaction(
        self, now: float, enemy_count: int, target_locked: bool
    ) -> bool:
        """Autodefesa fora do COMBAT: detecta queda de HP e caça o agressor.

        Gatilho: queda >= 5% em um único frame OU >= 5% acumulados em uma
        janela rolante de 3s (golpes pequenos que isoladamente ficam abaixo de
        5%/frame). Ação: tecla Tab (alvo mais próximo); se não houver nada no
        campo da câmera, gira a câmera ~180° (agressor atrás do boneco) e
        entra em COMBAT.
        """
        current = self._hp_ratio
        previous = self._hp_snapshot
        self._hp_snapshot = current
        if current is None or previous is None:
            return False  # sem leitura de HP neste frame (sem frame/monitor)
        if current >= previous:
            # HP estável ou subiu (poção): encerra a janela de dano acumulado.
            self._dmg_window_hp = None
            return False
        if self._dmg_window_hp is None or now - self._dmg_window_t > 3.0:
            self._dmg_window_hp = previous
            self._dmg_window_t = now
        frame_drop = previous - current
        total_drop = self._dmg_window_hp - current
        if frame_drop < 0.05 and total_drop < 0.05:
            return False
        logger.warning(
            f"[FSM] Dano sofrido em Roaming! Procurando agressor... "
            f"(HP {previous:.0%} -> {current:.0%})"
        )
        self._dmg_window_hp = None
        # Garante o mouse livre (RoamController pode estar girando a câmera).
        self._abort_roam()
        # Seleciona o alvo mais próximo (Tab).
        self.driver.tap_key("tab")
        # Sem alvo no campo da câmera: gira ~180° p/ trazer o agressor à vista.
        if enemy_count == 0 and not target_locked:
            self._spin_camera_180()
        self._enter_combat(now, enemy_count)
        return True

    def _spin_camera_180(self) -> None:
        """Gira a câmera ~180° (mesmo padrão de pan do RoamController)."""
        total = TURN_90_DEGREES_PIXELS * 2
        segment_pixels = total // TURN_SEGMENTS
        logger.info("[FSM] Girando câmera ~180° à procura do agressor")
        self.driver.focus()
        self.driver.hold_mouse("right", down=True)
        try:
            for _ in range(TURN_SEGMENTS):
                self.driver.move_mouse_relative(
                    segment_pixels, 0, steps=1, duration=0.02
                )
                time.sleep(0.05)
        finally:
            self.driver.hold_mouse("right", down=False)

