"""Action planner that maps detections to in-game actions.

Behavior implemented per user spec:
 - The planner ONLY reacts to detections with class `monster`.
 - Skills and attacks auto-target the nearest enemy (no Tab targeting needed).
 - FAST-PACED COMBAT: Attack interval (0.20-0.35s), panic mode (0.10-0.18s) at 3+ enemies.
 - Mid-combat looting: Press F every 1.5s during combat to pick up drops.
 - PANIC MODE: When 3+ enemies detected, enters aggressive mode with reduced delays.
 - After combat the planner enters cooldown where `F` is pressed randomly.

This module uses `input_controller` to send OS-level inputs via Interception driver.

(Stealth mode removed) Uses deterministic timings and immediate actions.
"""
import time
import threading
from typing import List, Dict, Tuple, Optional
from loguru import logger
import input_controller as ic
import skill_combo_config
import random


def _center_of(d: Dict) -> Tuple[int, int]:
    x = float(d.get("x", 0))
    y = float(d.get("y", 0))
    w = float(d.get("width", 0))
    h = float(d.get("height", 0))
    return int(x + w / 2.0), int(y + h / 2.0)


def _iou(a: Dict, b: Dict) -> float:
    ax1 = a.get("x", 0)
    ay1 = a.get("y", 0)
    ax2 = ax1 + a.get("width", 0)
    ay2 = ay1 + a.get("height", 0)
    bx1 = b.get("x", 0)
    by1 = b.get("y", 0)
    bx2 = bx1 + b.get("width", 0)
    by2 = by1 + b.get("height", 0)
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


