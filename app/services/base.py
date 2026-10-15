"""Shared helpers for services."""

from typing import Any

from app.database.client import GraphReader

# Projection reused by every endpoint that returns wines.
# Pattern comprehensions fetch one value per wine without multiplying rows,
# unlike a chain of OPTIONAL MATCH clauses (which builds a Cartesian product).
WINE_SUMMARY_RETURN = """
RETURN w.id AS id, w.title AS title, w.price AS price, w.points AS points, w.vintage AS vintage,
       head([(w)-[:OF_VARIETY]->(v_:Variety) | v_.name]) AS variety,
       head([(w)-[:MADE_BY]->(m_:Winery) | m_.name]) AS winery,
       head([(w)-[:FROM]->()-[:IN*1..2]->(c_:Country) | c_.name]) AS country
"""


class BaseService:  # pylint: disable=too-few-public-methods
    """Holds the read-only client shared by all services."""

    def __init__(self, client: GraphReader) -> None:
        self.client = client


def split_page(rows: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], bool]:
    """Queries fetch ``limit + 1`` rows; the extra row only tells us whether a next page exists."""
    return rows[:limit], len(rows) > limit
