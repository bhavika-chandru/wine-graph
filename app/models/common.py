"""Shared response shapes: pagination and errors."""

from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, Field

T = TypeVar("T")

MAX_LIMIT = 100
DEFAULT_LIMIT = 20
MAX_SKIP = 10_000  # deep SKIP gets slow; see the keyset-pagination note in the README


class PageParams(BaseModel):
    """Validated skip/limit pair."""

    skip: int
    limit: int


def page_params(
    skip: int = Query(0, ge=0, le=MAX_SKIP, description="Number of items to skip"),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="Page size"),
) -> PageParams:
    """FastAPI dependency so every list endpoint validates pagination the same way."""
    return PageParams(skip=skip, limit=limit)


class Page(BaseModel, Generic[T]):
    """One page of results.

    ``has_more`` is computed by fetching ``limit + 1`` rows, which avoids a second
    COUNT query on every request.
    """

    items: list[T]
    skip: int
    limit: int
    has_more: bool


class ErrorResponse(BaseModel):
    """Body returned for every handled error."""

    error: str
    candidates: list[str] = Field(default_factory=list)
