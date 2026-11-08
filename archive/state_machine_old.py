import json
import time
from pathlib import Path

from loguru import logger

from core.interfaces import IInputDriver


class SkillChain:
    def __init__(
        self,
        key: str,
        cooldown: float,
        burst_count: int = 1,
        press_duration: float = 0.15,
        name: str = "",
    ):
        self.key = key
        self.cooldown = cooldown
        self.burst_count = burst_count
        self.press_duration = press_duration
        self.name = name or f"Skill_{key}"
        self.last_used: float = 0.0

    def is_ready(self, now: float) -> bool:
        return (now - self.last_used) >= self.cooldown

    def execute(self, driver: IInputDriver, now: float) -> None:
        logger.debug(
            f"[COMBO:CLERIC] Executando {self.name} ({self.burst_count} toques)"
        )
        for _ in range(self.burst_count):
            driver.tap_key(self.key, duration=self.press_duration)
            time.sleep(0.06)
        self.last_used = now


class CombatRoutine:
    def __init__(self, driver: IInputDriver, config_file: str = "config/cleric.json"):
        self.driver = driver
        self.chains: list[SkillChain] = []
        self.global_cooldown = 0.25
        self.last_cast_time: float = 0.0

        self._load_config(config_file)

    def _load_config(self, filepath: str) -> None:
        path = Path(filepath)
        if not path.exists():
            # Fallback direto caso o json não seja encontrado
            logger.warning(
                f"Arquivo {filepath} não encontrado. Usando perfil padrão Clérigo."
            )
            self.chains = [
                SkillChain(
                    "t",
                    cooldown=9.0,
                    burst_count=4,
                    press_duration=0.20,
                    name="Cadeia T",
                ),
                SkillChain(
                    "e",
                    cooldown=6.5,
                    burst_count=2,
                    press_duration=0.18,
                    name="Cadeia E",
                ),
                SkillChain(
                    "r", cooldown=4.0, burst_count=1, press_duration=0.12, name="Raio R"
                ),
                SkillChain(
                    "q", cooldown=5.0, burst_count=1, press_duration=0.12, name="Orbe Q"
                ),
            ]
            return

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.global_cooldown = data.get("global_cooldown", 0.25)
        self.chains = [
            SkillChain(
                key=item["key"],
                cooldown=item["cooldown"],
                burst_count=item.get("burst_count", 1),
                press_duration=item.get("press_duration", 0.15),
                name=item.get("name", item["key"]),
            )
            for item in data.get("rotation", [])
        ]
        logger.info(
            f"✓ Perfil de combate carregado: {data.get('class_name', 'Custom')} ({len(self.chains)} habilidades configuradas)"
        )

    def execute_best_skill(self) -> None:
        now = time.perf_counter()
        if now - self.last_cast_time < self.global_cooldown:
            return

        for chain in self.chains:
            if chain.is_ready(now):
                chain.execute(self.driver, now)
                self.last_cast_time = now
                break
