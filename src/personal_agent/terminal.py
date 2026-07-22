import os
import platform
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
        self._output_queue = queue.Queue()
        self._reader_stop = threading.Event()
        self._reader_thread = None

    def start(self) -> None:
        if self.alive():
            self.stop()
        if platform.system() == "Windows":
            from winpty import PtyProcess
            self.process = PtyProcess.spawn(self.command, cwd=str(self.workspace), dimensions=(45, 140))
            self._reader_stop.clear()
            self._reader_thread = threading.Thread(target=self._read_windows_output, daemon=True)
            self._reader_thread.start()
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
