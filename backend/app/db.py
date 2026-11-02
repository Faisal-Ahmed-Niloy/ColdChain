"""SQLite access.

- `read()`  : short-lived connection for queries.
- `tx()`    : serialized write transaction (one writer at a time). Hash-chain
              appends rely on this lock so two writers never share a prev_hash.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import settings

_write_lock = threading.RLock()
_db_path = settings.db_path


def set_db_path(path: str) -> None:
    """Used by tests and scripts to point at another database file."""
    global _db_path
    _db_path = path


def get_db_path() -> str:
    return _db_path


def _connect() -> sqlite3.Connection:
    Path(_db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_db_path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@contextmanager
def read() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    with _write_lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def init_schema() -> None:
    sql = (Path(__file__).parent / "schema.sql").read_text()
    with _write_lock:
        conn = _connect()
        try:
            conn.executescript(sql)
        finally:
            conn.close()


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def one(cur) -> dict | None:
    r = cur.fetchone()
    return dict(r) if r else None
