"""Models for region profiles, taster bias, food pairings and flavour styles."""

from pydantic import BaseModel

from app.models.wine import WineSummary


class NameCount(BaseModel):
    """A named item with the number of wines it applies to."""

    name: str
    wines: int


class RegionProfile(BaseModel):
    """Aggregated view of one region."""

    region: str
    province: str
    wine_count: int
    avg_points: float | None
    avg_price: float | None
    top_varieties: list[NameCount]
    top_notes: list[NameCount]


class BiasItem(BaseModel):
    """Taster average vs other tasters' average on the same variety."""

    variety: str
    reviews: int
    taster_avg: float
    others_avg: float
    deviation: float


class TasterBias(BaseModel):
    """Overall deviation (review-weighted) plus a page of per-variety deviations."""

    taster: str
    total_reviews: int
    overall_deviation: float | None
    items: list[BiasItem]
    skip: int
    limit: int
    has_more: bool


class PairingItem(WineSummary):
    """A wine whose variety pairs with the requested food."""

    pairing_weight: float | None = None


class StyleSummary(BaseModel):
    """One Louvain community of descriptors."""

    id: int
    size: int
    top_notes: list[str]


class StyleDetail(BaseModel):
    """All notes of a style and the wines that express it most."""

    id: int
    notes: list[str]
    example_wines: list[WineSummary]
