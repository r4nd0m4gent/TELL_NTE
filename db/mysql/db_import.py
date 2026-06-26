import pandas as pd
from sqlalchemy import create_engine, text
import pgeocode
import os
from dotenv import load_dotenv

load_dotenv()

engine = create_engine(
    f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST')}:25060/{os.getenv('DB_NAME')}",
    connect_args={"ssl": {"ca": os.getenv("DB_CA_CERT")}},
)

df_org = pd.read_excel(
    r"C:\Users\fsollit\Desktop\Data\TELL\companies.xlsx",
    sheet_name=0,
    usecols=[0, 1, 6, 7, 8, 9, 14, 15, 17, 19, 20, 22, 24]
)
df_org.index.name = "id"

df_geo = pd.read_excel(
    r"C:\Users\fsollit\Desktop\Data\TELL\companies.xlsx",
    sheet_name=0,
    usecols=[14, 15, 16]
)

# Normalise text so de-duplication matches MySQL's case-insensitive,
# trailing-space-insensitive collation. City must not be null; postcode may be.
df_geo["postcode"] = df_geo["postcode"].astype("string").str.strip()
df_geo["city"] = df_geo["city"].astype("string").str.strip()
df_geo = df_geo[df_geo["city"].notna() & (df_geo["city"] != "")]

# One row per unique postcode-city combination (case-insensitive, no duplicates).
_key_pc = df_geo["postcode"].str.upper()
_key_city = df_geo["city"].str.casefold()
df_geo = (
    df_geo.assign(_kpc=_key_pc, _kcity=_key_city)
    .drop_duplicates(subset=["_kpc", "_kcity"])
    .reset_index(drop=True)
)

# Geocode Dutch postcodes to latitude/longitude. The NL pgeocode dataset is
# keyed by the 4-digit numeric part, so extract that before querying.
_nomi = pgeocode.Nominatim("nl")
_postcodes = df_geo["postcode"].astype(str).str.extract(r"(\d{4})")[0]
_geo = _nomi.query_postal_code(_postcodes.tolist())
df_geo["latitude"] = _geo["latitude"].values
df_geo["longitude"] = _geo["longitude"].values

# Collapse coordinates to a single point per city (median of its postcodes)
# so the map renders one bubble per city. Group on the case-insensitive key.
_city_coords = df_geo.groupby("_kcity")[["latitude", "longitude"]].transform("median")
df_geo["latitude"] = _city_coords["latitude"]
df_geo["longitude"] = _city_coords["longitude"]

df_geo = df_geo.drop(columns=["_kpc", "_kcity"])
df_geo.index.name = "id"

df_tags = pd.read_excel(
    r"C:\Users\fsollit\Desktop\Data\TELL\companies.xlsx",
    sheet_name=0,
    usecols=[29, 30, 31]
)
df_tags.index.name = "id"

with engine.begin() as conn:
    conn.execute(text("SET SESSION sql_require_primary_key = 0"))
    df_org.to_sql("organizations", conn, if_exists="replace", index=True)
    conn.execute(text("ALTER TABLE organizations ADD PRIMARY KEY (id)"))

    # Load to a staging table, then let MySQL collapse any remaining duplicates
    # using its own (case/space-insensitive) collation so the unique key holds.
    df_geo.to_sql("geo_stage", conn, if_exists="replace", index=True)
    conn.execute(text("DROP TABLE IF EXISTS geographies"))
    conn.execute(text(
        "CREATE TABLE geographies AS "
        "SELECT MIN(id) AS id, postcode, city, MAX(region) AS region, "
        "AVG(latitude) AS latitude, AVG(longitude) AS longitude "
        "FROM geo_stage GROUP BY postcode, city"
    ))
    conn.execute(text("DROP TABLE geo_stage"))
    conn.execute(text("ALTER TABLE geographies ADD PRIMARY KEY (id)"))
    conn.execute(text(
        "ALTER TABLE geographies ADD CONSTRAINT uq_postcode_city "
        "UNIQUE (postcode(16), city(128))"
    ))

    df_tags.to_sql("tags", conn, if_exists="replace", index=True)
    conn.execute(text("ALTER TABLE tags ADD PRIMARY KEY (id)"))

