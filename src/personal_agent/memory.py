import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class Memory:
    def __init__(self, database: Path):
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, content TEXT NOT NULL, created_at TEXT NOT NULL)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database)

    def remember(self, content: str) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO memories(content, created_at) VALUES (?, ?)", (content, datetime.now(timezone.utc).isoformat()))

    def recent(self, limit: int = 20) -> list[str]:
        with self._connect() as db:
            rows = db.execute("SELECT content FROM memories ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [row[0] for row in rows]
