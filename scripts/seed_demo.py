"""Create demo users and vaccine batches (run once after the first server start).

    python scripts/seed_demo.py
Users created: pharmacist / Pharma@123, inspector / Inspect@123
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import db, services  # noqa: E402
from backend.app.ingest import seed_devices  # noqa: E402

BATCHES = [
    ("BCG", "BCG-2611-A", 60, "2027-03-31", "FRIDGE-A"),
    ("Pentavalent (DTP-HepB-Hib)", "PV-2609-14", 80, "2027-08-15", "FRIDGE-A"),
    ("Insulin Glargine", "INS-GL-551", 40, "2027-01-20", "FRIDGE-A"),
    ("Measles-Rubella", "MR-2610-03", 50, "2027-05-30", "FRIDGE-B"),
    ("Hepatitis B", "HB-2607-22", 70, "2027-11-10", "FRIDGE-B"),
    ("Insulin Aspart", "INS-AS-208", 30, "2026-12-15", "FRIDGE-B"),
]


def main() -> None:
    now = time.time()
    db.init_schema()
    with db.tx() as conn:
        seed_devices(conn, now)
    services.ensure_admin(now)
    for u, p, r in (("pharmacist", "Pharma@123", "pharmacist"), ("inspector", "Inspect@123", "inspector")):
        try:
            services.create_user(u, p, r, "system:seed", now)
            print(f"user {u} / {p} ({r})")
        except services.ServiceError as e:
            print(f"user {u}: {e.message}")
    with db.read() as conn:
        have = conn.execute("SELECT COUNT(*) AS n FROM batches").fetchone()["n"]
    if have:
        print(f"{have} batches already exist - skipping batch seed")
        return
    for v, lot, q, exp, f in BATCHES:
        services.create_batch({"vaccine": v, "lot_no": lot, "quantity": q, "expiry_date": exp, "fridge_id": f},
                              "system:seed", now)
    print(f"created {len(BATCHES)} batches")


if __name__ == "__main__":
    main()
