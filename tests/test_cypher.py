"""Unit tests for query building and error mapping (no database needed)."""

import pytest
from fastapi.testclient import TestClient
from neo4j.exceptions import ClientError, ServiceUnavailable

from app.errors import NotFoundError, ServiceNotReadyError
from app.main import create_app
from app.models.common import PageParams
from app.models.wine import WineSort
from app.services.gds_service import TierPredictor
from app.services.wine_service import WineFilters, build_wine_list_query
from tests.conftest import FakeClient

PAGE = PageParams(skip=0, limit=20)


def test_no_filters_scans_wines():
    """No filters scans wines."""
    query, params = build_wine_list_query(WineFilters(), PAGE)
    assert query.startswith("MATCH (w:Wine)")
    assert "WHERE" not in query.split("RETURN", maxsplit=1)[0]
    assert params == {"skip": 0, "limit": 21}


def test_variety_filter_anchors_on_variety_node():
    """Variety filter anchors on variety node."""
    query, params = build_wine_list_query(WineFilters(variety="Riesling"), PAGE)
    assert query.startswith("MATCH (v:Variety {name: $variety})")
    assert params["variety"] == "Riesling"


def test_only_needed_conditions_are_added():
    """Only needed conditions are added."""
    query, params = build_wine_list_query(WineFilters(min_points=92, max_price=40), PAGE)
    assert "w.points >= $min_points AND w.price <= $max_price" in query
    assert "IS NULL OR" not in query
    assert "min_price" not in params


def test_user_input_never_enters_query_text():
    """User input never enters query text."""
    evil = "x'}) DETACH DELETE w //"
    query, params = build_wine_list_query(WineFilters(variety=evil, country=evil), PAGE)
    assert evil not in query
    assert params["variety"] == evil


def test_price_sort_excludes_missing_prices():
    """Price sort excludes missing prices."""
    query, _ = build_wine_list_query(WineFilters(sort=WineSort.PRICE_ASC), PAGE)
    assert "w.price IS NOT NULL" in query
    assert "ORDER BY w.price ASC" in query


def test_country_filter_handles_ragged_hierarchy():
    """Country filter handles ragged hierarchy."""
    query, _ = build_wine_list_query(WineFilters(country="France"), PAGE)
    assert "<-[:IN*1..2]-()<-[:FROM]-(w:Wine)" in query


class FailingClient(FakeClient):
    """Raises a chosen driver exception on every read."""

    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self.exc = exc

    def read(self, cypher, params=None):
        raise self.exc


@pytest.mark.parametrize(
    "exc, status",
    [
        (ServiceUnavailable("down"), 503),
        (
            ClientError._hydrate_neo4j(
                code="Neo.ClientError.Transaction.TransactionTimedOut", message="t"
            ),
            504,
        ),
        (
            ClientError._hydrate_neo4j(code="Neo.ClientError.Statement.SyntaxError", message="s"),
            500,
        ),
    ],
)
def test_database_errors_map_to_http(exc, status):
    """Database errors map to http."""
    api = TestClient(create_app(client=FailingClient(exc)))
    response = api.get("/api/wines")
    assert response.status_code == status
    assert "error" in response.json()


def test_not_found_message():
    """Not found message."""
    assert str(NotFoundError("Wine", 5)) == "Wine '5' not found"


def test_predictor_missing_model(tmp_path):
    """Predictor missing model."""
    predictor = TierPredictor(str(tmp_path / "missing.joblib"))
    with pytest.raises(ServiceNotReadyError):
        predictor.predict([0.1])


def test_predictor_loads_joblib(tmp_path):
    """Predictor loads joblib."""
    joblib = pytest.importorskip("joblib")
    linear = pytest.importorskip("sklearn.linear_model")
    model = linear.LogisticRegression().fit(
        [[0.0], [1.0], [0.1], [0.9]], ["standard", "high", "standard", "high"]
    )
    path = tmp_path / "model.joblib"
    joblib.dump(model, path)
    label, probability = TierPredictor(str(path)).predict([0.95])
    assert label == "high" and 0.5 < probability <= 1.0
