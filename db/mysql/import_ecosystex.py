"""
import_ecosystex.py
───────────────────
Load the ECOSYSTEX member projects (the projects listed at
ecosystex.eu/member-projects, compiled into a workbook) into one table,
`ecosystex_projects`, and link each one to its record in `funding_projects`
where TELL has it.

Workbook (ECOSYSTEX_member_projects.xlsx)
-----------------------------------------
    Projects     one row per project                 -> one row per project
    Partners     one row per organisation per project -> the `partners` JSON column
    Summary, Corrections                                (not imported)

Link to TELL
------------
`tell_source` + `tell_project_id` point at funding_projects (source, project_id),
and so at textile_projects where the project is a textile project there. They
are found by the project's grant id (the 6-9 digit CORDIS / EACEA number, or
the full Erasmus+ id such as 2021-1-EL01-KA220-VET-000034695), else by its
acronym (CORDIS acronym, or an Erasmus+ title starting with it); `tell_match`
says which. NULL where TELL has no such project: the funding network loads NWO,
Erasmus+ and Horizon Europe / Horizon 2020 projects with a Dutch partner only.

`programme` is the funding programme in short (Horizon Europe, Horizon 2020,
Erasmus+, Interreg, LIFE, Digital Europe, Single Market Programme, Creative
Europe, I3); `funding_programme` keeps the workbook's full text.

Columns
-------
    acronym                  primary key
    title, category, status  ECOSYSTEX category; status as of the workbook's date
    programme, funding_programme, grant_id
    start_date, end_date     as given: YYYY-MM-DD, YYYY-MM or YYYY
    start_year, end_year
    description, keywords
    coordinator, coordinator_country, coordinator_type
    partners_total ... partners_other   partner counts by organisation type
    partners_nl              partners in the Netherlands
    partners                 JSON list: organisation, country, type, role, is_coordinator
    total_budget_eur, eu_contribution_eur, eu_share, budget_notes
    website, sources, research_notes
    tell_source, tell_project_id, tell_match

Each run replaces the table. Rerun it after funding_projects is rebuilt, so
the links follow.

The network page
----------------
--apply also writes site/network/data/ecosystex_projects.json: every project
in the shape of the page's CORDIS data, with its link to TELL (`tell`). Its
partners (`project_members`) are the CORDIS participants where the grant id is
in the CORDIS exports (data/cordis, see build_network_locations.py): those
carry the same names as the rest of the page's data, so an organisation is one
dot, and CORDIS knows where they are. Elsewhere they are the workbook's
partners, with the country as a code (CORDIS's: EL for Greece, UK for the
United Kingdom).

build_textile_projects.py then adds the ECOSYSTEX projects that are not already
textile projects to textile_projects, as source 'ecosystex', so the page shows
them. Order after new data:

    import_funding.py --apply -> import_ecosystex.py --apply
        -> build_textile_projects.py --apply -> build_network_locations.py

Usage
-----
    python db/mysql/import_ecosystex.py                 # dry run: counts and links, nothing written
    python db/mysql/import_ecosystex.py --apply         # (re)create ecosystex_projects + the page's file
    python db/mysql/import_ecosystex.py --xlsx PATH     # another copy of the workbook
    python db/mysql/import_ecosystex.py --cordis-dir DIR
"""

from __future__ import annotations

import argparse
import io
import json
import math
import re
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from import_funding import DATA_DIR, get_engine

TABLE = "ecosystex_projects"
DEFAULT_XLSX = Path.home() / "Desktop" / "Data" / "TELL" / "ECOSYSTEX_member_projects.xlsx"
PAGE_FILE = DATA_DIR / "ecosystex_projects.json"
CORDIS_DIR = Path(__file__).resolve().parents[2] / "data" / "cordis"
CORDIS_EXPORTS = ("cordis-HORIZONprojects-xlsx.zip", "cordis-h2020projects-xlsx.zip")

