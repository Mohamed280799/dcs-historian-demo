"""
Report: summarises what the historian stored, what went wrong, and who changed what.

Usage:
    python report.py
"""
import sqlite3
from datetime import datetime

DB = "historian.db"


def main():
    db = sqlite3.connect(DB)

    print("\n=== Samples per tag ===")
    print(f"{'Tag':<7}{'Samples':>8}{'Good':>7}{'Bad':>6}   First stored -> Last stored")
    for tag, n, good, first, last in db.execute("""
            SELECT tag, COUNT(*), SUM(quality = 'Good'), MIN(stored_utc), MAX(stored_utc)
            FROM samples GROUP BY tag ORDER BY tag"""):
        print(f"{tag:<7}{n:>8}{good:>7}{n - good:>6}   {first} -> {last}")

    print("\n=== Largest data gap per tag (expected about 1 s between samples) ===")
    for (tag,) in db.execute("SELECT DISTINCT tag FROM samples ORDER BY tag"):
        times = [datetime.fromisoformat(t) for (t,) in
                 db.execute("SELECT stored_utc FROM samples WHERE tag = ? ORDER BY stored_utc", (tag,))]
        gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
        if gaps:
            worst = max(range(len(gaps)), key=gaps.__getitem__)
            print(f"{tag:<7}{gaps[worst]:>5.0f} s   after {times[worst].isoformat()}")

    print("\n=== Integrity events ===")
    rows = db.execute("SELECT utc, kind, COALESCE(tag, '-'), detail FROM events ORDER BY utc").fetchall()
    for utc, kind, tag, detail in rows or [("-", "none", "-", "")]:
        print(f"{utc}  {kind:<9} {tag:<6} {detail}")

    print("\n=== Audit trail (configuration changes) ===")
    rows = db.execute("SELECT utc, user, setting, old_value, new_value, reason FROM audit_trail ORDER BY utc").fetchall()
    if not rows:
        print("no changes recorded")
    for utc, user, setting, old, new, reason in rows:
        print(f"{utc}  {user}: {setting} {old} -> {new}  ({reason})")
    print()


if __name__ == "__main__":
    main()
