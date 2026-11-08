"""
Interception-only input controller.

This module provides the public input API used across the codebase and
delivers inputs exclusively via the Interception kernel driver / DLL.
No fallback backends (pydirectinput, AutoHotkey, SendInput, etc.) exist in
this implementation. If the interception DLL or driver is not available,
public API calls raise a clear RuntimeError.
"""
from typing import Optional
import os
import sys
import ctypes
from ctypes import wintypes
from pathlib import Path

# Some Python runtimes / older ctypes versions do not expose ULONG_PTR in
# ctypes.wintypes. Create a safe fallback mapped to the correct-sized unsigned
# integer based on pointer width so the SendInput ctypes Structures work.
if not hasattr(wintypes, 'ULONG_PTR'):
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        wintypes.ULONG_PTR = ctypes.c_ulonglong
    else:
        wintypes.ULONG_PTR = ctypes.c_ulong
import time
import logging
import win32gui
import win32con

logger = logging.getLogger(__name__)

_HERE = Path(__file__).parent

# Runtime backend selection and dry-run are now read from the JSON-backed
# `skill_combo_config.py` so the GUI can toggle these values at runtime.
try:
    import skill_combo_config as skill_cfg  # JSON-backed config module
except Exception:
    skill_cfg = None


def _get_backend() -> str:
    # Interception-only controller: always return 'interception'
    return 'interception'


def _is_dry_run() -> bool:
    # Prefer the module-level override `INPUT_DRY_RUN` for backwards compatibility
    try:
        return bool(INPUT_DRY_RUN)
    except Exception:
        pass
    if skill_cfg is not None:
        return bool(getattr(skill_cfg, 'INPUT_DRY_RUN', False))
    return os.environ.get('INPUT_DRY_RUN', '0').lower() in ('1', 'true', 'yes')


# Backwards-compatible module attributes expected by `main.py` and other callers.
# These can be updated by the GUI at runtime but the runtime enforces
# interception-only behavior.
if skill_cfg is not None:
    INPUT_BACKEND = 'interception'
    INPUT_DRY_RUN = bool(getattr(skill_cfg, 'INPUT_DRY_RUN', False))
else:
    INPUT_BACKEND = 'interception'
    INPUT_DRY_RUN = os.environ.get('INPUT_DRY_RUN', '0').lower() in ('1', 'true', 'yes')

# IMPORTANT: we do NOT automatically fallback here. The selected backend
# is respected; if a backend isn't available it'll log (not force a DRY_RUN).

# Compatibility / state
ACTIVE_HWND: Optional[int] = None

# Focus caching to avoid redundant focus calls (saves ~190ms per avoided focus)
_last_focus_time: float = 0.0
_FOCUS_CACHE_DURATION: float = 2.0  # Seconds before forcing re-focus

# Fast input timing constants
_KEY_PRESS_DURATION_FAST: float = 0.008  # 8ms for combat-critical keys
_KEY_PRESS_DURATION_NORMAL: float = 0.010  # 10ms for regular keys (was 20ms)
_KEY_INTERVAL_FAST: float = 0.025  # 25ms between consecutive presses (was 50ms)


def open_driver(path: str = None):
    # compatibility shim — always ready
    return True


def close():
    return True


def set_active_hwnd(hwnd: int):
    global ACTIVE_HWND
    ACTIVE_HWND = hwnd


def is_window_foreground(hwnd: int) -> bool:
    """Verifica se a janela está em foreground.
    
    SEGURANÇA: Retorna False em caso de erro (não assume foreground).
    """
    try:
        return win32gui.GetForegroundWindow() == hwnd
    except Exception as e:
        logger.warning(f"[SEGURANÇA] Falha ao verificar foco da janela: {e}")
        return False  # ← CRÍTICO: False em caso de erro


def _validate_window_focus() -> bool:
    """Validação rigorosa de foco da janela alvo.
    
    Bloqueia qualquer input se:
    - ACTIVE_HWND não está definido
    - Janela não está em foreground
    - GetForegroundWindow() falha (exceção)
    
    NUNCA assume foreground em caso de erro (segurança máxima).
    """
    if not ACTIVE_HWND:
        logger.warning("[SEGURANÇA] ACTIVE_HWND não definido - input bloqueado")
        return False
    return is_window_foreground(ACTIVE_HWND)


def _clamp_to_client_rect(x: int, y: int, hwnd: int = None) -> tuple[int, int]:
    """Limita coordenadas (x,y) à área cliente da janela (clamping rigoroso).
    
    Garante que coordenadas NUNCA escapem da janela do jogo, prevenindo
    cliques/movimentos em monitores vizinhos ou áreas externas.
    
    Args:
        x, y: Coordenadas de tela absolutas
        hwnd: Handle da janela (usa ACTIVE_HWND se None)
    
    Returns:
        (x_clamped, y_clamped) dentro da área cliente, ou (0, 0) em erro
    """
    target = hwnd or ACTIVE_HWND
    if not target:
        logger.warning("[SEGURANÇA] HWND inválido para clamping - retornando (0,0)")
        return 0, 0
    
    try:
        # Obtém área cliente (sem bordas/título) em coordenadas de tela
        rect = win32gui.GetClientRect(target)
        tl = win32gui.ClientToScreen(target, (rect[0], rect[1]))  # Top-left
        br = win32gui.ClientToScreen(target, (rect[2], rect[3]))  # Bottom-right
        
        # Clamp x,y dentro da área cliente (inclusive bordas internas)
        x_clamped = max(tl[0], min(x, br[0] - 1))
        y_clamped = max(tl[1], min(y, br[1] - 1))
        
        # Log se coordenadas foram modificadas (debug)
        if (x_clamped, y_clamped) != (x, y):
            logger.debug(
                f"[SEGURANÇA] Coordenadas clipadas: ({x},{y}) → ({x_clamped},{y_clamped})"
            )
        
        return x_clamped, y_clamped
    except Exception as e:
        logger.error(f"[SEGURANÇA] Falha ao obter client rect: {e}")
        return 0, 0  # Seguro: retorna origem em caso de erro


