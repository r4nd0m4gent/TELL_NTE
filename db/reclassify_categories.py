"""
reclassify_categories.py
────────────────────────
Two changes to the product category:

1. A new category, 'Technical & industrial', taken out of 'Other'. That bucket
   holds 2,493 companies, among them the nonwoven, rope, geotextile and
   composite makers - an industry quite unlike the clothing and home textiles
   the other categories describe.

2. The companies the original classification left as 'No match' or blank are
   placed where their registered activity says they belong. Whatever still
   cannot be placed keeps 'No match', and the dashboard hides it rather than
   showing a category that means "we could not tell".

How a category is decided
-------------------------
    technical      the registered activity is technical or industrial textiles
                   by definition (nonwovens, cord/twine/rope/nets, technical
                   and industrial textiles), or the company's keywords carry at
                   least two technical signals ('geotextile', 'filtration',
                   'composites', 'acoustic', 'tarpaulin'). One signal is not
                   enough: 'safety' alone appears on plenty of clothing sites
    activity       otherwise, for a company without a usable category, the
                   majority category of the companies classified under the same
                   registered activity - following the existing classification
                   rather than competing with it ('Wholesale of outerwear' is
                   Clothing for 99% of them)
    none           anything left kept as 'No match', and hidden by the dashboard

Only 'Other' and the unplaced companies are touched: a workwear brand that
mentions 'protective' stays in Clothing, where it belongs.

Output
------
Written into `tags.category`, with the previous label kept in
`tags.category_original` beside `category_decided_by` and `category_evidence`.
Re-running is repeatable: companies decided here are recognised by
`category_decided_by`, and the majorities always come from the original labels.

Usage
-----
    python db/reclassify_categories.py            # show what would change
    python db/reclassify_categories.py --apply    # write it into `tags`
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
TECHNICAL = "Technical & industrial"
GENERAL = "General textile"
NO_PRODUCT = "No product"       # associations, museums, schools: they sell none
UNDECIDED = "No match"          # hidden by the dashboard
NO_ORIGINAL = "(none)"          # stood in where a company had no category

MIN_EXAMPLES = 3                # classified companies needed to trust an activity
MIN_SHARE = 0.6                 # and how dominant its majority category must be
MIN_TECH_SIGNALS = 2            # keyword hits needed when the activity is generic

# Activities that are technical or industrial textiles by definition.
TECH_ACTIVITIES = {
    "Manufacture of technical and industrial textiles",
    "Manufacture of nonwovens and articles thereof (not clothing)",
    "Manufacture of cord, twine, rope and nets",
}
# Making or treating textile without the activity naming a product. These are
# what 'Other' was almost entirely made of: real textile companies whose
# registration says what they do, not what they make.
GENERAL_ACTIVITIES = {
    "Manufacture of made-up textile articles (other than clothing)",
    "Textile finishing",
    "Manufacture of other textile products n.e.c.",
    "Processing and spinning of textile fibers",
    "Weaving textiles",
    "Manufacture of knitted and crocheted fabrics",
    "Wholesale of textile goods in general range",
    "Wholesale of clothing fabrics and haberdashery",
}
# Products and processes that mark technical or industrial use.
TECH_TERMS = {
    "technical", "industrial", "technical textiles", "industrial textiles",
    "nonwoven", "nonwovens", "geotextile", "geotextiles", "filtration", "filter",
    "composite", "composites", "rope", "ropes", "cord", "twine", "net", "nets",
    "webbing", "belt", "belts", "tarpaulin", "tarpaulins", "awning", "awnings",
    "sail", "sails", "sailmaking", "tent", "tents", "canvas", "insulation",
    "acoustic", "acoustics", "automotive", "medical textiles", "protective",
    "protection", "safety", "reinforcement", "coating", "coated", "laminate",
    "laminated", "conveyor", "hose", "packaging", "agrotextile", "fire retardant",
}


def get_engine():
    load_dotenv(ENV_PATH)
    return create_engine(
        f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST')}:25060/{os.getenv('DB_NAME')}",
        connect_args={"ssl": {"ca": os.getenv("DB_CA_CERT")}},
    )


def has_history(engine) -> bool:
    """Whether `tags` already carries the columns this script adds."""
    with engine.connect() as conn:
        return bool(conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE()"
            " AND table_name = 'tags' AND column_name = 'category_decided_by'")).scalar())


def ensure_columns(engine) -> None:
    with engine.begin() as conn:
        for column, kind in (("category_original", "VARCHAR(32)"),
                             ("category_decided_by", "VARCHAR(20)"),
                             ("category_evidence", "VARCHAR(255)")):
            try:
                conn.execute(text(f"ALTER TABLE tags ADD COLUMN {column} {kind} NULL"))
            except Exception:
                pass          # already there


def activity_categories(engine) -> dict:
    """{registered activity: (category, share, examples)} from the original labels."""
    undecided = "AND t.category_decided_by IS NULL" if has_history(engine) else ""
    df = pd.read_sql(text(
        "SELECT o.main_activity, t.category FROM organizations o JOIN tags t ON t.id = o.id"
        " WHERE o.main_activity IS NOT NULL AND t.category IS NOT NULL"
        f"   AND t.category NOT IN ('No match', '') {undecided}"), engine)
    out = {}
    for activity, group in df.groupby("main_activity")["category"]:
        counts = Counter(group)
        category, n = counts.most_common(1)[0]
        total = sum(counts.values())
        if total >= MIN_EXAMPLES and n / total >= MIN_SHARE:
            out[activity] = (category, n / total, total)
    return out


def technical_signals(terms_value) -> list:
    terms = {t.strip().lower() for t in str(terms_value or "").split(",") if t.strip()}
    words = {w for t in terms for w in t.split()}
    return sorted((terms | words) & TECH_TERMS)


def decide(row, by_activity: dict) -> tuple:
    """(category, how it was decided, the evidence) for one company."""
    # pandas reads a missing activity as NaN, which is truthy.
    activity = None if pd.isna(row["main_activity"]) else row["main_activity"]
    if activity in TECH_ACTIVITIES:
        return TECHNICAL, "activity", activity
    signals = technical_signals(row["terms"])
    if len(signals) >= MIN_TECH_SIGNALS:
        return TECHNICAL, "keywords", ", ".join(signals[:4])

    if activity in GENERAL_ACTIVITIES:
        return GENERAL, "activity", activity

    # Only companies without a usable category are placed by their activity;
    # the rest keep what they have.
    if row["old_category"] in (UNDECIDED, NO_ORIGINAL):
        known = by_activity.get(activity)
        if known:
            category, share, examples = known
            return category, "activity", f"{activity} ({share:.0%} of {examples} classified)"
        if not activity:
            # No registered activity at all: the associations, museums and
            # schools added by hand after the KvK import. They sell no product,
            # which is a fact about them rather than a failure to classify.
            return NO_PRODUCT, "no activity", "no registered activity"
        return UNDECIDED, "none", None
    return row["old_category"], "unchanged", None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="write it into `tags`")
    p.add_argument("--csv", type=Path, default=None)
    args = p.parse_args()

    engine = get_engine()
    if args.apply:
        ensure_columns(engine)
    by_activity = activity_categories(engine)
    print(f"activities with a clear majority category: {len(by_activity):,}")

    history = has_history(engine)
    decided_before = "t.category_decided_by IS NOT NULL OR" if history else ""
    # On the first run the columns do not exist yet.
    old_expr = ("COALESCE(t.category_original, t.category, :none)" if history
                else "COALESCE(t.category, :none)")
    df = pd.read_sql(text(
        "SELECT o.id, o.trade_name, o.main_activity,"
        f"       {old_expr} AS old_category,"
        "       COALESCE(ts.tags_new, t.tags) AS terms"
        "  FROM organizations o JOIN tags t ON t.id = o.id"
        "  LEFT JOIN tags_scraped ts ON ts.id = o.id"
        f" WHERE {decided_before} t.category = 'Other'"
        "    OR t.category = 'No match' OR t.category IS NULL OR t.category = ''"),
        engine, params={"none": NO_ORIGINAL})
    print(f"companies considered (Other, No match, blank): {len(df):,}")

    decided = [decide(row, by_activity) for _, row in df.iterrows()]
    out = pd.DataFrame({
        "id": df["id"],
        "trade_name": df["trade_name"],
        "old_category": df["old_category"],
        "category": [d[0] for d in decided],
        "decided_by": [d[1] for d in decided],
        "evidence": [d[2] for d in decided],
    })
    changed = out[out["category"] != out["old_category"]]

    print(f"\nchanged: {len(changed):,}")
    for (old, new), n in Counter(zip(changed["old_category"], changed["category"])).most_common():
        print(f"   {old:12} -> {new:24} {n:5}")
    still = (out["category"] == UNDECIDED).sum()
    print(f"\nstill '{UNDECIDED}' (the dashboard hides these): {still:,}")

    if args.csv:
        out.to_csv(args.csv, index=False, encoding="utf-8")
        print(f"wrote {args.csv}")
    if not args.apply:
        print("dry run: database untouched (use --apply to write)")
        return 0

    with engine.begin() as conn:
        # Companies left as they were are not marked: a mark excludes them from
        # the activity majorities, and dropping the thousands that merely pass
        # through would change the majorities a later run computes.
        conn.execute(text(
            "UPDATE tags SET category_original = COALESCE(category_original, NULLIF(category, ''), :none),"
            "                category = :category, category_decided_by = :by,"
            "                category_evidence = :why"
            " WHERE id = :id"),
            # pandas turns a missing evidence into NaN, which MySQL rejects.
            [{"id": int(r.id), "category": r.category, "by": r.decided_by,
              "why": None if pd.isna(r.evidence) else r.evidence,
              "none": NO_ORIGINAL} for r in changed.itertuples()])
    print(f"wrote {len(changed):,} categories into `tags`; previous labels kept in "
          f"tags.category_original")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
