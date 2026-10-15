"""Generate a large synthetic wine graph for benchmarks and PROFILE screenshots.

The real ETL (scripts/seed_data.py) loads the Kaggle data; this script only exists so
query performance can be measured at scale before the full dataset is loaded. The shape
follows docs/contracts.md exactly, including the awkward parts of the real data:
~15% of wines have no Region, ~7% have no price, and descriptor usage is skewed
(a few very common notes = supernodes).

WIPES the target database. Dev instance only:
    make bench-data            # 100k wines
    BENCH_WINES=20000 make bench-data
"""

import os
import random
import sys
import time
from typing import Any

from dotenv import load_dotenv
from neo4j import GraphDatabase, Session

load_dotenv()  # NEO4J_ADMIN_USER / NEO4J_ADMIN_PASSWORD from .env

URI = os.getenv("NEO4J_BENCH_URI", "bolt://localhost:7687")
AUTH = (os.getenv("NEO4J_ADMIN_USER", "neo4j"), os.getenv("NEO4J_ADMIN_PASSWORD", "devpassword123"))
N_WINES = int(os.getenv("BENCH_WINES", "100000"))
BATCH = 5000
SEED = 42

COUNTRIES = {
    "France": ["Bordeaux", "Burgundy", "Rhone Valley", "Alsace"],
    "Italy": ["Tuscany", "Piedmont", "Veneto"],
    "US": ["California", "Washington", "Oregon", "New York"],
    "Spain": ["Rioja", "Catalonia", "Galicia"],
    "Portugal": ["Douro", "Alentejano"],
    "Germany": ["Mosel", "Rheingau"],
    "Australia": ["South Australia", "Victoria"],
    "Chile": ["Colchagua Valley", "Maipo Valley"],
    "Argentina": ["Mendoza Province"],
    "New Zealand": ["Marlborough", "Central Otago"],
}
VARIETIES = [
    "Pinot Noir",
    "Chardonnay",
    "Cabernet Sauvignon",
    "Red Blend",
    "Bordeaux-style Red Blend",
    "Riesling",
    "Sauvignon Blanc",
    "Syrah",
    "Rose",
    "Merlot",
    "Nebbiolo",
    "Zinfandel",
    "Sangiovese",
    "Malbec",
    "Portuguese Red",
    "White Blend",
    "Sparkling Blend",
    "Tempranillo",
    "Rhone-style Red Blend",
    "Pinot Gris",
    "Champagne Blend",
    "Cabernet Franc",
    "Grüner Veltliner",
    "Portuguese White",
    "Gewürztraminer",
    "Gamay",
    "Viognier",
    "Shiraz",
    "Petite Sirah",
    "Grenache",
]
DESCRIPTORS = [
    "fruit",
    "cherry",
    "black fruit",
    "tannic",
    "oak",
    "spice",
    "plum",
    "berry",
    "citrus",
    "apple",
    "vanilla",
    "chocolate",
    "pepper",
    "earthy",
    "mineral",
    "floral",
    "smoky",
    "toast",
    "honey",
    "peach",
    "pear",
    "tobacco",
    "leather",
    "herbal",
    "blackcurrant",
    "raspberry",
    "strawberry",
    "lemon",
    "lime",
    "grapefruit",
    "tropical",
    "pineapple",
    "melon",
    "licorice",
    "coffee",
    "cedar",
    "mocha",
    "violet",
    "rose petal",
    "graphite",
    "slate",
    "flint",
    "butter",
    "cream",
    "nutty",
    "almond",
    "caramel",
    "game",
    "mushroom",
    "forest floor",
    "eucalyptus",
    "mint",
    "olive",
    "cranberry",
    "fig",
    "dried fruit",
    "jam",
    "candied",
    "zesty",
    "crisp",
]
FOODS = [
    "beef",
    "lamb",
    "pork",
    "poultry",
    "fish",
    "shellfish",
    "pasta",
    "cheese",
    "vegetarian",
    "spicy food",
    "dessert",
    "game meat",
]