# Workbook country -> code, as CORDIS writes them (EL, UK)
COUNTRY_CODES = {
    "Austria": "AT", "Belgium": "BE", "Bosnia and Herzegovina": "BA", "Bulgaria": "BG",
    "Croatia": "HR", "Cyprus": "CY", "Czechia": "CZ", "Czech Republic": "CZ", "Denmark": "DK",
    "Egypt": "EG", "Estonia": "EE", "Faroe Islands": "FO", "Finland": "FI", "France": "FR",
    "Germany": "DE", "Greece": "EL", "Hungary": "HU", "Iceland": "IS", "Ireland": "IE",
    "Israel": "IL", "Italy": "IT", "Latvia": "LV", "Lebanon": "LB", "Lithuania": "LT",
    "Luxembourg": "LU", "Moldova": "MD", "Montenegro": "ME", "Netherlands": "NL",
    "New Zealand": "NZ", "North Macedonia": "MK", "Norway": "NO", "Peru": "PE", "Poland": "PL",
    "Portugal": "PT", "Romania": "RO", "Serbia": "RS", "Slovakia": "SK", "Slovenia": "SI",
    "Spain": "ES", "Sweden": "SE", "Switzerland": "CH", "Tunisia": "TN", "Türkiye": "TR",
    "Turkey": "TR", "Ukraine": "UA", "United Kingdom": "UK", "United States": "US",
}

CREATE_SQL = f"""
CREATE TABLE {TABLE} (
    acronym             VARCHAR(64)   NOT NULL,
    title               VARCHAR(512),
    category            VARCHAR(64)   NOT NULL,
    status              VARCHAR(32),
    programme           VARCHAR(32)   NOT NULL,
    funding_programme   VARCHAR(255),
    grant_id            VARCHAR(64),
    start_date          VARCHAR(10),
    end_date            VARCHAR(10),
    start_year          SMALLINT,
    end_year            SMALLINT,
    description         TEXT,
    keywords            VARCHAR(512),
    coordinator         VARCHAR(255),
    coordinator_country VARCHAR(64),
    coordinator_type    VARCHAR(64),
    partners_total      SMALLINT      NOT NULL,
    partners_academic   SMALLINT      NOT NULL,
    partners_research   SMALLINT      NOT NULL,
    partners_business   SMALLINT      NOT NULL,
    partners_sme        SMALLINT      NOT NULL,
    partners_network    SMALLINT      NOT NULL,
    partners_public     SMALLINT      NOT NULL,
    partners_ngo        SMALLINT      NOT NULL,
    partners_education  SMALLINT      NOT NULL,
    partners_other      SMALLINT      NOT NULL,
    partners_nl         SMALLINT      NOT NULL,
    partners            JSON          NOT NULL,
    total_budget_eur    DECIMAL(14,2),
    eu_contribution_eur DECIMAL(14,2),
    eu_share            DECIMAL(5,4),
    budget_notes        TEXT,
    website             VARCHAR(255),
    sources             TEXT,
    research_notes      TEXT,
    tell_source         VARCHAR(16),
    tell_project_id     VARCHAR(64),
    tell_match          VARCHAR(16),
    PRIMARY KEY (acronym),
    KEY idx_tell (tell_source, tell_project_id)
) DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci
"""

COLUMNS = re.findall(r"^\s{4}([a-z_]+)\s", CREATE_SQL, flags=re.M)   # not PRIMARY/KEY
LIMITS = {name: int(n) for name, n in
          re.findall(r"^\s{4}(\w+)\s+VARCHAR\((\d+)\)", CREATE_SQL, flags=re.M)}

# Workbook column -> table column (Projects sheet)
SHEET_COLUMNS = {
    "Acronym": "acronym", "Full title": "title", "ECOSYSTEX category": "category",
    "Status (27 Oct 2026)": "status", "Funding programme": "funding_programme",
    "Grant / project ID": "grant_id", "Start": "start_date", "End": "end_date",
    "Description": "description", "Keywords": "keywords", "Coordinator": "coordinator",
    "Coordinator country": "coordinator_country", "Coordinator type": "coordinator_type",
    "# partners (all)": "partners_total", "# Academic": "partners_academic",
    "# Research org / RTO": "partners_research", "# Business (all)": "partners_business",
    "of which SME": "partners_sme", "# Network / association / cluster": "partners_network",
    "# Public body": "partners_public", "# NGO / civil society": "partners_ngo",
    "# Education & training": "partners_education", "# Other": "partners_other",
    "Total budget (EUR)": "total_budget_eur", "EU contribution (EUR)": "eu_contribution_eur",
    "EU share": "eu_share", "Budget notes": "budget_notes", "Website": "website",
    "Sources": "sources", "Research notes / gaps": "research_notes",
}

