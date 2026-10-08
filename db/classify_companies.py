
"""
classify_companies.py
─────────────────────
Put every active company in one of three classes, plus Unclassified:

    Frontrunner              ahead in sustainability and/or digital
    Multinational            large and operating across borders
    Small-Medium Enterprise  everything else with evidence
    Unclassified             no website and no employee count: nothing to judge

There is nothing to learn from - no company in the database carries one of
these labels - so this is not a trained model. Each signal adds points, the
rules below are the whole classifier, and every company's score and the exact
signals that fired are stored with it, so a verdict can be checked and argued
with rather than taken on faith.

Signals (see SCORING below for the weights)
-------------------------------------------
    employees           the strongest size signal, but known for only 31% of
                        companies, and just 13 have 250 or more
    surface             floor area in m2, known for 89%
    legal form          'Foreign Legal Form' points abroad; a proprietorship
                        or partnership is a small business by construction
    website languages   how many languages the site offers: a proxy for
                        selling across borders
    keywords            from tags_scraped: international trade terms for
                        Multinational, sustainability and digital terms for
                        Frontrunner

A company that qualifies as both Frontrunner and Multinational is labelled
Frontrunner: it is the rarer and more interesting signal.

Usage
-----
    python db/classify_companies.py                 # show the split, write nothing
    python db/classify_companies.py --apply         # write table `company_class`
    python db/classify_companies.py --csv out.csv   # also save a copy to review
"""

from __future__ import annotations

import argparse
import os
import re
from collections import Counter
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import String, create_engine, text

ENV_PATH = Path(__file__).resolve().parent / "mysql" / ".env"
TARGET_TABLE = "company_class"

QUERY = """
    SELECT o.id, o.trade_name, o.employees, o.surface, o.legal_form, o.website,
           COALESCE(ts.tags_new, t.tags) AS tags,
           s.languages, s.keywords
    FROM organizations AS o
    LEFT JOIN tags AS t ON t.id = o.id
    LEFT JOIN tags_scraped AS ts ON ts.id = o.id
    LEFT JOIN (
        SELECT website, MIN(`Website languages`) AS languages,
               MIN(`English keywords`) AS keywords
        FROM scraping17092026 GROUP BY website
    ) AS s ON s.website = o.website
    WHERE o.status = 'Active'
"""

# ── Keyword signals ──────────────────────────────────────────────────────────
# Exact terms, never substrings: 'important' is not 'international'.
SUSTAINABILITY_STRONG = {
    "sustainability", "sustainable", "sustainable clothing", "sustainable materials",
    "sustainable solutions", "sustainable fashion", "circular", "circularity",
    "circular economy", "recycle", "recycled", "recycling", "recycled polyester",
    "upcycle", "upcycled", "upcycling", "organic cotton", "cradle to cradle",
    "fair trade", "oeko-tex", "gots", "second hand", "reuse", "biodegradable",
}
SUSTAINABILITY_WEAK = {
    "organic", "certified", "certification", "certifications", "fair",
    "repair", "repairs", "repairing", "clothing repair", "refurbished",
    "footprint", "climate", "co2", "environment", "environmentally friendly",
}
# Concrete capabilities, not adjectives: 'innovative' is what every site says
# about itself, while 'digital printing' or 'automation' is something a company
# actually does.
DIGITAL_STRONG = {
    "digital printing", "digitalisation", "digitization", "software", "platform",
    "automation", "automated", "3d", "3d printing", "artificial intelligence",
    "data driven", "configurator",
}
DIGITAL_WEAK = {
    "digital", "technology", "innovation", "innovative", "innovations", "smart",
    "app", "webshop", "official webshop", "online shops", "ecommerce", "e-commerce",
}
# Web-builder and parked-domain pages sell these; they say nothing about the
# company whose domain it is.
DIGITAL_BLOCKED = {
    "create an online store", "start an online store", "ecommerce software",
    "software webshop", "personal webshop", "webshop software", "web hosting",
}
INTERNATIONAL = {
    "export", "exporter", "exports", "import", "importer", "imports",
    "import export", "distribution", "distributor", "distributors",
    "international", "internationally", "international sales", "international brands",
    "international fashion", "international shipping", "global", "worldwide",
    "worldwide shipping", "worldwide importer", "countries", "overseas",
}
SMALL_LEGAL_FORMS = {"proprietorship", "general partnership (partnership)",
                     "civil partnership", "founding"}

