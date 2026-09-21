"""
fix_city_names.py
─────────────────
Merge the different spellings of one city in `organizations.city`, so the
dashboard shows one map bubble and one dropdown entry per place.

The source data spells some cities several ways: 'Amstelveen' / 'AMSTELVEEN',
'Àmsterdam', 'Bergen op Zoom' / 'Bergen Op Zoom', 'Bergen (Nh)' / 'Bergen (NH)'.

What counts as the same city
----------------------------
Two spellings are merged when they differ only in capitals, accents,
punctuation or how a province suffix is written ('Afferden L' / 'Afferden Lb').
The province suffix itself is kept: it separates different towns with the same
name ('Hengelo (Gld)' is not Hengelo in Overijssel, 'Katwijk NB' not Katwijk ZH).

Which spelling wins
-------------------
One that already exists in the data, preferring mixed case over ALL CAPS, then
Dutch particle casing ('Bergen op Zoom', not 'Bergen Op Zoom'), then the most
used. A city only ever written in capitals ('RIJSWIJK ZH') is re-cased
('Rijswijk ZH').

The dashboard joins `geographies` on city, so a new name must still match a
`geographies` row (MySQL compares ignoring case and accents); a rename that
would lose its coordinates is skipped and reported.

Usage
-----
    python db/mysql/fix_city_names.py            # print the changes, write nothing
    python db/mysql/fix_city_names.py --apply    # back up old names, then update

--apply first copies every changed row's old name into `city_fix_backup`.
To undo the latest run:
    UPDATE organizations o JOIN city_fix_backup b ON b.org_id = o.id
       SET o.city = b.old_city
     WHERE b.fixed_at = (SELECT MAX(fixed_at) FROM city_fix_backup);
"""

from __future__ import annotations

import argparse
import os
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ENV_PATH = Path(__file__).resolve().parent / ".env"

# Province suffixes as they appear in the data, and one way to write each.
PROVINCE_CODES = {
    "nh": "NH", "zh": "ZH", "nb": "NB", "gld": "GLD", "gl": "GLD", "ut": "UT",
    "utr": "UT", "ov": "OV", "fr": "FR", "gr": "GR", "dr": "DR", "lb": "L",
    "l": "L", "zl": "ZL", "fl": "FL",
}
_PROVINCE_RE = re.compile(
    r"[\s(]+(" + "|".join(sorted(PROVINCE_CODES, key=len, reverse=True)) + r")\)?\.?\s*$",
    re.IGNORECASE)
# Linking words written in lower case inside Dutch place names.
PLACE_PARTICLES = {"aan", "ad", "a/d", "bij", "de", "den", "der", "en", "het",
                   "in", "onder", "op", "over", "te", "ter", "van"}


def get_engine():
    load_dotenv(ENV_PATH)
    return create_engine(
        f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST')}:25060/{os.getenv('DB_NAME')}",
        connect_args={"ssl": {"ca": os.getenv("DB_CA_CERT")}},
    )


def split_province(name: str) -> tuple[str, str | None, str]:
    """'Bergen (Nh)' -> ('Bergen', 'NH', ' (Nh)'): base, suffix code, suffix as written."""
    m = _PROVINCE_RE.search(name)
    if not m or not name[:m.start()].strip():
        return name.strip(), None, ""
    return name[:m.start()].strip(), PROVINCE_CODES[m.group(1).lower()], name[m.start():]


def fold(text_: str) -> str:
    """Lower case without accents: how MySQL compares the city columns."""
    n = unicodedata.normalize("NFKD", text_).encode("ascii", "ignore").decode()
    return n.lower().strip()


def city_key(name: str) -> tuple[str, str | None]:
    base, code, _ = split_province(name)
    return re.sub(r"[^a-z0-9]", "", fold(base)), code


def place_case(base: str) -> str:
    """Dutch place-name casing: 'MILLINGEN AAN DE RIJN' -> 'Millingen aan de Rijn',
    'IJSSELSTEIN' -> 'IJsselstein'. Mixed-case names only get their particles fixed."""
    def cap(part: str) -> str:
        if part.upper().startswith("IJ"):
            return "IJ" + part[2:].lower()
        return part[:1].upper() + part[1:].lower()

    words = base.split()
    if base.isupper():
        words = ["-".join(cap(p) for p in w.split("-")) for w in words]
    return " ".join(w.lower() if i and w.lower() in PLACE_PARTICLES else w
                    for i, w in enumerate(words))


def canonical_names(counts: Counter) -> dict[str, str]:
    """{spelling: the spelling to use} for every spelling that should change."""
    groups: dict[tuple, list[str]] = {}
    for name in counts:
        groups.setdefault(city_key(name), []).append(name)

    renames = {}
    for spellings in groups.values():
        def rank(name):
            base = split_province(name)[0]
            return (not base.isupper(),              # mixed case first
                    place_case(base) == base,        # then correct particles
                    counts[name],                    # then the most used
                    name)                            # stable tie-break
        best = max(spellings, key=rank)
        base, _, suffix = split_province(best)
        if base.isupper():                           # only ever written in capitals
            best = place_case(base) + suffix.upper()
        for name in spellings:
            if name != best:
                renames[name] = best
    return renames


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true",
                   help="write the changes (default: only print them)")
    args = p.parse_args()

    engine = get_engine()
    with engine.connect() as conn:
        orgs = conn.execute(text(
            "SELECT id, city FROM organizations WHERE city IS NOT NULL AND city <> ''"
        )).fetchall()
        geo_cities = {fold(r[0]) for r in conn.execute(text(
            "SELECT DISTINCT city FROM geographies WHERE city IS NOT NULL"))}

    counts = Counter(city for _, city in orgs)
    renames = canonical_names(counts)

    # Keep only renames that still find their coordinates in `geographies`.
    lost = {old: new for old, new in renames.items() if fold(new) not in geo_cities}
    renames = {old: new for old, new in renames.items() if old not in lost}

    changes = [(org_id, city, renames[city]) for org_id, city in orgs if city in renames]
    if not changes:
        print("No city names to merge.")
        return 0

    print(f"{len(renames)} spellings to rename, {len(changes)} organizations affected:\n")
    for old, new in sorted(renames.items(), key=lambda kv: (kv[1].lower(), kv[0])):
        print(f"  {old!r:30} -> {new!r:30} ({counts[old]} org{'s' if counts[old] > 1 else ''})")
    if lost:
        print(f"\nSkipped, the new name has no row in `geographies`:")
        for old, new in lost.items():
            print(f"  {old!r} -> {new!r}")

    if not args.apply:
        print("\nDry run: nothing written. Re-run with --apply to update the database.")
        return 0

    fixed_at = datetime.now().replace(microsecond=0)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS city_fix_backup ("
            " backup_id INT AUTO_INCREMENT PRIMARY KEY,"
            " org_id BIGINT NOT NULL, old_city TEXT, new_city TEXT, fixed_at DATETIME)"))
        conn.execute(text(
            "INSERT INTO city_fix_backup (org_id, old_city, new_city, fixed_at) "
            "VALUES (:id, :old, :new, :at)"),
            [{"id": i, "old": o, "new": n, "at": fixed_at} for i, o, n in changes])
        conn.execute(text("UPDATE organizations SET city = :new WHERE id = :id"),
                     [{"id": i, "new": n} for i, _, n in changes])
    print(f"\nUpdated {len(changes)} organizations; old names saved in "
          f"`city_fix_backup` (fixed_at = {fixed_at}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
