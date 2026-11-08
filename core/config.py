# core/config.py
import json
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger


@dataclass(slots=True)
class AutomationConfig:
    skill_cooldowns: dict[str, float] = field(default_factory=dict)
    combo_sets: list[dict] = field(default_factory=list)
    single_skill_gcd: float = 1.5
    fast_mode_delay: float = 0.12
    panic_threshold: int = 3
    enable_roam: bool = True
    input_dry_run: bool = False

    @classmethod
    def load_from_json(cls, file_path: Path) -> "AutomationConfig":
        if not file_path.exists():
            logger.warning(f"Configuração {file_path} não encontrada. Criando padrão.")
            instance = cls()
            instance.save_to_json(file_path)
            return instance

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(
                skill_cooldowns=data.get("SKILL_COOLDOWNS", {}),
                combo_sets=data.get("COMBO_SETS", []),
                single_skill_gcd=float(data.get("SINGLE_SKILL_GLOBAL_COOLDOWN", 1.5)),
                fast_mode_delay=float(data.get("FAST_MODE_SKILL_DELAY", 0.12)),
                panic_threshold=int(data.get("PANIC_THRESHOLD", 3)),
                enable_roam=bool(data.get("ENABLE_ROAM", True)),
                input_dry_run=bool(data.get("INPUT_DRY_RUN", False)),
            )
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Erro ao carregar configurações: {e}. Usando padrões.")
            return cls()

    def save_to_json(self, file_path: Path) -> None:
        data = {
            "SKILL_COOLDOWNS": self.skill_cooldowns,
            "COMBO_SETS": self.combo_sets,
            "SINGLE_SKILL_GLOBAL_COOLDOWN": self.single_skill_gcd,
            "FAST_MODE_SKILL_DELAY": self.fast_mode_delay,
            "PANIC_THRESHOLD": self.panic_threshold,
            "ENABLE_ROAM": self.enable_roam,
            "INPUT_DRY_RUN": self.input_dry_run,
        }
        temp_file = file_path.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        temp_file.replace(file_path)
