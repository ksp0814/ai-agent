from __future__ import annotations

import argparse
from datetime import datetime
from dataclasses import dataclass
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
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

from .tools import IGNORED_DIRECTORIES, WorkspaceTools
from .model_catalog import consume_codex_reset_credit, fetch_codex_rate_limits
from .session_state import WorkspaceStateStore
from .terminal import TerminalSession

try:
    from PySide6.QtCore import QThread, QTimer, Qt, Signal, QObject, QUrl, Slot, QPoint, QEvent, QSize
    from PySide6.QtGui import QDesktopServices, QFont, QKeySequence, QShortcut
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
        QFrame,
        QPushButton,
        QSizePolicy,
        QSplitter,
        QStackedWidget,
        QStyle,
        QStatusBar,
        QToolButton,
        QTabBar,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise SystemExit("PySide6가 필요합니다. `python -m pip install -e '.[desktop]'` 실행 후 다시 시도하세요.") from exc


class CleanStatusBar(QStatusBar):
    """Keep the bottom bar reserved for persistent usage information."""

    def showMessage(self, message: str, timeout: int = 0) -> None:
        return


class AdaptiveTabBar(QTabBar):
    """Size each top tab from its label while keeping the tab strip compact."""

    def tabSizeHint(self, index: int) -> QSize:
        hint = super().tabSizeHint(index)
        text_width = self.fontMetrics().horizontalAdvance(self.tabText(index))
        icon_width = self.iconSize().width() + 6 if not self.tabIcon(index).isNull() else 0
        close_width = 26 if self.tabButton(index, QTabBar.ButtonPosition.RightSide) else 0
        width = max(76, min(420, text_width + icon_width + close_width + 18))
        return QSize(width, max(38, hint.height()))


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
    const isTerminalResponse = data => /^(?:\\x1b\\[[?0-9;]*c|\\x1b\\][0-9]+;rgb:[0-9a-f/]+\\x1b\\\\)+$/i.test(data);
    term.onData(data => {
      if (activeKey === key && !isTerminalResponse(data)) bridge.writeTo(key, data);
    });
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

MAX_PREVIEW_BYTES = 2 * 1024 * 1024
MAX_TERMINAL_HISTORY = 100_000


def _looks_binary(data: bytes) -> bool:
    if not data:
        return False
    if b"\x00" in data:
        return True
    control_bytes = sum(value < 9 or 14 <= value < 32 for value in data)
    return control_bytes / len(data) > 0.1


def _capture_file_baseline(path: Path, captured_bytes: int = 0) -> tuple[FileBaseline, int]:
    stat = path.stat()
    with path.open("rb") as handle:
        sample = handle.read(8192)
    is_binary = _looks_binary(sample)
    content = None
    captured = 0
    if not is_binary and stat.st_size <= 65_536 and captured_bytes + stat.st_size <= 2 * 1_048_576:
        content = path.read_bytes()
        captured = stat.st_size
    return FileBaseline(stat.st_mtime_ns, stat.st_size, content, is_binary), captured


def _read_preview(path: Path) -> tuple[str, bool, bool, int]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        data = handle.read(MAX_PREVIEW_BYTES + 1)
    truncated = len(data) > MAX_PREVIEW_BYTES
    data = data[:MAX_PREVIEW_BYTES]
    if _looks_binary(data[:8192]):
        return "", True, truncated, size
    return data.decode("utf-8", errors="replace"), False, truncated, size


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

    def __init__(self, window, pane_id: str = "main"):
        super().__init__(window)
        self.window = window
        self.pane_id = pane_id
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
            self.window.terminal_pane_sessions[self.pane_id] = session_id

    @Slot()
    def ready(self) -> None:
        self.ready_for_output = True
        self.activeChanged.emit(self.window.terminal_pane_sessions.get(self.pane_id) or self.window.terminal_active)
        for session_id, text in self.pending_output:
            self.output.emit(session_id, text)
        self.pending_output = []

    @Slot(str, str)
    def writeTo(self, session_id: str, text: str) -> None:
        self.window._write_terminal_to(session_id, text)

    @Slot(int, int)
    def resize(self, columns: int, rows: int) -> None:
        self.window._resize_terminal_for_pane(self.pane_id, columns, rows)

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
    scanned = Signal(object, object, object, str)
    failed = Signal(str)

    def __init__(self, workspace: Path):
        super().__init__()
        self.workspace = workspace

    def run(self) -> None:
        try:
            files, directories, metadata = WorkspaceTools(self.workspace).scan_tree_snapshot(file_limit=100_000, directory_limit=10_000)
            self.scanned.emit(files, directories, metadata, str(self.workspace))
        except Exception as exc:
            self.failed.emit(str(exc))


