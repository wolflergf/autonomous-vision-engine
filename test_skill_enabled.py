"""Teste unitário: habilidade com 'enabled=False' não é disparada pela rotação,
mesmo com cooldown zerado."""

import sys
import os

# Garante import dos módulos da raiz do projeto
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import skill_combo_config
from skill_combo_manager import SkillComboManager


class MockDriver:
    """Driver de input falso para teste sem hardware."""

    def focus(self):
        return True

    def tap_key(self, key):
        return True


def test_disabled_skill_not_executed():
    """Skill com enabled=False e cooldown=0 não deve ser executada."""
    # Sobrescreve config via update_config (atualiza globais + grava JSON)
    skill_combo_config.update_config({
        "SKILL_COOLDOWNS": {"1": 0.0, "2": 0.0},
        "SINGLE_SKILL_POOL": ["1", "2"],
        "SKILL_METADATA": {
            "1": {"enabled": False},
            "2": {"enabled": True},
        },
    })
    # Simula recarga em tempo de execução (como a UI faz após salvar)
    skill_combo_config.reload_config()

    mgr = SkillComboManager(hwnd=0, driver=MockDriver())
    # Zera cooldowns internos (nem devem existir para skills nunca usadas)
    mgr._skill_cooldowns = {}
    mgr._last_single_skill_use = 0.0

    # Skill 1: desabilitada, cooldown zerado -> não deve disparar
    result = mgr.execute_skill("1")
    assert result is False, "Skill desabilitada não deve ser disparada"

    # Skill 2: habilitada, cooldown zerado -> deve disparar
    result = mgr.execute_skill("2")
    assert result is True, "Skill habilitada deve ser disparada"

    # available_single_skills não deve incluir a skill desabilitada
    pool = mgr.available_single_skills()
    assert "1" not in pool, "Skill desabilitada não deve aparecer no pool disponível"
    assert "2" in pool, "Skill habilitada deve aparecer no pool disponível"

    print("✓ TESTE OK: skill com enabled=False não é disparada mesmo com cooldown zerado.")


def test_disabled_skill_in_combo_skipped():
    """Combo que contém skill desabilitada deve ser pulada."""
    skill_combo_config.update_config({
        "SKILL_COOLDOWNS": {"1": 0.0, "2": 0.0},
        "SKILL_METADATA": {
            "1": {"enabled": False},
            "2": {"enabled": True},
        },
        "COMBO_SETS": [
            {
                "name": "Combo com skill desabilitada",
                "skills": ["1", "2"],
                "cooldown": 0.0,
                "delay_between_skills": 0.0,
                "enabled": True,
            }
        ],
    })
    skill_combo_config.reload_config()

    mgr = SkillComboManager(hwnd=0, driver=MockDriver())
    mgr._skill_cooldowns = {}
    mgr._combo_cooldowns = {}
    mgr._last_single_skill_use = 0.0

    # try_execute_combos deve retornar False pois a skill '1' da combo está desabilitada
    result = mgr.try_execute_combos()
    assert result is False, "Combo com skill desabilitada não deve ser executada"

    print("✓ TESTE OK: combo com skill desabilitada é pulada pela rotação.")


if __name__ == "__main__":
    test_disabled_skill_not_executed()
    test_disabled_skill_in_combo_skipped()
    print("\nTodos os testes passaram.")
