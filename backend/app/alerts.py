"""Alerts: stored in the DB, pushed to the dashboard, and sent via Telegram
and/or e-mail (brief p.1: "SMS, Telegram, or email"). Sending happens on a
background thread so a slow network never blocks MQTT ingest.
Only the Python standard library is used (urllib, smtplib)."""
from __future__ import annotations

import json
import smtplib
import sqlite3
import threading
import urllib.parse
import urllib.request
from email.message import EmailMessage

from . import db, events
from .config import settings

# Kinds that leave the building (push notification); the rest stay on the dashboard.
NOTIFY_KINDS = {"door_alarm", "breach", "discard", "at_risk", "power_lost", "predicted_breach",
                "relocation_suggested", "device_offline", "compressor_weak", "test"}


def channels_configured() -> list[str]:
    ch = []
    if settings.telegram_token and settings.telegram_chat_id:
        ch.append("telegram")
    if settings.smtp_host and settings.alert_email_to:
        ch.append("email")
    return ch


def _send_telegram(text: str) -> str:
    url = f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": settings.telegram_chat_id, "text": text}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10) as r:
        return "sent" if r.status == 200 else f"http_{r.status}"


def _send_email(subject: str, text: str) -> str:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user or "coldchain@localhost"
    msg["To"] = settings.alert_email_to
    msg.set_content(text)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as s:
        s.starttls()
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_pass)
        s.send_message(msg)
    return "sent"


def _deliver(alert_id: int, kind: str, severity: str, message: str) -> None:
    results: dict[str, str] = {}
    text = f"[ColdChain {severity.upper()}] {message}"
    for ch in channels_configured():
        try:
            results[ch] = _send_telegram(text) if ch == "telegram" else _send_email(f"ColdChain {kind}", text)
        except Exception as e:  # network errors are recorded, never raised
            results[ch] = f"failed: {type(e).__name__}: {e}"[:200]
    if not results:
        results["none"] = "no channel configured (set CC_TELEGRAM_* or CC_SMTP_*)"
    with db.tx() as conn:
        conn.execute("UPDATE alerts SET delivery=? WHERE id=?", (json.dumps(results), alert_id))
    events.publish({"type": "alert_delivery", "alert_id": alert_id, "delivery": results})


# Tests set this to False to keep everything synchronous and offline.
SEND_ENABLED = True


def raise_alert(conn: sqlite3.Connection, now: float, fridge_id: str | None, kind: str,
                severity: str, message: str, cooldown_s: float = 0) -> int | None:
    """Insert an alert inside the caller's transaction; delivery starts after commit.
    With cooldown_s, a repeat of the same kind for the same fridge inside that window is
    suppressed (a temperature hovering at 8.0 C must not send a message every few seconds)."""
    if cooldown_s:
        last = conn.execute("SELECT ts FROM alerts WHERE kind=? AND fridge_id IS ? ORDER BY id DESC LIMIT 1",
                            (kind, fridge_id)).fetchone()
        if last and now - last["ts"] < cooldown_s:
            return None
    cur = conn.execute(
        "INSERT INTO alerts (ts, fridge_id, kind, severity, message, delivery) VALUES (?,?,?,?,?,?)",
        (now, fridge_id, kind, severity, message, json.dumps({"pending": "queued"} if kind in NOTIFY_KINDS
                                                              else {"dashboard": "only"})),
    )
    alert_id = cur.lastrowid
    events.publish({"type": "alert", "alert_id": alert_id, "fridge_id": fridge_id, "kind": kind,
                    "severity": severity, "message": message})
    if SEND_ENABLED and kind in NOTIFY_KINDS:
        # Small delay so the caller's transaction commits before the update runs.
        t = threading.Timer(0.2, _deliver, args=(alert_id, kind, severity, message))
        t.daemon = True
        t.start()
    return alert_id
