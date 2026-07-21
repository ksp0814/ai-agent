import unittest

from personal_agent.desktop import TERMINAL_HTML


class TerminalUiTests(unittest.TestCase):
    def test_xterm_terminal_includes_search_and_clipboard_controls(self):
        self.assertIn("xterm-addon-search", TERMINAL_HTML)
        self.assertIn("searchActiveTerminal", TERMINAL_HTML)
        self.assertIn("clearActiveTerminal", TERMINAL_HTML)
        self.assertIn("bridge.copy", TERMINAL_HTML)
        self.assertIn("bridge.paste", TERMINAL_HTML)
        self.assertIn("bridge.writeTo", TERMINAL_HTML)

    def test_desktop_includes_parallel_workspace_controls(self):
        from pathlib import Path

        desktop_source = Path(__file__).parents[1].joinpath("src", "personal_agent", "desktop.py").read_text(encoding="utf-8")
        self.assertIn("새 Git worktree", desktop_source)
        self.assertIn("터미널 분할", desktop_source)
        self.assertIn("terminal_layout", desktop_source)
        self.assertIn('setObjectName("terminalToolbar")', desktop_source)
        self.assertNotIn("center_header.hide()", desktop_source)
        self.assertIn("center_header_layout.addWidget(self.workspace_tabs)", desktop_source)
        self.assertIn('self.changed_list.hide()', desktop_source)
        self.assertIn('self.preview.hide()', desktop_source)
        self.assertIn('file_actions.hide()', desktop_source)
        self.assertIn("change_actions.hide()", desktop_source)


if __name__ == "__main__":
    unittest.main()
