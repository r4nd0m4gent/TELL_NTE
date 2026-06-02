#!/usr/bin/env python3
"""
db/migrate_excel_to_pg.py
──────────────────────────
One-time migration: reads the KvK Excel file and seeds the PostgreSQL
database (geographies, organizations, keywords tables).

Usage (after creating the DB and running schema.sql):
    export DATABASE_URL=postgresql://tell:password@localhost/tell
    python db/migrate_excel_to_pg.py [path/to/KvK_textile.xlsx]

The script is idempotent — run it again after updating the Excel and it
will INSERT new rows; existing postcodes / companies are skipped.
"""

import os
import sys
import warnings

import pandas as pd
import pgeocode
import psycopg2
import psycopg2.extras

warnings.filterwarnings("ignore", category=DeprecationWarning)

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_EXCEL = os.environ.get(
    "TEXTILE_DATA_PATH",
    r"C:\Users\fsollit\Desktop\Data\Supply chain\Modint KvK\KvK textile.xlsx",
)
DSN = os.environ.get("DATABASE_URL", "postgresql://tell:tell@localhost/tell")

# ── Load & clean Excel ────────────────────────────────────────────────────────

def load_excel(path: str) -> pd.DataFrame:
    print(f"Reading Excel: {path}")
    df = pd.read_excel(path, sheet_name=0)
    df = df.rename(columns={
        "visiting address_city":     "city",
        "new main":                  "derived_category",
        "tag_category":              "tag_category",
        "number_employees":          "number_employees",
        "visiting address_postcode": "raw_postcode",
    })
    df = df.dropna(subset=["city", "region"])
    df["tag_category"]    = df.get("tag_category",    pd.Series()).fillna("")
    df["tags"]            = df.get("tags",            pd.Series()).fillna("")
    df["number_employees"]= pd.to_numeric(df.get("number_employees", pd.Series()),
                                          errors="coerce").fillna(0).astype(int)
    df["pc4"] = df["raw_postcode"].astype(str).str.extract(r"^(\d{4})")[0]
    return df

# ── Geocode unique PC4 ─────────────────────────────────────────────────────────

def geocode_postcodes(df: pd.DataFrame) -> pd.DataFrame:
    nomi   = pgeocode.Nominatim("nl")
    unique = df["pc4"].dropna().unique().tolist()
    print(f"Geocoding {len(unique)} unique postcodes…")
    geo = nomi.query_postal_code(unique)[
        ["postal_code", "place_name", "state_name", "county_name", "latitude", "longitude"]
    ]
    geo["postal_code"] = geo["postal_code"].astype(str).str.zfill(4)
    return geo

# ── DB helpers ────────────────────────────────────────────────────────────────

def upsert_geographies(cur, geo: pd.DataFrame, df: pd.DataFrame):
    """
    Insert a row per unique PC4.  The city / region come from the Excel because
    pgeocode place_name can differ from KvK registered city.
    """
    pc4_to_city   = df.dropna(subset=["pc4"]).groupby("pc4")["city"].first()
    pc4_to_region = df.dropna(subset=["pc4"]).groupby("pc4")["region"].first()

    inserted = 0
    for _, row in geo.iterrows():
        pc4 = str(row["postal_code"]).zfill(4)
        if len(pc4) != 4 or not pc4.isdigit():
            continue
        cur.execute(
            """
            INSERT INTO geographies (postcode, region, province, city, latitude, longitude)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (postcode) DO NOTHING
            """,
            (
                pc4,
                pc4_to_region.get(pc4, row.get("state_name")),
                row.get("county_name"),
                pc4_to_city.get(pc4, row.get("place_name")),
                None if pd.isna(row["latitude"])  else float(row["latitude"]),
                None if pd.isna(row["longitude"]) else float(row["longitude"]),
            ),
        )
        if cur.rowcount:
            inserted += 1
    print(f"  geographies: {inserted} new rows")

def insert_organizations_and_keywords(cur, df: pd.DataFrame):
    added = 0
    skipped = 0
    for _, row in df.iterrows():
        trade_name = str(row.get("trade name", "") or "").strip()
        if not trade_name:
            continue

        # Skip if already exists (makes script re-runnable)
        cur.execute(
            "SELECT organization_id FROM organizations WHERE trade_name = %s",
            (trade_name,),
        )
        existing = cur.fetchone()
        if existing:
            skipped += 1
            continue

        pc4 = str(row.get("pc4", "")) if pd.notna(row.get("pc4")) else None
        if pc4 and (len(pc4) != 4 or not pc4.isdigit()):
            pc4 = None

        cur.execute(
            """
            INSERT INTO organizations
                (trade_name, legal_name, website, postcode, status, number_employees)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING organization_id
            """,
            (
                trade_name,
                str(row.get("legal name", "") or "").strip() or None,
                str(row.get("website", "") or "").strip() or None,
                pc4,
                str(row.get("status", "") or "").strip() or None,
                int(row.get("number_employees", 0) or 0),
            ),
        )
        org_id = cur.fetchone()[0]

        # Seed JSONB with any existing Predicted_Category / Predicted_Tier from Excel
        nlp: dict = {}
        pred_cat  = str(row.get("Predicted_Category", "") or "").strip()
        pred_tier = str(row.get("Predicted_Tier",     "") or "").strip()
        if pred_cat:
            nlp["predicted_category_v1"] = {"value": pred_cat, "source": "excel_import"}
        if pred_tier:
            nlp["predicted_tier_v1"] = {"value": pred_tier, "source": "excel_import"}

        cur.execute(
            """
            INSERT INTO keywords
                (organization_id, main_activity, tag_category, tags, nlp_analyses)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                org_id,
                str(row.get("derived_category", "") or "").strip() or None,
                str(row.get("tag_category", "") or "").strip() or None,
                str(row.get("tags", "") or "").strip() or None,
                psycopg2.extras.Json(nlp),
            ),
        )
        added += 1

    print(f"  organizations+keywords: {added} new, {skipped} skipped")

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    excel_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EXCEL
    df  = load_excel(excel_path)
    geo = geocode_postcodes(df)

    print(f"Connecting: {DSN.split('@')[-1]}")
    conn = psycopg2.connect(DSN)
    try:
        with conn.cursor() as cur:
            upsert_geographies(cur, geo, df)
            insert_organizations_and_keywords(cur, df)
        conn.commit()
        print("Migration complete.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
