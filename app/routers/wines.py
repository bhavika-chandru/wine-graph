"""/api/wines endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.deps import get_prediction_service, get_wine_service
from app.models.common import ErrorResponse, Page, PageParams, page_params
from app.models.wine import PredictedTier, SimilarPage, ValuePick, WineDetail, WineSort, WineSummary
from app.services.gds_service import PredictionService
from app.services.wine_service import WineFilters, WineService

router = APIRouter(prefix="/api/wines", tags=["wines"])

NOT_FOUND = {404: {"model": ErrorResponse, "description": "Wine not found"}}
WINE_ID = Path(..., ge=0, description="Wine id (row id from the WineEnthusiast dataset)")


@router.get("", response_model=Page[WineSummary])
def list_wines(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    variety: str | None = Query(None, max_length=100, examples=["Pinot Noir"]),
    country: str | None = Query(None, max_length=100, examples=["France"]),
    min_points: int | None = Query(None, ge=80, le=100),
    min_price: float | None = Query(None, ge=0),
    max_price: float | None = Query(None, ge=0),
    sort: WineSort = WineSort.POINTS,
    page: PageParams = Depends(page_params),
    service: WineService = Depends(get_wine_service),
) -> Page[WineSummary]:
    """List wines with optional filters, sorted and paginated."""
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(status_code=422, detail="min_price must be <= max_price")
    filters = WineFilters(variety, country, min_points, min_price, max_price, sort)
    items, has_more = service.list_wines(filters, page)
    return Page[WineSummary](items=items, skip=page.skip, limit=page.limit, has_more=has_more)


# Declared before /{wine_id} so "value" is not parsed as an id.
@router.get("/value", response_model=Page[ValuePick])
def best_value(
    max_price: float = Query(30, gt=0, le=10_000),
    min_points: int = Query(88, ge=80, le=100),
    variety: str | None = Query(None, max_length=100),
    page: PageParams = Depends(page_params),
    service: WineService = Depends(get_wine_service),
) -> Page[ValuePick]:
    """Best points-per-price picks under a price cap."""
    items, has_more = service.best_value(page, max_price, min_points, variety)
    return Page[ValuePick](items=items, skip=page.skip, limit=page.limit, has_more=has_more)


@router.get("/{wine_id}", response_model=WineDetail, responses=NOT_FOUND)
def get_wine(
    wine_id: int = WINE_ID, service: WineService = Depends(get_wine_service)
) -> WineDetail:
    """One wine with its review, taster, location chain and tasting notes."""
    return WineDetail(**service.get_wine(wine_id))


@router.get("/{wine_id}/similar", response_model=SimilarPage, responses=NOT_FOUND)
def similar_wines(
    wine_id: int = WINE_ID,
    max_price: float | None = Query(None, gt=0, description="Only cheaper alternatives"),
    page: PageParams = Depends(page_params),
    service: WineService = Depends(get_wine_service),
) -> SimilarPage:
    """Similar wines (GDS Node Similarity, with a Cypher Jaccard fallback)."""
    items, has_more, method = service.similar(wine_id, page, max_price)
    return SimilarPage(
        wine_id=wine_id,
        method=method,
        items=items,
        skip=page.skip,
        limit=page.limit,
        has_more=has_more,
    )


@router.get(
    "/{wine_id}/predicted-tier",
    response_model=PredictedTier,
    responses={**NOT_FOUND, 503: {"model": ErrorResponse, "description": "Model not trained"}},
)
def predicted_tier(
    wine_id: int = WINE_ID, service: PredictionService = Depends(get_prediction_service)
) -> PredictedTier:
    """Quality tier predicted from FastRP graph embeddings."""
    return PredictedTier(**service.predicted_tier(wine_id))
