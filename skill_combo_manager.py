"""Skill Combo Manager - Handles cooldown tracking and combo execution.

This manager:
- Tracks individual skill cooldowns
- Tracks combo set cooldowns
- Executes combos when all skills are available
- Uses hardware-level inputs via an injected IInputDriver
- Supports stealth attack mode (randomized between Tab-target + R/T attacks, single skill, combo)
"""
import time
import random
from typing import Dict, Optional, List, Tuple
from loguru import logger
import skill_combo_config
from core.interfaces import IInputDriver


# ------------------------------------------------------------------ Mana thresholds
MANA_SAVE_THRESHOLD: float = 0.45   # Abaixo disso, suspende skills de ataque
MANA_TARGET_RATIO: float = 0.70     # Regenera até atingir este nível
MANA_REGEN_KEY: str = "r"           # Tecla que regenera mana


class SkillComboManager:
    """Manages skill combo execution with cooldown tracking."""

    def __init__(self, hwnd: int, driver: Optional[IInputDriver] = None):
        """Initialize the skill combo manager.

        Args:
            hwnd: Window handle for the game
            driver: Input driver (IInputDriver). If None, a default
                InterceptionDriver is created for backward compatibility.
        """
        self.hwnd = hwnd

        if driver is None:
            # Backward compatibility: build the default hardware driver lazily
            from engine.interception_driver import InterceptionDriver
            driver = InterceptionDriver(hwnd=hwnd)
        self.driver = driver
        
        # Cooldown trackers: skill_key -> last_used_time
        self._skill_cooldowns: Dict[str, float] = {}
        
        # Combo cooldown trackers: combo_name -> last_used_time
        self._combo_cooldowns: Dict[str, float] = {}
        
        # Last combo check time (for logging)
        self._last_check_time = 0
        
        # Single skill global cooldown tracker
        self._last_single_skill_use = 0.0

        # Pack-awareness context
        self._enemy_count = 0

        # Mana ratio atual (1.0 = cheia). Inicializado aqui para evitar
        # AttributeError antes do primeiro set_mp_ratio() (frames sem vitals,
        # testes sem monitor ou FSM legado sem frame).
        self._mp_ratio: float = 1.0
        
        # Validate configuration on init
        if not skill_combo_config.validate_configuration():
            logger.error("Skill combo configuration is invalid!")
        
        logger.info("✓ Skill Combo Manager initialized")
    
    def is_skill_ready(self, skill: str) -> bool:
        """Check if a skill is off cooldown.
        
        Args:
            skill: Skill keybind (e.g., '1', 'alt+2', 'ctrl+5')
        
        Returns:
            True if skill is ready to use, False if on cooldown
        """
        skill_lower = skill.lower()
        cooldown = skill_combo_config.get_skill_cooldown(skill_lower)
        
        if skill_lower not in self._skill_cooldowns:
            return True  # Never used, ready to use
        
        last_used = self._skill_cooldowns[skill_lower]
        time_since_use = time.time() - last_used
        
        return time_since_use >= cooldown
    
    def is_combo_ready(self, combo: Dict) -> bool:
        """Check if a combo set is off cooldown.
        
        Args:
            combo: Combo set dictionary
        
        Returns:
            True if combo cooldown is ready, False if on cooldown
        """
        combo_name = combo.get('name', 'unnamed')
        combo_cooldown = combo.get('cooldown', 60.0)
        
        if combo_name not in self._combo_cooldowns:
            return True  # Never used, ready to use
        
        last_used = self._combo_cooldowns[combo_name]
        time_since_use = time.time() - last_used
        
        return time_since_use >= combo_cooldown
    
    def are_all_skills_ready(self, skills: List[str]) -> bool:
        """Check if all skills in a list are off cooldown.
        
        Args:
            skills: List of skill keybinds
        
        Returns:
            True if all skills are ready, False if any are on cooldown
        """
        for skill in skills:
            if not self.is_skill_ready(skill):
                return False
        return True
    
    def get_skill_cooldown_remaining(self, skill: str) -> float:
        """Get remaining cooldown time for a skill.
        
        Args:
            skill: Skill keybind
        
        Returns:
            Remaining cooldown in seconds (0 if ready)
        """
        skill_lower = skill.lower()
        cooldown = skill_combo_config.get_skill_cooldown(skill_lower)
        
        if skill_lower not in self._skill_cooldowns:
            return 0.0
        
        last_used = self._skill_cooldowns[skill_lower]
        time_since_use = time.time() - last_used
        remaining = cooldown - time_since_use
        
        return max(0.0, remaining)
    
    def get_combo_cooldown_remaining(self, combo: Dict) -> float:
        """Get remaining cooldown time for a combo.
        
        Args:
            combo: Combo set dictionary
        
        Returns:
            Remaining cooldown in seconds (0 if ready)
        """
        combo_name = combo.get('name', 'unnamed')
        combo_cooldown = combo.get('cooldown', 60.0)
        
        if combo_name not in self._combo_cooldowns:
            return 0.0
        
        last_used = self._combo_cooldowns[combo_name]
        time_since_use = time.time() - last_used
        remaining = combo_cooldown - time_since_use
        
        return max(0.0, remaining)
    
    def execute_skill(self, skill: str) -> bool:
        """Execute a single skill using hardware-level input.
        
        Args:
            skill: Skill keybind (e.g., '1', 'alt+2', 'ctrl+5')
        
        Returns:
            True if skill was executed, False if failed
        """
        try:
            # Parse and normalize key (modifiers stripped by config parser)
            _, key = skill_combo_config.parse_skill_keybind(skill)

            # Normalize lookup and cooldown check
            skill_lower = key.lower()

            # Respeita a flag 'enabled' dos metadados da skill (default True por retrocompatibilidade)
            meta = skill_combo_config.get_skill_metadata(skill_lower)
            if not meta.get('enabled', True):
                logger.debug(f"Skill '{skill}' (normalized '{skill_lower}') is disabled; skipping execution")
                return False

            if not self.is_skill_ready(skill_lower):
                logger.debug(f"Skill '{skill}' (normalized '{skill_lower}') is on cooldown; skipping execution")
                return False

            # Focus game window and send a single key press via the injected driver
            self.driver.focus()
            self.driver.tap_key(skill_lower)
            logger.debug(f"Skill executed: {skill_lower}")

            # Mark skill as used
            self._skill_cooldowns[skill_lower] = time.time()
            return True
        except Exception as e:
            logger.error(f"Failed to execute skill '{skill}': {e}")
            return False
    
    def execute_combo(self, combo: Dict) -> bool:
        """Execute a combo set (all skills in sequence).
        
        Args:
            combo: Combo set dictionary
        
        Returns:
            True if combo was executed successfully, False otherwise
        """
        combo_name = combo.get('name', 'unnamed')
        # Ignora etapas com teclas fora do mapa de skills (removidas/alteradas na UI).
        skills = skill_combo_config.filter_active_skills(combo.get('skills', []))
        if not skills:
            logger.debug(f"Combo '{combo_name}' sem teclas ativas; ignorado")
            return False
        
        # Use fast mode delay if enabled, otherwise use combo's configured delay
        if getattr(skill_combo_config, 'FAST_MODE_ENABLED', True):
            enemy_count = getattr(self, '_enemy_count', 0)
            panic_threshold = getattr(skill_combo_config, 'PANIC_THRESHOLD', 3)
            if enemy_count >= panic_threshold:
                delay = getattr(skill_combo_config, 'PANIC_MODE_SKILL_DELAY', 0.08)
            else:
                delay = getattr(skill_combo_config, 'FAST_MODE_SKILL_DELAY', 0.12)
        else:
            delay = combo.get('delay_between_skills', 0.5)
        
        logger.info(f"Executing combo: {combo_name} ({len(skills)} skills, delay={delay:.2f}s)")
        
        try:
            # Ensure combo and skills are ready before executing
            if not self.is_combo_ready(combo):
                logger.debug(f"Combo '{combo_name}' is on cooldown; skipping execution")
                return False
            if not self.are_all_skills_ready(skills):
                logger.debug(f"Combo '{combo_name}' has skills on cooldown; skipping execution")
                return False

            # Optional pre-macro focus + delay
            pre_focus = combo.get('pre_focus', None)
            if pre_focus is None:
                pre_focus = skill_combo_config.PRE_MACRO_FOCUS_ENABLED

            if pre_focus:
                pre_delay = combo.get('pre_focus_delay', skill_combo_config.PRE_MACRO_FOCUS_DELAY)
                randomized_pre_delay = skill_combo_config.get_randomized_delay(pre_delay)
                logger.debug(f"Pre-focus enabled for combo '{combo_name}': focusing window and waiting {randomized_pre_delay:.2f}s")
                # Focus the game window then wait before starting the combo
                self.driver.focus()
                time.sleep(randomized_pre_delay)

            for idx, skill in enumerate(skills):
                # Execute the skill (execute_skill now validates per-skill cooldown)
                if not self.execute_skill(skill):
                    logger.warning(f"Combo '{combo_name}' interrupted at skill {idx+1}/{len(skills)}")
                    return False

                # Wait between skills (except after the last one)
                if idx < len(skills) - 1:
                    randomized_delay = skill_combo_config.get_randomized_delay(delay)
                    logger.debug(f"Delay {randomized_delay:.2f}s before next skill")
                    time.sleep(randomized_delay)

            # Mark combo as used
            self._combo_cooldowns[combo_name] = time.time()

            logger.success(f"✓ Combo '{combo_name}' executed successfully")
            return True

        except Exception as e:
            logger.error(f"Error executing combo '{combo_name}': {e}")
            return False
    
    def try_execute_combos(self) -> bool:
        if self.is_mana_low():
            logger.debug(f"Combo blocked: mana baixa ({self._mp_ratio*100:.0f}% < {MANA_SAVE_THRESHOLD*100:.0f}%)")
            return False
        """Try to execute any available combo.
        
        Checks all enabled combos in priority order and executes the first one
        that is ready (all skills off cooldown and combo cooldown ready).
        
        Returns:
            True if a combo was executed, False if none were ready
        """
        if not skill_combo_config.SKILL_COMBO_ENABLED:
            return False
        
        # Get enabled combos in priority order
        combos = skill_combo_config.get_enabled_combo_sets()
        
        if not combos:
            return False
        
        # Try each combo in order
        for combo in combos:
            combo_name = combo.get('name', 'unnamed')
            skills = combo.get('skills', [])

            # Pack-aware metadata
            meta = {}
            try:
                meta = skill_combo_config.get_combo_metadata(combo_name)
            except Exception:
                meta = {}
            # Allow inline combo fields to override
            if 'type' in combo or 'min_enemy_count' in combo or 'save_for_pack' in combo or 'defensive' in combo:
                meta = {**meta, **{k: combo.get(k) for k in ('type', 'min_enemy_count', 'save_for_pack', 'defensive') if combo.get(k) is not None}}

            enemy_count = getattr(self, '_enemy_count', 0)
            min_count = int(meta.get('min_enemy_count', 1))
            save_for_pack = bool(meta.get('save_for_pack', False))
            if enemy_count < min_count and save_for_pack:
                logger.debug(f"Combo '{combo_name}' held for pack (needs {min_count}, have {enemy_count})")
                continue

            # Check if combo cooldown is ready
            if not self.is_combo_ready(combo):
                combo_cd_remaining = self.get_combo_cooldown_remaining(combo)
                logger.debug(f"Combo '{combo_name}' on cooldown: {combo_cd_remaining:.1f}s remaining")
                continue

            # Pula a combo se qualquer skill dela estiver desabilitada nos metadados
            if any(
                not skill_combo_config.get_skill_metadata(skill).get('enabled', True)
                for skill in skills
            ):
                logger.debug(f"Combo '{combo_name}' contains a disabled skill; skipping combo")
                continue

            # Check if all skills are ready
            if not self.are_all_skills_ready(skills):
                # Find which skills are on cooldown
                skills_on_cd = []
                for skill in skills:
                    if not self.is_skill_ready(skill):
                        cd_remaining = self.get_skill_cooldown_remaining(skill)
                        skills_on_cd.append(f"{skill}({cd_remaining:.1f}s)")
                logger.debug(f"Combo '{combo_name}' waiting for skills: {', '.join(skills_on_cd)}")
                continue

            # Combo is ready! Execute it
            logger.info(f"⚡ Combo '{combo_name}' ready - executing!")
            return self.execute_combo(combo)
        
        # No combos were ready
        return False

    def get_ready_combos(self) -> List[Dict]:
        """Return a list of enabled combos that are ready now.

        A combo is considered ready if:
        - The combo's own cooldown has expired, and
        - All of its skills are currently off cooldown.

        Returns:
            A list of combo dicts that are ready to execute.
        """
        if not skill_combo_config.SKILL_COMBO_ENABLED:
            return []

        combos = skill_combo_config.get_enabled_combo_sets()
        ready: List[Dict] = []
        for combo in combos:
            if not self.is_combo_ready(combo):
                continue
            skills = combo.get('skills', [])
            if self.are_all_skills_ready(skills):
                ready.append(combo)
        return ready

    def try_execute_random_combo(self) -> bool:
        """Pick a random ready combo and execute it.

        Returns:
            True if a combo was executed, False otherwise.
        """
        ready = self.get_ready_combos()
        if not ready:
            return False

        combo = random.choice(ready)
        logger.info(f"⚡ Random combo selected: {combo.get('name','unnamed')}")
        return self.execute_combo(combo)
    
    def get_status_summary(self) -> str:
        """Get a summary of all combo and skill cooldown statuses.
        
        Returns:
            Formatted string with cooldown information
        """
        lines = []
        lines.append("=== Skill Combo Status ===")
        
        combos = skill_combo_config.get_enabled_combo_sets()
        for combo in combos:
            combo_name = combo.get('name', 'unnamed')
            combo_cd = self.get_combo_cooldown_remaining(combo)
            
            if combo_cd > 0:
                status = f"⏱ Cooldown: {combo_cd:.1f}s"
            else:
                skills = combo.get('skills', [])
                if self.are_all_skills_ready(skills):
                    status = "✅ READY"
                else:
                    skills_on_cd = []
                    for skill in skills:
                        if not self.is_skill_ready(skill):
                            cd = self.get_skill_cooldown_remaining(skill)
                            skills_on_cd.append(f"{skill}({cd:.1f}s)")
                    status = f"⏳ Waiting: {', '.join(skills_on_cd)}"
            
            lines.append(f"  {combo_name}: {status}")
        
        return '\n'.join(lines)
    
    def reset_cooldowns(self):
        """Reset all cooldowns (for testing/debugging)."""
        self._skill_cooldowns.clear()
        self._combo_cooldowns.clear()
        self._last_single_skill_use = 0.0
        logger.info("All cooldowns reset")

    # ------------------------------
    # Readiness helpers
    # ------------------------------

    # ------------------------------------------------------- mana management
    def set_mp_ratio(self, ratio: float) -> None:
        """Define a proporção atual de mana (0.0 a 1.0)."""
        self._mp_ratio = max(0.0, min(1.0, float(ratio)))

    def get_mp_ratio(self) -> float:
        """Retorna a proporção atual de mana."""
        return self._mp_ratio

    def is_mana_low(self) -> bool:
        """True se a mana estiver abaixo do limiar de economia."""
        return self._mp_ratio < MANA_SAVE_THRESHOLD

    def is_mana_recovered(self) -> bool:
        """True se a mana já se recuperou até o alvo."""
        return self._mp_ratio >= MANA_TARGET_RATIO

    def available_single_skills(self) -> List[str]:
        """Return list of skills from pool that are currently off cooldown.

        Respects SINGLE_SKILL_POOL (or all skills if empty) and per-skill cooldowns.
        Does not consider the global single skill cooldown (GCD).
        """
        pool = skill_combo_config.filter_active_skills(
            skill_combo_config.SINGLE_SKILL_POOL
        ) or list(skill_combo_config.SKILL_COOLDOWNS.keys())

        if self.is_mana_low():
            # Mana baixa: apenas regeneraçăo (R) e habilidades defensivas
            result = []
            # Sempre inclui a tecla de regeneraçăo de mana mesmo que năo esteja no pool
            if MANA_REGEN_KEY not in pool:
                pool = list(pool) + [MANA_REGEN_KEY]
            for s in pool:
                if not self.is_skill_ready(s):
                    continue
                if not skill_combo_config.get_skill_metadata(s).get('enabled', True):
                    continue
                meta = skill_combo_config.get_skill_metadata(s)
                if meta.get('defensive', False) or s.lower() == MANA_REGEN_KEY:
                    result.append(s)
            return result

        return [
            s for s in pool
            if self.is_skill_ready(s)
            and skill_combo_config.get_skill_metadata(s).get('enabled', True)
        ]

    def has_available_single_skill(self) -> bool:
        """True if GCD is ready and at least one pool skill is off cooldown."""
        if not self.is_single_skill_ready():
            return False
        return len(self.available_single_skills()) > 0

    def has_ready_combo(self) -> bool:
        """True if at least one enabled combo is fully ready now."""
        return len(self.get_ready_combos()) > 0

    def set_enemy_count(self, count: int):
        try:
            self._enemy_count = max(0, int(count))
        except Exception:
            self._enemy_count = 0
    
    def choose_attack_mode(self) -> str:
        """Choose attack mode based on stealth configuration.
        
        Returns:
            'standard_attack', 'single_skill', or 'combo_set'
        """
        if not skill_combo_config.STEALTH_ATTACK_MODE_ENABLED:
            return 'combo_set'  # Default to combo execution
        
        weights = skill_combo_config.ATTACK_MODE_WEIGHTS
        modes = list(weights.keys())
        probabilities = list(weights.values())
        
        # Normalize probabilities
        total = sum(probabilities)
        if total != 1.0:
            probabilities = [p / total for p in probabilities]
        
        chosen = random.choices(modes, weights=probabilities, k=1)[0]
        return chosen

    def choose_actionable_mode(self, has_health: bool) -> str:
        """Choose an attack mode using weights but ensure it's actionable.

        Filters choices by readiness (available skills/ready combos). If no weighted choice is actionable,
        falls back in this order: single_skill -> combo_set -> standard_attack, preferring modes that are
        executable at this moment.
        """

        # Gather readiness
        single_ready = self.has_available_single_skill()
        combo_ready = self.has_ready_combo()

        # Build weighted candidate list conditioned on readiness
        weights = skill_combo_config.ATTACK_MODE_WEIGHTS.copy()
        candidates: List[Tuple[str, float]] = []
        for mode, w in weights.items():
            if mode == 'single_skill' and not single_ready:
                continue
            if mode == 'combo_set' and not combo_ready:
                continue
            candidates.append((mode, max(0.0, float(w))))

        if candidates:
            modes, probs = zip(*candidates)
            total = sum(probs)
            if total <= 0:
                modes = ('single_skill', 'combo_set', 'standard_attack')
                probs = (1, 1, 1)
            choice = random.choices(modes, weights=probs, k=1)[0]
            return choice

        # No weighted candidate is ready; pick best available fallback
        if single_ready:
            return 'single_skill'
        if combo_ready:
            return 'combo_set'
        return 'standard_attack'
    
    def is_single_skill_ready(self) -> bool:
        """Check if single skill global cooldown is ready.
        
        Returns:
            True if can use single skill, False if on GCD
        """
        gcd = skill_combo_config.SINGLE_SKILL_GLOBAL_COOLDOWN
        time_since_use = time.time() - self._last_single_skill_use
        return time_since_use >= gcd
    
    def execute_single_skill(self) -> bool:
        """Execute a single random skill from the pool.
        
        Returns:
            True if skill was executed, False otherwise
        """
        if not self.is_single_skill_ready():
            logger.debug("Single skill on global cooldown")
            return False
        

        if self.is_mana_low():
            # Prioridade: regenerar mana com R
            if self.is_skill_ready(MANA_REGEN_KEY):
                logger.info(f"Mana baixa ({self._mp_ratio*100:.0f}%) — regenerando com {MANA_REGEN_KEY}")
                if self.execute_skill(MANA_REGEN_KEY):
                    self._last_single_skill_use = time.time()
                    return True
            # Se R năo estiver disponível, tentar skill defensiva
            if self.execute_defensive_skill():
                return True
            return False

        # Get skill pool (ignora teclas fora do mapa de skills)
        skill_pool = skill_combo_config.filter_active_skills(
            skill_combo_config.SINGLE_SKILL_POOL
        )
        if not skill_pool:
            # Pool vazio ou totalmente desatualizado: usa todas as skills do mapa
            skill_pool = list(skill_combo_config.SKILL_COOLDOWNS.keys())

        # Filter to only skills that are off cooldown
        available_skills = [s for s in skill_pool if self.is_skill_ready(s)]

        if not available_skills:
            logger.debug("No skills available in single skill pool")
            return False

        enemy_count = getattr(self, '_enemy_count', 0)

        # Partition by metadata for pack-aware preference
        preferred: List[str] = []
        fallback: List[str] = []
        for s in available_skills:
            meta = skill_combo_config.get_skill_metadata(s)
            min_count = int(meta.get('min_enemy_count', 1))
            save_for_pack = bool(meta.get('save_for_pack', False))
            skill_type = str(meta.get('type', 'single')).lower()

            if enemy_count < min_count and save_for_pack:
                # Skip if we should hold this for larger packs
                continue

            is_aoe = skill_type in ('aoe', 'cleave')
            if enemy_count >= 2 and is_aoe:
                preferred.append(s)
            elif enemy_count < 2 and not is_aoe:
                preferred.append(s)
            else:
                fallback.append(s)

        candidate_pool = preferred if preferred else fallback
        if not candidate_pool:
            logger.debug("No skills match pack-aware constraints; using first available")
            candidate_pool = available_skills

        skill = random.choice(candidate_pool)

        # Execute the skill
        if self.execute_skill(skill):
            self._last_single_skill_use = time.time()
            return True

        return False

    def execute_defensive_skill(self) -> bool:
        """Execute the first ready defensive skill that matches pack constraints.

        Returns:
            True if a defensive skill was executed.
        """
        enemy_count = getattr(self, '_enemy_count', 0)

        defensive_skills = []
        for skill, cd in skill_combo_config.SKILL_COOLDOWNS.items():
            meta = skill_combo_config.get_skill_metadata(skill)
            if not meta or not meta.get('defensive', False):
                continue
            if not meta.get('enabled', True):
                continue
            min_count = int(meta.get('min_enemy_count', 1))
            save_for_pack = bool(meta.get('save_for_pack', False))
            if enemy_count < min_count and save_for_pack:
                continue
            if self.is_skill_ready(skill):
                defensive_skills.append(skill)

        if not defensive_skills:
            return False

        # Prefer the first ready defensive skill (deterministic ordering by name for stability)
        defensive_skills.sort()
        chosen = defensive_skills[0]
        return self.execute_skill(chosen)
    
    def try_stealth_attack(self, has_health: bool) -> Tuple[str, bool]:
        """Try to execute an attack using stealth mode.
        
        Args:
            has_health: Retained for compatibility; health detection no longer gates skill usage
        
        Returns:
            (attack_mode, success) tuple
            attack_mode: 'standard_attack', 'single_skill', or 'combo_set'
            success: True if action was taken
        """
        # Always prioritise ready combos so they fire exactly on cooldown, pack-aware
        if self.has_ready_combo():
            if self.try_execute_combos():
                return ('combo_set', True)

        # If no combo fired, try a single skill next (pack-aware filtering inside)
        if self.has_available_single_skill():
            if self.execute_single_skill():
                return ('single_skill', True)

        # Optional stealth weighting still allows additional attempts, but should not
        # prevent combos/skills from running as soon as they are ready.
        if skill_combo_config.STEALTH_ATTACK_MODE_ENABLED:
            mode = self.choose_actionable_mode(has_health)
            if mode == 'combo_set':
                if self.try_execute_combos():
                    return ('combo_set', True)
            elif mode == 'single_skill':
                if self.execute_single_skill():
                    return ('single_skill', True)

        # Nothing executed – fall back to standard attacks handled by the planner
        return ('standard_attack', False)




