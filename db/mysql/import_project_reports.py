"""
import_project_reports.py
─────────────────────────
Collect the public reports of the ECOSYSTEX member projects from CORDIS into
one table, `project_reports`, and write the data the articles page searches
alongside the journal articles.

What is collected
-----------------
For every project in `ecosystex_projects` with an EU grant number, the CORDIS
record (https://cordis.europa.eu/project/id/<grant>?format=xml) lists its
results. Two kinds are kept:

    report_summary   the periodic and final reporting summaries CORDIS
                     publishes: the context and objectives, the work
                     performed, and the results, as full text
                     (https://cordis.europa.eu/result/rcn/<rcn>?format=xml)
    deliverable      public deliverables of type R (document, report): title,
                     date and the link to the document. Their text is not
                     collected: the Participant Portal serves the files through
                     a download page that needs a browser.

Projects CORDIS does not know (most Erasmus+, Interreg and Single Market
projects, and those without a grant number) have no results here.

Downloads are kept in data/cordis_results/ (one file per project and per
report summary), so a rerun only fetches what is new; --refresh fetches
everything again. Requests are spaced by a second.

Columns
-------
    report_id      'C' + the CORDIS record number (rcn): the page's ID
    kind           report_summary | deliverable
    acronym        the ECOSYSTEX project (ecosystex_projects.acronym)
    programme, grant_id
    title, label   label: 'Periodic report 2', 'Final report', 'Deliverable'
    year           report summary: when CORDIS received it. Deliverable:
                   approximate - CORDIS gives only when its record was last
                   updated, so that year is kept within the project's years
    text           report summaries: objectives, results, work performed
    keywords       the project's ECOSYSTEX keywords
    url            report summary: its CORDIS page; deliverable: the document

Usage
-----
    python db/mysql/import_project_reports.py              # dry run: fetch and count
    python db/mysql/import_project_reports.py --apply      # (re)create project_reports + the page's file
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_funding import get_engine   # noqa: E402

TABLE = "project_reports"
ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "cordis_results"
PAGE_FILE = ROOT / "site" / "articles" / "data" / "project_reports.json"
SOURCE_LABEL = "ECOSYSTEX project reports"   # the page lists it with the journals
USER_AGENT = "TELL research index (https://tell.newtexeco.nl)"
DELIVERABLE_TYPES = {"R"}                     # R: document, report

CREATE_SQL = f"""
CREATE TABLE {TABLE} (
    report_id   VARCHAR(16)   NOT NULL,
    kind        VARCHAR(16)   NOT NULL,
    acronym     VARCHAR(64)   NOT NULL,
    programme   VARCHAR(32),
    grant_id    VARCHAR(64),
    title       VARCHAR(512)  NOT NULL,
    label       VARCHAR(64),
    year        SMALLINT,
    text        TEXT,
    keywords    VARCHAR(1024),
    url         VARCHAR(512),
    PRIMARY KEY (report_id),
    KEY idx_acronym (acronym),
    FULLTEXT KEY ft_text (title, text, keywords)
) DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci
"""
COLUMNS = re.findall(r"^\s{4}([a-z_]+)\s", CREATE_SQL, flags=re.M)
LIMITS = {name: int(n) for name, n in
          re.findall(r"^\s{4}(\w+)\s+VARCHAR\((\d+)\)", CREATE_SQL, flags=re.M)}
PAGE_FIELDS = ("id", "doi", "journal", "year", "title", "authors", "abstract", "keywords",
               "topics", "oa_url", "kind", "project")


# ── Fetching ──────────────────────────────────────────────────────────────────
_last_request = 0.0


def fetch(url: str, cache_file: Path, refresh: bool) -> bytes | None:
    """The document at url, from the cache if there; None if CORDIS has none."""
    global _last_request
    if cache_file.exists() and not refresh:
        data = cache_file.read_bytes()
        return data or None
    wait = 1.0 - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
            if "xml" not in (resp.headers.get("Content-Type") or ""):
                data = b""             # an HTML page: CORDIS has no such record
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        data = b""
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_bytes(data)       # an empty file remembers "not on CORDIS"
    return data or None


# ── Parsing ───────────────────────────────────────────────────────────────────
def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _child(el, name):
    return next((c for c in el if _tag(c) == name), None)


def _text(el, name) -> str:
    c = _child(el, name)
    return " ".join((c.text or "").split()) if c is not None else ""


def _codes(result) -> set[str]:
    return {c.text for c in result.iter() if _tag(c) == "code" and c.text}


def _year(*stamps) -> int | None:
    for s in stamps:
        m = re.match(r"(\d{4})-", s or "")
        if m:
            return int(m.group(1))
    return None


def project_results(xml: bytes):
    """(kind, element) for each report summary and report deliverable."""
    root = ET.fromstring(xml)
    for el in root.iter():
        if _tag(el) != "result":
            continue
        codes = _codes(el)
        if "pubsum" in codes:
            yield "report_summary", el
        elif "deliverable" in codes and codes & DELIVERABLE_TYPES:
            yield "deliverable", el


def report_label(title: str, description: str) -> str:
    m = re.match(r"\s*Periodic Reporting for period (\d+)", title, re.I)
    if m:
        return f"Periodic report {m.group(1)}"
    if re.match(r"\s*Final Report", title, re.I) or description == "final":
        return "Final report"
    return "Report summary"


def summary_text(xml: bytes) -> tuple[str, dict]:
    """The report's text: objectives, results, then work performed (so that a
    shortened text keeps the results)."""
    root = ET.fromstring(xml)
    res = next((e for e in root.iter() if _tag(e) == "result"), root)
    parts = {k: _text(res, k) for k in ("summary", "finalResults", "workPerformed")}
    heads = {"summary": "", "finalResults": "Results: ", "workPerformed": "Work performed: "}
    body = "\n\n".join(heads[k] + v for k, v in parts.items() if v)
    meta = {"title": _text(res, "title"), "teaser": _text(res, "teaser"),
            "description": _text(res, "description"),
            "date": _text(res, "sourceUpdateDate") or _text(res, "contentUpdateDate")}
    return body or meta["teaser"], meta


def collect(projects: list[dict], refresh: bool) -> tuple[list[dict], Counter]:
    rows, stats = [], Counter()
    for p in projects:
        m = re.search(r"(\d{6,9})$", p["grant_id"] or "")
        if not m:
            stats["no grant number"] += 1
            continue
        grant = m.group(1)
        xml = fetch(f"https://cordis.europa.eu/project/id/{grant}?format=xml",
                    CACHE / f"project_{grant}.xml", refresh)
        if not xml:
            stats["not on CORDIS"] += 1
            continue
        stats["on CORDIS"] += 1
        seen_titles = set()
        for kind, el in project_results(xml):
            rcn, title = _text(el, "rcn"), _text(el, "title")
            if not rcn or not title:
                continue
            base = {"report_id": f"C{rcn}", "kind": kind, "acronym": p["acronym"],
                    "programme": p["programme"], "grant_id": p["grant_id"],
                    "keywords": p["keywords"]}
            if kind == "report_summary":
                rxml = fetch(f"https://cordis.europa.eu/result/rcn/{rcn}?format=xml",
                             CACHE / f"result_{rcn}.xml", refresh)
                if not rxml:
                    continue
                body, meta = summary_text(rxml)
                rows.append({**base, "title": meta["title"] or title,
                             "label": report_label(meta["title"] or title, meta["description"]),
                             "year": _year(meta["date"], _text(el, "sourceUpdateDate")),
                             "text": body or None,
                             "url": f"https://cordis.europa.eu/project/id/{grant}/reporting"})
            else:
                key = title.lower()
                if key in seen_titles:            # the same deliverable, resubmitted
                    continue
                seen_titles.add(key)
                link = next((c for c in el.iter() if _tag(c) == "webLink"), None)
                url = _text(link, "physUrl") if link is not None else ""
                # CORDIS gives no submission date, only when the record was
                # last updated, which can be years later: keep it within the
                # project's own years.
                year = _year(_text(el, "sourceUpdateDate"), _text(el, "contentUpdateDate"))
                if year and p["end_year"]:
                    year = min(year, p["end_year"])
                if year and p["start_year"]:
                    year = max(year, p["start_year"])
                rows.append({**base, "title": title, "label": "Deliverable",
                             "year": year,
                             "text": None,
                             "url": url or f"https://cordis.europa.eu/project/id/{grant}/results"})
            stats[kind] += 1
    return rows, stats


# ── Output ────────────────────────────────────────────────────────────────────
def check(rows: list[dict]) -> None:
    ids = Counter(r["report_id"] for r in rows)
    dups = [k for k, n in ids.items() if n > 1]
    if dups:
        raise SystemExit(f"duplicate report_id: {dups[:5]}")
    for r in rows:
        for col, limit in LIMITS.items():
            if r.get(col) and len(r[col]) > limit:
                r[col] = r[col][:limit - 1] + "…"


def summary(rows: list[dict], stats: Counter) -> None:
    print(f"projects: {stats['on CORDIS']} on CORDIS, {stats['not on CORDIS']} not, "
          f"{stats['no grant number']} without an EU grant number")
    print(f"{stats['report_summary']} report summaries, {stats['deliverable']} report deliverables, "
          f"from {len({r['acronym'] for r in rows})} projects")
    for prog, n in Counter(r["programme"] for r in rows).most_common():
        print(f"  {prog:28} {n:4}")
    texts = [len(r["text"]) for r in rows if r["text"]]
    if texts:
        print(f"report text: {sum(texts) / len(texts):,.0f} characters on average")


def write(engine, rows: list[dict]) -> None:
    insert = text(f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) "
                  f"VALUES ({', '.join(':' + c for c in COLUMNS)})")
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))
        conn.execute(text(CREATE_SQL))
        if rows:
            conn.execute(insert, rows)
        n = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE}")).scalar()
    print(f"wrote {n:,} rows to {TABLE}")


def write_page(rows: list[dict]) -> None:
    """The page's file, in the shape of journal_articles.json: the reports
    are one more source next to the journals."""
    ordered = sorted(rows, key=lambda r: (-(r["year"] or 0), r["acronym"].lower(), r["title"].lower()))
    def page_row(r):
        topic = f"{r['label']} · {r['acronym']} ({r['programme']})"
        return {"id": r["report_id"], "doi": None, "journal": SOURCE_LABEL, "year": r["year"],
                "title": r["title"], "authors": None, "abstract": r["text"],
                "keywords": r["keywords"], "topics": None, "oa_url": r["url"],
                "kind": r["kind"], "project": topic}
    PAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PAGE_FILE.write_text(json.dumps({
        "meta": {"source": "CORDIS (cordis.europa.eu), for the ECOSYSTEX member projects",
                 "_about": f"The {TABLE} table, for the articles page. Written by "
                           "db/mysql/import_project_reports.py --apply.",
                 "generated": date.today().isoformat(), "reports": len(rows)},
        "fields": list(PAGE_FIELDS),
        "rows": [[page_row(r)[f] for f in PAGE_FIELDS] for r in ordered],
    }, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {len(rows):,} reports to {PAGE_FILE.relative_to(ROOT)} "
          f"({PAGE_FILE.stat().st_size / 1e6:.1f} MB)")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true",
                   help=f"replace the {TABLE} table and the page's file (default: dry run)")
    p.add_argument("--page-only", action="store_true",
                   help="write only the page's file, not the table")
    p.add_argument("--refresh", action="store_true",
                   help=f"fetch everything again instead of using {CACHE.relative_to(ROOT)}")
    args = p.parse_args()

    engine = get_engine()
    with engine.connect() as conn:
        projects = [dict(r) for r in conn.execute(text(
            "SELECT acronym, programme, grant_id, keywords, start_year, end_year FROM ecosystex_projects "
            "ORDER BY acronym")).mappings()]
    rows, stats = collect(projects, args.refresh)
    check(rows)
    summary(rows, stats)
    if args.apply:
        write(engine, rows)
    if args.apply or args.page_only:
        write_page(rows)
    if not args.apply:
        print(f"\ndry run: the table was not written. Rerun with --apply to replace {TABLE}"
              f" and {PAGE_FILE.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
