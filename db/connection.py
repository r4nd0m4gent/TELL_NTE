"""
db/connection.py
────────────────
Shared database helpers for all TELL apps.

Set DATABASE_URL in the environment (or systemd service):
    mysql+pymysql://tell:password@localhost/tell
"""
import os
import json
from urllib.parse import urlparse

import pymysql
import pandas as pd
from sqlalchemy import create_engine, text

_DSN = os.environ.get("DATABASE_URL", "mysql+pymysql://tell:tell@localhost/tell")
# SQLAlchemy engine for pd.read_sql (pandas 3.x requires this)
_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(_DSN)
    return _engine


def _conn_params() -> dict:
    """Parse _DSN into keyword arguments for pymysql.connect()."""
    url = urlparse(_DSN)
    return {
        "host":     url.hostname or "localhost",
        "port":     url.port or 3306,
        "user":     url.username,
        "password": url.password or "",
        "database": url.path.lstrip("/"),
        "charset":  "utf8mb4",
        "autocommit": False,
    }


def get_conn():
    """Return a new PyMySQL connection."""
    return pymysql.connect(**_conn_params())


# ---------------------------------------------------------------------------
# Dashboard data loader
# ---------------------------------------------------------------------------

def load_companies() -> pd.DataFrame:
    """
    Query v_companies and return a DataFrame with column names that match
    what textile_companies_NL.py expects (same as the old Excel layout).
    """
    sql = "SELECT * FROM v_companies ORDER BY trade_name"
    with get_engine().connect() as conn:
        df = pd.read_sql(text(sql), conn)

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
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT trade_name FROM organizations ORDER BY trade_name")
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{"label": r[0], "value": r[0]} for r in rows]


# ---------------------------------------------------------------------------
# Contribution form writers
# ---------------------------------------------------------------------------

def save_contribution(type_: str, payload: dict, organization_id: int | None = None):
    """Insert a contribution record for human review."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO contributions (type, organization_id, payload)
                VALUES (%s, %s, %s)
                """,
                (type_, organization_id, json.dumps(payload)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
    conn = get_conn()
    try:
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
                    JOIN organizations o ON k.organization_id = o.organization_id
                    SET k.nlp_analyses = JSON_MERGE_PATCH(k.nlp_analyses, %s)
                    WHERE o.trade_name = %s
                    """,
                    (entry, row["trade_name"]),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
