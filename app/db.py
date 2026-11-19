from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from flask import current_app, g

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def create_connection(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        db_path,
        timeout=15.0,
        isolation_level=None,  # explicit BEGIN / COMMIT / ROLLBACK
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def get_db() -> sqlite3.Connection:
    if "db_conn" not in g:
        g.db_conn = create_connection(current_app.config["DATABASE_PATH"])
    return g.db_conn


def close_db(_exc: BaseException | None = None) -> None:
    conn = g.pop("db_conn", None)
    if conn is not None:
        conn.close()


@contextmanager
def transaction(
    conn: sqlite3.Connection | None = None, *, immediate: bool = False
) -> Iterator[sqlite3.Connection]:
    own = conn is None
    db = conn or get_db()
    if db.in_transaction:
        # Nested logical block: rely on caller's transaction
        yield db
        return
    db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield db
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        if own and not current_app:
            db.close()


def run_migrations(db_path: str) -> list[str]:
    conn = create_connection(db_path)
    applied: list[str] = []
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """
        )
        done = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        for path in files:
            version = path.name
            if version in done:
                continue
            sql = path.read_text(encoding="utf-8")
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)", (version,)
            )
            applied.append(version)
    finally:
        conn.close()
    return applied
