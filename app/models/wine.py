"""Wine-related request and response models."""

from enum import Enum

from pydantic import BaseModel, Field

from app.models.common import Page


class WineSort(str, Enum):
    """Allowed sort orders. Mapped to fixed ORDER BY clauses, never interpolated from input."""

    POINTS = "points"
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"


class WineSummary(BaseModel):
    """Compact wine representation used in lists."""

    id: int
    title: str
    price: float | None = None
    points: int
    vintage: int | None = None
    variety: str | None = None
    winery: str | None = None
    country: str | None = None


class WineDetail(WineSummary):
    """Full wine with its review, taster, location chain and tasting notes."""

    description: str | None = None
    taster: str | None = None
    location: list[str] = Field(default_factory=list, description="Region -> Province -> Country")
    notes: list[str] = Field(
        default_factory=list, description="Descriptor nodes linked by HAS_NOTE"
    )


class SimilarWine(WineSummary):
    """A neighbour of the anchor wine with its similarity score (0-1)."""

    score: float


class SimilarPage(Page[SimilarWine]):  # pylint: disable=too-few-public-methods
    """Similar wines plus the method that produced them."""

    wine_id: int
    method: str = Field(description="'gds_node_similarity' or 'cypher_jaccard' (fallback)")


class ValuePick(WineSummary):
    """A wine ranked by points per unit of price."""

    value_score: float


class PredictedTier(BaseModel):
    """Classifier output for one wine, next to the observed value for transparency."""

    wine_id: int
    predicted_tier: str
    probability: float
    actual_points: int