class FileBaselineWorker(QThread):
    loaded = Signal(object, str)
    failed = Signal(str)

    def __init__(self, workspace: Path, files: list[str]):
        super().__init__()
        self.workspace = workspace
        self.files = files

    def run(self) -> None:
        try:
            baseline = {}
            captured_bytes = 0
            for relative in self.files:
                try:
                    snapshot, captured = _capture_file_baseline(self.workspace / relative, captured_bytes)
                except OSError:
                    continue
                baseline[relative] = snapshot
                captured_bytes += captured
            self.loaded.emit(baseline, str(self.workspace))
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
        self.terminal_layout_by_workspace = self.workspace_state.load_terminal_layout()
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
        self.baseline_worker = None
        self.pending_baseline = None
        self.git_entries = {}
        self.git_branch_name = ""
        self.git_available = False
        self.usage_worker = None
        self.usage_snapshot = {}
        self.usage_reset_count = 0
        self.reset_credit_worker = None
        self.reset_credit_id = ""
        self.terminal_sessions = {}
        self.terminal_active = ""
        self.terminal_pane_sessions = {"main": "", "split": ""}
        self.terminal_timer = None
        self.preview_path = None
        self.file_baselines = {}
        self.changed_files = set()
        self.rollback_trash_root = Path.home() / ".personal-agent" / "rollback-trash"
        self.change_timer = None
        self.git_status_timer = None
        self.change_scan_worker = None
        self.all_files = []
        self.all_directories = []
        self.preview_relative = None
        self.open_file_tabs = {}
        self.active_file_relative = None
        self.open_files_by_workspace = {}
        self.active_files_by_workspace = {}
        self.setWindowTitle("Personal Agent — Workbench")
        self.resize(1360, 820)
        self.setStyleSheet(self._style())
        self._build_ui()
        self.quick_open_shortcut = QShortcut(QKeySequence("Ctrl+P"), self)
        self.quick_open_shortcut.activated.connect(self._quick_open)
        if sys.platform == "darwin":
            self.quick_open_macos_shortcut = QShortcut(QKeySequence("Meta+P"), self)
            self.quick_open_macos_shortcut.activated.connect(self._quick_open)
        self._refresh_files()
        self.change_timer = QTimer(self)
        self.change_timer.setInterval(30_000)
        self.change_timer.timeout.connect(self._check_file_changes)
        self.change_timer.start()
        self.git_status_timer = QTimer(self)
        self.git_status_timer.setInterval(30_000)
        self.git_status_timer.timeout.connect(self._check_git_status)
        self.git_status_timer.start()
        self.usage_timer = QTimer(self)
        self.usage_timer.setInterval(5 * 60 * 1000)
        self.usage_timer.timeout.connect(self._load_usage)
        self._check_git_status()
        self._load_usage()
        self.usage_timer.start()
        self._restore_terminal_layout()

    def _build_ui(self) -> None:
        root = QSplitter()
        root.setChildrenCollapsible(False)
        self.setCentralWidget(root)

        left = QWidget(); left.setObjectName("sidebar"); left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(14, 16, 14, 14); left_layout.setSpacing(8)
        brand = QLabel("PERSONAL AGENT"); brand.setObjectName("brand"); left_layout.addWidget(brand)
        brand_subtitle = QLabel("LOCAL CODING WORKBENCH"); brand_subtitle.setObjectName("brandSubtitle"); left_layout.addWidget(brand_subtitle)
        left_layout.addWidget(self._title("프로젝트"))
        open_button = QPushButton("＋  작업 공간 추가"); open_button.setObjectName("primaryButton"); open_button.clicked.connect(self._choose_workspace); left_layout.addWidget(open_button)
        worktree_button = QPushButton("＋  새 Git worktree"); worktree_button.setObjectName("secondaryButton"); worktree_button.setToolTip("현재 Git 저장소에서 독립 작업 공간을 만듭니다."); worktree_button.clicked.connect(self._create_worktree); left_layout.addWidget(worktree_button)
        self.workspace_list = QListWidget(); self.workspace_list.setObjectName("workspaceList"); self.workspace_list.setAccessibleName("작업 공간 목록"); self.workspace_list.currentItemChanged.connect(self._workspace_selection_changed); self.workspace_list.itemClicked.connect(self._select_workspace_item); self.workspace_list.itemActivated.connect(self._select_workspace_item); left_layout.addWidget(self.workspace_list, 1)
        remove_workspace = QPushButton("선택 작업 공간 제거"); remove_workspace.setObjectName("secondaryButton"); remove_workspace.clicked.connect(self._remove_workspace); left_layout.addWidget(remove_workspace)
        self._refresh_workspaces()
        self.usage_label = QLabel("사용량 확인 중…", left); self.usage_label.setObjectName("infoCard"); self.usage_label.setWordWrap(True); self.usage_label.setToolTip("Codex 사용량은 5분마다 자동으로 갱신됩니다."); self.usage_label.hide()
        self.primary_usage_bar = QProgressBar(left); self.primary_usage_bar.setObjectName("usageBar"); self.primary_usage_bar.setRange(0, 100); self.primary_usage_bar.setTextVisible(True); self.primary_usage_bar.hide()
        self.secondary_usage_bar = QProgressBar(left); self.secondary_usage_bar.setObjectName("usageBar"); self.secondary_usage_bar.setRange(0, 100); self.secondary_usage_bar.setTextVisible(True); self.secondary_usage_bar.hide()
        self.reset_credit_button = QPushButton("사용량 초기화권 사용", left); self.reset_credit_button.setObjectName("primaryButton"); self.reset_credit_button.clicked.connect(self._consume_reset_credit); self.reset_credit_button.setEnabled(False); self.reset_credit_button.hide()

        center = QWidget(); center.setObjectName("centerPanel"); center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(10, 10, 10, 10); center_layout.setSpacing(8)
        center_header = QWidget(); center_header.setObjectName("terminalToolbar"); center_header_layout = QHBoxLayout(center_header); center_header_layout.setContentsMargins(4, 2, 4, 2); center_header_layout.setSpacing(6)
        center_header_layout.addWidget(self._title("터미널"))
        self.workspace_tabs = QTabBar(); self.workspace_tabs.setObjectName("workspaceTabs"); self.workspace_tabs.setAccessibleName("작업 공간 탭"); self.workspace_tabs.setExpanding(False); self.workspace_tabs.setMovable(False); self.workspace_tabs.currentChanged.connect(self._select_workspace_tab)
        center_header_layout.addWidget(self.workspace_tabs)
        center_header_layout.addStretch(1)
        self.restart_button = QPushButton("↻"); self.restart_button.setObjectName("iconButton"); self.restart_button.setAccessibleName("Codex 세션 재시작"); self.restart_button.setFixedSize(38, 38); self.restart_button.setToolTip("현재 작업 공간의 Codex 세션을 다시 시작합니다."); self.restart_button.clicked.connect(self._restart_terminal); center_header_layout.addWidget(self.restart_button)
        self.stop_button = QPushButton("■"); self.stop_button.setObjectName("dangerIconButton"); self.stop_button.setAccessibleName("Codex 세션 중지"); self.stop_button.setFixedSize(38, 38); self.stop_button.setToolTip("현재 작업 공간의 Codex 세션을 중지합니다."); self.stop_button.clicked.connect(self._stop_active_terminal); center_header_layout.addWidget(self.stop_button)
        self.search_terminal_button = QPushButton("⌕"); self.search_terminal_button.setObjectName("iconButton"); self.search_terminal_button.setAccessibleName("터미널 검색"); self.search_terminal_button.setFixedSize(38, 38); self.search_terminal_button.setToolTip("터미널 출력에서 검색합니다."); self.search_terminal_button.clicked.connect(self._search_terminal); center_header_layout.addWidget(self.search_terminal_button)
        self.clear_terminal_button = QPushButton("지우기"); self.clear_terminal_button.setObjectName("compactButton"); self.clear_terminal_button.setFixedSize(68, 34); self.clear_terminal_button.setToolTip("현재 터미널 화면을 지웁니다."); self.clear_terminal_button.clicked.connect(self._clear_terminal); center_header_layout.addWidget(self.clear_terminal_button)
        self.decrease_font_button = QPushButton("A−"); self.decrease_font_button.setObjectName("iconButton"); self.decrease_font_button.setAccessibleName("터미널 글자 작게"); self.decrease_font_button.setFixedSize(38, 38); self.decrease_font_button.setToolTip("터미널 글자를 작게 합니다."); self.decrease_font_button.clicked.connect(lambda: self._adjust_terminal_font_size(-1)); center_header_layout.addWidget(self.decrease_font_button)
        self.increase_font_button = QPushButton("A+"); self.increase_font_button.setObjectName("iconButton"); self.increase_font_button.setAccessibleName("터미널 글자 크게"); self.increase_font_button.setFixedSize(38, 38); self.increase_font_button.setToolTip("터미널 글자를 크게 합니다."); self.increase_font_button.clicked.connect(lambda: self._adjust_terminal_font_size(1)); center_header_layout.addWidget(self.increase_font_button)
        # Workspace/project context already lives in the left sidebar.
        # Keep legacy terminal controls alive for keyboard/API paths, but remove the redundant row.
        center_header.hide()
        session_header = QWidget(); session_header.setObjectName("tabHeader"); session_header.setMinimumHeight(44); session_header_layout = QHBoxLayout(session_header); session_header_layout.setContentsMargins(0, 0, 0, 0); session_header_layout.setSpacing(0)
        self.top_tabs = AdaptiveTabBar(); self.top_tabs.setObjectName("topTabs"); self.top_tabs.setAccessibleName("열린 세션 및 파일 탭"); self.top_tabs.setMinimumHeight(44); self.top_tabs.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred); self.top_tabs.setDocumentMode(True); self.top_tabs.setDrawBase(False); self.top_tabs.setExpanding(False); self.top_tabs.setMovable(False); self.top_tabs.setTabsClosable(True); self.top_tabs.setUsesScrollButtons(True); self.top_tabs.setElideMode(Qt.TextElideMode.ElideRight); self.top_tabs.currentChanged.connect(self._select_top_tab); self.top_tabs.tabBarClicked.connect(self._top_tab_clicked); self.top_tabs.tabCloseRequested.connect(self._close_top_tab); session_header_layout.addWidget(self.top_tabs)
        self.session_tabs = self.top_tabs
        self.file_view_tabs = self.top_tabs
        session_header_layout.addStretch(1)
        self.file_view_back_button = QPushButton("← 터미널"); self.file_view_back_button.setObjectName("compactButton"); self.file_view_back_button.clicked.connect(self._show_terminal_view); self.file_view_back_button.hide()
        self.new_session_button = QPushButton("＋"); self.new_session_button.setObjectName("iconButton"); self.new_session_button.setAccessibleName("새 Codex 세션"); self.new_session_button.setFixedSize(40, 40); self.new_session_button.setToolTip("현재 작업 공간에 새 Codex 세션을 만듭니다."); self.new_session_button.clicked.connect(self._new_session); session_header_layout.addWidget(self.new_session_button)
        self.rename_session_button = QPushButton("이름"); self.rename_session_button.setObjectName("compactButton"); self.rename_session_button.setFixedSize(60, 40); self.rename_session_button.clicked.connect(self._rename_session); session_header_layout.addWidget(self.rename_session_button)
        self.close_session_button = QPushButton("×"); self.close_session_button.setObjectName("dangerIconButton"); self.close_session_button.setFixedSize(40, 40); self.close_session_button.setToolTip("현재 세션을 닫습니다."); self.close_session_button.clicked.connect(self._close_session); session_header_layout.addWidget(self.close_session_button)
        self.rename_session_button.hide()
        self.close_session_button.hide()
        center_layout.addWidget(session_header)
        self.session_notice = QLabel(); self.session_notice.setObjectName("sessionNotice"); self.session_notice.setWordWrap(True); self.session_notice.hide()
        self.activity = QLabel("● Codex CLI 터미널")
        self.activity.setObjectName("activity")
        self.terminal_bridges = {}
        self.terminal = self._create_terminal_view("main")
        self.terminal_bridge = self.terminal_bridges["main"]
        self.split_terminal = self._create_terminal_view("split")
        self.split_terminal.hide()
        self.terminal_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.terminal_splitter.setChildrenCollapsible(False)
        self.terminal_splitter.addWidget(self.terminal)
        self.terminal_splitter.addWidget(self.split_terminal)
        self.terminal_splitter.setSizes([1, 0])
        self.center_stack = QStackedWidget()
        self.center_stack.addWidget(self.terminal_splitter)
        self.file_page = QWidget(); file_page_layout = QVBoxLayout(self.file_page); file_page_layout.setContentsMargins(0, 0, 0, 0); file_page_layout.setSpacing(8)
        self.file_view = QPlainTextEdit(); self.file_view.setObjectName("fileView"); self.file_view.setReadOnly(True); self.file_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap); file_page_layout.addWidget(self.file_view, 1)
        self.center_stack.addWidget(self.file_page)
        center_layout.addWidget(self.center_stack, 1)
        self._refresh_workspace_tabs()
        self._refresh_session_tabs()

        right = QWidget(); right.setObjectName("filePanel"); right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(14, 16, 14, 14); right_layout.setSpacing(8)
        right_header = QWidget(); right_header_layout = QHBoxLayout(right_header); right_header_layout.setContentsMargins(0, 0, 0, 0); right_header_layout.setSpacing(8)
        right_header_layout.addWidget(self._title("프로젝트 파일"))
        right_header_layout.addStretch(1)
        self.file_change_summary = QLabel("변경 없음"); self.file_change_summary.setObjectName("changeSummary")
        self.file_change_summary.hide()
        right_layout.addWidget(right_header)
        self.workspace_label = QLabel(str(self.workspace)); self.workspace_label.setObjectName("workspacePath"); self.workspace_label.setWordWrap(True); right_layout.addWidget(self.workspace_label)
        self.git_status_label = QLabel("Git 상태 확인 중…"); self.git_status_label.setObjectName("gitStatus"); self.git_status_label.setWordWrap(True); right_layout.addWidget(self.git_status_label)
        self.file_search = QLineEdit(); self.file_search.setObjectName("searchBox"); self.file_search.setAccessibleName("파일 검색"); self.file_search.setClearButtonEnabled(True); self.file_search.setPlaceholderText("파일 이름 검색…"); self.file_search.textChanged.connect(self._schedule_file_filter); right_layout.addWidget(self.file_search)
        self.file_filter_timer = QTimer(self)
        self.file_filter_timer.setSingleShot(True)
        self.file_filter_timer.setInterval(180)
        self.file_filter_timer.timeout.connect(lambda: self._filter_files(self.file_search.text()))
        self.files = QTreeWidget(); self.files.setObjectName("fileTree"); self.files.setAccessibleName("작업 공간 파일 목록"); self.files.setHeaderHidden(True); self.files.setUniformRowHeights(True); self.files.setIndentation(16); self.files.setMinimumHeight(180); self.files.itemClicked.connect(self._preview_file); self.files.itemActivated.connect(self._open_file_in_center); self.files.itemExpanded.connect(self._expand_folder); right_layout.addWidget(self.files, 1)
        self.changed_list = QListWidget(); self.changed_list.setObjectName("changedList"); self.changed_list.setMinimumHeight(100); self.changed_list.setMaximumHeight(260); self.changed_list.itemClicked.connect(self._preview_changed_item)
        self.changed_list.hide()
        self.preview_title = QLabel("파일을 선택하세요"); self.preview_title.setObjectName("previewTitle")
        self.preview_title.hide()
        self.preview = QPlainTextEdit(); self.preview.setObjectName("preview"); self.preview.setReadOnly(True); self.preview.setMinimumHeight(120); self.preview.setPlaceholderText("파일을 선택하면 미리보기가 표시됩니다."); self.preview.hide()
        self.open_file_button = QPushButton("기본 앱에서 열기"); self.open_file_button.setObjectName("secondaryButton"); self.open_file_button.setEnabled(False); self.open_file_button.clicked.connect(self._open_preview_file)
        self.git_diff_button = QPushButton("Git Diff 보기"); self.git_diff_button.setObjectName("secondaryButton"); self.git_diff_button.setEnabled(False); self.git_diff_button.clicked.connect(self._show_git_diff)
        file_actions = QWidget(right); file_actions_layout = QHBoxLayout(file_actions); file_actions_layout.setContentsMargins(0, 0, 0, 0); file_actions_layout.setSpacing(6); file_actions_layout.addWidget(self.open_file_button); file_actions_layout.addWidget(self.git_diff_button); file_actions.hide()
        change_actions = QWidget(right); change_actions_layout = QHBoxLayout(change_actions); change_actions_layout.setContentsMargins(0, 0, 0, 0)
        self.approve_change_button = QPushButton("변경 승인"); self.approve_change_button.setObjectName("primaryButton"); self.approve_change_button.setEnabled(False); self.approve_change_button.clicked.connect(self._approve_change); change_actions_layout.addWidget(self.approve_change_button)
        self.rollback_change_button = QPushButton("되돌리기"); self.rollback_change_button.setObjectName("dangerButton"); self.rollback_change_button.setEnabled(False); self.rollback_change_button.clicked.connect(self._rollback_change); change_actions_layout.addWidget(self.rollback_change_button)
        bulk_actions = QWidget(right); bulk_actions_layout = QHBoxLayout(bulk_actions); bulk_actions_layout.setContentsMargins(0, 0, 0, 0)
        self.approve_all_changes_button = QPushButton("변경 모두 승인"); self.approve_all_changes_button.setObjectName("secondaryButton"); self.approve_all_changes_button.setEnabled(False); self.approve_all_changes_button.clicked.connect(self._approve_all_changes); bulk_actions_layout.addWidget(self.approve_all_changes_button)
        self.rollback_all_changes_button = QPushButton("변경 모두 되돌리기"); self.rollback_all_changes_button.setObjectName("dangerButton"); self.rollback_all_changes_button.setEnabled(False); self.rollback_all_changes_button.clicked.connect(self._rollback_all_changes); bulk_actions_layout.addWidget(self.rollback_all_changes_button)
        self.open_trash_button = QPushButton("복구 보관함 열기"); self.open_trash_button.setObjectName("secondaryButton"); self.open_trash_button.clicked.connect(self._open_rollback_trash)
        change_actions.hide()
        bulk_actions.hide()
        self.open_trash_button.hide()

        root.addWidget(left); root.addWidget(center); root.addWidget(right); root.setSizes([270, 820, 340])
        root.setStretchFactor(0, 0); root.setStretchFactor(1, 1); root.setStretchFactor(2, 0)
        left.setMinimumWidth(250); right.setMinimumWidth(280)
        self.setStatusBar(CleanStatusBar())
        status_usage = QWidget(); status_usage.setObjectName("statusUsage"); status_usage.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.status_usage_widget = status_usage
        status_usage_layout = QHBoxLayout(status_usage); status_usage_layout.setContentsMargins(8, 0, 0, 0); status_usage_layout.setSpacing(7); status_usage_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.status_usage_icon = QLabel("◉"); self.status_usage_icon.setObjectName("statusUsageIcon"); status_usage_layout.addWidget(self.status_usage_icon)
        self.status_usage_bar = QProgressBar(); self.status_usage_bar.setObjectName("statusUsageBar"); self.status_usage_bar.setRange(0, 100); self.status_usage_bar.setValue(0); self.status_usage_bar.setTextVisible(False); self.status_usage_bar.setFixedSize(64, 8); status_usage_layout.addWidget(self.status_usage_bar)
        self.status_usage_label = QLabel("사용량 확인 중…"); self.status_usage_label.setObjectName("statusUsageLabel"); status_usage_layout.addWidget(self.status_usage_label)
        self.status_usage_refresh = QPushButton("↻"); self.status_usage_refresh.setObjectName("statusUsageRefreshButton"); self.status_usage_refresh.setAccessibleName("사용량 새로고침"); self.status_usage_refresh.setToolTip("사용량을 지금 새로고침합니다."); self.status_usage_refresh.setFixedSize(26, 24); self.status_usage_refresh.clicked.connect(self._refresh_usage_from_status); status_usage_layout.addWidget(self.status_usage_refresh)
        self.status_usage_refresh_timer = QTimer(self)
        self.status_usage_refresh_timer.setInterval(120)
        self.status_usage_refresh_timer.timeout.connect(self._animate_usage_refresh)
        self.status_usage_refresh_frames = ("↻", "↷", "↺", "↶")
        self.status_usage_refresh_frame = 0
        self._build_usage_popover(status_usage)
        self.statusBar().addWidget(status_usage)

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

    def _refresh_session_tabs(self, selected: Optional[tuple[str, str]] = None) -> None:
        if not hasattr(self, "top_tabs"):
            return
        active_id = self._ensure_session_for_workspace()
        records = self.sessions_by_workspace[str(self.workspace)]
        if selected is None:
            selected = ("file", self.active_file_relative) if self.active_file_relative else ("session", active_id)
        self.top_tabs.blockSignals(True)
        while self.top_tabs.count():
            self.top_tabs.removeTab(0)
        for record in records:
            index = self.top_tabs.addTab(record.name)
            self.top_tabs.setTabIcon(index, QApplication.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
            self._style_tab_close_button(index)
            self.top_tabs.setTabData(index, ("session", record.session_id))
            self.top_tabs.setTabToolTip(index, f"{record.name} · {self.workspace}")
        for relative in self.open_file_tabs:
            index = self.top_tabs.addTab(Path(relative).name)
            self.top_tabs.setTabIcon(index, QApplication.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
            self._style_tab_close_button(index)
            self.top_tabs.setTabData(index, ("file", relative))
            self.top_tabs.setTabToolTip(index, relative)
        active_index = next(
            (index for index in range(self.top_tabs.count()) if self.top_tabs.tabData(index) == selected),
            0,
        )
        self.top_tabs.setCurrentIndex(active_index)
        self.top_tabs.blockSignals(False)
        self.rename_session_button.setEnabled(bool(records))
        self.close_session_button.setEnabled(len(records) > 1)
        self.session_notice.hide()

    def _save_workspace_file_tabs(self) -> None:
        key = str(self.workspace)
        self.open_files_by_workspace[key] = dict(self.open_file_tabs)
        self.active_files_by_workspace[key] = self.active_file_relative

    def _load_workspace_file_tabs(self) -> None:
        key = str(self.workspace)
        self.open_file_tabs = dict(self.open_files_by_workspace.get(key, {}))
        self.active_file_relative = self.active_files_by_workspace.get(key)

    def _style_tab_close_button(self, index: int) -> None:
        button = QToolButton(self.top_tabs)
        button.setObjectName("simpleTabCloseButton")
        button.setText("×")
        button.setAutoRaise(True)
        button.setFixedSize(20, 20)
        button.setAccessibleName("탭 닫기")
        button.setToolTip("탭 닫기")

        def close_tab() -> None:
            tab_index = self.top_tabs.tabAt(button.geometry().center())
            if tab_index >= 0:
                self._close_top_tab(tab_index)

        button.clicked.connect(close_tab)
        self.top_tabs.setTabButton(index, QTabBar.ButtonPosition.RightSide, button)

    def _select_top_tab(self, index: int) -> None:
        if index < 0:
            return
        tab = self.top_tabs.tabData(index)
        if not isinstance(tab, (tuple, list)) or len(tab) != 2:
            return
        kind, value = tab
        if kind == "session":
            self.active_session_ids[str(self.workspace)] = value
            self._show_terminal_view(refresh_tabs=False)
            self._schedule_terminal_start()
            record = self._active_session()
            self.statusBar().showMessage(f"세션 선택됨: {record.name if record else value}")
        elif kind == "file":
            self._load_file_view(value)

    def _top_tab_clicked(self, index: int) -> None:
        if index >= 0:
            self._select_top_tab(index)

    def _close_top_tab(self, index: int) -> None:
        tab = self.top_tabs.tabData(index)
        if not isinstance(tab, (tuple, list)) or len(tab) != 2:
            return
        kind, value = tab
        if kind == "session":
            self.active_session_ids[str(self.workspace)] = value
            self._close_session()
            return
        self.open_file_tabs.pop(value, None)
        if self.active_file_relative == value:
            self.active_file_relative = None
            self.center_stack.setCurrentWidget(self.terminal_splitter)
        self._refresh_session_tabs()

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

    def _create_terminal_view(self, pane_id: str) -> QWebEngineView:
        bridge = TerminalBridge(self, pane_id)
        view = QWebEngineView()
        view.setAccessibleName(f"Codex 터미널 {pane_id}")
        view.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        channel = QWebChannel(view)
        channel.registerObject("bridge", bridge)
        view.page().setWebChannel(channel)
        view.setHtml(TERMINAL_HTML, QUrl("https://cdn.jsdelivr.net/npm/xterm@5.3.0/"))
        self.terminal_bridges[pane_id] = bridge
        return view

    def _ensure_terminal_session(self, session_id: str) -> TerminalSession:
        session = self.terminal_sessions.get(session_id)
        if session is None or not session.alive():
            session = TerminalSession(self.workspace)
            session.start()
            self.terminal_sessions[session_id] = session
        return session

    def _toggle_terminal_split(self) -> None:
        if self.split_terminal.isVisible():
            self.split_terminal.hide()
            self.terminal_splitter.setSizes([1, 0])
            self._save_terminal_layout()
            self.statusBar().showMessage("분할 터미널을 숨겼습니다. 세션은 계속 실행됩니다.")
            return
        primary_id = self._ensure_session_for_workspace()
        records = self.sessions_by_workspace[str(self.workspace)]
        split_id = self.terminal_pane_sessions.get("split")
        if not split_id or split_id == primary_id or split_id not in {record.session_id for record in records}:
            split_record = next((record for record in records if record.session_id != primary_id), None)
            if split_record is None:
                split_record = SessionRecord(uuid4().hex, f"세션 {len(records) + 1}")
                records.append(split_record)
            split_id = split_record.session_id
        self._ensure_terminal_session(split_id)
        self.terminal_pane_sessions["split"] = split_id
        self.split_terminal.show()
        self.terminal_splitter.setSizes([1, 1])
        self.terminal_bridges["split"].set_active(split_id)
        split_history = self.terminal_history.get(split_id)
        if split_history:
            self.terminal_bridges["split"].emit_output(split_id, split_history)
        self._refresh_session_tabs(("session", primary_id))
        self._save_terminal_layout()
        self.statusBar().showMessage(f"분할 터미널을 열었습니다: {self._session_name(split_id)}")

    def _session_name(self, session_id: str) -> str:
        record = next((item for item in self.sessions_by_workspace[str(self.workspace)] if item.session_id == session_id), None)
        return record.name if record else session_id
    def _start_terminal(self) -> None:
        try:
            session_id = self._ensure_session_for_workspace()
            session = self._ensure_terminal_session(session_id)
            self.terminal_pane_sessions["main"] = session_id
            self.terminal_active = session_id
            self.terminal_bridge.set_active(session_id)
            history = self.terminal_history.get(session_id)
            if history:
                for bridge in self.terminal_bridges.values():
                    bridge.emit_output(session_id, history)
            split_id = self.terminal_pane_sessions.get("split")
            if self.split_terminal.isVisible() and split_id and split_id != session_id:
                self._ensure_terminal_session(split_id)
                self.terminal_bridges["split"].set_active(split_id)
                split_history = self.terminal_history.get(split_id)
                if split_history:
                    self.terminal_bridges["split"].emit_output(split_id, split_history)
            self.restart_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            if self.terminal_timer is None:
                self.terminal_timer = QTimer(self)
                self.terminal_timer.timeout.connect(self._poll_terminal)
                self.terminal_timer.start(50)
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
        # Let workspace tabs and the file tree paint before ConPTY starts Codex.
        QTimer.singleShot(80, self._start_terminal)

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
                self.terminal_history[session_id] = (self.terminal_history.get(session_id, "") + output)[-MAX_TERMINAL_HISTORY:]
                for bridge in self.terminal_bridges.values():
                    bridge.emit_output(session_id, output)
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
        self._resize_terminal_for_pane("main", columns, rows)

    def _resize_terminal_for_pane(self, pane_id: str, columns: int, rows: int) -> None:
        session_id = self.terminal_pane_sessions.get(pane_id) or self.terminal_active
        session = self.terminal_sessions.get(session_id)
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
        if self.git_status_timer is not None:
            self.git_status_timer.stop()
        if self.usage_timer is not None:
            self.usage_timer.stop()
        if self.file_filter_timer is not None:
            self.file_filter_timer.stop()
        if self.terminal_timer is not None:
            self.terminal_timer.stop()
        for session in self.terminal_sessions.values():
            session.stop()
        workers = [
            self.scan_worker,
            self.change_scan_worker,
            self.git_status_worker,
            self.git_diff_worker,
            self.usage_worker,
            self.reset_credit_worker,
            self.baseline_worker,
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
        self._save_terminal_layout()
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
                self.terminal_layout_by_workspace,
            )
        except OSError as exc:
            self.statusBar().showMessage(f"작업 공간 목록을 저장하지 못했습니다: {exc}")

    def _build_usage_popover(self, status_usage: QWidget) -> None:
        self.usage_popover = QFrame(self, Qt.WindowType.Popup)
        self.usage_popover.setObjectName("usagePopover")
        layout = QVBoxLayout(self.usage_popover)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QLabel("◉  Codex")
        header.setObjectName("usagePopoverHeader")
        layout.addWidget(header)
        self.usage_popover_updated = QLabel("마지막 갱신: 확인 중…")
        self.usage_popover_updated.setObjectName("usagePopoverMuted")
        layout.addWidget(self.usage_popover_updated)

        session_label = QLabel("세션")
        session_label.setObjectName("usagePopoverSection")
        layout.addWidget(session_label)
        self.usage_popover_bar = QProgressBar()
        self.usage_popover_bar.setObjectName("usagePopoverBar")
        self.usage_popover_bar.setRange(0, 100)
        self.usage_popover_bar.setTextVisible(False)
        self.usage_popover_bar.setFixedHeight(8)
        layout.addWidget(self.usage_popover_bar)
        usage_row = QHBoxLayout()
        self.usage_popover_usage = QLabel("사용량 확인 중…")
        self.usage_popover_usage.setObjectName("usagePopoverValue")
        self.usage_popover_reset = QLabel("")
        self.usage_popover_reset.setObjectName("usagePopoverValue")
        usage_row.addWidget(self.usage_popover_usage)
        usage_row.addStretch(1)
        usage_row.addWidget(self.usage_popover_reset)
        layout.addLayout(usage_row)

        self.usage_popover_credit = QLabel("초기화권 정보를 확인 중…")
        self.usage_popover_credit.setObjectName("usagePopoverMuted")
        self.usage_popover_credit.setWordWrap(True)
        layout.addWidget(self.usage_popover_credit)
        self.usage_popover_reset_button = QPushButton("지금 재설정")
        self.usage_popover_reset_button.setObjectName("usagePopoverResetButton")
        self.usage_popover_reset_button.clicked.connect(self._consume_reset_credit)
        layout.addWidget(self.usage_popover_reset_button)
        self.usage_popover.hide()

        for widget in (status_usage, self.status_usage_icon, self.status_usage_bar, self.status_usage_label, self.status_usage_refresh):
            widget.installEventFilter(self)
        status_usage.setCursor(Qt.CursorShape.PointingHandCursor)

    def eventFilter(self, watched, event):
        status_widgets = (self.status_usage_widget, self.status_usage_icon, self.status_usage_bar, self.status_usage_label, self.status_usage_refresh)
        if watched in status_widgets and event.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
            self.status_usage_widget.setProperty("hovered", event.type() == QEvent.Type.Enter)
            self.status_usage_widget.style().unpolish(self.status_usage_widget)
            self.status_usage_widget.style().polish(self.status_usage_widget)
            self.status_usage_widget.update()
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and watched in (self.status_usage_widget, self.status_usage_icon, self.status_usage_bar, self.status_usage_label)
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._toggle_usage_popover()
            return True
        if event.type() == QEvent.Type.MouseButtonPress and watched is self.usage_popover:
            return False
        return super().eventFilter(watched, event)

    def _toggle_usage_popover(self) -> None:
        if self.usage_popover.isVisible():
            self.usage_popover.hide()
            return
        self._update_usage_popover()
        self.usage_popover.adjustSize()
        anchor = self.status_usage_label.mapToGlobal(QPoint(0, 0))
        x = max(0, anchor.x() - 8)
        y = anchor.y() - self.usage_popover.height() - 8
        self.usage_popover.move(x, y)
        self.usage_popover.show()
        self.usage_popover.raise_()

    def _update_usage_popover(self) -> None:
        snapshot = self.usage_snapshot
        window = snapshot.get("primary") or {}
        used = window.get("usedPercent")
        if used is None:
            self.usage_popover_usage.setText("사용량 정보 없음")
            self.usage_popover_reset.setText("")
            self.usage_popover_bar.setValue(0)
        else:
            used_value = float(used)
            self.usage_popover_usage.setText(f"{used_value:.0f}% 사용")
            self.usage_popover_bar.setValue(int(used_value))
            self.usage_popover_reset.setText(self._usage_reset_text(window.get("resetsAt")))
        self.usage_popover_updated.setText(
            f"마지막 갱신: {self.usage_updated_at.strftime('%H:%M')}"
            if hasattr(self, "usage_updated_at")
            else "마지막 갱신: 확인 중…"
        )
        self.usage_popover_credit.setText(
            f"초기화권 {self.usage_reset_count}회 사용 가능"
            if self.usage_reset_count
            else "사용 가능한 초기화권이 없습니다."
        )
        self.usage_popover_reset_button.setEnabled(self.usage_reset_count > 0)

    @staticmethod
    def _usage_reset_text(timestamp) -> str:
        if not timestamp:
            return ""
        seconds = max(0, int(timestamp - datetime.now().timestamp()))
        days, remainder = divmod(seconds, 86400)
        hours = remainder // 3600
        return f"재설정까지 {days}d {hours}h"

    def _load_usage(self, update_status_label: bool = True) -> None:
        if self._worker_is_running(self.usage_worker):
            return
        self.usage_label.setText("사용량 확인 중…")
        if update_status_label:
            self.status_usage_label.setText("사용량 확인 중…")
        self.status_usage_refresh.setEnabled(False)
        self.status_usage_refresh_timer.start()
        self.usage_worker = UsageWorker()
        self.usage_worker.loaded.connect(self._usage_loaded)
        self.usage_worker.failed.connect(self._usage_failed)
        self.usage_worker.finished.connect(self._usage_finished)
        self.usage_worker.start()

    def _usage_failed(self, message: str) -> None:
        self.usage_label.setText(f"사용량을 불러오지 못했습니다.\n{message}")
        self.status_usage_label.setText("사용량 확인 실패")
        self.usage_snapshot = {}
        self._update_usage_popover()

    def _usage_finished(self) -> None:
        if self.usage_worker is not None:
            self.usage_worker.deleteLater()
            self.usage_worker = None
        self.status_usage_refresh.setEnabled(True)
        self.status_usage_refresh.setText("↻")
        self.status_usage_refresh_timer.stop()

    def _animate_usage_refresh(self) -> None:
        self.status_usage_refresh_frame = (self.status_usage_refresh_frame + 1) % len(self.status_usage_refresh_frames)
        self.status_usage_refresh.setText(self.status_usage_refresh_frames[self.status_usage_refresh_frame])

    def _refresh_usage_from_status(self) -> None:
        if self._worker_is_running(self.usage_worker):
            return
        self._load_usage(update_status_label=False)

    def _usage_loaded(self, payload) -> None:
        snapshot = payload.get("rateLimits") or {}
        self.usage_snapshot = snapshot
        self.usage_updated_at = datetime.now()
        lines = []
        for bar in (self.primary_usage_bar, self.secondary_usage_bar):
            bar.hide()
        credits_summary = payload.get("rateLimitResetCredits") or {}
        available_count = int(credits_summary.get("availableCount") or 0)
        self.usage_reset_count = available_count
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
            # 좌측 사용량 섹션은 제거했으므로 숨김 상태를 유지한다.
            bar.hide()
            line = f"{label}: {remaining:.0f}% 남음"
            if window.get("resetsAt"):
                reset = datetime.fromtimestamp(window["resetsAt"]).strftime("%m/%d %H:%M")
                line += f" · {reset} 재설정"
            lines.append(line)
            if key == "primary":
                used = float(window["usedPercent"])
                self.status_usage_bar.setValue(int(used))
                status_line = f"{used:.0f}% 사용"
                if window.get("resetsAt"):
                    seconds = max(0, int(window["resetsAt"] - datetime.now().timestamp()))
                    days, remainder = divmod(seconds, 86400)
                    hours = remainder // 3600
                    status_line += f"  {days}d {hours}h"
                self.status_usage_label.setText(status_line)
        credits = snapshot.get("credits") or {}
        if credits.get("balance") is not None:
            lines.append(f"크레딧 잔액: {credits['balance']}")
        if not snapshot.get("primary", {}).get("usedPercent"):
            self.status_usage_bar.setValue(0)
            self.status_usage_label.setText("사용량 정보 없음")
        self.usage_label.setText("\n".join(lines) if lines else "현재 사용량 정보를 제공하지 않습니다.")
        self._update_usage_popover()

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
        self.usage_popover_reset_button.setEnabled(False)
        self.usage_popover_credit.setText("초기화 처리 중…")
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
        self._render_workspace_root_immediately()
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
        self._filter_files(self.file_search.text())
        self._schedule_file_baseline(workspace, files)
        self.statusBar().showMessage(f"파일 {len(files):,}개 · 폴더 {len(directories):,}개 · 최상위 항목 표시")

    def _files_scan_failed(self, message: str) -> None:
        self.files.clear()
        item = QTreeWidgetItem([f"파일 목록 오류: {message}"])
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        self.files.addTopLevelItem(item)
        self.statusBar().showMessage(f"파일 목록을 불러오지 못했습니다: {message}")

    def _render_workspace_root_immediately(self) -> None:
        try:
            entries = sorted(
                (entry for entry in self.workspace.iterdir() if entry.name not in IGNORED_DIRECTORIES),
                key=lambda entry: (not entry.is_dir(), entry.name.casefold()),
            )
            for entry in entries:
                if entry.is_dir():
                    self._add_folder_item(None, entry.name)
                elif entry.is_file():
                    item = QTreeWidgetItem([self._file_tree_label(entry.name, True)])
                    self._set_file_tree_icon(item, True)
                    item.setData(0, Qt.ItemDataRole.UserRole, entry.name)
                    item.setData(0, Qt.ItemDataRole.UserRole + 1, True)
                    self.files.addTopLevelItem(item)
            if not entries:
                item = QTreeWidgetItem(["빈 작업 공간입니다"])
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                self.files.addTopLevelItem(item)
        except OSError as exc:
            item = QTreeWidgetItem([f"파일 목록 오류: {exc}"])
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.files.addTopLevelItem(item)

    def _initialize_file_baseline(self, baseline: dict, workspace: str) -> None:
        if workspace == str(self.workspace):
            self.file_baselines[workspace] = baseline
            self._refresh_changed_list()

    def _schedule_file_baseline(self, workspace: str, files: list[str]) -> None:
        if workspace in self.file_baselines:
            return
        if self._worker_is_running(self.baseline_worker):
            self.pending_baseline = (workspace, files)
            return
        worker = FileBaselineWorker(Path(workspace), files)
        self.baseline_worker = worker
        worker.loaded.connect(self._initialize_file_baseline)
        worker.failed.connect(lambda message: self.statusBar().showMessage(f"파일 기준선 생성 실패: {message}"))
        worker.finished.connect(lambda worker=worker: self._baseline_finished(worker))
        worker.start()

    def _baseline_finished(self, worker) -> None:
        if worker is self.baseline_worker:
            worker.deleteLater()
            self.baseline_worker = None
            pending = self.pending_baseline
            self.pending_baseline = None
            if pending is not None:
                self._schedule_file_baseline(*pending)

    @staticmethod
    def _looks_binary(data: bytes) -> bool:
        return _looks_binary(data)

    @staticmethod
    def _capture_file_baseline(path: Path, captured_bytes: int = 0) -> tuple[FileBaseline, int]:
        return _capture_file_baseline(path, captured_bytes)

    def _check_file_changes(self) -> None:
        if self._worker_is_running(self.change_scan_worker):
            return
        self.change_scan_worker = FileChangeWorker(self.workspace)
        self.change_scan_worker.scanned.connect(self._file_changes_scanned)
        self.change_scan_worker.failed.connect(lambda message: self.statusBar().showMessage(f"파일 변경 확인 실패: {message}"))
        self.change_scan_worker.finished.connect(self._change_scan_finished)
        self.change_scan_worker.start()

    def _check_git_status(self, force: bool = False) -> None:
        if not force and self._worker_is_running(self.git_status_worker):
            return
        worker = GitStatusWorker(self.workspace)
        self.git_status_worker = worker
        worker.loaded.connect(self._git_status_loaded)
        worker.failed.connect(lambda message: self.git_status_label.setText(f"Git 상태 확인 실패\n{message}"))
        worker.finished.connect(lambda worker=worker: self._git_status_finished(worker))
        worker.start()

    def _git_status_finished(self, worker=None) -> None:
        if worker is None or worker is self.git_status_worker:
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
        self._refresh_file_tree_status()
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

    def _file_changes_scanned(self, files, directories, metadata, workspace: str) -> None:
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
            current_metadata = metadata.get(relative)
            if current_metadata is None:
                changed.add(relative)
            elif current_metadata != (baseline[relative].mtime_ns, baseline[relative].size):
                changed.add(relative)
        structural_change = files != self.all_files or directories != self.all_directories
        previous_changed = self.changed_files
        self.changed_files = changed
        self._refresh_changed_list()
        self._refresh_file_tree_status()
        self._update_bulk_change_buttons()
        if structural_change:
            self.all_files = files
            self.all_directories = directories
            self._filter_files(self.file_search.text())
        if changed:
            self.statusBar().showMessage(f"변경된 파일 {len(changed):,}개 · 파일을 두 번 클릭해 확인하세요")
        elif structural_change:
            self.statusBar().showMessage(f"파일 목록 갱신 · 파일 {len(files):,}개 · 폴더 {len(directories):,}개")
        if changed == previous_changed:
            return

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
            if current_path.exists() and current_path.stat().st_size > MAX_PREVIEW_BYTES:
                with current_path.open("rb") as handle:
                    new_content = handle.read(MAX_PREVIEW_BYTES + 1)
                new_content += b"\n[Diff preview truncated]"
            else:
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
        if hasattr(self, "file_change_summary"):
            self.file_change_summary.setText(f"변경 {len(self.changed_files):,}개" if self.changed_files else "변경 없음")
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
        self.workspace_list.blockSignals(True)
        self.workspace_list.clear()
        for path in self.workspaces:
            item = QListWidgetItem(path.name or str(path))
            item.setToolTip(str(path))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.workspace_list.addItem(item)
        self.workspace_list.setCurrentRow(self.workspaces.index(self.workspace))
        self.workspace_list.blockSignals(False)
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
            workspace_key = str(path)
            records = self.sessions_by_workspace.setdefault(workspace_key, [])
            if not records:
                records.append(SessionRecord(uuid4().hex, "세션 1"))
            self.active_session_ids[workspace_key] = records[0].session_id
            self.open_files_by_workspace.setdefault(workspace_key, {})
            self.active_files_by_workspace.setdefault(workspace_key, None)
            self._refresh_workspaces()
            self._save_workspaces()
        self._select_workspace_path(path)

    def _workspace_selection_changed(self, item: QListWidgetItem, previous: QListWidgetItem) -> None:
        if item is None:
            return
        target = Path(item.data(Qt.ItemDataRole.UserRole)).resolve()
        if target != self.workspace:
            self._select_workspace_path(target)

    def _select_workspace_item(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        target = Path(item.data(Qt.ItemDataRole.UserRole)).resolve()
        if target != self.workspace:
            self._select_workspace_path(target)

    def _select_workspace_path(self, path: Path) -> None:
        if hasattr(self, "workspace"):
            self._save_terminal_layout()
            self._save_workspace_file_tabs()
        self.workspace = path.resolve()
        self.tools = WorkspaceTools(self.workspace)
        self._ensure_session_for_workspace()
        self.workspace_label.setText(str(self.workspace))
        self._load_workspace_file_tabs()
        self.terminal_active = ""
        self.terminal_pane_sessions = {"main": "", "split": ""}
        self._restore_terminal_layout()
        self.changed_files = set()
        self.all_files = []
        self.all_directories = []
        self.files.clear()
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
        selected_tab = (
            ("file", self.active_file_relative)
            if self.active_file_relative
            else ("session", self._ensure_session_for_workspace())
        )
        self._refresh_session_tabs(selected_tab)
        self._refresh_files()
        self.git_available = False
        self.git_branch_name = ""
        self.git_entries = {}
        self.git_status_label.setText("Git 상태 확인 중…")
        self._check_git_status(force=True)
        self._schedule_terminal_start()
        if self.active_file_relative:
            self._load_file_view(self.active_file_relative)
        else:
            self.center_stack.setCurrentWidget(self.terminal_splitter)
        self._save_workspaces()
        self.statusBar().showMessage(f"작업 공간 선택됨: {self.workspace.name or self.workspace}")

    def _remove_workspace(self) -> None:
        if len(self.workspaces) == 1:
            QMessageBox.information(self, "작업 공간 제거", "최소 하나의 작업 공간은 유지해야 합니다.")
            return
        selected_item = self.workspace_list.currentItem()
        target = Path(selected_item.data(Qt.ItemDataRole.UserRole)) if selected_item else self.workspace
        target = target.resolve()
        if target not in self.workspaces:
            return
        removed = str(target)
        for record in self.sessions_by_workspace.pop(removed, []):
            session = self.terminal_sessions.pop(record.session_id, None)
            if session is not None:
                session.stop()
        self.active_session_ids.pop(removed, None)
        self.open_files_by_workspace.pop(removed, None)
        self.active_files_by_workspace.pop(removed, None)
        self.workspaces.remove(target)
        self._save_workspaces()
        next_workspace = self.workspaces[0] if target == self.workspace else self.workspace
        self._select_workspace_path(next_workspace)

    def _quick_open(self) -> None:
        """Open a searchable list of workspaces, sessions, and files."""
        entries = []
        for workspace in self.workspaces:
            entries.append((f"작업 공간 · {workspace.name or workspace}", "workspace", str(workspace)))
            for record in self.sessions_by_workspace.get(str(workspace), []):
                entries.append((f"세션 · {workspace.name or workspace} / {record.name}", "session", f"{workspace}|{record.session_id}"))
        for relative in self.all_files:
            entries.append((f"파일 · {relative}", "file", relative))
        if not entries:
            return
        labels = [label for label, _, _ in entries]
        label, accepted = QInputDialog.getItem(self, "빠른 열기", "작업 공간·세션·파일", labels, 0, True)
        if not accepted or not label:
            return
        selected = next((entry for entry in entries if entry[0] == label), None)
        if selected is None:
            selected = next((entry for entry in entries if label.casefold() in entry[0].casefold()), None)
        if selected is None:
            return
        _, kind, value = selected
        if kind == "workspace":
            self._select_workspace_path(Path(value))
        elif kind == "session":
            workspace_value, session_id = value.split("|", 1)
            self._select_workspace_path(Path(workspace_value))
            self.active_session_ids[str(self.workspace)] = session_id
            self._refresh_session_tabs(("session", session_id))
            self._show_terminal_view()
        else:
            item = QTreeWidgetItem()
            item.setData(0, Qt.ItemDataRole.UserRole, value)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, True)
            self._open_file_in_center(item)
    def _schedule_file_filter(self, query: str) -> None:
        self.file_filter_timer.start()

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
                        relative = str(Path(*key))
                        is_leaf_file = is_file and key in file_paths
                        item = QTreeWidgetItem([self._file_tree_label(relative, is_leaf_file)])
                        self._set_file_tree_icon(item, is_leaf_file)
                        item.setData(0, Qt.ItemDataRole.UserRole, relative)
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
                item = QTreeWidgetItem([self._file_tree_label(name, True)])
                self._set_file_tree_icon(item, True)
                item.setData(0, Qt.ItemDataRole.UserRole, name)
                item.setData(0, Qt.ItemDataRole.UserRole + 1, True)
                self.files.addTopLevelItem(item)
        finally:
            self.files.setUpdatesEnabled(True)

    def _add_folder_item(self, parent, relative: str):
        item = QTreeWidgetItem([self._file_tree_label(relative, False)])
        self._set_file_tree_icon(item, False)
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
                    child = QTreeWidgetItem([self._file_tree_label(child_relative, True)])
                    self._set_file_tree_icon(child, True)
                    child.setData(0, Qt.ItemDataRole.UserRole, child_relative)
                    child.setData(0, Qt.ItemDataRole.UserRole + 1, True)
                    item.addChild(child)
        except (ValueError, OSError) as exc:
            item.addChild(QTreeWidgetItem([f"읽기 실패: {exc}"]))

    @staticmethod
    def _set_file_tree_icon(item: QTreeWidgetItem, is_file: bool) -> None:
        icon_type = QStyle.StandardPixmap.SP_FileIcon if is_file else QStyle.StandardPixmap.SP_DirIcon
        item.setIcon(0, QApplication.style().standardIcon(icon_type))

    def _file_tree_label(self, relative: str, is_file: bool) -> str:
        name = Path(relative).name or relative
        if is_file and (relative in self.changed_files or relative in self.git_entries):
            return f"●  {name}  [변경]"
        if not is_file:
            prefix = Path(relative)
            changed_count = sum(
                1 for path in (set(self.changed_files) | set(self.git_entries))
                if Path(path) == prefix or prefix in Path(path).parents
            )
            if changed_count:
                return f"▸  {name}  [{changed_count}개 변경]"
        return name

    def _refresh_file_tree_status(self) -> None:
        def update(item: QTreeWidgetItem) -> None:
            relative = item.data(0, Qt.ItemDataRole.UserRole)
            if relative:
                item.setText(0, self._file_tree_label(str(relative), bool(item.data(0, Qt.ItemDataRole.UserRole + 1))))
            for index in range(item.childCount()):
                update(item.child(index))

        for index in range(self.files.topLevelItemCount()):
            update(self.files.topLevelItem(index))

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
            self._open_file_in_center(item)
            return
        self._open_file_in_center(item)

    def _open_file_in_center(self, item: QTreeWidgetItem, column: int = 0) -> None:
        relative = item.data(0, Qt.ItemDataRole.UserRole)
        if not item.data(0, Qt.ItemDataRole.UserRole + 1):
            item.setExpanded(not item.isExpanded())
            return
        try:
            path = self.tools.resolve(relative)
            text, is_binary, truncated, size = _read_preview(path)
            if is_binary:
                content = f"바이너리 파일이라 내용을 표시할 수 없습니다.\n\n파일 크기: {size:,} bytes"
            else:
                content = text
                if truncated:
                    content += f"\n\n[미리보기 제한: {MAX_PREVIEW_BYTES:,} bytes 중 앞부분만 표시]"
            self.open_file_tabs.setdefault(relative, None)
            self.active_file_relative = relative
            self._refresh_session_tabs(("file", relative))
            self.file_view.setPlainText(content)
            self.center_stack.setCurrentWidget(self.file_page)
            self.file_view.setFocus()
        except (ValueError, FileNotFoundError, OSError) as exc:
            self.statusBar().showMessage(f"파일을 열 수 없습니다: {exc}")

    def _select_file_view_tab(self, index: int) -> None:
        self._select_top_tab(index)

    def _load_file_view(self, relative: str) -> None:
        try:
            text, is_binary, truncated, size = _read_preview(self.tools.resolve(relative))
            if is_binary:
                content = f"바이너리 파일이라 내용을 표시할 수 없습니다.\n\n파일 크기: {size:,} bytes"
            else:
                content = text
                if truncated:
                    content += f"\n\n[미리보기 제한: {MAX_PREVIEW_BYTES:,} bytes 중 앞부분만 표시]"
            self.active_file_relative = relative
            self.file_view.setPlainText(content)
            self.center_stack.setCurrentWidget(self.file_page)
        except (ValueError, FileNotFoundError, OSError) as exc:
            self.file_view.setPlainText(f"파일을 열 수 없습니다.\n\n{exc}")

    def _close_file_view_tab(self, index: int) -> None:
        self._close_top_tab(index)

    def _show_terminal_view(self, refresh_tabs: bool = True) -> None:
        self.active_file_relative = None
        if refresh_tabs:
            self._refresh_session_tabs(("session", self._ensure_session_for_workspace()))
        self.file_view_back_button.hide()
        self.center_stack.setCurrentWidget(self.terminal_splitter)
        self.terminal.setFocus()
        self.statusBar().showMessage("Codex CLI 터미널로 돌아왔습니다.")

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
            text, is_binary, truncated, size = _read_preview(path)
            self.preview_title.setText(f"{path.name}  ·  {size:,} bytes")
            if is_binary:
                self.preview.setPlainText(
                    f"바이너리 파일이라 텍스트 미리보기를 지원하지 않습니다.\n\n"
                    f"파일 크기: {size:,} bytes"
                )
                return
            prefix = "[미리보기 제한: 앞부분만 표시]\n\n" if truncated else ""
            self.preview.setPlainText(prefix + text)
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

    def _create_worktree(self) -> None:
        current = self.workspace.resolve()
        branch, accepted = QInputDialog.getText(
            self,
            "새 Git worktree",
            "새 브랜치 이름(여러 개는 쉼표 또는 줄바꿈)",
            text=f"worktree-{datetime.now():%m%d-%H%M}",
        )
        branches = [value.strip() for value in re.split(r"[,\n]+", branch) if value.strip()]
        if not accepted or not branches:
            return
        parent_text = QFileDialog.getExistingDirectory(
            self,
            "worktree를 만들 상위 폴더 선택",
            str(current.parent),
        )
        if not parent_text:
            return
        targets = [
            Path(parent_text) / f"{current.name}-{re.sub(r'[^A-Za-z0-9._-]+', '-', value).strip('.-') or 'worktree'}"
            for value in branches
        ]
        try:
            created_paths = WorkspaceTools(current).git_worktree_create_many(targets, branches)
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "Git worktree 생성 실패", str(exc))
            return
        for created in created_paths:
            if created not in self.workspaces:
                self.workspaces.append(created)
            key = str(created)
            self.sessions_by_workspace.setdefault(key, [SessionRecord(uuid4().hex, "세션 1")])
            self.active_session_ids.setdefault(key, self.sessions_by_workspace[key][0].session_id)
            self.open_files_by_workspace.setdefault(key, {})
            self.active_files_by_workspace.setdefault(key, None)
        self._save_workspaces()
        self._refresh_workspaces()
        self._select_workspace_path(created_paths[-1])

    def _save_terminal_layout(self) -> None:
        if not hasattr(self, "terminal_layout_by_workspace") or not hasattr(self, "split_terminal"):
            return
        key = str(self.workspace)
        split_id = self.terminal_pane_sessions.get("split", "")
        if self.split_terminal.isVisible() and split_id:
            sizes = [max(1, int(size)) for size in self.terminal_splitter.sizes()]
            if len(sizes) == 2:
                self.terminal_layout_by_workspace[key] = {"visible": True, "split_session": split_id, "sizes": sizes}
        else:
            self.terminal_layout_by_workspace.pop(key, None)

    def _restore_terminal_layout(self) -> None:
        if not hasattr(self, "split_terminal"):
            return
        layout = self.terminal_layout_by_workspace.get(str(self.workspace), {})
        records = self.sessions_by_workspace.get(str(self.workspace), [])
        split_id = layout.get("split_session") if isinstance(layout, dict) else None
        valid_ids = {record.session_id for record in records}
        if layout.get("visible") is True and isinstance(split_id, str) and split_id in valid_ids:
            self.terminal_pane_sessions["split"] = split_id
            self.split_terminal.show()
            sizes = layout.get("sizes")
            self.terminal_splitter.setSizes([int(sizes[0]), int(sizes[1])] if isinstance(sizes, list) and len(sizes) == 2 else [1, 1])
        else:
            self.terminal_pane_sessions["split"] = ""
            self.split_terminal.hide()
            self.terminal_splitter.setSizes([1, 0])

    def _choose_workspace(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "작업 공간 선택", str(self.workspace))
        if selected:
            self._add_workspace(Path(selected))

    @staticmethod
    def _style() -> str:
        return """
        * { outline: none; }
        QMainWindow, QWidget { background:#0f1013; color:#e7e9ed; font-size:13px; }
        QWidget#sidebar { background:#17181c; border-right:1px solid #2a2c32; }
        QWidget#filePanel { background:#15161a; border-left:1px solid #2a2c32; }
        QWidget#centerPanel { background:#101114; }
        QWidget#terminalToolbar, QWidget#tabHeader { background:#111216; border-bottom:1px solid #2a2c32; }
        QSplitter::handle { background:#292b31; width:4px; }
        QSplitter::handle:hover { background:#7064a8; }
        QLabel#brand { color:#f5f5f7; font-size:16px; font-weight:800; letter-spacing:1.2px; padding:2px 0; }
        QLabel#brandSubtitle { color:#858792; font-size:10px; font-weight:700; letter-spacing:1.1px; padding-bottom:8px; }
        QLabel#section { color:#b5b6bd; font-size:11px; font-weight:800; letter-spacing:.8px; padding-top:7px; padding-bottom:2px; }
        QLabel#workspacePath, QLabel#gitStatus { color:#c9cad1; background:#1d1e23; border:1px solid #303239; border-radius:5px; padding:9px; }
        QLabel#workspacePath { font-size:12px; }
        QLabel#changeSummary { color:#d8cfff; background:#29243e; border:1px solid #66589d; border-radius:10px; padding:5px 9px; font-size:11px; font-weight:750; }
        QTabBar#topTabs { background:#111216; }
        QTabBar#topTabs::tab { background:#111820; color:#8d9aa6; border:1px solid #202c37; border-top:2px solid transparent; border-bottom:1px solid #202c37; padding:0 4px 0 10px; margin:5px 3px 0 0; min-width:0; min-height:38px; }
        QTabBar#topTabs::tab:hover { background:#18242d; color:#e7f0f1; border-color:#344550; border-top-color:#5a8c83; }
        QTabBar#topTabs::tab:selected { background:#1a2932; color:#f2faf8; border-color:#315047; border-top-color:#75ddb2; border-bottom-color:#1a2932; }
        QToolButton#simpleTabCloseButton { background:transparent; border:0; color:#82929e; font-size:15px; font-weight:400; padding:0; margin-left:4px; margin-right:6px; border-radius:4px; }
        QToolButton#simpleTabCloseButton:hover { background:#30434d; color:#f4fffc; }
        QToolButton#simpleTabCloseButton:pressed { background:#3b5e60; color:#ffffff; }
        QPlainTextEdit#fileView { background:#101114; border:1px solid #2b2d34; border-radius:4px; padding:16px; color:#dfe0e5; font-family:Consolas, 'Cascadia Code', monospace; font-size:13px; }
        QLabel#activity { color:#e4ddff; background:#292542; border:1px solid #776ab4; border-radius:8px; padding:9px 12px; font-weight:750; }
        QLabel#approvalNotice { color:#b9c9d8; background:#162337; border:1px solid #2b4159; border-radius:9px; padding:8px 11px; }
        QLabel#approvalNotice[pending="true"] { color:#ffe9b0; background:#48381d; border-color:#b08a3d; }
        QLabel#sessionNotice { color:#ffe1a0; background:#3c311d; border:1px solid #806532; border-radius:9px; padding:8px 11px; }
        QLabel#infoCard { color:#cfdeeb; background:#161e27; border:1px solid #2a3541; border-radius:6px; padding:10px; }
        QLabel#previewTitle { color:#d2e0ec; font-size:12px; font-weight:650; padding:2px; }
        QLabel#hint { color:#8fa5ba; font-size:11px; padding-top:2px; }
        QLabel { color:#d5e1ec; }
        QLineEdit, QListWidget, QTreeWidget, QPlainTextEdit, QWebEngineView { background:#101114; border:1px solid #2b2d34; border-radius:5px; }
        QWebEngineView { border-color:#2b2d34; }
        QLineEdit { padding:9px 11px; color:#f0f0f3; selection-background-color:#4b3e73; }
        QLineEdit:focus, QPlainTextEdit:focus, QListWidget:focus, QTreeWidget:focus, QWebEngineView:focus { border:2px solid #8b7bd0; }
        QListWidget, QTreeWidget, QPlainTextEdit { padding:5px; }
        QListWidget::item, QTreeWidget::item { min-height:22px; padding:6px 8px; border-radius:4px; }
        QListWidget::item:hover, QTreeWidget::item:hover { background:#24252b; }
        QListWidget::item:selected, QTreeWidget::item:selected { background:#332b50; color:#f7f3ff; border-left:3px solid #9a8add; }
        QPushButton { min-height:36px; background:#24252a; border:1px solid #3a3b43; border-radius:5px; padding:0 12px; color:#e7e8ec; font-weight:650; }
        QPushButton:hover { background:#302f3b; border-color:#8174bd; }
        QPushButton:focus { border:2px solid #8b7bd0; }
        QPushButton:pressed { background:#1d1d22; }
        QPushButton:disabled { background:#1b1c20; border-color:#2d2e34; color:#71727c; }
        QPushButton#primaryButton { background:#6654a1; border-color:#9e8ddd; color:#ffffff; }
        QPushButton#primaryButton:hover { background:#7867b9; border-color:#c0b6ef; }
        QPushButton#compactButton { min-height:34px; background:#24252a; border:1px solid #3a3b43; border-radius:5px; padding:0 11px; color:#d8d9df; font-size:12px; font-weight:650; }
        QPushButton#compactButton:hover { background:#302f3b; border-color:#8174bd; color:#ffffff; }
        QPushButton#compactButton:disabled { background:#1b1c20; border-color:#2d2e34; color:#71727c; }
        QPushButton#secondaryButton { background:#24252a; border-color:#3a3b43; color:#d8d9df; }
        QPushButton#secondaryButton:hover { background:#302f3b; border-color:#8174bd; color:#ffffff; }
        QPushButton#dangerButton { background:#442832; border-color:#8d5369; color:#ffdbe6; }
        QPushButton#dangerButton:hover { background:#5d3343; border-color:#c87997; color:#ffffff; }
        QPushButton#iconButton { min-height:36px; background:#24252a; border:1px solid #3a3b43; border-radius:5px; padding:0; color:#d8d9df; font-size:15px; font-weight:700; }
        QPushButton#iconButton:hover { background:#302f3b; border-color:#8174bd; color:#ffffff; }
        QPushButton#dangerIconButton { min-height:36px; background:#442832; border:1px solid #8d5369; border-radius:6px; padding:0; color:#ffdbe6; font-size:11px; }
        QPushButton#dangerIconButton:hover { background:#5d3343; border-color:#c87997; color:#ffffff; }
        QProgressBar#usageBar { background:#1b1c21; border:1px solid #30313a; border-radius:4px; color:#e5e1ff; text-align:center; min-height:18px; max-height:18px; }
        QProgressBar#usageBar::chunk { background:#7564b4; border-radius:4px; }
        QTabBar#workspaceTabs { background:transparent; }
        QTabBar#workspaceTabs::tab { background:#1b1c21; color:#92939d; border:1px solid #2d2e35; border-bottom:2px solid #2d2e35; padding:0 11px; margin:0 3px 0 0; min-height:30px; }
        QTabBar#workspaceTabs::tab:hover { color:#f0eefc; background:#29263a; border-color:#645a91; }
        QTabBar#workspaceTabs::tab:selected { color:#f7f4ff; background:#332b50; border-color:#7564b4; border-bottom:2px solid #a696e0; }
        QScrollBar:vertical { background:#15161a; width:9px; margin:3px; }
        QScrollBar::handle:vertical { background:#41414b; border-radius:4px; min-height:28px; }
        QScrollBar::handle:vertical:hover { background:#7564b4; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
        QStatusBar { background:#111216; color:#a9aab3; border-top:1px solid #2a2c32; padding:0; }
        QWidget#statusUsage { background:#111216; border:0; }
        QWidget#statusUsage[hovered="true"] { background:#24252a; }
        QWidget#statusUsage[hovered="true"] QLabel#statusUsageIcon { color:#ffffff; }
        QWidget#statusUsage[hovered="true"] QLabel#statusUsageLabel { color:#ffffff; }
        QWidget#statusUsage[hovered="true"] QProgressBar#statusUsageBar { background:#3a4046; }
        QLabel#statusUsageIcon { color:#d8d1ff; font-size:13px; }
        QLabel#statusUsageLabel { color:#e7e7eb; font-size:12px; font-weight:650; }
        QPushButton#statusUsageRefreshButton { min-height:24px; background:transparent; border:0; color:#93a3ad; font-size:16px; font-weight:400; padding:0; }
        QPushButton#statusUsageRefreshButton:hover { background:#263440; color:#f0f6fc; border-radius:3px; }
        QPushButton#statusUsageRefreshButton:pressed { background:#35515b; color:#ffffff; }
        QPushButton#statusUsageRefreshButton:disabled { background:transparent; color:#60717a; }
        QProgressBar#statusUsageBar { background:#2c3035; border:0; border-radius:4px; }
        QProgressBar#statusUsageBar::chunk { background:#626970; border-radius:4px; }
        QFrame#usagePopover { background:#0d0f10; border:1px solid #343a40; border-radius:10px; }
        QLabel#usagePopoverHeader { color:#f0f2f4; font-size:16px; font-weight:750; }
        QLabel#usagePopoverSection { color:#f0f2f4; font-size:13px; font-weight:750; padding-top:7px; }
        QLabel#usagePopoverMuted { color:#969ca2; font-size:12px; }
        QLabel#usagePopoverValue { color:#d9dde0; font-size:13px; }
        QProgressBar#usagePopoverBar { background:#282c30; border:0; border-radius:4px; }
        QProgressBar#usagePopoverBar::chunk { background:#00c767; border-radius:4px; }
        QPushButton#usagePopoverResetButton { min-height:34px; background:transparent; border:0; border-top:1px solid #292d31; border-radius:0; color:#e6e8eb; text-align:left; padding:0; }
        QPushButton#usagePopoverResetButton:hover { color:#62dca1; }
        QPushButton#usagePopoverResetButton:disabled { color:#6e7479; }
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
