import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


class Memory:
    def __init__(self, database: Path):
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._database() as db:
            db.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, content TEXT NOT NULL, created_at TEXT NOT NULL)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS interactions "
                "(id INTEGER PRIMARY KEY, request TEXT NOT NULL, response TEXT NOT NULL, created_at TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database)

    @contextmanager
    def _database(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def remember(self, content: str) -> None:
        with self._database() as db:
            db.execute("INSERT INTO memories(content, created_at) VALUES (?, ?)", (content, datetime.now(timezone.utc).isoformat()))

    def recent(self, limit: int = 20) -> list[str]:
        with self._database() as db:
            rows = db.execute("SELECT content FROM memories ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [row[0] for row in rows]

    def record_interaction(self, request: str, response: str) -> None:
        with self._database() as db:
            db.execute(
                "INSERT INTO interactions(request, response, created_at) VALUES (?, ?, ?)",
                (request, response, datetime.now(timezone.utc).isoformat()),
            )

    def recent_interactions(self, limit: int = 10) -> list[tuple[str, str]]:
        with self._database() as db:
            rows = db.execute(
                "SELECT request, response FROM interactions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return list(reversed(rows))
