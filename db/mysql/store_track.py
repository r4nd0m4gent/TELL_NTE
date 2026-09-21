"""
store_track.py
──────────────
Maintenance for the usage-tracking table `tracking_events`. The dashboard writes
the events itself (textile_companies_NL.py, log_event); this script manages the
table.

Visit source
------------
`--add-source` adds a `source` column and labels the events already there, so
test and development visits can be told apart from real visitors:

    public     a visitor on the live site
    automated  a headless (scripted) browser on the live site - tests, bots
    local      the dashboard running anywhere but the live host, e.g. a laptop

The dashboard fills the column for new events (_visit_source). Older events
never stored the host, so a session is labelled from its `session_start` row:
`local` when the page it came from (referrer) was not the live site, or the
visit came from a private IP address; else `automated` when the browser was
headless; else `public`. Every event of a session gets the session's label.
Sessions without a `session_start` row stay NULL.

Run it before deploying the dashboard version that writes `source`: until the
column exists, that version's tracking inserts fail (silently).

Usage
-----
    python db/mysql/store_track.py                        # counts per source
    python db/mysql/store_track.py --add-source           # show what would change
    python db/mysql/store_track.py --add-source --apply   # add the column, label rows
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ENV_PATH = Path(__file__).resolve().parent / ".env"
PUBLIC_HOSTS = {"tell.newtexeco.nl"}      # keep in line with the dashboard


def get_engine():
    load_dotenv(ENV_PATH)
    return create_engine(
        f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST')}:25060/{os.getenv('DB_NAME')}",
        connect_args={"ssl": {"ca": os.getenv("DB_CA_CERT")}},
    )


def has_source_column(conn) -> bool:
    return bool(conn.execute(text(
        "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE()"
        " AND table_name = 'tracking_events' AND column_name = 'source'")).scalar())


def classify(details: dict) -> str:
    """Label a session from its session_start details."""
    referrer_host = (urlparse(details.get("referrer") or "").hostname or "").lower()
    if referrer_host and referrer_host not in PUBLIC_HOSTS:
        return "local"
    try:
        if ipaddress.ip_address(details.get("ip") or "").is_private:
            return "local"
    except ValueError:
        pass
    if "Headless" in (details.get("user_agent") or ""):
        return "automated"
    return "public"


def session_labels(conn) -> dict[str, str]:
    """{session_id: label}, decided by each session's first session_start."""
    labels: dict[str, str] = {}
    for session_id, details in conn.execute(text(
            "SELECT session_id, details FROM tracking_events"
            " WHERE event_type = 'session_start' ORDER BY id")):
        d = details if isinstance(details, dict) else json.loads(details or "{}")
        labels.setdefault(session_id, classify(d))
    return labels


def add_source(engine, apply: bool) -> None:
    with engine.connect() as conn:
        exists = has_source_column(conn)
        labels = session_labels(conn)
    print(f"`source` column exists: {exists}")
    print(f"sessions by label: {dict(Counter(labels.values()))}")
    if not apply:
        print("Dry run: nothing written. Add --apply to write.")
        return

    with engine.begin() as conn:
        if not exists:
            conn.execute(text(
                "ALTER TABLE tracking_events ADD COLUMN source VARCHAR(16) NULL AFTER details"))
        # Only rows still unlabelled, so re-running never overwrites a label
        # the dashboard wrote itself.
        for label in ("public", "automated", "local"):
            ids = [s for s, l in labels.items() if l == label]
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                marks = ",".join(f":s{j}" for j in range(len(chunk)))
                conn.execute(text(
                    f"UPDATE tracking_events SET source = :label"
                    f" WHERE source IS NULL AND session_id IN ({marks})"),
                    {"label": label, **{f"s{j}": s for j, s in enumerate(chunk)}})
    print("labels written")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--add-source", action="store_true",
                   help="add the `source` column and label existing events")
    p.add_argument("--apply", action="store_true",
                   help="with --add-source: write the changes (default: dry run)")
    args = p.parse_args()

    engine = get_engine()
    if args.add_source:
        add_source(engine, args.apply)

    with engine.connect() as conn:
        if has_source_column(conn):
            rows = conn.execute(text(
                "SELECT source, COUNT(*), COUNT(DISTINCT session_id)"
                " FROM tracking_events GROUP BY source")).fetchall()
            print("events by source:",
                  {r[0]: f"{r[1]} events / {r[2]} sessions" for r in rows})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
