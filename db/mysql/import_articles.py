"""
import_articles.py
──────────────────
Load the open-access journal articles (new_tell_scraper/fetch_textile_oa.py,
from OpenAlex) into one table, `journal_articles`, and write the data the
articles page (site/articles/) searches.

Source (textile_oa_articles.xlsx)
---------------------------------
One row per article from five open-access journals: Textiles (MDPI), Fibers
(MDPI), Sustainability (MDPI; only its textile articles), Fashion and Textiles
(SpringerOpen) and Journal of Engineered Fibers and Fabrics (SAGE).

Columns
-------
    openalex_id     primary key (W...)
    doi             without https://doi.org/
    journal, year, title
    authors         '; '-separated, in the article's order
    author_count
    abstract        as OpenAlex rebuilds it, without a leading 'Abstract' label
    keywords        '; '-separated, assigned by OpenAlex (not the authors')
    topics          '; '-separated OpenAlex topics
    oa_url          the open-access copy (PDF or landing page), where known

A FULLTEXT index on title, abstract and keywords is there for searching in SQL;
the page searches its own copy in the browser.

Each run replaces the table.

The page
--------
--apply also writes site/articles/data/journal_articles.json: the same
articles, as a list of fields and one row per article.

Usage
-----
    python db/mysql/import_articles.py                # dry run: counts, nothing written
    python db/mysql/import_articles.py --apply        # (re)create journal_articles + the page's file
    python db/mysql/import_articles.py --xlsx PATH    # another copy of the workbook
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from import_funding import get_engine

TABLE = "journal_articles"
DEFAULT_XLSX = Path.home() / "Desktop" / "Data" / "TELL" / "textile_oa_articles.xlsx"
PAGE_FILE = Path(__file__).resolve().parents[2] / "site" / "articles" / "data" / "journal_articles.json"

CREATE_SQL = f"""
CREATE TABLE {TABLE} (
    openalex_id     VARCHAR(16)   NOT NULL,
    doi             VARCHAR(128),
    journal         VARCHAR(64)   NOT NULL,
    year            SMALLINT,
    title           VARCHAR(512)  NOT NULL,
    authors         TEXT,
    author_count    SMALLINT      NOT NULL,
    abstract        TEXT,
    keywords        VARCHAR(1024),
    topics          VARCHAR(512),
    oa_url          VARCHAR(512),
    PRIMARY KEY (openalex_id),
    KEY idx_journal_year (journal, year),
    KEY idx_doi (doi),
    FULLTEXT KEY ft_text (title, abstract, keywords)
) DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci
"""

COLUMNS = re.findall(r"^\s{4}([a-z_]+)\s", CREATE_SQL, flags=re.M)   # not PRIMARY/KEY
LIMITS = {name: int(n) for name, n in
          re.findall(r"^\s{4}(\w+)\s+VARCHAR\((\d+)\)", CREATE_SQL, flags=re.M)}
SOURCE_COLUMNS = ("journal", "year", "title", "authors", "abstract", "keywords",
                  "topics", "doi", "openalex_id", "oa_url")
ABSTRACT_LABEL = re.compile(r"^\s*abstract\b[\s.:]*", re.I)   # 'Abstract The mid-20th century ...'
PAGE_FIELDS = ("id", "doi", "journal", "year", "title", "authors", "abstract", "keywords",
               "topics", "oa_url")


def _str(v) -> str | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    v = " ".join(str(v).split())   # one line, single spaces
    return v or None


def read_workbook(path: Path) -> list[dict]:
    df = pd.read_excel(path, dtype=str)
    missing = set(SOURCE_COLUMNS) - set(df.columns)
    if missing:
        raise SystemExit(f"{path.name} lacks the columns {sorted(missing)}")
    rows = []
    for rec in df.to_dict("records"):
        r = {c: _str(rec[c]) for c in SOURCE_COLUMNS}
        doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", r["doi"] or "", flags=re.I) or None
        abstract = ABSTRACT_LABEL.sub("", r["abstract"] or "") or None
        year = int(r["year"]) if (r["year"] or "").isdigit() else None
        authors = [a.strip() for a in (r["authors"] or "").split(";") if a.strip()]
        rows.append({**r, "doi": doi, "abstract": abstract, "year": year,
                     "authors": "; ".join(authors) or None, "author_count": len(authors)})
    return rows


def check(rows: list[dict]) -> None:
    dups = [k for k, n in Counter(r["openalex_id"] for r in rows).items() if n > 1]
    if dups:
        raise SystemExit(f"duplicate openalex_id: {dups[:5]}")
    if any(not r["openalex_id"] or not r["title"] or not r["journal"] for r in rows):
        raise SystemExit("rows without an openalex_id, title or journal")
    too_long = [(r["openalex_id"], col, len(r[col])) for r in rows for col, limit in LIMITS.items()
                if r[col] is not None and len(r[col]) > limit]
    if too_long:
        raise SystemExit(f"values longer than their column: {too_long[:5]}")


def summary(rows: list[dict]) -> None:
    years = [r["year"] for r in rows if r["year"]]
    print(f"{len(rows):,} articles, {min(years)}-{max(years)}")
    for journal, n in Counter(r["journal"] for r in rows).most_common():
        print(f"  {journal:45} {n:5,}")
    print(f"without abstract {sum(not r['abstract'] for r in rows)}, "
          f"without open-access link {sum(not r['oa_url'] for r in rows)}, "
          f"without DOI {sum(not r['doi'] for r in rows)}")


def write(engine, rows: list[dict]) -> None:
    insert = text(f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) "
                  f"VALUES ({', '.join(':' + c for c in COLUMNS)})")
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))
        conn.execute(text(CREATE_SQL))
        conn.execute(insert, rows)
        n = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE}")).scalar()
    print(f"wrote {n:,} rows to {TABLE}")


def write_page(rows: list[dict]) -> None:
    ordered = sorted(rows, key=lambda r: (-(r["year"] or 0), r["title"].lower()))
    PAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PAGE_FILE.write_text(json.dumps({
        "meta": {"source": "OpenAlex, via new_tell_scraper/fetch_textile_oa.py",
                 "_about": f"The {TABLE} table, for the articles page. Written by "
                           "db/mysql/import_articles.py --apply.",
                 "generated": date.today().isoformat(), "articles": len(rows)},
        "fields": list(PAGE_FIELDS),
        "rows": [[r["openalex_id" if f == "id" else f] for f in PAGE_FIELDS] for r in ordered],
    }, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {len(rows):,} articles to {PAGE_FILE.relative_to(PAGE_FILE.parents[3])} "
          f"({PAGE_FILE.stat().st_size / 1e6:.1f} MB)")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true",
                   help=f"replace the {TABLE} table and the page's file (default: dry run)")
    p.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX,
                   help=f"the workbook (default: {DEFAULT_XLSX})")
    args = p.parse_args()

    rows = read_workbook(args.xlsx)
    check(rows)
    summary(rows)
    if args.apply:
        write(get_engine(), rows)
        write_page(rows)
    else:
        print(f"\ndry run: nothing written. Rerun with --apply to replace {TABLE} and {PAGE_FILE.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
