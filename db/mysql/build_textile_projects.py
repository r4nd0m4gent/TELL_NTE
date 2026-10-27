"""
build_textile_projects.py
─────────────────────────
Pick the textile projects out of `funding_projects` (see import_funding.py)
into a table of their own, `textile_projects`, with a relevance score and the
project description as tokens. The network page shows exactly these projects:
--apply also writes their ids to site/network/data/textile_projects.json.

Relevance score
---------------
Points for textile vocabulary (the core and related terms in
site/network/data/textile_terms.json, after its idiom is removed) in three
places:

    title      6    when it names a core term: the project's own one-line
                    statement, decisive on its own; else 1 for a related term
    keywords   0-7  CORDIS only: its `topics` are the keywords the project gave
                    itself (call codes such as HORIZON-CL6-2021-... are skipped).
                    6 x the share of keywords that name a core term, full points
                    from a third up, so 'textiles' as 1 of 15 keywords (an
                    application among many) counts for little; +0.5 per
                    related term, at most 1. Erasmus+ topics are programme
                    categories ('Creativity, arts and culture') and NWO has
                    none, so for those the title and summary decide.
    summary    0-12 2 per mention of a core term, at most 10, and 0.5 per
                    related one, at most 2. Mentions are scaled to a 150-word
                    summary (x0.75 to x2): two in a short NWO summary weigh
                    like four in a full CORDIS one. NWO's summary is the
                    English one where it has one (as funding_projects keeps it).

A project is a textile project at SCORE_THRESHOLD (5.5) or more, with at least
one core term somewhere: a related term alone (fibre, fabric) never suffices.

Tokens
------
The summary tokenised as new_tell_scraper does for website keywords
(site_enrichment.extract_keywords): lower-cased and accent-folded, words of
3-30 letters, without its Dutch + English stopwords, in text order, joined by
spaces. That code is imported from the new_tell_scraper checkout next to this
repository (or --scraper-dir).

Columns
-------
    source, project_id   primary key, as in funding_projects
    programme, title, year
    relevance_score      score_title + score_keywords + score_summary
    textile_keywords     the terms found in title, keywords or summary
    tokens, token_count  the tokenised summary

Usage
-----
    python db/mysql/build_textile_projects.py           # dry run: counts, nothing written
    python db/mysql/build_textile_projects.py --apply   # (re)create textile_projects + the page's list
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from sqlalchemy import text

from import_funding import DATA_DIR, get_engine, textile_terms

TABLE = "textile_projects"
SOURCE_TABLE = "funding_projects"
PAGE_LIST = DATA_DIR / "textile_projects.json"
SCRAPER_DIR = Path(__file__).resolve().parents[3] / "new_tell_scraper"

SCORE_THRESHOLD = 5.5
SUMMARY_WORDS = 150                                   # summaries are scaled to this length
CALL_CODE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")  # HORIZON-CL6-2021-CIRCBIO-01-04, NMP-22-2015

CREATE_SQL = f"""
CREATE TABLE {TABLE} (
    source              VARCHAR(16)   NOT NULL,
    project_id          VARCHAR(64)   NOT NULL,
    programme           VARCHAR(32)   NOT NULL,
    title               VARCHAR(512)  NOT NULL,
    year                SMALLINT,
    relevance_score     DECIMAL(4,1)  NOT NULL,
    score_title         DECIMAL(3,1)  NOT NULL,
    score_keywords      DECIMAL(3,1)  NOT NULL,
    score_summary       DECIMAL(3,1)  NOT NULL,
    textile_keywords    VARCHAR(512)  NOT NULL,
    tokens              MEDIUMTEXT    NOT NULL,
    token_count         SMALLINT      NOT NULL,
    PRIMARY KEY (source, project_id),
    KEY idx_score (relevance_score)
) DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci
"""

COLUMNS = re.findall(r"^\s{4}([a-z_]+)\s", CREATE_SQL, flags=re.M)   # not PRIMARY/KEY


def load_tokenizer(scraper_dir: Path):
    """The scraper's keyword tokenisation, as a function text -> [tokens]."""
    sys.path.insert(0, str(scraper_dir))
    try:
        import site_enrichment as se
    except ImportError as exc:
        raise SystemExit(f"new_tell_scraper not found in {scraper_dir} ({exc}); pass --scraper-dir")

    def tokenize(value: str | None) -> list[str]:
        # Same steps as site_enrichment.extract_keywords, keeping every token
        return [tok for segment in se._SEGMENT_RE.split(se.fold(value or ""))
                for tok in se._TOKEN_RE.findall(segment) if se._keyword_ok(tok)]
    return tokenize


def author_keywords(row: dict) -> list[str]:
    """CORDIS topics: the project's own keywords, without call codes."""
    if row["source"] != "cordis":
        return []
    return [k for k in (k.strip() for k in re.split(r"[,;|]", row["topics"] or ""))
            if k and not CALL_CODE.match(k)]


