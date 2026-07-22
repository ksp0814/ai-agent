import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from personal_agent.terminal import TerminalSession, build_interactive_command, build_terminal_environment, build_windows_command


class TerminalResizeTests(unittest.TestCase):
    @patch("personal_agent.terminal.platform.system", return_value="Windows")
    def test_terminal_starts_plain_windows_shell(self, _system):
        command = build_interactive_command(Path("C:/workspace"), None, None)
        self.assertIn(Path(command[0]).name.lower(), {"pwsh.exe", "powershell.exe"})
        self.assertEqual(command[1:], ["-NoLogo", "-NoExit"])

    def test_powershell_command_does_not_add_cmd_wrapper(self):
        command = build_windows_command(["pwsh.exe", "-NoLogo", "-NoExit"])

        self.assertEqual(command[0], "pwsh.exe")
        self.assertNotIn("cmd.exe", command)
        self.assertIn("-Command", command)

    def test_windows_command_forces_utf8_console_code_page(self):
        command = build_windows_command(["codex", "--no-alt-screen", "-C", "C:/workspace"])

        self.assertEqual(command[:3], ["cmd.exe", "/d", "/s"])
        self.assertIn("chcp 65001>nul", command[-1])
        self.assertIn("codex", command[-1])

    def test_terminal_environment_does_not_inherit_orca_session_controls(self):
        with patch.dict("os.environ", {
            "ORCA_AGENT_HOOK_ENDPOINT": "hook",
            "CODEX_PERMISSION_PROFILE": ":workspace",
            "CODEX_THREAD_ID": "thread",
            "CODEX_HOME": "C:/codex-home",
            "PYTHONPATH": "C:/python-path",
            "_PYI_APPLICATION_HOME_DIR": "C:/pyinstaller",
        }, clear=False):
            environment = build_terminal_environment()
        self.assertNotIn("ORCA_AGENT_HOOK_ENDPOINT", environment)
        self.assertNotIn("CODEX_PERMISSION_PROFILE", environment)
        self.assertNotIn("CODEX_THREAD_ID", environment)
        self.assertNotIn("PYTHONPATH", environment)
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", environment)
        self.assertEqual(environment["CODEX_HOME"], "C:/codex-home")

    @patch("personal_agent.terminal._is_writable_directory", return_value=False)
    def test_terminal_environment_uses_workspace_home_when_host_home_is_read_only(self, _is_writable):
        with tempfile.TemporaryDirectory() as workspace:
            with patch.dict("os.environ", {"CODEX_HOME": "C:/orca-codex-home"}, clear=False):
                environment = build_terminal_environment(Path(workspace))

            self.assertEqual(environment["CODEX_HOME"], str(Path(workspace) / ".agent" / "codex-home"))

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

    @patch("personal_agent.terminal.platform.system", return_value="Linux")
    @patch("personal_agent.terminal.select.select", return_value=([], [], []))
    def test_read_does_not_block_polling_loop(self, _select, _system):
        session = TerminalSession(Path("/workspace"))
        session.master_fd = 123

        self.assertEqual(session.read(), "")
        self.assertEqual(_select.call_args.args, ([123], [], [], 0))


if __name__ == "__main__":
    unittest.main()
