"""Device message authentication (brief p.2): HMAC-SHA256 + timestamp + nonce.

Envelope (same on ESP32 firmware/fridge_node/protocol.cpp and simulator):
  {"dev": ..., "ts": <send time, epoch s>, "nonce": <hex>, "body": "<json string>", "sig": <hex>}
  sig = HMAC-SHA256(key, f"{dev}|{ts}|{nonce}|{body}")

Rejection reasons are stable strings so the dashboard and tests can rely on them.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3


class AuthError(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def signing_string(dev: str, ts: int, nonce: str, body: str) -> str:
    return f"{dev}|{ts}|{nonce}|{body}"


def sign(key: str, dev: str, ts: int, nonce: str, body: str) -> str:
    return hmac.new(key.encode(), signing_string(dev, ts, nonce, body).encode(), hashlib.sha256).hexdigest()


def build_envelope(dev: str, key: str, ts: int, body: dict | str, nonce: str | None = None) -> bytes:
    body_s = body if isinstance(body, str) else json.dumps(body, separators=(",", ":"))
    nonce = nonce or secrets.token_hex(16)
    env = {"dev": dev, "ts": int(ts), "nonce": nonce, "body": body_s,
           "sig": sign(key, dev, int(ts), nonce, body_s)}
    return json.dumps(env, separators=(",", ":")).encode()


def verify_envelope(conn: sqlite3.Connection, raw: bytes, topic_dev: str, now: float,
                    window_s: int) -> tuple[str, dict]:
    """Returns (device_id, body_dict) or raises AuthError. Records the nonce on success.
    Must run inside a write transaction (nonce insert)."""
    try:
        env = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise AuthError("bad_json")
    if not isinstance(env, dict):
        raise AuthError("bad_json")
    dev, ts, nonce, body, sig = (env.get(k) for k in ("dev", "ts", "nonce", "body", "sig"))
    if not (isinstance(dev, str) and isinstance(ts, int) and isinstance(nonce, str)
            and isinstance(body, str) and isinstance(sig, str)):
        raise AuthError("malformed", "missing or mistyped envelope field")
    if len(nonce) < 16 or len(nonce) > 64 or len(sig) != 64:
        raise AuthError("malformed", "nonce/sig length")
    if dev != topic_dev:
        raise AuthError("topic_mismatch", f"envelope dev={dev} topic dev={topic_dev}")
    row = conn.execute("SELECT hmac_key FROM devices WHERE device_id=?", (dev,)).fetchone()
    if row is None:
        raise AuthError("unknown_device", dev)
    skew = now - ts
    if skew > window_s:
        raise AuthError("stale_timestamp", f"{skew:.0f}s old")
    if skew < -window_s:
        raise AuthError("future_timestamp", f"{-skew:.0f}s ahead")
    expected = sign(row["hmac_key"], dev, ts, nonce, body)
    if not hmac.compare_digest(expected, sig):
        raise AuthError("bad_signature")
    # Signature is valid: only now is the nonce worth remembering (prevents cache poisoning)
    cur = conn.execute("INSERT OR IGNORE INTO nonces (device_id, nonce, ts) VALUES (?,?,?)", (dev, nonce, now))
    if cur.rowcount == 0:
        raise AuthError("replayed_nonce")
    try:
        body_d = json.loads(body)
    except ValueError:
        raise AuthError("bad_body")
    if not isinstance(body_d, dict):
        raise AuthError("bad_body")
    return dev, body_d


def prune_nonces(conn: sqlite3.Connection, now: float, window_s: int) -> None:
    """Nonces older than 2x the window can be forgotten: their timestamp check already fails."""
    conn.execute("DELETE FROM nonces WHERE ts < ?", (now - 2 * window_s,))
