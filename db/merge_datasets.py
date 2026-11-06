"""
merge_datasets.py
─────────────────
Merge the two company datasets into one table:

    KvK      KvK_companies.xlsx, sheet 'Kvk_Modint_2021'  (11.8k rows, the TELL base;
             its row index is the `organizations.id` used in the database)
    Textile  companies.xlsx,     sheet 'Textile_filter'   (15.2k rows, directory
             listing with e-mail, website and a free-text category)

Matching
--------
A Textile row is linked to a KvK row when they share identifiers:

    website domain  +3      telephone   +3
    company name    +3 (exact after normalising) or +2 (≥ 0.85 similar)
    postcode        +2      city        +1

and either the location agrees (same postcode or city) with a score of at least
4, or — for companies that moved — a non-chain website *and* the name agree.
Chain domains (zeeman.com appears 541 times) never count without a location
match. Every row is matched at most once, best-scoring pairs first.

Output (one workbook)
---------------------
    merged    all KvK rows, enriched from their Textile match (e-mail, and
              website / telephone / address where KvK has none), followed by the
              unmatched Textile rows mapped onto the same columns.
              `source` is kvk / both / textile; `kvk_id` keeps the database id.
    matches   one row per matched pair with both names, the keys that agreed and
              the score — sort by score to review the weakest links.
    summary   counts.

Usage
-----
    python db/merge_datasets.py
    python db/merge_datasets.py --output "C:\\...\\merged_companies.xlsx"
"""

import argparse
import difflib
import re
from collections import defaultdict

import pandas as pd

DATA_DIR = r"C:\Users\fsollit\Desktop\Data\TELL"
KVK_PATH = DATA_DIR + r"\KvK_companies.xlsx"
TEXTILE_PATH = DATA_DIR + r"\companies.xlsx"
OUTPUT_PATH = DATA_DIR + r"\merged_companies.xlsx"

CHAIN_MIN_ROWS = 4          # a domain on this many rows is treated as a chain
NAME_SIMILAR = 0.85


# ── Normalisation ─────────────────────────────────────────────────────────────
_LEGAL_WORDS = re.compile(
    r"\b(b\s?\.?\s?v|v\s?\.?\s?o\s?\.?\s?f|n\s?\.?\s?v|c\s?\.?\s?v|holding|"
    r"de|het|the|en|and)\b\.?"
)


def norm_domain(url):
    if pd.isna(url):
        return None
    u = str(url).strip().lower()
    if "@" in u and "//" not in u:
        return None
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\d?\.", "", u)
    u = re.split(r"[/?#\s]", u)[0].strip(".")
    return u if "." in u else None


def norm_postcode(zip_code):
    if pd.isna(zip_code):
        return None
    z = re.sub(r"\s", "", str(zip_code)).upper()
    return z if re.fullmatch(r"\d{4}[A-Z]{2}", z) else None


def norm_name(name):
    if pd.isna(name):
        return None
    n = _LEGAL_WORDS.sub(" ", str(name).lower().replace("&", " "))
    n = re.sub(r"[^a-z0-9]", "", n)
    return n or None


def norm_phone(tel):
    if pd.isna(tel):
        return None
    d = re.sub(r"\D", "", str(tel))
    if d.startswith("0031"):
        d = "0" + d[4:]
    elif d.startswith("31") and len(d) == 11:
        d = "0" + d[2:]
    elif not d.startswith("0") and len(d) == 9:
        d = "0" + d               # leading zero lost when stored as a number
    return d if len(d) == 10 else None


def norm_city(city):
    if pd.isna(city) or str(city).strip() in ("", "0"):
        return None
    return re.sub(r"\s+", " ", str(city).strip().lower())


def split_address(address):
    """'Hamseweg 90 a' -> ('Hamseweg', 90, 'a')."""
    if pd.isna(address):
        return None, None, None
    m = re.match(r"^(.*?)\s+(\d+)\s*(.*)$", str(address).strip())
    if not m:
        return str(address).strip(), None, None
    return m.group(1), int(m.group(2)), (m.group(3) or None)


def add_keys(df, name, postcode, city, phone, website):
    df["_name"] = df[name].map(norm_name)
    df["_pc"] = df[postcode].map(norm_postcode)
    df["_city"] = df[city].map(norm_city)
    df["_tel"] = df[phone].map(norm_phone)
    df["_dom"] = df[website].map(norm_domain)