class ActionPlanner:
    def __init__(self, hwnd: int, enabled: bool = True, conf_thresh: float = 0.25):
        self.hwnd = hwnd
        self.enabled = enabled
        self.conf_thresh = conf_thresh
        self._lock = threading.Lock()
        self._last_action = 0.0
        # Use non-stealth deterministic timing
        self._cooldown = 0.2
        # previous target lock removed; use self._current_target state instead
        
        # Warmup/idle tracking removed (no stealth behavior)
        self._action_count = 0
        
        # Skill Combo Manager - handles skill macros with cooldown tracking
        try:
            from skill_combo_manager import SkillComboManager
            self.skill_combo_manager = SkillComboManager(hwnd)
            logger.info("✓ Skill Combo Manager integrated into Action Planner")
        except Exception as e:
            logger.warning(f"Skill Combo Manager not available: {e}")
            self.skill_combo_manager = None
        # New state machine: scanning -> combat -> cooldown
        self._mode = 'scanning'  # scanning | combat | cooldown
        self._current_target: Optional[Dict] = None
        self._combat_until = 0.0
        self._cooldown_until = 0.0
        self._last_attack = 0.0
        self._attack_interval = (0.15, 0.28)  # FASTER basic attacks between skills
        self._panic_attack_interval = (0.08, 0.15)  # even faster in panic mode
        self._last_cooldown_action = 0.0
        self._cooldown_f_interval = (0.6, 1.8)  # random press F intervals during cooldown ONLY
        
        # PANIC MODE: 3+ enemies triggers aggressive combat with random skill spam
        self._panic_threshold = 3
        self._panic_mode = False
        
        # REMOVED: Mid-combat looting - F is now ONLY pressed during cooldown phase
        # self._combat_loot_interval = 1.5
        # self._last_combat_loot = 0.0
        
        # SEQUENTIAL R/T ALTERNATION - 2 second interval between light/heavy attacks
        self._last_attack_type = None  # 'r' (light) or 't' (heavy)
        self._attack_alternation_interval = 2.0  # seconds between switching attack type
        self._last_attack_switch_time = 0.0
        
        # SEQUENTIAL COMBAT PATTERN: attack -> skill -> attack -> combo in sequence
        self._combat_pattern_index = 0
        self._combat_patterns = ['attack', 'attack', 'skill', 'attack', 'attack', 'combo', 'attack']
        self._last_pattern_action = 0.0
        self._pattern_action_interval = (0.20, 0.40)  # FAST pattern execution
        
        # RANDOM COMBAT TRIGGER during roaming (configurable chance)
        try:
            import skill_combo_config as scc
            self._random_combat_chance = getattr(scc, 'RANDOM_COMBAT_CHANCE', 0.50)
        except Exception:
            self._random_combat_chance = 0.50
        self._last_random_combat_check = 0.0
        self._random_combat_check_interval = 3.0  # check every 3s during roam
        
        # roaming (idle movement) state
        self._roaming = False
        self._roam_thread = None
        self._last_roam_time = 0.0
        self._roam_cooldown = (1.0, 2.0)  # FASTER: 1-2s between roam attempts (was 3-8s)
        # consecutive no-detections counter (trigger roam after N empties)
        self._no_mobs_count = 0
        self._no_mobs_threshold = 2  # FASTER: trigger roam after 2 empty frames (was 5)
        # store last detections so roaming thread can re-check for monsters
        self._last_detections: List[Dict] = []
        # defensive cadence guard
        self._last_defensive_time = 0.0

    def _send_follow_up_attack(self):
        """Fire a light/heavy attack so the character resumes basic attacks."""
        try:
            ic.focus_window(self.hwnd)
            follow = random.choice(['r', 't'])
            ic.tap_key(follow)
            self._last_attack = time.time()
            logger.info(f"ActionPlanner: follow-up basic attack '{follow.upper()}' issued")
        except Exception as e:
            logger.error(f"ActionPlanner: failed to send follow-up attack: {e}")

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)

    def is_enabled(self) -> bool:
        return bool(self.enabled)

    def _find_target_mob(self, detections: List[Dict]) -> Optional[Dict]:
        # priority: mob_oncursor, mob_near, mob_away
        priority = ["mob_oncursor", "mob_near", "mob_away"]
        
        # Get player position (center of screen)
        from utils import get_window_rect
        window_rect = get_window_rect(self.hwnd)
        if window_rect:
            _, _, screen_w, screen_h = window_rect
            player_x = screen_w / 2.0
            player_y = screen_h / 2.0
        else:
            player_x = player_y = 0
        
        # Collect all valid mob targets with their distances to player
        candidates = []
        
        for p in priority:
            for d in detections:
                name = str(d.get("class", "")).lower()
                if name == p and float(d.get("confidence") or 0) >= self.conf_thresh:
                    # check for overlapping mob_target marker
                    has_target_marker = False
                    for m in detections:
                        if str(m.get("class", "")).lower() == "mob_target":
                            if _iou(d, m) > 0.05:
                                has_target_marker = True
                                break
                    
                    # Calculate distance to player (center of screen)
                    mob_cx, mob_cy = _center_of(d)
                    distance = ((mob_cx - player_x) ** 2 + (mob_cy - player_y) ** 2) ** 0.5
                    
                    # Store candidate with priority weight and distance
                    priority_weight = priority.index(p)  # 0=oncursor, 1=near, 2=away
                    candidates.append((d, priority_weight, distance, has_target_marker))
        
        if not candidates:
            return None
        
        # Sort by: priority first (oncursor > near > away), then by distance (nearest first)
        # Prefer mobs with target markers
        candidates.sort(key=lambda x: (x[1], not x[3], x[2]))  # priority, not has_marker, distance
        
        return candidates[0][0]  # Return the closest mob with highest priority

    def _find_map_dots(self, detections: List[Dict]) -> List[Dict]:
        candidates = []
        for d in detections:
            name = str(d.get("class", "")).lower()
            if any(k in name for k in ("map", "dot", "red", "enemy", "map_dot", "map_enemy")):
                candidates.append(d)
        return candidates

    def _find_all_monsters(self, detections: List[Dict]) -> List[Dict]:
        """Return all detections that match the 'monster' class using the new model.

        The updated model only outputs detections with class 'monster' for enemies.
        """
        out = []
        for d in detections:
            name = str(d.get('class', '')).lower()
            # New model uses exact class 'monster'
            if name == 'monster':
                try:
                    if float(d.get('confidence', 0.0)) >= self.conf_thresh:
                        out.append(d)
                except Exception:
                    out.append(d)
        return out

    def _compute_combat_duration(self, target: Dict, window_w: int, window_h: int) -> float:
        """Heuristic: base 5s + scaled by target size and distance to lower-center of screen.

        Larger targets and targets further from the player's lower-center require longer combat windows.
        """
        try:
            tw = float(target.get('width', 0))
            th = float(target.get('height', 0))
            tx, ty = _center_of(target)
        except Exception:
            return 5.0

        area = max(1.0, tw * th)
        window_area = max(1.0, window_w * window_h)
        area_ratio = area / window_area  # small value e.g. 0.01

        # Lower-center reference point (player is slightly lower than center)
        px = window_w / 2.0
        py = window_h * 0.70
        dist = ((tx - px) ** 2 + (ty - py) ** 2) ** 0.5
        max_diag = (window_w ** 2 + window_h ** 2) ** 0.5
        dist_ratio = dist / max(1.0, max_diag)

        # scale more with size than distance. Keep within reasonable bounds
        extra = (area_ratio * 30.0) + (dist_ratio * 8.0)
        duration = max(5.0, 5.0 + extra)
        # clamp to a sane max (e.g., 20s)
        return min(duration, 20.0)
    
    def _find_north_map_dots(self, dots: List[Dict], window_w: int, window_h: int) -> List[Dict]:
        """
        Find map dots that are in the 'north' direction relative to player.
        Assumes minimap is typically in top-right corner and player is at center.
        Dots above the player's position on minimap = north direction.
        """
        if not dots:
            return []
        
        # Find the minimap region (usually top-right quadrant)
        # Assume dots with x > window_w/2 and y < window_h/2 are on minimap
        map_dots = []
        for d in dots:
            dx = float(d.get("x", 0))
            dy = float(d.get("y", 0))
            # Minimap is typically in top-right
            if dx > window_w * 0.5 and dy < window_h * 0.5:
                map_dots.append(d)
        
        if not map_dots:
            return dots  # Fallback: return all dots
        
        # Find the center of minimap dots (approximate player position on minimap)
        avg_x = sum(float(d.get("x", 0)) for d in map_dots) / len(map_dots)
        avg_y = sum(float(d.get("y", 0)) for d in map_dots) / len(map_dots)
        
        # Dots with y < avg_y are "north" of player
        north_dots = [d for d in map_dots if float(d.get("y", 0)) < avg_y]
        
        return north_dots if north_dots else map_dots
    
    # Movement macros removed — this planner focuses on scanning -> combat -> cooldown.

    def plan_and_execute(self, detections: List[Dict], window_rect: Tuple[int, int, int, int]):
        """detections are in target (window) pixel coords; window_rect is (left, top, w, h)
        """
        if not self.enabled:
            return
        
        now = time.time()
        
        # Idle simulation removed (deterministic, continuous execution)
        
        # SYNC FIX: Detection loop controls timing at 1 FPS
        # Action planner executes on EVERY detection update (no extra cooldown)
        # This keeps detection overlay and combat actions perfectly synchronized
        
        # Warmup removed — no extra delays
        
        self._last_action = now
        self._action_count += 1

        # update last detections (for roam thread to consult)
        try:
            with self._lock:
                self._last_detections = list(detections)
        except Exception:
            self._last_detections = list(detections)

        left, top, w, h = window_rect

        # 1) New MONSTER targeting + combat state-machine
        mobs = self._find_all_monsters(detections)
        enemy_count = len(mobs)
        try:
            if self.skill_combo_manager:
                self.skill_combo_manager.set_enemy_count(enemy_count)
        except Exception:
            pass

        # Exit cooldown early if new enemies appear
        if self._mode == 'cooldown':
            if enemy_count > 0:
                self._mode = 'scanning'
                self._current_target = None
                logger.info("ActionPlanner: enemies appeared, exiting cooldown to retarget")
            elif time.time() < self._cooldown_until:
                # schedule random F presses during cooldown
                if time.time() - self._last_cooldown_action >= random.uniform(*self._cooldown_f_interval):
                    try:
                        ic.focus_window(self.hwnd)
                        ic.tap_key('f')
                        self._last_cooldown_action = time.time()
                        logger.info("ActionPlanner(COOLDOWN): pressed 'F' during cooldown")
                    except Exception as e:
                        logger.error(f"ActionPlanner(COOLDOWN): failed to press F: {e}")
                return
            else:
                # cooldown finished — go back to scanning
                self._mode = 'scanning'
                self._current_target = None
                logger.info("ActionPlanner: cooldown finished, back to scanning")

        # SCANNING: detect mobs and acquire a target
        if self._mode == 'scanning':
            if mobs:
                # Choose a reasonable candidate: prefer nearest to lower-center (player area)
                px = w / 2.0
                py = h * 0.70
                def score_m(d):
                    cx, cy = _center_of(d)
                    # smaller score = better (closer and larger)
                    dist = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5
                    area = (d.get('width', 0) or 0) * (d.get('height', 0) or 0)
                    return dist - (area * 0.05)

                mobs.sort(key=score_m)
                candidate = mobs[0]

                # Engage target: skills auto-target so just attack immediately (no Tab needed)
                try:
                    ic.focus_window(self.hwnd)
                    # Immediate attack - skills and attacks auto-target nearest enemy
                    atk = random.choice(['r', 't'])
                    ic.tap_key(atk)
                    logger.info(f"ActionPlanner: engaging with initial attack '{atk.upper()}' (auto-target)")
                except Exception as e:
                    logger.error(f"ActionPlanner: initial attack failed: {e}")

                # Check for panic mode (3+ enemies)
                self._panic_mode = (enemy_count >= self._panic_threshold)
                if self._panic_mode:
                    logger.warning(f"⚠️ PANIC MODE: {enemy_count} enemies detected!")
                
                # start combat mode with duration based on target size/distance
                dur = self._compute_combat_duration(candidate, w, h)
                self._current_target = candidate
                self._combat_until = time.time() + dur
                self._mode = 'combat'
                self._last_attack = 0.0
                self._combat_pattern_index = 0  # reset sequential combat pattern
                self._last_pattern_action = 0.0
                logger.info(f"ActionPlanner: entering COMBAT mode for {dur:.1f}s (panic={self._panic_mode})")
                return

        # If in combat but lost the target while others remain, re-enter scanning to retarget
        if self._mode == 'combat' and self._current_target is not None:
            target_still_visible = any(_iou(self._current_target, m) > 0.05 for m in mobs)
            if not target_still_visible and enemy_count > 0:
                logger.info("ActionPlanner: lost target during combat, reacquiring")
                self._mode = 'scanning'
                self._current_target = None

        # During COMBAT, perform sequential pattern: attack -> skill -> attack -> combo
        if self._mode == 'combat':
            if time.time() < self._combat_until:
                # Update panic mode status based on current enemy count
                self._panic_mode = (enemy_count >= self._panic_threshold)
                
                # Choose attack interval based on panic mode
                attack_interval = self._panic_attack_interval if self._panic_mode else self._attack_interval
                
                # NO MID-COMBAT F PRESS - F is ONLY pressed during cooldown phase after combat ends
                
                # Defensive trigger if outnumbered
                try:
                    import skill_combo_config as scc
                    outnumbered_thresh = getattr(scc, 'OUTNUMBERED_THRESHOLD', 3)
                    defensive_cooldown = getattr(scc, 'DEFENSIVE_COOLDOWN_SEC', 8.0)
                    if enemy_count >= outnumbered_thresh and (time.time() - self._last_defensive_time) >= defensive_cooldown:
                        if self.skill_combo_manager and self.skill_combo_manager.execute_defensive_skill():
                            self._last_defensive_time = time.time()
                            self._send_follow_up_attack()
                            return
                except Exception:
                    pass

                # PANIC MODE: Random skill spam - rapidly execute random skills
                if self._panic_mode:
                    try:
                        if self.skill_combo_manager is not None:
                            self.skill_combo_manager.set_enemy_count(enemy_count)
                            # Try to fire any available skill/combo rapidly
                            if random.random() < 0.7:  # 70% chance to try skill in panic
                                mode, success = self.skill_combo_manager.try_stealth_attack(True)
                                if mode != 'standard_attack' and success:
                                    logger.info(f"ActionPlanner(PANIC): fired '{mode}'")
                                    self._send_follow_up_attack()
                                    return
                    except Exception:
                        pass

                # SEQUENTIAL COMBAT PATTERN execution
                pattern_interval = random.uniform(*self._pattern_action_interval)
                if time.time() - self._last_pattern_action >= pattern_interval:
                    current_pattern = self._combat_patterns[self._combat_pattern_index % len(self._combat_patterns)]
                    executed = False
                    
                    ic.focus_window(self.hwnd)
                    
                    try:
                        import skill_combo_config as scc
                    except Exception:
                        scc = None
                    
                    if current_pattern == 'skill' and self.skill_combo_manager is not None and scc is not None:
                        # Execute a single skill
                        if getattr(scc, 'SKILL_COMBO_ENABLED', True) and getattr(scc, 'COMBAT_USE_SKILLS', True):
                            try:
                                self.skill_combo_manager.set_enemy_count(enemy_count)
                                if self.skill_combo_manager.execute_single_skill():
                                    executed = True
                                    logger.info(f"ActionPlanner(COMBAT): sequential SKILL (pattern {self._combat_pattern_index})")
                                    self._send_follow_up_attack()
                            except Exception:
                                pass
                    
                    elif current_pattern == 'combo' and self.skill_combo_manager is not None and scc is not None:
                        # Execute a combo
                        if getattr(scc, 'SKILL_COMBO_ENABLED', True) and getattr(scc, 'COMBAT_USE_SKILLS', True):
                            try:
                                self.skill_combo_manager.set_enemy_count(enemy_count)
                                if self.skill_combo_manager.try_execute_combos():
                                    executed = True
                                    logger.info(f"ActionPlanner(COMBAT): sequential COMBO (pattern {self._combat_pattern_index})")
                                    self._send_follow_up_attack()
                            except Exception:
                                pass
                    
                    elif current_pattern == 'attack':
                        # SEQUENTIAL R/T ALTERNATION with 2 second switch interval
                        now = time.time()
                        if self._last_attack_type is None:
                            self._last_attack_type = random.choice(['r', 't'])
                            self._last_attack_switch_time = now
                        elif now - self._last_attack_switch_time >= self._attack_alternation_interval:
                            # Switch attack type after 2 seconds
                            self._last_attack_type = 't' if self._last_attack_type == 'r' else 'r'
                            self._last_attack_switch_time = now
                            logger.debug(f"ActionPlanner: switching to {'LIGHT (R)' if self._last_attack_type == 'r' else 'HEAVY (T)'}")
                        
                        try:
                            ic.tap_key(self._last_attack_type)
                            executed = True
                            logger.info(f"ActionPlanner(COMBAT): sequential {self._last_attack_type.upper()} attack")
                        except Exception as e:
                            logger.error(f"ActionPlanner(COMBAT): attack failed: {e}")
                    
                    # Advance pattern if we executed something, or if skill/combo wasn't available, do attack instead
                    if executed:
                        self._combat_pattern_index += 1
                        self._last_pattern_action = time.time()
                        self._last_attack = time.time()
                    elif current_pattern in ['skill', 'combo']:
                        # Skill/combo not ready, fallback to attack
                        try:
                            atk = self._last_attack_type or random.choice(['r', 't'])
                            ic.tap_key(atk)
                            self._last_attack = time.time()
                            self._last_pattern_action = time.time()
                            logger.info(f"ActionPlanner(COMBAT): fallback {atk.upper()} (no skill/combo ready)")
                        except Exception:
                            pass
                        self._combat_pattern_index += 1
                
                return
            else:
                # combat period ended -> if enemies remain, extend; otherwise cooldown
                if enemy_count > 0:
                    extra = max(2.0, min(5.0, 3.0 + 0.5 * enemy_count))
                    self._combat_until = time.time() + extra
                    logger.info(f"ActionPlanner: enemies still present, extending combat by {extra:.1f}s")
                    return
                self._mode = 'cooldown'
                self._cooldown_until = time.time() + 1.0  # 1 second F-spam phase
                self._last_cooldown_action = 0.0
                logger.info("ActionPlanner: combat finished, entering 1s cooldown (F spam)")
                return

        # If we had a locked target (we were attacking), but now no monster target found,
        # check if health still present for locked target; if so, try to click its last pos
        if self._current_target is not None:
            # check health across detections for any mob_combat_health overlapping previous lock
            health_remaining = any((str(d.get("class","")) .lower() == "mob_combat_health" and _iou(self._current_target, d) > 0.05) for d in detections)
            if health_remaining:
                tx, ty = _center_of(self._current_target)
                screen_x = int(left + tx)
                screen_y = int(top + ty)
                # If health remains, stay engaged — press a small attack (R/T) occasionally
                if time.time() - self._last_attack >= random.uniform(*self._attack_interval):
                    ic.focus_window(self.hwnd)
                    try:
                        atk = random.choice(['r', 't'])
                        ic.tap_key(atk)
                        self._last_attack = time.time()
                        logger.info(f"ActionPlanner: locked target attack '{atk.upper()}'")
                    except Exception as e:
                        logger.error(f"ActionPlanner: locked attack failed: {e}")
                return
            else:
                # health gone -> target dead
                logger.info("ActionPlanner: target appears dead, clearing lock")
                self._current_target = None

        # 2) No monsters detected: do nothing (movement macros removed)
        # The planner will optionally perform roaming (look-around / move) when no monsters
        # are present and the user has enabled roaming in configuration.
        if not mobs:
            # Only count this as an "empty detection" if the detection loop also
            # reported that the last inference produced no detections. DetectionController
            # sets `_last_frame_had_detections` on this ActionPlanner instance when available.
            has_frame_detections = getattr(self, '_last_frame_had_detections', None)
            if has_frame_detections is None:
                # detection loop hasn't provided a flag; fall back to previous behavior
                self._no_mobs_count += 1
            else:
                # Only increment when the detection loop explicitly reported no detections
                if has_frame_detections is False:
                    self._no_mobs_count += 1
                else:
                    # detection loop reported detections (even if filtered by conf_thresh)
                    # treat this as a reset of the empty counter
                    self._no_mobs_count = 0
            try:
                import skill_combo_config as scc
            except Exception:
                scc = None

            if scc is not None and getattr(scc, 'ENABLE_ROAM', False):
                # RANDOM COMBAT TRIGGER during roaming - 30% chance to enter combat mode
                now = time.time()
                if now - self._last_random_combat_check >= self._random_combat_check_interval:
                    self._last_random_combat_check = now
                    if random.random() < self._random_combat_chance:
                        logger.info("ActionPlanner(ROAM): RANDOM COMBAT TRIGGER - entering combat mode!")
                        # Enter a brief combat mode even without monsters
                        self._mode = 'combat'
                        self._combat_until = time.time() + random.uniform(2.0, 4.0)
                        self._current_target = None  # No specific target
                        self._combat_pattern_index = 0
                        self._last_pattern_action = 0.0
                        return
                
                # Trigger roaming when we've had N consecutive empties
                if self._no_mobs_count >= self._no_mobs_threshold:
                    now_roam = time.time()
                    # ensure roam cooldown window elapsed
                    min_cd, max_cd = self._roam_cooldown
                    if now_roam - self._last_roam_time >= random.uniform(min_cd, max_cd):
                        if not self._roaming:
                            self._roaming = True
                            self._last_roam_time = now_roam
                            # reset counter to avoid immediate retrigger
                            self._no_mobs_count = 0
                            t = threading.Thread(target=self._perform_roam, daemon=True)
                            self._roam_thread = t
                            t.start()
            # finished handling empty-detection case; return to avoid falling through
            return
        else:
            # reset counter when any mob is detected
            self._no_mobs_count = 0
        # No movement macros — when no monsters are detected, remain idle in scanning mode.

    def _perform_roam(self):
        """Background roaming routine: move forward, then perform a short look-around swipe.

        Runs in a separate thread so it does not block the detection loop.
        """
        try:
            import skill_combo_config as scc
        except Exception:
            scc = None

        # 90-DEGREE CAMERA TURN ROAMING
        # A 90-degree turn typically requires ~400-500 pixels of mouse movement
        # This value may need adjustment based on in-game mouse sensitivity
        TURN_90_DEGREES_PIXELS = 450
        
        max_retries = 5
        try:
            for attempt in range(max_retries):
                # Check for monsters before each attempt
                try:
                    with self._lock:
                        recent = list(self._last_detections)
                    if self._find_all_monsters(recent):
                        logger.info("ActionPlanner(ROAM): monsters detected, aborting roam")
                        break
                except Exception:
                    pass
                
                # Move forward briefly (1.5-2.5s)
                forward_sec = random.uniform(1.5, 2.5)

                try:
                    ic.focus_window(self.hwnd)
                    ic.hold_key('w', forward_sec)
                    logger.debug(f"ActionPlanner(ROAM): moving forward {forward_sec:.1f}s")
                except Exception as e:
                    logger.debug(f"ActionPlanner(ROAM): failed to hold 'w': {e}")

                time.sleep(0.05)

                # 90-DEGREE CAMERA TURN - alternate left/right
                try:
                    ic.focus_window(self.hwnd)
                    # Alternate: even attempts turn right, odd turn left
                    turn_right = (attempt % 2 == 0)
                    direction = 1 if turn_right else -1
                    turn_pixels = TURN_90_DEGREES_PIXELS * direction
                    
                    logger.info(f"ActionPlanner(ROAM): 90° turn {'RIGHT' if turn_right else 'LEFT'}")
                    
                    # Hold right mouse and perform the turn in segments for smoothness
                    ic.hold_mouse_down('right')
                    
                    # Split the turn into 6 segments for smoother camera movement
                    segments = 6
                    segment_pixels = turn_pixels // segments
                    segment_delay = 0.08  # 80ms per segment
                    
                    for seg in range(segments):
                        ic.move_mouse_relative(segment_pixels, 0, steps=1, duration=0.02)
                        time.sleep(segment_delay)
                        
                        # Check for monsters mid-turn
                        if seg == segments // 2:
                            try:
                                with self._lock:
                                    recent = list(self._last_detections)
                                if self._find_all_monsters(recent):
                                    logger.info("ActionPlanner(ROAM): monsters found mid-turn!")
                                    ic.release_mouse('right')
                                    raise StopIteration()
                            except StopIteration:
                                raise
                            except Exception:
                                pass
                    
                    ic.release_mouse('right')
                    
                except StopIteration:
                    break
                except Exception as e:
                    logger.debug(f"ActionPlanner(ROAM): camera turn failed: {e}")
                    try:
                        ic.release_mouse('right')
                    except Exception:
                        pass

                # Brief pause for detection
                time.sleep(0.15)

                # Check for monsters after turn
                try:
                    with self._lock:
                        recent = list(self._last_detections)
                except Exception:
                    recent = list(self._last_detections)

                if self._find_all_monsters(recent):
                    logger.info("ActionPlanner(ROAM): detected monsters, returning to scanning")
                    break

                time.sleep(0.1)

        finally:
            self._roaming = False
            logger.debug("ActionPlanner(ROAM): roam cycle complete")
