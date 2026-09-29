"""
build_dashboard_tags.py
───────────────────────
Build the merged keyword list per organization that the TELL dashboard shows
and searches: the curated tags plus everything scraped from the website.

Input (joined on website)
-------------------------
    tags.tags                              the current, curated tag string
    scraping17092026.`English keywords`    scraped single words  } translated to English by
    scraping17092026.`English bigrams`     scraped two-word terms } new_tell_scraper/translate_keywords.py

What goes into the merged list, in this order, de-duplicated
------------------------------------------------------------
* every tag the organization already has, in its current order;
* the scraped keywords, in the order the scraper ranked them;
* the scraped bigrams, likewise.

Scraped terms (never curated tags) are dropped when fewer than --min-companies
companies use them, or when they are in STOPLIST. The default of 2 removes the
~29k terms only one company uses, nearly all brand names, surnames and street
names; --min-companies 3 is stricter but also loses real niche vocabulary
('cross stitch', 'leather garments'). The stoplist covers what frequency
cannot: cookie, hosting and navigation text that appears on hundreds of sites.

Output
------
Table `tags_scraped`: id, trade_name, website, tags_old, tags_new and the term
counts. The dashboard reads tags_new, falling back to `tags` for organizations
that were not scraped. Nothing writes to `tags`.

Usage
-----
    python db/build_dashboard_tags.py
    python db/build_dashboard_tags.py --min-companies 3
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
from sqlalchemy import String, create_engine, text

ENV_PATH = Path(__file__).resolve().parent / "mysql" / ".env"
SCRAPE_TABLE = "scraping17092026"
TARGET_TABLE = "tags_scraped"
VOCAB_TABLE = "keyword_vocabulary"
MIN_COMPANIES = 2

QUERY = f"""
    SELECT DISTINCT
        org.id,
        org.trade_name,
        org.website,
        org.main_activity,
        t.tags,
        s.`English keywords` AS english_keywords,
        s.`English bigrams`  AS english_bigrams
    FROM organizations org
    JOIN {SCRAPE_TABLE} s ON org.website = s.website
    JOIN tags t ON t.id = org.id
