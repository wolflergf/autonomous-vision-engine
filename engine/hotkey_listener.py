"""Listener global de teclas de atalho (hotkeys) via Win32 RegisterHotKey.

Permite pausar/retomar e parada de emergência da automação mesmo com o
jogo em tela cheia/foco, sem precisar de Alt+Tab.

Funciona em uma thread dedicada com message loop do Win32, de baixo consumo
de CPU. Nenhuma dependência externa além de ``ctypes`` e ``winsound``.

Atalhos padrão:
    * F9  — Toggle Pause / Resume
    * F11 — Emergency Stop (killswitch)
"""

from __future__ import annotations

import ctypes
import threading
import time

from loguru import logger
from PySide6 import QtCore

# ------------------------------------------------------------------ Win32 constants
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

VK_F9 = 0x78
VK_F11 = 0x7A

MOD_NONE = 0x0000

HOTKEY_ID_PAUSE = 1
HOTKEY_ID_STOP = 2


# WNDCLASSEXW não está disponível em ctypes.wintypes em todas as versões do
# Python, então definimos a estrutura manualmente.
class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("style", ctypes.c_uint),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
        ("hIconSm", ctypes.c_void_p),
    ]


class HotkeySignals(QtCore.QObject):
    """Sinais Qt para comunicação thread-safe entre o listener Win32 e o GUI."""
    pause_toggled = QtCore.Signal(bool)
    emergency_stop = QtCore.Signal()


class HotkeyListener:
    """Registra e escuta hotkeys globais (Win32 RegisterHotKey).

    Executa em sua própria thread. Os eventos são reencaminhados via sinais
    Qt para que a UI e o AutomationWorker reajam de forma thread-safe.
    """

    def __init__(self) -> None:
        self.signals = HotkeySignals()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._hwnd = None
        self._registered = False

    def start(self) -> bool:
        """Inicia o listener em uma thread dedicada. Retorna True se sucesso."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("[Hotkey] Listener já está em execução.")
            return True

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="HotkeyListener", daemon=True)
        self._thread.start()
        logger.info("[Hotkey] Listener iniciado (F9: Pausar/Retomar | F11: Parada de Emergência)")
        return True

    def stop(self) -> None:
        """Para o listener e libera as hotkeys registradas."""
        self._stop_event.set()
        if self._hwnd is not None:
            try:
                user32.UnregisterHotKey(self._hwnd, HOTKEY_ID_PAUSE)
                user32.UnregisterHotKey(self._hwnd, HOTKEY_ID_STOP)
                user32.DestroyWindow(self._hwnd)
            except Exception as e:
                logger.debug(f"[Hotkey] Erro ao limpar janela: {e}")
            self._hwnd = None
        self._registered = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        logger.info("[Hotkey] Listener encerrado.")

    def _run(self) -> None:
        """Cria janela oculta, registra hotkeys e entra no loop de mensagens."""
        hinst = kernel32.GetModuleHandleW(None)

        class_name_str = "AionHotkeyListenerWnd"

        wc = _WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(wc)
        wc.lpfnWndProc = ctypes.cast(user32.DefWindowProcW, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = ctypes.c_wchar_p(class_name_str)

        if not user32.RegisterClassExW(ctypes.byref(wc)):
            logger.error("[Hotkey] Falha ao registrar classe de janela.")
            return

        self._hwnd = user32.CreateWindowExW(
            0, ctypes.c_wchar_p(class_name_str), ctypes.c_wchar_p("AionHotkeyListener"), 0,
            0, 0, 0, 0,
            0, 0, hinst, 0,
        )
        if not self._hwnd:
            logger.error("[Hotkey] Falha ao criar janela oculta.")
            return

        ok1 = user32.RegisterHotKey(self._hwnd, HOTKEY_ID_PAUSE, MOD_NONE, VK_F9)
        ok2 = user32.RegisterHotKey(self._hwnd, HOTKEY_ID_STOP, MOD_NONE, VK_F11)
        if not ok1 or not ok2:
            logger.error(
                f"[Hotkey] Falha ao registrar hotkeys (pause={ok1}, stop={ok2}). "
                "Outro aplicativo pode estar usando F9/F11."
            )
            return

        self._registered = True
        logger.debug("[Hotkey] Hotkeys registradas: F9 (pause) e F11 (emergency stop).")

        msg = ctypes.wintypes.MSG()
        while not self._stop_event.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0:
                break
            if ret < 0:
                continue

            if msg.message == WM_HOTKEY:
                self._on_hotkey(msg.wParam)

            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._hwnd:
            user32.UnregisterHotKey(self._hwnd, HOTKEY_ID_PAUSE)
            user32.UnregisterHotKey(self._hwnd, HOTKEY_ID_STOP)
            user32.DestroyWindow(self._hwnd)
            self._hwnd = None
        self._registered = False

    def _on_hotkey(self, hotkey_id: int) -> None:
        """Manipula WM_HOTKEY recebido."""
        if hotkey_id == HOTKEY_ID_PAUSE:
            logger.info("[Hotkey] F9 pressionado — Toggle Pause/Resume")
            try:
                import winsound
                winsound.Beep(800, 120)
                time.sleep(0.05)
                winsound.Beep(800, 120)
            except Exception as e:
                logger.debug(f"[Hotkey] Sem áudio: {e}")
            self.signals.pause_toggled.emit(True)

        elif hotkey_id == HOTKEY_ID_STOP:
            logger.warning("[Hotkey] F11 pressionado — Parada de Emergência")
            try:
                import winsound
                winsound.Beep(400, 500)
            except Exception as e:
                logger.debug(f"[Hotkey] Sem áudio: {e}")
            self.signals.emergency_stop.emit()