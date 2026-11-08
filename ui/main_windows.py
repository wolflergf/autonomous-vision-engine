import sys
import threading
import time
from pathlib import Path

import torch
from PySide6 import QtCore, QtWidgets

import skill_combo_config
from core.taxonomy import ENEMY_TAGS, TARGET_TAGS
from engine.capture_worker import CaptureEngine
from engine.hotkey_listener import HotkeyListener
from engine.interception_driver import InterceptionDriver
from engine.onnx_client import OnnxYoloClient
from engine.state_machine import CombatFSM
from utils import list_windows


class AutomationWorker(QtCore.QThread):
    telemetry_signal = QtCore.Signal(float, float, float, int, str)
    paused_state_changed = QtCore.Signal(bool)  # emitido quando pausa/retoma

    def __init__(
        self, hwnd: int, model_path: Path, mode: str = "HYBRID", enable_roam=None
    ):
        super().__init__()
        self.hwnd = hwnd
        self.model_path = model_path
        self.mode = mode
        # Roaming é ativado por padrão em FULL_AUTO; modos híbrido/assist não vagueiam pelo mapa.
        self.enable_roam = (
            (mode == "FULL_AUTO") if enable_roam is None else enable_roam
        )
        self._is_running = True
        self._paused = False
        self._pause_lock = threading.Lock()
        self._emergency_stop = False
        # Referências para limpeza segura no stop().
        self.driver = None
        self.fsm = None
        self.capture = None

    def run(self):
        driver = InterceptionDriver(hwnd=self.hwnd)
        fsm = CombatFSM(
            driver=driver,
            mode=self.mode,
            enable_roam=self.enable_roam,
            hwnd=self.hwnd,
        )
        capture = CaptureEngine(hwnd=self.hwnd, target_fps=30)
        inference = OnnxYoloClient(self.model_path)

        # Guarda referências para limpeza segura no stop().
        self.driver, self.fsm, self.capture = driver, fsm, capture

        capture.start_capture()
        time.sleep(0.3)
        fsm.update_screen_size(*capture.get_dimensions())

        # Confinamento do mouse (ClipCursor): trava o cursor na janela do
        # jogo enquanto o worker está ativo. Qualquer falha é best-effort
        # (não interrompe a automação).
        try:
            driver.lock_cursor()
        except Exception:
            pass

        fps_counter = 0
        last_metric_time = time.perf_counter()

        while self._is_running and not self._emergency_stop:
            # Pausa: aguarda sem processar frames enquanto estiver pausado.
            with self._pause_lock:
                paused = self._paused
            if paused:
                time.sleep(0.05)
                continue

            frame = capture.get_latest_frame()
            if frame is None:
                time.sleep(0.005)
                continue

            detections = inference.infer(frame)
            fsm.update_screen_size(*capture.get_dimensions())
            fsm.update(detections, frame=frame)

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
                    1 for d in detections if d.class_name in (TARGET_TAGS | ENEMY_TAGS)
                )

                self.telemetry_signal.emit(
                    fps, inference.last_inference_ms, vram, real_targets, fsm.state.name
                )
                fps_counter = 0
                last_metric_time = now

        # Encerra o roaming de forma limpa antes de liberar a captura.
        if self.fsm is not None:
            self.fsm.shutdown()
        capture.stop_capture()
        # Libera o cursor (ClipCursor off) ao encerrar o loop.
        try:
            driver.unlock_cursor()
        except Exception:
            pass

    def stop(self):
        self._is_running = False
        self.wait()
        # Rede de segurança: garante que a thread de roaming foi encerrada.
        if self.fsm is not None:
            self.fsm.shutdown()

    def pause(self):
        """Pausa imediatamente o processamento, força STANDBY na FSM e
        libera qualquer trava de chat (clear_chat_alert)."""
        with self._pause_lock:
            if self._paused:
                return  # já está pausado
            self._paused = True
        self.paused_state_changed.emit(True)
        # Libera o cursor para o usuário recuperar o mouse no pause (F9).
        if self.driver is not None:
            try:
                self.driver.unlock_cursor()
            except Exception:
                pass
        # Força STANDBY na FSM se existir.
        if self.fsm is not None:
            try:
                self.fsm.state = self.fsm.BotState.STANDBY
            except Exception:
                pass
            # Libera trava de chat se houver.
            try:
                self.fsm.clear_chat_alert()
            except Exception:
                pass

    def resume(self):
        """Retorna a operação normal após pausa."""
        with self._pause_lock:
            if not self._paused:
                return  # já está rodando
            self._paused = False
        self.paused_state_changed.emit(False)
        # Retrava o cursor na janela ao retomar a automação.
        if self.driver is not None:
            try:
                self.driver.lock_cursor()
            except Exception:
                pass

    def toggle_pause(self):
        """Alterna entre pausa e retomada."""
        with self._pause_lock:
            currently_paused = self._paused
        if currently_paused:
            self.resume()
        else:
            self.pause()

    def emergency_stop(self):
        """Parada de emergência (killswitch): interrompe o loop de captura,
        solta todas as teclas físicas e finaliza o worker."""
        self._emergency_stop = True
        self._is_running = False
        # Libera o cursor imediatamente no killswitch (F11).
        if self.driver is not None:
            try:
                self.driver.unlock_cursor()
            except Exception as e:
                print(f"[Killswitch] Erro ao liberar cursor: {e}")
        # Solta teclas imediatamente.
        if self.driver is not None:
            try:
                self.driver.release_all_keys()
            except Exception as e:
                print(f"[Killswitch] Erro ao soltar teclas: {e}")
        self.wait()
        if self.fsm is not None:
            try:
                self.fsm.shutdown()
            except Exception:
                pass


