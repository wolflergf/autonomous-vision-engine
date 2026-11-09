"""Simple test runner for E2E simulation suite."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.test_e2e_simulation import (
    test_exploration_sweep_and_walk,
    test_target_acquisition_interrupts_exploration,
    test_combat_skill_rotation_and_cooldowns,
    test_target_elimination_and_cooldown_reset,
    test_defensive_telemetry_trigger,
)

if __name__ == "__main__":
    tests = [
        ("test_exploration_sweep_and_walk", test_exploration_sweep_and_walk),
        ("test_target_acquisition_interrupts_exploration", test_target_acquisition_interrupts_exploration),
        ("test_combat_skill_rotation_and_cooldowns", test_combat_skill_rotation_and_cooldowns),
        ("test_target_elimination_and_cooldown_reset", test_target_elimination_and_cooldown_reset),
        ("test_defensive_telemetry_trigger", test_defensive_telemetry_trigger),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            test_func()
            print(f"✓ {name} PASSED")
            passed += 1
        except Exception as e:
            print(f"✗ {name} FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    
    if failed > 0:
        sys.exit(1)