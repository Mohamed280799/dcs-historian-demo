"""
Historian client: reads DCS tags over OPC UA and stores them, like a small PI.

Every poll stores value, quality and the source timestamp for each tag in SQLite.
On top of that it runs data-integrity checks that matter in a GMP plant:

  * STALE     a tag's source timestamp has not changed for longer than the limit
  * BAD       the tag reports a Bad quality status
  * CONN_LOST the connection to the DCS dropped; the client keeps retrying
  * CONN_OK   the connection is back (the gap in between is visible in the data)

Configuration changes are written to an audit trail (who, when, old -> new, why),
because in a validated system no setting may change silently.

Usage:
    python historian.py                      # run until Ctrl+C
    python historian.py --duration 60        # run for 60 seconds
    python historian.py --set stale_limit_s=10 --user mohamed --reason "tighter check"
"""
import argparse
import asyncio
import getpass
import json
import logging
import sqlite3
from datetime import datetime, timezone

from asyncua import Client

DEFAULT_PORT = 53530  # 4840 is the standard OPC UA port but is often taken (e.g. by Siemens tools)


def endpoint(port):
    return f"opc.tcp://127.0.0.1:{port}/dcs_sim/"
TAGS = ["TT101", "PT102", "FT103", "P104"]
DB = "historian.db"
DEFAULT_CONFIG = {"poll_s": 1.0, "stale_limit_s": 5.0, "retry_s": 3.0}
logging.getLogger("asyncua").setLevel(logging.ERROR)  # keep the console readable


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- storage
def open_db():
    db = sqlite3.connect(DB)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS samples (
            stored_utc TEXT, tag TEXT, value REAL, quality TEXT, source_utc TEXT);
        CREATE TABLE IF NOT EXISTS events (
            utc TEXT, kind TEXT, tag TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS audit_trail (
            utc TEXT, user TEXT, setting TEXT, old_value TEXT, new_value TEXT, reason TEXT);
    """)
    for k, v in DEFAULT_CONFIG.items():
        db.execute("INSERT OR IGNORE INTO config VALUES (?, ?)", (k, json.dumps(v)))
    db.commit()
    return db


def load_config(db):
    return {k: json.loads(v) for k, v in db.execute("SELECT key, value FROM config")}


def change_setting(db, setting, value, user, reason):
    """Every configuration change is recorded: attributable and traceable (ALCOA+)."""
    if setting not in DEFAULT_CONFIG:
        raise SystemExit(f"Unknown setting '{setting}'. Allowed: {', '.join(DEFAULT_CONFIG)}")
    if not reason:
        raise SystemExit("A reason is required for every change (--reason).")
    old = load_config(db)[setting]
    new = float(value)
    db.execute("UPDATE config SET value = ? WHERE key = ?", (json.dumps(new), setting))
    db.execute("INSERT INTO audit_trail VALUES (?, ?, ?, ?, ?, ?)",
               (now(), user, setting, str(old), str(new), reason))
    db.commit()
    print(f"{setting}: {old} -> {new} (by {user}: {reason})")


def log_event(db, kind, tag, detail):
    db.execute("INSERT INTO events VALUES (?, ?, ?, ?)", (now(), kind, tag, detail))
    db.commit()
    print(f"{now()}  {kind:<9} {tag or '-':<6} {detail}")


# ---------------------------------------------------------------- collection
async def collect(db, cfg, stop_at, url):
    last_source = {}       # tag -> last source timestamp seen
    last_change = {}       # tag -> when that timestamp last changed (our clock)
    flagged = set()        # (kind, tag) already reported, so we report once
    connected = None

    while asyncio.get_running_loop().time() < stop_at:
        try:
            async with Client(url=url, timeout=4) as client:
                if connected is not True:
                    log_event(db, "CONN_OK", None, f"connected to {url}")
                    connected = True
                ns = await client.get_namespace_index("DCS_Sim")
                nodes = {t: await client.nodes.objects.get_child([f"{ns}:Area100", f"{ns}:{t}"])
                         for t in TAGS}

                while asyncio.get_running_loop().time() < stop_at:
                    loop_t = asyncio.get_running_loop().time()
                    for tag, node in nodes.items():
                        dv = await node.read_data_value(raise_on_bad_status=False)
                        quality = dv.StatusCode.name
                        src = dv.SourceTimestamp.isoformat(timespec="seconds") if dv.SourceTimestamp else None
                        value = dv.Value.Value if dv.Value is not None else None
                        db.execute("INSERT INTO samples VALUES (?, ?, ?, ?, ?)",
                                   (now(), tag, float(value) if value is not None else None, quality, src))

                        # --- quality check
                        if not dv.StatusCode.is_good():
                            if ("BAD", tag) not in flagged:
                                log_event(db, "BAD", tag, f"quality {quality}")
                                flagged.add(("BAD", tag))
                        elif ("BAD", tag) in flagged:
                            log_event(db, "GOOD", tag, "quality back to Good")
                            flagged.discard(("BAD", tag))

                        # --- stale check (value not refreshed by the source)
                        if src != last_source.get(tag):
                            last_source[tag], last_change[tag] = src, loop_t
                            if ("STALE", tag) in flagged:
                                log_event(db, "FRESH", tag, "updates resumed")
                                flagged.discard(("STALE", tag))
                        elif loop_t - last_change[tag] > cfg["stale_limit_s"] and ("STALE", tag) not in flagged:
                            log_event(db, "STALE", tag,
                                      f"no update for > {cfg['stale_limit_s']:.0f} s (last source time {src})")
                            flagged.add(("STALE", tag))
                    db.commit()
                    await asyncio.sleep(cfg["poll_s"])
        except (OSError, asyncio.TimeoutError, ConnectionError, Exception) as exc:  # noqa: BLE001
            if connected is not False:
                log_event(db, "CONN_LOST", None, f"{type(exc).__name__}: retrying every {cfg['retry_s']:.0f} s")
                connected = False
            await asyncio.sleep(cfg["retry_s"])


def main():
    ap = argparse.ArgumentParser(description="Small OPC UA historian with data-integrity checks")
    ap.add_argument("--duration", type=float, default=None, help="seconds to run (default: until Ctrl+C)")
    ap.add_argument("--set", help="change a setting, e.g. stale_limit_s=10")
    ap.add_argument("--user", default=getpass.getuser())
    ap.add_argument("--reason", default="")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"simulator port (default {DEFAULT_PORT})")
    args = ap.parse_args()

    db = open_db()
    if args.set:
        key, value = args.set.split("=")
        change_setting(db, key, value, args.user, args.reason)
        return

    cfg = load_config(db)
    print(f"Historian started. Settings: {cfg}. Data -> {DB}")

    async def run():
        stop_at = asyncio.get_running_loop().time() + (args.duration or 10**9)
        await collect(db, cfg, stop_at, endpoint(args.port))

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    print("Historian stopped. Run `python report.py` for a summary.")


if __name__ == "__main__":
    main()