def build_dimensions(rng: random.Random) -> dict[str, list[dict[str, Any]]]:
    """Countries, provinces, regions, varieties, wineries, tasters, descriptors, foods."""
    provinces, regions = [], []
    for country, province_names in COUNTRIES.items():
        for province in province_names:
            provinces.append({"name": province, "country": country})
            for i in range(1, 5):
                regions.append({"name": f"{province} Region {i}", "province": province})
    # One name shared by two provinces, like the real data can have.
    regions.append({"name": "Columbia Valley", "province": "Washington"})
    regions.append({"name": "Columbia Valley", "province": "Oregon"})
    wineries = [
        {"name": f"Winery {i:04d}", "country": rng.choice(list(COUNTRIES))} for i in range(2000)
    ]
    pairings = [
        {"variety": v, "food": f, "weight": round(rng.uniform(0.3, 1.0), 2)}
        for v in VARIETIES
        for f in rng.sample(FOODS, 3)
    ]
    return {
        "countries": [{"name": c} for c in COUNTRIES],
        "provinces": provinces,
        "regions": regions,
        "varieties": [{"name": v} for v in VARIETIES],
        "wineries": wineries,
        "tasters": [{"name": f"Taster {i:02d}"} for i in range(20)],
        "descriptors": [{"name": d} for d in DESCRIPTORS],
        "foods": [{"name": f} for f in FOODS],
        "pairings": pairings,
    }


def build_wines(rng: random.Random, dims: dict[str, list[dict[str, Any]]], n: int) -> list[dict]:
    """Wine rows with realistic gaps and a skewed (Zipf-like) descriptor distribution."""
    note_weights = [1 / (rank + 1) for rank in range(len(DESCRIPTORS))]
    regions_by_province: dict[str, list[str]] = {}
    for region in dims["regions"]:
        regions_by_province.setdefault(region["province"], []).append(region["name"])
    wines = []
    for wine_id in range(n):
        province = rng.choice(dims["provinces"])
        points = max(80, min(100, round(rng.gauss(88.5, 3))))
        price = round(rng.lognormvariate(3.0 + (points - 88) * 0.08, 0.5), 2)
        wines.append(
            {
                "id": wine_id,
                "title": f"Wine {wine_id}",
                "points": points,
                "price": None if rng.random() < 0.07 else price,
                "vintage": rng.randint(1995, 2022),
                "description": "Synthetic benchmark wine.",
                "variety": rng.choice(VARIETIES),
                "winery": rng.choice(dims["wineries"]),
                "province": province["name"],
                "country": province["country"],
                "region": (
                    None
                    if rng.random() < 0.15
                    else rng.choice(regions_by_province[province["name"]])
                ),
                "taster": None if rng.random() < 0.2 else rng.choice(dims["tasters"])["name"],
                "notes": list(
                    set(rng.choices(DESCRIPTORS, weights=note_weights, k=rng.randint(2, 6)))
                ),
            }
        )
    return wines


WINE_QUERY = """
UNWIND $rows AS r
CREATE (w:Wine {id: r.id, title: r.title, points: r.points, price: r.price,
                vintage: r.vintage, description: r.description})
WITH w, r
MATCH (v:Variety {name: r.variety})
MATCH (m:Winery {name: r.winery.name, country: r.winery.country})
CREATE (w)-[:OF_VARIETY]->(v), (w)-[:MADE_BY]->(m)
WITH w, r
CALL {
  WITH w, r
  WITH w, r WHERE r.region IS NOT NULL
  MATCH (loc:Region {name: r.region, province: r.province})
  CREATE (w)-[:FROM]->(loc)
}
CALL {
  WITH w, r
  WITH w, r WHERE r.region IS NULL
  MATCH (loc:Province {name: r.province, country: r.country})
  CREATE (w)-[:FROM]->(loc)
}
CALL {
  WITH w, r
  WITH w, r WHERE r.taster IS NOT NULL
  MATCH (t:Taster {name: r.taster})
  CREATE (t)-[:REVIEWED]->(w)
}
CALL {
  WITH w, r
  UNWIND r.notes AS note
  MATCH (d:Descriptor {name: note})
  CREATE (w)-[:HAS_NOTE]->(d)
}
"""