def relevance(row: dict, terms: dict) -> dict:
    """The score's three parts and the textile terms found (see the module docstring)."""
    core, related = terms["core"], terms["related"]
    clean = lambda s: terms["idiom"].sub(" ", s or "")                     # noqa: E731
    found = lambda pats, s: [k for k, rx in pats.items() if rx.search(s)]  # noqa: E731
    count = lambda pats, s: sum(len(rx.findall(s)) for rx in pats.values())  # noqa: E731
    title, summary = clean(row["title"]), clean(row["summary"])
    keywords = [clean(k) for k in author_keywords(row)]

    title_core = found(core, title)
    score_title = 6.0 if title_core else min(1.0, 1.0 * len(found(related, title)))

    score_keywords, keywords_core = 0.0, 0
    if keywords:
        keywords_core = sum(bool(found(core, k)) for k in keywords)
        keywords_related = {t for k in keywords for t in found(related, k)}
        score_keywords = (6 * min(1.0, 3 * keywords_core / len(keywords))
                          + min(1.0, 0.5 * len(keywords_related)))

    scale = max(0.75, min(2.0, SUMMARY_WORDS / max(len(summary.split()), 1)))
    summary_core = count(core, summary)
    score_summary = min(10.0, 2 * summary_core * scale) + min(2.0, 0.5 * count(related, summary))

    parts = (round(score_title, 1), round(score_keywords, 1), round(score_summary, 1))
    texts = (title, summary, *keywords)
    return {
        "score_title": parts[0], "score_keywords": parts[1], "score_summary": parts[2],
        "relevance_score": round(sum(parts), 1),
        "has_core": bool(title_core or keywords_core or summary_core),
        "textile_keywords": ", ".join(k for k, rx in {**core, **related}.items()
                                      if any(rx.search(t) for t in texts)),
    }


def build_rows(engine, tokenize) -> tuple[list[dict], list[dict]]:
    """(textile projects, all projects with their score)."""
    terms = textile_terms()
    with engine.connect() as conn:
        source = [dict(r._mapping) for r in conn.execute(text(
            f"SELECT source, project_id, programme, title, year, summary, topics FROM {SOURCE_TABLE}"))]
    scored = [{**r, **relevance(r, terms)} for r in source]
    rows = []
    for r in scored:
        if r["relevance_score"] < SCORE_THRESHOLD or not r["has_core"]:
            continue
        tokens = tokenize(r["summary"])
        rows.append({**{c: r[c] for c in COLUMNS if c in r},
                     "tokens": " ".join(tokens), "token_count": len(tokens)})
    return rows, scored


def summary(rows: list[dict], scored: list[dict]) -> None:
    total = Counter(r["programme"] for r in scored)
    kept = Counter(r["programme"] for r in rows)
    print(f"{len(rows)} of {len(scored)} projects are textile projects (score >= {SCORE_THRESHOLD})")
    for prog in sorted(total):
        print(f"  {prog:15} {kept[prog]:4} of {total[prog]:5}")
    near = sorted((r for r in scored if r["has_core"]
                   and abs(r["relevance_score"] - SCORE_THRESHOLD) <= 1),
                  key=lambda r: -r["relevance_score"])
    print(f"\nwithin 1 point of the threshold ({len(near)}):")
    for r in near:
        mark = "+" if r["relevance_score"] >= SCORE_THRESHOLD else "-"
        print(f"  {mark} {r['relevance_score']:4} = {r['score_title']} + {r['score_keywords']}"
              f" + {r['score_summary']}  {r['source']:7} {r['title'][:70]}")


def write(engine, rows: list[dict]) -> None:
    insert = text(f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) "
                  f"VALUES ({', '.join(':' + c for c in COLUMNS)})")
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))
        conn.execute(text(CREATE_SQL))
        conn.execute(insert, rows)
        n = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE}")).scalar()
    print(f"wrote {n} rows to {TABLE}")

    PAGE_LIST.write_text(json.dumps({
        "_about": f"The projects in the {TABLE} table, with their relevance score: the network page "
                  "shows only these. Written by db/mysql/build_textile_projects.py --apply.",
        "generated": date.today().isoformat(),
        "threshold": SCORE_THRESHOLD,
        "projects": {f"{r['source']}:{r['project_id']}": float(r["relevance_score"])
                     for r in sorted(rows, key=lambda r: (r["source"], r["project_id"]))},
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} project ids to {PAGE_LIST.relative_to(DATA_DIR.parents[2])}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true",
                   help=f"replace the {TABLE} table and {PAGE_LIST.name} (default: dry run)")
    p.add_argument("--scraper-dir", type=Path, default=SCRAPER_DIR,
                   help=f"new_tell_scraper checkout (default: {SCRAPER_DIR})")
    args = p.parse_args()

    engine = get_engine()
    rows, scored = build_rows(engine, load_tokenizer(args.scraper_dir))
    summary(rows, scored)
    if args.apply:
        write(engine, rows)
    else:
        print(f"\ndry run: nothing written. Rerun with --apply to replace {TABLE}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
