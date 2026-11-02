"""Append-only, hash-chained audit trail."""
from __future__ import annotations

import json
import sqlite3

from . import hashchain
from .config import settings


def log(conn: sqlite3.Connection, now: float, actor: str, action: str, details: dict | None = None) -> int:
    return hashchain.append(conn, "audit_log", {
        "ts": now,
        "actor": actor,
        "action": action,
        "details": json.dumps(details or {}, sort_keys=True, separators=(",", ":"), default=str),
    }, settings.log_seal_key)


def security_event(conn: sqlite3.Connection, now: float, device_id: str | None, kind: str,
                   detail: str = "", topic: str = "") -> None:
    """Rejected/replayed device messages: stored in security_events AND the audit chain."""
    conn.execute("INSERT INTO security_events (ts, device_id, kind, detail, topic) VALUES (?,?,?,?,?)",
                 (now, device_id, kind, detail[:300], topic[:120]))
    if device_id:
        conn.execute("UPDATE devices SET rejected = rejected + 1 WHERE device_id=?", (device_id,))
    log(conn, now, f"device:{device_id or 'unknown'}", f"security.reject.{kind}", {"detail": detail[:300]})