# Short programme name, from the workbook's 'Funding programme' text (first match wins)
PROGRAMMES = (
    (r"\bH2020\b|Horizon 2020|BBI JU", "Horizon 2020"),
    (r"Horizon Europe|CBE JU|HORIZON-", "Horizon Europe"),
    (r"Erasmus", "Erasmus+"),
    (r"Interregional Innovation Investments|\bI3\b", "I3"),
    (r"Interreg", "Interreg"),
    (r"\bLIFE\b", "LIFE"),
    (r"Digital Europe", "Digital Europe"),
    (r"Single Market|COSME", "Single Market Programme"),
    (r"Creative Europe", "Creative Europe"),
)
GRANT_NUMBER = re.compile(r"(?<!\d)(\d{6,9})(?!\d)")   # CORDIS / EACEA agreement number


def programme(text_: str) -> str:
    return next((name for rx, name in PROGRAMMES if re.search(rx, text_ or "")), "Other")


def _str(v) -> str | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    v = str(v).strip()
    return v or None


def _num(v) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _year(v) -> int | None:
    m = re.match(r"(\d{4})", v or "")
    return int(m.group(1)) if m else None


def read_workbook(path: Path) -> list[dict]:
    projects = pd.read_excel(path, sheet_name="Projects", dtype=object)
    partners = pd.read_excel(path, sheet_name="Partners", dtype=object)
    missing = set(SHEET_COLUMNS) - set(projects.columns)
    if missing:
        raise SystemExit(f"Projects sheet lacks the columns {sorted(missing)}")

    by_project: dict[str, list[dict]] = {}
    for p in partners.to_dict("records"):
        by_project.setdefault(_str(p["Project"]), []).append({
            "organisation": _str(p["Organisation"]), "country": _str(p["Country"]),
            "type": _str(p["Organisation type"]), "role": _str(p["Role"]),
            "is_coordinator": _str(p["Is coordinator"]) == "Yes",
        })
    unknown = set(by_project) - {_str(a) for a in projects["Acronym"]}
    if unknown:
        raise SystemExit(f"Partners sheet names projects not on the Projects sheet: {sorted(unknown)}")

    rows = []
    for rec in projects.to_dict("records"):
        row = {col: rec[name] for name, col in SHEET_COLUMNS.items()}
        for col in row:
            if col.startswith("partners_"):
                row[col] = int(_num(row[col]) or 0)
            elif col in ("total_budget_eur", "eu_contribution_eur", "eu_share"):
                row[col] = _num(row[col])
            else:
                row[col] = _str(row[col])
        members = by_project.get(row["acronym"], [])
        row.update({
            "programme": programme(row["funding_programme"]),
            "start_year": _year(row["start_date"]), "end_year": _year(row["end_date"]),
            "partners_nl": sum((m["country"] or "") == "Netherlands" for m in members),
            "partners": json.dumps(members, ensure_ascii=False),
        })
        rows.append(row)
    return rows


def link(rows: list[dict], engine) -> None:
    """Fill tell_source / tell_project_id / tell_match from funding_projects."""
    with engine.connect() as conn:
        funding = [dict(r._mapping) for r in conn.execute(text(
            "SELECT source, project_id, acronym, title FROM funding_projects "
            "WHERE source IN ('cordis', 'erasmus')"))]
    by_id = {}
    for f in funding:
        by_id.setdefault(f["project_id"], []).append(f)

    for row in rows:
        row.update(tell_source=None, tell_project_id=None, tell_match=None)
        gid = row["grant_id"] or ""
        number = GRANT_NUMBER.search(gid)
        candidates = by_id.get(gid, []) + (by_id.get(number.group(1), []) if number else [])
        how = "grant id"
        if not candidates:
            acr = row["acronym"].lower()
            candidates = [f for f in funding
                          if (f["source"] == "cordis" and (f["acronym"] or "").lower() == acr)
                          or (f["source"] == "erasmus" and (f["title"] or "").lower().startswith(acr + " "))]
            how = "acronym"
        if len({(f["source"], f["project_id"]) for f in candidates}) == 1:
            row.update(tell_source=candidates[0]["source"],
                       tell_project_id=candidates[0]["project_id"], tell_match=how)
        elif candidates:
            print(f"  ! {row['acronym']}: several matches by {how}, left unlinked: "
                  + ", ".join(f"{f['source']}:{f['project_id']}" for f in candidates))