# ── Scoring ──────────────────────────────────────────────────────────────────
SCORING = {
    "employees_large": 4,      # 250+
    "employees_medium": 2,     # 50-249
    "languages_many": 2,       # 4 or more
    "languages_some": 1,       # 3
    "foreign_legal_form": 2,
    "surface_large": 1,        # 10,000 m2 or more
    "international_keyword": 1,   # each, capped
    "sustainability_strong": 2,   # each, capped
    "sustainability_weak": 1,
    "digital_strong": 2,
    "digital_weak": 1,
}
MULTINATIONAL_AT = 4          # score needed for Multinational
MULTINATIONAL_EMPLOYEES = 50  # this many employees qualifies on its own
FRONTRUNNER_AT = 4         # score needed in one theme
FRONTRUNNER_BOTH_AT = 2    # or this much in both themes at once
KEYWORD_CAP = 3            # how many keyword hits a theme may count


def get_engine():
    load_dotenv(ENV_PATH)
    return create_engine(
        f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST')}:25060/{os.getenv('DB_NAME')}",
        connect_args={"ssl": {"ca": os.getenv("DB_CA_CERT")}},
    )


def split_terms(value) -> set:
    if pd.isna(value):
        return set()
    return {t.strip().lower() for t in str(value).split(",") if t.strip()}


def count_languages(value) -> int:
    if pd.isna(value) or not str(value).strip():
        return 0
    return len([p for p in re.split(r"[,\s]+", str(value)) if p])


