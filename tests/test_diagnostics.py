import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from personal_agent.diagnostics import collect_diagnostics


class DiagnosticsTests(unittest.TestCase):
    @patch("personal_agent.diagnostics.shutil.which", return_value=None)
    def test_reports_missing_codex_without_exposing_command_output(self, _which):
        with tempfile.TemporaryDirectory() as directory:
            result = collect_diagnostics(Path(directory))

            self.assertEqual(result["Codex CLI"], "찾을 수 없음")
            self.assertEqual(result["로그인"], "확인 불가")
            self.assertNotIn("token", " ".join(result.values()).lower())


if __name__ == "__main__":
    unittest.main()
