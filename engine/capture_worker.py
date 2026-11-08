import queue
import threading
import time

import mss
import numpy as np
from loguru import logger

from core.interfaces import ICaptureEngine
from utils import get_window_rect


class CaptureEngine(ICaptureEngine, threading.Thread):
    def __init__(self, hwnd: int, target_fps: int = 30):
        super().__init__(daemon=True, name="CaptureThread")
        self.hwnd = hwnd
        self.target_fps = target_fps
        self._running = threading.Event()
        self._frame_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=1)
        self._dimensions = (0, 0)

    def start_capture(self) -> None:
        self._running.set()
        if not self.is_alive():
            self.start()

    def stop_capture(self) -> None:
        self._running.clear()

    def get_latest_frame(self) -> np.ndarray | None:
        try:
            return self._frame_queue.get_nowait()
        except queue.Empty:
            return None

    def get_dimensions(self) -> tuple[int, int]:
        return self._dimensions

    def run(self) -> None:
        """Loop de captura com recuperação graceful de erros."""
        try:
            sct = mss.MSS()  # ✅ Inicialização protegida
            interval = 1.0 / max(1, self.target_fps)
            logger.info(f"CaptureEngine inicializado (Raw BGRA) a {self.target_fps} FPS.")
            
            while self._running.is_set():
                try:  # ✅ Protege cada iteração do loop
                    loop_start = time.perf_counter()
                    rect = get_window_rect(self.hwnd)

                    if not rect:
                        time.sleep(0.1)
                        continue

                    left, top, w, h = rect
                    self._dimensions = (w, h)
                    monitor = {"left": left, "top": top, "width": w, "height": h}

                    # Captura sem cvtColor na CPU: apenas lê buffer cru BGRA contíguo
                    sct_img = sct.grab(monitor)
                    frame_bgra = np.frombuffer(sct_img.raw, dtype=np.uint8).reshape((h, w, 4))

                    # Descarta frame antigo para processamento sem lag acumulado
                    if self._frame_queue.full():
                        try:
                            self._frame_queue.get_nowait()
                        except queue.Empty:
                            pass

                    self._frame_queue.put(frame_bgra)

                    elapsed = time.perf_counter() - loop_start
                    sleep_time = interval - elapsed
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                
                except Exception as e:  # ✅ Recuperação de erro no loop
                    logger.error(f"[CaptureEngine] Erro no loop de captura: {e}")
                    time.sleep(0.5)  # Evita loop infinito em caso de erro persistente
                    # Tenta recriar MSS context se corrompido
                    try:
                        sct = mss.MSS()
                    except Exception:
                        pass  # Mantém instância antiga se falhar
        
        except Exception as e:  # ✅ Falha fatal de inicialização
            logger.critical(f"[CaptureEngine] Falha fatal ao inicializar: {e}")
            logger.critical("[CaptureEngine] Thread de captura encerrada - bot não funcionará!")
        
        logger.info("[CaptureEngine] Thread de captura finalizada.")
