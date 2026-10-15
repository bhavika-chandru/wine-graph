"""ETL: WineEnthusiast reviews + X-Wines into Neo4j, following docs/contracts.md (`make seed`).

Sources (put them in data/raw/, which is git-ignored):
- winemag-data-130k-v2.csv  (Kaggle, zynicide/wine-reviews): one expert review per row
- XWines_Slim_1K_wines.csv and XWines_Slim_150K_ratings.csv  (GitHub, rogerioxavier/X-Wines)

What the ETL decides (each choice is printed with counts when it runs):
- Wine.id = the CSV row id (int). Exact duplicate reviews (same title and description) are dropped.
- Rows without a country or province are dropped: they cannot be placed in the hierarchy.
- Vintage = first year between 1900 and 2030 found in the title; null if there is none.
- Descriptors = tasting terms from a fixed vocabulary found in the description, with
  synonyms merged ("earthy" -> earth, "cassis" -> blackcurrant).
- X-Wines is joined on grape variety, never wine by wine: food pairings, typical type,
  body and acidity, and the crowd rating per variety.

The database is wiped first (after confirmation), so the load is always clean.
"""

import ast
import csv
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
from neo4j import GraphDatabase, Session

load_dotenv()

URI = os.getenv("NEO4J_ADMIN_URI", "bolt://localhost:7687")
AUTH = (os.getenv("NEO4J_ADMIN_USER", "neo4j"), os.getenv("NEO4J_ADMIN_PASSWORD", "devpassword123"))
RAW = Path(os.getenv("DATA_DIR", "data/raw"))
REVIEWS_CSV = RAW / "winemag-data-130k-v2.csv"
XWINES_CSV = RAW / "XWines_Slim_1K_wines.csv"
RATINGS_CSV = RAW / "XWines_Slim_150K_ratings.csv"
BATCH = 5000
MIN_XWINES = 3  # a variety needs at least this many X-Wines wines to get pairings
MIN_PAIRING = 0.25  # keep a food if at least 25% of the variety's X-Wines wines list it

# canonical descriptor -> other spellings that mean the same thing
DESCRIPTORS: dict[str, list[str]] = {
    "fruit": ["fruity"], "red fruit": [], "black fruit": [], "dark fruit": [], "tropical fruit": [],
    "dried fruit": [], "stone fruit": [], "citrus": [],
    "cherry": [], "black cherry": [], "red cherry": [], "raspberry": [], "strawberry": [],
    "cranberry": [], "red currant": [], "pomegranate": [], "red berry": [],
    "blackberry": [], "blackcurrant": ["black currant", "cassis"], "plum": [], "black plum": [],
    "blueberry": [], "boysenberry": [],
    "lemon": [], "lime": [], "grapefruit": [], "orange": [], "orange peel": [], "lemon zest": [],
    "apple": [], "green apple": [], "pear": [], "peach": [], "apricot": [], "nectarine": [],
    "quince": [], "pineapple": [], "mango": [], "passion fruit": [], "lychee": [], "melon": [],
    "banana": [], "fig": [], "raisin": [], "prune": [],
    "floral": ["flower"], "rose": [], "violet": [], "blossom": [], "honeysuckle": [], "jasmine": [],
    "lavender": [],
    "herb": ["herbal"], "sage": [], "thyme": [], "mint": [], "eucalyptus": [], "grass": ["grassy"],
    "green pepper": ["bell pepper"], "tomato leaf": [], "fennel": [], "anise": [], "licorice": [],
    "spice": ["spicy"], "black pepper": [], "white pepper": [], "pepper": ["peppery"],
    "cinnamon": [], "clove": [], "nutmeg": [], "ginger": [], "baking spice": [],
    "oak": ["oaky"], "toast": ["toasty", "toasted"], "vanilla": [], "smoke": ["smoky"], "cedar": [],
    "wood": ["woody"], "coconut": [], "caramel": [], "butterscotch": [], "butter": ["buttery"],
    "cream": ["creamy"], "brioche": [], "bread": [], "yeast": ["yeasty"],
    "earth": ["earthy"], "mineral": ["minerality"], "slate": [], "flint": ["flinty"], "chalk": ["chalky"],
    "wet stone": [], "graphite": [], "mushroom": [], "forest floor": [], "leather": [],
    "tobacco": [], "tar": [], "truffle": [], "iron": [], "saline": ["salty"], "petrol": [],
    "honey": [], "chocolate": [], "dark chocolate": [], "cocoa": [], "coffee": [], "espresso": [],
    "mocha": [], "almond": [], "hazelnut": [], "walnut": [], "marzipan": [], "toffee": [],
    "molasses": [], "jam": ["jammy"], "meat": ["meaty"], "bacon": [], "game": ["gamey"],
    "olive": [], "black olive": [],
}


