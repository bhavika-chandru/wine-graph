"""Liveness and Neo4j connectivity, used by Docker healthchecks and tests."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.database.client import GraphReader
from app.deps import get_client

router = APIRouter(tags=["health"])


@router.get("/health")
def health(client: GraphReader = Depends(get_client)) -> JSONResponse:
    """200 if the API and Neo4j are up, 503 if Neo4j does not answer."""
    if client.ping():
        return JSONResponse(status_code=200, content={"status": "ok", "neo4j": "up"})
    return JSONResponse(status_code=503, content={"status": "degraded", "neo4j": "down"})
