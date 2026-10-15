"""Shared fixtures: a fake Neo4j client so API tests run without a database."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


class FakeClient:
    """Returns queued result sets in order and records every query it receives."""

    def __init__(self) -> None:
        self.responses: list[list[dict[str, Any]]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.is_up = True

    def queue(self, *result_sets: list[dict[str, Any]]) -> None:
        """Queue the rows returned by the next read() calls."""
        self.responses.extend(result_sets)

    def read(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Pop the next queued result set (empty list if none)."""
        self.calls.append((cypher, params or {}))
        return self.responses.pop(0) if self.responses else []

    def ping(self) -> bool:
        """Simulated connectivity."""
        return self.is_up

    def close(self) -> None:
        """Nothing to release."""


class FakePredictor:
    """Stands in for the trained scikit-learn model."""

    def predict(self, embedding: list[float]) -> tuple[str, float]:
        """Always predicts the high tier."""
        assert embedding
        return "high", 0.87


def wine_row(wine_id: int = 1, **overrides: Any) -> dict[str, Any]:
    """A row shaped like WINE_SUMMARY_RETURN."""
    row = {
        "id": wine_id,
        "title": f"Wine {wine_id}",
        "price": 25.0,
        "points": 90,
        "vintage": 2015,
        "variety": "Pinot Noir",
        "winery": "Domaine X",
        "country": "France",
    }
    row.update(overrides)
    return row


@pytest.fixture(name="fake")
def fake_fixture() -> FakeClient:
    """A fresh fake client per test."""
    return FakeClient()


@pytest.fixture(name="api")
def api_fixture(fake: FakeClient) -> TestClient:
    """TestClient wired to the fake client and predictor."""
    return TestClient(create_app(client=fake, predictor=FakePredictor()))
