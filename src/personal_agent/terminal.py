import os
import platform
import shutil
import select
import signal
import struct
import subprocess
import queue
import threading
from pathlib import Path
from typing import Optional

if platform.system() != "Windows":
    import fcntl
    import pty
    import termios
else:
    from winpty import PtyProcess

def build_interactive_command(workspace: Path, model: Optional[str], reasoning_effort: Optional[str]):
    """Start a plain interactive shell; applications are launched by the user."""
    if platform.system() == "Windows":
        powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe") or "powershell.exe"
        return [powershell, "-NoLogo", "-NoExit"]
    shell = os.environ.get("SHELL") or "/bin/sh"
    return [shell, "-i"]


def build_windows_command(command: list[str]) -> list[str]:
    """Run the PTY child through a UTF-8 Windows console code page."""
    if command and Path(command[0]).name.lower() in {"cmd.exe", "cmd"}:
        return [command[0], "/d", "/k", "chcp 65001>nul"]
    if command and Path(command[0]).name.lower() in {"pwsh.exe", "pwsh", "powershell.exe", "powershell"}:
        return [
            *command,
            "-Command",
            "[Console]::InputEncoding = [Text.UTF8Encoding]::new($false); [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)",
        ]
    command_line = subprocess.list2cmdline(command)
    return ["cmd.exe", "/d", "/s", "/c", f"chcp 65001>nul && {command_line}"]


def _is_writable_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".personal-agent-write-test-{os.getpid()}"
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False


def build_terminal_environment(workspace: Optional[Path] = None) -> dict[str, str]:
    """Return a Codex environment without host-agent control variables."""
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith("ORCA_") or key.startswith("_PYI") or key in {
            "CODEX_PERMISSION_PROFILE",
            "CODEX_SANDBOX_NETWORK_DISABLED",
            "CODEX_THREAD_ID",
            "PYTHONHOME",
            "PYTHONPATH",
        }:
            environment.pop(key, None)
    codex_home = environment.get("CODEX_HOME")
    if workspace is not None and codex_home and not _is_writable_directory(Path(codex_home)):
        fallback_home = workspace / ".agent" / "codex-home"
        if workspace.exists():
            fallback_home.mkdir(parents=True, exist_ok=True)
        environment["CODEX_HOME"] = str(fallback_home)
    return environment


class TerminalSession:
    def __init__(self, workspace: Path, model: Optional[str] = None, reasoning_effort: Optional[str] = None):
        self.workspace = workspace
        self.command = build_interactive_command(workspace, model, reasoning_effort)
        self.environment = build_terminal_environment(self.workspace)
        self.process = None
        self.master_fd = None
        self._output_queue = queue.Queue()
        self._reader_stop = threading.Event()
        self._reader_thread = None

    def start(self) -> None:
        if self.alive():
            self.stop()
        if platform.system() == "Windows":
            self.process = PtyProcess.spawn(
                build_windows_command(self.command),
                cwd=str(self.workspace),
                dimensions=(45, 140),
                env=self.environment,
            )
            self._reader_stop.clear()
            self._reader_thread = threading.Thread(target=self._read_windows_output, daemon=True)
            self._reader_thread.start()
            return
        self.master_fd, slave_fd = pty.openpty()
        try:
            self.resize(140, 45)
            self.process = subprocess.Popen(
                self.command,
                cwd=self.workspace,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                env=self.environment,
            )
        finally:
            os.close(slave_fd)

    def resize(self, columns: int, rows: int) -> None:
        columns = max(40, int(columns))
        rows = max(12, int(rows))
        if platform.system() == "Windows":
            if self.process is not None:
                # pywinpty 3.x uses setwinsize(rows, columns); older
                # releases exposed set_size(columns, rows).
                resize = getattr(self.process, "setwinsize", None)
                if resize is not None:
                    resize(rows, columns)
                else:
                    self.process.set_size(columns, rows)
            return
        if self.master_fd is not None:
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))

    def read(self) -> str:
        if platform.system() == "Windows":
            chunks = []
            while True:
                try:
                    chunks.append(self._output_queue.get_nowait())
                except queue.Empty:
                    break
            return "".join(chunks)
        if self.master_fd is None:
            return ""
        # Poll loop already runs every 50 ms. Never block Qt while waiting for PTY output.
        ready, _, _ = select.select([self.master_fd], [], [], 0)
        if not ready:
            return ""
        chunks = []
        while True:
            try:
                chunks.append(os.read(self.master_fd, 8192))
            except (BlockingIOError, OSError, ValueError):
                break
            ready, _, _ = select.select([self.master_fd], [], [], 0)
            if not ready or sum(len(chunk) for chunk in chunks) >= 65_536:
                break
        return b"".join(chunks).decode("utf-8", errors="replace")

    def write(self, text: str) -> None:
        if platform.system() == "Windows":
            self.process.write(text)
        elif self.master_fd is not None:
            os.write(self.master_fd, text.encode("utf-8"))

    def alive(self) -> bool:
        if platform.system() == "Windows":
            return self.process is not None and self.process.isalive()
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        process = self.process
        self._reader_stop.set()
        try:
            if process is not None and self.alive():
                if platform.system() == "Windows":
                    process.terminate()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
        finally:
            if self._reader_thread is not None:
                self._reader_thread.join(timeout=1)
                self._reader_thread = None
            self.process = None
            if self.master_fd is not None:
                try:
                    os.close(self.master_fd)
                except OSError:
                    pass
                self.master_fd = None

    def _read_windows_output(self) -> None:
        process = self.process
        while process is not None and not self._reader_stop.is_set():
            try:
                output = process.read(4096)
            except EOFError:
                break
            except (OSError, ValueError):
                break
            if output:
                self._output_queue.put(output)
