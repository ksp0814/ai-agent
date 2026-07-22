import json
import unittest

from personal_agent.terminal_bridge import encode_command


class TerminalBridgeTests(unittest.TestCase):
    def test_encode_command_keeps_workspace_and_payload(self):
        encoded = encode_command("write", text="echo hello\n")
        self.assertEqual(json.loads(encoded), {"command": "write", "text": "echo hello\n"})

    def test_resize_command_uses_integer_dimensions(self):
        encoded = encode_command("resize", columns=140, rows=45)
        self.assertEqual(json.loads(encoded), {"command": "resize", "columns": 140, "rows": 45})


if __name__ == "__main__":
    unittest.main()
