"""Uniqueness constraints and indexes from docs/contracts.md (first step of `make seed`).

Safe to re-run: every statement uses IF NOT EXISTS. The names match the ones used by
scripts/generate_bench_data.py, so running one after the other never creates duplicates.
"""

import os

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

URI = os.getenv("NEO4J_ADMIN_URI", "bolt://localhost:7687")
AUTH = (os.getenv("NEO4J_ADMIN_USER", "neo4j"), os.getenv("NEO4J_ADMIN_PASSWORD", "devpassword123"))

STATEMENTS = [
    # One node per key; MERGE in the ETL relies on these.
    "CREATE CONSTRAINT wine_id IF NOT EXISTS FOR (w:Wine) REQUIRE w.id IS UNIQUE",
    "CREATE CONSTRAINT variety_name IF NOT EXISTS FOR (v:Variety) REQUIRE v.name IS UNIQUE",
    "CREATE CONSTRAINT country_name IF NOT EXISTS FOR (c:Country) REQUIRE c.name IS UNIQUE",
    "CREATE CONSTRAINT taster_name IF NOT EXISTS FOR (t:Taster) REQUIRE t.name IS UNIQUE",
    "CREATE CONSTRAINT descriptor_name IF NOT EXISTS FOR (d:Descriptor) REQUIRE d.name IS UNIQUE",
    "CREATE CONSTRAINT food_name IF NOT EXISTS FOR (f:Food) REQUIRE f.name IS UNIQUE",
    # Composite keys: the same region / province / winery name exists in several places.
    "CREATE INDEX region_key IF NOT EXISTS FOR (r:Region) ON (r.name, r.province)",
    "CREATE INDEX province_key IF NOT EXISTS FOR (p:Province) ON (p.name, p.country)",
    "CREATE INDEX winery_key IF NOT EXISTS FOR (m:Winery) ON (m.name, m.country)",
    # Range indexes the API filters and sorts on (names are fixed: `make bench` drops and recreates them).
    "CREATE RANGE INDEX wine_points IF NOT EXISTS FOR (w:Wine) ON (w.points)",
    "CREATE RANGE INDEX wine_price IF NOT EXISTS FOR (w:Wine) ON (w.price)",
    "CREATE RANGE INDEX descriptor_style IF NOT EXISTS FOR (d:Descriptor) ON (d.style_id)",
    "CALL db.awaitIndexes(300)",
]


def main() -> None:
    """Create everything, then list what exists."""
    with GraphDatabase.driver(URI, auth=AUTH) as driver, driver.session() as session:
        for statement in STATEMENTS:
            session.run(statement).consume()
        names = [record["name"] for record in session.run("SHOW INDEXES YIELD name RETURN name ORDER BY name")]
    print(f"Constraints and indexes ready on {URI}: {', '.join(names)}")


if __name__ == "__main__":
    main()
