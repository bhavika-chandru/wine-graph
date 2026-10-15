"""Entry point of the NL->Cypher agent.

PLACEHOLDER owned by Person 3. Keep this exact signature: the /api/ask router calls it.
Expected behaviour (see project brief, section 7): scope check, entity grounding,
generation, static validation, EXPLAIN check with one retry, read-only execution,
summary written only from the returned rows.
"""

from app.database.client import GraphReader
from app.errors import ServiceNotReadyError
from app.models.ask import AskResponse


def answer_question(question: str, client: GraphReader) -> AskResponse:
    """Answer a natural-language question. Must return status 'rejected' when a guardrail fires."""
    raise ServiceNotReadyError(f"NL->Cypher agent not implemented yet (question: {question!r})")


__all__ = ["answer_question", "GraphReader"]