def focus_window(hwnd: int):
    """Restore and set the target window to foreground (best-effort)."""
    try:
        if not hwnd:
            return
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.02)
        # Try attaching our thread input to the target window's thread
        try:
            # Get thread ids
            current_tid = ctypes.windll.kernel32.GetCurrentThreadId()
            target_tid = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, 0)
            attached = False
            if current_tid and target_tid and current_tid != target_tid:
                attached = bool(ctypes.windll.user32.AttachThreadInput(current_tid, target_tid, True))
            # Bring to foreground while threads are attached (if possible)
            win32gui.SetForegroundWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)
            time.sleep(0.06)
        finally:
            # Detach if we attached
            try:
                if 'attached' in locals() and attached:
                    ctypes.windll.user32.AttachThreadInput(current_tid, target_tid, False)
            except Exception:
                pass
        # Small pause to allow the OS and game to accept focus
        time.sleep(0.08)
    except Exception:
        # best-effort only
        logger.debug("focus_window: unable to focus hwnd=%s" % (str(hwnd)))


def _ensure_focus(force: bool = False):
    """Ensure the currently configured `ACTIVE_HWND` is foreground (best-effort).

    This helper is used before sending inputs so macros are more likely to
    be accepted by the game. Uses focus caching to skip redundant calls.
    Set force=True to bypass the cache (e.g., periodic safety re-focus).
    """
    global _last_focus_time
    try:
        if ACTIVE_HWND is None:
            return
        now = time.time()
        # Skip focus if recently focused (cache hit) and not forced
        if not force and (now - _last_focus_time) < _FOCUS_CACHE_DURATION:
            if is_window_foreground(ACTIVE_HWND):
                return  # Still focused, skip
        if not is_window_foreground(ACTIVE_HWND):
            focus_window(ACTIVE_HWND)
            _last_focus_time = now
            # Reduced delay after focusing
            time.sleep(0.02)
        else:
            _last_focus_time = now
    except Exception:
        # swallow errors; focusing is best-effort
        pass


def force_focus():
    """Force re-focus the active window, bypassing the cache.
    
    Call this periodically during combat as a safety net.
    """
    _ensure_focus(force=True)


# Local helper for key name translation
_KEY_TRANSLATION = {
    '-': 'minus',
    '=': 'equals',
    '\t': 'tab',
    'tab': 'tab',
    'r': 'r',
    't': 't',
}


def _translate_key(key: str) -> str:
    if not isinstance(key, str):
        return str(key).lower()
    k = key.lower().strip()
    return _KEY_TRANSLATION.get(k, k)


# Minimal virtual-key map used for scancode mapping. Keep only keys the app uses.
_USER32 = ctypes.windll.user32
VK_MAP = {
    'tab': 0x09,
    'r': 0x52,
    't': 0x54,
    'f': 0x46,
    'w': 0x57,
    'a': 0x41,
    's': 0x53,
    'd': 0x44,
    '1': 0x31,'2':0x32,'3':0x33,'4':0x34,'5':0x35,'6':0x36,'7':0x37,'8':0x38,'9':0x39,'0':0x30,
    'minus': 0xBD, 'equals': 0xBB,
    'alt': 0x12, 'ctrl': 0x11
}


def _vk_to_scancode(vk: int) -> int:
    # MapVirtualKeyW with MAPVK_VK_TO_VSC (0)
    try:
        return _USER32.MapVirtualKeyW(vk, 0)
    except Exception:
        return int(vk)


def tap_key(key: str, presses: int = 1, interval: float = 0.025):
    """Press a key using the Interception backend with duplicate prevention.

    Returns True on success. In DRY_RUN mode logs the call and returns True.
    Default interval reduced to 25ms for faster input.
    Uses combat queue to prevent double presses.
    """
    key_name = _translate_key(key)
    if _is_dry_run():
        logger.info(f"DRY_RUN: tap_key({key_name}, presses={presses}, interval={interval})")
        return True

    # Combat queue duplicate prevention
    try:
        from combat_queue import get_combat_queue, ActionType
        queue = get_combat_queue()
        if not queue.execute_immediate(key_name, ActionType.SKILL, source="tap_key"):
            return False  # Blocked as duplicate
    except ImportError:
        pass  # Combat queue not available, continue without duplicate prevention

    # Ensure the configured target window has focus before sending inputs
    try:
        _ensure_focus()
    except Exception:
        pass
    # Interception-only: ensure driver/DLL loaded then dispatch
    if not _load_interception():
        raise RuntimeError('Interception backend required but interception DLL/driver failed to load. See logs and ensure the Interception driver is installed and the DLL is present under Interception/library/x64/')
    return _tap_key_interception(key_name, presses=presses, interval=interval, fast=False)


def tap_key_fast(key: str, presses: int = 1):
    """Press a key with minimal delay for combat-critical inputs com validação de foco.
    
    Uses 8ms press duration and 25ms interval for maximum speed.
    Ideal for R, T, skill keys during aggressive combat.
    Uses combat queue to prevent double presses.
    
    SEGURANÇA: Valida foco antes de pressionar tecla.
    """
    key_name = _translate_key(key)
    if _is_dry_run():
        logger.info(f"DRY_RUN: tap_key_fast({key_name}, presses={presses})")
        return True
    
    # ✅ SEGURANÇA: Valida foco da janela
    if not _validate_window_focus():
        logger.warning(f"[SEGURANÇA] tap_key_fast('{key_name}') bloqueado - janela sem foco")
        return False

    # Combat queue duplicate prevention
    try:
        from combat_queue import get_combat_queue, ActionType
        queue = get_combat_queue()
        action_type = ActionType.BASIC_ATTACK if key_name in ('r', 't') else ActionType.SKILL
        if not queue.execute_immediate(key_name, action_type, source="tap_key_fast"):
            return False  # Blocked as duplicate
    except ImportError:
        pass  # Combat queue not available

    try:
        _ensure_focus()
    except Exception:
        pass
    if not _load_interception():
        raise RuntimeError('Interception backend required but interception DLL/driver failed to load')
    return _tap_key_interception(key_name, presses=presses, interval=_KEY_INTERVAL_FAST, fast=True)


