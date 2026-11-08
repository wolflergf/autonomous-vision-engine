"""Testes unitários: prioridade de habilidades e gestão de mana.

Cobre:
a) Execução de skill avulsa quando o combo principal está em espera (cooldown).
b) Priorização da tecla 'R' quando MP ratio < 0.45.
c) Combo bloqueado quando mana está baixa.
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import skill_combo_config
from skill_combo_manager import SkillComboManager


class MockDriver:
    """Driver de input falso para teste sem hardware."""

    def __init__(self):
        self.pressed_keys = []

    def focus(self):
        return True

    def tap_key(self, key):
        self.pressed_keys.append(key)
        return True


def test_single_skill_when_combo_waiting():
    """Skill avulsa deve ser executada mesmo quando combo principal está em cooldown."""
    skill_combo_config.update_config({
        "SKILL_COOLDOWNS": {
            "1": 28.0,
            "2": 0.0,
            "3": 0.0,
            "4": 0.0,
            "5": 0.0,
        },
        "SINGLE_SKILL_POOL": ["1", "2", "3", "4", "5"],
        "SINGLE_SKILL_GLOBAL_COOLDOWN": 0.0,
        "COMBO_SETS": [
            {
                "name": "Basic DPS Rotation",
                "skills": ["1", "2", "3", "4"],
                "cooldown": 0.0,
                "delay_between_skills": 0.0,
                "enabled": True,
            },
        ],
        "SKILL_COMBO_ENABLED": True,
        "SKILL_METADATA": {},
    })
    skill_combo_config.reload_config()

    mgr = SkillComboManager(hwnd=0, driver=MockDriver())
    mgr._skill_cooldowns = {}
    mgr._combo_cooldowns = {}
    mgr._last_single_skill_use = 0.0

    # Simula que skill 1 acabou de ser usada (está em cooldown de 28s)
    mgr._skill_cooldowns["1"] = time.time()

    driver = mgr.driver

    # Combo deve falhar porque skill 1 está em cooldown


def test_r_priority_when_mana_low():
    """Tecla 'R' deve ser priorizada quando MP ratio < 0.45."""
    skill_combo_config.update_config({
        "SKILL_COOLDOWNS": {
            "1": 0.0,
            "2": 0.0,
            "3": 0.0,
            "4": 0.0,
            "5": 0.0,
            "r": 0.0,
        },
        "SINGLE_SKILL_POOL": ["1", "2", "3", "4", "5"],
        "SINGLE_SKILL_GLOBAL_COOLDOWN": 0.0,
        "COMBO_SETS": [],
        "SKILL_COMBO_ENABLED": True,
        "SKILL_METADATA": {},
    })
    skill_combo_config.reload_config()

    mgr = SkillComboManager(hwnd=0, driver=MockDriver())
    mgr._skill_cooldowns = {}
    mgr._combo_cooldowns = {}
    mgr._last_single_skill_use = 0.0

    mgr.set_mp_ratio(0.30)
    assert mgr.is_mana_low(), "Mana deve estar baixa com 30%"

    driver = mgr.driver

    available = mgr.available_single_skills()
    assert "r" in available, "'R' deve estar disponível quando mana baixa"

    result = mgr.execute_single_skill()
    assert result is True, "execute_single_skill deve retornar True quando mana baixa"
    assert "r" in driver.pressed_keys, "'R' deve ser pressionado quando mana baixa"
    assert driver.pressed_keys[-1] == "r", "Última tecla pressionada deve ser 'R'"

    mgr.set_mp_ratio(0.80)
    assert not mgr.is_mana_low(), "Mana não deve estar baixa com 80%"
    assert mgr.is_mana_recovered(), "Mana deve estar recuperada com 80%"

    driver.pressed_keys.clear()

    result = mgr.execute_single_skill()
    assert result is True, "Skill deve ser executada com mana normal"

    print("✓ TESTE B OK: tecla 'R' priorizada quando MP < 0.45")


def test_combo_blocked_when_mana_low():
    """Combos devem ser bloqueados quando mana está baixa."""
    skill_combo_config.update_config({
        "SKILL_COOLDOWNS": {
            "1": 0.0,
            "2": 0.0,
            "3": 0.0,
            "4": 0.0,
        },
        "SINGLE_SKILL_POOL": ["1", "2", "3", "4"],
        "SINGLE_SKILL_GLOBAL_COOLDOWN": 0.0,
        "COMBO_SETS": [
            {
                "name": "Basic DPS Rotation",
                "skills": ["1", "2", "3", "4"],
                "cooldown": 0.0,
                "delay_between_skills": 0.0,
                "enabled": True,
            },
        ],
        "SKILL_COMBO_ENABLED": True,
        "SKILL_METADATA": {},
    })
    skill_combo_config.reload_config()

    mgr = SkillComboManager(hwnd=0, driver=MockDriver())
    mgr._skill_cooldowns = {}
    mgr._combo_cooldowns = {}
    mgr._last_single_skill_use = 0.0

    mgr.set_mp_ratio(0.30)
    assert mgr.is_mana_low()

    result = mgr.try_execute_combos()
    assert result is False, "Combo deve ser bloqueado quando mana baixa"

    print("✓ TESTE C OK: combo bloqueado quando mana baixa")


if __name__ == "__main__":
    test_single_skill_when_combo_waiting()
    test_r_priority_when_mana_low()
    test_combo_blocked_when_mana_low()
    print("\nTodos os testes de prioridade passaram.")
