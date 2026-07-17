import subprocess
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
        result = subprocess.run(["python", "-m", "pytest", "-q"], cwd=self.root, text=True, capture_output=True, timeout=120)
        return (result.stdout + result.stderr).strip() or f"pytest 종료 코드: {result.returncode}"
