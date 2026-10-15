"""Aggregation endpoints: region profiles, taster bias, food pairings."""

from typing import Any

from app.errors import AmbiguousError, NotFoundError
from app.models.common import PageParams
from app.services.base import WINE_SUMMARY_RETURN, BaseService, split_page

# Region is keyed by (name, province) because names like "Napa Valley" are not unique worldwide.
REGION_CANDIDATES_QUERY = (
    "MATCH (r:Region {name: $name}) RETURN r.province AS province ORDER BY province"
)

# Each CALL subquery aggregates independently, so the three aggregations do not multiply rows.
REGION_PROFILE_QUERY = """
MATCH (r:Region {name: $name, province: $province})
CALL {
  WITH r
  MATCH (r)<-[:FROM]-(w:Wine)
  RETURN count(w) AS wine_count, avg(w.points) AS avg_points, avg(w.price) AS avg_price
}
CALL {
  WITH r
  MATCH (r)<-[:FROM]-(:Wine)-[:OF_VARIETY]->(v:Variety)
  WITH v, count(*) AS n ORDER BY n DESC LIMIT 5
  RETURN collect({name: v.name, wines: n}) AS top_varieties
}
CALL {
  WITH r
  MATCH (r)<-[:FROM]-(:Wine)-[:HAS_NOTE]->(d:Descriptor)
  WITH d, count(*) AS n ORDER BY n DESC LIMIT 8
  RETURN collect({name: d.name, wines: n}) AS top_notes
}
RETURN r.name AS region, r.province AS province, wine_count, avg_points, avg_price,
       top_varieties, top_notes
"""

TASTER_EXISTS_QUERY = "MATCH (t:Taster {name: $name}) RETURN count(t) AS n"

# For each variety the taster reviewed, compare their average with every other taster's average.
TASTER_BIAS_QUERY = """
MATCH (t:Taster {name: $name})-[:REVIEWED]->(w:Wine)-[:OF_VARIETY]->(v:Variety)
WITH t, v, avg(w.points) AS taster_avg, count(w) AS reviews
WHERE reviews >= $min_reviews
CALL {
  WITH t, v
  MATCH (o:Taster)-[:REVIEWED]->(w2:Wine)-[:OF_VARIETY]->(v)
  WHERE o <> t
  RETURN avg(w2.points) AS others_avg
}
WITH v, reviews, taster_avg, others_avg
WHERE others_avg IS NOT NULL
RETURN v.name AS variety, reviews, taster_avg, others_avg, taster_avg - others_avg AS deviation
ORDER BY abs(deviation) DESC, reviews DESC
"""

FOOD_EXISTS_QUERY = "MATCH (f:Food {name: $food}) RETURN count(f) AS n"

PAIRING_QUERY_TEMPLATE = """
MATCH (:Food {{name: $food}})<-[p:PAIRS_WITH]-(:Variety)<-[:OF_VARIETY]-(w:Wine)
{where}
WITH w, max(p.weight) AS pairing_weight
ORDER BY pairing_weight DESC, w.points DESC, w.id ASC
SKIP $skip LIMIT $limit
""" + WINE_SUMMARY_RETURN + ", pairing_weight"


class InsightService(BaseService):
    """Region, taster and pairing aggregations."""

    def region_profile(self, name: str, province: str | None) -> dict[str, Any]:
        """Profile of one region; 409 if the name exists in several provinces and none was given."""
        provinces = [
            row["province"] for row in self.client.read(REGION_CANDIDATES_QUERY, {"name": name})
        ]
        if not provinces:
            raise NotFoundError("Region", name)
        if province is None:
            if len(provinces) > 1:
                raise AmbiguousError(
                    f"Region '{name}' exists in several provinces; pass ?province=", provinces
                )
            province = provinces[0]
        elif province not in provinces:
            raise NotFoundError("Region", f"{name} ({province})")
        rows = self.client.read(REGION_PROFILE_QUERY, {"name": name, "province": province})
        return rows[0]

    def taster_bias(self, name: str, min_reviews: int, page: PageParams) -> dict[str, Any]:
        """Per-variety deviation from other tasters, plus a review-weighted overall deviation.

        A taster covers a few hundred varieties at most, so all groups are aggregated in one
        query and paginated in Python; the overall figure needs every group anyway.
        """
        rows = self.client.read(TASTER_EXISTS_QUERY, {"name": name})
        if not rows or rows[0]["n"] == 0:
            raise NotFoundError("Taster", name)
        groups = self.client.read(TASTER_BIAS_QUERY, {"name": name, "min_reviews": min_reviews})
        total = sum(g["reviews"] for g in groups)
        overall = sum(g["deviation"] * g["reviews"] for g in groups) / total if total else None
        window = groups[page.skip : page.skip + page.limit]
        return {
            "taster": name,
            "total_reviews": total,
            "overall_deviation": overall,
            "items": window,
            "skip": page.skip,
            "limit": page.limit,
            "has_more": page.skip + page.limit < len(groups),
        }

    def pairings(
        self, food: str, page: PageParams, max_price: float | None, min_points: int | None
    ) -> tuple[list[dict[str, Any]], bool]:
        """Wines whose variety pairs with a food (X-Wines pairings x WineEnthusiast wines)."""
        food = food.strip().lower()  # contract: Food.name is stored in lowercase
        rows = self.client.read(FOOD_EXISTS_QUERY, {"food": food})
        if not rows or rows[0]["n"] == 0:
            raise NotFoundError("Food", food)
        params: dict[str, Any] = {"food": food, "skip": page.skip, "limit": page.limit + 1}
        conditions = []
        if max_price is not None:
            conditions.append("w.price <= $max_price")
            params["max_price"] = max_price
        if min_points is not None:
            conditions.append("w.points >= $min_points")
            params["min_points"] = min_points
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        query = PAIRING_QUERY_TEMPLATE.format(where=where)
        return split_page(self.client.read(query, params), page.limit)
