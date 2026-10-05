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

Duplicate session_start rows
----------------------------
Until the dashboard's session-id callback was fixed, every page load wrote its
`session_start` twice: the callback ran on render and again when its interval
fired, and each store update logged a row. `--dedupe` removes the extra rows.

It only collapses rows that are the same page load - same session, identical
details, within DEDUPE_SECONDS of each other. A session id is kept in the
browser tab, so the same id legitimately comes back days later from another
network, and a crawler can load the page several times in a row; those are
separate visits and are left alone.

Reverse DNS
-----------
`--resolve-ips` adds a `resolves_to` column and fills in what each visitor's IP
resolves to (`86-92-168-150.fixed.kpn.net`, `...wireless.hva.nl`), which says
more about who is visiting than the number does. Addresses that do not resolve
are recorded as '-' so they are not looked up again.

Usage
-----
    python db/mysql/store_track.py                        # counts per source
    python db/mysql/store_track.py --add-source           # show what would change
    python db/mysql/store_track.py --add-source --apply   # add the column, label rows
    python db/mysql/store_track.py --dedupe               # count duplicate rows
    python db/mysql/store_track.py --dedupe --apply       # delete them
    python db/mysql/store_track.py --drop-detail referrer --apply
    python db/mysql/store_track.py --resolve-ips --apply  # add + fill resolves_to
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
DEDUPE_SECONDS = 5        # rows this close together are one page load
DNS_TIMEOUT = 2.0         # seconds per reverse lookup


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


def dedupe(engine, apply: bool) -> None:
    """Delete repeated session_start rows written for a single page load."""
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, session_id, created_at, CAST(details AS CHAR) FROM tracking_events"
            " WHERE event_type = 'session_start' ORDER BY session_id, id")).fetchall()

    doomed, kept = [], {}
    for row_id, session_id, created_at, details in rows:
        key = (session_id, details)
        previous = kept.get(key)
        if previous and abs((created_at - previous).total_seconds()) <= DEDUPE_SECONDS:
            doomed.append(row_id)        # same page load, logged twice
        else:
            kept[key] = created_at

    print(f"session_start rows: {len(rows):,}; duplicates of a page load: {len(doomed):,}")
    if not apply or not doomed:
        if not apply:
            print("Dry run: nothing deleted. Add --apply to delete.")
        return
    with engine.begin() as conn:
        for i in range(0, len(doomed), 500):
            chunk = doomed[i:i + 500]
            marks = ",".join(f":i{j}" for j in range(len(chunk)))
            conn.execute(text(f"DELETE FROM tracking_events WHERE id IN ({marks})"),
                         {f"i{j}": v for j, v in enumerate(chunk)})
    print(f"deleted {len(doomed):,} rows")


def drop_detail(engine, field: str, apply: bool) -> None:
    """Remove one key from every event's details, e.g. a field that never varies."""
    with engine.connect() as conn:
        n = conn.execute(text(
            "SELECT COUNT(*) FROM tracking_events WHERE JSON_CONTAINS_PATH(details, 'one', :p)"),
            {"p": f"$.{field}"}).scalar()
    print(f"events carrying details.{field}: {n:,}")
    if not apply:
        print("Dry run: nothing written. Add --apply to remove it.")
        return
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE tracking_events SET details = JSON_REMOVE(details, :p)"
            " WHERE JSON_CONTAINS_PATH(details, 'one', :p)"), {"p": f"$.{field}"})
    print(f"removed details.{field} from {n:,} events")


def resolve_ips(engine, apply: bool) -> None:
    """Add `resolves_to` and fill it from each event's IP by reverse DNS."""
    import socket

    with engine.connect() as conn:
        has_column = bool(conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE()"
            " AND table_name = 'tracking_events' AND column_name = 'resolves_to'")).scalar())
        # An IP is only recorded on session_start; the rest of a session's
        # events inherit it.
        ips = {r[0]: r[1] for r in conn.execute(text(
            "SELECT DISTINCT details->>'$.ip', MIN(resolves_to)" if has_column else
            "SELECT DISTINCT details->>'$.ip', NULL"
            " FROM tracking_events WHERE details->>'$.ip' IS NOT NULL"
            " GROUP BY details->>'$.ip'"))}

    todo = sorted(ip for ip, known in ips.items() if not known and ip != "null")
    print(f"`resolves_to` column exists: {has_column}")
    print(f"distinct IP addresses: {len(ips):,}; still to look up: {len(todo):,}")
    if not apply:
        print("Dry run: nothing written. Add --apply to add the column and fill it.")
        return

    socket.setdefaulttimeout(DNS_TIMEOUT)
    resolved = {}
    for i, ip in enumerate(todo, 1):
        try:
            resolved[ip] = socket.gethostbyaddr(ip)[0]
        except Exception:
            resolved[ip] = "-"            # no reverse DNS; do not look it up again
        if i % 50 == 0:
            print(f"   looked up {i:,} / {len(todo):,}", flush=True)

    with engine.begin() as conn:
        if not has_column:
            conn.execute(text("ALTER TABLE tracking_events"
                              " ADD COLUMN resolves_to VARCHAR(255) NULL AFTER source"))
        # Every event of a session gets the hostname, not just the session_start.
        for ip, host in resolved.items():
            conn.execute(text(
                "UPDATE tracking_events SET resolves_to = :host WHERE session_id IN ("
                "  SELECT session_id FROM (SELECT DISTINCT session_id FROM tracking_events"
                "   WHERE details->>'$.ip' = :ip) x)"), {"host": host, "ip": ip})
    named = sum(1 for h in resolved.values() if h != "-")
    print(f"resolved {named:,} of {len(todo):,} addresses to a hostname")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--add-source", action="store_true",
                   help="add the `source` column and label existing events")
    p.add_argument("--dedupe", action="store_true",
                   help="delete session_start rows repeated for one page load")
    p.add_argument("--drop-detail", metavar="FIELD",
                   help="remove one key from every event's details, e.g. referrer")
    p.add_argument("--resolve-ips", action="store_true",
                   help="add `resolves_to` and fill it by reverse DNS")
    p.add_argument("--apply", action="store_true",
                   help="write the changes (default: dry run)")
    args = p.parse_args()

    engine = get_engine()
    if args.add_source:
        add_source(engine, args.apply)
    if args.dedupe:
        dedupe(engine, args.apply)
    if args.drop_detail:
        drop_detail(engine, args.drop_detail, args.apply)
    if args.resolve_ips:
        resolve_ips(engine, args.apply)

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