def hold_key(key: str, duration: float):
    """Mantém tecla pressionada por duração específica com validação de foco.
    
    SEGURANÇA: Valida foco antes de manter tecla pressionada.
    """
    key_name = _translate_key(key)
    if _is_dry_run():
        logger.info(f"DRY_RUN: hold_key({key_name}, duration={duration})")
        return True
    
    # ✅ SEGURANÇA: Valida foco da janela
    if not _validate_window_focus():
        logger.warning(f"[SEGURANÇA] hold_key('{key_name}') bloqueado - janela sem foco")
        return False
    
    try:
        _ensure_focus()
    except Exception:
        pass
    if not _load_interception():
        raise RuntimeError('Interception backend required but interception DLL/driver failed to load')
    return _hold_key_interception(key_name, duration)


def move_mouse_to(x: int, y: int, duration: float = 0.0):
    """Move mouse to (x,y) usando Interception com validação e clamping.

    SEGURANÇA:
    - Valida foco da janela antes do movimento
    - Clipa coordenadas para área cliente (GetClientRect)
    - Bloqueia movimento se janela não estiver em foco
    """
    if _is_dry_run():
        logger.info(f"DRY_RUN: move_mouse_to({x},{y}, duration={duration})")
        return True
    
    # ✅ SEGURANÇA: Valida foco da janela
    if not _validate_window_focus():
        logger.warning(f"[SEGURANÇA] move_mouse_to bloqueado - janela sem foco")
        return False
    
    # ✅ SEGURANÇA: Clamp coordenadas para área cliente
    x_safe, y_safe = _clamp_to_client_rect(x, y)
    
    try:
        _ensure_focus()
    except Exception:
        pass
    
    if not _load_interception():
        raise RuntimeError('Interception backend required but interception DLL/driver failed to load')
    return _move_mouse_interception(x_safe, y_safe, duration)


def click_at(x: int, y: int, button: str = "left", clicks: int = 1, interval: float = 0.1):
    """Clica em posição (x,y) com validação e clamping rigoroso.

    Coordenadas de entrada são da ÁREA DE CLIENTE do jogo.
    Elas são convertidas para coordenadas de tela antes do envio.

    SEGURANÇA:
    - Força foco na janela do jogo (SetForegroundWindow)
    - Converte coordenadas cliente → tela via ClientToScreen
    - Valida foco da janela antes do clique
    - Clipa coordenadas para área cliente
    - Bloqueia clique se janela não estiver em foco
    - Bloqueia se coordenadas convertidas estiverem fora da área cliente
    """
    if _is_dry_run():
        logger.info(f"DRY_RUN: click_at({x},{y}, button={button}, clicks={clicks}, interval={interval})")
        return True

    target = ACTIVE_HWND
    if not target:
        logger.warning("[SEGURANÇA] ACTIVE_HWND não definido - click_at bloqueado")
        return False

    # ✅ SEGURANÇA: Força foco na janela do jogo
    try:
        win32gui.SetForegroundWindow(target)
        time.sleep(0.05)
    except Exception as e:
        logger.warning(f"[SEGURANÇA] Falha ao forçar foco na janela: {e}")
        return False

    # ✅ SEGURANÇA: Valida foco da janela após tentativa de foco
    is_focused = is_window_foreground(target)
    if not is_focused:
        logger.warning(f"[SEGURANÇA] click_at bloqueado - janela não está em foco (HWND={target})")
        return False

    # ✅ SEGURANÇA: Clipa coordenadas para área cliente
    x_safe, y_safe = _clamp_to_client_rect(x, y, target)

    # ✅ SEGURANÇA: Converte coordenadas cliente → tela
    try:
        screen_x, screen_y = win32gui.ClientToScreen(target, (x_safe, y_safe))
    except Exception as e:
        logger.error(f"[SEGURANÇA] Falha ao converter coordenadas cliente→tela: {e}")
        return False

    # ✅ SEGURANÇA: Verifica se coordenadas convertidas estão dentro do cliente
    try:
        rect = win32gui.GetClientRect(target)
        tl = win32gui.ClientToScreen(target, (rect[0], rect[1]))
        br = win32gui.ClientToScreen(target, (rect[2], rect[3]))
        if not (tl[0] <= screen_x < br[0] and tl[1] <= screen_y < br[1]):
            logger.warning(f"[SEGURANÇA] Coordenadas fora da área cliente: ({screen_x},{screen_y})")
            return False
    except Exception as e:
        logger.error(f"[SEGURANÇA] Falha ao validar coordenadas de tela: {e}")
        return False

    # ✅ LOG: Exibe conversão de coordenadas
    logger.info(
        f"[INPUT] Clique em cliente ({x_safe}, {y_safe}) -> Tela ({screen_x}, {screen_y}) | "
        f"HWND em foco: {is_focused}"
    )

    if not _load_interception():
        raise RuntimeError('Interception backend required but interception DLL/driver failed to load')
    return _click_at_interception(screen_x, screen_y, button=button, clicks=clicks, interval=interval)

