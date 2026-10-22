"""
import_funding.py
─────────────────
Load every research-funding project behind the funding-network page
(site/network/) into one database table, `funding_projects`.

Sources (the JSON files the page loads, in site/network/data/)
-------------------------------------------------------------
    nwo       nwo_textile_raw.json            NWO / SIA, all 4,090 projects
    erasmus   erasmus_textile_projects.json   Erasmus+, projects with a Dutch partner
    cordis    cordis_textile_projects.json    Horizon Europe + Horizon 2020, idem

All projects are kept, not only the textile ones: `textile_keywords` lists the
page's default keywords a project matches (same list, read from the page, and
the same substring match), so `WHERE textile_keywords <> ''` gives the set the
page shows by default.

Columns
-------
    source, project_id   primary key; project_id is only unique within a source
    programme            NWO | Erasmus+ | Horizon Europe | Horizon 2020
    year                 the year the page filters on: start year, or the call
                         year for Erasmus+ (which has no dates)
    grant_eur            the grant in euros: the EU contribution as stated by the
                         source, or, where the source has no amount (all of NWO),
                         an estimate from the funding scheme (see NWO_ESTIMATES)
    grant_basis          'stated' or 'estimate': where grant_eur comes from
    coordinator          coordinating organisation (NWO: the main applicant's)
    members              JSON list of the partners as given by the source
    members_nl           partners in the Netherlands; for NWO, all of them (the
                         source has no countries, and the page treats NWO as Dutch)
    project_url          the project's page at the funder
    project_website      the project's own site, where the source has one

Each run replaces the table, so rerun it after refreshing the JSON.

Usage
-----
    python db/mysql/import_funding.py           # dry run: counts, nothing written
    python db/mysql/import_funding.py --apply   # (re)create funding_projects
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ENV_PATH = Path(__file__).resolve().parent / ".env"
NETWORK_DIR = Path(__file__).resolve().parents[2] / "site" / "network"
DATA_DIR = NETWORK_DIR / "data"
TABLE = "funding_projects"

NL_CODES = {"NL", "NLD", "NETHERLANDS", "THE NETHERLANDS"}     # as in the page
NWO_PI_ROLES = ("main applicant", "project leader")             # in order of preference

# NWO publishes no amounts. Typical SIA/NWO award per funding scheme (prefix of
# the scheme name), the same rule the page uses (loadData in site/network/
# index.html) - change both together. Other schemes get NWO_ESTIMATE_DEFAULT.
NWO_ESTIMATES = (("raak-pro", 700_000), ("raak-mkb", 400_000),
                 ("raak-publiek", 300_000), ("kiem", 40_000))
NWO_ESTIMATE_DEFAULT = 300_000

CREATE_SQL = f"""
CREATE TABLE {TABLE} (
    source              VARCHAR(16)   NOT NULL,
    project_id          VARCHAR(64)   NOT NULL,
    programme           VARCHAR(32)   NOT NULL,
    funding_scheme      VARCHAR(255),
    acronym             VARCHAR(64),
    title               VARCHAR(512)  NOT NULL,
    summary             MEDIUMTEXT,
    topics              TEXT,
    start_date          DATE,
    end_date            DATE,
    year                SMALLINT,
    status              VARCHAR(32),
    grant_eur           DECIMAL(14,2),
    grant_basis         VARCHAR(8),
    coordinator         VARCHAR(255),
    coordinator_country VARCHAR(8),
    pi_name             VARCHAR(255),
    members_total       SMALLINT      NOT NULL,
    members_nl          SMALLINT      NOT NULL,
    members             JSON          NOT NULL,
    textile_keywords    VARCHAR(512)  NOT NULL,
    project_url         VARCHAR(512),
    project_website     VARCHAR(512),
    PRIMARY KEY (source, project_id),
    KEY idx_programme_year (programme, year)
) DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci
"""

COLUMNS = re.findall(r"^\s{4}([a-z_]+)\s", CREATE_SQL, flags=re.M)   # not PRIMARY/KEY
# String columns with a length limit, checked before anything is written
LIMITS = {name: int(n) for name, n in
          re.findall(r"^\s{4}(\w+)\s+VARCHAR\((\d+)\)", CREATE_SQL, flags=re.M)}


def get_engine():
    load_dotenv(ENV_PATH)
    return create_engine(
        f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST')}:25060/{os.getenv('DB_NAME')}",
        connect_args={"ssl": {"ca": os.getenv("DB_CA_CERT")}},
    )


def page_keywords() -> list[str]:
    """The page's DEFAULT_KEYWORDS, so the table matches what the page shows."""
    page = (NETWORK_DIR / "index.html").read_text(encoding="utf-8")
    block = re.search(r"const DEFAULT_KEYWORDS\s*=\s*\[(.*?)\];", page, flags=re.S)
    if not block:
        raise SystemExit("DEFAULT_KEYWORDS not found in site/network/index.html")
    return re.findall(r"'([^']*)'", block.group(1))


def load(name: str) -> list[dict]:
    data = json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("projects", [])


def _str(v) -> str | None:
    v = str(v).strip() if v is not None else ""
    return v or None


def _date(v) -> str | None:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(v or ""))
    return m.group(1) if m else None


def _year(v) -> int | None:
    m = re.match(r"(\d{4})", str(v or "").strip())
    return int(m.group(1)) if m else None


def _keywords(hay: str, keywords: list[str]) -> str:
    hay = hay.lower()
    return ", ".join(k for k in keywords if k.lower() in hay)


def nwo_estimate(scheme: str | None) -> int:
    scheme = (scheme or "").strip().lower()
    return next((eur for prefix, eur in NWO_ESTIMATES if scheme.startswith(prefix)),
                NWO_ESTIMATE_DEFAULT)


