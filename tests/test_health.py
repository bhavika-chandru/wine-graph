"""Container health tests against the running compose stack.

Skipped by default; run with `make test-integration` (sets RUN_INTEGRATION=1)
after `make docker-run`.
"""

import os
import urllib.request

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="needs the compose stack"),
]

BASE_URL = os.getenv("API_BASE_URL", "http://localhost")  # through Nginx


def _get(path: str) -> tuple[int, str]:
    with urllib.request.urlopen(BASE_URL + path, timeout=10) as response:  # nosec - local URL
        return response.status, response.read().decode()


def test_nginx_is_up():
    """Nginx answers on its own health route."""
    status, _ = _get("/nginx-health")
    assert status == 200


def test_api_and_neo4j_are_up_through_proxy():
    """The API is reachable through Nginx and reports Neo4j as up."""
    status, body = _get("/health")
    assert status == 200
    assert '"neo4j":"up"' in body.replace(" ", "")


def test_neo4j_answers_a_query():
    """The read-only user can run a query directly against Neo4j."""
    neo4j = pytest.importorskip("neo4j")
    uri = os.getenv("NEO4J_TEST_URI", "bolt://localhost:7687")
    auth = (os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", ""))
    with neo4j.GraphDatabase.driver(uri, auth=auth) as driver:
        records, _, _ = driver.execute_query("RETURN 1 AS ok")
    assert records[0]["ok"] == 1
