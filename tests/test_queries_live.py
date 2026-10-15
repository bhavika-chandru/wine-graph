"""Every endpoint's Cypher against a real Neo4j loaded with tests/fixtures/seed.cypher.

Skipped unless RUN_DB_TESTS=1 (run with `make test-db`). The seed WIPES the target
database, so point NEO4J_TEST_URI at a dev instance only.
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from app.database.client import Neo4jClient
from app.main import create_app

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="needs a dev Neo4j (make test-db)"),
]

URI = os.getenv("NEO4J_TEST_URI", "bolt://localhost:7687")
AUTH = (os.getenv("NEO4J_TEST_USER", "neo4j"), os.getenv("NEO4J_TEST_PASSWORD", "devpassword123"))
SEED = Path(__file__).parent / "fixtures" / "seed.cypher"


def _statements(text: str) -> list[str]:
    """Split the seed file on ';' and drop comment-only chunks."""
    chunks = []
    for chunk in text.split(";"):
        code = "\n".join(line for line in chunk.splitlines() if not line.strip().startswith("//"))
        if code.strip():
            chunks.append(code)
    return chunks


@pytest.fixture(name="neo", scope="module")
def neo_fixture():
    """Load the seed once per module and yield a real read-only client."""
    with GraphDatabase.driver(URI, auth=AUTH) as driver:
        for statement in _statements(SEED.read_text(encoding="utf-8")):
            driver.execute_query(statement)
    client = Neo4jClient(URI, AUTH)
    yield client
    client.close()


@pytest.fixture(name="live", scope="module")
def live_fixture(neo):
    """TestClient wired to the real database."""
    return TestClient(create_app(client=neo))


def ids(response) -> list[int]:
    """Wine ids of a list response, in order."""
    return [item["id"] for item in response.json()["items"]]


def test_health(live):
    """Neo4j answers."""
    assert live.get("/health").json()["neo4j"] == "up"


def test_writes_are_refused_in_read_transactions(neo):
    """The API client runs read transactions, so a CREATE must fail (contracts.md section 5)."""
    with pytest.raises(Neo4jError):
        neo.read("CREATE (:ShouldNotExist) RETURN 1")


def test_list_by_variety(live):
    """Variety anchor + sort by points."""
    assert ids(live.get("/api/wines", params={"variety": "Riesling"})) == [7, 8]


def test_list_by_country_includes_wines_without_region(live):
    """Wine 6 is linked straight to its Province and must still be found."""
    assert ids(live.get("/api/wines", params={"country": "France"})) == [1, 9, 6, 2]


def test_list_range_filters(live):
    """Range filters; a wine with no price never matches a price filter."""
    assert ids(live.get("/api/wines", params={"min_points": 92, "max_price": 50})) == [6]


def test_list_price_sort_and_pagination(live):
    """Cheapest first, limit + 1 gives has_more."""
    response = live.get("/api/wines", params={"sort": "price_asc", "limit": 3})
    assert ids(response) == [8, 10, 7]
    assert response.json()["has_more"] is True


def test_wine_detail(live):
    """Pattern comprehensions return the full context of one wine."""
    body = live.get("/api/wines/1").json()
    assert body["variety"] == "Pinot Noir"
    assert body["winery"] == "Domaine A"
    assert body["taster"] == "Roger Voss"
    assert body["country"] == "France"
    assert body["location"] == ["Cote de Nuits", "Burgundy", "France"]
    assert sorted(body["notes"]) == ["cherry", "earthy", "mineral"]


def test_wine_detail_without_region(live):
    """Ragged hierarchy: location chain starts at the Province."""
    assert live.get("/api/wines/6").json()["location"] == ["Bordeaux", "France"]


def test_wine_not_found(live):
    """Unknown id."""
    assert live.get("/api/wines/999").status_code == 404


def test_similar_from_gds(live):
    """Wine 1 has SIMILAR_TO relationships."""
    body = live.get("/api/wines/1/similar").json()
    assert body["method"] == "gds_node_similarity"
    assert [w["id"] for w in body["items"]] == [2, 9]


def test_similar_gds_filtered_stays_gds(live):
    """Filtering GDS neighbours never switches to the fallback."""
    body = live.get("/api/wines/1/similar", params={"max_price": 50}).json()
    assert body["method"] == "gds_node_similarity"
    assert [w["id"] for w in body["items"]] == [2]


def test_similar_cypher_fallback(live):
    """Wine 3 has no SIMILAR_TO: Jaccard on shared descriptors, ties broken by points."""
    body = live.get("/api/wines/3/similar").json()
    assert body["method"] == "cypher_jaccard"
    assert [w["id"] for w in body["items"]] == [10, 2, 5, 4, 1]
    assert body["items"][0]["score"] == pytest.approx(0.5)


def test_best_value(live):
    """Points per price among wines with at least min_points."""
    response = live.get("/api/wines/value", params={"max_price": 30, "min_points": 88})
    assert ids(response) == [7, 3]


def test_region_profile(live):
    """Aggregations in CALL subqueries."""
    body = live.get("/api/regions/Napa Valley/profile").json()
    assert body["wine_count"] == 3
    assert body["avg_points"] == pytest.approx(89.67, abs=0.01)
    assert body["top_varieties"][0] == {"name": "Cabernet Sauvignon", "wines": 3}
    assert body["top_notes"][0] == {"name": "oak", "wines": 3}


def test_region_ambiguous_then_disambiguated(live):
    """Same region name in two provinces: 409, then ?province= resolves it."""
    response = live.get("/api/regions/Twin Valley/profile")
    assert response.status_code == 409
    assert response.json()["candidates"] == ["California", "Washington"]
    body = live.get("/api/regions/Twin Valley/profile", params={"province": "Washington"}).json()
    assert body["wine_count"] == 2


def test_taster_bias(live):
    """Roger Voss scores Pinot Noir 4.33 above the other tasters."""
    body = live.get("/api/tasters/Roger Voss/bias", params={"min_reviews": 1}).json()
    assert body["total_reviews"] == 4
    assert body["items"][0]["variety"] == "Pinot Noir"
    assert body["items"][0]["deviation"] == pytest.approx(4.33, abs=0.01)
    assert body["overall_deviation"] == pytest.approx(3.83, abs=0.01)


def test_pairings(live):
    """Food <- Variety <- Wine, ordered by pairing weight then points."""
    response = live.get("/api/pairings", params={"food": "Beef", "max_price": 50})
    assert ids(response) == [6, 5, 10, 2, 3]


def test_styles(live):
    """Louvain communities read from Descriptor.style_id."""
    body = live.get("/api/styles").json()
    assert [s["id"] for s in body["items"]] == [0, 1, 2]


def test_style_detail(live):
    """Notes ordered by usage, wines ordered by how many notes of the style they have."""
    body = live.get("/api/styles/1").json()
    assert body["notes"] == ["oak", "blackcurrant", "vanilla"]
    assert body["example_wines"][0]["id"] == 4


def test_predicted_tier_without_embeddings(live):
    """No fastrp property yet: 503, not a crash."""
    assert live.get("/api/wines/1/predicted-tier").status_code == 503