"""

# Scraped terms that say nothing about a company: they come from cookie
# banners, parked-domain and hosting pages, and site navigation. Frequency
# cannot filter these out - a cookie banner is on hundreds of sites - so they
# are listed. Curated tags are never dropped, only scraped terms.
STOPLIST = {
    # cookie banners, consent and privacy text
    "consent", "management consent", "preferences", "necessary", "purpose",
    "purposes", "legitimate purpose", "legitimate interest", "statistical",
    "statistical purposes", "anonymous statistical", "technical storage",
    "storage", "cookie", "cookies", "cookie settings", "cookie statement",
    "privacy", "privacy policy", "personal data", "data", "user", "users",
    "browser", "session", "functionality", "functionalities",
    "basic functionalities", "additional data", "subscriber", "vendor",
    # parked domains, hosting and registrars
    "domain", "domains", "domain name", "domain name buy", "domain buy",
    "domain name connect", "domain name check", "largest domain name",
    "subdomain", "hosting", "web hosting", "hosting provider", "webhosting",
    "parked", "registered", "registration", "server", "vps", "vps server",
    "dedicated", "nameshift", "hostnet", "strato", "transip", "vimexx",
    "dns", "forwarding", "ownership", "giant", "transfer", "load link",
    # site navigation and account plumbing
    "password", "password forgotten", "forgot password", "email address",
    "form", "contact form", "country selector", "selector", "menu",
    "log", "login", "sign", "register", "account", "wishlist", "cart",
    "checkout", "newsletter", "subscribe", "share", "download", "downloads",
    "add", "added", "click", "link", "links", "back", "next", "previous",
    "read more", "learn more", "view", "page", "product page", "main content",
    "search", "filter", "sort by", "select", "submit", "send",
    # filler words the extractor keeps because sites repeat them
    "use", "used", "make use", "makes use", "available", "possible", "needed",
    "required", "correct", "ready", "options", "various", "general", "item",
    "items", "thing", "things", "part", "parts", "way", "days", "day", "week",
    "minutes", "first", "along", "come along", "choose", "check", "find",
    "help", "visit", "follow", "start", "create", "reviews", "review",
    # third-party services on the page, not the company's own business
    "google", "google maps", "social media", "facebook", "instagram",
    "linkedin", "youtube", "pinterest", "whatsapp", "tiktok", "media",
}

# Words that say nothing on their own, whatever a site repeats them. A term is
# dropped when every word of it is in here, so 'formerly known' goes and
# 'known brands' stays.
GENERIC_WORDS = {
    "formerly", "known", "various", "several", "many", "much", "more", "most",
    "also", "however", "therefore", "always", "never", "often", "already",
    "still", "together", "whole", "entire", "complete", "different", "similar",
    "particular", "certain", "sure", "able", "enough", "almost", "around",
    "simple", "possible", "needed", "required", "correct", "ready", "available",
    "new", "old", "big", "small", "low", "high", "hot", "long", "short", "wide",
    "good", "better", "best", "great", "nice", "fine", "real", "true", "full",
    "one", "two", "three", "ten", "all", "any", "every", "each", "other",
    "next", "last", "first", "second", "own", "same", "such", "very", "just",
    "get", "got", "put", "see", "say", "says", "may", "can", "will", "would",
    "try", "let", "want", "need", "know", "think", "look", "come", "comes",
    "give", "take", "keep", "made", "make", "makes", "end", "part", "way",
    "thing", "things", "yes", "hey", "wow", "fun", "mad", "etc", "soon",
    "today", "now", "then", "here", "there", "back", "again", "away",
    "please", "thanks", "welcome", "hello", "dear", "sorry",
}

# Short tokens are nearly always codes, initials or file types ('vls', 'afg',
# 'jpg'). These are the ones that do mean something in this trade.
SHORT_ALLOWED = {
    "bag", "rug", "bed", "men", "art", "web", "pvc", "fit", "set", "dye", "net",
    "cap", "xxl", "xxs", "raw", "sew", "cut", "ppe", "diy", "eco", "bio", "oil",
    "tee", "dry", "toy", "air", "oak", "ink", "bra", "zip", "tie", "hat", "fur",
    "wax", "kit", "led", "mix", "rib", "mug", "cup", "ski", "gym", "spa", "lab",
    "tex", "upf", "iso", "csr", "esg", "uv", "3d",
}

# Dutch street names end in these; a company's address is not a keyword.
STREET_SUFFIXES = ("straat", "weg", "laan", "plein", "kade", "dijk", "singel",
                   "dreef", "gracht", "hof", "baan", "pad")

_COUNT_RE = re.compile(r"\s*\(\d+\)\s*$")


def get_engine():
    load_dotenv(ENV_PATH)
    return create_engine(
        f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST')}:25060/{os.getenv('DB_NAME')}",
        connect_args={"ssl": {"ca": os.getenv("DB_CA_CERT")}},
    )


def noise_reason(term: str, places: set) -> str | None:
    """Why a scraped term carries no information, or None to keep it.

    Curated tags never reach this: only the scraped vocabulary is filtered.
    The reason is recorded in the vocabulary table, so the filtering can be
    reviewed instead of taken on trust.
    """
    if term in STOPLIST:
        return "boilerplate"
    if term in places:
        return "place"
    words = term.split()
    # 'formerly known', 'come by', 'very nice' - nothing but filler
    if all(w in GENERIC_WORDS or w in places for w in words):
        return "filler"
    for word in words:
        if word.endswith(STREET_SUFFIXES) and len(word) > 6:
            return "street"                  # an address, not a keyword
    if len(term) <= 3 and term.isalpha() and term not in SHORT_ALLOWED:
        return "short"                       # 'vls', 'afg', 'jpg'
    return None


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


def load_places(engine) -> set:
    """Dutch city and province names, plus countries, as they appear in the
    database: a place is not a keyword ('genemuiden', 'amsterdam')."""
    places = set()
    with engine.connect() as conn:
        for column in ("city", "region"):
            places |= {str(r[0]).strip().lower() for r in conn.execute(text(
                f"SELECT DISTINCT {column} FROM geographies WHERE {column} IS NOT NULL"))}
    return places | {
        "netherlands", "holland", "belgium", "germany", "france", "spain",
        "italy", "portugal", "poland", "turkey", "china", "india", "pakistan",
        "bangladesh", "vietnam", "indonesia", "japan", "korea", "america",
        "united states", "usa", "uk", "england", "europe", "european",
        "african", "asia", "asian", "dutch", "german", "french", "italian",
    }


def build(df: pd.DataFrame, min_companies: int, places: set) -> pd.DataFrame:
    old_terms = [parse_terms(v) for v in df["tags"]]
    new_terms = [parse_terms(k) + parse_terms(b)
                 for k, b in zip(df["english_keywords"], df["english_bigrams"])]

    # How many companies use each scraped keyword; the curated tags are not
    # counted here because they are kept regardless.
    company_count = collections.Counter()
    for terms in new_terms:
        company_count.update(set(terms))
    keep = {t for t, n in company_count.items()
            if n >= min_companies and noise_reason(t, places) is None}

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
    out.attrs["vocabulary_table"] = vocabulary_table(
        old_terms, company_count, keep, min_companies, places)
    return out


def vocabulary_table(old_terms, company_count, keep,
                     min_companies: int, places: set) -> pd.DataFrame:
    """One row per term the dashboard could show, kept or dropped.

    Dropped terms are included with the reason, so the filters can be checked:
    a term missing from the Keywords filter can be looked up here.
    """
    curated_count = collections.Counter()
    for terms in old_terms:
        curated_count.update(set(terms))

    rows = []
    for term in sorted(set(curated_count) | set(company_count)):
        curated, scraped = curated_count.get(term, 0), company_count.get(term, 0)
        if curated:                       # curated tags are never filtered
            kept, reason = True, None
        elif term in keep:
            kept, reason = True, None
        else:
            kept = False
            reason = noise_reason(term, places) or "rare"
        rows.append({
            "term": term,
            "companies": curated + scraped,
            "companies_curated": curated,
            "companies_scraped": scraped,
            "source": "both" if curated and scraped else ("curated" if curated else "scraped"),
            "words": len(term.split()),
            "kept": int(kept),
            "drop_reason": reason,
        })
    table = pd.DataFrame(rows)
    table.attrs["min_companies"] = min_companies
    return table


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
          f"({df['english_keywords'].notna().sum():,} with scraped keywords, "
          f"{df['english_bigrams'].notna().sum():,} with bigrams)")

    places = load_places(engine)
    out = build(df, args.min_companies, places)
    vocabulary = out.attrs["vocabulary"]
    gained = out["n_new"] - out["n_old"]
    if args.min_companies > 1:
        print(f"scraped keywords dropped (used by < {args.min_companies} companies, "
              f"or on the stoplist): {out.attrs['dropped']:,}")
    print(f"vocabulary for the Keywords filter: {len(vocabulary):,} terms")
    print(f"terms per company: {out['n_old'].mean():.1f} -> {out['n_new'].mean():.1f} "
          f"(max {out['n_new'].max()}) · {int((gained > 0).sum()):,} companies gained terms")

    vocab = out.attrs["vocabulary_table"]
    dropped = vocab[vocab["kept"] == 0]["drop_reason"].value_counts().to_dict()
    print(f"vocabulary table: {len(vocab):,} terms · kept {int(vocab['kept'].sum()):,} · "
          f"dropped {dict(sorted(dropped.items(), key=lambda kv: -kv[1]))}")

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
        # The vocabulary is written from the same run as the per-company
        # strings, so the two cannot drift apart.
        vocab.to_sql(VOCAB_TABLE, conn, if_exists="replace", index=False,
                     chunksize=1000, dtype={"term": String(191)})
        # Binary collation: MySQL's default ignores accents, and would refuse
        # the key over pairs like 'dia' / 'đia' that are distinct terms here.
        conn.execute(text(f"ALTER TABLE `{VOCAB_TABLE}` MODIFY term "
                          f"VARCHAR(191) COLLATE utf8mb4_bin NOT NULL"))
        conn.execute(text(f"ALTER TABLE `{VOCAB_TABLE}` ADD PRIMARY KEY (term)"))
        conn.execute(text(f"CREATE INDEX idx_kept ON `{VOCAB_TABLE}` (kept, companies)"))
    print(f"wrote table `{args.table}` ({len(out):,} rows) and `{VOCAB_TABLE}` "
          f"({len(vocab):,} rows); `tags` left untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