def click_at(x: int, y: int, button: str = 'left', clicks: int = 1, interval: float = 0.1):
    """Clica em posição (x,y) com validação e clamping rigoroso.
    
    SEGURANÇA:
    - Valida foco da janela antes do clique
    - Clipa coordenadas para área cliente (GetClientRect)
    - Bloqueia clique se janela não estiver em foco
    """
    if _is_dry_run():
        logger.info(f"DRY_RUN: click_at({x},{y}, button={button}, clicks={clicks}, interval={interval})")
        return True
    
    # ✅ SEGURANÇA: Valida foco da janela
    if not _validate_window_focus():
        logger.warning(f"[SEGURANÇA] click_at bloqueado - janela sem foco")
        return False
    
    # ✅ SEGURANÇA: Clamp coordenadas para área cliente
    x_safe, y_safe = _clamp_to_client_rect(x, y)
    
    try:
        _ensure_focus()
    except Exception:
        pass
    
    if not _load_interception():
        raise RuntimeError('Interception backend required but interception DLL/driver failed to load')
    return _click_at_interception(x_safe, y_safe, button=button, clicks=clicks, interval=interval)


def hold_mouse_down(button: str = 'right') -> bool:
    """Press and hold a mouse button (does NOT release) com validação de foco.

    Use `release_mouse(button)` to release. In DRY_RUN mode this logs the call.
    
    SEGURANÇA: Valida foco antes de pressionar botão do mouse.
    """
    btn = (button or 'right').lower()
    if _is_dry_run():
        logger.info(f"DRY_RUN: hold_mouse_down({btn})")
        return True
    
    # ✅ SEGURANÇA: Valida foco da janela
    if not _validate_window_focus():
        logger.warning(f"[SEGURANÇA] hold_mouse_down('{btn}') bloqueado - janela sem foco")
        return False
    
    if not _load_interception():
        raise RuntimeError('Interception backend required but interception DLL/driver failed to load')
    code = INTERCEPTION_MOUSE_RIGHT_BUTTON_DOWN if btn == 'right' else INTERCEPTION_MOUSE_LEFT_BUTTON_DOWN
    stroke = _InterceptionMouseStroke(state=code, flags=0, rolling=0, x=0, y=0, information=0)
    res = _INTERCEPTION_DLL.interception_send(_INTERCEPTION_CTX, 11, ctypes.byref(stroke), 1)
    return res != 0


def release_mouse(button: str = 'right') -> bool:
    """Release a previously held mouse button com validação de foco.
    
    SEGURANÇA: Valida foco antes de soltar botão do mouse.
    """
    btn = (button or 'right').lower()
    if _is_dry_run():
        logger.info(f"DRY_RUN: release_mouse({btn})")
        return True
    
    # ✅ SEGURANÇA: Valida foco da janela
    if not _validate_window_focus():
        logger.warning(f"[SEGURANÇA] release_mouse('{btn}') bloqueado - janela sem foco")
        return False
    
    if not _load_interception():
        raise RuntimeError('Interception backend required but interception DLL/driver failed to load')
    code = INTERCEPTION_MOUSE_RIGHT_BUTTON_UP if btn == 'right' else INTERCEPTION_MOUSE_LEFT_BUTTON_UP
    stroke = _InterceptionMouseStroke(state=code, flags=0, rolling=0, x=0, y=0, information=0)
    res = _INTERCEPTION_DLL.interception_send(_INTERCEPTION_CTX, 11, ctypes.byref(stroke), 1)
    return res != 0


def move_mouse_by(dx: int, dy: int, duration: float = 0.2, steps: int = 8) -> bool:
    """Move the mouse by a relative amount over a duration (interpolated steps).

    This uses the interception mouse move stroke. Uses screen absolute positions computed
    from the current cursor position. In DRY_RUN mode this logs the intended motion.
    """
    if _is_dry_run():
        logger.info(f"DRY_RUN: move_mouse_by(dx={dx}, dy={dy}, duration={duration})")
        return True
    try:
        cx, cy = win32gui.GetCursorPos()
    except Exception:
        # Fallback to (0,0)
        cx, cy = 0, 0
    tx = int(cx + dx)
    ty = int(cy + dy)
    steps = max(1, int(steps))
    sleep_per = max(0.0, duration / steps) if duration > 0 else 0.0
    for i in range(1, steps + 1):
        ix = int(cx + (tx - cx) * (i / steps))
        iy = int(cy + (ty - cy) * (i / steps))
        _interception_send_mouse_move(ix, iy)
        if sleep_per:
            time.sleep(sleep_per)
    return True


def move_mouse_relative(dx: int, dy: int, *, steps: int = 1, duration: float = 0.0) -> bool:
    """Move mouse por deltas relativos com validação de foco.

    Projetado para pans de câmera onde posicionamento absoluto é indesejável.
    Divide o delta requisitado em passos incrementais para criar movimento suave.
    
    SEGURANÇA:
    - Valida foco da janela antes do movimento
    - Bloqueia movimento se janela não estiver em foco
    
    Positive `dx` moves right, positive `dy` moves down.
    """
    if _is_dry_run():
        logger.info(f"DRY_RUN: move_mouse_relative(dx={dx}, dy={dy}, steps={steps}, duration={duration})")
        return True
    
    # ✅ SEGURANÇA: Valida foco da janela
    if not _validate_window_focus():
        logger.warning(f"[SEGURANÇA] move_mouse_relative bloqueado - janela sem foco")
        return False
    
    if not _load_interception():
        raise RuntimeError('Interception backend required but interception DLL/driver failed to load')
    steps = max(1, int(steps))
    step_delay = max(0.0, duration / steps) if duration > 0 else 0.0
    rem_x = int(dx)
    rem_y = int(dy)
    for i in range(steps):
        # distribute remainder to keep totals accurate
        chunk_x = rem_x // (steps - i) if (steps - i) else rem_x
        chunk_y = rem_y // (steps - i) if (steps - i) else rem_y
        rem_x -= chunk_x
        rem_y -= chunk_y
        _interception_send_mouse_move(chunk_x, chunk_y)
        if step_delay:
            time.sleep(step_delay)
    return True


