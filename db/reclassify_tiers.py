"""
reclassify_tiers.py
───────────────────
Give a supply-chain tier to the companies the original classification left
without one: tier 'No match', tier 'Other', or no tier at all.

Those labels described the old classifier's failure, not the companies. The 46
'No match' ones each carried a single tag ('textile', 'jacket'), too little to
place them in the chain, though their registered activity says plainly what
they do. The ones with no tier are the organizations appended after the KvK
import - associations, museums, festivals - which never had the fields a tier
was derived from.

How a tier is decided
---------------------
1. Registered activity, the strongest signal. Rather than inventing a mapping
   from activity to tier, the majority tier of the companies that *were*
   classified under the same activity is used, so the result agrees with the
   existing classification instead of competing with it (97% of 'Manufacture
   of other outerwear' is Retail & Brand - these are fashion brands, not mills).
2. Otherwise the company's name and scraped keywords: an industry association,
   museum, school or consultancy supports the sector without sitting in the
   chain and becomes 'No tier'; a sorting centre, repair shop, webshop or
   weaving mill gets the matching tier.
3. Anything still undecided stays 'No match'. The dashboard hides those rather
   than showing a bucket that means nothing.

'Other' is renamed to 'No tier' for every company that keeps it.

Output
------
The decided tier is written into `tags.tier` itself, so the dashboard and every
other reader need no extra table. Three columns are added beside it:

    tier_original    the label this company had before, kept so nothing is
                     lost and so a re-run can find the same companies again
    tier_decided_by  activity / keywords / supporting / checked by hand
    tier_evidence    what the decision rests on

Re-running is safe and repeatable: a company that was decided here is
recognised by its `tier_original`, and the activity majorities are always
computed from the original labels, so repeated runs cannot drift.

Usage
-----
    python db/reclassify_tiers.py            # show what would change
    python db/reclassify_tiers.py --apply    # write it into `tags`
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ENV_PATH = Path(__file__).resolve().parent / "mysql" / ".env"
OLD_TABLE = "company_tier"     # earlier home of this result; dropped on --apply
NO_TIER = "No tier"            # replaces the old 'Other'
UNDECIDED = "No match"         # hidden by the dashboard

# Tiers the original classification uses, kept as they are.
MIN_EXAMPLES = 3               # classified companies needed to trust an activity
MIN_SHARE = 0.6                # and how dominant its majority tier must be

# The company is not in the chain: it supports the sector around it.
SUPPORTING = {
    "association", "associations", "vereniging", "stichting", "foundation",
    "federation", "museum", "museums", "festival", "biennale", "exhibition",
    "education", "school", "academy", "college", "university", "students",
    "teachers", "training", "courses", "research", "knowledge", "science",
    "platform", "network", "cluster", "programme", "program", "manifesto",
    "consultancy", "consultant", "consulting", "advice", "advisory", "agency",
    "magazine", "journalism", "members", "membership", "lobby",
    "government", "province", "municipality", "subsidy", "funding", "incubator",
}

# A handful the rules read wrongly, checked by hand against their websites.
# Keyed on the trade name; the reason is recorded as the evidence.
OVERRIDES = {
    "Alexandra Barker": ("Retail & Brand", "hemp fashion label with its own shop"),
    "Dutch Yarnery": ("Retail & Brand", "shop selling yarn, not a spinner"),
    "Schijvens Corporate Fashion": ("Textile producer", "manufactures corporate clothing"),
    "bAwear": (NO_TIER, "impact assessment consultancy"),
    "Dutch Circular Textile Valley (DCTV)": (NO_TIER, "regional cluster organization"),
    "New Industrial Order": ("Retail & Brand", "design brand; 'manifesto' on its site"
                             " read as a supporting organization"),
    "Erdotex B.V.": ("Collection & Sorting", "collects and sorts textiles; registered"
                     " as wholesale, whose majority tier is Wholesale"),
}
# Keyword rules for the rest, in the order they are tried.
KEYWORD_TIERS = [
    ("Collection & Sorting", {"sorting", "sorteer", "sorteercentrum", "collecting",
                              "collection centre", "textile collection", "inzameling"}),
    ("Recycling", {"recycling", "recycler", "fibre recycling", "shredding"}),
    ("Repair & Re-manufacturing", {"repair", "repairs", "repairing", "remanufacturing",
                                   "re-manufacturing", "upcycling", "disassemble"}),
    ("Textile producer", {"manufacture", "manufacturing", "produce", "production",
                          "atelier", "weaving", "weaving mill", "knitting", "spinning",
                          "dyeing", "dyes", "finishing", "yarn", "yarns", "fabric",
                          "fabrics", "sailmaking", "embroidery", "printing"}),
    ("Wholesale", {"wholesale", "wholesaler", "distribution", "distributor",
                   "import", "importer", "export", "agent", "agents"}),
    ("Retail & Brand", {"shop", "webshop", "store", "stores", "retail", "brand",
                        "brands", "collection", "fashion", "clothing"}),
]


def get_engine():
    load_dotenv(ENV_PATH)
    return create_engine(
        f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST')}:25060/{os.getenv('DB_NAME')}",
        connect_args={"ssl": {"ca": os.getenv("DB_CA_CERT")}},
    )


def ensure_columns(engine) -> None:
    """Add the three columns beside `tags.tier`, once."""
    with engine.begin() as conn:
        for column, kind in (("tier_original", "VARCHAR(32)"),
                             ("tier_decided_by", "VARCHAR(20)"),
                             ("tier_evidence", "VARCHAR(255)")):
            try:
                conn.execute(text(f"ALTER TABLE tags ADD COLUMN {column} {kind} NULL"))
            except Exception:
                pass          # already there


NO_ORIGINAL = "(none)"     # stood in a company that had no tier at all


def has_history(engine) -> bool:
    """Whether `tags` already carries the columns this script adds."""
    with engine.connect() as conn:
        return bool(conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE()"
            " AND table_name = 'tags' AND column_name = 'tier_decided_by'")).scalar())


def activity_tiers(engine) -> dict:
    """{registered activity: (tier, share, examples)} from companies already placed.

    Judged on the labels as they were before this script ran, so running it
    again cannot feed its own verdicts back into the majorities.
    """
    # Companies this script decided are left out, so its verdicts never feed
    # back into the majorities that produced them.
    undecided = "AND t.tier_decided_by IS NULL" if has_history(engine) else ""
    df = pd.read_sql(text(
        "SELECT o.main_activity, t.tier"
        "  FROM organizations o JOIN tags t ON t.id = o.id"
        " WHERE o.main_activity IS NOT NULL AND t.tier IS NOT NULL"
        f"   AND t.tier NOT IN ('No match', 'Other', '') {undecided}"), engine)
    out = {}
    for activity, group in df.groupby("main_activity")["tier"]:
        counts = Counter(group)
        tier, n = counts.most_common(1)[0]
        total = sum(counts.values())
        if total >= MIN_EXAMPLES and n / total >= MIN_SHARE:
            out[activity] = (tier, n / total, total)
    return out


def terms_of(row) -> set:
    """Everything known about a company as lower-case words and phrases."""
    text_bits = " ".join(str(row[f] or "") for f in ("trade_name", "tags", "scraped"))
    words = {w.strip(" ,.-()") for w in text_bits.lower().split()}
    phrases = {t.strip().lower() for t in str(row["scraped"] or "").split(",") if t.strip()}
    return words | phrases


def decide(row, by_activity: dict) -> tuple:
    """(tier, how it was decided, the evidence) for one company."""
    override = OVERRIDES.get(str(row["trade_name"]).strip())
    if override:
        return override[0], "checked by hand", override[1]

    known = by_activity.get(row["main_activity"])
    if known:
        tier, share, examples = known
        return tier, "activity", f"{row['main_activity']} ({share:.0%} of {examples} classified)"

    terms = terms_of(row)
    supporting = sorted(terms & SUPPORTING)
    if supporting:
        return NO_TIER, "supporting", ", ".join(supporting[:4])
    for tier, keywords in KEYWORD_TIERS:
        hits = sorted(terms & keywords)
        if hits:
            return tier, "keywords", ", ".join(hits[:4])
    return UNDECIDED, "none", None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="write the table")
    p.add_argument("--csv", type=Path, default=None)
    args = p.parse_args()

    engine = get_engine()
    if args.apply:
        ensure_columns(engine)
    by_activity = activity_tiers(engine)
    print(f"activities with a clear majority tier: {len(by_activity):,}")

    # Companies without a usable tier, plus the ones this script decided before
    # (recognised by tier_original), so corrections can be re-applied.
    # `tier_decided_by` marks every company this script has decided before -
    # including those that had no tier at all, which a NULL `tier_original`
    # would hide, losing their hand-checked corrections on the next run.
    decided_before = "t.tier_decided_by IS NOT NULL OR" if has_history(engine) else ""
    df = pd.read_sql(text(
        "SELECT o.id, o.trade_name, o.main_activity,"
        "       COALESCE(t.tier_original, t.tier) AS old_tier, t.tags,"
        "       ts.tags_new AS scraped"
        "  FROM organizations o JOIN tags t ON t.id = o.id"
        "  LEFT JOIN tags_scraped ts ON ts.id = o.id"
        f" WHERE {decided_before} t.tier IS NULL OR t.tier IN ('No match', 'Other', '')"), engine)
    print(f"companies without a usable tier: {len(df):,}")

    decided = [decide(row, by_activity) for _, row in df.iterrows()]
    out = pd.DataFrame({
        "id": df["id"],
        "trade_name": df["trade_name"],
        "old_tier": df["old_tier"].fillna("(none)"),
        "tier": [d[0] for d in decided],
        "decided_by": [d[1] for d in decided],
        "evidence": [d[2] for d in decided],
    })

    print("\nnew tiers:")
    for tier, n in Counter(out["tier"]).most_common():
        print(f"   {tier:28} {n:4}")
    print("\ndecided by:", dict(Counter(out["decided_by"])))

    if args.csv:
        out.to_csv(args.csv, index=False, encoding="utf-8")
        print(f"wrote {args.csv}")
    if not args.apply:
        print("dry run: database untouched (use --apply to write)")
        return 0

    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE tags SET tier_original = COALESCE(tier_original, NULLIF(tier, ''), :none),"
            "                tier = :tier, tier_decided_by = :by, tier_evidence = :why"
            " WHERE id = :id"),
            [{"id": int(r.id), "tier": r.tier, "by": r.decided_by, "why": r.evidence,
              "none": NO_ORIGINAL} for r in out.itertuples()])
        # Its contents now live in `tags`; the separate table would only drift.
        conn.execute(text(f"DROP TABLE IF EXISTS `{OLD_TABLE}`"))
    print(f"wrote {len(out):,} tiers into `tags`; previous labels kept in "
          f"tags.tier_original; dropped `{OLD_TABLE}`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
