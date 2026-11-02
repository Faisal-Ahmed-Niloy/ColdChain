"""Tamper-evident log demo: edit one stored reading directly in SQLite (as an
insider with database access might), then press "Verify Integrity" in the app.

    python scripts/tamper_demo.py            # edits the newest reading, saves a backup
    python scripts/tamper_demo.py --restore  # puts the original value back
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import settings  # noqa: E402

BACKUP = ROOT / "data" / "tamper_backup.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=settings.db_path)
    ap.add_argument("--id", type=int, default=None, help="reading id (default: a recent one)")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    if args.restore:
        b = json.loads(BACKUP.read_text())
        conn.execute("UPDATE readings SET temp=? WHERE id=?", (b["temp"], b["id"]))
        conn.commit()
        BACKUP.unlink()
        print(f"restored reading {b['id']} to {b['temp']} C - Verify Integrity should pass again")
        return
    rid = args.id or conn.execute("SELECT id FROM readings ORDER BY id DESC LIMIT 1 OFFSET 20").fetchone()[0]
    old = conn.execute("SELECT temp FROM readings WHERE id=?", (rid,)).fetchone()[0]
    BACKUP.write_text(json.dumps({"id": rid, "temp": old}))
    conn.execute("UPDATE readings SET temp=? WHERE id=?", ((old or 5.0) - 3.0, rid))
    conn.commit()
    print(f"reading {rid}: temp {old} -> {(old or 5.0) - 3.0} (edited directly in the database)")
    print("now click Verify Integrity in the Logs page; restore with --restore")


if __name__ == "__main__":
    main()
