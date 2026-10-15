"""Region profiles, taster bias and food pairings."""

from fastapi import APIRouter, Depends, Path, Query

from app.deps import get_insight_service
from app.models.common import ErrorResponse, Page, PageParams, page_params
from app.models.insights import PairingItem, RegionProfile, TasterBias
from app.services.insight_service import InsightService

router = APIRouter(prefix="/api", tags=["insights"])


@router.get(
    "/regions/{name}/profile",
    response_model=RegionProfile,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse, "description": "Name exists in several provinces"},
    },
)
def region_profile(
    name: str = Path(..., max_length=100, examples=["Napa Valley"]),
    province: str | None = Query(None, max_length=100),
    service: InsightService = Depends(get_insight_service),
) -> RegionProfile:
    """Dominant varieties, average price and points, and typical notes of a region."""
    return RegionProfile(**service.region_profile(name, province))


@router.get(
    "/tasters/{name}/bias", response_model=TasterBias, responses={404: {"model": ErrorResponse}}
)
def taster_bias(
    name: str = Path(..., max_length=100, examples=["Roger Voss"]),
    min_reviews: int = Query(5, ge=1, le=1000),
    page: PageParams = Depends(page_params),
    service: InsightService = Depends(get_insight_service),
) -> TasterBias:
    """How much a taster scores above or below other tasters on the same varieties."""
    return TasterBias(**service.taster_bias(name, min_reviews, page))


@router.get(
    "/pairings", response_model=Page[PairingItem], responses={404: {"model": ErrorResponse}}
)
def pairings(
    food: str = Query(..., min_length=2, max_length=50, examples=["lamb"]),
    max_price: float | None = Query(None, gt=0),
    min_points: int | None = Query(None, ge=80, le=100),
    page: PageParams = Depends(page_params),
    service: InsightService = Depends(get_insight_service),
) -> Page[PairingItem]:
    """Wines for a food: Food <- PAIRS_WITH - Variety <- OF_VARIETY - Wine."""
    items, has_more = service.pairings(food, page, max_price, min_points)
    return Page[PairingItem](items=items, skip=page.skip, limit=page.limit, has_more=has_more)