class UnifiedMainWindow(QtWidgets.QMainWindow):
    """Janela principal com abas de controle, telemetria e gerenciamento de skills."""

    skill_config_changed = QtCore.Signal()

    def __init__(self, config=None, config_path=None, parent=None):
        super().__init__(parent)
        self.config = config or {}
        self.config_path = config_path

        self.setWindowTitle("Aion 2 - Combat Assistant & Config Studio")
        self.resize(850, 600)
        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e24; }
            QLabel { color: #f0f0f0; font-family: 'Segoe UI', sans-serif; }
            QGroupBox { color: #8ab4f8; font-weight: bold; border: 1px solid #3c4043; border-radius: 6px; margin-top: 10px; padding-top: 10px; }
            QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox { background-color: #2b2b36; color: #fff; border: 1px solid #4a4a5a; border-radius: 4px; padding: 4px 8px; }
            QPushButton { background-color: #3b82f6; color: white; font-weight: bold; border-radius: 6px; padding: 8px 16px; }
            QPushButton:hover { background-color: #2563eb; }
            QTabBar::tab { background: #2b2b36; color: #9ca3af; padding: 8px 18px; border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right: 2px; }
            QTabBar::tab:selected { background: #3b82f6; color: white; font-weight: bold; }
            QTabWidget::pane { border: 1px solid #3c4043; background: #1e1e24; }
            QPlainTextEdit { background-color: #141418; color: #10b981; font-family: 'Consolas', monospace; border: 1px solid #2a2a32; border-radius: 4px; }
        """)

        self.worker: AutomationWorker | None = None
        self.skill_config_changed.connect(self._on_skill_config_changed)

        # --- Listener global de hotkeys (F9: pause/retoma | F11: killswitch) ---
        self.hotkey_listener = HotkeyListener()
        self.hotkey_listener.signals.pause_toggled.connect(
            self._on_hotkey_pause_toggled
        )
        self.hotkey_listener.signals.emergency_stop.connect(
            self._on_hotkey_emergency_stop
        )
        self.hotkey_listener.start()

        self._init_ui()

    def _init_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        # Header: Janela e Ação Principal
        header_box = QtWidgets.QGroupBox("Sessão do Jogo")
        header_layout = QtWidgets.QHBoxLayout(header_box)

        self.window_combo = QtWidgets.QComboBox()
        self.window_combo.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )

        self.btn_refresh = QtWidgets.QPushButton("🔄 Janelas")
        self.btn_refresh.clicked.connect(self._populate_windows)

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(
            [
                "Híbrido (Standby & Rotação)",
                "Totalmente Autónomo",
            ]
        )

        self.btn_toggle = QtWidgets.QPushButton("Iniciar Assistente")
        self.btn_toggle.clicked.connect(self._toggle_bot)

        header_layout.addWidget(QtWidgets.QLabel("Janela:"))
        header_layout.addWidget(self.window_combo, 3)
        header_layout.addWidget(self.btn_refresh, 0)
        header_layout.addWidget(QtWidgets.QLabel("Modo:"))
        header_layout.addWidget(self.mode_combo, 2)
        header_layout.addWidget(self.btn_toggle, 1)

        # Label com as hotkeys globais ativas.
        self.lbl_hotkeys = QtWidgets.QLabel(
            "🎹 F9: Pausar/Retomar  |  F11: Parada de Emergência"
        )
        self.lbl_hotkeys.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.lbl_hotkeys.setStyleSheet(
            "color: #8ab4f8; font-weight: bold; padding: 4px; border-radius: 4px; "
            "background-color: #2b2b36;"
        )
        header_layout.addWidget(self.lbl_hotkeys, 1)
        layout.addWidget(header_box)

        # Abas de Navegação
        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs)

        # Aba 1: Painel de Controle e Telemetria
        tab_dashboard = QtWidgets.QWidget()
        dash_layout = QtWidgets.QVBoxLayout(tab_dashboard)

        telemetry_box = QtWidgets.QGroupBox("Métricas em Tempo Real")
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
        dash_layout.addWidget(telemetry_box)

        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        dash_layout.addWidget(self.console)
        self.tabs.addTab(tab_dashboard, "Painel")

        # Aba 2: Combos & Skills — gerenciamento de skill_combo_config.json
        tab_skills = QtWidgets.QWidget()
        skills_vbox = QtWidgets.QVBoxLayout(tab_skills)

        # ── Tabela de habilidades ──
        skills_group = QtWidgets.QGroupBox("Habilidades Configuradas")
        skills_grid = QtWidgets.QGridLayout(skills_group)

        self.skills_table = QtWidgets.QTableWidget(0, 4)
        self.skills_table.setHorizontalHeaderLabels(
            ["Skill (ID / Nome)", "Tecla (Keybind)", "Cooldown (s)", "Habilitada"]
        )
        self.skills_table.horizontalHeader().setStretchLastSection(True)
        self.skills_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.skills_table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.skills_table.setAlternatingRowColors(True)

        self._load_skills_table()

        # Alteração na coluna 'Habilitada' dispara recarga de config em tempo de execução
        self.skills_table.itemChanged.connect(self._on_skills_table_item_changed)

        btn_layout = QtWidgets.QHBoxLayout()
        self.btn_add_skill = QtWidgets.QPushButton("➕ Adicionar Habilidade")
        self.btn_remove_skill = QtWidgets.QPushButton("➖ Remover Selecionada")
        self.btn_save_skills = QtWidgets.QPushButton("💾 Salvar Alterações")
        self.btn_save_skills.setStyleSheet("background-color: #10b981;")
        btn_layout.addWidget(self.btn_add_skill)
        btn_layout.addWidget(self.btn_remove_skill)
        btn_layout.addWidget(self.btn_save_skills)
        btn_layout.addStretch()

        skills_grid.addWidget(self.skills_table, 0, 0, 1, 2)
        skills_grid.addLayout(btn_layout, 1, 0, 1, 2)
        skills_vbox.addWidget(skills_group)

        # ── Limiares de sobrevivência do Clérigo ──
        healing_group = QtWidgets.QGroupBox(
            "Limiares de Cura (Clérigo — seção HEALING)"
        )
        healing_layout = QtWidgets.QFormLayout(healing_group)

        self.healing_spins: dict[str, QtWidgets.QDoubleSpinBox] = {}
        healing_defaults = {
            "potion": ("Poção — HP abaixo de:", 0.30),
            "heal": ("Cura Rápida (7) — HP abaixo de:", 0.50),
            "radiant": ("Radiant — HP abaixo de:", 0.70),
            "regen": ("Regen (HoT) — HP abaixo de:", 0.90),
        }
        healing_cfg = skill_combo_config.get_healing_config() or {}
        healing_skills = healing_cfg.get("SKILLS", {}) or {}
        for skill_id, (label, default) in healing_defaults.items():
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(0.0, 1.0)
            spin.setSingleStep(0.05)
            spin.setDecimals(2)
            skill_cfg = healing_skills.get(skill_id, {})
            spin.setValue(float(skill_cfg.get("hp_below", default)))
            self.healing_spins[skill_id] = spin
            healing_layout.addRow(QtWidgets.QLabel(label), spin)

        skills_vbox.addWidget(healing_group)
        skills_vbox.addStretch()

        self.tabs.addTab(tab_skills, "Combos & Skills")

        # ── Conexões dos botões ──
        self.btn_add_skill.clicked.connect(self._on_add_skill)
        self.btn_remove_skill.clicked.connect(self._on_remove_skill)
        self.btn_save_skills.clicked.connect(self._on_save_skills)

        # Aba 3: Suporte & Poções (HP / MP / Loot)
        tab_survival = QtWidgets.QWidget()
        surv_layout = QtWidgets.QFormLayout(tab_survival)

        self.chk_auto_pot = QtWidgets.QCheckBox("Usar Poção de Vida Automática")
        self.chk_auto_pot.setChecked(True)
        self.txt_pot_key = QtWidgets.QLineEdit("x")
        self.spin_pot_cooldown = QtWidgets.QDoubleSpinBox()
        self.spin_pot_cooldown.setValue(15.0)

        self.chk_auto_loot = QtWidgets.QCheckBox("Recolha Automática de Itens (F)")
        self.chk_auto_loot.setChecked(True)

        surv_layout.addRow(self.chk_auto_pot)
        surv_layout.addRow(QtWidgets.QLabel("Tecla da Poção:"), self.txt_pot_key)
        surv_layout.addRow(
            QtWidgets.QLabel("Cooldown da Poção (seg):"), self.spin_pot_cooldown
        )
        surv_layout.addRow(self.chk_auto_loot)
        self.tabs.addTab(tab_survival, "Sobrevivência & Loot")

        self._populate_windows()

    # ------------------------------------------------------------- skills table

    def _load_skills_table(self) -> None:
        """Carrega habilidades de ``skill_combo_config.SKILL_COOLDOWNS`` na tabela."""
        self.skills_table.setRowCount(0)
        cooldowns = skill_combo_config.SKILL_COOLDOWNS or {}
        metadata = skill_combo_config.SKILL_METADATA or {}
        for keybind, cooldown in sorted(cooldowns.items()):
            row = self.skills_table.rowCount()
            self.skills_table.insertRow(row)

            # Coluna 0: Nome / Identificador
            name_item = QtWidgets.QTableWidgetItem(str(keybind))
            self.skills_table.setItem(row, 0, name_item)

            # Coluna 1: Tecla (Keybind)
            key_item = QtWidgets.QTableWidgetItem(str(keybind))
            self.skills_table.setItem(row, 1, key_item)

            # Coluna 2: Cooldown (s) — QDoubleSpinBox como cell widget
            cd_spin = QtWidgets.QDoubleSpinBox()
            cd_spin.setRange(0.0, 999.0)
            cd_spin.setDecimals(1)
            cd_spin.setValue(float(cooldown))
            self.skills_table.setCellWidget(row, 2, cd_spin)

            # Coluna 3: Habilitada (checkbox via QTableWidgetItem)
            enabled = bool(metadata.get(str(keybind), {}).get("enabled", True))
            chk = QtWidgets.QTableWidgetItem()
            chk.setFlags(
                QtCore.Qt.ItemFlag.ItemIsUserCheckable
                | QtCore.Qt.ItemFlag.ItemIsEnabled
            )
            chk.setCheckState(
                QtCore.Qt.CheckState.Checked
                if enabled
                else QtCore.Qt.CheckState.Unchecked
            )
            self.skills_table.setItem(row, 3, chk)

    def _on_add_skill(self) -> None:
        """Adiciona uma linha vazia à tabela de habilidades."""
        row = self.skills_table.rowCount()
        self.skills_table.insertRow(row)

        name_item = QtWidgets.QTableWidgetItem("nova_skill")
        self.skills_table.setItem(row, 0, name_item)

        key_item = QtWidgets.QTableWidgetItem("1")
        self.skills_table.setItem(row, 1, key_item)

        cd_spin = QtWidgets.QDoubleSpinBox()
        cd_spin.setRange(0.0, 999.0)
        cd_spin.setDecimals(1)
        cd_spin.setValue(10.0)
        self.skills_table.setCellWidget(row, 2, cd_spin)

        chk = QtWidgets.QTableWidgetItem()
        chk.setFlags(
            QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsEnabled
        )
        chk.setCheckState(QtCore.Qt.CheckState.Checked)
        self.skills_table.setItem(row, 3, chk)

    def _on_remove_skill(self) -> None:
        """Remove a linha selecionada da tabela."""
        row = self.skills_table.currentRow()
        if row >= 0:
            self.skills_table.removeRow(row)

    def _collect_skills_data(self) -> tuple[dict[str, float], dict[str, dict]]:
        """Extrai dados da tabela e retorna (SKILL_COOLDOWNS, SKILL_METADATA)."""
        cooldowns: dict[str, float] = {}
        metadata: dict[str, dict] = {}
        for row in range(self.skills_table.rowCount()):
            key_item = self.skills_table.item(row, 1)
            cd_widget = self.skills_table.cellWidget(row, 2)
            chk_item = self.skills_table.item(row, 3)

            keybind = (key_item.text() if key_item else "").strip().lower()
            if not keybind:
                continue
            try:
                cooldown = float(cd_widget.value()) if cd_widget else 10.0
            except Exception:
                cooldown = 10.0
            enabled = (
                chk_item.checkState() == QtCore.Qt.CheckState.Checked
                if chk_item
                else True
            )

            cooldowns[keybind] = cooldown
            metadata[keybind] = {"enabled": enabled}
        return cooldowns, metadata

    def _on_skills_table_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        """Emite sinal de recarga quando a coluna 'Habilitada' (índice 3) é alterada."""
        if item.column() == 3:
            self.skill_config_changed.emit()

    def _on_save_skills(self) -> None:
        """Salva alterações na tabela e limiares de cura no JSON (escrita atômica)."""
        cooldowns, metadata = self._collect_skills_data()

        # Pool de rotação do SkillComboManager: reflete imediatamente as skills
        # ativas (coluna 'Habilitada' marcada) no SINGLE_SKILL_POOL.
        active_pool = [
            keybind for keybind, meta in metadata.items() if meta.get("enabled", True)
        ]

        # Atualiza SKILL_COOLDOWNS, SKILL_METADATA e SINGLE_SKILL_POOL no config
        # (grava atômica + refresh das variáveis de módulo)
        skill_combo_config.update_config(
            {
                "SKILL_COOLDOWNS": cooldowns,
                "SKILL_METADATA": metadata,
                "SINGLE_SKILL_POOL": active_pool,
            }
        )

        # Atualiza limiares de cura (HEALING.SKILLS[*].hp_below)
        healing_cfg = dict(skill_combo_config.get_healing_config() or {})
        healing_skills = healing_cfg.get("SKILLS", {}) or {}
        for skill_id, spin in self.healing_spins.items():
            if skill_id in healing_skills:
                healing_skills[skill_id]["hp_below"] = float(spin.value())
        healing_cfg["SKILLS"] = healing_skills
        skill_combo_config.update_config({"HEALING": healing_cfg})

        # Emite sinal para notificar recarga de config em tempo de execução
        self.skill_config_changed.emit()
        self.console.appendPlainText(
            f"[UI] skill_combo_config.json salvo (atômico); pool de rotação: "
            f"{len(active_pool)} skill(s) ativa(s); config recarregada."
        )

    @QtCore.Slot()
    def _on_skill_config_changed(self) -> None:
        """Recarrega variáveis de módulo do skill_combo_config para o SkillComboManager em execução."""
        skill_combo_config.reload_config()
        self.console.appendPlainText(
            "[UI] Variáveis do skill_combo_config atualizadas em tempo de execução."
        )

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
            # Rede de segurança: o loop do worker já libera o cursor, mas se
            # o driver ainda existir, garante o unlock aqui também.
            try:
                if self.worker.driver is not None:
                    self.worker.driver.unlock_cursor()
            except Exception:
                pass
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

            # Mapeia o índice do QComboBox para o modo da FSM/worker.
            mode_map = {0: "HYBRID", 1: "FULL_AUTO"}
            mode = mode_map.get(self.mode_combo.currentIndex(), "HYBRID")
            model_path = Path("models/aion.pt")

            self.worker = AutomationWorker(hwnd=hwnd, model_path=model_path, mode=mode)
            self.worker.telemetry_signal.connect(self._update_telemetry)
            self.worker.paused_state_changed.connect(
                self._on_worker_paused_state_changed
            )
            self.worker.start()

            self.btn_toggle.setText("Parar Assistente")
            self.btn_toggle.setStyleSheet("background-color: #ef4444;")

    # ------------------------------------------------------------------ hotkey handlers
    def _on_hotkey_pause_toggled(self, _requested: bool) -> None:
        """Chamado quando F9 é pressionado. Alterna pausa/retoma do worker."""
        if self.worker is None or not self.worker.isRunning():
            return
        self.worker.toggle_pause()

    def _on_hotkey_emergency_stop(self) -> None:
        """Chamado quando F11 é pressionado. Para o worker imediatamente."""
        if self.worker is None or not self.worker.isRunning():
            # Ainda assim, solta teclas por segurança.
            if self.worker is not None:
                try:
                    self.worker.driver.release_all_keys()
                except Exception:
                    pass
            return
        self.worker.emergency_stop()
        self.worker = None
        self.btn_toggle.setText("Iniciar Assistente")
        self.btn_toggle.setStyleSheet("background-color: #3b82f6;")
        self.lbl_state.setText("ESTADO: DESLIGADO")
        self.lbl_state.setStyleSheet(
            "background-color: #374151; color: #9ca3af; padding: 6px; border-radius: 4px;"
        )
        self.console.appendPlainText(
            "[HOTKEY] F11 — Parada de Emergência acionada. Bot desligado."
        )

    @QtCore.Slot(bool)
    def _on_worker_paused_state_changed(self, is_paused: bool) -> None:
        """Sincroniza a UI quando o worker muda o estado de pausa."""
        if is_paused:
            self.btn_toggle.setText("Pausado (F9 para retomar)")
            self.btn_toggle.setStyleSheet("background-color: #d97706;")
            self.lbl_state.setText("ESTADO: PAUSADO")
            self.lbl_state.setStyleSheet(
                "background-color: #d97706; color: white; font-weight: bold; "
                "border-radius: 4px; padding: 6px;"
            )
            self.console.appendPlainText("[HOTKEY] F9 — Bot PAUSADO.")
        else:
            self.btn_toggle.setText("Parar Assistente")
            self.btn_toggle.setStyleSheet("background-color: #ef4444;")
            self.lbl_state.setText("ESTADO: STANDBY")
            self.lbl_state.setStyleSheet(
                "background-color: #2563eb; color: white; font-weight: bold; "
                "border-radius: 4px; padding: 6px;"
            )
            self.console.appendPlainText("[HOTKEY] F9 — Bot RETOMADO.")

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

    def closeEvent(self, event: QtCore.QEvent) -> None:
        """Garante que o listener de hotkeys seja encerrado ao fechar a janela."""
        try:
            if self.worker is not None and self.worker.driver is not None:
                self.worker.driver.unlock_cursor()
        except Exception:
            pass
        try:
            self.hotkey_listener.stop()
        except Exception:
            pass
        super().closeEvent(event)


def main():
    app = QtWidgets.QApplication(sys.argv)
    win = UnifiedMainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
