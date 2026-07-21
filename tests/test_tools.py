import tempfile
import unittest
from pathlib import Path
import subprocess

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

    def test_scan_tree_ignores_virtual_environments_and_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / ".venv" / "Lib").mkdir(parents=True)
            (root / ".venv-py314").mkdir()
            (root / "__pycache__").mkdir()
            (root / "src" / "main.py").write_text("pass\n", encoding="utf-8")
            (root / ".venv" / "Lib" / "site.py").write_text("ignored\n", encoding="utf-8")

            files, directories = WorkspaceTools(root).scan_tree()

            self.assertEqual(files, [str(Path("src") / "main.py")])
            self.assertEqual(directories, [str(Path("src"))])

    def test_git_snapshot_and_untracked_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            note = root / "note.txt"
            note.write_text("hello\n", encoding="utf-8")
            tools = WorkspaceTools(root)

            snapshot = tools.git_snapshot()

            self.assertTrue(snapshot["available"])
            self.assertEqual(snapshot["entries"]["note.txt"], "??")
            self.assertIn("note.txt", tools.git_diff_file("note.txt"))


if __name__ == "__main__":
    unittest.main()
