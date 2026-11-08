# main.py
import sys
from pathlib import Path

from loguru import logger
from PySide6 import QtWidgets

from core.config import AutomationConfig
from ui.main_windows import UnifiedMainWindow


def is_admin() -> bool:
    import ctypes

    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def run_as_admin() -> None:
    import ctypes
    import os

    script_path = os.path.abspath(sys.argv[0])
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, f'"{script_path}"', os.getcwd(), 1
    )
    sys.exit(0)


def main():
    if not is_admin():
        logger.info("Solicitando elevação de privilégios de Administrador...")
        run_as_admin()

    config_path = Path(__file__).parent / "skill_combo_config.json"
    config = AutomationConfig.load_from_json(config_path)

    app = QtWidgets.QApplication(sys.argv)
    window = UnifiedMainWindow(config=config, config_path=config_path)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