def key_down(key_name: str) -> bool:
    """Mantém uma tecla pressionada (sem release) via Interception com validação de foco.

    Usado pela caminhada fluida da aproximação de coleta: 'W' fica retida
    entre frames até ser solta explicitamente por ``key_up``.
    
    SEGURANÇA: Valida foco antes de pressionar tecla.
    """
    if _is_dry_run():
        logger.info(f"DRY_RUN: key_down({key_name})")
        return True
    
    # ✅ SEGURANÇA: Valida foco da janela
    if not _validate_window_focus():
        logger.warning(f"[SEGURANÇA] key_down('{key_name}') bloqueado - janela sem foco")
        return False
    
    if not _load_interception():
        return False
    try:
        vk = VK_MAP.get(key_name) or (
            ord(key_name.upper()) if len(key_name) == 1 else None
        )
        if vk is None:
            logger.warning(f"key_down: tecla desconhecida '{key_name}'")
            return False
        sc = _vk_to_scancode(vk)
        stroke = _InterceptionKeyStroke(code=sc, state=0, information=0)
        res = _INTERCEPTION_DLL.interception_send(
            _INTERCEPTION_CTX, 1, ctypes.byref(stroke), 1
        )
        return res != 0
    except Exception as e:
        logger.warning(f"key_down({key_name}) falhou: {e}")
        return False


def key_up(key_name: str) -> bool:
    """Solta uma tecla previamente mantida pressionada por ``key_down`` com validação de foco.
    
    SEGURANÇA: Valida foco antes de soltar tecla.
    """
    if _is_dry_run():
        logger.info(f"DRY_RUN: key_up({key_name})")
        return True
    
    # ✅ SEGURANÇA: Valida foco da janela
    if not _validate_window_focus():
        logger.warning(f"[SEGURANÇA] key_up('{key_name}') bloqueado - janela sem foco")
        return False
    
    if not _load_interception():
        return False
    try:
        vk = VK_MAP.get(key_name) or (
            ord(key_name.upper()) if len(key_name) == 1 else None
        )
        if vk is None:
            logger.warning(f"key_up: tecla desconhecida '{key_name}'")
            return False
        sc = _vk_to_scancode(vk)
        stroke = _InterceptionKeyStroke(code=sc, state=1, information=0)
        res = _INTERCEPTION_DLL.interception_send(
            _INTERCEPTION_CTX, 1, ctypes.byref(stroke), 1
        )
        return res != 0
    except Exception as e:
        logger.warning(f"key_up({key_name}) falhou: {e}")
        return False


# ------------------------------------------------------------------ cursor clip
# Confinamento do cursor à janela do jogo via Win32 ClipCursor.
# Impede que o pan de câmera relativo escape para o monitor secundário
# (modo janela 1920x1080). Estado global idempotente + bypass em DRY_RUN.

_CURSOR_CLIP_HWND: Optional[int] = None