def _forms(term: str) -> list[str]:
    """A term and its plurals: cherry -> cherry, cherries; peach -> peach, peaches."""
    forms = [term, term + "s", term + "es"]
    if term.endswith("y"):
        forms.append(term[:-1] + "ies")
    return forms


# every surface form -> canonical descriptor name
_LOOKUP = {form: name for name, variants in DESCRIPTORS.items() for v in [name, *variants] for form in _forms(v)}
# longest first, so "black cherry" wins over "cherry" at the same place
_NOTE_RE = re.compile(r"\b(" + "|".join(re.escape(f) for f in sorted(_LOOKUP, key=len, reverse=True)) + r")\b")
_YEAR_RE = re.compile(r"\b(19\d{2}|20[0-3]\d)\b")


def extract_notes(description: str) -> list[str]:
    """Canonical descriptors found in a review, without duplicates, in order of appearance."""
    found: list[str] = []
    for match in _NOTE_RE.finditer(description.lower()):
        name = _LOOKUP[match.group(1)]
        if name not in found:
            found.append(name)
    return found


def parse_vintage(title: str) -> int | None:
    """First plausible year in the title, e.g. 'Nicosia 2013 Vulkà Bianco (Etna)' -> 2013."""
    match = _YEAR_RE.search(title)
    return int(match.group(1)) if match else None


def parse_list(text: str) -> list[Any]:
    """X-Wines stores lists as Python literals: "['Pork', 'Shellfish']"."""
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return []
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def clean_review(row: dict[str, str]) -> dict[str, Any] | None:
    """One CSV row -> the wine dict the loader expects, or None if it cannot be placed."""
    country, province = _text(row.get("country")), _text(row.get("province"))
    if not country or not province:
        return None
    title, description = _text(row.get("title")) or "", _text(row.get("description")) or ""
    price = _text(row.get("price"))
    return {
        "id": int(row[""]),
        "title": title,
        "description": description,
        "points": int(row["points"]),
        "price": float(price) if price else None,
        "vintage": parse_vintage(title),
        "country": country,
        "province": province,
        "region": _text(row.get("region_1")),
        "variety": _text(row.get("variety")),
        "winery": _text(row.get("winery")),
        "taster": _text(row.get("taster_name")),
        "twitter": _text(row.get("taster_twitter_handle")),
        "notes": extract_notes(description),
    }


def read_reviews(path: Path) -> tuple[list[dict[str, Any]], Counter]:
    """Clean rows, drop unplaceable rows and exact duplicates; return wines and drop counts."""
    wines, dropped, seen = [], Counter(), set()
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            wine = clean_review(row)
            if wine is None:
                dropped["no country or province"] += 1
                continue
            key = (wine["title"], wine["description"])
            if key in seen:
                dropped["duplicate review"] += 1
                continue
            seen.add(key)
            wines.append(wine)
    return wines, dropped


def match_varieties(grapes: Iterable[str], known: dict[str, str]) -> set[str]:
    """X-Wines grape names -> WineEnthusiast variety names ('Syrah/Shiraz' matches either)."""
    matched = set()
    for grape in grapes:
        for part in str(grape).split("/"):
            name = known.get(part.strip().lower())
            if name:
                matched.add(name)
    return matched


def read_ratings(path: Path) -> dict[str, float]:
    """Average crowd rating per X-Wines wine id (empty if the file is missing)."""
    if not path.exists():
        print(f"  {path} not found: varieties get no crowd rating")
        return {}
    totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            total = totals[row["WineID"]]
            total[0] += float(row["Rating"])
            total[1] += 1
    return {wine_id: total[0] / total[1] for wine_id, total in totals.items()}