def classify_row(row) -> dict:
    """Score one company and name the class. Returns the scores and evidence."""
    terms = split_terms(row["tags"])
    employees = row["employees"] if pd.notna(row["employees"]) else None
    surface = row["surface"] if pd.notna(row["surface"]) else None
    languages = count_languages(row["languages"])
    legal_form = ("" if pd.isna(row["legal_form"]) else str(row["legal_form"])).strip().lower()

    evidence, multinational, cross_border = [], 0, 0

    def add(points, label, abroad=False):
        nonlocal multinational, cross_border
        multinational += points
        if abroad:
            cross_border += points
        evidence.append(label)

    wholesale = "wholesale" in str(row.get("main_activity") or "").lower()

    if employees is not None and employees >= 250:
        add(SCORING["employees_large"], f"{int(employees)} employees")
    elif employees is not None and employees >= 50:
        add(SCORING["employees_medium"], f"{int(employees)} employees")
    # A wholesaler of this size buys or sells abroad as a matter of course,
    # which is what separates it from a company that is merely large.
    if wholesale and employees is not None and employees >= MULTINATIONAL_EMPLOYEES:
        add(SCORING["wholesale_at_scale"], "wholesale at scale", abroad=True)
    if languages >= 4:
        add(SCORING["languages_many"], f"{languages} website languages", abroad=True)
    elif languages == 3:
        add(SCORING["languages_some"], f"{languages} website languages", abroad=True)
    if legal_form == "foreign legal form":
        add(SCORING["foreign_legal_form"], "foreign legal form", abroad=True)
    if surface is not None and surface >= 10000:
        add(SCORING["surface_large"], f"{int(surface):,} m2")

    hits = sorted(terms & INTERNATIONAL)[:KEYWORD_CAP]
    if hits:
        add(SCORING["international_keyword"] * len(hits), ", ".join(hits), abroad=True)

    # A one-person business is not a multinational, whatever its website says.
    if legal_form in SMALL_LEGAL_FORMS and (employees is None or employees < 50):
        multinational = min(multinational, MULTINATIONAL_AT - 1)

    sus_strong = sorted(terms & SUSTAINABILITY_STRONG)[:KEYWORD_CAP]
    sus_weak = sorted(terms & SUSTAINABILITY_WEAK)[:KEYWORD_CAP]
    dig_strong = sorted((terms & DIGITAL_STRONG) - DIGITAL_BLOCKED)[:KEYWORD_CAP]
    dig_weak = sorted((terms & DIGITAL_WEAK) - DIGITAL_BLOCKED)[:KEYWORD_CAP]
    sustainability = (len(sus_strong) * SCORING["sustainability_strong"]
                      + len(sus_weak) * SCORING["sustainability_weak"])
    digital = (len(dig_strong) * SCORING["digital_strong"]
               + len(dig_weak) * SCORING["digital_weak"])
    front_evidence = sus_strong + sus_weak + dig_strong + dig_weak

    is_frontrunner = (sustainability >= FRONTRUNNER_AT or digital >= FRONTRUNNER_AT
                      or (sustainability >= FRONTRUNNER_BOTH_AT
                          and digital >= FRONTRUNNER_BOTH_AT))
    # Nothing to go on: no employee count, and no website the scraper could
    # read. These companies are called small and medium enterprises, which is
    # what the overwhelming majority of the register is; `classified_from`
    # records that the label rests on no evidence of its own.
    no_evidence = employees is None and languages == 0 and pd.isna(row["keywords"])

    # Size alone counts from MULTINATIONAL_EMPLOYEES up - calling a company
    # that size a small or medium enterprise would be plainly wrong. Below it,
    # scale must come with some sign of working across borders, or every large
    # domestic manufacturer would be called a multinational.
    is_multinational = (employees is not None and employees >= MULTINATIONAL_EMPLOYEES) or (
        multinational >= MULTINATIONAL_AT and cross_border >= 1)

    if is_frontrunner:
        label = "Frontrunner"
    elif is_multinational:
        label = "Multinational"
    else:
        label = "Small-Medium Enterprise"

    return {
        "company_class": label,
        "classified_from": "no evidence" if no_evidence else "signals",
        "multinational_score": multinational,
        "sustainability_score": sustainability,
        "digital_score": digital,
        "evidence": "; ".join(evidence + front_evidence)[:500] or None,
    }


def classify(df: pd.DataFrame) -> pd.DataFrame:
    scored = pd.DataFrame([classify_row(r) for _, r in df.iterrows()], index=df.index)
    return pd.concat([df[["id", "trade_name"]], scored], axis=1)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="write the table")
    p.add_argument("--csv", type=Path, default=None, help="also write a CSV copy")
    p.add_argument("--table", default=TARGET_TABLE)
    args = p.parse_args()

    engine = get_engine()
    df = pd.read_sql(text(QUERY), engine)
    out = classify(df)

    counts = Counter(out["company_class"])
    total = len(out)
    print(f"{total:,} active companies")
    for label, n in counts.most_common():
        print(f"   {label:24} {n:6,}  ({n / total:5.1%})")

    if args.csv:
        out.to_csv(args.csv, index=False, encoding="utf-8")
        print(f"wrote {args.csv}")
    if not args.apply:
        print("dry run: database untouched (use --apply to write)")
        return 0

    with engine.begin() as conn:
        conn.execute(text("SET SESSION sql_require_primary_key = 0"))
        out.to_sql(args.table, conn, if_exists="replace", index=False, chunksize=500,
                   dtype={"company_class": String(32)})
        conn.execute(text(f"ALTER TABLE `{args.table}` ADD PRIMARY KEY (id)"))
        conn.execute(text(f"CREATE INDEX idx_class ON `{args.table}` (company_class)"))
    print(f"wrote table `{args.table}` ({len(out):,} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