# ── Matching ──────────────────────────────────────────────────────────────────
def score_pair(a, b, chain_domains):
    # Bracket access throughout: `row._name` is pandas' own Series attribute.
    agreed = []
    score = 0
    same_pc = bool(a["_pc"] and a["_pc"] == b["_pc"])
    same_city = bool(a["_city"] and a["_city"] == b["_city"])
    same_dom = bool(a["_dom"] and a["_dom"] == b["_dom"])
    if not (same_pc or same_city or same_dom):
        return False, 0, []          # can never be accepted; skip the name comparison

    if same_dom:
        score += 3
        agreed.append("website")
    if a["_tel"] and a["_tel"] == b["_tel"]:
        score += 3
        agreed.append("telephone")
    name_match = False
    if a["_name"] and b["_name"]:
        if a["_name"] == b["_name"]:
            score += 3
            agreed.append("name")
            name_match = True
        elif difflib.SequenceMatcher(None, a["_name"], b["_name"]).ratio() >= NAME_SIMILAR:
            score += 2
            agreed.append("name~")
            name_match = True
    if same_pc:
        score += 2
        agreed.append("postcode")
    if same_city:
        score += 1
        agreed.append("city")

    located = same_pc or same_city
    moved = same_dom and a["_dom"] not in chain_domains and name_match
    accept = (located and score >= 4 and len(agreed) >= 2) or moved
    return accept, score, agreed


def match(kvk, tex):
    chain_domains = {
        d for d, n in pd.concat([kvk._dom, tex._dom]).value_counts().items()
        if n >= CHAIN_MIN_ROWS
    }

    # Plain dicts: indexing pandas rows inside this loop is far too slow.
    keys = ["_dom", "_tel", "_name", "_pc", "_city"]
    # pandas turns the normalisers' None into NaN, which is truthy and would
    # make every row with a missing phone a "candidate" of every other one.
    def records(df):
        return {idx: {k: (None if pd.isna(v) else v) for k, v in row.items()}
                for idx, row in zip(df.index, df[keys].to_dict("records"))}

    kvk_rows = records(kvk)
    tex_rows = records(tex)

    index = {k: defaultdict(list) for k in ("_dom", "_tel", "_name", "_pc")}
    for j, row in tex_rows.items():
        for k in index:
            if row[k]:
                index[k][row[k]].append(j)

    pairs = []
    for i, a in kvk_rows.items():
        candidates = set()
        for k in ("_tel", "_name", "_pc"):
            if a[k]:
                candidates.update(index[k].get(a[k], ()))
        if a["_dom"]:
            doms = index["_dom"].get(a["_dom"], ())
            if a["_dom"] in chain_domains:     # only branches in the same place
                doms = [j for j in doms if tex_rows[j]["_pc"] == a["_pc"]
                        or tex_rows[j]["_city"] == a["_city"]]
            candidates.update(doms)
        for j in candidates:
            ok, score, agreed = score_pair(a, tex_rows[j], chain_domains)
            if ok:
                pairs.append((score, i, j, agreed))

    # Best pairs first; each row on either side is used once.
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
    used_k, used_t, result = set(), set(), []
    for score, i, j, agreed in pairs:
        if i in used_k or j in used_t:
            continue
        used_k.add(i)
        used_t.add(j)
        result.append((i, j, score, "+".join(agreed)))
    return result


# ── Merge ─────────────────────────────────────────────────────────────────────
def load_kvk(path):
    kvk = pd.read_excel(path, sheet_name="Kvk_Modint_2021")
    kvk = kvk.loc[:, ~kvk.columns.str.startswith("Unnamed")]
    kvk.insert(0, "kvk_id", kvk.index)          # = organizations.id in the DB
    add_keys(kvk, "trade_name", "postcode", "city", "telephone", "website")

    geo = pd.read_excel(path, sheet_name="Geographies")
    pc4_region = dict(zip(geo["PC4"].astype(int), geo["Provincie name"]))
    return kvk, pc4_region


def load_textile(path):
    tex = pd.read_excel(path, sheet_name="Textile_filter")
    tex.insert(0, "textile_id", tex.index)
    add_keys(tex, "Name", "Zip", "City", "Tel", "Website")
    return tex


