import unittest
from pathlib import Path
from unittest.mock import patch

from personal_agent.terminal import TerminalSession


class TerminalResizeTests(unittest.TestCase):
    @patch("personal_agent.terminal.platform.system", return_value="Windows")
    def test_windows_pywinpty_3_uses_setwinsize(self, _system):
        session = TerminalSession(Path("/workspace"))

        class Process:
            def __init__(self):
                self.calls = []

            def setwinsize(self, rows, columns):
                self.calls.append((rows, columns))

        session.process = Process()
        session.resize(140, 45)

        self.assertEqual(session.process.calls, [(45, 140)])

    @patch("personal_agent.terminal.platform.system", return_value="Windows")
    def test_windows_legacy_pywinpty_uses_set_size(self, _system):
        session = TerminalSession(Path("/workspace"))

        class Process:
            def __init__(self):
                self.calls = []

            def set_size(self, columns, rows):
                self.calls.append((columns, rows))

        session.process = Process()
        session.resize(140, 45)

        self.assertEqual(session.process.calls, [(140, 45)])


if __name__ == "__main__":
    unittest.main()
