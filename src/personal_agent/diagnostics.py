"""Read-only runtime diagnostics for the desktop application."""

import platform
import shutil
import subprocess
from pathlib import Path

from .tools import WorkspaceTools


def _run(command: list[str], cwd: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = (result.stdout or result.stderr).strip().splitlines()
    return result.returncode == 0, output[0][:120] if output else ""


def collect_diagnostics(workspace: Path) -> dict[str, str]:
    diagnostics = {}
    codex_path = shutil.which("codex")
    if not codex_path:
        diagnostics["Codex CLI"] = "찾을 수 없음"
        diagnostics["로그인"] = "확인 불가"
    else:
        version_ok, version = _run(["codex", "--version"], workspace)
        login_ok, _ = _run(["codex", "login", "status"], workspace)
        diagnostics["Codex CLI"] = version if version_ok else "실행 실패"
        diagnostics["로그인"] = "확인됨" if login_ok else "로그인 필요 또는 확인 실패"

    snapshot = WorkspaceTools(workspace).git_snapshot()
    if snapshot.get("available"):
        diagnostics["Git"] = f"{snapshot.get('branch', '(브랜치 없음)')} · 변경 {len(snapshot.get('entries', {}))}개"
    else:
        diagnostics["Git"] = "Git 저장소 아님"

    if platform.system() == "Windows":
        try:
            import winpty  # noqa: F401
            diagnostics["터미널"] = "Windows ConPTY 사용 가능"
        except ImportError:
            diagnostics["터미널"] = "pywinpty 필요"
    else:
        try:
            import pty  # noqa: F401
            diagnostics["터미널"] = f"{platform.system()} PTY 사용 가능"
        except ImportError:
            diagnostics["터미널"] = "PTY 사용 불가"
    return diagnostics
