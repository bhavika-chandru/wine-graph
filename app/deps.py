"""FastAPI dependencies: database client, services and API-key check."""

import secrets

from fastapi import Depends, Header, HTTPException, Request

from app.config import get_settings
from app.database.client import GraphReader
from app.services.gds_service import PredictionService, StyleService
from app.services.insight_service import InsightService
from app.services.wine_service import WineService


def get_client(request: Request) -> GraphReader:
    """The single client created at startup (see app.main.lifespan)."""
    return request.app.state.client


def get_wine_service(client: GraphReader = Depends(get_client)) -> WineService:
    """WineService bound to the shared client."""
    return WineService(client)


def get_insight_service(client: GraphReader = Depends(get_client)) -> InsightService:
    """InsightService bound to the shared client."""
    return InsightService(client)


def get_style_service(client: GraphReader = Depends(get_client)) -> StyleService:
    """StyleService bound to the shared client."""
    return StyleService(client)


def get_prediction_service(
    request: Request, client: GraphReader = Depends(get_client)
) -> PredictionService:
    """PredictionService using the process-wide classifier."""
    return PredictionService(client, request.app.state.predictor)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Protect sensitive endpoints when API_KEY is set; constant-time comparison."""
    expected = get_settings().api_key
    if expected and not (x_api_key and secrets.compare_digest(x_api_key, expected)):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")
