"""Read-only access to Neo4j.

Every query the API runs goes through ``Neo4jClient.read``: parameterised Cypher,
a read transaction and a server-side timeout. The API never writes to the graph.
"""

import logging
from typing import Any, Protocol

from neo4j import READ_ACCESS, GraphDatabase, ManagedTransaction, unit_of_work
from neo4j.exceptions import DriverError, Neo4jError

log = logging.getLogger(__name__)


class GraphReader(Protocol):
    """Interface used by services, so tests can pass a fake client."""

    def read(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a read-only query and return rows as dictionaries."""

    def ping(self) -> bool:
        """Return True if the database answers."""

    def close(self) -> None:
        """Release resources."""


def _collect(tx: ManagedTransaction, cypher: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Run one query inside a managed transaction and materialise the rows."""
    return [record.data() for record in tx.run(cypher, params)]


class Neo4jClient:
    """Owns a single driver for the whole process (the driver keeps a connection pool)."""

    def __init__(
        self, uri: str, auth: tuple[str, str], database: str = "neo4j", timeout_s: float = 10.0
    ) -> None:
        self._driver = GraphDatabase.driver(uri, auth=auth)
        self._database = database
        self._work = unit_of_work(timeout=timeout_s)(_collect)

    def read(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a parameterised read query with the configured timeout."""
        with self._driver.session(
            database=self._database, default_access_mode=READ_ACCESS
        ) as session:
            return session.execute_read(self._work, cypher, params or {})

    def ping(self) -> bool:
        """Check connectivity without raising."""
        try:
            self._driver.verify_connectivity()
            return True
        except (DriverError, Neo4jError) as exc:
            log.warning(f"Neo4j ping failed: {exc}")
            return False

    def close(self) -> None:
        """Close the driver and its connection pool."""
        self._driver.close()