def merge(kvk, tex, pc4_region, pairs):
    merged = kvk.copy()
    merged.insert(1, "source", "kvk")
    merged.insert(2, "textile_id", pd.NA)
    merged["email"] = pd.NA
    merged["textile_category"] = pd.NA
    merged["textile_description"] = pd.NA
    merged["match_score"] = pd.NA
    merged["match_on"] = pd.NA

    fill_if_empty = {"website": "Website", "telephone": "Tel"}
    for i, j, score, agreed in pairs:
        t = tex.loc[j]
        merged.at[i, "source"] = "both"
        merged.at[i, "textile_id"] = j
        merged.at[i, "email"] = t["Email"]
        merged.at[i, "textile_category"] = t["New category"]
        merged.at[i, "textile_description"] = t["Category"]
        merged.at[i, "match_score"] = score
        merged.at[i, "match_on"] = agreed
        for kvk_col, tex_col in fill_if_empty.items():
            if pd.isna(merged.at[i, kvk_col]) and pd.notna(t[tex_col]):
                merged.at[i, kvk_col] = t[tex_col]

    matched_t = {j for _, j, _, _ in pairs}
    rest = tex.loc[~tex.index.isin(matched_t)].copy()
    street = rest["Address"].map(split_address)
    pc4 = rest["_pc"].str[:4].astype("Int64")
    extra = pd.DataFrame({
        "kvk_id": pd.NA,
        "source": "textile",
        "textile_id": rest["textile_id"],
        "trade_name": rest["Name"],
        "visiting address_street": street.str[0],
        "visiting address_house number": street.str[1],
        "visiting address_house number_addition": street.str[2],
        "postcode": rest["_pc"].fillna(rest["Zip"]),
        "city": rest["City"],
        "region": pc4.map(pc4_region),
        "telephone": rest["Tel"],
        "website": rest["Website"],
        "email": rest["Email"],
        "textile_category": rest["New category"],
        "textile_description": rest["Category"],
    }, index=rest.index)

    out = pd.concat([merged, extra], ignore_index=True)
    out = out.drop(columns=[c for c in out.columns if c.startswith("_")])
    return out


def match_table(kvk, tex, pairs):
    rows = []
    for i, j, score, agreed in pairs:
        a, t = kvk.loc[i], tex.loc[j]
        rows.append({
            "match_score": score, "match_on": agreed,
            "kvk_id": i, "kvk_name": a["trade_name"], "kvk_postcode": a["postcode"],
            "kvk_city": a["city"], "kvk_website": a["website"],
            "textile_id": j, "textile_name": t["Name"], "textile_zip": t["Zip"],
            "textile_city": t["City"], "textile_website": t["Website"],
        })
    return pd.DataFrame(rows).sort_values(["match_score", "kvk_id"])


def main():
    p = argparse.ArgumentParser(description="Merge the KvK and Textile company datasets.")
    p.add_argument("--kvk", default=KVK_PATH)
    p.add_argument("--textile", default=TEXTILE_PATH)
    p.add_argument("--output", default=OUTPUT_PATH)
    args = p.parse_args()

    kvk, pc4_region = load_kvk(args.kvk)
    tex = load_textile(args.textile)
    print(f"KvK: {len(kvk):,} rows · Textile: {len(tex):,} rows")

    pairs = match(kvk, tex)
    merged = merge(kvk, tex, pc4_region, pairs)
    matches = match_table(kvk, tex, pairs)

    counts = merged["source"].value_counts()
    summary = pd.DataFrame([
        ("KvK rows", len(kvk)),
        ("Textile rows", len(tex)),
        ("Matched pairs", len(pairs)),
        ("Merged rows", len(merged)),
        ("  source = both", counts.get("both", 0)),
        ("  source = kvk only", counts.get("kvk", 0)),
        ("  source = textile only", counts.get("textile", 0)),
        ("Rows with website", int(merged["website"].notna().sum())),
        ("Rows with e-mail", int(merged["email"].notna().sum())),
    ], columns=["metric", "value"])

    with pd.ExcelWriter(args.output) as xw:
        merged.to_excel(xw, sheet_name="merged", index=False)
        matches.to_excel(xw, sheet_name="matches", index=False)
        summary.to_excel(xw, sheet_name="summary", index=False)

    print(summary.to_string(index=False))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
