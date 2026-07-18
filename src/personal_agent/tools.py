import os
import subprocess
import sys
from pathlib import Path


class WorkspaceTools:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def resolve(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("workspace 밖의 경로는 사용할 수 없습니다")
        return path

    def list_files(self, limit: int = 100) -> list[str]:
        files = []
        for path in sorted(self.root.rglob("*")):
            if ".git" in path.parts or ".agent" in path.parts or not path.is_file():
                continue
            files.append(str(path.relative_to(self.root)))
            if len(files) >= limit:
                break
        return files

    def scan_tree(self, file_limit: int = 100_000, directory_limit: int = 10_000) -> tuple[list[str], list[str]]:
        """Scan files and directories in one traversal for the desktop tree."""
        files, directories = [], []
        for current, dir_names, file_names in os.walk(self.root):
            dir_names[:] = sorted(name for name in dir_names if name not in {".git", ".agent"})
            relative_dir = Path(current).relative_to(self.root)
            if relative_dir != Path(".") and len(directories) < directory_limit:
                directories.append(str(relative_dir))
            for name in sorted(file_names, key=str.casefold):
                if len(files) >= file_limit:
                    break
                files.append(str(relative_dir / name))
            if len(files) >= file_limit and len(directories) >= directory_limit:
                break
        return files[:file_limit], directories[:directory_limit]

    def read_file(self, relative: str) -> str:
        path = self.resolve(relative)
        if not path.is_file():
            raise FileNotFoundError(relative)
        return path.read_text(encoding="utf-8")

    def write_file(self, relative: str, content: str) -> None:
        path = self.resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def run_tests(self) -> str:
        result = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=self.root, text=True, capture_output=True, timeout=120)
        return (result.stdout + result.stderr).strip() or f"pytest 종료 코드: {result.returncode}"

    def git_status(self) -> str:
        return self._run_readonly(["git", "status", "--short"], "Git 상태를 확인하지 못했습니다") or "변경 사항이 없습니다."

    def git_diff(self) -> str:
        return self._run_readonly(["git", "diff", "--"], "Git diff를 확인하지 못했습니다") or "현재 diff가 없습니다."

    def run_validation(self) -> str:
        """Run project tests without accepting arbitrary user-supplied commands."""
        commands = [[sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"]]
        results = []
        for command in commands:
            try:
                result = subprocess.run(command, cwd=self.root, text=True, capture_output=True, timeout=120)
            except (OSError, subprocess.TimeoutExpired) as exc:
                results.append(f"검증 실패: {exc}")
                continue
            output = (result.stdout + result.stderr).strip()
            results.append(f"$ {' '.join(command)}\n{output or f'종료 코드: {result.returncode}'}")
        return "\n\n".join(results)

    def _run_readonly(self, command: list[str], error: str) -> str:
        try:
            result = subprocess.run(command, cwd=self.root, text=True, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"{error}: {exc}"
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            return f"{error}: {detail or f'종료 코드: {result.returncode}'}"
        return result.stdout.strip()
