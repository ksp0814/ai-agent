"""Persistent, non-sensitive desktop session state."""

import json
from pathlib import Path
from typing import Optional


class WorkspaceStateStore:
    """Persist workspace paths and the last active workspace."""

    def __init__(self, path: Path):
        self.path = path

    def load(self, initial: Path) -> tuple[list[Path], Path]:
        initial = initial.expanduser().resolve()
        paths = [initial]
        active = initial
        payload = self._read_payload()

        if isinstance(payload, list):
            values = payload
            requested_active = None
        elif isinstance(payload, dict):
            values = payload.get("workspaces", [])
            requested_active = payload.get("active_workspace")
        else:
            values = []
            requested_active = None

        if isinstance(values, list):
            for value in values:
                if not isinstance(value, str):
                    continue
                path = Path(value).expanduser().resolve()
                if path.is_dir() and path not in paths:
                    paths.append(path)

        if isinstance(requested_active, str):
            candidate = Path(requested_active).expanduser().resolve()
            if candidate.is_dir():
                if candidate not in paths:
                    paths.append(candidate)
                active = candidate
        return paths, active

    def load_sessions(self) -> tuple[dict[str, list[dict[str, str]]], dict[str, str], dict[str, str]]:
        payload = self._read_payload()
        if not isinstance(payload, dict):
            return {}, {}, {}
        sessions = payload.get("sessions", {})
        active_sessions = payload.get("active_sessions", {})
        terminal_history = payload.get("terminal_history", {})
        if not isinstance(sessions, dict):
            sessions = {}
        if not isinstance(active_sessions, dict):
            active_sessions = {}
        if not isinstance(terminal_history, dict):
            terminal_history = {}
        cleaned_sessions: dict[str, list[dict[str, str]]] = {}
        for workspace, values in sessions.items():
            if not isinstance(workspace, str) or not isinstance(values, list):
                continue
            cleaned_values = []
            for value in values:
                if not isinstance(value, dict):
                    continue
                session_id = value.get("id")
                name = value.get("name")
                if isinstance(session_id, str) and isinstance(name, str) and session_id and name.strip():
                    cleaned_values.append({"id": session_id, "name": name.strip()})
            if cleaned_values:
                cleaned_sessions[workspace] = cleaned_values
        cleaned_active = {
            workspace: session_id
            for workspace, session_id in active_sessions.items()
            if isinstance(workspace, str) and isinstance(session_id, str)
        }
        cleaned_history = {
            session_id: value[-100_000:]
            for session_id, value in terminal_history.items()
            if isinstance(session_id, str) and isinstance(value, str)
        }
        return cleaned_sessions, cleaned_active, cleaned_history

    def load_terminal_layout(self) -> dict[str, dict[str, object]]:
        payload = self._read_payload()
        raw_layout = payload.get("terminal_layout", {}) if isinstance(payload, dict) else {}
        if not isinstance(raw_layout, dict):
            return {}
        layout: dict[str, dict[str, object]] = {}
        for workspace, value in raw_layout.items():
            if not isinstance(workspace, str) or not isinstance(value, dict):
                continue
            visible = value.get("visible")
            split_session = value.get("split_session")
            sizes = value.get("sizes")
            if not isinstance(visible, bool) or not isinstance(split_session, str) or not split_session:
                continue
            if not isinstance(sizes, list) or len(sizes) != 2 or not all(isinstance(size, int) and size > 0 for size in sizes):
                continue
            layout[workspace] = {"visible": visible, "split_session": split_session, "sizes": sizes}
        return layout

    def save(
        self,
        workspaces: list[Path],
        active: Path,
        sessions: Optional[dict[str, list[dict[str, str]]]] = None,
        active_sessions: Optional[dict[str, str]] = None,
        terminal_history: Optional[dict[str, str]] = None,
        terminal_layout: Optional[dict[str, dict[str, object]]] = None,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "active_workspace": str(active),
            "workspaces": [str(path) for path in workspaces],
        }
        if sessions is not None:
            payload["sessions"] = sessions
        if active_sessions is not None:
            payload["active_sessions"] = active_sessions
        if terminal_history is not None:
            payload["terminal_history"] = terminal_history
        if terminal_layout is not None:
            payload["terminal_layout"] = terminal_layout
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def _read_payload(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
