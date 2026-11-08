import sys
import time
from pathlib import Path

import torch
from PySide6 import QtCore, QtWidgets

from engine.capture_worker import CaptureEngine
from engine.interception_driver import InterceptionDriver
from engine.onnx_client import OnnxYoloClient
from engine.state_machine import CombatFSM
from utils import list_windows


class AutomationWorker(QtCore.QThread):
    log_signal = QtCore.Signal(str)
    telemetry_signal = QtCore.Signal(float, float, float, int, str)

    def __init__(self, hwnd: int, model_path: Path, mode: str = "HYBRID"):
        super().__init__()
        self.hwnd = hwnd
        self.model_path = model_path
        self.mode = mode
        self._is_running = True

    def run(self):
        # Repassa hwnd obrigatório para o InterceptionDriver
        driver = InterceptionDriver(hwnd=self.hwnd)
        fsm = CombatFSM(driver=driver, mode=self.mode)
        capture = CaptureEngine(hwnd=self.hwnd, target_fps=30)
        inference = OnnxYoloClient(self.model_path)

        capture.start_capture()
        time.sleep(0.3)

        fps_counter = 0
        last_metric_time = time.perf_counter()

        while self._is_running:
            frame = capture.get_latest_frame()
            if frame is None:
                time.sleep(0.005)
                continue

            detections = inference.infer(frame)
            fsm.update(detections)

            fps_counter += 1
            now = time.perf_counter()
            if now - last_metric_time >= 0.5:
                fps = fps_counter / (now - last_metric_time)
                vram = (
                    torch.cuda.memory_allocated() / (1024 * 1024)
                    if torch.cuda.is_available()
                    else 0.0
                )
                real_targets = sum(
                    1
                    for d in detections
                    if d.class_name in ("Target_Tag", "Top_Target_Tag", "Enemy_Tag")
                )

                self.telemetry_signal.emit(
                    fps, inference.last_inference_ms, vram, real_targets, fsm.state.name
                )
                fps_counter = 0
                last_metric_time = now

        capture.stop_capture()

    def stop(self):
        self._is_running = False
        self.wait()


