import argparse
from datetime import datetime
from dataclasses import dataclass
import difflib
import hashlib
import os
from pathlib import Path
import shutil
import sys
from typing import Optional
import unicodedata
from uuid import uuid4

if sys.platform == "darwin":
    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--disable-gpu --disable-gpu-compositing --disable-logging --log-level=3",
    )

from .tools import WorkspaceTools
from .model_catalog import consume_codex_reset_credit, fetch_codex_rate_limits
from .session_state import WorkspaceStateStore
from .terminal import TerminalSession

try:
    from PySide6.QtCore import QThread, QTimer, Qt, Signal, QObject, QUrl, Slot
    from PySide6.QtGui import QDesktopServices, QFont
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import (
        QApplication,
        QFileDialog,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QLineEdit,
        QMessageBox,
        QHBoxLayout,
        QInputDialog,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QSplitter,
        QStatusBar,
        QTabBar,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise SystemExit("PySide6가 필요합니다. `python -m pip install -e '.[desktop]'` 실행 후 다시 시도하세요.") from exc


TERMINAL_HTML = """<!doctype html>
<html><head>
<meta charset="utf-8">
<link rel="stylesheet" href="css/xterm.css">
<script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js"></script>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<style>html,body,#terminals{height:100%;margin:0;background:#0d141d;overflow:hidden}.terminal{height:100%;padding:14px;box-sizing:border-box}.terminal.hidden{display:none}</style>
</head><body><div id="terminals"></div><script>
new QWebChannel(qt.webChannelTransport, function(channel) {
  const bridge = channel.objects.bridge;
  const terminals = new Map(); let activeKey = '';
  const getTerminal = key => {
    if (terminals.has(key)) return terminals.get(key);
    const host = document.createElement('div'); host.className = 'terminal hidden'; document.getElementById('terminals').appendChild(host);
    const term = new Terminal({cursorBlink:true, convertEol:true, fontFamily:'Menlo, Monaco, Consolas, monospace', fontSize:14, theme:{background:'#0d141d', foreground:'#dce7e5', cursor:'#8ee0bd', selectionBackground:'#29483f'}});
    const fit = new FitAddon.FitAddon(); term.loadAddon(fit); term.open(host);
    term.onData(data => { if (activeKey === key) bridge.write(data); });
    terminals.set(key, {term, fit, host}); return terminals.get(key);
  };
  const activate = key => { activeKey = key; terminals.forEach(item => item.host.className = 'terminal hidden'); const item = getTerminal(key); item.host.className = 'terminal'; item.fit.fit(); bridge.resize(item.term.cols, item.term.rows); item.term.focus(); };
  const resize = () => { const item = terminals.get(activeKey); if (item) { item.fit.fit(); bridge.resize(item.term.cols, item.term.rows); } };
  bridge.activeChanged.connect(activate);
  bridge.output.connect((key, data) => getTerminal(key).term.write(data));
  bridge.ready();
  window.addEventListener('resize', resize);
});
</script></body></html>"""


@dataclass(frozen=True)
class FileBaseline:
    mtime_ns: int
    size: int
    content: Optional[bytes]
    is_binary: bool


@dataclass
class SessionRecord:
    session_id: str
    name: str


class TerminalBridge(QObject):
    output = Signal(str, str)
    activeChanged = Signal(str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.ready_for_output = False
        self.pending_output = []

    def emit_output(self, workspace: str, text: str) -> None:
        if not self.ready_for_output:
            self.pending_output.append((workspace, text))
            return
        self.output.emit(workspace, text)

    def set_active(self, workspace: str) -> None:
        if self.ready_for_output:
            self.activeChanged.emit(workspace)
        else:
            self.window.terminal_active = workspace

    @Slot()
    def ready(self) -> None:
        self.ready_for_output = True
        self.activeChanged.emit(self.window.terminal_active)
        for workspace, text in self.pending_output:
            self.output.emit(workspace, text)
        self.pending_output = []

    @Slot(str)
    def write(self, text: str) -> None:
        self.window._write_terminal(text)

    @Slot(int, int)
    def resize(self, columns: int, rows: int) -> None:
        self.window._resize_terminal(columns, rows)


class FileScanWorker(QThread):
    scanned = Signal(object, object, str)
    failed = Signal(str)

    def __init__(self, workspace: Path):
        super().__init__()
        self.workspace = workspace

    def run(self) -> None:
        try:
            tools = WorkspaceTools(self.workspace)
            files, directories = tools.scan_tree(file_limit=100_000, directory_limit=10_000)
            self.scanned.emit(files, directories, str(self.workspace))
        except Exception as exc:
            self.failed.emit(str(exc))


class FileChangeWorker(QThread):
    scanned = Signal(object, object, str)
    failed = Signal(str)

    def __init__(self, workspace: Path):
        super().__init__()
        self.workspace = workspace

    def run(self) -> None:
        try:
            files, directories = WorkspaceTools(self.workspace).scan_tree(file_limit=100_000, directory_limit=10_000)
            self.scanned.emit(files, directories, str(self.workspace))
        except Exception as exc:
            self.failed.emit(str(exc))


class UsageWorker(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.loaded.emit(fetch_codex_rate_limits())
        except Exception as exc:
            self.failed.emit(str(exc))


class ResetCreditWorker(QThread):
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, credit_id: str):
        super().__init__()
        self.credit_id = credit_id

    def run(self) -> None:
        try:
            self.completed.emit(consume_codex_reset_credit(self.credit_id))
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self, workspace: Path):
        super().__init__()
        self.workspace = workspace.resolve()
        self.workspace_store = Path.home() / ".personal-agent" / "workspaces.json"
        self.workspace_state = WorkspaceStateStore(self.workspace_store)
        self.workspaces, self.workspace = self.workspace_state.load(self.workspace)
        saved_sessions, saved_active_sessions = self.workspace_state.load_sessions()
        self.sessions_by_workspace = self._restore_sessions(saved_sessions)
        self.active_session_ids = {
            str(Path(path).expanduser().resolve()): session_id
            for path, session_id in saved_active_sessions.items()
        }
        self._ensure_session_for_workspace()
        self.tools = WorkspaceTools(self.workspace)
        self.scan_worker = None
        self.scan_workers = []
        self.usage_worker = None
        self.reset_credit_worker = None
        self.reset_credit_id = ""
        self.terminal_sessions = {}
        self.terminal_active = ""
        self.terminal_timer = None
        self.preview_path = None
        self.file_baselines = {}
        self.changed_files = set()
        self.rollback_trash_root = Path.home() / ".personal-agent" / "rollback-trash"
        self.change_timer = None
        self.change_scan_worker = None
        self.all_files = []
        self.all_directories = []
        self.preview_relative = None
        self.setWindowTitle("Personal Agent — Workbench")
        self.resize(1360, 820)
        self.setStyleSheet(self._style())
        self._build_ui()
        self._refresh_files()
        self._start_terminal()
        self.change_timer = QTimer(self)
        self.change_timer.setInterval(2000)
        self.change_timer.timeout.connect(self._check_file_changes)
        self.change_timer.start()
        self._load_usage()

    def _build_ui(self) -> None:
        root = QSplitter()
        root.setChildrenCollapsible(False)
        self.setCentralWidget(root)

        left = QWidget(); left.setObjectName("sidebar"); left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(16, 18, 16, 16); left_layout.setSpacing(10)
        brand = QLabel("PERSONAL AGENT"); brand.setObjectName("brand"); left_layout.addWidget(brand)
        brand_subtitle = QLabel("LOCAL CODING WORKBENCH"); brand_subtitle.setObjectName("brandSubtitle"); left_layout.addWidget(brand_subtitle)
        left_layout.addWidget(self._title("프로젝트"))
        self.workspace_label = QLabel(str(self.workspace))
        self.workspace_label.setObjectName("workspacePath")
        self.workspace_label.setWordWrap(True); left_layout.addWidget(self.workspace_label)
        open_button = QPushButton("＋  작업 공간 추가"); open_button.setObjectName("primaryButton"); open_button.clicked.connect(self._choose_workspace); left_layout.addWidget(open_button)
        self.workspace_list = QListWidget(); self.workspace_list.setObjectName("workspaceList"); self.workspace_list.itemClicked.connect(self._select_workspace); left_layout.addWidget(self.workspace_list, 1)
        remove_workspace = QPushButton("선택 작업 공간 제거"); remove_workspace.setObjectName("secondaryButton"); remove_workspace.clicked.connect(self._remove_workspace); left_layout.addWidget(remove_workspace)
        self._refresh_workspaces()
        left_layout.addWidget(self._title("사용량"))
        self.usage_label = QLabel("사용량 확인 중…"); self.usage_label.setObjectName("infoCard"); self.usage_label.setWordWrap(True); left_layout.addWidget(self.usage_label)
        self.primary_usage_bar = QProgressBar(); self.primary_usage_bar.setObjectName("usageBar"); self.primary_usage_bar.setRange(0, 100); self.primary_usage_bar.setTextVisible(True); self.primary_usage_bar.hide(); left_layout.addWidget(self.primary_usage_bar)
        self.secondary_usage_bar = QProgressBar(); self.secondary_usage_bar.setObjectName("usageBar"); self.secondary_usage_bar.setRange(0, 100); self.secondary_usage_bar.setTextVisible(True); self.secondary_usage_bar.hide(); left_layout.addWidget(self.secondary_usage_bar)
        refresh_usage = QPushButton("사용량 새로고침"); refresh_usage.setObjectName("secondaryButton"); refresh_usage.clicked.connect(self._load_usage); left_layout.addWidget(refresh_usage)
        self.reset_credit_button = QPushButton("사용량 초기화권 사용"); self.reset_credit_button.setObjectName("primaryButton"); self.reset_credit_button.clicked.connect(self._consume_reset_credit); self.reset_credit_button.setEnabled(False); left_layout.addWidget(self.reset_credit_button)

        center = QWidget(); center.setObjectName("centerPanel"); center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(16, 18, 16, 16); center_layout.setSpacing(10)
        center_header = QWidget(); center_header_layout = QHBoxLayout(center_header); center_header_layout.setContentsMargins(0, 0, 0, 0)
        center_header_layout.addWidget(self._title("터미널"))
        self.workspace_tabs = QTabBar(); self.workspace_tabs.setObjectName("workspaceTabs"); self.workspace_tabs.setExpanding(False); self.workspace_tabs.setMovable(False); self.workspace_tabs.currentChanged.connect(self._select_workspace_tab); center_header_layout.addWidget(self.workspace_tabs)
        center_header_layout.addStretch(1)
        self.restart_button = QPushButton("↻"); self.restart_button.setObjectName("iconButton"); self.restart_button.setFixedSize(30, 28); self.restart_button.setToolTip("현재 작업 공간의 Codex 세션을 다시 시작합니다."); self.restart_button.clicked.connect(self._restart_terminal); center_header_layout.addWidget(self.restart_button)
        self.stop_button = QPushButton("■"); self.stop_button.setObjectName("dangerIconButton"); self.stop_button.setFixedSize(30, 28); self.stop_button.setToolTip("현재 작업 공간의 Codex 세션을 중지합니다."); self.stop_button.clicked.connect(self._stop_active_terminal); center_header_layout.addWidget(self.stop_button)
        center_layout.addWidget(center_header)
        session_header = QWidget(); session_header_layout = QHBoxLayout(session_header); session_header_layout.setContentsMargins(0, 0, 0, 0); session_header_layout.setSpacing(6)
        session_header_layout.addWidget(self._title("세션"))
        self.session_tabs = QTabBar(); self.session_tabs.setObjectName("sessionTabs"); self.session_tabs.setExpanding(False); self.session_tabs.setMovable(False); self.session_tabs.currentChanged.connect(self._select_session_tab); session_header_layout.addWidget(self.session_tabs, 1)
        self.new_session_button = QPushButton("＋"); self.new_session_button.setObjectName("iconButton"); self.new_session_button.setFixedSize(30, 28); self.new_session_button.setToolTip("현재 작업 공간에 새 Codex 세션을 만듭니다."); self.new_session_button.clicked.connect(self._new_session); session_header_layout.addWidget(self.new_session_button)
        self.rename_session_button = QPushButton("이름"); self.rename_session_button.setObjectName("secondaryButton"); self.rename_session_button.setFixedSize(48, 28); self.rename_session_button.clicked.connect(self._rename_session); session_header_layout.addWidget(self.rename_session_button)
        self.close_session_button = QPushButton("×"); self.close_session_button.setObjectName("dangerIconButton"); self.close_session_button.setFixedSize(30, 28); self.close_session_button.setToolTip("현재 세션을 닫습니다."); self.close_session_button.clicked.connect(self._close_session); session_header_layout.addWidget(self.close_session_button)
        center_layout.addWidget(session_header)
        self.session_notice = QLabel(); self.session_notice.setObjectName("sessionNotice"); self.session_notice.setWordWrap(True); center_layout.addWidget(self.session_notice)
        self.activity = QLabel("● Codex CLI 터미널")
        self.activity.setObjectName("activity")
        center_layout.addWidget(self.activity)
        self.terminal = QWebEngineView()
        self.terminal_bridge = TerminalBridge(self)
        self.terminal_channel = QWebChannel(self.terminal)
        self.terminal_channel.registerObject("bridge", self.terminal_bridge)
        self.terminal.page().setWebChannel(self.terminal_channel)
        self.terminal.setHtml(TERMINAL_HTML, QUrl("https://cdn.jsdelivr.net/npm/xterm@5.3.0/"))
        center_layout.addWidget(self.terminal, 1)
        self._refresh_workspace_tabs()
        self._refresh_session_tabs()

        right = QWidget(); right.setObjectName("filePanel"); right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 18, 16, 16); right_layout.setSpacing(10)
        right_layout.addWidget(self._title("탐색기"))
        self.file_search = QLineEdit(); self.file_search.setObjectName("searchBox"); self.file_search.setPlaceholderText("파일 찾기…"); self.file_search.textChanged.connect(self._filter_files); right_layout.addWidget(self.file_search)
        self.files = QTreeWidget(); self.files.setObjectName("fileTree"); self.files.setHeaderHidden(True); self.files.setUniformRowHeights(True); self.files.itemClicked.connect(self._preview_file); self.files.itemExpanded.connect(self._expand_folder); right_layout.addWidget(self.files, 1)
        refresh = QPushButton("파일 새로고침"); refresh.setObjectName("secondaryButton"); refresh.clicked.connect(self._refresh_files); right_layout.addWidget(refresh)
        right_layout.addWidget(self._title("변경 사항"))
        self.changed_list = QListWidget(); self.changed_list.setObjectName("changedList"); self.changed_list.setMinimumHeight(72); self.changed_list.setMaximumHeight(150); self.changed_list.itemClicked.connect(self._preview_changed_item); right_layout.addWidget(self.changed_list)
        right_layout.addWidget(self._title("미리보기"))
        self.preview_title = QLabel("파일을 선택하세요"); self.preview_title.setObjectName("previewTitle"); right_layout.addWidget(self.preview_title)
        self.preview = QPlainTextEdit(); self.preview.setObjectName("preview"); self.preview.setReadOnly(True); self.preview.setPlaceholderText("파일을 선택하면 미리보기가 표시됩니다."); right_layout.addWidget(self.preview, 1)
        self.open_file_button = QPushButton("기본 앱에서 열기"); self.open_file_button.setObjectName("secondaryButton"); self.open_file_button.setEnabled(False); self.open_file_button.clicked.connect(self._open_preview_file); right_layout.addWidget(self.open_file_button)
        change_actions = QWidget(); change_actions_layout = QHBoxLayout(change_actions); change_actions_layout.setContentsMargins(0, 0, 0, 0)
        self.approve_change_button = QPushButton("변경 승인"); self.approve_change_button.setObjectName("primaryButton"); self.approve_change_button.setEnabled(False); self.approve_change_button.clicked.connect(self._approve_change); change_actions_layout.addWidget(self.approve_change_button)
        self.rollback_change_button = QPushButton("되돌리기"); self.rollback_change_button.setObjectName("dangerButton"); self.rollback_change_button.setEnabled(False); self.rollback_change_button.clicked.connect(self._rollback_change); change_actions_layout.addWidget(self.rollback_change_button)
        right_layout.addWidget(change_actions)
        bulk_actions = QWidget(); bulk_actions_layout = QHBoxLayout(bulk_actions); bulk_actions_layout.setContentsMargins(0, 0, 0, 0)
        self.approve_all_changes_button = QPushButton("변경 모두 승인"); self.approve_all_changes_button.setObjectName("secondaryButton"); self.approve_all_changes_button.setEnabled(False); self.approve_all_changes_button.clicked.connect(self._approve_all_changes); bulk_actions_layout.addWidget(self.approve_all_changes_button)
        self.rollback_all_changes_button = QPushButton("변경 모두 되돌리기"); self.rollback_all_changes_button.setObjectName("dangerButton"); self.rollback_all_changes_button.setEnabled(False); self.rollback_all_changes_button.clicked.connect(self._rollback_all_changes); bulk_actions_layout.addWidget(self.rollback_all_changes_button)
        right_layout.addWidget(bulk_actions)
        self.open_trash_button = QPushButton("복구 보관함 열기"); self.open_trash_button.setObjectName("secondaryButton"); self.open_trash_button.clicked.connect(self._open_rollback_trash); right_layout.addWidget(self.open_trash_button)

        root.addWidget(left); root.addWidget(center); root.addWidget(right); root.setSizes([300, 820, 330])
        root.setStretchFactor(0, 0); root.setStretchFactor(1, 1); root.setStretchFactor(2, 0)
        left.setMinimumWidth(250); right.setMinimumWidth(280)
        self.setStatusBar(QStatusBar()); self.statusBar().showMessage("준비됨")

    @staticmethod
    def _title(text: str) -> QLabel:
        label = QLabel(text); label.setObjectName("section"); return label

    @staticmethod
    def _restore_sessions(saved_sessions: dict[str, list[dict[str, str]]]) -> dict[str, list[SessionRecord]]:
        restored = {}
        for workspace, values in saved_sessions.items():
            key = str(Path(workspace).expanduser().resolve())
            records = [SessionRecord(value["id"], value["name"]) for value in values]
            if records:
                restored[key] = records
        return restored

    def _ensure_session_for_workspace(self) -> str:
        workspace = str(self.workspace)
        records = self.sessions_by_workspace.setdefault(workspace, [])
        if not records:
            records.append(SessionRecord(uuid4().hex, "세션 1"))
        active = self.active_session_ids.get(workspace)
        if active not in {record.session_id for record in records}:
            active = records[0].session_id
            self.active_session_ids[workspace] = active
        return active

    def _active_session(self) -> Optional[SessionRecord]:
        active_id = self._ensure_session_for_workspace()
        return next((record for record in self.sessions_by_workspace[str(self.workspace)] if record.session_id == active_id), None)

    def _refresh_session_tabs(self) -> None:
        if not hasattr(self, "session_tabs"):
            return
        active_id = self._ensure_session_for_workspace()
        records = self.sessions_by_workspace[str(self.workspace)]
        self.session_tabs.blockSignals(True)
        while self.session_tabs.count():
            self.session_tabs.removeTab(0)
        for record in records:
            index = self.session_tabs.addTab(record.name)
            self.session_tabs.setTabData(index, record.session_id)
            self.session_tabs.setTabToolTip(index, f"{record.name} · {self.workspace}")
        active_index = next((index for index, record in enumerate(records) if record.session_id == active_id), 0)
        self.session_tabs.setCurrentIndex(active_index)
        self.session_tabs.blockSignals(False)
        self.rename_session_button.setEnabled(bool(records))
        self.close_session_button.setEnabled(len(records) > 1)
        multiple_sessions = len(records) > 1
        self.session_notice.setVisible(multiple_sessions)
        if multiple_sessions:
            self.session_notice.setText("⚠ 여러 세션이 같은 작업 공간의 파일을 공유합니다. 동시에 수정하면 충돌이 발생할 수 있습니다.")

    def _select_session_tab(self, index: int) -> None:
        if index < 0:
            return
        session_id = self.session_tabs.tabData(index)
        if not session_id:
            return
        self.active_session_ids[str(self.workspace)] = session_id
        self._start_terminal()
        record = self._active_session()
        self.statusBar().showMessage(f"세션 선택됨: {record.name if record else session_id}")

    def _new_session(self) -> None:
        records = self.sessions_by_workspace[str(self.workspace)]
        if records:
            answer = QMessageBox.warning(
                self,
                "새 세션 확인",
                "같은 작업 공간에 두 번째 Codex 세션을 열까요?\n\n여러 세션이 같은 파일을 동시에 수정하면 충돌이 발생할 수 있습니다.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        session_number = len(records) + 1
        record = SessionRecord(uuid4().hex, f"세션 {session_number}")
        records.append(record)
        self.active_session_ids[str(self.workspace)] = record.session_id
        self._refresh_session_tabs()
        self._save_workspaces()
        self._start_terminal()
        self.statusBar().showMessage(f"새 세션을 시작했습니다: {record.name}")

    def _rename_session(self) -> None:
        record = self._active_session()
        if record is None:
            return
        name, accepted = QInputDialog.getText(self, "세션 이름 변경", "세션 이름", text=record.name)
        name = name.strip()
        if not accepted or not name:
            return
        record.name = name
        self._refresh_session_tabs()
        self._save_workspaces()
        self.statusBar().showMessage(f"세션 이름을 변경했습니다: {name}")

    def _close_session(self) -> None:
        records = self.sessions_by_workspace[str(self.workspace)]
        if len(records) <= 1:
            self.statusBar().showMessage("작업 공간에는 최소 하나의 세션이 필요합니다.")
            return
        record = self._active_session()
        if record is None:
            return
        answer = QMessageBox.warning(
            self,
            "세션 닫기",
            f"'{record.name}' 세션을 닫을까요?\n\n실행 중인 Codex 프로세스도 종료됩니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        session = self.terminal_sessions.pop(record.session_id, None)
        if session is not None:
            session.stop()
        records.remove(record)
        self.active_session_ids[str(self.workspace)] = records[0].session_id
        self._refresh_session_tabs()
        self._save_workspaces()
        self._start_terminal()
        self.statusBar().showMessage(f"세션을 닫았습니다: {record.name}")

    def _start_terminal(self) -> None:
        try:
            session_id = self._ensure_session_for_workspace()
            session = self.terminal_sessions.get(session_id)
            if session is None or not session.alive():
                session = TerminalSession(self.workspace)
                session.start()
                self.terminal_sessions[session_id] = session
            self.terminal_active = session_id
            self.terminal_bridge.set_active(session_id)
            self.restart_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            if self.terminal_timer is None:
                self.terminal_timer = QTimer(self)
                self.terminal_timer.timeout.connect(self._poll_terminal)
            self.terminal_timer.start(30)
            self.terminal.setFocus()
            record = self._active_session()
            self.activity.setText(f"● 실행 중 · {record.name if record else 'Codex CLI 세션'}")
            self.statusBar().showMessage("Codex CLI 세션 실행 중")
        except Exception as exc:
            self.restart_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.activity.setText("● 시작 실패")
            self.statusBar().showMessage(f"Codex CLI 세션 시작 실패: {exc}")

    def _restart_terminal(self) -> None:
        session = self.terminal_sessions.get(self._ensure_session_for_workspace())
        if session is not None:
            session.stop()
        self._start_terminal()

    def _stop_active_terminal(self) -> None:
        session = self.terminal_sessions.get(self.terminal_active)
        if session is None:
            return
        session.stop()
        self.stop_button.setEnabled(False)
        self.restart_button.setEnabled(True)
        self.activity.setText("● 중지됨 · 재시작 가능")
        self.statusBar().showMessage("Codex CLI 세션을 중지했습니다.")

    def _poll_terminal(self) -> None:
        if not self.terminal_sessions:
            return
        for session_id, session in list(self.terminal_sessions.items()):
            try:
                output = session.read()
            except OSError as exc:
                self.statusBar().showMessage(f"Codex CLI 출력 읽기 실패: {exc}")
                continue
            if output:
                self.terminal_bridge.emit_output(session_id, output)
            if not session.alive() and session_id == self.terminal_active:
                self.stop_button.setEnabled(False)
                self.restart_button.setEnabled(True)
                self.activity.setText("● 세션 종료됨 · 재시작 가능")
                self.statusBar().showMessage("Codex CLI 세션 종료됨")

    def _write_terminal(self, text: str) -> None:
        session = self.terminal_sessions.get(self.terminal_active)
        if session is not None and session.alive():
            try:
                session.write(text)
            except OSError as exc:
                self.statusBar().showMessage(f"Codex CLI 입력 실패: {exc}")

    def _resize_terminal(self, columns: int, rows: int) -> None:
        session = self.terminal_sessions.get(self.terminal_active)
        if session is not None:
            session.resize(columns, rows)

    def closeEvent(self, event) -> None:
        if self.change_timer is not None:
            self.change_timer.stop()
        if self.terminal_timer is not None:
            self.terminal_timer.stop()
        for session in self.terminal_sessions.values():
            session.stop()
        for worker in (self.scan_worker, self.change_scan_worker, self.usage_worker, self.reset_credit_worker):
            if worker is not None and worker.isRunning():
                worker.requestInterruption()
                worker.quit()
                worker.wait(1500)
        self._save_workspaces()
        event.accept()

    def _save_workspaces(self) -> None:
        try:
            sessions = {
                workspace: [{"id": record.session_id, "name": record.name} for record in records]
                for workspace, records in self.sessions_by_workspace.items()
                if records
            }
            self.workspace_state.save(self.workspaces, self.workspace, sessions, self.active_session_ids)
        except OSError as exc:
            self.statusBar().showMessage(f"작업 공간 목록을 저장하지 못했습니다: {exc}")

    def _load_usage(self) -> None:
        self.usage_label.setText("사용량 확인 중…")
        self.usage_worker = UsageWorker()
        self.usage_worker.loaded.connect(self._usage_loaded)
        self.usage_worker.failed.connect(lambda message: self.usage_label.setText(f"사용량을 불러오지 못했습니다.\n{message}"))
        self.usage_worker.start()

    def _usage_loaded(self, payload) -> None:
        snapshot = payload.get("rateLimits") or {}
        lines = []
        for bar in (self.primary_usage_bar, self.secondary_usage_bar):
            bar.hide()
        credits_summary = payload.get("rateLimitResetCredits") or {}
        available_count = int(credits_summary.get("availableCount") or 0)
        available_credits = credits_summary.get("credits") or []
        self.reset_credit_id = available_credits[0].get("id", "") if available_credits else ""
        self.reset_credit_button.setEnabled(available_count > 0)
        self.reset_credit_button.setText(f"사용량 초기화권 사용 ({available_count}개)" if available_count else "사용량 초기화권 없음")
        if available_count:
            lines.append(f"초기화권: {available_count}개 보유")
        if snapshot.get("planType"):
            lines.append(f"요금제: {snapshot['planType']}")
        for label, key in (("기본 한도", "primary"), ("보조 한도", "secondary")):
            window = snapshot.get(key) or {}
            if "usedPercent" not in window:
                continue
            remaining = max(0, 100 - float(window["usedPercent"]))
            bar = self.primary_usage_bar if key == "primary" else self.secondary_usage_bar
            bar.setValue(int(remaining))
            bar.setFormat(f"{label}  ·  {remaining:.0f}% 남음")
            bar.show()
            line = f"{label}: {remaining:.0f}% 남음"
            if window.get("resetsAt"):
                reset = datetime.fromtimestamp(window["resetsAt"]).strftime("%m/%d %H:%M")
                line += f" · {reset} 재설정"
            lines.append(line)
        credits = snapshot.get("credits") or {}
        if credits.get("balance") is not None:
            lines.append(f"크레딧 잔액: {credits['balance']}")
        self.usage_label.setText("\n".join(lines) if lines else "현재 사용량 정보를 제공하지 않습니다.")

    def _consume_reset_credit(self) -> None:
        answer = QMessageBox.warning(
            self,
            "사용량 초기화권 사용 확인",
            "초기화권 1개를 사용해 사용량 한도를 초기화할까요?\n\n사용한 초기화권은 되돌릴 수 없습니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.reset_credit_button.setEnabled(False)
        self.reset_credit_button.setText("초기화 중…")
        self.reset_credit_worker = ResetCreditWorker(self.reset_credit_id)
        self.reset_credit_worker.completed.connect(self._reset_credit_done)
        self.reset_credit_worker.failed.connect(self._reset_credit_failed)
        self.reset_credit_worker.start()

    def _reset_credit_done(self, outcome: str) -> None:
        messages = {"reset": "사용량을 초기화했습니다.", "nothingToReset": "현재 초기화할 사용량 한도가 없습니다.", "noCredit": "사용 가능한 초기화권이 없습니다.", "alreadyRedeemed": "이미 처리된 초기화 요청입니다."}
        self.statusBar().showMessage(messages.get(outcome, f"초기화 결과: {outcome}"))
        self._load_usage()

    def _reset_credit_failed(self, message: str) -> None:
        self.statusBar().showMessage(f"초기화권 사용 실패: {message}")
        self._load_usage()

    def _refresh_files(self) -> None:
        self.files.clear()
        self.statusBar().showMessage("파일 목록을 불러오는 중…")
        self.scan_worker = FileScanWorker(self.workspace)
        self.scan_worker.scanned.connect(self._files_scanned)
        self.scan_worker.failed.connect(self._files_scan_failed)
        self.scan_worker.finished.connect(lambda worker=self.scan_worker: self._scan_finished(worker))
        self.scan_workers.append(self.scan_worker)
        self.scan_worker.start()

    def _scan_finished(self, worker) -> None:
        if worker in self.scan_workers:
            self.scan_workers.remove(worker)
        worker.deleteLater()

    def _files_scanned(self, files, directories, workspace: str) -> None:
        if workspace != str(self.workspace):
            return
        self.all_files = files
        self.all_directories = directories
        self._initialize_file_baseline(workspace, files)
        self._filter_files(self.file_search.text())
        self.statusBar().showMessage(f"파일 {len(files):,}개 · 폴더 {len(directories):,}개 · 최상위 항목 표시")

    def _files_scan_failed(self, message: str) -> None:
        self.statusBar().showMessage(f"파일 목록을 불러오지 못했습니다: {message}")

    def _initialize_file_baseline(self, workspace: str, files: list[str]) -> None:
        if workspace in self.file_baselines:
            return
        baseline = {}
        root = Path(workspace)
        captured_bytes = 0
        for relative in files:
            path = root / relative
            try:
                snapshot, captured = self._capture_file_baseline(path, captured_bytes)
                baseline[relative] = snapshot
                captured_bytes += captured
            except OSError:
                continue
        self.file_baselines[workspace] = baseline

    @staticmethod
    def _looks_binary(data: bytes) -> bool:
        if not data:
            return False
        if b"\x00" in data:
            return True
        control_bytes = sum(value < 9 or 14 <= value < 32 for value in data)
        return control_bytes / len(data) > 0.1

    def _capture_file_baseline(self, path: Path, captured_bytes: int = 0) -> tuple[FileBaseline, int]:
        stat = path.stat()
        with path.open("rb") as handle:
            sample = handle.read(8192)
        is_binary = self._looks_binary(sample)
        content = None
        captured = 0
        if not is_binary and stat.st_size <= 262_144 and captured_bytes + stat.st_size <= 16 * 1_048_576:
            content = path.read_bytes()
            captured = stat.st_size
        return FileBaseline(stat.st_mtime_ns, stat.st_size, content, is_binary), captured

    def _check_file_changes(self) -> None:
        if self.change_scan_worker is not None and self.change_scan_worker.isRunning():
            return
        self.change_scan_worker = FileChangeWorker(self.workspace)
        self.change_scan_worker.scanned.connect(self._file_changes_scanned)
        self.change_scan_worker.failed.connect(lambda message: self.statusBar().showMessage(f"파일 변경 확인 실패: {message}"))
        self.change_scan_worker.finished.connect(self._change_scan_finished)
        self.change_scan_worker.start()

    def _change_scan_finished(self) -> None:
        if self.change_scan_worker is not None:
            self.change_scan_worker.deleteLater()
            self.change_scan_worker = None

    def _file_changes_scanned(self, files, directories, workspace: str) -> None:
        if workspace != str(self.workspace):
            return
        baseline = self.file_baselines.get(workspace)
        if baseline is None:
            self._initialize_file_baseline(workspace, files)
            return
        current_files = set(files)
        baseline_files = set(baseline)
        changed = (current_files - baseline_files) | (baseline_files - current_files)
        for relative in current_files & baseline_files:
            path = self.workspace / relative
            try:
                stat = path.stat()
                if stat.st_mtime_ns != baseline[relative].mtime_ns or stat.st_size != baseline[relative].size:
                    changed.add(relative)
            except OSError:
                changed.add(relative)
        structural_change = files != self.all_files or directories != self.all_directories
        previous_changed = self.changed_files
        self.changed_files = changed
        self._refresh_changed_list()
        self._update_bulk_change_buttons()
        if structural_change:
            self.all_files = files
            self.all_directories = directories
            self._filter_files(self.file_search.text())
        if changed:
            self.statusBar().showMessage(f"변경된 파일 {len(changed):,}개 · 미리보기에서 확인하세요")
        elif structural_change:
            self.statusBar().showMessage(f"파일 목록 갱신 · 파일 {len(files):,}개 · 폴더 {len(directories):,}개")
        if changed == previous_changed:
            return
        if self.preview_relative in changed:
            self._preview_file_by_path(self.preview_relative)

    def _preview_file_by_path(self, relative: str) -> None:
        if relative not in self.changed_files:
            return
        self.preview_relative = relative
        baseline_map = self.file_baselines.get(str(self.workspace), {})
        baseline = baseline_map.get(relative)
        current_path = self.workspace / relative
        is_new = baseline is None
        old_content = b"" if is_new else baseline.content
        self.preview_path = current_path if current_path.exists() else None
        self.open_file_button.setEnabled(self.preview_path is not None)
        if self.preview_path is not None:
            self.open_file_button.setText(f"기본 앱에서 열기 · {self.preview_path.name}")
        try:
            new_content = current_path.read_bytes()
        except FileNotFoundError:
            new_content = b""
        if old_content is None:
            kind = "바이너리" if baseline is not None and baseline.is_binary else "대용량"
            self.preview_title.setText(f"{kind} 변경 · 되돌리기 불가 · {Path(relative).name}")
            self.preview.setPlainText("원본이 크거나 바이너리라 텍스트 Diff를 표시할 수 없습니다.\n\n원본을 메모리에 보관하지 않아 되돌릴 수 없습니다. 변경을 승인하면 현재 상태가 기준이 됩니다.")
        else:
            try:
                old_text = old_content.decode("utf-8", errors="replace").splitlines(keepends=True)
                new_text = new_content.decode("utf-8", errors="replace").splitlines(keepends=True)
                diff = "".join(difflib.unified_diff(old_text, new_text, fromfile=f"이전/{relative}", tofile=f"현재/{relative}"))
                change_type = "신규 파일" if is_new else "변경됨"
                self.preview_title.setText(f"{change_type} · {Path(relative).name}")
                self.preview.setPlainText(diff or "변경 내용을 표시할 수 없습니다.")
            except Exception as exc:
                self.preview.setPlainText(f"Diff를 표시하지 못했습니다.\n{exc}")
        self.approve_change_button.setEnabled(True)
        self.rollback_change_button.setEnabled(is_new or old_content is not None)
        self._update_bulk_change_buttons()

    def _refresh_changed_list(self) -> None:
        self.changed_list.clear()
        if not self.changed_files:
            item = QListWidgetItem("변경된 파일이 없습니다")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.changed_list.addItem(item)
            return
        baseline_map = self.file_baselines.get(str(self.workspace), {})
        for relative in sorted(self.changed_files, key=str.casefold):
            path = self.workspace / relative
            if relative not in baseline_map:
                kind = "신규"
            elif not path.exists():
                kind = "삭제"
            elif baseline_map[relative].content is None:
                kind = "바이너리" if baseline_map[relative].is_binary else "대용량"
            else:
                kind = "변경"
            item = QListWidgetItem(f"{kind}  {relative}")
            item.setData(Qt.ItemDataRole.UserRole, relative)
            self.changed_list.addItem(item)

    def _preview_changed_item(self, item: QListWidgetItem) -> None:
        relative = item.data(Qt.ItemDataRole.UserRole)
        if relative:
            self._preview_file_by_path(relative)

    def _update_bulk_change_buttons(self) -> None:
        has_changes = bool(self.changed_files)
        self.approve_all_changes_button.setEnabled(has_changes)
        self.rollback_all_changes_button.setEnabled(has_changes and any(
            (baseline := self.file_baselines.get(str(self.workspace), {}).get(relative)) is None or baseline.content is not None
            for relative in self.changed_files
        ))

    def _open_rollback_trash(self) -> None:
        try:
            self.rollback_trash_root.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.rollback_trash_root))):
                self.statusBar().showMessage("복구 보관함을 열지 못했습니다.")
        except OSError as exc:
            self.statusBar().showMessage(f"복구 보관함을 준비하지 못했습니다: {exc}")

    def _move_new_file_to_trash(self, relative: str) -> Optional[Path]:
        source = self.tools.resolve(relative)
        if not source.exists():
            return None
        workspace_key = hashlib.sha256(str(self.workspace).encode("utf-8")).hexdigest()[:16]
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        destination = self.rollback_trash_root / workspace_key / timestamp / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        return destination

    def _approve_change(self) -> None:
        if self.preview_relative is None:
            return
        relative = self.preview_relative
        if relative not in self.changed_files:
            return
        try:
            path = self.workspace / relative
            if not path.exists():
                self.file_baselines[str(self.workspace)].pop(relative, None)
                self.changed_files.discard(relative)
                self.approve_change_button.setEnabled(False)
                self.rollback_change_button.setEnabled(False)
                self.preview.setPlainText("삭제된 변경을 승인했습니다.")
                self.statusBar().showMessage(f"삭제를 승인했습니다: {relative}")
                self._refresh_changed_list()
                self._update_bulk_change_buttons()
                return
            snapshot, _ = self._capture_file_baseline(path)
            self.file_baselines[str(self.workspace)][relative] = snapshot
            self.changed_files.discard(relative)
            self.approve_change_button.setEnabled(False)
            self.rollback_change_button.setEnabled(False)
            self._preview_file_by_path(relative) if relative in self.changed_files else self._preview_file_content(relative)
            self._refresh_changed_list()
            self._update_bulk_change_buttons()
            self.statusBar().showMessage(f"변경을 승인했습니다: {relative}")
        except (OSError, ValueError) as exc:
            self.statusBar().showMessage(f"변경 승인 실패: {exc}")

    def _rollback_change(self) -> None:
        if self.preview_relative is None:
            return
        relative = self.preview_relative
        baseline = self.file_baselines.get(str(self.workspace), {}).get(relative)
        if relative not in self.changed_files:
            return
        answer = QMessageBox.warning(self, "변경 되돌리기", f"{relative}의 변경을 이전 상태로 되돌릴까요?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            path = self.workspace / relative
            if baseline is None:
                moved_to = self._move_new_file_to_trash(relative)
                message = "신규 파일을 복구 보관함으로 이동했습니다." if moved_to else "신규 파일이 이미 삭제되어 있습니다."
            elif baseline.content is not None:
                path.write_bytes(baseline.content)
                message = "변경을 되돌렸습니다."
            else:
                return
            self.changed_files.discard(relative)
            self.approve_change_button.setEnabled(False)
            self.rollback_change_button.setEnabled(False)
            self._preview_file_content(relative)
            self._refresh_changed_list()
            self._update_bulk_change_buttons()
            self.statusBar().showMessage(f"{message} · {relative}")
        except OSError as exc:
            self.statusBar().showMessage(f"변경 되돌리기 실패: {exc}")

    def _approve_all_changes(self) -> None:
        if not self.changed_files:
            return
        for relative in list(self.changed_files):
            path = self.workspace / relative
            try:
                if path.exists():
                    snapshot, _ = self._capture_file_baseline(path)
                    self.file_baselines[str(self.workspace)][relative] = snapshot
                else:
                    self.file_baselines[str(self.workspace)].pop(relative, None)
                self.changed_files.discard(relative)
            except OSError as exc:
                self.statusBar().showMessage(f"변경 승인 실패: {relative} · {exc}")
        self.approve_change_button.setEnabled(False)
        self.rollback_change_button.setEnabled(False)
        self._refresh_changed_list()
        self._update_bulk_change_buttons()
        self.statusBar().showMessage("변경된 파일을 모두 승인했습니다.")

    def _rollback_all_changes(self) -> None:
        rollbackable = [
            relative for relative in self.changed_files
            if (baseline := self.file_baselines.get(str(self.workspace), {}).get(relative)) is None or baseline.content is not None
        ]
        if not rollbackable:
            return
        answer = QMessageBox.warning(
            self,
            "변경 모두 되돌리기",
            f"변경된 파일 {len(rollbackable)}개를 이전 상태로 되돌릴까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        baseline_map = self.file_baselines.get(str(self.workspace), {})
        failures = []
        moved_new_files = 0
        for relative in rollbackable:
            baseline = baseline_map.get(relative)
            path = self.workspace / relative
            try:
                if baseline is None:
                    if self._move_new_file_to_trash(relative) is not None:
                        moved_new_files += 1
                else:
                    path.write_bytes(baseline.content)
                self.changed_files.discard(relative)
            except OSError as exc:
                failures.append(f"{relative}: {exc}")
        self.approve_change_button.setEnabled(False)
        self.rollback_change_button.setEnabled(False)
        self._refresh_changed_list()
        self._update_bulk_change_buttons()
        if failures:
            self.statusBar().showMessage(f"일부 되돌리기 실패: {failures[0]}")
        else:
            suffix = f" 신규 파일 {moved_new_files}개는 복구 보관함에 보관했습니다." if moved_new_files else ""
            self.statusBar().showMessage(f"변경된 파일 {len(rollbackable)}개를 되돌렸습니다.{suffix}")

    def _refresh_workspaces(self) -> None:
        self.workspace_list.clear()
        for path in self.workspaces:
            item = QListWidgetItem(path.name or str(path))
            item.setToolTip(str(path))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.workspace_list.addItem(item)
        self.workspace_list.setCurrentRow(self.workspaces.index(self.workspace))
        self._refresh_workspace_tabs()

    def _refresh_workspace_tabs(self) -> None:
        if not hasattr(self, "workspace_tabs"):
            return
        self.workspace_tabs.blockSignals(True)
        while self.workspace_tabs.count():
            self.workspace_tabs.removeTab(0)
        for path in self.workspaces:
            index = self.workspace_tabs.addTab(path.name or str(path))
            self.workspace_tabs.setTabData(index, str(path))
            self.workspace_tabs.setTabToolTip(index, str(path))
        self.workspace_tabs.setCurrentIndex(self.workspaces.index(self.workspace))
        self.workspace_tabs.blockSignals(False)

    def _select_workspace_tab(self, index: int) -> None:
        if index < 0 or index >= len(self.workspaces):
            return
        path = self.workspace_tabs.tabData(index)
        if path:
            self._select_workspace_path(Path(path))

    def _add_workspace(self, path: Path) -> None:
        path = path.resolve()
        if path not in self.workspaces:
            self.workspaces.append(path)
            self._save_workspaces()
        self._select_workspace_path(path)

    def _select_workspace(self, item: QListWidgetItem) -> None:
        self._select_workspace_path(Path(item.data(Qt.ItemDataRole.UserRole)))

    def _select_workspace_path(self, path: Path) -> None:
        self.workspace = path.resolve()
        self.tools = WorkspaceTools(self.workspace)
        self._ensure_session_for_workspace()
        self.workspace_label.setText(str(self.workspace))
        self.changed_files = set()
        self.preview_path = None
        self.preview_relative = None
        self.preview_title.setText("파일을 선택하세요")
        self.preview.clear()
        self.open_file_button.setEnabled(False)
        self.approve_change_button.setEnabled(False)
        self.rollback_change_button.setEnabled(False)
        self._refresh_changed_list()
        self._update_bulk_change_buttons()
        self._refresh_workspaces()
        self._refresh_session_tabs()
        self._refresh_files()
        self._start_terminal()
        self._save_workspaces()
        self.statusBar().showMessage(f"작업 공간 선택됨: {self.workspace.name or self.workspace}")

    def _remove_workspace(self) -> None:
        if len(self.workspaces) == 1:
            self.statusBar().showMessage("최소 하나의 작업 공간이 필요합니다.")
            return
        removed = str(self.workspace)
        for record in self.sessions_by_workspace.pop(removed, []):
            session = self.terminal_sessions.pop(record.session_id, None)
            if session is not None:
                session.stop()
        self.active_session_ids.pop(removed, None)
        self.workspaces.remove(self.workspace)
        self._save_workspaces()
        self._select_workspace_path(self.workspaces[0])

    def _filter_files(self, query: str) -> None:
        needle = unicodedata.normalize("NFC", query.strip()).casefold()
        if not needle:
            self._render_root()
            return

        self.files.setUpdatesEnabled(False)
        self.files.clear()
        visible_files = []
        for name in self.all_files:
            normalized_name = unicodedata.normalize("NFC", name).casefold()
            if not needle or needle in normalized_name:
                visible_files.append(name)
                if len(visible_files) >= 2_000:
                    break

        nodes = {}
        file_paths = {tuple(Path(name).parts) for name in visible_files}
        ancestor_paths = set()
        for name in visible_files:
            ancestor_paths.update(Path(name).parents)
        visible_directories = []
        for name in self.all_directories:
            normalized_name = unicodedata.normalize("NFC", name).casefold()
            is_ancestor = Path(name) in ancestor_paths
            if not needle or needle in normalized_name or is_ancestor:
                visible_directories.append(name)

        try:
            for name, is_file in [(directory, False) for directory in visible_directories] + [(file, True) for file in visible_files]:
                parts = Path(name).parts
                parent = None
                for index, part in enumerate(parts):
                    key = parts[: index + 1]
                    if key not in nodes:
                        item = QTreeWidgetItem([part])
                        item.setData(0, Qt.ItemDataRole.UserRole, str(Path(*key)))
                        item.setData(0, Qt.ItemDataRole.UserRole + 1, is_file and key in file_paths)
                        if parent is None:
                            self.files.addTopLevelItem(item)
                        else:
                            parent.addChild(item)
                        nodes[key] = item
                    parent = nodes[key]
        finally:
            self.files.setUpdatesEnabled(True)

    def _render_root(self) -> None:
        self.files.setUpdatesEnabled(False)
        self.files.clear()
        try:
            directories = sorted(name for name in self.all_directories if Path(name).parent == Path("."))
            files = sorted(name for name in self.all_files if Path(name).parent == Path("."))
            for name in directories:
                self._add_folder_item(None, name)
            for name in files:
                item = QTreeWidgetItem([Path(name).name])
                item.setData(0, Qt.ItemDataRole.UserRole, name)
                item.setData(0, Qt.ItemDataRole.UserRole + 1, True)
                self.files.addTopLevelItem(item)
        finally:
            self.files.setUpdatesEnabled(True)

    def _add_folder_item(self, parent, relative: str):
        item = QTreeWidgetItem([Path(relative).name])
        item.setData(0, Qt.ItemDataRole.UserRole, relative)
        item.setData(0, Qt.ItemDataRole.UserRole + 1, False)
        item.setData(0, Qt.ItemDataRole.UserRole + 2, True)
        if parent is None:
            self.files.addTopLevelItem(item)
        else:
            parent.addChild(item)
        item.addChild(QTreeWidgetItem(["불러오는 중…"]))
        return item

    def _expand_folder(self, item: QTreeWidgetItem) -> None:
        if not item.data(0, Qt.ItemDataRole.UserRole + 2):
            return
        item.setData(0, Qt.ItemDataRole.UserRole + 2, False)
        item.takeChildren()
        relative = item.data(0, Qt.ItemDataRole.UserRole)
        try:
            directory = self.tools.resolve(relative)
            entries = sorted((entry for entry in directory.iterdir() if entry.name not in {".git", ".agent"}), key=lambda entry: (not entry.is_dir(), entry.name.casefold()))
            for entry in entries:
                child_relative = str(Path(relative) / entry.name)
                if entry.is_dir():
                    self._add_folder_item(item, child_relative)
                elif entry.is_file():
                    child = QTreeWidgetItem([entry.name])
                    child.setData(0, Qt.ItemDataRole.UserRole, child_relative)
                    child.setData(0, Qt.ItemDataRole.UserRole + 1, True)
                    item.addChild(child)
        except (ValueError, OSError) as exc:
            item.addChild(QTreeWidgetItem([f"읽기 실패: {exc}"]))

    def _preview_file(self, item: QListWidgetItem) -> None:
        relative = item.data(0, Qt.ItemDataRole.UserRole)
        if not item.data(0, Qt.ItemDataRole.UserRole + 1):
            item.setExpanded(not item.isExpanded())
            self.preview_path = None
            self.preview_relative = None
            self.open_file_button.setEnabled(False)
            self.preview_title.setText(f"폴더 · {relative}")
            self.preview.setPlainText(f"폴더: {relative}")
            return
        if relative in self.changed_files:
            self.preview_relative = relative
            self.preview_path = self.tools.resolve(relative)
            self.open_file_button.setEnabled(True)
            self.open_file_button.setText(f"기본 앱에서 열기 · {self.preview_path.name}")
            self._preview_file_by_path(relative)
            return
        self._preview_file_content(relative)

    def _preview_file_content(self, relative: str) -> None:
        try:
            path = self.tools.resolve(relative)
            self.preview_path = path
            self.preview_relative = relative
            self.approve_change_button.setEnabled(False)
            self.rollback_change_button.setEnabled(False)
            self.open_file_button.setEnabled(True)
            self.open_file_button.setText(f"기본 앱에서 열기 · {path.name}")
            self.preview_title.setText(f"{path.name}  ·  {path.stat().st_size:,} bytes")
            data = path.read_bytes()
            if b"\x00" in data[:8192]:
                self.preview.setPlainText(
                    f"바이너리 파일이라 텍스트 미리보기를 지원하지 않습니다.\n\n"
                    f"파일 크기: {len(data):,} bytes"
                )
                return
            try:
                text = data.decode("utf-8")
                self.preview.setPlainText(text)
            except UnicodeDecodeError:
                text = data.decode("utf-8", errors="replace")
                self.preview.setPlainText(
                    "UTF-8이 아닌 문자 일부를 대체 표시했습니다.\n\n" + text
                )
        except (ValueError, FileNotFoundError, OSError) as exc:
            self.preview_path = None
            self.open_file_button.setEnabled(False)
            self.preview_title.setText("미리보기 오류")
            self.preview.setPlainText(str(exc))

    def _open_preview_file(self) -> None:
        if self.preview_path is None:
            return
        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.preview_path))):
            self.statusBar().showMessage(f"기본 앱으로 열었습니다: {self.preview_path.name}")
        else:
            self.statusBar().showMessage(f"파일을 열 수 없습니다: {self.preview_path.name}")

    def _choose_workspace(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "작업 공간 선택", str(self.workspace))
        if selected:
            self._add_workspace(Path(selected))

    @staticmethod
    def _style() -> str:
        return """
        * { outline: none; }
        QWidget { background:#111821; color:#dce7e5; font-size:12px; }
        QWidget#sidebar, QWidget#filePanel { background:#17212b; }
        QWidget#centerPanel { background:#101821; }
        QSplitter::handle { background:#26343d; width:4px; }
        QLabel#brand { color:#effbf5; font-size:16px; font-weight:800; letter-spacing:1.8px; padding:4px 0 0; }
        QLabel#brandSubtitle { color:#76918c; font-size:9px; font-weight:700; letter-spacing:1.4px; padding-bottom:10px; }
        QLabel#section { color:#78928e; font-size:10px; font-weight:800; letter-spacing:1.5px; padding-top:10px; padding-bottom:3px; }
        QLabel#workspacePath { color:#9cb1ad; font-size:11px; padding:3px 0 5px; }
        QLabel#activity { color:#bdf4d9; background:#17362f; border:1px solid #2c795f; border-radius:8px; padding:9px 12px; font-weight:700; }
        QLabel#sessionNotice { color:#f5d99b; background:#342c1d; border:1px solid #765f35; border-radius:8px; padding:8px 11px; }
        QLabel#infoCard { color:#c5d5d1; background:#101820; border:1px solid #2a3a43; border-radius:8px; padding:12px; line-height:1.4em; }
        QLabel#previewTitle { color:#b4c7c2; font-size:11px; padding:2px; }
        QLabel#hint { color:#78918d; font-size:11px; padding-top:2px; }
        QLabel { color:#cedbd8; }
        QLineEdit, QListWidget, QTreeWidget, QPlainTextEdit, QWebEngineView { background:#0d151d; border:1px solid #2a3b45; border-radius:8px; }
        QLineEdit { padding:9px 11px; color:#e5f1ee; selection-background-color:#285148; }
        QLineEdit:focus { border:1px solid #65c49d; }
        QListWidget, QTreeWidget, QPlainTextEdit { padding:7px; }
        QListWidget::item, QTreeWidget::item { padding:7px 8px; border-radius:5px; }
        QListWidget::item:hover, QTreeWidget::item:hover { background:#203039; }
        QListWidget::item:selected, QTreeWidget::item:selected { background:#24443e; color:#f4fffb; border-left:2px solid #76d5ae; }
        QPushButton { background:#22313a; border:1px solid #38505a; border-radius:7px; padding:9px 12px; color:#dce9e6; font-weight:650; }
        QPushButton:hover { background:#2b4047; border-color:#5b7d82; }
        QPushButton:pressed { background:#1b292f; }
        QPushButton:disabled { background:#1a252c; border-color:#29383f; color:#667b7c; }
        QPushButton#primaryButton { background:#28755d; border-color:#4fb68e; color:#f1fff9; }
        QPushButton#primaryButton:hover { background:#33916f; border-color:#79dfb6; }
        QPushButton#secondaryButton { background:#1d2a33; border-color:#344a55; color:#bcd0cc; }
        QPushButton#secondaryButton:hover { background:#294049; border-color:#5f7e82; color:#f1fffb; }
        QPushButton#dangerButton { background:#432c32; border-color:#754c57; color:#f2c8cc; }
        QPushButton#dangerButton:hover { background:#5a3740; border-color:#a56b76; color:#ffffff; }
        QPushButton#iconButton { background:#1d2a33; border:1px solid #344a55; border-radius:7px; padding:0; color:#bcd0cc; font-size:15px; font-weight:700; }
        QPushButton#iconButton:hover { background:#2b4148; border-color:#6b8d90; color:#ffffff; }
        QPushButton#dangerIconButton { background:#432c32; border:1px solid #754c57; border-radius:7px; padding:0; color:#f2c8cc; font-size:11px; }
        QPushButton#dangerIconButton:hover { background:#5a3740; border-color:#a56b76; color:#ffffff; }
        QProgressBar#usageBar { background:#0e171e; border:1px solid #2a3b45; border-radius:5px; color:#dcece8; text-align:center; min-height:18px; max-height:18px; }
        QProgressBar#usageBar::chunk { background:#3b9e79; border-radius:4px; }
        QTabBar#workspaceTabs { background:transparent; }
        QTabBar#workspaceTabs::tab { background:#17232c; color:#78918d; border:1px solid #2d414a; border-bottom:0; border-top-left-radius:7px; border-top-right-radius:7px; padding:8px 13px; margin-left:4px; }
        QTabBar#workspaceTabs::tab:hover { color:#e4f2ef; background:#22343d; }
        QTabBar#workspaceTabs::tab:selected { color:#effff9; background:#24443e; border-color:#4e9c82; }
        QTabBar#sessionTabs { background:transparent; }
        QTabBar#sessionTabs::tab { background:#16222a; color:#78918d; border:1px solid #2d414a; border-radius:6px; padding:6px 11px; margin-right:4px; }
        QTabBar#sessionTabs::tab:hover { color:#e4f2ef; background:#22343d; }
        QTabBar#sessionTabs::tab:selected { color:#effff9; background:#24443e; border-color:#65c49d; }
        QScrollBar:vertical { background:#111a21; width:10px; margin:3px; }
        QScrollBar::handle:vertical { background:#3d5960; border-radius:5px; min-height:28px; }
        QScrollBar::handle:vertical:hover { background:#5d7d80; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
        QStatusBar { background:#0d151d; color:#91aaa5; border-top:1px solid #263943; padding-left:12px; }
        """


def main() -> None:
    parser = argparse.ArgumentParser(description="Personal Agent desktop app")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args()
    app = QApplication([]); app.setApplicationName("개인 에이전트")
    font_family = "Segoe UI" if sys.platform == "win32" else "Helvetica Neue" if sys.platform == "darwin" else "Noto Sans"
    app.setFont(QFont(font_family, 12))
    window = MainWindow(args.workspace); window.show(); raise SystemExit(app.exec())