def _mode(values: list[str]) -> str | None:
    return Counter(values).most_common(1)[0][0] if values else None


def variety_profiles(
    xwines: list[dict[str, str]], known: dict[str, str], ratings: dict[str, float]
) -> list[dict[str, Any]]:
    """Per WineEnthusiast variety: type, body, acidity, crowd rating and food pairings from X-Wines."""
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for wine in xwines:
        for variety in match_varieties(parse_list(wine["Grapes"]), known):
            grouped[variety].append(wine)
    profiles = []
    for variety, wines in sorted(grouped.items()):
        if len(wines) < MIN_XWINES:
            continue
        foods = Counter(food.strip().lower() for wine in wines for food in set(parse_list(wine["Harmonize"])))
        scores = [ratings[w["WineID"]] for w in wines if w["WineID"] in ratings]
        profiles.append(
            {
                "name": variety,
                "xwines": len(wines),
                "type": _mode([w["Type"] for w in wines]),
                "body": _mode([w["Body"] for w in wines]),
                "acidity": _mode([w["Acidity"] for w in wines]),
                "crowd_rating": round(sum(scores) / len(scores), 2) if scores else None,
                "pairings": [
                    {"food": food, "weight": round(count / len(wines), 3)}
                    for food, count in foods.most_common()
                    if count / len(wines) >= MIN_PAIRING
                ],
            }
        )
    return profiles


# ----------------------------------------------------------------------------- loading

# Relationships first, then nodes: DETACH DELETE on a supernode ("fruit" has ~60k HAS_NOTE)
# would delete all its relationships in one transaction and run out of memory.
WIPE = [
    "MATCH ()-[r]->() CALL (r) { DELETE r } IN TRANSACTIONS OF 50000 ROWS",
    "MATCH (n) CALL (n) { DELETE n } IN TRANSACTIONS OF 50000 ROWS",
]

DIMENSIONS = {
    "countries": "UNWIND $rows AS r MERGE (:Country {name: r.name})",
    "provinces": """UNWIND $rows AS r MATCH (c:Country {name: r.country})
        MERGE (p:Province {name: r.name, country: r.country}) MERGE (p)-[:IN]->(c)""",
    "regions": """UNWIND $rows AS r MATCH (p:Province {name: r.province, country: r.country})
        MERGE (g:Region {name: r.name, province: r.province}) MERGE (g)-[:IN]->(p)""",
    "varieties": "UNWIND $rows AS r MERGE (:Variety {name: r.name})",
    "wineries": "UNWIND $rows AS r MERGE (:Winery {name: r.name, country: r.country})",
    "tasters": "UNWIND $rows AS r MERGE (t:Taster {name: r.name}) SET t.twitter = r.twitter",
    "descriptors": "UNWIND $rows AS r MERGE (:Descriptor {name: r.name})",
}

WINES = """
UNWIND $rows AS r
CREATE (:Wine {id: r.id, title: r.title, description: r.description,
               points: r.points, price: r.price, vintage: r.vintage})
"""

LINKS = [
    ("OF_VARIETY", "variety", "MATCH (v:Variety {name: r.variety}) CREATE (w)-[:OF_VARIETY]->(v)"),
    ("MADE_BY", "winery", "MATCH (m:Winery {name: r.winery, country: r.country}) CREATE (w)-[:MADE_BY]->(m)"),
    ("FROM region", "region", "MATCH (g:Region {name: r.region, province: r.province}) CREATE (w)-[:FROM]->(g)"),
    ("REVIEWED", "taster", "MATCH (t:Taster {name: r.taster}) CREATE (t)-[:REVIEWED]->(w)"),
    ("HAS_NOTE", "notes", "UNWIND r.notes AS note MATCH (d:Descriptor {name: note}) CREATE (w)-[:HAS_NOTE]->(d)"),
]

FROM_PROVINCE = """
UNWIND $rows AS r MATCH (w:Wine {id: r.id})
MATCH (p:Province {name: r.province, country: r.country}) CREATE (w)-[:FROM]->(p)
"""

VARIETY_PROFILES = """
UNWIND $rows AS r MATCH (v:Variety {name: r.name})
SET v.wine_type = r.type, v.body = r.body, v.acidity = r.acidity,
    v.crowd_rating = r.crowd_rating, v.xwines_wines = r.xwines
WITH v, r UNWIND r.pairings AS p
MERGE (f:Food {name: p.food})
MERGE (v)-[x:PAIRS_WITH]->(f) SET x.weight = p.weight
"""


