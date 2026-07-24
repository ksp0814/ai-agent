import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from personal_agent.bridge import ApprovalStore, handle_request, process_request


class BridgeTests(unittest.TestCase):
    def test_ping_returns_protocol_status(self):
        with tempfile.TemporaryDirectory() as directory:
            result = handle_request({"method": "ping"}, Path(directory))
        self.assertEqual(result, {"ok": True, "result": {"status": "ready", "protocol": "jsonl-v1"}})

    def test_snapshot_stays_inside_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("hello", encoding="utf-8")
            result = handle_request({"method": "workspace_snapshot"}, root)
        self.assertEqual(result["ok"], True)
        self.assertIn("README.md", result["result"]["files"])

    def test_read_file_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            result = handle_request({"method": "read_file", "path": "../secret.txt"}, Path(directory))
        self.assertFalse(result["ok"])
        self.assertIn("workspace", result["error"])

    def test_stream_protocol_returns_one_json_object_per_request(self):
        from personal_agent.bridge import process_lines

        with tempfile.TemporaryDirectory() as directory:
            output = process_lines([json.dumps({"method": "ping"})], Path(directory))
        self.assertEqual(json.loads(output[0])["result"]["protocol"], "jsonl-v1")

    def test_single_request_serializes_bridge_response(self):
        with tempfile.TemporaryDirectory() as directory:
            output = process_request(json.dumps({"method": "ping"}), Path(directory))
        self.assertEqual(json.loads(output)["result"]["status"], "ready")

    def test_approval_store_restores_captured_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "note.txt"
            target.write_text("before", encoding="utf-8")
            store = ApprovalStore(root, recovery_root=root / "trash")
            store.capture("note.txt")
            target.write_text("after", encoding="utf-8")
            store.rollback("note.txt")
            self.assertEqual(target.read_text(encoding="utf-8"), "before")

    def test_approval_store_batches_baseline_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.txt").write_text("one", encoding="utf-8")
            (root / "two.txt").write_text("two", encoding="utf-8")
            store = ApprovalStore(root, recovery_root=root / "trash")

            with patch.object(store, "_save", wraps=store._save) as save:
                store.capture_many(["one.txt", "two.txt"])

            self.assertEqual(save.call_count, 1)
            self.assertEqual(set(store.records), {"one.txt", "two.txt"})

    def test_approval_store_moves_new_file_to_recovery_trash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ApprovalStore(root, recovery_root=root / "trash")
            store.capture("new.txt")
            (root / "new.txt").write_text("new", encoding="utf-8")
            result = store.rollback("new.txt")
            self.assertFalse((root / "new.txt").exists())
            self.assertTrue(Path(result).exists())


if __name__ == "__main__":
    unittest.main()
