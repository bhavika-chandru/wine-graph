"""Flavour styles (Louvain communities of descriptors)."""

from fastapi import APIRouter, Depends, Path

from app.deps import get_style_service
from app.models.common import ErrorResponse, Page, PageParams, page_params
from app.models.insights import StyleDetail, StyleSummary
from app.services.gds_service import StyleService

router = APIRouter(prefix="/api/styles", tags=["styles"])

NOT_READY = {503: {"model": ErrorResponse, "description": "GDS results not computed"}}


@router.get("", response_model=Page[StyleSummary], responses=NOT_READY)
def list_styles(
    page: PageParams = Depends(page_params), service: StyleService = Depends(get_style_service)
) -> Page[StyleSummary]:
    """All flavour families, largest first."""
    items, has_more = service.list_styles(page)
    return Page[StyleSummary](items=items, skip=page.skip, limit=page.limit, has_more=has_more)


@router.get("/{style_id}", response_model=StyleDetail, responses={404: {"model": ErrorResponse}})
def get_style(
    style_id: int = Path(..., ge=0), service: StyleService = Depends(get_style_service)
) -> StyleDetail:
    """Notes in one flavour family and the wines that express it most."""
    return StyleDetail(**service.get_style(style_id))
