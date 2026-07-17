from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    workspace: Path
    database: Path

    @classmethod
    def from_workspace(cls, workspace: Path) -> "Settings":
        workspace = workspace.expanduser().resolve()
        return cls(workspace=workspace, database=workspace / ".agent" / "memory.sqlite3")
