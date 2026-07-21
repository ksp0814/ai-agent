import json
import tempfile
import unittest
from pathlib import Path

from personal_agent.session_state import WorkspaceStateStore


class WorkspaceStateTests(unittest.TestCase):
    def test_save_and_load_restores_active_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            store = WorkspaceStateStore(root / "state.json")

            store.save([first, second], second)

            workspaces, active = store.load(first)
            self.assertEqual(workspaces, [first.resolve(), second.resolve()])
            self.assertEqual(active, second.resolve())

    def test_loads_legacy_workspace_list(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            first.mkdir()
            state_path = root / "state.json"
            state_path.write_text(json.dumps([str(first)]), encoding="utf-8")

            workspaces, active = WorkspaceStateStore(state_path).load(first)
            self.assertEqual(workspaces, [first.resolve()])
            self.assertEqual(active, first.resolve())

    def test_saves_and_loads_session_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            state_path = root / "state.json"
            store = WorkspaceStateStore(state_path)
            sessions = {str(workspace): [{"id": "session-1", "name": "분석"}]}

            store.save([workspace], workspace, sessions, {str(workspace): "session-1"})

            loaded_sessions, active_sessions, history = store.load_sessions()
            self.assertEqual(loaded_sessions, sessions)
            self.assertEqual(active_sessions, {str(workspace): "session-1"})
            self.assertEqual(history, {})

    def test_saves_and_loads_limited_terminal_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            store = WorkspaceStateStore(root / "state.json")
            history = {"session-1": "x" * 120_000}

            store.save([workspace], workspace, terminal_history=history)

            _, _, loaded_history = store.load_sessions()
            self.assertEqual(loaded_history["session-1"], "x" * 100_000)

    def test_saves_and_loads_terminal_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            store = WorkspaceStateStore(root / "state.json")
            layout = {str(workspace): {"visible": True, "split_session": "session-2", "sizes": [640, 360]}}

            store.save([workspace], workspace, terminal_layout=layout)

            self.assertEqual(store.load_terminal_layout(), layout)

    def test_ignores_invalid_terminal_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            state_path.write_text(
                json.dumps({"terminal_layout": {"workspace": {"visible": "yes", "sizes": [1]}}}),
                encoding="utf-8",
            )

            self.assertEqual(WorkspaceStateStore(state_path).load_terminal_layout(), {})


if __name__ == "__main__":
    unittest.main()
