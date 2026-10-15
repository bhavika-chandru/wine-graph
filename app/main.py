"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.database.client import GraphReader, Neo4jClient
from app.errors import register_error_handlers
from app.routers import ask, health, insights, styles, wines
from app.services.gds_service import TierPredictor


def create_app(
    client: GraphReader | None = None, predictor: TierPredictor | None = None
) -> FastAPI:
    """Build the app.

    Tests inject a fake client; in production one Neo4j driver is created at startup.
    """
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if application.state.client is None:
            application.state.client = Neo4jClient(
                settings.neo4j_uri,
                (settings.neo4j_user, settings.neo4j_password),
                settings.neo4j_database,
                settings.query_timeout_s,
            )
        yield
        application.state.client.close()

    application = FastAPI(
        title="Wine Explorer API",
        description="Graph-backed sommelier: Neo4j + GDS + NL-to-Cypher.",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.client = client
    application.state.predictor = predictor or TierPredictor(settings.model_path)

    register_error_handlers(application)
    for module in (health, wines, insights, styles, ask):
        application.include_router(module.router)
    return application


app = create_app()