def check(rows: list[dict]) -> None:
    dups = [k for k, n in Counter(r["acronym"] for r in rows).items() if n > 1]
    if dups:
        raise SystemExit(f"duplicate acronyms: {dups}")
    too_long = [(r["acronym"], col, len(r[col])) for r in rows for col, limit in LIMITS.items()
                if r[col] is not None and len(r[col]) > limit]
    if too_long:
        raise SystemExit(f"values longer than their column: {too_long[:5]}")


def report(rows: list[dict], engine) -> None:
    """Where each project stands in TELL, grouped."""
    with engine.connect() as conn:
        textile = {(r.source, r.project_id): r.relevance_score for r in conn.execute(text(
            "SELECT source, project_id, relevance_score FROM textile_projects"))}
    covered = {"Horizon Europe", "Horizon 2020", "Erasmus+"}
    groups: dict[str, list[str]] = {}
    for r in rows:
        key = (r["tell_source"], r["tell_project_id"])
        if key in textile:
            g = "0 in textile_projects from TELL's own data"
        elif ("ecosystex", r["acronym"]) in textile:
            g = "1 in textile_projects as source 'ecosystex'"
            groups.setdefault(g, []).append(r["acronym"])
            continue
        elif r["tell_source"]:
            g = "2 in funding_projects, below the textile threshold"
        elif not r["partners_nl"]:
            g = "5 no Dutch partner"
        elif r["programme"] in covered:
            g = "3 Dutch partner, but not in TELL's CORDIS / Erasmus+ data"
        else:
            g = f"4 Dutch partner, programme TELL doesn't load"
        groups.setdefault(g, []).append(
            r["acronym"] + (f" ({textile[key]})" if key in textile else "")
            + (f" [{r['programme']}]" if g[0] in "45" else ""))
    print(f"\n{len(rows)} ECOSYSTEX projects, {sum(bool(r['partners_nl']) for r in rows)} with a Dutch partner")
    for g in sorted(groups):
        print(f"\n{g[2:]}: {len(groups[g])}")
        print("  " + ", ".join(groups[g]))


def grant_number(row: dict) -> str | None:
    m = GRANT_NUMBER.search(row["grant_id"] or "")
    return m.group(1) if m else None


def cordis_participants(cordis_dir: Path, ids: set[str]) -> dict[str, list[dict]]:
    """projectID -> its participants in the CORDIS exports, in CORDIS's order."""
    found: dict[str, list[dict]] = {}
    for fname in CORDIS_EXPORTS:
        path = cordis_dir / fname
        if not path.exists():
            print(f"  ! {path} not found: those projects get the workbook's partners")
            continue
        with zipfile.ZipFile(path) as z:
            orgs = pd.read_excel(io.BytesIO(z.read("organization.xlsx")), dtype=str,
                                 usecols=["projectID", "name", "country", "role", "activityType", "order"])
        orgs = orgs[orgs["projectID"].isin(ids)].copy()
        orgs["order"] = pd.to_numeric(orgs["order"], errors="coerce")
        for pid, grp in orgs.sort_values("order").groupby("projectID"):
            found[pid] = [{"organisation": _str(m["name"]), "country": _str(m["country"]),
                           "role": _str(m["role"]), "type": _str(m["activityType"])}
                          for m in grp.to_dict("records") if _str(m["name"])]
    return found