class UnifiedMainWindow(QtWidgets.QMainWindow):
    def __init__(self, config=None, config_path=None, parent=None):
        super().__init__(parent)
        self.config = config or {}
        self.config_path = config_path

        self.setWindowTitle("Aion 2 - Combat Assistant & Automation")
        self.resize(800, 520)
        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e24; }
            QLabel { color: #f0f0f0; font-family: 'Segoe UI', sans-serif; }
            QGroupBox { color: #8ab4f8; font-weight: bold; border: 1px solid #3c4043; border-radius: 6px; margin-top: 10px; padding-top: 10px; }
            QComboBox { background-color: #2b2b36; color: #fff; border: 1px solid #4a4a5a; border-radius: 4px; padding: 4px 8px; }
            QPushButton { background-color: #3b82f6; color: white; font-weight: bold; border-radius: 6px; padding: 8px 16px; }
            QPushButton:hover { background-color: #2563eb; }
            QPushButton:disabled { background-color: #4b5563; color: #9ca3af; }
            QPlainTextEdit { background-color: #141418; color: #10b981; font-family: 'Consolas', monospace; border: 1px solid #2a2a32; border-radius: 4px; }
        """)

        self.worker: AutomationWorker | None = None
        self._init_ui()

    def _init_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        # 1. Configuração de Execução
        top_box = QtWidgets.QGroupBox("Configuração de Execução")
        top_layout = QtWidgets.QHBoxLayout(top_box)

        self.window_combo = QtWidgets.QComboBox()
        self.window_combo.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )

        self.btn_refresh = QtWidgets.QPushButton("🔄 Atualizar")
        self.btn_refresh.setToolTip("Atualizar lista de janelas ativas")
        self.btn_refresh.setStyleSheet("background-color: #4b5563; padding: 6px 12px;")
        self.btn_refresh.clicked.connect(self._populate_windows)

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["Híbrido (Standby & Rotação)", "Totalmente Autónomo"])

        self.btn_toggle = QtWidgets.QPushButton("Iniciar Assistente")
        self.btn_toggle.clicked.connect(self._toggle_bot)

        top_layout.addWidget(QtWidgets.QLabel("Janela:"))
        top_layout.addWidget(self.window_combo, 3)
        top_layout.addWidget(self.btn_refresh, 0)
        top_layout.addWidget(QtWidgets.QLabel("Modo:"))
        top_layout.addWidget(self.mode_combo, 2)
        top_layout.addWidget(self.btn_toggle, 1)

        layout.addWidget(top_box)

        # 2. Painel de Telemetria (Definição dos atributos da classe)
        telemetry_box = QtWidgets.QGroupBox("Painel de Telemetria")
        grid = QtWidgets.QGridLayout(telemetry_box)

        self.lbl_fps = QtWidgets.QLabel("FPS: --")
        self.lbl_cuda = QtWidgets.QLabel("Inferência: -- ms")
        self.lbl_vram = QtWidgets.QLabel("VRAM: -- MB")
        self.lbl_target = QtWidgets.QLabel("Alvos Ativos: 0")

        self.lbl_state = QtWidgets.QLabel("ESTADO: STANDBY")
        self.lbl_state.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.lbl_state.setStyleSheet(
            "background-color: #374151; color: #f9fafb; font-size: 14px; font-weight: bold; border-radius: 4px; padding: 6px;"
        )

        grid.addWidget(self.lbl_fps, 0, 0)
        grid.addWidget(self.lbl_cuda, 0, 1)
        grid.addWidget(self.lbl_vram, 0, 2)
        grid.addWidget(self.lbl_target, 0, 3)
        grid.addWidget(self.lbl_state, 1, 0, 1, 4)
        layout.addWidget(telemetry_box)

        # 3. Console de Logs
        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        layout.addWidget(self.console)

        # Popula a lista inicial de janelas
        self._populate_windows()

    def _populate_windows(self):
        self.window_combo.clear()
        windows = list_windows()

        parsed_windows = []
        for item in windows:
            if not isinstance(item, (tuple, list)) or len(item) < 2:
                continue
            first, second = item[0], item[1]
            if isinstance(first, str):
                title, hwnd = first, second
            else:
                hwnd, title = first, str(second)
            parsed_windows.append((hwnd, str(title)))

        aion_found = False
        for hwnd, title in parsed_windows:
            if "aion" in title.lower():
                self.window_combo.addItem(f"{title} ({hwnd})", hwnd)
                aion_found = True

        if not aion_found:
            for hwnd, title in parsed_windows:
                if title.strip():
                    self.window_combo.addItem(f"{title} ({hwnd})", hwnd)

    def _toggle_bot(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker = None
            self.btn_toggle.setText("Iniciar Assistente")
            self.btn_toggle.setStyleSheet("background-color: #3b82f6;")
            self.lbl_state.setText("ESTADO: DESLIGADO")
            self.lbl_state.setStyleSheet(
                "background-color: #374151; color: #9ca3af; padding: 6px; border-radius: 4px;"
            )
        else:
            hwnd = self.window_combo.currentData()
            if not hwnd:
                return

            mode = "HYBRID" if self.mode_combo.currentIndex() == 0 else "FULL_AUTO"
            model_path = Path("models/aion.pt")

            self.worker = AutomationWorker(hwnd=hwnd, model_path=model_path, mode=mode)
            self.worker.telemetry_signal.connect(self._update_telemetry)
            self.worker.start()

            self.btn_toggle.setText("Parar Assistente")
            self.btn_toggle.setStyleSheet("background-color: #ef4444;")

    @QtCore.Slot(float, float, float, int, str)
    def _update_telemetry(self, fps, cuda_ms, vram, targets, state_str):
        if hasattr(self, "lbl_fps"):
            self.lbl_fps.setText(f"FPS: {fps:.1f}")
        if hasattr(self, "lbl_cuda"):
            self.lbl_cuda.setText(f"Inferência: {cuda_ms:.1f} ms")
        if hasattr(self, "lbl_vram"):
            self.lbl_vram.setText(f"VRAM: {vram:.0f} MB")
        if hasattr(self, "lbl_target"):
            self.lbl_target.setText(f"Alvos Ativos: {targets}")

        if hasattr(self, "lbl_state"):
            self.lbl_state.setText(f"ESTADO: {state_str}")
            if state_str == "COMBAT":
                self.lbl_state.setStyleSheet(
                    "background-color: #dc2626; color: white; font-weight: bold; border-radius: 4px; padding: 6px;"
                )
            elif state_str == "STANDBY":
                self.lbl_state.setStyleSheet(
                    "background-color: #2563eb; color: white; font-weight: bold; border-radius: 4px; padding: 6px;"
                )
            else:
                self.lbl_state.setStyleSheet(
                    "background-color: #d97706; color: white; font-weight: bold; border-radius: 4px; padding: 6px;"
                )


def main():
    app = QtWidgets.QApplication(sys.argv)
    win = UnifiedMainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