def lock_cursor_to_window(hwnd: int = None) -> bool:
    """Confina o cursor ao rect da janela do jogo (ClipCursor).

    Idempotente: se já travado no mesmo hwnd, retorna True sem re-chamar.
    Em DRY_RUN, apenas registra o estado (sem chamar a API real).
    Retorna False se hwnd inválido ou a API falhar.
    """
    global _CURSOR_CLIP_HWND
    target = hwnd or ACTIVE_HWND
    if not target:
        logger.warning("[input_controller] lock_cursor_to_window sem hwnd.")
        return False
    if _CURSOR_CLIP_HWND == target:
        return True
    if _is_dry_run():
        _CURSOR_CLIP_HWND = target
        logger.debug(f"[input_controller] [DRY_RUN] cursor confinado (hwnd={target}).")
        return True
    try:
        left, top, right, bottom = win32gui.GetWindowRect(target)

        class _ClipRect(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        _USER32.ClipCursor(ctypes.byref(_ClipRect(left, top, right, bottom)))
        _CURSOR_CLIP_HWND = target
        logger.info(
            f"[input_controller] Cursor confinado à janela "
            f"(hwnd={target}, rect=({left},{top},{right},{bottom}))."
        )
        return True
    except Exception as e:
        logger.warning(f"[input_controller] Falha ao confinar cursor: {e}")
        return False


def unlock_cursor() -> bool:
    """Libera o cursor (ClipCursor(None)). Idempotente + bypass em DRY_RUN."""
    global _CURSOR_CLIP_HWND
    if _CURSOR_CLIP_HWND is None:
        return True
    _CURSOR_CLIP_HWND = None
    if _is_dry_run():
        logger.debug("[input_controller] [DRY_RUN] cursor liberado.")
        return True
    try:
        _USER32.ClipCursor(None)
        logger.info("[input_controller] Cursor liberado (ClipCursor off).")
        return True
    except Exception as e:
        logger.warning(f"[input_controller] Falha ao liberar cursor: {e}")
        return False


def is_cursor_locked() -> bool:
    """Retorna True se o cursor está confinado (estado interno)."""
    return _CURSOR_CLIP_HWND is not None


def get_cursor_clip_hwnd() -> Optional[int]:
    """HWND atualmente confinado, ou None."""
    return _CURSOR_CLIP_HWND


def recentre_cursor_in_window(hwnd: int = None) -> bool:
    """Recentraliza o cursor dentro da janela alvo (melhor-esforço).

    Pans de câmera relativos (Interception) movem o cursor real do sistema;
    em modo janela com vários monitores o cursor pode escapar para o
    monitor vizinho. Recentralizar após cada pan mantém o cursor dentro da
    área da janela do jogo. Usa o ``hwnd`` informado ou o ativo registrado.
    """
    target = hwnd or ACTIVE_HWND
    if not target:
        return False
    try:
        rect = win32gui.GetClientRect(target)
        tl = win32gui.ClientToScreen(target, (rect[0], rect[1]))
        br = win32gui.ClientToScreen(target, (rect[2], rect[3]))
        cx = (tl[0] + br[0]) // 2
        cy = (tl[1] + br[1]) // 2
        win32gui.SetCursorPos((cx, cy))
        return True
    except Exception as e:
        logger.debug(f"recentre_cursor_in_window falhou: {e}")
        return False


def double_click_at(x: int, y: int):
    return click_at(x, y, button='left', clicks=2, interval=0.08)


def perform_human_attack_click(x: int, y: int):
    # Compatibility helper: historically this performed a double-click attack.
    # ActionPlanner now uses keyboard-based targeting (Tab + R/T) so callers
    # should prefer the new behavior, but keep this function for backward-compat.
    return double_click_at(x, y)


def press_key_combination(modifier: str, key: str):
    """Hold modifier, press key, release modifier.

    Supported modifiers: 'alt', 'ctrl' (case-insensitive).
    """
    mod = modifier.lower() if modifier else ''
    key_name = _translate_key(key)
    if _is_dry_run():
        logger.info(f"DRY_RUN: press_key_combination({mod}, {key_name})")
        return True
    try:
        _ensure_focus()
    except Exception:
        pass
    if not _load_interception():
        raise RuntimeError('Interception backend required but interception DLL/driver failed to load')
    return _press_key_combination_interception(mod, key_name)


# module init
try:
    open_driver()
except Exception:
    pass

# --------------------------
# Minimal Interception-only implementation
# --------------------------

def _tap_key_interception(key_name: str, presses: int = 1, interval: float = 0.025, fast: bool = False) -> bool:
    # Interception-only: send using the interception DLL. No fallbacks allowed.
    if not _load_interception():
        raise RuntimeError('Interception DLL/driver not loaded')
    ok = _interception_send_key(key_name, presses=presses, interval=interval, fast=fast)
    if not ok:
        raise RuntimeError(f'Interception failed to send key: {key_name}')
    return True


def _hold_key_interception(key_name: str, duration: float) -> bool:
    # Implement hold via interception: send down, sleep, send up
    if not _load_interception():
        raise RuntimeError('Interception DLL/driver not loaded')
    # Map to VK/scan code
    base = key_name.split('+')[-1]
    vk = VK_MAP.get(base) or (ord(base.upper()) if len(base) == 1 else None)
    if vk is None:
        raise RuntimeError(f'Unsupported key for interception hold: {key_name}')
    sc = _vk_to_scancode(vk)
    down = _InterceptionKeyStroke(code=sc, state=0, information=0)
    up = _InterceptionKeyStroke(code=sc, state=1, information=0)
    res = _INTERCEPTION_DLL.interception_send(_INTERCEPTION_CTX, 1, ctypes.byref(down), 1)
    if res == 0:
        raise RuntimeError('Interception send (down) failed')
    time.sleep(max(0.0, duration))
    res = _INTERCEPTION_DLL.interception_send(_INTERCEPTION_CTX, 1, ctypes.byref(up), 1)
    if res == 0:
        raise RuntimeError('Interception send (up) failed')
    return True


def _move_mouse_interception(x: int, y: int, duration: float = 0.0) -> bool:
    return _interception_send_mouse_move(x, y)


def _click_at_interception(x: int, y: int, button: str = 'left', clicks: int = 1, interval: float = 0.1) -> bool:
    return _interception_send_mouse_click(x, y, button=button, clicks=clicks, interval=interval)


def _press_key_combination_interception(mod: str, key_name: str) -> bool:
    # Interception-only implementation for modifier+key combos
    if not _load_interception():
        raise RuntimeError('Interception DLL/driver not loaded')
    mod_lower = (mod or '').lower()
    if mod_lower not in ('alt', 'ctrl'):
        raise RuntimeError(f'Unsupported modifier for interception: {mod}')
    # map modifier VK
    mod_vk = 0x12 if mod_lower == 'alt' else 0x11
    # map key
    base = key_name.split('+')[-1]
    vk = VK_MAP.get(base) or (ord(base.upper()) if len(base) == 1 else None)
    if vk is None:
        raise RuntimeError(f'Unsupported key for interception combo: {key_name}')
    mod_sc = _vk_to_scancode(mod_vk)
    key_sc = _vk_to_scancode(vk)
    down_mod = _InterceptionKeyStroke(code=mod_sc, state=0, information=0)
    up_mod = _InterceptionKeyStroke(code=mod_sc, state=1, information=0)
    down_key = _InterceptionKeyStroke(code=key_sc, state=0, information=0)
    up_key = _InterceptionKeyStroke(code=key_sc, state=1, information=0)

    # press modifier down
    res = _INTERCEPTION_DLL.interception_send(_INTERCEPTION_CTX, 1, ctypes.byref(down_mod), 1)
    if res == 0:
        raise RuntimeError('Interception send failed (mod down)')
    time.sleep(0.02)
    # press key
    res = _INTERCEPTION_DLL.interception_send(_INTERCEPTION_CTX, 1, ctypes.byref(down_key), 1)
    if res == 0:
        # attempt to release mod then error
        _INTERCEPTION_DLL.interception_send(_INTERCEPTION_CTX, 1, ctypes.byref(up_mod), 1)
        raise RuntimeError('Interception send failed (key down)')
    time.sleep(0.02)
    # release key
    res = _INTERCEPTION_DLL.interception_send(_INTERCEPTION_CTX, 1, ctypes.byref(up_key), 1)
    if res == 0:
        _INTERCEPTION_DLL.interception_send(_INTERCEPTION_CTX, 1, ctypes.byref(up_mod), 1)
        raise RuntimeError('Interception send failed (key up)')
    # release modifier
    res = _INTERCEPTION_DLL.interception_send(_INTERCEPTION_CTX, 1, ctypes.byref(up_mod), 1)
    if res == 0:
        raise RuntimeError('Interception send failed (mod up)')
    return True


# --------------------------
# Minimal Interception ctypes wrapper
# --------------------------

_INTERCEPTION_DLL = None
_INTERCEPTION_CTX = None

def _load_interception():
    global _INTERCEPTION_DLL, _INTERCEPTION_CTX
    if _INTERCEPTION_DLL is not None:
        return True
    # Try common candidate locations (repo-provided x64/x86 and system/global)
    candidates = [
        Path(__file__).parent / 'Interception' / 'library' / 'x64' / 'interception.dll',
        Path(__file__).parent / 'Interception' / 'library' / 'x86' / 'interception.dll',
    ]
    # Also allow loading by name if installed system-wide
    tried = []
    for lib_path in candidates:
        tried.append(str(lib_path))
        if lib_path.exists():
            try:
                _INTERCEPTION_DLL = ctypes.WinDLL(str(lib_path))
                break
            except Exception as e:
                logger.debug(f"interception: failed to load {lib_path}: {e}")
                _INTERCEPTION_DLL = None
    if _INTERCEPTION_DLL is None:
        # try by library name (system-installed)
        try:
            _INTERCEPTION_DLL = ctypes.WinDLL('interception')
            tried.append('system:interception')
        except Exception as e:
            logger.warning(f"interception: failed to load from candidates {tried}: {e}")
            _INTERCEPTION_DLL = None
    if _INTERCEPTION_DLL is None:
        # As a last resort attempt to run the Interception installer (requires elevation)
        try:
            if _attempt_install_interception():
                # After installer runs attempt to load again from system name
                try:
                    _INTERCEPTION_DLL = ctypes.WinDLL('interception')
                    tried.append('system:interception-after-install')
                except Exception:
                    _INTERCEPTION_DLL = None
        except Exception:
            pass
    if _INTERCEPTION_DLL is None:
        return False
    try:
        # define functions with precise prototypes
        _INTERCEPTION_DLL.interception_create_context.restype = ctypes.c_void_p
        _INTERCEPTION_DLL.interception_destroy_context.argtypes = [ctypes.c_void_p]
        _INTERCEPTION_DLL.interception_send.restype = ctypes.c_int
        _INTERCEPTION_DLL.interception_send.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
        _INTERCEPTION_DLL.interception_is_keyboard.argtypes = [ctypes.c_int]
        _INTERCEPTION_DLL.interception_is_mouse.argtypes = [ctypes.c_int]
        # create context
        ctx = _INTERCEPTION_DLL.interception_create_context()
        if not ctx:
            logger.warning("interception: interception_create_context returned NULL")
            _INTERCEPTION_DLL = None
            return False
        _INTERCEPTION_CTX = ctypes.c_void_p(ctx)
        logger.debug(f"interception: loaded DLL and created context (ctx={_INTERCEPTION_CTX})")
        return True
    except Exception as e:
        logger.warning(f"Failed to initialize interception DLL: {e}")
        _INTERCEPTION_DLL = None
        _INTERCEPTION_CTX = None
        return False


def _attempt_install_interception() -> bool:
    """Attempt to run the Interception installer found in common locations.

    This will attempt to launch the bundled `install-interception.exe` with
    elevation (ShellExecuteW verb 'runas'). After launching the installer it
    retries loading the interception DLL a few times. Returns True if the
    installer was launched (and DLL becomes loadable), False otherwise.
    """
    try:
        candidates = []
        # Installer alongside this module (source tree)
        candidates.append(Path(__file__).parent / 'Interception' / 'command line installer' / 'install-interception.exe')
        # Installer relative to the running executable (when packaged in output/)
        try:
            exe_parent = Path(sys.argv[0]).parent
            candidates.append(exe_parent / 'Interception' / 'command line installer' / 'install-interception.exe')
        except Exception:
            pass
        # Installer from current working directory
        candidates.append(Path.cwd() / 'Interception' / 'command line installer' / 'install-interception.exe')

        installer_path = None
        for c in candidates:
            if c.exists():
                installer_path = c
                break
        if installer_path is None:
            logger.debug(f"No Interception installer found in candidates: {candidates}")
            return False

        logger.info(f"Attempting to run Interception installer: {installer_path}")
        # Launch elevated installer using ShellExecuteW
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(installer_path), None, str(installer_path.parent), 1)
            if int(ret) <= 32:
                logger.warning(f"ShellExecuteW failed to launch installer, code={ret}")
                return False
        except Exception as e:
            logger.warning(f"Failed to launch installer via ShellExecuteW: {e}")
            return False

        # Wait & retry loading DLL a few times
        for attempt in range(8):
            time.sleep(2)
            # try known candidate dll locations again
            for lib_path in [
                Path(__file__).parent / 'Interception' / 'library' / 'x64' / 'interception.dll',
                Path(__file__).parent / 'Interception' / 'library' / 'x86' / 'interception.dll',
                exe_parent / 'Interception' / 'library' / 'x64' / 'interception.dll' if 'exe_parent' in locals() else None,
            ]:
                if lib_path is None:
                    continue
                if lib_path.exists():
                    try:
                        ctypes.WinDLL(str(lib_path))
                        logger.info("Interception DLL detected after installer run")
                        return True
                    except Exception:
                        continue
        logger.warning("Interception installer ran but DLL not detected after retries")
        return False
    except Exception as e:
        logger.warning(f"_attempt_install_interception failed: {e}")
        return False