def page_projects(rows: list[dict], cordis: dict[str, list[dict]]) -> list[dict]:
    """The projects as the network page reads them (the shape of its CORDIS data)."""
    unknown = Counter()
    out = []
    for r in rows:
        number = grant_number(r)
        members = cordis.get(number) if number else None
        source = "cordis" if members else "ecosystex"
        if not members:
            members = []
            for m in json.loads(r["partners"]):
                code = COUNTRY_CODES.get(m["country"] or "")
                if m["country"] and not code:
                    unknown[m["country"]] += 1
                members.append({"organisation": m["organisation"], "country": code or "",
                                "role": "coordinator" if m["is_coordinator"] else (m["role"] or "partner"),
                                "type": m["type"]})
        coord = next((m for m in members if (m["role"] or "").lower() == "coordinator"), None)
        urls = re.findall(r"https?://\S+", r["sources"] or "")
        out.append({
            "project_id": r["acronym"], "acronym": r["acronym"],
            "title_en": r["title"] or r["acronym"], "programme": r["programme"],
            "funding_scheme": r["funding_programme"], "grant_id": r["grant_id"],
            "category": r["category"], "status": r["status"],
            "start_date": r["start_date"], "end_date": r["end_date"],
            "ec_grant": r["eu_contribution_eur"], "total_cost": r["total_budget_eur"],
            "objective": r["description"], "topics": r["keywords"] or "",
            "_pi_institution": coord["organisation"] if coord else r["coordinator"],
            "_pi_country": coord["country"] if coord else COUNTRY_CODES.get(r["coordinator_country"] or "", ""),
            "project_page_url": (f"https://cordis.europa.eu/project/id/{number}" if source == "cordis"
                                 else (urls[0] if urls else None)),
            "project_website": r["website"],
            "members_source": source,
            "tell": f"{r['tell_source']}:{r['tell_project_id']}" if r["tell_source"] else None,
            "project_members": members,
        })
    if unknown:
        print(f"  ! countries without a code (left empty): {dict(unknown)}")
    return out


def write_page(rows: list[dict], cordis_dir: Path) -> None:
    ids = {n for n in (grant_number(r) for r in rows) if n}
    projects = page_projects(rows, cordis_participants(cordis_dir, ids))
    PAGE_FILE.write_text(json.dumps({
        "meta": {"source": "ECOSYSTEX member projects (ecosystex.eu/member-projects)",
                 "_about": "Written by db/mysql/import_ecosystex.py --apply. Partners from CORDIS where "
                           "members_source is 'cordis', else from the ECOSYSTEX workbook. `tell`: the same "
                           "project in TELL's own data, if any.",
                 "generated": date.today().isoformat(), "projects_kept": len(projects)},
        "projects": projects,
    }, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    by = Counter(p["members_source"] for p in projects)
    print(f"wrote {len(projects)} projects to {PAGE_FILE.relative_to(DATA_DIR.parents[2])} "
          f"(partners from CORDIS for {by['cordis']}, from the workbook for {by['ecosystex']})")


def write(engine, rows: list[dict]) -> None:
    insert = text(f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) "
                  f"VALUES ({', '.join(':' + c for c in COLUMNS)})")
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))
        conn.execute(text(CREATE_SQL))
        conn.execute(insert, rows)
        n = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE}")).scalar()
    print(f"\nwrote {n} rows to {TABLE}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true",
                   help=f"replace the {TABLE} table (default: dry run)")
    p.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX,
                   help=f"the workbook (default: {DEFAULT_XLSX})")
    p.add_argument("--cordis-dir", type=Path, default=CORDIS_DIR,
                   help=f"the CORDIS exports, for the partners (default: {CORDIS_DIR})")
    args = p.parse_args()

    rows = read_workbook(args.xlsx)
    engine = get_engine()
    link(rows, engine)
    check(rows)
    print(f"{len(rows)} projects; by programme: "
          + ", ".join(f"{k} {n}" for k, n in Counter(r["programme"] for r in rows).most_common()))
    print("linked to funding_projects: "
          + ", ".join(f"{n} by {k}" for k, n in Counter(r["tell_match"] for r in rows if r["tell_match"]).items()))
    report(rows, engine)
    if args.apply:
        write(engine, rows)
        write_page(rows, args.cordis_dir)
    else:
        print(f"\ndry run: nothing written. Rerun with --apply to replace {TABLE} and {PAGE_FILE.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
