"""POST /api/ask: natural language to Cypher."""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.agent.pipeline import answer_question
from app.database.client import GraphReader
from app.deps import get_client, require_api_key
from app.models.ask import AskRequest, AskResponse
from app.models.common import ErrorResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ask"])


@router.post(
    "/ask",
    response_model=AskResponse,
    dependencies=[Depends(require_api_key)],
    responses={
        400: {"model": AskResponse, "description": "Rejected by a guardrail"},
        401: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def ask(body: AskRequest, client: GraphReader = Depends(get_client)) -> JSONResponse:
    """Answer a question in plain English.

    A query rejected by a guardrail returns the full response body with HTTP 400.
    """
    result = answer_question(body.question, client)
    log.info(f"ask status={result.status} question={body.question!r} cypher={result.cypher!r}")
    status_code = 400 if result.status == "rejected" else 200
    return JSONResponse(status_code=status_code, content=result.model_dump())
