import os
import platform
import select
import signal
import struct
import subprocess
from pathlib import Path
from typing import Optional

if platform.system() != "Windows":
    import fcntl
    import pty
    import termios

def build_interactive_command(workspace: Path, model: Optional[str], reasoning_effort: Optional[str]):
    command = ["codex", "--no-alt-screen", "-C", str(workspace)]
    if model:
        command.extend(["-m", model])
    if reasoning_effort:
        command.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    return command


class TerminalSession:
    def __init__(self, workspace: Path, model: Optional[str] = None, reasoning_effort: Optional[str] = None):
        self.workspace = workspace
        self.command = build_interactive_command(workspace, model, reasoning_effort)
        self.process = None
        self.master_fd = None

    def start(self) -> None:
        if self.alive():
            self.stop()
        if platform.system() == "Windows":
            from winpty import PtyProcess
            self.process = PtyProcess.spawn(self.command, cwd=str(self.workspace), dimensions=(45, 140))
            return
        self.master_fd, slave_fd = pty.openpty()
        try:
            self.resize(140, 45)
            self.process = subprocess.Popen(self.command, cwd=self.workspace, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, start_new_session=True)
        finally:
            os.close(slave_fd)

    def resize(self, columns: int, rows: int) -> None:
        columns = max(40, int(columns))
        rows = max(12, int(rows))
        if platform.system() == "Windows":
            if self.process is not None:
                self.process.set_size(columns, rows)
            return
        if self.master_fd is not None:
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))

    def read(self) -> str:
        if platform.system() == "Windows":
            if not self.process.isalive():
                return ""
            try:
                return self.process.read(4096)
            except EOFError:
                return ""
        if self.master_fd is None:
            return ""
        ready, _, _ = select.select([self.master_fd], [], [], 0.05)
        if not ready:
            return ""
        try:
            return os.read(self.master_fd, 8192).decode("utf-8", errors="replace")
        except (OSError, ValueError):
            return ""

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
            self.process = None
            if self.master_fd is not None:
                try:
                    os.close(self.master_fd)
                except OSError:
                    pass
                self.master_fd = None
