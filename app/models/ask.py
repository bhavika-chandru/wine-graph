"""Contract between the API and the NL->Cypher agent (owned by Person 3)."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """A natural-language question about the wine graph."""

    question: str = Field(min_length=3, max_length=500)


class Provenance(BaseModel):
    """Separates facts read from the database from text written by the LLM."""

    rows: str = "verified (database)"
    summary: str = "AI-generated from rows only"


class AskResponse(BaseModel):
    """Structured answer returned by ``app.agent.pipeline.answer_question``."""

    question: str
    cypher: str | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)
    summary: str | None = None
    provenance: Provenance = Field(default_factory=Provenance)
    status: Literal["ok", "empty", "rejected", "error"]
    reason: str | None = Field(default=None, description="Why a query was rejected, if it was")