def nwo_row(p: dict, keywords: list[str]) -> dict:
    members = p.get("project_members") or []
    pi = next((m for role in NWO_PI_ROLES for m in members
               if (m.get("role") or "").strip().lower() == role),
              members[0] if members else None)
    pi_name = f"{pi.get('first_name') or ''} {pi.get('last_name') or ''}".strip() if pi else ""
    return {
        "source": "nwo", "project_id": _str(p["project_id"]), "programme": "NWO",
        "funding_scheme": _str(p.get("funding_scheme")), "acronym": None,
        "title": _str(p.get("title")) or "(no title)",
        "summary": _str(p.get("summary_en")) or _str(p.get("summary_nl")),
        "topics": None,
        "start_date": _date(p.get("start_date")), "end_date": _date(p.get("end_date")),
        "year": _year(p.get("start_date")), "status": None,
        "grant_eur": nwo_estimate(p.get("funding_scheme")), "grant_basis": "estimate",
        "coordinator": _str((pi or {}).get("organisation", "").split("||")[0]),
        "coordinator_country": None, "pi_name": pi_name or None,
        "members_total": len(members), "members_nl": len(members),
        "members": json.dumps(members, ensure_ascii=False),
        "textile_keywords": _keywords(" ".join(
            p.get(k) or "" for k in ("title", "summary_nl", "summary_en")), keywords),
        "project_url": f"https://www.nwo.nl/projecten/{p['project_id']}",
        "project_website": None,
    }


def eu_row(p: dict, source: str, keywords: list[str]) -> dict:
    members = p.get("project_members") or []
    grant = p.get("ec_grant")
    try:
        grant = round(float(str(grant).replace(" ", "").replace(",", ".")), 2)
    except (TypeError, ValueError):
        grant = None
    return {
        "source": source, "project_id": _str(p["project_id"]),
        "programme": _str(p.get("programme")) or "?",
        "funding_scheme": _str(p.get("funding_scheme")), "acronym": _str(p.get("acronym")),
        "title": _str(p.get("title_en")) or "(no title)",
        "summary": _str(p.get("objective")), "topics": _str(p.get("topics")),
        "start_date": _date(p.get("start_date")), "end_date": _date(p.get("end_date")),
        "year": _year(p.get("call_year") if source == "erasmus" else p.get("start_date")),
        "status": _str(p.get("status")), "grant_eur": grant,
        "grant_basis": "stated" if grant is not None else None,
        "coordinator": _str(p.get("_pi_institution")),
        "coordinator_country": _str(p.get("_pi_country")), "pi_name": None,
        "members_total": len(members),
        "members_nl": sum((m.get("country") or "").upper() in NL_CODES for m in members),
        "members": json.dumps(members, ensure_ascii=False),
        "textile_keywords": _keywords(" ".join(
            p.get(k) or "" for k in ("title_en", "objective", "topics")), keywords),
        "project_url": _str(p.get("project_page_url")),
        "project_website": _str(p.get("project_website")),
    }


def is_nl(row: dict) -> bool:
    """The page drops EU projects without a Dutch coordinator or partner."""
    return (row["source"] == "nwo" or row["members_nl"] > 0
            or (row["coordinator_country"] or "").upper() in NL_CODES)


def build_rows() -> list[dict]:
    keywords = page_keywords()
    rows = [nwo_row(p, keywords) for p in load("nwo_textile_raw.json")]
    for name, source in (("erasmus_textile_projects.json", "erasmus"),
                         ("cordis_textile_projects.json", "cordis")):
        rows += [r for r in (eu_row(p, source, keywords) for p in load(name)) if is_nl(r)]
    return rows


def check(rows: list[dict]) -> None:
    keys = Counter((r["source"], r["project_id"]) for r in rows)
    dups = [k for k, n in keys.items() if n > 1]
    if dups:
        raise SystemExit(f"duplicate (source, project_id): {dups[:5]}")
    too_long = [(r["source"], r["project_id"], col, len(r[col]))
                for r in rows for col, limit in LIMITS.items()
                if r[col] is not None and len(r[col]) > limit]
    if too_long:
        raise SystemExit(f"values longer than their column: {too_long[:5]}")


def summary(rows: list[dict]) -> None:
    print(f"{len(rows)} projects")
    for prog in sorted({r["programme"] for r in rows}):
        sel = [r for r in rows if r["programme"] == prog]
        textile = sum(bool(r["textile_keywords"]) for r in sel)
        years = [r["year"] for r in sel if r["year"]]
        span = f"{min(years)}-{max(years)}" if years else "-"
        grants = ", ".join(
            f"EUR {sum(r['grant_eur'] for r in sel if r['grant_basis'] == basis)/1e6:,.1f}M {basis}"
            for basis in ("stated", "estimate") if any(r["grant_basis"] == basis for r in sel))
        missing = sum(r["grant_eur"] is None for r in sel)
        print(f"  {prog:15} {len(sel):5} projects, {textile:5} textile, years {span:9},"
              f" grants {grants}" + (f", {missing} without" if missing else ""))


def write(engine, rows: list[dict]) -> None:
    insert = text(f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) "
                  f"VALUES ({', '.join(':' + c for c in COLUMNS)})")
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))
        conn.execute(text(CREATE_SQL))
        conn.execute(insert, rows)
        n = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE}")).scalar()
    print(f"wrote {n} rows to {TABLE}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true",
                   help=f"replace the {TABLE} table (default: dry run)")
    args = p.parse_args()

    rows = build_rows()
    check(rows)
    summary(rows)
    if args.apply:
        write(get_engine(), rows)
    else:
        print(f"dry run: nothing written. Rerun with --apply to replace {TABLE}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