def batched(session: Session, query: str, rows: list[dict[str, Any]]) -> None:
    """Run an UNWIND query over rows in batches."""
    for start in range(0, len(rows), BATCH):
        session.run(query, rows=rows[start : start + BATCH]).consume()


def dimensions(wines: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Distinct shared nodes (countries, regions, ...) referenced by the wines."""
    def unique(keys: tuple[str, ...], rename: dict[str, str]) -> list[dict[str, Any]]:
        values = {tuple(w[k] for k in keys) for w in wines if all(w[k] for k in keys)}
        return [{rename.get(k, k): v for k, v in zip(keys, value)} for value in sorted(values)]

    twitter = {w["taster"]: w["twitter"] for w in wines if w["taster"]}
    return {
        "countries": unique(("country",), {"country": "name"}),
        "provinces": unique(("province", "country"), {"province": "name"}),
        "regions": unique(("region", "province", "country"), {"region": "name"}),
        "varieties": unique(("variety",), {"variety": "name"}),
        "wineries": unique(("winery", "country"), {"winery": "name"}),
        "tasters": [{"name": name, "twitter": handle} for name, handle in sorted(twitter.items())],
        "descriptors": [{"name": name} for name in sorted({n for w in wines for n in w["notes"]})],
    }


def load(session: Session, wines: list[dict[str, Any]], profiles: list[dict[str, Any]]) -> None:
    """Shared nodes first, then wines, then one relationship type at a time."""
    for key, rows in dimensions(wines).items():
        batched(session, DIMENSIONS[key], rows)
        print(f"  {key}: {len(rows):,}")
    batched(session, WINES, wines)
    print(f"  wines: {len(wines):,}")
    for label, field, clause in LINKS:
        rows = [w for w in wines if w[field]]
        batched(session, f"UNWIND $rows AS r MATCH (w:Wine {{id: r.id}}) {clause}", rows)
        print(f"  {label}: {len(rows):,} wines")
    no_region = [w for w in wines if not w["region"]]
    batched(session, FROM_PROVINCE, no_region)
    print(f"  FROM province (no region_1): {len(no_region):,} wines")
    batched(session, VARIETY_PROFILES, profiles)
    print(f"  X-Wines profiles: {len(profiles)} varieties, {sum(len(p['pairings']) for p in profiles)} PAIRS_WITH")


def main() -> None:
    """Read, clean, confirm the wipe, load and report."""
    for path in (REVIEWS_CSV, XWINES_CSV):
        if not path.exists():
            sys.exit(f"Missing {path}: see the README section 'Data sources'.")
    start = time.perf_counter()
    wines, dropped = read_reviews(REVIEWS_CSV)
    print(f"Reviews: {len(wines):,} kept; dropped {dict(dropped)}")
    print(f"  no vintage in title: {sum(w['vintage'] is None for w in wines):,}; "
          + f"no price: {sum(w['price'] is None for w in wines):,}; "
          + f"no region_1: {sum(w['region'] is None for w in wines):,}; "
          + f"no descriptor found: {sum(not w['notes'] for w in wines):,}")
    known = {w["variety"].lower(): w["variety"] for w in wines if w["variety"]}
    with XWINES_CSV.open(encoding="utf-8", newline="") as handle:
        xwines = list(csv.DictReader(handle))
    profiles = variety_profiles(xwines, known, read_ratings(RATINGS_CSV))
    print(f"X-Wines: {len(xwines):,} wines matched to {len(profiles)} WineEnthusiast varieties")
    if "--yes" not in sys.argv:
        answer = input(f"This WIPES every node in {URI} and reloads it. Type 'yes' to continue: ")
        if answer.strip().lower() != "yes":
            sys.exit("Aborted, nothing was changed.")
    with GraphDatabase.driver(URI, auth=AUTH) as driver, driver.session() as session:
        for statement in WIPE:
            session.run(statement).consume()
        load(session, wines, profiles)
    print(f"Done in {time.perf_counter() - start:.0f}s. Next: make gds")


if __name__ == "__main__":
    main()
