"""Domain errors and their mapping to HTTP responses.

Services raise these exceptions; the handlers below turn them into clean JSON errors,
so routers never build error responses by hand and no stack trace reaches the client.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from neo4j.exceptions import ClientError, DriverError, Neo4jError, ServiceUnavailable

log = logging.getLogger(__name__)


class NotFoundError(Exception):
    """A requested node does not exist (HTTP 404)."""

    def __init__(self, resource: str, key: object) -> None:
        super().__init__(f"{resource} '{key}' not found")


class AmbiguousError(Exception):
    """A name matches several nodes and the caller must disambiguate (HTTP 409)."""

    def __init__(self, message: str, candidates: list[str]) -> None:
        super().__init__(message)
        self.candidates = candidates


class ServiceNotReadyError(Exception):
    """A feature depends on something not computed yet, e.g. GDS results (HTTP 503)."""


class QueryRejectedError(Exception):
    """A natural-language query was rejected by the guardrails (HTTP 400)."""


def _error(status: int, message: str, **extra: object) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message, **extra})


def register_error_handlers(app: FastAPI) -> None:
    """Attach one handler per error type to the application."""

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return _error(404, str(exc))

    @app.exception_handler(AmbiguousError)
    async def ambiguous(_: Request, exc: AmbiguousError) -> JSONResponse:
        return _error(409, str(exc), candidates=exc.candidates)

    @app.exception_handler(ServiceNotReadyError)
    async def not_ready(_: Request, exc: ServiceNotReadyError) -> JSONResponse:
        return _error(503, str(exc))

    @app.exception_handler(QueryRejectedError)
    async def rejected(_: Request, exc: QueryRejectedError) -> JSONResponse:
        return _error(400, str(exc))

    @app.exception_handler(ServiceUnavailable)
    async def db_down(_: Request, exc: ServiceUnavailable) -> JSONResponse:
        log.error(f"Neo4j unavailable: {exc}")
        return _error(503, "Database unavailable")

    @app.exception_handler(ClientError)
    async def db_client_error(_: Request, exc: ClientError) -> JSONResponse:
        # e.g. Neo.ClientError.Transaction.TransactionTimedOut
        if exc.code and "TimedOut" in exc.code:
            return _error(504, "Query timed out")
        log.error(f"Cypher client error: {exc}")
        return _error(500, "Query failed")

    @app.exception_handler(Neo4jError)
    async def db_error(_: Request, exc: Neo4jError) -> JSONResponse:
        log.error(f"Neo4j error: {exc}")
        return _error(500, "Database error")

    @app.exception_handler(DriverError)
    async def driver_error(_: Request, exc: DriverError) -> JSONResponse:
        log.error(f"Driver error: {exc}")
        return _error(503, "Database unavailable")
