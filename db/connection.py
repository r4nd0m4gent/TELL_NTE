"""
db/connection.py
────────────────
Shared database helpers for all TELL apps.

Set DATABASE_URL in the environment (or systemd service):
    postgresql://tell:password@localhost/tell
"""
import os
import json
import psycopg2
import psycopg2.extras
import pandas as pd

_DSN = os.environ.get("DATABASE_URL", "postgresql://tell:tell@localhost/tell")


def get_conn():
    """Return a new psycopg2 connection."""
    return psycopg2.connect(_DSN)


# ---------------------------------------------------------------------------
# Dashboard data loader
# ---------------------------------------------------------------------------

def load_companies() -> pd.DataFrame:
    """
    Query v_companies and return a DataFrame with column names that match
    what textile_companies_NL.py expects (same as the old Excel layout).
    """
    sql = "SELECT * FROM v_companies ORDER BY trade_name"
    with get_conn() as conn:
        df = pd.read_sql(sql, conn)

    # Rename DB columns → legacy names used throughout the dashboard
    df = df.rename(columns={
        "trade_name":       "trade name",
        "number_employees": "value",
    })
    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0)
    df["Predicted_Category"] = df["Predicted_Category"].fillna("Unknown")
    df["Predicted_Tier"]     = df["Predicted_Tier"].fillna("Unknown")
    df["tags"]               = df["tags"].fillna("")
    return df


def get_company_names() -> list[dict]:
    """Return sorted list of {label, value} dicts for the company dropdown."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT trade_name FROM organizations ORDER BY trade_name")
            rows = cur.fetchall()
    return [{"label": r[0], "value": r[0]} for r in rows]


# ---------------------------------------------------------------------------
# Contribution form writers
# ---------------------------------------------------------------------------

def save_contribution(type_: str, payload: dict, organization_id: int | None = None):
    """Insert a contribution record for human review."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO contributions (type, organization_id, payload)
                VALUES (%s, %s, %s)
                """,
                (type_, organization_id, json.dumps(payload)),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# NLP result writer (used by analysis scripts)
# ---------------------------------------------------------------------------

def save_nlp_results(results: list[dict], analysis_name: str, run_date: str):
    """
    Merge NLP results into keywords.nlp_analyses for multiple organisations.

    Parameters
    ----------
    results : list of {"trade_name": str, "value": str, "confidence": float}
    analysis_name : key added to the JSONB, e.g. "predicted_category_v2"
    run_date : ISO date string, e.g. "2026-06-02"
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            for row in results:
                entry = json.dumps({
                    analysis_name: {
                        "value":      row["value"],
                        "confidence": row.get("confidence"),
                        "run_date":   run_date,
                    }
                })
                cur.execute(
                    """
                    UPDATE keywords k
                    SET nlp_analyses = nlp_analyses || %s::jsonb
                    FROM organizations o
                    WHERE k.organization_id = o.organization_id
                      AND o.trade_name = %s
                    """,
                    (entry, row["trade_name"]),
                )
        conn.commit()
