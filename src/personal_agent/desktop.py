import argparse
from datetime import datetime
import os
from pathlib import Path
import sys
import unicodedata

if sys.platform == "darwin":
    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--disable-gpu --disable-gpu-compositing --disable-logging --log-level=3",
    )

from .tools import WorkspaceTools
from .model_catalog import consume_codex_reset_credit, fetch_codex_rate_limits
from .terminal import TerminalSession

try:
    from PySide6.QtCore import QThread, QTimer, Qt, Signal, QObject, QUrl, Slot
    from PySide6.QtGui import QFont
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
        QPlainTextEdit,
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
<style>html,body,#terminals{height:100%;margin:0;background:#0b1220;overflow:hidden}.terminal{height:100%;padding:10px;box-sizing:border-box}.terminal.hidden{display:none}</style>
</head><body><div id="terminals"></div><script>
new QWebChannel(qt.webChannelTransport, function(channel) {
  const bridge = channel.objects.bridge;
  const terminals = new Map(); let activeKey = '';
  const getTerminal = key => {
    if (terminals.has(key)) return terminals.get(key);
    const host = document.createElement('div'); host.className = 'terminal hidden'; document.getElementById('terminals').appendChild(host);
    const term = new Terminal({cursorBlink:true, convertEol:true, fontFamily:'Menlo, Monaco, Consolas, monospace', fontSize:14, theme:{background:'#0b1220', foreground:'#e5e7eb', cursor:'#93c5fd'}});
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
        self.workspaces = [self.workspace]
        self.tools = WorkspaceTools(self.workspace)
        self.scan_worker = None
        self.scan_workers = []
        self.usage_worker = None
        self.reset_credit_worker = None
        self.reset_credit_id = ""
        self.terminal_sessions = {}
        self.terminal_active = ""
        self.terminal_timer = None
        self.all_files = []
        self.all_directories = []
        self.setWindowTitle("Personal Agent")
        self.resize(1360, 820)
        self.setStyleSheet(self._style())
        self._build_ui()
        self._refresh_files()
        self._start_terminal()
        self._load_usage()

    def _build_ui(self) -> None:
        root = QSplitter()
        root.setChildrenCollapsible(False)
        self.setCentralWidget(root)

        left = QWidget(); left.setObjectName("sidebar"); left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(16, 18, 16, 16); left_layout.setSpacing(10)
        brand = QLabel("PERSONAL AGENT"); brand.setObjectName("brand"); left_layout.addWidget(brand)
        nav = QLabel("⌂  작업 공간     ◷  세션"); nav.setObjectName("navLabel"); left_layout.addWidget(nav)
        left_layout.addWidget(self._title("PROJECTS"))
        self.workspace_label = QLabel(str(self.workspace))
        self.workspace_label.setObjectName("workspacePath")
        self.workspace_label.setWordWrap(True); left_layout.addWidget(self.workspace_label)
        open_button = QPushButton("＋  작업 공간 추가"); open_button.setObjectName("primaryButton"); open_button.clicked.connect(self._choose_workspace); left_layout.addWidget(open_button)
        self.workspace_list = QListWidget(); self.workspace_list.setObjectName("workspaceList"); self.workspace_list.itemClicked.connect(self._select_workspace); left_layout.addWidget(self.workspace_list, 1)
        remove_workspace = QPushButton("선택 작업 공간 제거"); remove_workspace.setObjectName("secondaryButton"); remove_workspace.clicked.connect(self._remove_workspace); left_layout.addWidget(remove_workspace)
        self._refresh_workspaces()
        left_layout.addWidget(self._title("USAGE"))
        self.usage_label = QLabel("사용량 확인 중…"); self.usage_label.setObjectName("infoCard"); self.usage_label.setWordWrap(True); left_layout.addWidget(self.usage_label)
        refresh_usage = QPushButton("사용량 새로고침"); refresh_usage.setObjectName("secondaryButton"); refresh_usage.clicked.connect(self._load_usage); left_layout.addWidget(refresh_usage)
        self.reset_credit_button = QPushButton("사용량 초기화권 사용"); self.reset_credit_button.setObjectName("primaryButton"); self.reset_credit_button.clicked.connect(self._consume_reset_credit); self.reset_credit_button.setEnabled(False); left_layout.addWidget(self.reset_credit_button)

        center = QWidget(); center.setObjectName("centerPanel"); center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(16, 18, 16, 16); center_layout.setSpacing(10)
        center_header = QWidget(); center_header_layout = QHBoxLayout(center_header); center_header_layout.setContentsMargins(0, 0, 0, 0)
        center_header_layout.addWidget(self._title("CODEX TERMINAL"))
        center_header_layout.addStretch(1)
        self.workspace_tabs = QTabBar(); self.workspace_tabs.setObjectName("workspaceTabs"); self.workspace_tabs.setExpanding(False); self.workspace_tabs.setMovable(False); self.workspace_tabs.currentChanged.connect(self._select_workspace_tab); center_header_layout.addWidget(self.workspace_tabs)
        center_layout.addWidget(center_header)
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

        right = QWidget(); right.setObjectName("filePanel"); right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 18, 16, 16); right_layout.setSpacing(10)
        right_layout.addWidget(self._title("FILES"))
        self.file_search = QLineEdit(); self.file_search.setObjectName("searchBox"); self.file_search.setPlaceholderText("파일 찾기…"); self.file_search.textChanged.connect(self._filter_files); right_layout.addWidget(self.file_search)
        self.files = QTreeWidget(); self.files.setObjectName("fileTree"); self.files.setHeaderHidden(True); self.files.setUniformRowHeights(True); self.files.itemClicked.connect(self._preview_file); self.files.itemExpanded.connect(self._expand_folder); right_layout.addWidget(self.files, 1)
        refresh = QPushButton("파일 새로고침"); refresh.setObjectName("secondaryButton"); refresh.clicked.connect(self._refresh_files); right_layout.addWidget(refresh)
        right_layout.addWidget(self._title("PREVIEW"))
        self.preview = QPlainTextEdit(); self.preview.setObjectName("preview"); self.preview.setReadOnly(True); self.preview.setPlaceholderText("파일을 선택하면 미리보기가 표시됩니다."); right_layout.addWidget(self.preview, 1)

        root.addWidget(left); root.addWidget(center); root.addWidget(right); root.setSizes([300, 820, 330])
        self.setStatusBar(QStatusBar()); self.statusBar().showMessage("준비됨")

    @staticmethod
    def _title(text: str) -> QLabel:
        label = QLabel(text); label.setObjectName("section"); return label

    def _start_terminal(self) -> None:
        try:
            workspace = str(self.workspace)
            session = self.terminal_sessions.get(workspace)
            if session is None or not session.alive():
                session = TerminalSession(self.workspace)
                session.start()
                self.terminal_sessions[workspace] = session
            self.terminal_active = workspace
            self.terminal_bridge.set_active(workspace)
            if self.terminal_timer is None:
                self.terminal_timer = QTimer(self)
                self.terminal_timer.timeout.connect(self._poll_terminal)
            self.terminal_timer.start(30)
            self.terminal.setFocus()
            self.statusBar().showMessage("Codex CLI 세션 실행 중")
        except Exception as exc:
            self.statusBar().showMessage("Codex CLI 세션 시작 실패")

    def _poll_terminal(self) -> None:
        if not self.terminal_sessions:
            return
        for workspace, session in list(self.terminal_sessions.items()):
            try:
                output = session.read()
            except OSError as exc:
                self.statusBar().showMessage(f"Codex CLI 출력 읽기 실패: {exc}")
                continue
            if output:
                self.terminal_bridge.emit_output(workspace, output)
            if not session.alive() and workspace == self.terminal_active:
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
        if self.terminal_timer is not None:
            self.terminal_timer.stop()
        for session in self.terminal_sessions.values():
            session.stop()
        for worker in (self.scan_worker, self.usage_worker, self.reset_credit_worker):
            if worker is not None and worker.isRunning():
                worker.requestInterruption()
                worker.quit()
                worker.wait(1500)
        event.accept()

    def _load_usage(self) -> None:
        self.usage_label.setText("사용량 확인 중…")
        self.usage_worker = UsageWorker()
        self.usage_worker.loaded.connect(self._usage_loaded)
        self.usage_worker.failed.connect(lambda message: self.usage_label.setText(f"사용량을 불러오지 못했습니다.\n{message}"))
        self.usage_worker.start()

    def _usage_loaded(self, payload) -> None:
        snapshot = payload.get("rateLimits") or {}
        lines = []
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
        self._filter_files(self.file_search.text())
        self.statusBar().showMessage(f"파일 {len(files):,}개 · 폴더 {len(directories):,}개 · 최상위 항목 표시")

    def _files_scan_failed(self, message: str) -> None:
        self.statusBar().showMessage(f"파일 목록을 불러오지 못했습니다: {message}")

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
        self._select_workspace_path(path)

    def _select_workspace(self, item: QListWidgetItem) -> None:
        self._select_workspace_path(Path(item.data(Qt.ItemDataRole.UserRole)))

    def _select_workspace_path(self, path: Path) -> None:
        self.workspace = path.resolve()
        self.tools = WorkspaceTools(self.workspace)
        self.workspace_label.setText(str(self.workspace))
        self._refresh_workspaces()
        self._refresh_files()
        self._start_terminal()
        self.statusBar().showMessage(f"작업 공간 선택됨: {self.workspace.name or self.workspace}")

    def _remove_workspace(self) -> None:
        if len(self.workspaces) == 1:
            self.statusBar().showMessage("최소 하나의 작업 공간이 필요합니다.")
            return
        removed = str(self.workspace)
        session = self.terminal_sessions.pop(removed, None)
        if session is not None:
            session.stop()
        self.workspaces.remove(self.workspace)
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
            self.preview.setPlainText(f"폴더: {relative}")
            return
        try:
            self.preview.setPlainText(self.tools.read_file(relative))
        except (ValueError, FileNotFoundError, UnicodeDecodeError) as exc:
            self.preview.setPlainText(str(exc))

    def _choose_workspace(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "작업 공간 선택", str(self.workspace))
        if selected:
            self._add_workspace(Path(selected))

    @staticmethod
    def _style() -> str:
        return """
        * { outline: none; }
        QWidget { background:#202124; color:#e7e8ea; font-size:13px; }
        QWidget#sidebar { background:#292a2c; }
        QWidget#centerPanel { background:#222428; }
        QWidget#filePanel { background:#242527; }
        QSplitter::handle { background:#3a3b3e; width:1px; }
        QLabel#brand { color:#f0f1f2; font-size:15px; font-weight:700; padding:2px 0 10px; }
        QLabel#navLabel { color:#b7bbc0; background:#343638; border:1px solid #44474a; border-radius:5px; padding:8px 10px; }
        QLabel#section { color:#9fa3a8; font-size:10px; font-weight:800; letter-spacing:1.2px; padding-top:8px; padding-bottom:2px; }
        QLabel#workspacePath { color:#a8abb0; font-size:12px; padding:2px 0 4px; }
        QLabel#activity { color:#c8f4df; background:#1e332d; border:1px solid #2b755c; border-radius:6px; padding:9px 11px; font-weight:600; }
        QLabel#infoCard { color:#d0d2d5; background:#1b1c1e; border:1px solid #3b3d40; border-radius:6px; padding:11px 12px; line-height:1.35em; }
        QLabel#hint { color:#85898f; font-size:11px; padding-top:2px; }
        QLabel { color:#d3d5d8; }
        QLineEdit, QListWidget, QTreeWidget, QPlainTextEdit, QWebEngineView { background:#1e2022; border:1px solid #3a3c40; border-radius:6px; }
        QLineEdit { padding:8px 10px; color:#e7e8ea; selection-background-color:#3f454a; }
        QLineEdit:focus { border:1px solid #68b89a; }
        QListWidget, QTreeWidget, QPlainTextEdit { padding:6px; }
        QListWidget::item, QTreeWidget::item { padding:6px 8px; border-radius:4px; }
        QListWidget::item:hover, QTreeWidget::item:hover { background:#35373a; }
        QListWidget::item:selected, QTreeWidget::item:selected { background:#3b3f43; color:#ffffff; border-left:2px solid #62c39d; }
        QPushButton { background:#343638; border:1px solid #4a4c4f; border-radius:5px; padding:8px 12px; color:#e5e6e8; font-weight:600; }
        QPushButton:hover { background:#414447; border-color:#6a6e72; }
        QPushButton:pressed { background:#2b2d2f; }
        QPushButton:disabled { background:#2a2b2d; border-color:#393a3c; color:#777b80; }
        QPushButton#primaryButton { background:#2f765d; border-color:#4caa85; color:#f0fff8; }
        QPushButton#primaryButton:hover { background:#3b9474; border-color:#70d1aa; }
        QPushButton#secondaryButton { background:#303235; border-color:#46494c; color:#c8cacf; }
        QPushButton#secondaryButton:hover { background:#3b3e41; border-color:#686c70; color:#ffffff; }
        QTabBar#workspaceTabs { background:transparent; }
        QTabBar#workspaceTabs::tab { background:#2a2c2f; color:#92969b; border:1px solid #3d4044; border-bottom:0; border-top-left-radius:4px; border-top-right-radius:4px; padding:7px 12px; margin-left:3px; }
        QTabBar#workspaceTabs::tab:hover { color:#e4e5e7; background:#35383b; }
        QTabBar#workspaceTabs::tab:selected { color:#ffffff; background:#3a3e42; border-color:#61666b; }
        QScrollBar:vertical { background:#222326; width:9px; margin:2px; }
        QScrollBar::handle:vertical { background:#55595e; border-radius:4px; min-height:26px; }
        QScrollBar::handle:vertical:hover { background:#70757a; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
        QStatusBar { background:#1b1c1e; color:#a3a6aa; border-top:1px solid #3a3b3e; padding-left:8px; }
        """


def main() -> None:
    parser = argparse.ArgumentParser(description="Personal Agent desktop app")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args()
    app = QApplication([]); app.setApplicationName("Personal Agent"); app.setFont(QFont("Arial", 13))
    window = MainWindow(args.workspace); window.show(); raise SystemExit(app.exec())
