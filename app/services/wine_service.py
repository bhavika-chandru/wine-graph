"""Wine queries: list with filters, detail, similar wines, best value."""

from dataclasses import dataclass
from typing import Any

from app.errors import NotFoundError
from app.models.common import PageParams
from app.models.wine import WineSort
from app.services.base import WINE_SUMMARY_RETURN, BaseService, split_page

ORDER_BY = {
    WineSort.POINTS: "w.points DESC, w.id ASC",
    WineSort.PRICE_ASC: "w.price ASC, w.id ASC",
    WineSort.PRICE_DESC: "w.price DESC, w.id ASC",
}


@dataclass(frozen=True)
class WineFilters:
    """Optional filters for GET /api/wines."""

    variety: str | None = None
    country: str | None = None
    min_points: int | None = None
    min_price: float | None = None
    max_price: float | None = None
    sort: WineSort = WineSort.POINTS


def build_wine_list_query(filters: WineFilters, page: PageParams) -> tuple[str, dict[str, Any]]:
    """Build the list query from fixed clause templates.

    Only the clauses that are needed are added, so the planner can use the
    range indexes on price/points. The naive alternative,
    ``WHERE $p IS NULL OR w.points >= $p``, defeats index use (see benchmarks).
    User input only ever goes into parameters, never into the query text.
    """
    params: dict[str, Any] = {"skip": page.skip, "limit": page.limit + 1}
    lines: list[str] = []

    # Start from the most selective anchor: a Variety or Country node (uniqueness index).
    if filters.variety:
        lines.append("MATCH (v:Variety {name: $variety})<-[:OF_VARIETY]-(w:Wine)")
        params["variety"] = filters.variety
    if filters.country:
        lines.append("MATCH (c:Country {name: $country})<-[:IN*1..2]-()<-[:FROM]-(w:Wine)")
        params["country"] = filters.country
    if not lines:
        lines.append("MATCH (w:Wine)")

    conditions: list[str] = []
    if filters.min_points is not None:
        conditions.append("w.points >= $min_points")
        params["min_points"] = filters.min_points
    if filters.min_price is not None:
        conditions.append("w.price >= $min_price")
        params["min_price"] = filters.min_price
    if filters.max_price is not None:
        conditions.append("w.price <= $max_price")
        params["max_price"] = filters.max_price
    if filters.sort != WineSort.POINTS:
        conditions.append("w.price IS NOT NULL")
    if conditions:
        lines.append("WHERE " + " AND ".join(conditions))

    lines.append(f"WITH DISTINCT w ORDER BY {ORDER_BY[filters.sort]} SKIP $skip LIMIT $limit")
    lines.append(WINE_SUMMARY_RETURN)
    return "\n".join(lines), params


WINE_DETAIL_QUERY = """
MATCH (w:Wine {id: $id})
RETURN w.id AS id, w.title AS title, w.price AS price, w.points AS points,
       w.vintage AS vintage, w.description AS description,
       head([(w)-[:OF_VARIETY]->(v:Variety) | v.name]) AS variety,
       head([(w)-[:MADE_BY]->(m:Winery) | m.name]) AS winery,
       head([(t:Taster)-[:REVIEWED]->(w) | t.name]) AS taster,
       head([p = (w)-[:FROM]->()-[:IN*1..2]->(:Country) | [n IN tail(nodes(p)) | n.name]]) AS location,
       head([(w)-[:FROM]->()-[:IN*1..2]->(c:Country) | c.name]) AS country,
       [(w)-[:HAS_NOTE]->(d:Descriptor) | d.name] AS notes
"""

# Top-k neighbours written by GDS Node Similarity (scripts/gds_setup.py, write mode).
SIMILAR_GDS_QUERY = """
MATCH (anchor:Wine {id: $id})-[s:SIMILAR_TO]->(w:Wine)
WHERE $max_price IS NULL OR w.price <= $max_price
WITH w, s.score AS score
ORDER BY score DESC, w.points DESC
SKIP $skip LIMIT $limit
""" + WINE_SUMMARY_RETURN + ", score"

# Fallback when GDS results are missing: Jaccard on shared descriptors, computed at query time.
# Cost depends on the degree of the anchor's descriptors, not on the size of the graph.
SIMILAR_CYPHER_QUERY = """
MATCH (anchor:Wine {id: $id})-[:HAS_NOTE]->(d:Descriptor)<-[:HAS_NOTE]-(w:Wine)
WHERE w <> anchor AND ($max_price IS NULL OR w.price <= $max_price)
WITH anchor, w, count(d) AS shared
WITH w, toFloat(shared) / (COUNT { (anchor)-[:HAS_NOTE]->() } + COUNT { (w)-[:HAS_NOTE]->() } - shared) AS score
ORDER BY score DESC, w.points DESC
SKIP $skip LIMIT $limit
""" + WINE_SUMMARY_RETURN + ", score"

VALUE_QUERY_TEMPLATE = """
{anchor}
WHERE w.price > 0 AND w.price <= $max_price AND w.points >= $min_points
WITH w, toFloat(w.points) / w.price AS value_score
ORDER BY value_score DESC, w.points DESC
SKIP $skip LIMIT $limit
""" + WINE_SUMMARY_RETURN + ", value_score"


class WineService(BaseService):
    """All wine-centred read queries."""

    def list_wines(
        self, filters: WineFilters, page: PageParams
    ) -> tuple[list[dict[str, Any]], bool]:
        """Filtered, sorted, paginated list of wines."""
        query, params = build_wine_list_query(filters, page)
        return split_page(self.client.read(query, params), page.limit)

    def get_wine(self, wine_id: int) -> dict[str, Any]:
        """One wine with its variety, winery, taster, location chain and notes."""
        rows = self.client.read(WINE_DETAIL_QUERY, {"id": wine_id})
        if not rows:
            raise NotFoundError("Wine", wine_id)
        row = rows[0]
        row["location"] = row.get("location") or []
        return row

    def similar(
        self, wine_id: int, page: PageParams, max_price: float | None = None
    ) -> tuple[list[dict[str, Any]], bool, str]:
        """Similar wines from GDS results if present, else from the Cypher Jaccard fallback."""
        self._require_wine(wine_id)
        params = {"id": wine_id, "max_price": max_price, "skip": page.skip, "limit": page.limit + 1}
        rows = self.client.read(SIMILAR_GDS_QUERY, params)
        method = "gds_node_similarity"
        if not rows and page.skip == 0:
            rows = self.client.read(SIMILAR_CYPHER_QUERY, params)
            method = "cypher_jaccard"
        items, has_more = split_page(rows, page.limit)
        return items, has_more, method

    def best_value(
        self, page: PageParams, max_price: float, min_points: int, variety: str | None = None
    ) -> tuple[list[dict[str, Any]], bool]:
        """Highest points per unit of price under a price cap."""
        params: dict[str, Any] = {
            "max_price": max_price,
            "min_points": min_points,
            "skip": page.skip,
            "limit": page.limit + 1,
        }
        if variety:
            anchor = "MATCH (:Variety {name: $variety})<-[:OF_VARIETY]-(w:Wine)"
            params["variety"] = variety
        else:
            anchor = "MATCH (w:Wine)"
        query = VALUE_QUERY_TEMPLATE.format(anchor=anchor)
        return split_page(self.client.read(query, params), page.limit)
