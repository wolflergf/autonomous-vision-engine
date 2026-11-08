from loguru import logger

import input_controller as ic
from core.interfaces import IInputDriver


class InterceptionDriver(IInputDriver):
    def __init__(self, hwnd: int, dry_run: bool = False):
        self.hwnd = hwnd
        self.dry_run = dry_run
        ic.set_active_hwnd(hwnd)
        ic.INPUT_DRY_RUN = dry_run

    def tap_key(self, key: str, duration: float = 0.0) -> bool:
        try:
            if duration > 0:
                return ic.hold_key(key, duration)
            return ic.tap_key_fast(key)
        except Exception as e:
            logger.error(f"Erro ao disparar tecla {key}: {e}")
            return False

    def send_relative_mouse(self, dx: int, dy: int) -> bool:
        try:
            ok = ic.move_mouse_relative(dx, dy, steps=6, duration=0.08)
            # Mesmo cuidado de move_mouse_relative: mantém o cursor dentro
            # da janela do jogo após o pan relativo (multi-monitor).
            ic.recentre_cursor_in_window(self.hwnd)
            return ok
        except Exception as e:
            logger.error(f"Erro ao mover mouse: {e}")
            return False

    # ------------------------------------------------------------------
    # Capacidades estendidas (sobrescrevem os defaults de IInputDriver).
    # ------------------------------------------------------------------

    def hold_key(self, key: str, duration: float) -> bool:
        """Mantém uma tecla pressionada por `duration` segundos (ex.: 'w' no roaming)."""
        try:
            return ic.hold_key(key, duration)
        except Exception as e:
            logger.error(f"Erro ao manter tecla {key}: {e}")
            return False

    def hold_mouse(self, button: str = "right", down: bool = True) -> bool:
        """Pressiona (down=True) ou solta (down=False) um botão do mouse, sem auto-release."""
        try:
            if down:
                return ic.hold_mouse_down(button)
            return ic.release_mouse(button)
        except Exception as e:
            logger.error(f"Erro em hold_mouse(button={button}, down={down}): {e}")
            return False

    def move_mouse_relative(
        self, dx: int, dy: int, steps: int = 1, duration: float = 0.0
    ) -> bool:
        """Move o mouse por deltas relativos (pan de câmera) com swipe suave."""
        try:
            steps = max(1, int(steps))
            ok = ic.move_mouse_relative(dx, dy, steps=steps, duration=duration)
            # O pan relativo move o cursor real do sistema; em modo janela ele
            # pode escapar para outro monitor. Recentraliza-o dentro da janela
            # do jogo a cada pan para que a câmera nunca "fuja" da tela.
            ic.recentre_cursor_in_window(self.hwnd)
            return ok
        except Exception as e:
            logger.error(f"Erro ao mover mouse relativo ({dx},{dy}): {e}")
            return False

    def key_down(self, key: str) -> bool:
        """Mantém uma tecla pressionada (ex.: 'w' na caminhada fluida da coleta)."""
        try:
            return ic.key_down(key)
        except Exception as e:
            logger.error(f"Erro ao pressionar tecla {key}: {e}")
            return False

    def key_up(self, key: str) -> bool:
        """Solta uma tecla mantida por ``key_down``."""
        try:
            return ic.key_up(key)
        except Exception as e:
            logger.error(f"Erro ao soltar tecla {key}: {e}")
            return False

    def focus(self) -> bool:
        """Traz a janela alvo para foreground (best-effort)."""
        try:
            ic.focus_window(self.hwnd)
            return True
        except Exception as e:
            logger.error(f"Erro ao focar janela {self.hwnd}: {e}")
            return False

    def lock_cursor(self) -> bool:
        """Confina o cursor à janela do jogo (ClipCursor)."""
        try:
            return ic.lock_cursor_to_window(self.hwnd)
        except Exception as e:
            logger.error(f"Erro ao confinar cursor: {e}")
            return False

    def unlock_cursor(self) -> bool:
        """Libera o cursor confinado (ClipCursor(None))."""
        try:
            return ic.unlock_cursor()
        except Exception as e:
            logger.error(f"Erro ao liberar cursor: {e}")
            return False

    def click_at(self, x: int, y: int, button: str = "left") -> bool:
        """Clica em coordenadas absolutas (x, y) na tela.

        Usado para selecionar nós de recurso (auto-pathing do jogo).
        Delegada para input_controller.click_at com validação de foco e clamping.
        """
        try:
            return ic.click_at(x, y, button=button)
        except Exception as e:
            logger.error(f"Erro ao clicar em ({x},{y}): {e}")
            return False

    def release_all_keys(self) -> None:
        """Solta imediatamente todas as teclas físicas que possam estar retidas.

        Usado pelo killswitch (F11) para garantir que nenhuma tecla de
        movimento ou habilidade fique pressionada após uma parada de emergência.
        """
        try:
            ic.release_all_keys()
        except Exception as e:
            logger.error(f"Erro ao soltar todas as teclas: {e}")
