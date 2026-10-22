"""
build_network_locations.py
──────────────────────────
Write site/network/data/org_locations.json: where the organisations in the
funding network are, taken from the CORDIS project exports (Horizon Europe and
Horizon 2020). CORDIS gives every participant a city, coordinates and a type.

    HES  higher or secondary education     PRC  private company
    REC  research organisation             PUB  public body       OTH  other

The page looks organisations up by name and country ("NAME|CC", name in
capitals with single spaces), so Erasmus+ and NWO partners are found too when
they also take part in a Horizon project under the same name. NWO lists no
countries; its partners are looked up as Dutch. Only organisations that occur
in the page's data are written.

Dutch organisations are placed by data/nl_org_locations.json first (their
main seat); this file fills in the rest, and places the European partners.

CORDIS coordinates are not always right: some sit in the country's capital
(York, Lancaster and Cambridge all in central London; an organisation in Milan
placed in Rome). So each one is checked against its city in GeoNames
(cities1000): if a place of that name lies within MAX_KM, the CORDIS point is
kept (it is the address); if not, the city's own coordinates are used; if the
city is not found, the CORDIS point stays.

The CORDIS exports (~150 MB) and GeoNames cities (~11 MB) are downloaded to
data/ once and reused.

Usage
-----
    python build_network_locations.py
    python build_network_locations.py --cordis-dir data/cordis
"""

from __future__ import annotations

import argparse
import io
import json
import math
import re
import unicodedata
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "site" / "network" / "data"
OUT = DATA / "org_locations.json"
GEONAMES_URL = "https://download.geonames.org/export/dump/cities1000.zip"
MAX_KM = 25       # a CORDIS point this close to a place of its city's name is kept
ISO = {"UK": "GB", "EL": "GR"}   # CORDIS country codes that differ from ISO
# First words of place names that are no place on their own ("Saint-Andre de
# Cubzac" must not fall back to any "Saint")
GENERIC = {"saint", "sainte", "sankt", "sint", "san", "santa", "santo", "sao", "bad", "new",
           "north", "south", "east", "west", "nord", "sud", "port", "mont", "monte", "villa",
           "castel", "castelnuovo", "puerto", "porto", "nova", "novo", "nove", "stara", "nowy",
           "groot", "klein", "gross", "neu", "alt", "upper", "lower", "great", "little", "la", "le"}
EXPORTS = {   # same exports as textile-network/scrapers/cordis.py
    "cordis-HORIZONprojects-xlsx.zip": "https://cordis.europa.eu/data/cordis-HORIZONprojects-xlsx.zip",
    "cordis-h2020projects-xlsx.zip": "https://cordis.europa.eu/data/cordis-h2020projects-xlsx.zip",
}


def key(name: str | None, country: str | None) -> str:
    return " ".join(str(name or "").upper().split()) + "|" + str(country or "").upper().strip()


def wanted_keys() -> set[str]:
    """Name|country of every organisation in the page's data."""
    keys = set()
    for fname in ("cordis_textile_projects.json", "erasmus_textile_projects.json"):
        data = json.loads((DATA / fname).read_text(encoding="utf-8"))
        for p in data["projects"]:
            for m in p.get("project_members") or []:
                keys.add(key(m.get("organisation"), m.get("country")))
    for p in json.loads((DATA / "nwo_textile_raw.json").read_text(encoding="utf-8")):
        for m in p.get("project_members") or []:
            keys.add(key((m.get("organisation") or "").split("||")[0], "NL"))
    return keys


def export(cordis_dir: Path, fname: str) -> Path:
    path = cordis_dir / fname
    if not path.exists():
        cordis_dir.mkdir(parents=True, exist_ok=True)
        print(f"downloading {EXPORTS[fname]} …")
        urllib.request.urlretrieve(EXPORTS[fname], path)
    return path


def participants(zip_path: Path):
    """(name, country, city, lat, lon, type) for each row of organization.xlsx."""
    with zipfile.ZipFile(zip_path) as z:
        wb = load_workbook(io.BytesIO(z.read("organization.xlsx")), read_only=True)
    rows = wb.worksheets[0].iter_rows(values_only=True)
    header = next(rows)
    for row in rows:
        r = dict(zip(header, row))
        try:
            lat, lon = (float(v) for v in str(r.get("geolocation") or "").split(","))
        except ValueError:
            continue
        yield r.get("name"), r.get("country"), (r.get("city") or "").strip(), lat, lon, r.get("activityType") or ""


def download(url: str, path: Path) -> Path:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {url} …")
        urllib.request.urlretrieve(url, path)
    return path


