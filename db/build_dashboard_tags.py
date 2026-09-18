"""
build_dashboard_tags.py
───────────────────────
Prepare an updated keyword string per organization for the TELL dashboard, by
adding the scraped website keywords to the tags the dashboard already shows.

Input (joined on website)
-------------------------
    tags.tags                       the current, curated tag string
    scraping17092026.`English keywords`   the scraped keywords, translated to
                                    English by new_tell_scraper/translate_keywords.py

The bigram columns are deliberately left out: they repeat the keywords as
phrases and are still in the language of the site.

What goes into the new string
-----------------------------
* every tag the organization already has, de-duplicated and in its current
  order. These are curated and kept whatever their frequency - 204 of the 513
  existing tags are used by fewer than three companies ('upcycle',
  'womenswear', 'loungewear') and dropping them would lose real information.
* then the scraped keywords, in the order the scraper ranked them, but only
  those used by at least --min-companies companies. A keyword one company uses
  is almost always a brand name, a surname or a typo, and the dashboard's
  Keywords filter is a dropdown: every kept term becomes an entry in it.

Output
------
Table `tags_scraped`: id, trade_name, website, tags_old, tags_new and the term
counts. Nothing writes to `tags`, so the dashboard keeps reading the old column
until someone points it at this one.

Usage
-----
    python db/build_dashboard_tags.py
    python db/build_dashboard_tags.py --min-companies 5
    python db/build_dashboard_tags.py --dry-run --csv out.csv
"""

from __future__ import annotations

import argparse
import collections
import os
import re
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ENV_PATH = Path(__file__).resolve().parent / "mysql" / ".env"
SCRAPE_TABLE = "scraping17092026"
TARGET_TABLE = "tags_scraped"
MIN_COMPANIES = 3

QUERY = f"""
    SELECT DISTINCT
        org.id,
        org.trade_name,
        org.website,
        org.main_activity,
        t.tags,
        s.`English keywords` AS english_keywords
    FROM organizations org
    JOIN {SCRAPE_TABLE} s ON org.website = s.website
    JOIN tags t ON t.id = org.id
"""

_COUNT_RE = re.compile(r"\s*\(\d+\)\s*$")


def get_engine():
    load_dotenv(ENV_PATH)
    return create_engine(
        f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST')}:25060/{os.getenv('DB_NAME')}",
        connect_args={"ssl": {"ca": os.getenv("DB_CA_CERT")}},
    )


def parse_terms(value) -> list[str]:
    """'fashion, shop, fashion' -> ['fashion', 'shop'] (order kept, no counts)."""
    if pd.isna(value):
        return []
    out: list[str] = []
    for part in str(value).split(","):
        term = _COUNT_RE.sub("", part.strip()).lower()
        term = re.sub(r"\s+", " ", term)
        if term and term not in out:
            out.append(term)
    return out


def build(df: pd.DataFrame, min_companies: int) -> pd.DataFrame:
    old_terms = [parse_terms(v) for v in df["tags"]]
    new_terms = [parse_terms(v) for v in df["english_keywords"]]

    # How many companies use each scraped keyword; the curated tags are not
    # counted here because they are kept regardless.
    company_count = collections.Counter()
    for terms in new_terms:
        company_count.update(set(terms))
    keep = {t for t, n in company_count.items() if n >= min_companies}

    rows = []
    for old, new in zip(old_terms, new_terms):
        combined = list(old)
        for term in new:
            if term in keep and term not in combined:
                combined.append(term)
        rows.append(combined)

    out = pd.DataFrame({
        "id": df["id"],
        "trade_name": df["trade_name"],
        "website": df["website"],
        "tags_old": [", ".join(t) for t in old_terms],
        "tags_new": [", ".join(t) for t in rows],
        "n_old": [len(t) for t in old_terms],
        "n_new": [len(t) for t in rows],
    })
    out.attrs["vocabulary"] = sorted({t for r in rows for t in r})
    out.attrs["dropped"] = len(company_count) - len(keep)
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--min-companies", type=int, default=MIN_COMPANIES,
                   help="keep a scraped keyword only when this many companies use it")
    p.add_argument("--table", default=TARGET_TABLE)
    p.add_argument("--csv", type=Path, default=None, help="also write a CSV copy")
    p.add_argument("--dry-run", action="store_true", help="do not write to the database")
    args = p.parse_args()

    engine = get_engine()
    df = pd.read_sql(text(QUERY), engine)
    print(f"joined {len(df):,} organizations "
          f"({df['english_keywords'].notna().sum():,} with scraped keywords)")

    out = build(df, args.min_companies)
    vocabulary = out.attrs["vocabulary"]
    gained = out["n_new"] - out["n_old"]
    print(f"keywords used by < {args.min_companies} companies dropped: "
          f"{out.attrs['dropped']:,}")
    print(f"vocabulary for the Keywords filter: {len(vocabulary):,} terms")
    print(f"terms per company: {out['n_old'].mean():.1f} -> {out['n_new'].mean():.1f} "
          f"(max {out['n_new'].max()}) · {int((gained > 0).sum()):,} companies gained terms")

    if args.csv:
        out.to_csv(args.csv, index=False, encoding="utf-8")
        print(f"wrote {args.csv}")

    if args.dry_run:
        print("dry run: database untouched")
        return 0

    with engine.begin() as conn:
        conn.execute(text("SET SESSION sql_require_primary_key = 0"))
        out.to_sql(args.table, conn, if_exists="replace", index=False, chunksize=500)
        conn.execute(text(f"ALTER TABLE `{args.table}` ADD PRIMARY KEY (id)"))
    print(f"wrote table `{args.table}` ({len(out):,} rows); `tags` left untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