class _InterceptionKeyStroke(ctypes.Structure):
    _fields_ = [('code', ctypes.c_ushort), ('state', ctypes.c_ushort), ('information', ctypes.c_uint)]


def _interception_send_key(key_name: str, presses: int = 1, interval: float = 0.025, fast: bool = False) -> bool:
    if not _load_interception():
        return False
    # use device 1 (INTERCEPTION_KEYBOARD(0) == 1)
    device = 1
    # map key_name to vk/scancode
    parts = key_name.split('+')
    base = parts[-1]
    vk = VK_MAP.get(base) or (ord(base.upper()) if len(base) == 1 else None)
    if vk is None:
        return False
    sc = _vk_to_scancode(vk)
    # Use faster timing for combat-critical keys
    press_duration = _KEY_PRESS_DURATION_FAST if fast else _KEY_PRESS_DURATION_NORMAL
    for i in range(presses):
        stroke_down = _InterceptionKeyStroke(code=sc, state=0, information=0)
        stroke_up = _InterceptionKeyStroke(code=sc, state=1, information=0)
        # send down
        res = _INTERCEPTION_DLL.interception_send(_INTERCEPTION_CTX, device, ctypes.byref(stroke_down), 1)
        if res == 0:
            return False
        time.sleep(press_duration)  # 8ms fast, 10ms normal (was 20ms)
        res = _INTERCEPTION_DLL.interception_send(_INTERCEPTION_CTX, device, ctypes.byref(stroke_up), 1)
        if res == 0:
            return False
        if presses > 1 and i < presses - 1:
            time.sleep(max(interval, 0.008))  # Minimum 8ms between presses
    return True


