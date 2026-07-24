"""Small JSON-lines bridge for the React/Tauri desktop client."""

from __future__ import annotations

import json
import argparse
import shutil
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

from .model_catalog import consume_codex_reset_credit, fetch_codex_rate_limits
from .terminal import build_terminal_environment
from .tools import WorkspaceTools


class ApprovalStore:
    """Persist small text baselines and move new files to a recovery area."""

    max_file_size = 1_000_000

    def __init__(self, workspace: Path, recovery_root: Optional[Path] = None):
        self.workspace = workspace.resolve()
        self.tools = WorkspaceTools(self.workspace)
        self.path = self.workspace / ".agent" / "react-baseline.json"
        self.recovery_root = recovery_root or (Path.home() / ".personal-agent" / "rollback-trash")
        self.records = self._load()

    def _load(self) -> dict[str, dict[str, object]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(json.dumps(self.records, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    def _record_current(self, relative: str) -> dict[str, object]:
        path = self.tools.resolve(relative)
        if not path.exists():
            return {"exists": False, "content": None}
        if not path.is_file():
            raise ValueError("파일만 승인 기준선으로 저장할 수 있습니다.")
        if path.stat().st_size > self.max_file_size:
            return {"exists": True, "content": None, "unsupported": True}
        try:
            return {"exists": True, "content": path.read_text(encoding="utf-8")}
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError(f"텍스트 기준선을 저장하지 못했습니다: {exc}") from exc

    def capture(self, relative: str) -> None:
        if relative not in self.records:
            self.records[relative] = self._record_current(relative)
            self._save()

    def capture_many(self, relatives: Iterable[str]) -> None:
        changed = False
        for relative in relatives:
            if relative in self.records:
                continue
            try:
                self.records[relative] = self._record_current(relative)
            except (OSError, ValueError):
                continue
            changed = True
        if changed:
            self._save()

    def approve(self, relative: str) -> None:
        self.records[relative] = self._record_current(relative)
        self._save()

    def rollback(self, relative: str) -> str:
        record = self.records.get(relative)
        if record is None:
            raise ValueError("승인 기준선이 없습니다.")
        target = self.tools.resolve(relative)
        if record.get("unsupported"):
            raise ValueError("대용량·바이너리 파일은 자동 되돌리기를 지원하지 않습니다.")
        if record.get("exists"):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(record.get("content", "")), encoding="utf-8")
            return str(target)
        if not target.exists():
            return str(target)
        recovery = self.recovery_root / self.workspace.name / str(int(time.time() * 1000)) / relative
        recovery.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(recovery))
        return str(recovery)


def _ok(result: object) -> dict[str, object]:
    return {"ok": True, "result": result}


def _error(message: str) -> dict[str, object]:
    return {"ok": False, "error": message}


def handle_request(request: dict[str, object], workspace: Path) -> dict[str, object]:
    """Handle one read-oriented request without leaving the workspace root."""
    method = request.get("method")
    tools = WorkspaceTools(workspace)
    if method == "ping":
        return _ok({"status": "ready", "protocol": "jsonl-v1"})
    if method == "usage_snapshot":
        try:
            return _ok(fetch_codex_rate_limits(environment=build_terminal_environment(workspace)))
        except (OSError, RuntimeError, ValueError) as exc:
            return _error(f"사용량을 불러오지 못했습니다: {exc}")
    if method == "usage_reset":
        credit_id = request.get("creditId", "")
        if not isinstance(credit_id, str):
            return _error("초기화권 식별자가 올바르지 않습니다.")
        try:
            outcome = consume_codex_reset_credit(credit_id, environment=build_terminal_environment(workspace))
            return _ok({"outcome": outcome})
        except (OSError, RuntimeError, ValueError) as exc:
            return _error(f"사용량을 초기화하지 못했습니다: {exc}")
    if method == "workspace_snapshot":
        files, directories = tools.scan_tree()
        approvals = ApprovalStore(workspace)
        try:
            approvals.capture_many(files[:100])
        except OSError:
            pass
        return _ok({"files": files, "directories": directories, "git": tools.git_snapshot()})
    if method == "read_file":
        relative = request.get("path")
        if not isinstance(relative, str):
            return _error("path가 필요합니다.")
        try:
            return _ok({"path": relative, "content": tools.read_file(relative)})
        except (OSError, ValueError) as exc:
            return _error(str(exc))
    if method == "git_diff_file":
        relative = request.get("path")
        if not isinstance(relative, str):
            return _error("path가 필요합니다.")
        try:
            return _ok({"path": relative, "content": tools.git_diff_file(relative)})
        except (OSError, ValueError) as exc:
            return _error(str(exc))
    if method in {"approve_file", "rollback_file"}:
        relative = request.get("path")
        if not isinstance(relative, str):
            return _error("path가 필요합니다.")
        try:
            store = ApprovalStore(workspace)
            if method == "approve_file":
                store.approve(relative)
                return _ok({"path": relative, "action": "approved"})
            location = store.rollback(relative)
            return _ok({"path": relative, "action": "rolled_back", "location": location})
        except (OSError, ValueError) as exc:
            return _error(str(exc))
    return _error(f"지원하지 않는 bridge 메서드입니다: {method}")


def process_lines(lines: Iterable[str], workspace: Path) -> list[str]:
    output = []
    for line in lines:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = handle_request(request, workspace)
        except (json.JSONDecodeError, TypeError) as exc:
            response = _error(f"잘못된 JSON 요청입니다: {exc}")
        output.append(json.dumps(response, ensure_ascii=True))
    return output


def process_request(line: str, workspace: Path) -> str:
    """Process one request for a desktop command invocation."""
    try:
        response = handle_request(json.loads(line), workspace)
    except (json.JSONDecodeError, TypeError) as exc:
        response = _error(f"잘못된 JSON 요청입니다: {exc}")
    return json.dumps(response, ensure_ascii=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Personal Agent JSON-line bridge")
    parser.add_argument("workspace", nargs="?", default=str(Path.cwd()))
    parser.add_argument("--request", help="process one JSON request and exit")
    args = parser.parse_args()
    workspace = Path(args.workspace).expanduser()
    if args.request is not None:
        print(process_request(args.request, workspace), flush=True)
        return
    for response in process_lines(sys.stdin, workspace):
        print(response, flush=True)


if __name__ == "__main__":
    main()
