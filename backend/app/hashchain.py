"""Tamper-evident logs (brief p.2).

Each row stores:
  prev_hash = hash of the previous row (genesis = 64 zeros)
  hash      = SHA-256(prev_hash + canonical JSON of the row's fields)
  seal      = HMAC-SHA256(server_key, hash)

Verify Integrity walks the chain and reports the first row where:
  - hash  != recomputed hash  -> the row's content was EDITED
  - prev_hash != previous hash -> a row was DELETED, INSERTED or REORDERED
  - seal  != HMAC(hash)        -> someone rebuilt the chain without the server key
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3

GENESIS = "0" * 64

# Fields covered by the chain, per table (order is irrelevant: canonical JSON sorts keys)
CHAINS: dict[str, tuple[str, ...]] = {
    "readings": ("device_id", "fridge_id", "boot", "seq", "ts", "temp", "door", "comp",
                 "power", "batt", "state", "reason", "led", "buffered", "received_at"),
    "audit_log": ("ts", "actor", "action", "details"),
}


def canonical(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute(prev_hash: str, record: dict, key: str) -> tuple[str, str]:
    h = hashlib.sha256((prev_hash + canonical(record)).encode()).hexdigest()
    seal = hmac.new(key.encode(), h.encode(), hashlib.sha256).hexdigest()
    return h, seal


def append(conn: sqlite3.Connection, table: str, record: dict, key: str) -> int:
    """Insert `record` at the end of `table`'s chain. Call inside db.tx()."""
    fields = CHAINS[table]
    rec = {f: record.get(f) for f in fields}
    last = conn.execute(f"SELECT hash FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
    prev = last["hash"] if last else GENESIS
    h, seal = compute(prev, rec, key)
    cols = list(fields) + ["prev_hash", "hash", "seal"]
    vals = [rec[f] for f in fields] + [prev, h, seal]
    extra = {k: v for k, v in record.items() if k not in fields}
    cols += list(extra)
    vals += list(extra.values())
    cur = conn.execute(
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals
    )
    return cur.lastrowid


def verify(conn: sqlite3.Connection, table: str, key: str) -> dict:
    fields = CHAINS[table]
    prev = GENESIS
    checked = 0
    problems: list[dict] = []
    cur = conn.execute(f"SELECT id, {','.join(fields)}, prev_hash, hash, seal FROM {table} ORDER BY id")
    for row in cur:
        checked += 1
        rec = {f: row[f] for f in fields}
        if row["prev_hash"] != prev:
            problems.append({"id": row["id"], "problem": "broken_link",
                             "meaning": "a record before this one was deleted, inserted or reordered"})
        h, seal = compute(row["prev_hash"], rec, key)
        if h != row["hash"]:
            problems.append({"id": row["id"], "problem": "content_mismatch",
                             "meaning": "this record was edited after it was written"})
        elif not hmac.compare_digest(seal, row["seal"]):
            problems.append({"id": row["id"], "problem": "bad_seal",
                             "meaning": "hash recomputed without the server key (chain rewritten)"})
        prev = row["hash"]
        if len(problems) >= 50:
            break
    return {
        "table": table,
        "records_checked": checked,
        "ok": not problems,
        "first_problem_id": problems[0]["id"] if problems else None,
        "problems": problems,
        "head_hash": prev,
    }