# Interception mouse support
INTERCEPTION_MOUSE_LEFT_BUTTON_DOWN = 0x001
INTERCEPTION_MOUSE_LEFT_BUTTON_UP = 0x002
INTERCEPTION_MOUSE_RIGHT_BUTTON_DOWN = 0x004
INTERCEPTION_MOUSE_RIGHT_BUTTON_UP = 0x008
INTERCEPTION_MOUSE_MIDDLE_BUTTON_DOWN = 0x010
INTERCEPTION_MOUSE_MIDDLE_BUTTON_UP = 0x020
INTERCEPTION_MOUSE_MOVE_FLAG = 0x1000


class _InterceptionMouseStroke(ctypes.Structure):
    _fields_ = [
        ('state', ctypes.c_ushort),
        ('flags', ctypes.c_ushort),
        ('rolling', ctypes.c_short),
        ('x', ctypes.c_int),
        ('y', ctypes.c_int),
        ('information', ctypes.c_uint),
    ]


def _interception_send_mouse_move(x: int, y: int) -> bool:
    if not _load_interception():
        return False
    # mouse device index 0 -> device id = INTERCEPTION_MOUSE(0) == INTERCEPTION_MAX_KEYBOARD + 0 + 1
    device = 11
    stroke = _InterceptionMouseStroke(state=0, flags=INTERCEPTION_MOUSE_MOVE_FLAG, rolling=0, x=int(x), y=int(y), information=0)
    res = _INTERCEPTION_DLL.interception_send(_INTERCEPTION_CTX, device, ctypes.byref(stroke), 1)
    return res != 0


def _interception_send_mouse_click(x: int, y: int, button: str = 'left', clicks: int = 1, interval: float = 0.1) -> bool:
    if not _load_interception():
        return False
    device = 11
    btn_down = INTERCEPTION_MOUSE_LEFT_BUTTON_DOWN if button == 'left' else INTERCEPTION_MOUSE_RIGHT_BUTTON_DOWN
    btn_up = INTERCEPTION_MOUSE_LEFT_BUTTON_UP if button == 'left' else INTERCEPTION_MOUSE_RIGHT_BUTTON_UP
    for i in range(clicks):
        stroke_down = _InterceptionMouseStroke(state=btn_down, flags=0, rolling=0, x=int(x), y=int(y), information=0)
        stroke_up = _InterceptionMouseStroke(state=btn_up, flags=0, rolling=0, x=int(x), y=int(y), information=0)
        res = _INTERCEPTION_DLL.interception_send(_INTERCEPTION_CTX, device, ctypes.byref(stroke_down), 1)
        if res == 0:
            return False
        time.sleep(0.02)
        res = _INTERCEPTION_DLL.interception_send(_INTERCEPTION_CTX, device, ctypes.byref(stroke_up), 1)
        if res == 0:
            return False
        if clicks > 1 and i < clicks - 1:
            time.sleep(max(interval, 0.01))
    return True


def validate_backend(backend: str) -> None:
    """Validate that the given backend is available.

    Raises RuntimeError with actionable guidance when missing.
    """
    if not backend:
        raise RuntimeError('No backend specified')
    b = str(backend).lower()
    # Only interception is supported in this build
    if b == 'interception':
        if not _load_interception():
            raise RuntimeError('Interception backend selected but the interception DLL or driver failed to load. Ensure the Interception DLL exists under Interception/library/x64 (or x86) matching your Python bitness, and install the Interception kernel driver. Run Python as Administrator when testing.')
        return
    raise RuntimeError(f'Unsupported INPUT_BACKEND: {backend} — only "interception" is supported')



# ------------------------------------------------------------------ release_all_keys


def _keybd_event_keyup(vk: int) -> None:
    """Solta uma tecla virtual via SendInput (KEYEVENTF_KEYUP)."""
    try:
        sc = ctypes.windll.user32.MapVirtualKeyW(vk, 0)

        class _KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class _INPUT(ctypes.Structure):
            _fields_ = [
                ("type", ctypes.c_ulong),
                ("ki", _KEYBDINPUT),
            ]

        INPUT_KEYBOARD = 1
        KEYEVENTF_KEYUP = 0x0002

        ki = _KEYBDINPUT(wVk=vk, wScan=sc, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)
        inp = _INPUT(type=INPUT_KEYBOARD, ki=ki)
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
    except Exception:
        pass


def release_all_keys() -> None:
    """Solta imediatamente todas as teclas físicas que possam estar retidas.

    Usado pelo killswitch (F11) para garantir que nenhuma tecla de movimento
    ou habilidade fique pressionada após uma parada de emergência.
    """
    keys_to_release = list(VK_MAP.values())
    for extra in (0x10, 0x11, 0x12, 0x1B, 0x20):  # Shift, Ctrl, Alt, Esc, Space
        if extra not in keys_to_release:
            keys_to_release.append(extra)

    for vk in keys_to_release:
        _keybd_event_keyup(vk)

    logger.info("[input_controller] Todas as teclas foram soltas (release_all_keys).")
