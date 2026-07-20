import argparse
from datetime import datetime
from dataclasses import dataclass
import difflib
import hashlib
import json
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
        "--disable-logging --log-level=3",
    )

from .tools import WorkspaceTools
from .diagnostics import collect_diagnostics
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
<script src="https://cdn.jsdelivr.net/npm/xterm-addon-search@0.13.0/lib/xterm-addon-search.min.js"></script>
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
    const fit = new FitAddon.FitAddon(); const search = new SearchAddon.SearchAddon(); term.loadAddon(fit); term.loadAddon(search); term.open(host);
    if (term.textarea) { term.textarea.setAttribute('autocomplete','off'); term.textarea.setAttribute('autocorrect','off'); term.textarea.setAttribute('autocapitalize','off'); term.textarea.setAttribute('spellcheck','false'); }
    term.onData(data => { if (activeKey === key) bridge.writeTo(key, data); });
    term.attachCustomKeyEventHandler(event => {
      if (event.type !== 'keydown') return true;
      const modifier = event.ctrlKey || event.metaKey;
      if (modifier && event.key.toLowerCase() === 'c' && term.hasSelection()) { bridge.copy(term.getSelection()); return false; }
      if (modifier && event.key.toLowerCase() === 'v') { bridge.paste(text => term.paste(text)); return false; }
      return true;
    });
    terminals.set(key, {term, fit, search, host}); return terminals.get(key);
  };
  const activate = key => { activeKey = key; terminals.forEach(item => item.host.className = 'terminal hidden'); const item = getTerminal(key); item.host.className = 'terminal'; item.fit.fit(); bridge.resize(item.term.cols, item.term.rows); item.term.focus(); };
  const resize = () => { const item = terminals.get(activeKey); if (item) { item.fit.fit(); bridge.resize(item.term.cols, item.term.rows); } };
  bridge.activeChanged.connect(activate);
  bridge.output.connect((key, data) => getTerminal(key).term.write(data));
  window.searchActiveTerminal = query => { const item = terminals.get(activeKey); if (item && query) { item.search.findNext(query); item.term.focus(); } };
  window.clearActiveTerminal = () => { const item = terminals.get(activeKey); if (item) { item.term.clear(); item.term.focus(); } };
  window.adjustTerminalFontSize = delta => { const item = terminals.get(activeKey); if (item) { item.term.options.fontSize = Math.max(10, Math.min(22, item.term.options.fontSize + delta)); item.fit.fit(); bridge.resize(item.term.cols, item.term.rows); item.term.focus(); } };
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

    def emit_output(self, session_id: str, text: str) -> None:
        if not self.ready_for_output:
            self.pending_output.append((session_id, text))
            return
        self.output.emit(session_id, text)

    def set_active(self, session_id: str) -> None:
        if self.ready_for_output:
            self.activeChanged.emit(session_id)
        else:
            self.window.terminal_active = session_id

    @Slot()
    def ready(self) -> None:
        self.ready_for_output = True
        self.activeChanged.emit(self.window.terminal_active)
        for session_id, text in self.pending_output:
            self.output.emit(session_id, text)
        self.pending_output = []

    @Slot(str, str)
    def writeTo(self, session_id: str, text: str) -> None:
        self.window._write_terminal_to(session_id, text)

    @Slot(int, int)
    def resize(self, columns: int, rows: int) -> None:
        self.window._resize_terminal(columns, rows)

    @Slot(str)
    def copy(self, text: str) -> None:
        QApplication.clipboard().setText(text)

    @Slot(result=str)
    def paste(self) -> str:
        return QApplication.clipboard().text()


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


class GitStatusWorker(QThread):
    loaded = Signal(object, str)
    failed = Signal(str)

    def __init__(self, workspace: Path):
        super().__init__()
        self.workspace = workspace

    def run(self) -> None:
        try:
            self.loaded.emit(WorkspaceTools(self.workspace).git_snapshot(), str(self.workspace))
        except Exception as exc:
            self.failed.emit(str(exc))