def norm(text: str) -> str:
    """Lower case, no accents, words of letters and digits: 'Münster' -> 'munster'."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def city_index(path: Path, countries: set[str]) -> dict:
    """(country, normalised name) -> [(population, lat, lon)], all names of a place."""
    index = defaultdict(list)
    with zipfile.ZipFile(path) as z, z.open("cities1000.txt") as fh:
        for line in io.TextIOWrapper(fh, encoding="utf-8"):
            f = line.rstrip("\n").split("\t")
            if f[8] not in countries:
                continue
            entry = (int(f[14] or 0), float(f[4]), float(f[5]))
            for name in {f[1], f[2], *f[3].split(",")}:
                if (k := norm(name)):
                    index[(f[8], k)].append(entry)
    return index


def city_names(city: str) -> list[tuple[str, bool]]:
    """Names to try for a CORDIS city, most specific first, each with whether it
    is only the first word(s) of a name: 'YORK NORTH YORKSHIRE' -> ...,
    ('york', True); 'Bruxelles / Brussel' -> ('bruxelles', False), ('brussel', False)."""
    out, seen = [], set()
    for part in [city, *re.split(r"\s*(?:/|,|\(|\)|\s-\s|\s–\s)\s*", city)]:
        words = norm(part).split()
        for i in range(len(words), 0, -1):
            name = " ".join(words[:i])
            if i == 1 and (len(name) < 4 or name in GENERIC):   # would match too much
                break
            if name not in seen:
                seen.add(name)
                out.append((name, i < len(words)))
    return out


def km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def check(index: dict, city: str, country: str, lat: float, lon: float, fallback: bool):
    """(lat, lon, verdict): 'ok' kept, 'moved' to the city, 'unchecked' city not found.
    `fallback`: the CORDIS point is one CORDIS uses for many cities (e.g. the
    capital), so it says nothing about where the organisation is."""
    cc = ISO.get(country, country)
    for name, partial in city_names(city):
        places = index.get((cc, name))
        if not places:
            continue
        if any(km(lat, lon, plat, plon) <= MAX_KM for _, plat, plon in places):
            return lat, lon, "ok"
        # Only the first word(s) of the name matched ('Villars' for 'Villars S
        # Glane'): too weak to overrule a CORDIS point, unless that is a fallback
        if partial and not fallback:
            continue
        _, plat, plon = max(places)    # the most populous place of that name
        return round(plat, 4), round(plon, 4), "moved"
    return lat, lon, "unchecked"


def fallback_points(found: dict) -> set[tuple[float, float]]:
    """Points CORDIS gives to organisations in many different cities."""
    cities = defaultdict(set)
    for city, lat, lon, _ in found.values():
        cities[(round(lat, 2), round(lon, 2))].add(norm(city))
    return {pt for pt, names in cities.items() if len(names) >= 3}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cordis-dir", type=Path, default=ROOT / "data" / "cordis",
                   help="where the CORDIS exports are kept (default: data/cordis)")
    args = p.parse_args()

    wanted = wanted_keys()
    # An organisation appears once per project; keep its most frequent location
    seen: dict[str, Counter] = defaultdict(Counter)
    for fname in EXPORTS:
        for name, country, city, lat, lon, typ in participants(export(args.cordis_dir, fname)):
            k = key(name, country)
            if k in wanted:
                seen[k][(city, round(lat, 4), round(lon, 4), typ)] += 1

    found = {k: list(c.most_common(1)[0][0]) for k, c in sorted(seen.items())}

    countries = {ISO.get(k.rsplit("|", 1)[1], k.rsplit("|", 1)[1]) for k in found}
    index = city_index(download(GEONAMES_URL, ROOT / "data" / "geonames" / "cities1000.zip"), countries)
    shared = fallback_points(found)
    verdicts, moved = Counter(), []
    for k, loc in found.items():
        city, lat, lon, _ = loc
        loc[1], loc[2], verdict = check(index, city, k.rsplit("|", 1)[1], lat, lon,
                                        (round(lat, 2), round(lon, 2)) in shared)
        verdicts[verdict] += 1
        if verdict == "moved":
            moved.append(f"{k.split('|')[0][:45]} ({city}): {lat:.2f},{lon:.2f} -> {loc[1]:.2f},{loc[2]:.2f}")
    print(f"locations checked against GeoNames: {dict(verdicts)}; "
          f"{len(shared)} points shared by organisations in 3+ cities")
    for line in moved[::max(1, len(moved) // 25)]:   # a sample to eyeball
        print("  moved", line)

    # [city, lat, lon, type] per organisation, compact
    out = {
        "_about": ("Locations of the funding network's organisations from the CORDIS exports "
                   "(Horizon Europe, Horizon 2020). Key: NAME|COUNTRY, name in capitals. "
                   "Value: [city, lat, lon, activity type (HES/REC/PRC/PUB/OTH)]. "
                   "Coordinates checked against GeoNames cities (CC BY 4.0). "
                   "Written by build_network_locations.py."),
        "organisations": {k: [city, lat, lon, typ] for k, (city, lat, lon, typ) in found.items()},
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    types = Counter(v[3] for v in out["organisations"].values())
    print(f"{len(found)} of {len(wanted)} organisations located -> {OUT.relative_to(ROOT)}"
          f" ({OUT.stat().st_size/1024:.0f} KB); types {dict(types)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
