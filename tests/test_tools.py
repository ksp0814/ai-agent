import tempfile
import unittest
from pathlib import Path

from personal_agent.tools import WorkspaceTools


class WorkspaceToolsTests(unittest.TestCase):
    def test_git_status_reports_clean_non_git_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            result = WorkspaceTools(Path(directory)).git_status()
            self.assertIn("Git 상태를 확인하지 못했습니다", result)

    def test_validation_uses_unittest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            result = WorkspaceTools(root).run_validation()
            self.assertIn("unittest discover", result)


if __name__ == "__main__":
    unittest.main()
