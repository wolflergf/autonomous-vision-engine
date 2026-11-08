"""Main GUI application for AION using CustomTkinter.
Uses a local YOLO weight (models/aion.pt) to run realtime detections
and draw a click-through overlay on the selected game window.
"""

import ctypes
import os
import sys
import threading
import time
from tkinter import messagebox

import customtkinter as ctk
from loguru import logger

# PySide6 is still needed for the overlay (transparent click-through window)
from PySide6 import QtWidgets

import input_controller as ic
from detection import DetectionController
from overlay import OverlayWindow
from utils import get_window_rect, list_windows

# ============================================================================
# TRANSLATIONS (preserved exactly as before)
# ============================================================================
TRANSLATIONS = {
    "en": {
        "app_title": "AION Autoplay",
        "nav_dashboard": "Dashboard",
        "nav_skills": "Skills",
        "nav_combos": "Combos",
        "nav_cooldowns": "Cooldowns",
        "nav_logs": "Logs",
        "nav_settings": "Settings",
        "label_game_window": "Game window:",
        "label_settings": "Settings:",
        "label_settings_info": "Using local model weights: models/aion.pt (Ultralytics YOLO)",
        "label_input_backend": "Input backend:",
        "checkbox_simulate": "Simulate mode (log only, no real inputs)",
        "action_refresh": "Refresh",
        "status_available": "Available",
        "status_unavailable": "Unavailable",
        "status_unknown": "Unknown",
        "group_skill_config": "Skill Combo Configuration",
        "checkbox_stealth_attack": "Enable randomized attack mode",
        "label_standard_weight": "Standard Attack Weight:",
        "label_single_weight": "Single Skill Weight:",
        "label_combo_weight": "Combo Set Weight:",
        "label_force_skill_mode": "Force Skill Before Standard:",
        "force_mode_ready_only": "Ready-only",
        "force_mode_always": "Always",
        "force_mode_disabled": "Disabled",
        "checkbox_combat_skills": "Enable skills & combos during combat",
        "label_outnumbered_threshold": "Outnumbered threshold (enemies):",
        "label_defensive_cooldown": "Defensive reuse (sec):",
        "button_edit_skills": "⚙️ Edit Skills",
        "button_edit_combos": "🎯 Edit Combos",
        "button_start": "Start",
        "button_stop": "Stop",
        "button_emergency_stop": "EMERGENCY STOP",
        "group_cooldown_monitor": "Cooldown Monitor",
        "skills_table_header_skill": "Skill",
        "skills_table_header_remaining": "Remaining(s)",
        "skills_table_header_cooldown": "Cooldown(s)",
        "combos_table_header_combo": "Combo",
        "combos_table_header_status": "Status",
        "status_ready": "Ready",
        "status_cooldown": "CD: {seconds}s",
        "settings_language_group": "Language",
        "settings_language_label": "Interface language:",
        "language_english": "English",
        "language_korean": "Korean",
        "log_skill_config_updated": "✓ Skill combo configuration updated",
        "log_no_window_selected": "No window selected",
        "log_unable_get_window_rect": "Unable to get window rect",
        "log_started_detection": "Started detection",
        "log_stopped": "Stopped",
        "log_emergency_stop": "EMERGENCY STOP: Automation disabled",
        "log_simulate_mode": "Simulate mode (DRY_RUN) set to: {state}",
        "state_on": "ON",
        "state_off": "OFF",
        "prestart_title": "Before You Start",
        "prestart_info": (
            "PLEASE VERIFY THE FOLLOWING BEFORE STARTING:\n\n"
            "✓ Player is in the hunting/farming area\n"
            "✓ Interception driver is installed (program will prompt if missing)\n"
            "✓ Skills and Combos are configured correctly (avoid invalid inputs)\n\n"
            "═══════════════════════════════════════════════════\n"
            "RECOMMENDED IN-GAME SETTINGS:\n"
            "═══════════════════════════════════════════════════\n\n"
            "Graphics → Display Mode → Windowed Mode\n"
            "Graphics → Nvidia Reflex → BOOST (if using Nvidia)\n\n"
            "Key Settings → Change Target → Tab\n\n"
            "Combat → Target Search → Priority Direction → Camera Forward\n"
            "Combat → Target Search → Details → Closest target first\n\n"
            "Combat → Controls → Control Mode → AION 1\n"
            "Combat → Controls → Ground Click Movement → Allow Both\n"
            "Combat → Controls → Follow Target on Skill Use → ON\n"
            "Combat → Controls → Repeat Basic Attack → ON\n"
            "Combat → Controls → Auto Target on Skill Use → ON"
        ),
        "prestart_button": "I Understand and Have Done Everything",
        "msg_interception_missing_title": "Interception Driver",
        "msg_interception_missing_body": "Interception driver appears missing. Please install and reboot.",
        "button_ok": "OK",
        "button_save": "💾 Save",
        "button_cancel": "✗ Cancel",
        "button_add": "➕ Add",
        "button_remove": "➖ Remove",
        "button_new_combo": "➕ New Combo",
        "button_delete_combo": "🗑️ Delete Combo",
        "skill_editor_title": "Skill Cooldown Editor",
        "skill_editor_subtitle": "Configure individual skill cooldowns and the single-skill pool.",
        "group_skill_cooldowns": "Individual Skill Cooldowns",
        "group_single_skill_pool": "Single Skill Pool",
        "group_skill_timing": "Skill Timing",
        "label_single_skill_gcd": "Single Skill GCD:",
        "group_skill_metadata": "Skill Metadata",
        "label_skill_type": "Skill type:",
        "skill_type_single": "Single target",
        "skill_type_cleave": "Cleave (2-3 targets)",
        "skill_type_aoe": "AOE (multiple)",
        "label_min_enemy_count": "Min enemy count:",
        "checkbox_save_for_pack": "Save for pack",
        "checkbox_defensive_skill": "Defensive skill",
        "combo_editor_title": "Combo Set Editor",
        "combo_editor_subtitle": "Create and manage skill combo sets.",
        "label_combo_name": "Combo Name:",
        "label_combo_cooldown": "Combo Cooldown (sec):",
        "label_combo_delay": "Delay Between Skills (sec):",
        "label_combo_skills": "Skills (one per line):",
        "checkbox_combo_enabled": "Enabled",
        "msg_combo_saved": "Combo saved!",
        "msg_combo_deleted": "Combo deleted!",
        "msg_skill_saved": "Skills saved!",
        "capture_title": "Capture Key",
        "capture_instruction": "Press a skill key:\n\nSupported: F1-F9, 1-9, 0, -, =",
        "capture_waiting": "Waiting...",
        "capture_confirm": "✓ Confirm",
        "capture_cancel": "✗ Cancel",
        "capture_invalid": "Invalid key! Use F1-F9, 1-9, 0, -, =",
        "log_skills_updated": "✓ Skills updated",
        "button_capture_key": "⌨️ Capture Key",
        "button_add_skill_key": "⌨️ Add Skill Key",
        "skill_pool_hint": "Tip: Use capture button to add keys, click to select and remove.",
        "table_header_keybind": "Key",
        "table_header_cooldown": "Cooldown (s)",
        "nav_queue": "Combat Queue",
        "queue_title": "Combat Action Queue",
        "queue_subtitle": "Real-time view of combat inputs and duplicate prevention.",
        "queue_pending": "Pending Actions",
        "queue_history": "Recent Actions",
        "queue_stats": "Statistics",
        "queue_total_queued": "Total Queued:",
        "queue_total_executed": "Total Executed:",
        "queue_total_blocked": "Blocked (Duplicates):",
        "queue_clear": "🗑️ Clear Queue",
        "queue_status_pending": "Pending",
        "queue_status_executing": "Executing",
        "queue_status_completed": "Completed",
        "queue_status_blocked": "Blocked",
        "queue_empty": "No pending actions",
        "label_random_combat_chance": "Random Combat Chance:",
        "label_random_combat_percent": "{percent}%",
        "settings_combat_group": "Combat Settings",
    },
    "ko": {
        "app_title": "AION 자동 실행",
        "nav_dashboard": "대시보드",
        "nav_skills": "스킬",
        "nav_combos": "콤보",
        "nav_cooldowns": "재사용 대기",
        "nav_queue": "전투 대기열",
        "nav_logs": "로그",
        "nav_settings": "설정",
        "label_game_window": "게임 창:",
        "label_settings": "설정:",
        "label_settings_info": "로컬 모델: models/aion.pt (YOLO)",
        "label_input_backend": "입력 백엔드:",
        "checkbox_simulate": "시뮬레이션 모드",
        "action_refresh": "새로 고침",
        "status_available": "사용 가능",
        "status_unavailable": "사용 불가",
        "status_unknown": "알 수 없음",
        "group_skill_config": "스킬 콤보 설정",
        "checkbox_stealth_attack": "랜덤 공격 모드",
        "label_standard_weight": "일반 공격 가중치:",
        "label_single_weight": "단일 스킬 가중치:",
        "label_combo_weight": "콤보 세트 가중치:",
        "label_force_skill_mode": "스킬 강제:",
        "force_mode_ready_only": "준비 시만",
        "force_mode_always": "항상",
        "force_mode_disabled": "사용 안 함",
        "checkbox_combat_skills": "전투 중 스킬 사용",
        "label_outnumbered_threshold": "열세 기준 (적 수):",
        "label_defensive_cooldown": "방어 재사용 (초):",
        "button_edit_skills": "⚙️ 스킬 편집",
        "button_edit_combos": "🎯 콤보 편집",
        "button_start": "시작",
        "button_stop": "중지",
        "button_emergency_stop": "비상 정지",
        "group_cooldown_monitor": "재사용 대기 모니터",
        "skills_table_header_skill": "스킬",
        "skills_table_header_remaining": "남은 시간(초)",
        "skills_table_header_cooldown": "쿨다운(초)",
        "combos_table_header_combo": "콤보",
        "combos_table_header_status": "상태",
        "status_ready": "준비됨",
        "status_cooldown": "대기: {seconds}초",
        "settings_language_group": "언어",
        "settings_language_label": "인터페이스 언어:",
        "language_english": "영어",
        "language_korean": "한국어",
        "log_skill_config_updated": "✓ 스킬 설정 업데이트됨",
        "log_no_window_selected": "선택된 창 없음",
        "log_unable_get_window_rect": "창 위치 가져오기 실패",
        "log_started_detection": "감지 시작됨",
        "log_stopped": "정지됨",
        "log_emergency_stop": "긴급 정지: 자동화 비활성화",
        "log_simulate_mode": "시뮬레이션 모드: {state}",
        "state_on": "켬",
        "state_off": "끔",
        "prestart_title": "시작 전 확인",
        "prestart_info": (
            "시작 전 반드시 확인하세요:\n\n"
            "✓ 캐릭터가 사냥/파밍 지역에 있는지 확인\n"
            "✓ Interception 드라이버 설치됨 (없으면 프로그램이 안내)\n"
            "✓ 스킬/콤보 설정이 올바른지 확인 (잘못된 입력 방지)\n\n"
            "═══════════════════════════════════════════════════\n"
            "권장 인게임 설정:\n"
            "═══════════════════════════════════════════════════\n\n"
            "그래픽 → 표시 모드 → 창 모드\n"
            "그래픽 → Nvidia Reflex → BOOST (Nvidia 사용 시)\n\n"
            "키 설정 → 타겟 변경 → Tab 키\n\n"
            "전투 → 타겟 탐색 → 우선 탐색 방향 → 카메라 전방\n"
            "전투 → 타겟 탐색 → 세부 설정 → 가장 가까운 대상 우선\n\n"
            "전투 → 조작 → 조작 모드 → AION 1\n"
            "전투 → 조작 → 지면 클릭 이동 → 양쪽 허용\n"
            "전투 → 조작 → 스킬 사용 시 대상 추적 → 켬\n"
            "전투 → 조작 → 기본 공격 반복 사용 → 켬\n"
            "전투 → 조작 → 스킬 사용 시 자동 타겟 → 켬"
        ),
        "prestart_button": "모두 완료했습니다",
        "msg_interception_missing_title": "Interception 드라이버",
        "msg_interception_missing_body": "Interception 드라이버가 없습니다. 설치 후 재부팅하세요.",
        "button_ok": "확인",
        "button_save": "💾 저장",
        "button_cancel": "✗ 취소",
        "button_add": "➕ 추가",
        "button_remove": "➖ 제거",
        "button_new_combo": "➕ 새 콤보",
        "button_delete_combo": "🗑️ 콤보 삭제",
        "skill_editor_title": "스킬 쿨다운 편집기",
        "skill_editor_subtitle": "개별 스킬 쿨다운과 스킬 풀을 설정하세요.",
        "group_skill_cooldowns": "개별 스킬 쿨다운",
        "group_single_skill_pool": "단일 스킬 풀",
        "group_skill_timing": "스킬 타이밍",
        "label_single_skill_gcd": "단일 스킬 GCD:",
        "group_skill_metadata": "스킬 메타데이터",
        "label_skill_type": "스킬 유형:",
        "skill_type_single": "단일 대상",
        "skill_type_cleave": "광역(2-3 대상)",
        "skill_type_aoe": "AOE(다수)",
        "label_min_enemy_count": "최소 적 수:",
        "checkbox_save_for_pack": "무리에서만 사용",
        "checkbox_defensive_skill": "방어 스킬",
        "combo_editor_title": "콤보 세트 편집기",
        "combo_editor_subtitle": "스킬 콤보 세트를 생성하고 관리하세요.",
        "label_combo_name": "콤보 이름:",
        "label_combo_cooldown": "콤보 쿨다운 (초):",
        "label_combo_delay": "스킬 간 지연 (초):",
        "label_combo_skills": "스킬 (줄당 하나씩):",
        "checkbox_combo_enabled": "사용",
        "msg_combo_saved": "콤보가 저장되었습니다!",
        "msg_combo_deleted": "콤보가 삭제되었습니다!",
        "msg_skill_saved": "스킬이 저장되었습니다!",
        "capture_title": "키 캡처",
        "capture_instruction": "스킬 키를 누르세요:\n\n지원: F1-F9, 1-9, 0, -, =",
        "capture_waiting": "대기 중...",
        "capture_confirm": "✓ 확인",
        "capture_cancel": "✗ 취소",
        "capture_invalid": "잘못된 키입니다! F1-F9, 1-9, 0, -, = 를 사용하세요",
        "log_skills_updated": "✓ 스킬 업데이트됨",
        "button_capture_key": "⌨️ 키 캡처",
        "button_add_skill_key": "⌨️ 스킬 키 추가",
        "skill_pool_hint": "팁: 캡처 버튼으로 키를 추가하고, 클릭으로 선택하여 제거하세요.",
        "table_header_keybind": "키",
        "table_header_cooldown": "쿨다운 (초)",
        "queue_title": "전투 동작 대기열",
        "queue_subtitle": "전투 입력 및 중복 방지 실시간 보기.",
        "queue_pending": "대기 중인 동작",
        "queue_history": "최근 동작",
        "queue_stats": "통계",
        "queue_total_queued": "총 대기:",
        "queue_total_executed": "총 실행:",
        "queue_total_blocked": "차단됨 (중복):",
        "queue_clear": "🗑️ 대기열 지우기",
        "queue_status_pending": "대기 중",
        "queue_status_executing": "실행 중",
        "queue_status_completed": "완료됨",
        "queue_status_blocked": "차단됨",
        "queue_empty": "대기 중인 동작 없음",
        "label_random_combat_chance": "랜덤 전투 확률:",
        "label_random_combat_percent": "{percent}%",
        "settings_combat_group": "전투 설정",
    },
}


