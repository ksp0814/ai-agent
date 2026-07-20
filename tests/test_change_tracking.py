import tempfile
import unittest
from pathlib import Path

from personal_agent.desktop import MainWindow


class ChangeTrackingTests(unittest.TestCase):
    def test_deleted_qt_worker_is_treated_as_not_running(self):
        class DeletedWorker:
            def isRunning(self):
                raise RuntimeError("Internal C++ object already deleted")

        self.assertFalse(MainWindow._worker_is_running(DeletedWorker()))

    def test_binary_baseline_is_not_captured_for_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.bin"
            path.write_bytes(b"PK\x03\x04\x00\x00binary")
            window = MainWindow.__new__(MainWindow)

            baseline, captured = window._capture_file_baseline(path)

            self.assertTrue(baseline.is_binary)
            self.assertIsNone(baseline.content)
            self.assertEqual(captured, 0)

    def test_text_baseline_is_captured(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "note.txt"
            path.write_text("안녕하세요\n", encoding="utf-8")
            window = MainWindow.__new__(MainWindow)

            baseline, captured = window._capture_file_baseline(path)

            self.assertFalse(baseline.is_binary)
            self.assertEqual(baseline.content, path.read_bytes())
            self.assertEqual(captured, path.stat().st_size)


if __name__ == "__main__":
    unittest.main()
