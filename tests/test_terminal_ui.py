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


if __name__ == "__main__":
    unittest.main()