def translate_text(language: str, key: str, **kwargs) -> str:
    """Get translated text for key in specified language."""
    lang_map = TRANSLATIONS.get(language, TRANSLATIONS.get("en", {}))
    text = lang_map.get(key)
    if text is None:
        text = TRANSLATIONS["en"].get(key, key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception:
            pass
    return text


def is_admin():
    """Check if the program is running with administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def run_as_admin():
    """Restart the program with administrator privileges."""
    try:
        script_path = os.path.abspath(sys.argv[0])
        args = f'"{script_path}"'
        if len(sys.argv) > 1:
            args += " " + " ".join([f'"{arg}"' for arg in sys.argv[1:]])
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, args, os.getcwd(), 1
        )
        if int(ret) <= 32:
            raise OSError(f"ShellExecuteW failed with code {ret}")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Failed to elevate privileges: {e}")
        return False
    return True


# ============================================================================
# CUSTOMTKINTER MAIN APPLICATION
# ============================================================================


class AIONApp(ctk.CTk):
    """Main application window using CustomTkinter."""

    def __init__(self):
        super().__init__()

        # Load config
        try:
            import skill_combo_config as scc

            self._config = scc
        except Exception:
            self._config = None

        # Language
        self.current_language = "en"
        if self._config:
            self.current_language = getattr(self._config, "LANGUAGE", "en") or "en"

        # Configure window
        self.title(self.tr("app_title"))
        self.geometry("1100x700")
        self.minsize(900, 600)

        # Set dark theme
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        # Configure grid
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # State
        self._controller = None
        self._overlay = None
        self._automation_enabled = True
        self._qt_app = None

        # Build UI
        self._create_sidebar()
        self._create_main_frame()
        self._create_pages()

        # Show dashboard by default
        self._show_page("dashboard")

        # Refresh window list
        self._refresh_windows()

        # Start overlay system (Qt app in background thread)
        self._start_overlay_system()

        # Protocol for window close
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def tr(self, key: str, **kwargs) -> str:
        """Translate key to current language."""
        return translate_text(self.current_language, key, **kwargs)

    def _create_sidebar(self):
        """Create the navigation sidebar."""
        self.sidebar = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nswe")
        self.sidebar.grid_rowconfigure(10, weight=1)

        # Logo/Title
        self.logo_label = ctk.CTkLabel(
            self.sidebar,
            text="AION\nAutoplay",
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        # Navigation buttons
        nav_items = [
            ("nav_dashboard", "🏠", "dashboard"),
            ("nav_skills", "⚙️", "skills"),
            ("nav_combos", "🎯", "combos"),
            ("nav_cooldowns", "⏱️", "cooldowns"),
            ("nav_queue", "📊", "queue"),
            ("nav_logs", "📜", "logs"),
            ("nav_settings", "🔧", "settings"),
        ]

        self.nav_buttons = {}
        for i, (key, icon, page_name) in enumerate(nav_items, start=1):
            btn = ctk.CTkButton(
                self.sidebar,
                text=f"{icon}  {self.tr(key)}",
                font=ctk.CTkFont(size=14),
                height=40,
                corner_radius=8,
                fg_color="transparent",
                text_color=("gray10", "gray90"),
                hover_color=("gray70", "gray30"),
                anchor="w",
                command=lambda p=page_name: self._show_page(p),
            )
            btn.grid(row=i, column=0, padx=10, pady=5, sticky="we")
            self.nav_buttons[page_name] = btn

    def _create_main_frame(self):
        """Create the main content area."""
        self.main_frame = ctk.CTkFrame(self, corner_radius=10)
        self.main_frame.grid(row=0, column=1, padx=10, pady=10, sticky="nswe")
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(0, weight=1)

    def _create_pages(self):
        """Create all page frames."""
        self.pages = {}

        # Dashboard page
        self.pages["dashboard"] = self._create_dashboard_page()

        # Skills page
        self.pages["skills"] = self._create_skills_page()

        # Combos page
        self.pages["combos"] = self._create_combos_page()

        # Cooldowns page
        self.pages["cooldowns"] = self._create_cooldowns_page()

        # Combat Queue page
        self.pages["queue"] = self._create_queue_page()

        # Logs page
        self.pages["logs"] = self._create_logs_page()

        # Settings page
        self.pages["settings"] = self._create_settings_page()

    def _show_page(self, page_name: str):
        """Show the specified page and hide others."""
        # Update button colors
        for name, btn in self.nav_buttons.items():
            if name == page_name:
                btn.configure(fg_color=("gray75", "gray25"))
            else:
                btn.configure(fg_color="transparent")

        # Hide all pages
        for page in self.pages.values():
            page.grid_forget()

        # Show selected page
        if page_name in self.pages:
            self.pages[page_name].grid(row=0, column=0, padx=20, pady=20, sticky="nswe")

    def _create_dashboard_page(self) -> ctk.CTkFrame:
        """Create the dashboard page."""
        page = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)

        # Title
        title = ctk.CTkLabel(
            page,
            text=self.tr("nav_dashboard"),
            font=ctk.CTkFont(size=24, weight="bold"),
        )
        title.grid(row=0, column=0, pady=(0, 20), sticky="w")

        # Window selection
        win_frame = ctk.CTkFrame(page)
        win_frame.grid(row=1, column=0, pady=10, sticky="we")
        win_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(win_frame, text=self.tr("label_game_window")).grid(
            row=0, column=0, padx=10, pady=10
        )
        self.window_combo = ctk.CTkComboBox(win_frame, values=[], width=400)
        self.window_combo.grid(row=0, column=1, padx=10, pady=10, sticky="we")
        self.refresh_btn = ctk.CTkButton(
            win_frame,
            text=self.tr("action_refresh"),
            width=100,
            command=self._refresh_windows,
        )
        self.refresh_btn.grid(row=0, column=2, padx=10, pady=10)

        # Backend selection
        backend_frame = ctk.CTkFrame(page)
        backend_frame.grid(row=2, column=0, pady=10, sticky="we")

        ctk.CTkLabel(backend_frame, text=self.tr("label_input_backend")).grid(
            row=0, column=0, padx=10, pady=10
        )
        self.backend_combo = ctk.CTkComboBox(
            backend_frame, values=["interception"], width=200
        )
        self.backend_combo.grid(row=0, column=1, padx=10, pady=10)
        self.backend_combo.set("interception")

        self.simulate_var = ctk.BooleanVar(value=False)
        self.simulate_cb = ctk.CTkCheckBox(
            backend_frame,
            text=self.tr("checkbox_simulate"),
            variable=self.simulate_var,
            command=self._on_simulate_toggle,
        )
        self.simulate_cb.grid(row=0, column=2, padx=20, pady=10)

        # Control buttons
        btn_frame = ctk.CTkFrame(page, fg_color="transparent")
        btn_frame.grid(row=3, column=0, pady=20, sticky="we")

        self.start_btn = ctk.CTkButton(
            btn_frame,
            text=self.tr("button_start"),
            font=ctk.CTkFont(size=16, weight="bold"),
            height=50,
            width=150,
            fg_color="green",
            hover_color="darkgreen",
            command=self._start_detection,
        )
        self.start_btn.pack(side="left", padx=10)

        self.stop_btn = ctk.CTkButton(
            btn_frame,
            text=self.tr("button_stop"),
            font=ctk.CTkFont(size=16, weight="bold"),
            height=50,
            width=150,
            fg_color="gray",
            state="disabled",
            command=self._stop_detection,
        )
        self.stop_btn.pack(side="left", padx=10)

        self.emergency_btn = ctk.CTkButton(
            btn_frame,
            text=self.tr("button_emergency_stop"),
            font=ctk.CTkFont(size=16, weight="bold"),
            height=50,
            width=200,
            fg_color="red",
            hover_color="darkred",
            command=self._emergency_stop,
        )
        self.emergency_btn.pack(side="left", padx=10)

        # Info label
        info = ctk.CTkLabel(
            page, text=self.tr("label_settings_info"), text_color="gray"
        )
        info.grid(row=4, column=0, pady=10, sticky="w")

        return page

    def _create_skills_page(self) -> ctk.CTkFrame:
        """Create the skills configuration page with embedded skill editor."""
        page = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_columnconfigure(1, weight=1)
        page.grid_rowconfigure(2, weight=1)

        title = ctk.CTkLabel(
            page, text=self.tr("nav_skills"), font=ctk.CTkFont(size=24, weight="bold")
        )
        title.grid(row=0, column=0, columnspan=2, pady=(0, 10), sticky="w")

        subtitle = ctk.CTkLabel(
            page, text=self.tr("skill_editor_subtitle"), text_color="gray"
        )
        subtitle.grid(row=0, column=0, columnspan=2, pady=(35, 10), sticky="w")

        # === Top Row: Config Options ===
        config_frame = ctk.CTkFrame(page)
        config_frame.grid(row=1, column=0, columnspan=2, pady=10, sticky="we")
        config_frame.grid_columnconfigure(1, weight=1)
        config_frame.grid_columnconfigure(3, weight=1)

        # Row 0: Stealth + Combat Skills
        self.stealth_var = ctk.BooleanVar(value=False)
        if self._config:
            self.stealth_var.set(
                getattr(self._config, "STEALTH_ATTACK_MODE_ENABLED", False)
            )
        ctk.CTkCheckBox(
            config_frame,
            text=self.tr("checkbox_stealth_attack"),
            variable=self.stealth_var,
            command=self._update_skill_config,
        ).grid(row=0, column=0, padx=20, pady=8, sticky="w")

        self.combat_skills_var = ctk.BooleanVar(value=True)
        if self._config:
            self.combat_skills_var.set(getattr(self._config, "COMBAT_USE_SKILLS", True))
        ctk.CTkCheckBox(
            config_frame,
            text=self.tr("checkbox_combat_skills"),
            variable=self.combat_skills_var,
            command=self._update_skill_config,
        ).grid(row=0, column=2, padx=20, pady=8, sticky="w")

        # Row 1: Force skill mode + GCD
        ctk.CTkLabel(config_frame, text=self.tr("label_force_skill_mode")).grid(
            row=1, column=0, padx=20, pady=8, sticky="w"
        )
        self.force_skill_var = ctk.StringVar(value="ready_only")
        if self._config:
            self.force_skill_var.set(
                getattr(self._config, "FORCE_SKILL_BEFORE_STANDARD_MODE", "ready_only")
            )
        force_options = [
            self.tr("force_mode_ready_only"),
            self.tr("force_mode_always"),
            self.tr("force_mode_disabled"),
        ]
        self.force_skill_combo = ctk.CTkComboBox(
            config_frame,
            values=force_options,
            command=self._on_force_skill_change,
            width=150,
        )
        self.force_skill_combo.grid(row=1, column=1, padx=10, pady=8, sticky="w")
        self._set_force_skill_combo()

        ctk.CTkLabel(config_frame, text=self.tr("label_single_skill_gcd")).grid(
            row=1, column=2, padx=20, pady=8, sticky="w"
        )
        self.skill_gcd_entry = ctk.CTkEntry(config_frame, width=80)
        self.skill_gcd_entry.grid(row=1, column=3, padx=10, pady=8, sticky="w")
        if self._config:
            self.skill_gcd_entry.insert(
                0, str(getattr(self._config, "SINGLE_SKILL_GLOBAL_COOLDOWN", 1.5))
            )

        # Row 2: Weights
        ctk.CTkLabel(config_frame, text=self.tr("label_standard_weight")).grid(
            row=2, column=0, padx=20, pady=8, sticky="w"
        )
        self.standard_weight = ctk.CTkSlider(
            config_frame,
            from_=0,
            to=1,
            number_of_steps=20,
            command=self._update_skill_config,
            width=150,
        )
        self.standard_weight.grid(row=2, column=1, padx=10, pady=8, sticky="w")
        if self._config:
            self.standard_weight.set(
                self._config.ATTACK_MODE_WEIGHTS.get("standard_attack", 0.5)
            )

        ctk.CTkLabel(config_frame, text=self.tr("label_single_weight")).grid(
            row=2, column=2, padx=20, pady=8, sticky="w"
        )
        self.single_weight = ctk.CTkSlider(
            config_frame,
            from_=0,
            to=1,
            number_of_steps=20,
            command=self._update_skill_config,
            width=150,
        )
        self.single_weight.grid(row=2, column=3, padx=10, pady=8, sticky="w")
        if self._config:
            self.single_weight.set(
                self._config.ATTACK_MODE_WEIGHTS.get("single_skill", 0.3)
            )

        # Row 3: More weights + thresholds
        ctk.CTkLabel(config_frame, text=self.tr("label_combo_weight")).grid(
            row=3, column=0, padx=20, pady=8, sticky="w"
        )
        self.combo_weight = ctk.CTkSlider(
            config_frame,
            from_=0,
            to=1,
            number_of_steps=20,
            command=self._update_skill_config,
            width=150,
        )
        self.combo_weight.grid(row=3, column=1, padx=10, pady=8, sticky="w")
        if self._config:
            self.combo_weight.set(
                self._config.ATTACK_MODE_WEIGHTS.get("combo_set", 0.2)
            )

        ctk.CTkLabel(config_frame, text=self.tr("label_outnumbered_threshold")).grid(
            row=3, column=2, padx=20, pady=8, sticky="w"
        )
        self.outnumbered_spin = ctk.CTkEntry(config_frame, width=80)
        self.outnumbered_spin.grid(row=3, column=3, padx=10, pady=8, sticky="w")
        if self._config:
            self.outnumbered_spin.insert(
                0, str(getattr(self._config, "OUTNUMBERED_THRESHOLD", 3))
            )

        # Row 4: Defensive cooldown
        ctk.CTkLabel(config_frame, text=self.tr("label_defensive_cooldown")).grid(
            row=4, column=0, padx=20, pady=8, sticky="w"
        )
        self.defensive_cd = ctk.CTkEntry(config_frame, width=80)
        self.defensive_cd.grid(row=4, column=1, padx=10, pady=8, sticky="w")
        if self._config:
            self.defensive_cd.insert(
                0, str(getattr(self._config, "DEFENSIVE_COOLDOWN_SEC", 8.0))
            )

        # === Bottom Row: Skill Cooldowns (left) + Skill Pool (right) ===
        # Left side: Skill Cooldowns
        left_frame = ctk.CTkFrame(page)
        left_frame.grid(row=2, column=0, padx=(0, 5), pady=10, sticky="nswe")
        left_frame.grid_columnconfigure(0, weight=1)
        left_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            left_frame,
            text=self.tr("group_skill_cooldowns"),
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, padx=10, pady=10, sticky="w")

        self.skill_cd_scroll = ctk.CTkScrollableFrame(left_frame)
        self.skill_cd_scroll.grid(row=1, column=0, padx=10, pady=5, sticky="nswe")

        # Skill cooldown buttons
        skill_btn_frame = ctk.CTkFrame(left_frame, fg_color="transparent")
        skill_btn_frame.grid(row=2, column=0, padx=10, pady=10, sticky="we")

        self.add_skill_cd_btn = ctk.CTkButton(
            skill_btn_frame,
            text=self.tr("button_add_skill_key"),
            command=self._add_skill_cooldown,
            fg_color="#4CAF50",
            hover_color="#66BB6A",
            width=140,
        )
        self.add_skill_cd_btn.pack(side="left", padx=(0, 5))

        self.remove_skill_cd_btn = ctk.CTkButton(
            skill_btn_frame,
            text=self.tr("button_remove"),
            command=self._remove_skill_cooldown,
            fg_color="#f44336",
            hover_color="#EF5350",
            width=100,
        )
        self.remove_skill_cd_btn.pack(side="left")

        # Right side: Skill Pool
        right_frame = ctk.CTkFrame(page)
        right_frame.grid(row=2, column=1, padx=(5, 0), pady=10, sticky="nswe")
        right_frame.grid_columnconfigure(0, weight=1)
        right_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            right_frame,
            text=self.tr("group_single_skill_pool"),
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, padx=10, pady=10, sticky="w")

        self.skill_pool_scroll = ctk.CTkScrollableFrame(right_frame)
        self.skill_pool_scroll.grid(row=1, column=0, padx=10, pady=5, sticky="nswe")

        # Pool hint
        ctk.CTkLabel(
            right_frame,
            text=self.tr("skill_pool_hint"),
            text_color="gray",
            font=ctk.CTkFont(size=11),
        ).grid(row=2, column=0, padx=10, pady=5, sticky="w")

        # Pool buttons
        pool_btn_frame = ctk.CTkFrame(right_frame, fg_color="transparent")
        pool_btn_frame.grid(row=3, column=0, padx=10, pady=10, sticky="we")

        self.add_pool_btn = ctk.CTkButton(
            pool_btn_frame,
            text=self.tr("button_capture_key"),
            command=self._add_pool_skill_embedded,
            fg_color="#5E35B1",
            hover_color="#7E57C2",
            width=140,
        )
        self.add_pool_btn.pack(side="left", padx=(0, 5))

        self.remove_pool_btn = ctk.CTkButton(
            pool_btn_frame,
            text=self.tr("button_remove"),
            command=self._remove_pool_skill_embedded,
            fg_color="#8E24AA",
            hover_color="#AB47BC",
            width=100,
        )
        self.remove_pool_btn.pack(side="left", padx=(0, 5))

        self.save_skills_btn = ctk.CTkButton(
            pool_btn_frame,
            text=self.tr("button_save"),
            command=self._save_skill_config,
            fg_color="#2196F3",
            hover_color="#42A5F5",
            width=100,
        )
        self.save_skills_btn.pack(side="right")

        # Load initial data
        self._skill_cooldowns_data = {}
        self._skill_pool_data = []
        self._selected_pool_skill = None
        self._skill_cd_entries = {}
        self._load_skill_data()
        self._refresh_skill_cooldowns_display()
        self._refresh_skill_pool_display()

        return page

    def _load_skill_data(self):
        """Load skill data from config."""
        if self._config:
            self._skill_cooldowns_data = dict(
                getattr(self._config, "SKILL_COOLDOWNS", {})
            )
            self._skill_pool_data = list(getattr(self._config, "SINGLE_SKILL_POOL", []))

    def _refresh_skill_cooldowns_display(self):
        """Refresh the skill cooldowns display."""
        for widget in self.skill_cd_scroll.winfo_children():
            widget.destroy()

        self._skill_cd_entries = {}
        for skill, cooldown in sorted(self._skill_cooldowns_data.items()):
            frame = ctk.CTkFrame(self.skill_cd_scroll)
            frame.pack(fill="x", padx=2, pady=2)

            # Skill key label
            skill_lbl = ctk.CTkLabel(
                frame, text=skill.upper(), font=ctk.CTkFont(weight="bold"), width=60
            )
            skill_lbl.pack(side="left", padx=10, pady=5)

            # Cooldown entry
            cd_entry = ctk.CTkEntry(frame, width=80)
            cd_entry.pack(side="right", padx=10, pady=5)
            cd_entry.insert(0, str(cooldown))
            self._skill_cd_entries[skill] = cd_entry

            ctk.CTkLabel(frame, text="sec").pack(side="right", pady=5)

    def _refresh_skill_pool_display(self):
        """Refresh the skill pool display."""
        for widget in self.skill_pool_scroll.winfo_children():
            widget.destroy()

        self._pool_frames = []
        for skill in self._skill_pool_data:
            frame = ctk.CTkFrame(self.skill_pool_scroll, cursor="hand2")
            frame.pack(fill="x", padx=2, pady=2)
            frame.bind(
                "<Button-1>", lambda e, s=skill: self._select_pool_skill_embedded(s)
            )

            lbl = ctk.CTkLabel(
                frame, text=skill.upper(), font=ctk.CTkFont(weight="bold")
            )
            lbl.pack(side="left", padx=10, pady=5)
            lbl.bind(
                "<Button-1>", lambda e, s=skill: self._select_pool_skill_embedded(s)
            )

            self._pool_frames.append((skill, frame))

    def _select_pool_skill_embedded(self, skill: str):
        """Select a pool skill for removal."""
        self._selected_pool_skill = skill
        for s, frame in self._pool_frames:
            if s == skill:
                frame.configure(fg_color=("#5E35B1", "#5E35B1"))
            else:
                frame.configure(fg_color=("gray86", "gray17"))

    def _add_skill_cooldown(self):
        """Add a new skill cooldown via key capture."""
        dialog = KeyCaptureDialog(self, self.current_language)
        self.wait_window(dialog)
        if dialog.captured_key:
            key = dialog.captured_key.lower()
            if key and key not in self._skill_cooldowns_data:
                self._skill_cooldowns_data[key] = 10.0
                self._refresh_skill_cooldowns_display()

    def _remove_skill_cooldown(self):
        """Remove the last skill cooldown entry."""
        if self._skill_cooldowns_data:
            keys = list(self._skill_cooldowns_data.keys())
            if keys:
                del self._skill_cooldowns_data[keys[-1]]
                self._refresh_skill_cooldowns_display()

    def _add_pool_skill_embedded(self):
        """Add a skill to the pool via key capture."""
        dialog = KeyCaptureDialog(self, self.current_language)
        self.wait_window(dialog)
        if dialog.captured_key:
            key = dialog.captured_key.lower()
            if key and key not in self._skill_pool_data:
                self._skill_pool_data.append(key)
                self._refresh_skill_pool_display()

    def _remove_pool_skill_embedded(self):
        """Remove the selected skill from pool."""
        if (
            self._selected_pool_skill
            and self._selected_pool_skill in self._skill_pool_data
        ):
            self._skill_pool_data.remove(self._selected_pool_skill)
            self._selected_pool_skill = None
            self._refresh_skill_pool_display()

    def _save_skill_config(self):
        """Save all skill configuration."""
        # Update cooldowns from entries
        for skill, entry in self._skill_cd_entries.items():
            try:
                self._skill_cooldowns_data[skill] = float(entry.get())
            except ValueError:
                pass

        # Get GCD
        try:
            gcd = float(self.skill_gcd_entry.get())
        except ValueError:
            gcd = 1.5

        # Save to config
        if self._config:
            self._config.update_config(
                {
                    "SKILL_COOLDOWNS": self._skill_cooldowns_data,
                    "SINGLE_SKILL_POOL": self._skill_pool_data,
                    "SINGLE_SKILL_GLOBAL_COOLDOWN": gcd,
                }
            )
            self.log(self.tr("msg_skill_saved"))

    def _set_force_skill_combo(self):
        """Set the force skill combo to match config value."""
        if not self._config:
            return
        mode = getattr(self._config, "FORCE_SKILL_BEFORE_STANDARD_MODE", "ready_only")
        if mode == "ready_only":
            self.force_skill_combo.set(self.tr("force_mode_ready_only"))
        elif mode == "always":
            self.force_skill_combo.set(self.tr("force_mode_always"))
        else:
            self.force_skill_combo.set(self.tr("force_mode_disabled"))

    def _on_force_skill_change(self, choice):
        """Handle force skill mode change."""
        if self.tr("force_mode_ready_only") in choice:
            mode = "ready_only"
        elif self.tr("force_mode_always") in choice:
            mode = "always"
        else:
            mode = "disabled"

        if self._config:
            self._config.update_config({"FORCE_SKILL_BEFORE_STANDARD_MODE": mode})

    def _create_combos_page(self) -> ctk.CTkFrame:
        """Create the combos page with full editor."""
        page = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_columnconfigure(1, weight=2)
        page.grid_rowconfigure(1, weight=1)

        title = ctk.CTkLabel(
            page, text=self.tr("nav_combos"), font=ctk.CTkFont(size=24, weight="bold")
        )
        title.grid(row=0, column=0, columnspan=2, pady=(0, 20), sticky="w")

        # Left side - combo list
        left_frame = ctk.CTkFrame(page)
        left_frame.grid(row=1, column=0, padx=(0, 10), sticky="nswe")
        left_frame.grid_rowconfigure(1, weight=1)
        left_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            left_frame,
            text=self.tr("combos_table_header_combo"),
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, padx=10, pady=10, sticky="w")

        self.combo_listbox = ctk.CTkScrollableFrame(left_frame)
        self.combo_listbox.grid(row=1, column=0, padx=10, pady=10, sticky="nswe")

        # Combo list buttons
        combo_btn_frame = ctk.CTkFrame(left_frame, fg_color="transparent")
        combo_btn_frame.grid(row=2, column=0, padx=10, pady=10, sticky="we")

        self.new_combo_btn = ctk.CTkButton(
            combo_btn_frame,
            text=self.tr("button_new_combo"),
            command=self._new_combo,
            fg_color="#4CAF50",
            hover_color="#66BB6A",
        )
        self.new_combo_btn.pack(side="left", padx=(0, 5))

        self.delete_combo_btn = ctk.CTkButton(
            combo_btn_frame,
            text=self.tr("button_delete_combo"),
            command=self._delete_combo,
            fg_color="#f44336",
            hover_color="#EF5350",
        )
        self.delete_combo_btn.pack(side="left")

        # Right side - combo editor
        right_frame = ctk.CTkFrame(page)
        right_frame.grid(row=1, column=1, sticky="nswe")
        right_frame.grid_columnconfigure(1, weight=1)
        right_frame.grid_rowconfigure(5, weight=1)

        row = 0
        ctk.CTkLabel(right_frame, text=self.tr("label_combo_name")).grid(
            row=row, column=0, padx=20, pady=10, sticky="w"
        )
        self.combo_name_entry = ctk.CTkEntry(right_frame, width=300)
        self.combo_name_entry.grid(row=row, column=1, padx=20, pady=10, sticky="we")
        row += 1

        ctk.CTkLabel(right_frame, text=self.tr("label_combo_cooldown")).grid(
            row=row, column=0, padx=20, pady=10, sticky="w"
        )
        self.combo_cooldown_entry = ctk.CTkEntry(right_frame, width=100)
        self.combo_cooldown_entry.grid(row=row, column=1, padx=20, pady=10, sticky="w")
        row += 1

        ctk.CTkLabel(right_frame, text=self.tr("label_combo_delay")).grid(
            row=row, column=0, padx=20, pady=10, sticky="w"
        )
        self.combo_delay_entry = ctk.CTkEntry(right_frame, width=100)
        self.combo_delay_entry.grid(row=row, column=1, padx=20, pady=10, sticky="w")
        row += 1

        self.combo_enabled_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            right_frame,
            text=self.tr("checkbox_combo_enabled"),
            variable=self.combo_enabled_var,
        ).grid(row=row, column=0, columnspan=2, padx=20, pady=10, sticky="w")
        row += 1

        # Skill list label
        ctk.CTkLabel(right_frame, text=self.tr("label_combo_skills")).grid(
            row=row, column=0, padx=20, pady=10, sticky="nw"
        )

        # Skill list frame with scrollable list
        skills_container = ctk.CTkFrame(right_frame)
        skills_container.grid(row=row, column=1, padx=20, pady=10, sticky="nswe")
        skills_container.grid_columnconfigure(0, weight=1)
        skills_container.grid_rowconfigure(0, weight=1)

        self.combo_skills_scroll = ctk.CTkScrollableFrame(skills_container, height=120)
        self.combo_skills_scroll.grid(row=0, column=0, sticky="nswe")

        # Skill buttons for combo
        combo_skill_btns = ctk.CTkFrame(skills_container, fg_color="transparent")
        combo_skill_btns.grid(row=1, column=0, pady=5, sticky="we")

        self.add_combo_skill_btn = ctk.CTkButton(
            combo_skill_btns,
            text=self.tr("button_capture_key"),
            command=self._add_combo_skill,
            fg_color="#5E35B1",
            hover_color="#7E57C2",
            width=120,
        )
        self.add_combo_skill_btn.pack(side="left", padx=(0, 5))

        self.remove_combo_skill_btn = ctk.CTkButton(
            combo_skill_btns,
            text=self.tr("button_remove"),
            command=self._remove_combo_skill,
            fg_color="#f44336",
            hover_color="#EF5350",
            width=100,
        )
        self.remove_combo_skill_btn.pack(side="left")

        row += 1

        # Save button
        btn_row = ctk.CTkFrame(right_frame, fg_color="transparent")
        btn_row.grid(row=row, column=0, columnspan=2, padx=20, pady=20, sticky="e")

        self.save_combo_btn = ctk.CTkButton(
            btn_row,
            text=self.tr("button_save"),
            command=self._save_combo,
            fg_color="#4CAF50",
            hover_color="#66BB6A",
        )
        self.save_combo_btn.pack(side="right")

        # Track selected combo index and skills
        self._selected_combo_idx = -1
        self._combo_skills_list = []
        self._selected_combo_skill = None

        self._refresh_combo_list()

        return page

    def _refresh_combo_skills_display(self):
        """Refresh the combo skills display in the editor."""
        for widget in self.combo_skills_scroll.winfo_children():
            widget.destroy()

        self._combo_skill_frames = []
        for i, skill in enumerate(self._combo_skills_list):
            frame = ctk.CTkFrame(self.combo_skills_scroll, cursor="hand2")
            frame.pack(fill="x", padx=2, pady=2)
            frame.bind("<Button-1>", lambda e, idx=i: self._select_combo_skill(idx))

            # Order number
            num_lbl = ctk.CTkLabel(frame, text=f"{i + 1}.", width=30)
            num_lbl.pack(side="left", padx=5, pady=5)
            num_lbl.bind("<Button-1>", lambda e, idx=i: self._select_combo_skill(idx))

            # Skill key
            skill_lbl = ctk.CTkLabel(
                frame, text=skill.upper(), font=ctk.CTkFont(weight="bold")
            )
            skill_lbl.pack(side="left", padx=5, pady=5)
            skill_lbl.bind("<Button-1>", lambda e, idx=i: self._select_combo_skill(idx))

            self._combo_skill_frames.append((i, frame))

    def _select_combo_skill(self, idx: int):
        """Select a combo skill for removal."""
        self._selected_combo_skill = idx
        for i, frame in self._combo_skill_frames:
            if i == idx:
                frame.configure(fg_color=("#5E35B1", "#5E35B1"))
            else:
                frame.configure(fg_color=("gray86", "gray17"))

    def _add_combo_skill(self):
        """Add a skill to the current combo via key capture."""
        dialog = KeyCaptureDialog(self, self.current_language)
        self.wait_window(dialog)
        if dialog.captured_key:
            key = dialog.captured_key.lower()
            self._combo_skills_list.append(key)
            self._refresh_combo_skills_display()

    def _remove_combo_skill(self):
        """Remove the selected skill from the combo."""
        if (
            self._selected_combo_skill is not None
            and 0 <= self._selected_combo_skill < len(self._combo_skills_list)
        ):
            del self._combo_skills_list[self._selected_combo_skill]
            self._selected_combo_skill = None
            self._refresh_combo_skills_display()

    def _refresh_combo_list(self):
        """Refresh the combo list display."""
        # Clear existing
        for widget in self.combo_listbox.winfo_children():
            widget.destroy()

        if not self._config:
            return

        combos = getattr(self._config, "COMBO_SETS", [])
        for i, combo in enumerate(combos):
            name = combo.get("name", f"Combo {i + 1}")
            enabled = combo.get("enabled", True)
            skills = combo.get("skills", [])

            frame = ctk.CTkFrame(self.combo_listbox, cursor="hand2")
            frame.pack(fill="x", padx=2, pady=2)
            frame.bind("<Button-1>", lambda e, idx=i: self._select_combo(idx))

            status = "✓" if enabled else "✗"
            status_color = "green" if enabled else "red"

            name_lbl = ctk.CTkLabel(
                frame, text=f"{status} {name}", font=ctk.CTkFont(weight="bold")
            )
            name_lbl.pack(side="left", padx=10, pady=8)
            name_lbl.bind("<Button-1>", lambda e, idx=i: self._select_combo(idx))

            skills_lbl = ctk.CTkLabel(
                frame, text=f"({len(skills)} skills)", text_color="gray"
            )
            skills_lbl.pack(side="right", padx=10, pady=8)
            skills_lbl.bind("<Button-1>", lambda e, idx=i: self._select_combo(idx))

    def _select_combo(self, idx: int):
        """Select a combo for editing."""
        self._selected_combo_idx = idx

        if not self._config:
            return

        combos = getattr(self._config, "COMBO_SETS", [])
        if 0 <= idx < len(combos):
            combo = combos[idx]

            self.combo_name_entry.delete(0, "end")
            self.combo_name_entry.insert(0, combo.get("name", ""))

            self.combo_cooldown_entry.delete(0, "end")
            self.combo_cooldown_entry.insert(0, str(combo.get("cooldown", 60.0)))

            self.combo_delay_entry.delete(0, "end")
            self.combo_delay_entry.insert(
                0, str(combo.get("delay_between_skills", 0.5))
            )

            self.combo_enabled_var.set(combo.get("enabled", True))

            # Load skills into the list (not text)
            self._combo_skills_list = list(combo.get("skills", []))
            self._selected_combo_skill = None
            self._refresh_combo_skills_display()

    def _new_combo(self):
        """Create a new combo."""
        if not self._config:
            return

        combos = list(getattr(self._config, "COMBO_SETS", []))
        new_combo = {
            "name": f"New Combo {len(combos) + 1}",
            "skills": ["1", "2", "3"],
            "cooldown": 60.0,
            "delay_between_skills": 0.5,
            "enabled": True,
        }
        combos.append(new_combo)
        self._config.update_config({"COMBO_SETS": combos})
        self._refresh_combo_list()
        self._select_combo(len(combos) - 1)

    def _delete_combo(self):
        """Delete the selected combo."""
        if self._selected_combo_idx < 0 or not self._config:
            return

        combos = list(getattr(self._config, "COMBO_SETS", []))
        if 0 <= self._selected_combo_idx < len(combos):
            del combos[self._selected_combo_idx]
            self._config.update_config({"COMBO_SETS": combos})
            self._selected_combo_idx = -1
            self._refresh_combo_list()
            self.log(self.tr("msg_combo_deleted"))

    def _save_combo(self):
        """Save the current combo."""
        if self._selected_combo_idx < 0 or not self._config:
            return

        combos = list(getattr(self._config, "COMBO_SETS", []))
        if 0 <= self._selected_combo_idx < len(combos):
            combo = combos[self._selected_combo_idx]
            combo["name"] = self.combo_name_entry.get()

            try:
                combo["cooldown"] = float(self.combo_cooldown_entry.get())
            except ValueError:
                combo["cooldown"] = 60.0

            try:
                combo["delay_between_skills"] = float(self.combo_delay_entry.get())
            except ValueError:
                combo["delay_between_skills"] = 0.5

            combo["enabled"] = self.combo_enabled_var.get()

            # Use the skills list directly (captured via key capture)
            combo["skills"] = list(self._combo_skills_list)

            self._config.update_config({"COMBO_SETS": combos})
            self._refresh_combo_list()
            self.log(self.tr("msg_combo_saved"))

    def _create_cooldowns_page(self) -> ctk.CTkFrame:
        """Create the cooldowns page with live tracking."""
        page = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_columnconfigure(1, weight=1)
        page.grid_rowconfigure(1, weight=1)

        title = ctk.CTkLabel(
            page,
            text=self.tr("nav_cooldowns"),
            font=ctk.CTkFont(size=24, weight="bold"),
        )
        title.grid(row=0, column=0, columnspan=2, pady=(0, 20), sticky="w")

        # Skills cooldowns
        skills_frame = ctk.CTkFrame(page)
        skills_frame.grid(row=1, column=0, padx=(0, 10), sticky="nswe")
        skills_frame.grid_rowconfigure(1, weight=1)
        skills_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            skills_frame,
            text=self.tr("skills_table_header_skill"),
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, padx=10, pady=10, sticky="w")

        self.skills_cooldown_frame = ctk.CTkScrollableFrame(skills_frame)
        self.skills_cooldown_frame.grid(
            row=1, column=0, padx=10, pady=10, sticky="nswe"
        )

        # Combos cooldowns
        combos_frame = ctk.CTkFrame(page)
        combos_frame.grid(row=1, column=1, sticky="nswe")
        combos_frame.grid_rowconfigure(1, weight=1)
        combos_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            combos_frame,
            text=self.tr("combos_table_header_combo"),
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, padx=10, pady=10, sticky="w")

        self.combos_cooldown_frame = ctk.CTkScrollableFrame(combos_frame)
        self.combos_cooldown_frame.grid(
            row=1, column=0, padx=10, pady=10, sticky="nswe"
        )

        # Start cooldown update timer
        self._cooldown_update_running = True
        self._start_cooldown_timer()

        return page

    def _start_cooldown_timer(self):
        """Start the cooldown update timer."""

        def update_cooldowns():
            if not self._cooldown_update_running:
                return
            try:
                self._refresh_cooldown_display()
            except Exception as e:
                logger.error(f"Cooldown update error: {e}")
            # Schedule next update
            self.after(500, update_cooldowns)

        self.after(500, update_cooldowns)

    def _refresh_cooldown_display(self):
        """Refresh the cooldown displays."""
        # Clear existing widgets
        for widget in self.skills_cooldown_frame.winfo_children():
            widget.destroy()
        for widget in self.combos_cooldown_frame.winfo_children():
            widget.destroy()

        # Get skill combo manager from controller if running
        scm = None
        if self._controller and hasattr(self._controller, "action_planner"):
            ap = self._controller.action_planner
            if hasattr(ap, "skill_combo_manager"):
                scm = ap.skill_combo_manager

        # Skills
        if self._config:
            skills = getattr(self._config, "SKILL_COOLDOWNS", {})
            for skill, cooldown in skills.items():
                frame = ctk.CTkFrame(self.skills_cooldown_frame)
                frame.pack(fill="x", padx=2, pady=2)

                ctk.CTkLabel(
                    frame, text=skill.upper(), font=ctk.CTkFont(weight="bold"), width=60
                ).pack(side="left", padx=10, pady=5)

                # Check if skill is on cooldown
                remaining = 0.0
                if scm:
                    remaining = scm.get_skill_cooldown_remaining(skill)

                if remaining > 0:
                    status_text = self.tr("status_cooldown", seconds=f"{remaining:.1f}")
                    status_color = "orange"
                else:
                    status_text = self.tr("status_ready")
                    status_color = "green"

                ctk.CTkLabel(frame, text=status_text, text_color=status_color).pack(
                    side="right", padx=10, pady=5
                )

        # Combos
        if self._config:
            combos = getattr(self._config, "COMBO_SETS", [])
            for i, combo in enumerate(combos):
                if not combo.get("enabled", True):
                    continue

                frame = ctk.CTkFrame(self.combos_cooldown_frame)
                frame.pack(fill="x", padx=2, pady=2)

                name = combo.get("name", f"Combo {i + 1}")
                ctk.CTkLabel(frame, text=name, font=ctk.CTkFont(weight="bold")).pack(
                    side="left", padx=10, pady=5
                )

                # Check if combo is on cooldown
                remaining = 0.0
                if scm:
                    remaining = scm.get_combo_cooldown_remaining(combo)

                if remaining > 0:
                    status_text = self.tr("status_cooldown", seconds=f"{remaining:.1f}")
                    status_color = "orange"
                else:
                    status_text = self.tr("status_ready")
                    status_color = "green"

                ctk.CTkLabel(frame, text=status_text, text_color=status_color).pack(
                    side="right", padx=10, pady=5
                )

    def _create_queue_page(self) -> ctk.CTkFrame:
        """Create the combat queue monitoring page."""
        page = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_columnconfigure(1, weight=1)
        page.grid_rowconfigure(2, weight=1)

        # Title
        title = ctk.CTkLabel(
            page, text=self.tr("queue_title"), font=ctk.CTkFont(size=24, weight="bold")
        )
        title.grid(row=0, column=0, columnspan=2, pady=(0, 5), sticky="w")

        subtitle = ctk.CTkLabel(
            page,
            text=self.tr("queue_subtitle"),
            font=ctk.CTkFont(size=12),
            text_color="gray",
        )
        subtitle.grid(row=1, column=0, columnspan=2, pady=(0, 20), sticky="w")

        # Statistics frame
        stats_frame = ctk.CTkFrame(page)
        stats_frame.grid(row=2, column=0, padx=(0, 10), pady=(0, 10), sticky="new")
        stats_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            stats_frame,
            text=self.tr("queue_stats"),
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, padx=10, pady=10, sticky="w")

        self.queue_stat_labels = {}
        stat_items = [
            ("total_queued", self.tr("queue_total_queued")),
            ("total_executed", self.tr("queue_total_executed")),
            ("total_blocked", self.tr("queue_total_blocked")),
        ]
        for i, (key, label_text) in enumerate(stat_items, start=1):
            ctk.CTkLabel(stats_frame, text=label_text).grid(
                row=i, column=0, padx=10, pady=5, sticky="w"
            )
            value_label = ctk.CTkLabel(
                stats_frame, text="0", font=ctk.CTkFont(weight="bold")
            )
            value_label.grid(row=i, column=1, padx=10, pady=5, sticky="e")
            self.queue_stat_labels[key] = value_label

        # Clear queue button
        clear_btn = ctk.CTkButton(
            stats_frame,
            text=self.tr("queue_clear"),
            command=self._clear_combat_queue,
            fg_color="red",
            hover_color="darkred",
            width=150,
        )
        clear_btn.grid(
            row=len(stat_items) + 1, column=0, columnspan=2, padx=10, pady=15
        )

        # Recent actions (history) frame
        history_frame = ctk.CTkFrame(page)
        history_frame.grid(row=2, column=1, pady=(0, 10), sticky="nswe")
        history_frame.grid_columnconfigure(0, weight=1)
        history_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            history_frame,
            text=self.tr("queue_history"),
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=0, column=0, padx=10, pady=10, sticky="w")

        self.queue_history_frame = ctk.CTkScrollableFrame(history_frame)
        self.queue_history_frame.grid(
            row=1, column=0, padx=10, pady=(0, 10), sticky="nswe"
        )

        # Track last state to avoid unnecessary redraws
        self._queue_last_history_count = 0
        self._queue_last_stats = {}
        self._queue_history_widgets = []

        # Start queue update timer
        self._queue_update_running = True
        self._start_queue_timer()

        return page

    def _start_queue_timer(self):
        """Start the queue status update timer."""

        def update_queue():
            if not self._queue_update_running:
                return
            try:
                self._refresh_queue_display()
            except Exception as e:
                logger.debug(f"Queue update error: {e}")
            self.after(
                500, update_queue
            )  # Update every 500ms (was 200ms - reduces flicker)

        self.after(500, update_queue)

    def _refresh_queue_display(self):
        """Refresh the combat queue display with minimal redraws to prevent flickering."""
        try:
            from combat_queue import get_combat_queue

            queue = get_combat_queue()
            state = queue.get_queue_state()
        except Exception:
            return

        # Update statistics only if changed
        stats = state.get("stats", {})
        for key, label in self.queue_stat_labels.items():
            value = stats.get(key, 0)
            if self._queue_last_stats.get(key) != value:
                label.configure(text=str(value))
                self._queue_last_stats[key] = value

        # Only rebuild history if the count changed
        history = state.get("history", [])
        history_count = len(history)

        if history_count == self._queue_last_history_count and history_count > 0:
            # Just update ages on existing widgets if possible
            try:
                for i, widget_info in enumerate(self._queue_history_widgets):
                    if i < len(history):
                        age_ms = history[i].get("age_ms", 0)
                        age_text = (
                            f"{age_ms}ms" if age_ms < 1000 else f"{age_ms / 1000:.1f}s"
                        )
                        widget_info.get("age_label", None)
                        if widget_info.get("age_label"):
                            widget_info["age_label"].configure(text=age_text)
                return
            except Exception:
                pass  # Fall through to full rebuild

        self._queue_last_history_count = history_count

        # Full rebuild needed
        for widget in self.queue_history_frame.winfo_children():
            widget.destroy()
        self._queue_history_widgets = []

        if not history:
            ctk.CTkLabel(
                self.queue_history_frame, text=self.tr("queue_empty"), text_color="gray"
            ).pack(pady=20)
            return

        for action in history[:12]:  # Show last 12 actions (reduced from 15)
            frame = ctk.CTkFrame(self.queue_history_frame)
            frame.pack(fill="x", padx=2, pady=1)

            widget_info = {"frame": frame}

            # Key and type
            key_text = action.get("key", "?").upper()
            type_text = action.get("type", "unknown")[:6]
            ctk.CTkLabel(
                frame, text=key_text, font=ctk.CTkFont(weight="bold"), width=40
            ).pack(side="left", padx=3, pady=2)
            ctk.CTkLabel(
                frame, text=f"[{type_text}]", text_color="gray", width=60
            ).pack(side="left", padx=3, pady=2)

            # Status with color
            status = action.get("status", "unknown")
            status_colors = {
                "completed": "green",
                "blocked": "red",
                "pending": "orange",
                "executing": "yellow",
            }
            status_color = status_colors.get(status, "gray")
            ctk.CTkLabel(
                frame, text=status[:4].upper(), text_color=status_color, width=50
            ).pack(side="left", padx=3, pady=2)

            # Age
            age_ms = action.get("age_ms", 0)
            age_text = f"{age_ms}ms" if age_ms < 1000 else f"{age_ms / 1000:.1f}s"
            age_label = ctk.CTkLabel(frame, text=age_text, text_color="gray", width=50)
            age_label.pack(side="right", padx=3, pady=2)
            widget_info["age_label"] = age_label

            self._queue_history_widgets.append(widget_info)

    def _clear_combat_queue(self):
        """Clear the combat queue."""
        try:
            from combat_queue import get_combat_queue

            queue = get_combat_queue()
            queue.clear_queue()
            self.log("Combat queue cleared")
        except Exception as e:
            self.log(f"Failed to clear queue: {e}")

    def _create_logs_page(self) -> ctk.CTkFrame:
        """Create the logs page."""
        page = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        title = ctk.CTkLabel(
            page, text=self.tr("nav_logs"), font=ctk.CTkFont(size=24, weight="bold")
        )
        title.grid(row=0, column=0, pady=(0, 20), sticky="w")

        self.log_text = ctk.CTkTextbox(
            page, font=ctk.CTkFont(family="Consolas", size=12)
        )
        self.log_text.grid(row=1, column=0, sticky="nswe")

        return page

    def _create_settings_page(self) -> ctk.CTkFrame:
        """Create the settings page."""
        page = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(
            page, text=self.tr("nav_settings"), font=ctk.CTkFont(size=24, weight="bold")
        )
        title.grid(row=0, column=0, pady=(0, 20), sticky="w")

        # Language selection
        lang_frame = ctk.CTkFrame(page)
        lang_frame.grid(row=1, column=0, pady=10, sticky="we")

        ctk.CTkLabel(lang_frame, text=self.tr("settings_language_label")).grid(
            row=0, column=0, padx=20, pady=10
        )
        self.language_combo = ctk.CTkComboBox(
            lang_frame,
            values=[self.tr("language_english"), self.tr("language_korean")],
            command=self._on_language_change,
        )
        self.language_combo.grid(row=0, column=1, padx=20, pady=10)
        self.language_combo.set(
            self.tr("language_english")
            if self.current_language == "en"
            else self.tr("language_korean")
        )

        # Combat settings
        combat_frame = ctk.CTkFrame(page)
        combat_frame.grid(row=2, column=0, pady=10, sticky="we")
        combat_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            combat_frame,
            text=self.tr("settings_combat_group"),
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, columnspan=3, padx=20, pady=(10, 5), sticky="w")

        # Random Combat Chance slider
        ctk.CTkLabel(combat_frame, text=self.tr("label_random_combat_chance")).grid(
            row=1, column=0, padx=20, pady=10, sticky="w"
        )

        self.random_combat_slider = ctk.CTkSlider(
            combat_frame,
            from_=0,
            to=100,
            number_of_steps=20,
            command=self._on_random_combat_change,
            width=200,
        )
        self.random_combat_slider.grid(row=1, column=1, padx=10, pady=10, sticky="w")

        # Load current value from config
        try:
            import skill_combo_config as scc

            current_chance = getattr(scc, "RANDOM_COMBAT_CHANCE", 0.50) * 100
        except Exception:
            current_chance = 50
        self.random_combat_slider.set(current_chance)

        self.random_combat_label = ctk.CTkLabel(
            combat_frame,
            text=self.tr("label_random_combat_percent", percent=int(current_chance)),
        )
        self.random_combat_label.grid(row=1, column=2, padx=10, pady=10, sticky="w")

        return page

    def _on_random_combat_change(self, value):
        """Handle random combat chance slider change."""
        percent = int(value)
        self.random_combat_label.configure(
            text=self.tr("label_random_combat_percent", percent=percent)
        )

        # Save to config
        try:
            import skill_combo_config as scc

            scc.update_config({"RANDOM_COMBAT_CHANCE": percent / 100.0})
            logger.info(f"Random combat chance set to {percent}%")
        except Exception as e:
            logger.error(f"Failed to save random combat chance: {e}")

    def _refresh_windows(self):
        """Refresh the window list."""
        wins = list_windows()
        values = [f"{title} (hwnd={hwnd})" for title, hwnd in wins]
        self.window_combo.configure(values=values)
        if values:
            self.window_combo.set(values[0])
        self._windows = wins

    def _start_overlay_system(self):
        """Start the Qt overlay system in a background thread with proper event processing."""

        def run_qt():
            try:
                self._qt_app = QtWidgets.QApplication.instance()
                if not self._qt_app:
                    self._qt_app = QtWidgets.QApplication([])

                self._overlay = OverlayWindow()
                self._overlay.set_automation_enabled(self._automation_enabled)

                # Process Qt events in a loop to keep the overlay responsive
                import time

                while True:
                    try:
                        self._qt_app.processEvents()
                        time.sleep(0.016)  # ~60 FPS event processing
                    except Exception:
                        break
            except Exception as e:
                logger.error(f"Qt overlay system error: {e}")

        self._qt_thread = threading.Thread(target=run_qt, daemon=True)
        self._qt_thread.start()

        # Give Qt time to initialize
        time.sleep(0.2)

    def _start_detection(self):
        """Start the detection system."""
        sel = self.window_combo.get()
        if not sel or not hasattr(self, "_windows"):
            self.log(self.tr("log_no_window_selected"))
            return

        # Find hwnd
        hwnd = None
        for title, h in self._windows:
            if f"(hwnd={h})" in sel:
                hwnd = h
                break

        if not hwnd:
            self.log(self.tr("log_no_window_selected"))
            return

        rect = get_window_rect(hwnd)
        if not rect:
            self.log(self.tr("log_unable_get_window_rect"))
            return

        try:
            ic.set_active_hwnd(hwnd)
        except Exception:
            pass

        # Show overlay
        left, top, w, h = rect
        if self._overlay:
            try:
                self._overlay.setGeometry(left, top, w, h)
                self._overlay.show()
                self._overlay.raise_()  # Bring to front
                self._overlay.activateWindow()
                self._overlay.make_clickthrough()
                self.log(f"Overlay positioned at ({left}, {top}) size ({w}x{h})")
            except Exception as e:
                self.log(f"Overlay setup error: {e}")
        else:
            self.log("Warning: Overlay not initialized")

        # Create controller
        self._controller = DetectionController(
            hwnd=hwnd,
            overlay_update=self._overlay.update_overlay
            if self._overlay
            else lambda *a: None,
            log_fn=self.log,
            fps=None,
        )
        self._controller.start()

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal", fg_color="orange")

        self.log(self.tr("log_started_detection"))

    def _stop_detection(self):
        """Stop the detection system."""
        if self._controller:
            self._controller.stop()
            self._controller = None

        if self._overlay:
            self._overlay.hide()

        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled", fg_color="gray")

        self.log(self.tr("log_stopped"))

    def _emergency_stop(self):
        """Emergency stop - disable everything immediately."""
        self._automation_enabled = False
        if self._overlay:
            self._overlay.set_automation_enabled(False)
        if self._controller:
            try:
                self._controller.action_planner.set_enabled(False)
            except Exception:
                pass
        self.log(self.tr("log_emergency_stop"))

    def _on_simulate_toggle(self):
        """Handle simulate mode toggle."""
        ic.INPUT_DRY_RUN = self.simulate_var.get()
        state = self.tr("state_on") if ic.INPUT_DRY_RUN else self.tr("state_off")
        self.log(self.tr("log_simulate_mode", state=state))

    def _update_skill_config(self, *args):
        """Update skill configuration from UI values."""
        if not self._config:
            return

        try:
            self._config.STEALTH_ATTACK_MODE_ENABLED = self.stealth_var.get()
            self._config.COMBAT_USE_SKILLS = self.combat_skills_var.get()
            self._config.ATTACK_MODE_WEIGHTS["standard_attack"] = (
                self.standard_weight.get()
            )
            self._config.ATTACK_MODE_WEIGHTS["single_skill"] = self.single_weight.get()
            self._config.ATTACK_MODE_WEIGHTS["combo_set"] = self.combo_weight.get()

            try:
                self._config.OUTNUMBERED_THRESHOLD = int(self.outnumbered_spin.get())
            except ValueError:
                pass

            try:
                self._config.DEFENSIVE_COOLDOWN_SEC = float(self.defensive_cd.get())
            except ValueError:
                pass

            # Persist
            self._config.update_config(
                {
                    "STEALTH_ATTACK_MODE_ENABLED": self._config.STEALTH_ATTACK_MODE_ENABLED,
                    "COMBAT_USE_SKILLS": self._config.COMBAT_USE_SKILLS,
                    "ATTACK_MODE_WEIGHTS": self._config.ATTACK_MODE_WEIGHTS,
                    "OUTNUMBERED_THRESHOLD": self._config.OUTNUMBERED_THRESHOLD,
                    "DEFENSIVE_COOLDOWN_SEC": self._config.DEFENSIVE_COOLDOWN_SEC,
                }
            )

            self.log(self.tr("log_skill_config_updated"))
        except Exception as e:
            logger.error(f"Failed to update skill config: {e}")

    def _on_language_change(self, choice):
        """Handle language change."""
        if self.tr("language_english") in choice:
            self.current_language = "en"
        else:
            self.current_language = "ko"

        if self._config:
            try:
                self._config.update_config({"LANGUAGE": self.current_language})
            except Exception:
                pass

        # Reload UI would require restart
        messagebox.showinfo(
            "Language", "Please restart the application for full language change."
        )

    def log(self, text: str):
        """Append text to the log view."""
        try:
            self.log_text.insert("end", text + "\n")
            self.log_text.see("end")
        except Exception:
            pass
        logger.info(text)

    def _on_close(self):
        """Handle window close."""
        self._cooldown_update_running = False
        self._stop_detection()
        if self._qt_app:
            self._qt_app.quit()
        self.destroy()


# ============================================================================
# KEY CAPTURE DIALOG
# ============================================================================


class KeyCaptureDialog(ctk.CTkToplevel):
    """Dialog for capturing a single key press for skill binding."""

    # Valid keys for skill binds
    VALID_KEYS = {
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "0",
        "minus",
        "equal",
        "F1",
        "F2",
        "F3",
        "F4",
        "F5",
        "F6",
        "F7",
        "F8",
        "F9",
    }

    # Key mapping from tkinter keysym to our format
    KEY_MAP = {
        "minus": "-",
        "equal": "=",
        "F1": "f1",
        "F2": "f2",
        "F3": "f3",
        "F4": "f4",
        "F5": "f5",
        "F6": "f6",
        "F7": "f7",
        "F8": "f8",
        "F9": "f9",
    }

    def __init__(self, parent, language: str):
        super().__init__(parent)
        self.language = language
        self.captured_key = None

        self.title(translate_text(language, "capture_title"))
        self.geometry("400x220")
        self.resizable(False, False)

        # Make modal
        self.transient(parent)
        self.grab_set()

        # Center on parent
        self.update_idletasks()
        parent_x = parent.winfo_x()
        parent_y = parent.winfo_y()
        parent_w = parent.winfo_width()
        parent_h = parent.winfo_height()
        x = parent_x + (parent_w - 400) // 2
        y = parent_y + (parent_h - 220) // 2
        self.geometry(f"400x220+{x}+{y}")

        self._build_ui()

        # Bind key events
        self.bind("<KeyPress>", self._on_key_press)
        self.focus_set()

    def _build_ui(self):
        # Instruction
        instruction = ctk.CTkLabel(
            self,
            text=translate_text(self.language, "capture_instruction"),
            font=ctk.CTkFont(size=14),
            justify="center",
        )
        instruction.pack(pady=(20, 10))

        # Display captured key
        self.key_display = ctk.CTkLabel(
            self,
            text=translate_text(self.language, "capture_waiting"),
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color="#4CAF50",
        )
        self.key_display.pack(pady=15)

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=20)

        self.confirm_btn = ctk.CTkButton(
            btn_frame,
            text=translate_text(self.language, "capture_confirm"),
            command=self._on_confirm,
            state="disabled",
            fg_color="#4CAF50",
            hover_color="#66BB6A",
            width=120,
        )
        self.confirm_btn.pack(side="left", padx=10)

        self.cancel_btn = ctk.CTkButton(
            btn_frame,
            text=translate_text(self.language, "capture_cancel"),
            command=self._on_cancel,
            fg_color="#f44336",
            hover_color="#EF5350",
            width=120,
        )
        self.cancel_btn.pack(side="left", padx=10)

    def _on_key_press(self, event):
        """Handle key press event."""
        keysym = event.keysym

        # Check if it's a valid key
        if keysym in self.VALID_KEYS or keysym in [
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "0",
        ]:
            # Map to our format
            if keysym in self.KEY_MAP:
                self.captured_key = self.KEY_MAP[keysym]
            else:
                self.captured_key = keysym.lower()

            # Update display
            self.key_display.configure(
                text=self.captured_key.upper()
                if len(self.captured_key) <= 2
                else self.captured_key.upper(),
                text_color="#4CAF50",
            )
            self.confirm_btn.configure(state="normal")
        else:
            # Invalid key
            self.key_display.configure(
                text=translate_text(self.language, "capture_invalid"),
                text_color="#f44336",
            )
            self.confirm_btn.configure(state="disabled")
            self.captured_key = None

    def _on_confirm(self):
        """Confirm the captured key."""
        self.destroy()

    def _on_cancel(self):
        """Cancel key capture."""
        self.captured_key = None
        self.destroy()


def show_prestart_dialog(language: str) -> bool:
    """Show pre-start checklist dialog. Returns True if user confirms."""
    root = ctk.CTk()
    root.title(translate_text(language, "prestart_title"))
    root.geometry("700x500")
    root.resizable(False, False)

    ctk.set_appearance_mode("dark")

    confirmed = [False]

    def on_confirm():
        confirmed[0] = True
        root.destroy()

    def on_cancel():
        root.destroy()

    # Info text
    info = ctk.CTkTextbox(root, font=ctk.CTkFont(size=13), wrap="word")
    info.pack(fill="both", expand=True, padx=20, pady=20)
    info.insert("1.0", translate_text(language, "prestart_info"))
    info.configure(state="disabled")

    # Button
    btn = ctk.CTkButton(
        root,
        text=translate_text(language, "prestart_button"),
        font=ctk.CTkFont(size=14, weight="bold"),
        height=50,
        command=on_confirm,
    )
    btn.pack(pady=20)

    root.protocol("WM_DELETE_WINDOW", on_cancel)
    root.mainloop()

    return confirmed[0]


def check_and_install_interception(language: str) -> bool:
    """Check for Interception driver and attempt installation if missing."""
    import subprocess
    from pathlib import Path

    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    drivers_dir = Path(sysroot) / "System32" / "drivers"
    required = [drivers_dir / "mouse.sys", drivers_dir / "keyboard.sys"]
    missing = [p for p in required if not p.exists()]

    if not missing:
        return True

    installer = (
        Path(__file__).parent
        / "Interception"
        / "command line installer"
        / "install-interception.exe"
    )
    if installer.exists():
        try:
            installer_dir = str(installer.parent)
            installer_name = installer.name

            inner_cmd = (
                f"Set-Location -LiteralPath '{installer_dir}';"
                f" .\\{installer_name};"
                " Read-Host -Prompt 'Press Enter to close';"
                " exit"
            )

            outer_cmd = (
                "Start-Process powershell -Verb runAs -ArgumentList "
                f"'-NoProfile -ExecutionPolicy Bypass -Command \"{inner_cmd}\"' -Wait"
            )

            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    outer_cmd,
                ],
                check=False,
            )
        except Exception as e:
            logger.error(f"Failed to run interception installer: {e}")

    # Re-check
    missing = [p for p in required if not p.exists()]
    if missing:
        messagebox.showwarning(
            translate_text(language, "msg_interception_missing_title"),
            translate_text(language, "msg_interception_missing_body"),
        )
        return False

    return True


def run_app():
    """Main application entry point."""
    logger.info(
        "Interception backend enforced; ensure interception driver is installed"
    )

    # Get language preference
    try:
        import skill_combo_config as scc

        app_language = getattr(scc, "LANGUAGE", "en") or "en"
    except Exception:
        app_language = "en"

    # Show pre-start dialog
    if not show_prestart_dialog(app_language):
        logger.info("User cancelled pre-start checklist; exiting")
        return

    # Check interception
    check_and_install_interception(app_language)

    # Run main app
    app = AIONApp()
    app.mainloop()


if __name__ == "__main__":
    logger.info("Starting AION automation (Interception backend)")

    # Require admin privileges
    try:
        if not is_admin():
            logger.info(
                "Not running as administrator - attempting to relaunch elevated..."
            )
            run_as_admin()
            logger.error("Administrator privileges are REQUIRED to run this program.")
            input("\nPress Enter to exit...")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Admin elevation failed: {e}")
        input("\nPress Enter to exit...")
        sys.exit(1)

    logger.success("✓ Running with administrator privileges")
    run_app()