SETUP = [
    "CREATE CONSTRAINT wine_id IF NOT EXISTS FOR (w:Wine) REQUIRE w.id IS UNIQUE",
    "CREATE CONSTRAINT variety_name IF NOT EXISTS FOR (v:Variety) REQUIRE v.name IS UNIQUE",
    "CREATE CONSTRAINT country_name IF NOT EXISTS FOR (c:Country) REQUIRE c.name IS UNIQUE",
    "CREATE CONSTRAINT taster_name IF NOT EXISTS FOR (t:Taster) REQUIRE t.name IS UNIQUE",
    "CREATE CONSTRAINT descriptor_name IF NOT EXISTS FOR (d:Descriptor) REQUIRE d.name IS UNIQUE",
    "CREATE CONSTRAINT food_name IF NOT EXISTS FOR (f:Food) REQUIRE f.name IS UNIQUE",
    "CREATE INDEX region_key IF NOT EXISTS FOR (r:Region) ON (r.name, r.province)",
    "CREATE INDEX province_key IF NOT EXISTS FOR (p:Province) ON (p.name, p.country)",
    "CREATE INDEX winery_key IF NOT EXISTS FOR (m:Winery) ON (m.name, m.country)",
    "CALL db.awaitIndexes(300)",
]

DIMENSION_QUERIES = [
    ("countries", "UNWIND $rows AS r CREATE (:Country {name: r.name})"),
    (
        "provinces",
        """UNWIND $rows AS r MATCH (c:Country {name: r.country})
        CREATE (:Province {name: r.name, country: r.country})-[:IN]->(c)""",
    ),
    (
        "regions",
        """UNWIND $rows AS r MATCH (p:Province {name: r.province})
        CREATE (:Region {name: r.name, province: r.province})-[:IN]->(p)""",
    ),
    ("varieties", "UNWIND $rows AS r CREATE (:Variety {name: r.name})"),
    ("wineries", "UNWIND $rows AS r CREATE (:Winery {name: r.name, country: r.country})"),
    ("tasters", "UNWIND $rows AS r CREATE (:Taster {name: r.name})"),
    ("descriptors", "UNWIND $rows AS r CREATE (:Descriptor {name: r.name})"),
    ("foods", "UNWIND $rows AS r CREATE (:Food {name: r.name})"),
    (
        "pairings",
        """UNWIND $rows AS r MATCH (v:Variety {name: r.variety}), (f:Food {name: r.food})
        MERGE (v)-[p:PAIRS_WITH]->(f) SET p.weight = r.weight""",
    ),
]


def wipe(session: Session) -> None:
    """Delete everything in batches (a single DETACH DELETE on 100k nodes can run out of memory)."""
    session.run("MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS").consume()


def main() -> None:
    """Wipe, create keys, load dimensions, then wines in batches."""
    if "--yes" not in sys.argv:
        answer = input(f"This WIPES every node in {URI}. Type 'yes' to continue: ")
        if answer.strip().lower() != "yes":
            sys.exit("Aborted, nothing was changed.")
    rng = random.Random(SEED)
    dims = build_dimensions(rng)
    wines = build_wines(rng, dims, N_WINES)
    start = time.perf_counter()
    with GraphDatabase.driver(URI, auth=AUTH) as driver, driver.session() as session:
        wipe(session)
        for statement in SETUP:
            session.run(statement).consume()
        for key, query in DIMENSION_QUERIES:
            session.run(query, rows=dims[key]).consume()
        for i in range(0, len(wines), BATCH):
            session.run(WINE_QUERY, rows=wines[i : i + BATCH]).consume()
            print(f"  {min(i + BATCH, len(wines)):,}/{len(wines):,} wines")
    print(f"Loaded {len(wines):,} wines in {time.perf_counter() - start:.0f}s")


if __name__ == "__main__":
    main()