class GitDiffWorker(QThread):
    loaded = Signal(str, str)
    failed = Signal(str)

    def __init__(self, workspace: Path, relative: str):
        super().__init__()
        self.workspace = workspace
        self.relative = relative

    def run(self) -> None:
        try:
            diff = WorkspaceTools(self.workspace).git_diff_file(self.relative)
            self.loaded.emit(self.relative, diff)
        except Exception as exc:
            self.failed.emit(str(exc))


class DiagnosticsWorker(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, workspace: Path):
        super().__init__()
        self.workspace = workspace

    def run(self) -> None:
        try:
            self.loaded.emit(collect_diagnostics(self.workspace))
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
        saved_sessions, saved_active_sessions, saved_terminal_history = self.workspace_state.load_sessions()
        self.sessions_by_workspace = self._restore_sessions(saved_sessions)
        self.active_session_ids = {
            str(Path(path).expanduser().resolve()): session_id
            for path, session_id in saved_active_sessions.items()
        }
        self.terminal_history = {
            session_id: value[-100_000:]
            for session_id, value in saved_terminal_history.items()
        }
        self._ensure_session_for_workspace()
        self.tools = WorkspaceTools(self.workspace)
        self.scan_worker = None
        self.scan_workers = []
        self.git_status_worker = None
        self.git_diff_worker = None
        self.git_entries = {}
        self.git_branch_name = ""
        self.git_available = False
        self.diagnostics_worker = None
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
        self.approval_pending = False
        self.setWindowTitle("Personal Agent — Workbench")
        self.resize(1360, 820)
        self.setStyleSheet(self._style())
        self._build_ui()
        self._refresh_files()
        self.change_timer = QTimer(self)
        self.change_timer.setInterval(2000)
        self.change_timer.timeout.connect(self._check_file_changes)
        self.change_timer.timeout.connect(self._check_git_status)
        self.change_timer.start()
        self._check_git_status()
        self._load_diagnostics()
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
        self.git_status_label = QLabel("Git 상태 확인 중…"); self.git_status_label.setObjectName("gitStatus"); self.git_status_label.setWordWrap(True); left_layout.addWidget(self.git_status_label)
        open_button = QPushButton("＋  작업 공간 추가"); open_button.setObjectName("primaryButton"); open_button.clicked.connect(self._choose_workspace); left_layout.addWidget(open_button)
        self.workspace_list = QListWidget(); self.workspace_list.setObjectName("workspaceList"); self.workspace_list.setAccessibleName("작업 공간 목록"); self.workspace_list.itemClicked.connect(self._select_workspace); self.workspace_list.itemActivated.connect(self._select_workspace); left_layout.addWidget(self.workspace_list, 1)
        remove_workspace = QPushButton("선택 작업 공간 제거"); remove_workspace.setObjectName("secondaryButton"); remove_workspace.clicked.connect(self._remove_workspace); left_layout.addWidget(remove_workspace)
        self._refresh_workspaces()
        left_layout.addWidget(self._title("사용량"))
        self.usage_label = QLabel("사용량 확인 중…"); self.usage_label.setObjectName("infoCard"); self.usage_label.setWordWrap(True); left_layout.addWidget(self.usage_label)
        self.primary_usage_bar = QProgressBar(); self.primary_usage_bar.setObjectName("usageBar"); self.primary_usage_bar.setRange(0, 100); self.primary_usage_bar.setTextVisible(True); self.primary_usage_bar.hide(); left_layout.addWidget(self.primary_usage_bar)
        self.secondary_usage_bar = QProgressBar(); self.secondary_usage_bar.setObjectName("usageBar"); self.secondary_usage_bar.setRange(0, 100); self.secondary_usage_bar.setTextVisible(True); self.secondary_usage_bar.hide(); left_layout.addWidget(self.secondary_usage_bar)
        refresh_usage = QPushButton("사용량 새로고침"); refresh_usage.setObjectName("secondaryButton"); refresh_usage.clicked.connect(self._load_usage); left_layout.addWidget(refresh_usage)
        self.reset_credit_button = QPushButton("사용량 초기화권 사용"); self.reset_credit_button.setObjectName("primaryButton"); self.reset_credit_button.clicked.connect(self._consume_reset_credit); self.reset_credit_button.setEnabled(False); left_layout.addWidget(self.reset_credit_button)
        left_layout.addWidget(self._title("진단"))
        self.diagnostic_label = QLabel("진단 확인 중…"); self.diagnostic_label.setObjectName("diagnosticCard"); self.diagnostic_label.setWordWrap(True); left_layout.addWidget(self.diagnostic_label)
        refresh_diagnostics = QPushButton("진단 새로고침"); refresh_diagnostics.setObjectName("secondaryButton"); refresh_diagnostics.clicked.connect(self._load_diagnostics); left_layout.addWidget(refresh_diagnostics)

        center = QWidget(); center.setObjectName("centerPanel"); center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(16, 18, 16, 16); center_layout.setSpacing(10)
        center_header = QWidget(); center_header_layout = QHBoxLayout(center_header); center_header_layout.setContentsMargins(0, 0, 0, 0)
        center_header_layout.addWidget(self._title("터미널"))
        self.workspace_tabs = QTabBar(); self.workspace_tabs.setObjectName("workspaceTabs"); self.workspace_tabs.setExpanding(False); self.workspace_tabs.setMovable(False); self.workspace_tabs.currentChanged.connect(self._select_workspace_tab); center_header_layout.addWidget(self.workspace_tabs)
        center_header_layout.addStretch(1)
        self.restart_button = QPushButton("↻"); self.restart_button.setObjectName("iconButton"); self.restart_button.setAccessibleName("Codex 세션 재시작"); self.restart_button.setFixedSize(38, 38); self.restart_button.setToolTip("현재 작업 공간의 Codex 세션을 다시 시작합니다."); self.restart_button.clicked.connect(self._restart_terminal); center_header_layout.addWidget(self.restart_button)
        self.stop_button = QPushButton("■"); self.stop_button.setObjectName("dangerIconButton"); self.stop_button.setAccessibleName("Codex 세션 중지"); self.stop_button.setFixedSize(38, 38); self.stop_button.setToolTip("현재 작업 공간의 Codex 세션을 중지합니다."); self.stop_button.clicked.connect(self._stop_active_terminal); center_header_layout.addWidget(self.stop_button)
        self.search_terminal_button = QPushButton("⌕"); self.search_terminal_button.setObjectName("iconButton"); self.search_terminal_button.setAccessibleName("터미널 검색"); self.search_terminal_button.setFixedSize(38, 38); self.search_terminal_button.setToolTip("터미널 출력에서 검색합니다."); self.search_terminal_button.clicked.connect(self._search_terminal); center_header_layout.addWidget(self.search_terminal_button)
        self.clear_terminal_button = QPushButton("지우기"); self.clear_terminal_button.setObjectName("compactButton"); self.clear_terminal_button.setFixedSize(68, 34); self.clear_terminal_button.setToolTip("현재 터미널 화면을 지웁니다."); self.clear_terminal_button.clicked.connect(self._clear_terminal); center_header_layout.addWidget(self.clear_terminal_button)
        self.decrease_font_button = QPushButton("A−"); self.decrease_font_button.setObjectName("iconButton"); self.decrease_font_button.setAccessibleName("터미널 글자 작게"); self.decrease_font_button.setFixedSize(38, 38); self.decrease_font_button.setToolTip("터미널 글자를 작게 합니다."); self.decrease_font_button.clicked.connect(lambda: self._adjust_terminal_font_size(-1)); center_header_layout.addWidget(self.decrease_font_button)
        self.increase_font_button = QPushButton("A+"); self.increase_font_button.setObjectName("iconButton"); self.increase_font_button.setAccessibleName("터미널 글자 크게"); self.increase_font_button.setFixedSize(38, 38); self.increase_font_button.setToolTip("터미널 글자를 크게 합니다."); self.increase_font_button.clicked.connect(lambda: self._adjust_terminal_font_size(1)); center_header_layout.addWidget(self.increase_font_button)
        center_layout.addWidget(center_header)
        session_header = QWidget(); session_header_layout = QHBoxLayout(session_header); session_header_layout.setContentsMargins(0, 0, 0, 0); session_header_layout.setSpacing(6)
        session_header_layout.addWidget(self._title("세션"))
        self.session_tabs = QTabBar(); self.session_tabs.setObjectName("sessionTabs"); self.session_tabs.setExpanding(False); self.session_tabs.setMovable(False); self.session_tabs.currentChanged.connect(self._select_session_tab); session_header_layout.addWidget(self.session_tabs, 1)
        self.new_session_button = QPushButton("＋"); self.new_session_button.setObjectName("iconButton"); self.new_session_button.setFixedSize(34, 34); self.new_session_button.setToolTip("현재 작업 공간에 새 Codex 세션을 만듭니다."); self.new_session_button.clicked.connect(self._new_session); session_header_layout.addWidget(self.new_session_button)
        self.rename_session_button = QPushButton("이름"); self.rename_session_button.setObjectName("compactButton"); self.rename_session_button.setFixedSize(56, 34); self.rename_session_button.clicked.connect(self._rename_session); session_header_layout.addWidget(self.rename_session_button)
        self.close_session_button = QPushButton("×"); self.close_session_button.setObjectName("dangerIconButton"); self.close_session_button.setFixedSize(34, 34); self.close_session_button.setToolTip("현재 세션을 닫습니다."); self.close_session_button.clicked.connect(self._close_session); session_header_layout.addWidget(self.close_session_button)
        center_layout.addWidget(session_header)
        self.session_notice = QLabel(); self.session_notice.setObjectName("sessionNotice"); self.session_notice.setWordWrap(True); center_layout.addWidget(self.session_notice)
        self.activity = QLabel("● Codex CLI 터미널")
        self.activity.setObjectName("activity")
        center_layout.addWidget(self.activity)
        self.approval_label = QLabel("승인 대기 없음 · Codex CLI 화면에서 승인 요청을 확인합니다.")
        self.approval_label.setObjectName("approvalNotice")
        self.approval_label.setWordWrap(True)
        center_layout.addWidget(self.approval_label)
        self.approval_focus_button = QPushButton("승인 요청으로 이동")
        self.approval_focus_button.setObjectName("compactButton")
        self.approval_focus_button.clicked.connect(self._focus_terminal_for_approval)
        self.approval_focus_button.hide()
        center_layout.addWidget(self.approval_focus_button)
        self.terminal = QWebEngineView()
        self.terminal.setAccessibleName("Codex 터미널")
        self.terminal.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.terminal.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
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
        self.file_search = QLineEdit(); self.file_search.setObjectName("searchBox"); self.file_search.setAccessibleName("파일 검색"); self.file_search.setClearButtonEnabled(True); self.file_search.setPlaceholderText("파일 이름 검색…"); self.file_search.textChanged.connect(self._filter_files); right_layout.addWidget(self.file_search)
        self.files = QTreeWidget(); self.files.setObjectName("fileTree"); self.files.setAccessibleName("작업 공간 파일 목록"); self.files.setHeaderHidden(True); self.files.setUniformRowHeights(True); self.files.itemClicked.connect(self._preview_file); self.files.itemActivated.connect(self._preview_file); self.files.itemExpanded.connect(self._expand_folder); right_layout.addWidget(self.files, 1)
        refresh = QPushButton("파일 새로고침"); refresh.setObjectName("secondaryButton"); refresh.clicked.connect(self._refresh_files); right_layout.addWidget(refresh)
        right_layout.addWidget(self._title("변경 사항"))
        self.changed_list = QListWidget(); self.changed_list.setObjectName("changedList"); self.changed_list.setMinimumHeight(72); self.changed_list.setMaximumHeight(150); self.changed_list.itemClicked.connect(self._preview_changed_item); right_layout.addWidget(self.changed_list)
        right_layout.addWidget(self._title("미리보기"))
        self.preview_title = QLabel("파일을 선택하세요"); self.preview_title.setObjectName("previewTitle"); right_layout.addWidget(self.preview_title)
        self.preview = QPlainTextEdit(); self.preview.setObjectName("preview"); self.preview.setReadOnly(True); self.preview.setPlaceholderText("파일을 선택하면 미리보기가 표시됩니다."); right_layout.addWidget(self.preview, 1)
        self.open_file_button = QPushButton("기본 앱에서 열기"); self.open_file_button.setObjectName("secondaryButton"); self.open_file_button.setEnabled(False); self.open_file_button.clicked.connect(self._open_preview_file); right_layout.addWidget(self.open_file_button)
        self.git_diff_button = QPushButton("Git Diff 보기"); self.git_diff_button.setObjectName("secondaryButton"); self.git_diff_button.setEnabled(False); self.git_diff_button.clicked.connect(self._show_git_diff); right_layout.addWidget(self.git_diff_button)
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
        self._schedule_terminal_start()
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
        self._schedule_terminal_start()
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
        self._schedule_terminal_start()
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
            history = self.terminal_history.get(session_id)
            if history:
                self.terminal_bridge.emit_output(session_id, history)
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

    def _schedule_terminal_start(self) -> None:
        QTimer.singleShot(0, self._start_terminal)

    def _restart_terminal(self) -> None:
        session = self.terminal_sessions.get(self._ensure_session_for_workspace())
        if session is not None:
            session.stop()
        self._schedule_terminal_start()

    def _search_terminal(self) -> None:
        query, accepted = QInputDialog.getText(self, "터미널 검색", "검색어")
        if accepted and query:
            self.terminal.page().runJavaScript(f"window.searchActiveTerminal({json.dumps(query, ensure_ascii=False)});")

    def _clear_terminal(self) -> None:
        self.terminal.page().runJavaScript("window.clearActiveTerminal();")

    def _adjust_terminal_font_size(self, delta: int) -> None:
        self.terminal.page().runJavaScript(f"window.adjustTerminalFontSize({int(delta)});")

    def _stop_active_terminal(self) -> None:
        session = self.terminal_sessions.get(self.terminal_active)
        if session is None:
            return
        session.stop()
        self.stop_button.setEnabled(False)
        self.restart_button.setEnabled(True)
        self.activity.setText("● 중지됨 · 재시작 가능")
        self.statusBar().showMessage("Codex CLI 세션을 중지했습니다.")

    def _focus_terminal_for_approval(self) -> None:
        self.terminal.setFocus()
        self.statusBar().showMessage("중앙 Codex CLI 화면에서 승인 요청을 확인하세요.")

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
                self.terminal_history[session_id] = (self.terminal_history.get(session_id, "") + output)[-100_000:]
                lowered = output.lower()
                if any(marker in lowered for marker in ("approve", "allow", "y/n", "승인", "허용")):
                    self.approval_pending = True
                    self.approval_label.setText("⚠ 승인 대기 · 중앙 Codex CLI 화면에서 요청을 확인하세요.")
                    self.approval_label.setProperty("pending", True)
                    self.approval_focus_button.show()
                    self.approval_label.style().unpolish(self.approval_label)
                    self.approval_label.style().polish(self.approval_label)
                self.terminal_bridge.emit_output(session_id, output)
            if not session.alive() and session_id == self.terminal_active:
                self.stop_button.setEnabled(False)
                self.restart_button.setEnabled(True)
                self.activity.setText("● 세션 종료됨 · 재시작 가능")
                self.statusBar().showMessage("Codex CLI 세션 종료됨")

    def _write_terminal(self, text: str) -> None:
        self._write_terminal_to(self.terminal_active, text)

    def _write_terminal_to(self, session_id: str, text: str) -> None:
        session = self.terminal_sessions.get(session_id)
        if session is not None and session.alive():
            try:
                session.write(text)
            except OSError as exc:
                self.statusBar().showMessage(f"Codex CLI 입력 실패: {exc}")

    def _resize_terminal(self, columns: int, rows: int) -> None:
        session = self.terminal_sessions.get(self.terminal_active)
        if session is not None:
            session.resize(columns, rows)

    @staticmethod
    def _worker_is_running(worker) -> bool:
        if worker is None:
            return False
        try:
            return worker.isRunning()
        except RuntimeError:
            # Qt may delete the C++ worker before Python clears its reference.
            return False

    def closeEvent(self, event) -> None:
        if self.change_timer is not None:
            self.change_timer.stop()
        if self.terminal_timer is not None:
            self.terminal_timer.stop()
        for session in self.terminal_sessions.values():
            session.stop()
        workers = [
            self.scan_worker,
            self.change_scan_worker,
            self.git_status_worker,
            self.git_diff_worker,
            self.diagnostics_worker,
            self.usage_worker,
            self.reset_credit_worker,
            *self.scan_workers,
        ]
        seen = set()
        for worker in workers:
            if worker is None or id(worker) in seen:
                continue
            seen.add(id(worker))
            if self._worker_is_running(worker):
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
            self.workspace_state.save(
                self.workspaces,
                self.workspace,
                sessions,
                self.active_session_ids,
                self.terminal_history,
            )
        except OSError as exc:
            self.statusBar().showMessage(f"작업 공간 목록을 저장하지 못했습니다: {exc}")

    def _load_usage(self) -> None:
        self.usage_label.setText("사용량 확인 중…")
        self.usage_worker = UsageWorker()
        self.usage_worker.loaded.connect(self._usage_loaded)
        self.usage_worker.failed.connect(lambda message: self.usage_label.setText(f"사용량을 불러오지 못했습니다.\n{message}"))
        self.usage_worker.start()

    def _load_diagnostics(self) -> None:
        if self._worker_is_running(self.diagnostics_worker):
            return
        self.diagnostic_label.setText("진단 확인 중…")
        self.diagnostics_worker = DiagnosticsWorker(self.workspace)
        self.diagnostics_worker.loaded.connect(self._diagnostics_loaded)
        self.diagnostics_worker.failed.connect(lambda message: self.diagnostic_label.setText(f"진단 실패\n{message}"))
        self.diagnostics_worker.finished.connect(self._diagnostics_finished)
        self.diagnostics_worker.start()

    def _diagnostics_finished(self) -> None:
        if self.diagnostics_worker is not None:
            self.diagnostics_worker.deleteLater()
            self.diagnostics_worker = None

    def _diagnostics_loaded(self, diagnostics: dict[str, str]) -> None:
        self.diagnostic_label.setText("\n".join(f"{label}  {value}" for label, value in diagnostics.items()))

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
        if self._worker_is_running(self.change_scan_worker):
            return
        self.change_scan_worker = FileChangeWorker(self.workspace)
        self.change_scan_worker.scanned.connect(self._file_changes_scanned)
        self.change_scan_worker.failed.connect(lambda message: self.statusBar().showMessage(f"파일 변경 확인 실패: {message}"))
        self.change_scan_worker.finished.connect(self._change_scan_finished)
        self.change_scan_worker.start()

    def _check_git_status(self) -> None:
        if self._worker_is_running(self.git_status_worker):
            return
        self.git_status_worker = GitStatusWorker(self.workspace)
        self.git_status_worker.loaded.connect(self._git_status_loaded)
        self.git_status_worker.failed.connect(lambda message: self.git_status_label.setText(f"Git 상태 확인 실패\n{message}"))
        self.git_status_worker.finished.connect(self._git_status_finished)
        self.git_status_worker.start()

    def _git_status_finished(self) -> None:
        if self.git_status_worker is not None:
            self.git_status_worker.deleteLater()
            self.git_status_worker = None

    def _git_status_loaded(self, snapshot: dict, workspace: str) -> None:
        if workspace != str(self.workspace):
            return
        self.git_available = bool(snapshot.get("available"))
        self.git_branch_name = snapshot.get("branch", "")
        self.git_entries = snapshot.get("entries", {}) if self.git_available else {}
        if not self.git_available:
            self.git_status_label.setText("Git 저장소 아님")
        else:
            staged = sum(code[0] not in {" ", "?"} for code in self.git_entries.values())
            unstaged = sum(code[1] not in {" ", "?"} or code == "??" for code in self.git_entries.values())
            self.git_status_label.setText(
                f"브랜치  {self.git_branch_name}\n"
                f"변경 {len(self.git_entries):,}개 · staged {staged:,} · unstaged {unstaged:,}"
            )
        self._refresh_changed_list()
        self._update_git_diff_button()

    def _update_git_diff_button(self) -> None:
        self.git_diff_button.setEnabled(bool(self.preview_relative and self.preview_relative in self.git_entries))

    def _show_git_diff(self) -> None:
        relative = self.preview_relative
        if not relative or relative not in self.git_entries:
            return
        if self._worker_is_running(self.git_diff_worker):
            return
        self.git_diff_button.setEnabled(False)
        self.git_diff_worker = GitDiffWorker(self.workspace, relative)
        self.git_diff_worker.loaded.connect(self._git_diff_loaded)
        self.git_diff_worker.failed.connect(lambda message: self.statusBar().showMessage(f"Git Diff 확인 실패: {message}"))
        self.git_diff_worker.finished.connect(self._git_diff_finished)
        self.git_diff_worker.start()

    def _git_diff_finished(self) -> None:
        if self.git_diff_worker is not None:
            self.git_diff_worker.deleteLater()
            self.git_diff_worker = None

    def _git_diff_loaded(self, relative: str, diff: str) -> None:
        if relative != self.preview_relative:
            return
        self.preview_title.setText(f"Git Diff · {Path(relative).name}")
        self.preview.setPlainText(diff or "Git 기준 변경 내용이 없습니다.")
        self._update_git_diff_button()

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
        self._update_git_diff_button()
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
            git_code = self.git_entries.get(relative)
            git_kind = ""
            if git_code:
                if git_code == "??":
                    git_kind = " · Git 미추적"
                elif git_code[0] != " " and git_code[1] != " ":
                    git_kind = " · Git staged+수정"
                elif git_code[0] != " ":
                    git_kind = " · Git staged"
                elif git_code[1] != " ":
                    git_kind = " · Git 수정"
            item = QListWidgetItem(f"{kind}{git_kind}  {relative}")
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
        self.git_diff_button.setEnabled(False)
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
        self._schedule_terminal_start()
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
            self.git_diff_button.setEnabled(False)
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
            self._update_git_diff_button()
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
            self.git_diff_button.setEnabled(False)
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
        QMainWindow, QWidget { background:#0b1220; color:#e5edf5; font-size:13px; }
        QWidget#sidebar, QWidget#filePanel { background:#111b2b; }
        QWidget#centerPanel { background:#0d1726; }
        QSplitter::handle { background:#26364b; width:6px; }
        QSplitter::handle:hover { background:#4c7ca0; }
        QLabel#brand { color:#f5f9ff; font-size:19px; font-weight:800; letter-spacing:1.6px; padding:2px 0; }
        QLabel#brandSubtitle { color:#8fa5ba; font-size:10px; font-weight:700; letter-spacing:1.2px; padding-bottom:8px; }
        QLabel#section { color:#9bb4c9; font-size:11px; font-weight:800; letter-spacing:1.1px; padding-top:7px; padding-bottom:2px; }
        QLabel#workspacePath, QLabel#gitStatus, QLabel#diagnosticCard { color:#c8d6e4; background:#162337; border:1px solid #2b4159; border-radius:9px; padding:10px; }
        QLabel#workspacePath { font-size:12px; }
        QLabel#activity { color:#dfffee; background:#15523f; border:1px solid #48b58d; border-radius:9px; padding:9px 12px; font-weight:750; }
        QLabel#approvalNotice { color:#b9c9d8; background:#162337; border:1px solid #2b4159; border-radius:9px; padding:8px 11px; }
        QLabel#approvalNotice[pending="true"] { color:#ffe9b0; background:#48381d; border-color:#b08a3d; }
        QLabel#sessionNotice { color:#ffe1a0; background:#3c311d; border:1px solid #806532; border-radius:9px; padding:8px 11px; }
        QLabel#infoCard { color:#cfdeeb; background:#162337; border:1px solid #2b4159; border-radius:9px; padding:11px; }
        QLabel#previewTitle { color:#d2e0ec; font-size:12px; font-weight:650; padding:2px; }
        QLabel#hint { color:#8fa5ba; font-size:11px; padding-top:2px; }
        QLabel { color:#d5e1ec; }
        QLineEdit, QListWidget, QTreeWidget, QPlainTextEdit, QWebEngineView { background:#0a1321; border:1px solid #304963; border-radius:9px; }
        QWebEngineView { border-color:#3a5b77; }
        QLineEdit { padding:10px 12px; color:#edf5fb; selection-background-color:#245a67; }
        QLineEdit:focus, QPlainTextEdit:focus, QListWidget:focus, QTreeWidget:focus, QWebEngineView:focus { border:2px solid #62c8b0; }
        QListWidget, QTreeWidget, QPlainTextEdit { padding:7px; }
        QListWidget::item, QTreeWidget::item { padding:8px 9px; border-radius:6px; }
        QListWidget::item:hover, QTreeWidget::item:hover { background:#1b3449; }
        QListWidget::item:selected, QTreeWidget::item:selected { background:#1d5960; color:#f4fffb; border-left:3px solid #76d5ae; }
        QPushButton { min-height:38px; background:#1b3044; border:1px solid #41617a; border-radius:8px; padding:0 13px; color:#e6f0f7; font-weight:650; }
        QPushButton:hover { background:#294a61; border-color:#78b6c5; }
        QPushButton:focus { border:2px solid #62c8b0; }
        QPushButton:pressed { background:#142638; }
        QPushButton:disabled { background:#162333; border-color:#2c3e52; color:#71859a; }
        QPushButton#primaryButton { background:#287b68; border-color:#62cda8; color:#f1fff9; }
        QPushButton#primaryButton:hover { background:#359b82; border-color:#9be8c8; }
        QPushButton#compactButton { min-height:36px; background:#1a2e41; border:1px solid #3e5d76; border-radius:8px; padding:0 11px; color:#d2e1eb; font-size:12px; font-weight:650; }
        QPushButton#compactButton:hover { background:#294a61; border-color:#82bdc9; color:#f1fffb; }
        QPushButton#compactButton:disabled { background:#162333; border-color:#2c3e52; color:#71859a; }
        QPushButton#secondaryButton { background:#1a2e41; border-color:#3e5d76; color:#d2e1eb; }
        QPushButton#secondaryButton:hover { background:#294a61; border-color:#82bdc9; color:#f1fffb; }
        QPushButton#dangerButton { background:#51313e; border-color:#a7677b; color:#ffe0e6; }
        QPushButton#dangerButton:hover { background:#704252; border-color:#dc92a4; color:#ffffff; }
        QPushButton#iconButton { min-height:38px; background:#1a2e41; border:1px solid #3e5d76; border-radius:8px; padding:0; color:#d2e1eb; font-size:15px; font-weight:700; }
        QPushButton#iconButton:hover { background:#294a61; border-color:#82bdc9; color:#ffffff; }
        QPushButton#dangerIconButton { min-height:38px; background:#51313e; border:1px solid #a7677b; border-radius:8px; padding:0; color:#ffe0e6; font-size:11px; }
        QPushButton#dangerIconButton:hover { background:#704252; border-color:#dc92a4; color:#ffffff; }
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
    app = QApplication([]); app.setApplicationName("개인 에이전트"); app.setStyle("Fusion")
    font_family = "Segoe UI" if sys.platform == "win32" else "Helvetica Neue" if sys.platform == "darwin" else "Noto Sans"
    app.setFont(QFont(font_family, 12))
    window = MainWindow(args.workspace)
    window.show()
    # Windows ConPTY startup can briefly block while Codex CLI initializes.
    # Let Qt paint the window before starting the external terminal process.
    QTimer.singleShot(0, window._start_terminal)
    raise SystemExit(app.exec())
