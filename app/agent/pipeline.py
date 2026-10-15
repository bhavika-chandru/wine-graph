"""Entry point of the NL->Cypher agent: called by POST /api/ask.

Steps (README, "NL->Cypher agent"):
1. scope check: a question that asks to change data is rejected before any model call
2. entity grounding: exact names from the graph that appear in the question go into the prompt
3. generation: schema + rules + examples, temperature 0 (app/agent/prompts.py)
4. static validation: write clauses rejected, unknown names sent back once, paths and LIMIT capped
5. EXPLAIN check: the database parses and plans the query; one retry with the error message
6. execution in a read-only transaction with a timeout
7. summary written by the model from the returned rows only (marked as AI-generated)
"""

import json
import logging
import re
from typing import Any

from neo4j.exceptions import ClientError, Neo4jError

from app.agent import guardrails, prompts
from app.agent.llm import LLM, make_llm
from app.database.client import GraphReader
from app.models.ask import AskResponse

log = logging.getLogger(__name__)

VOCABULARY_QUERY = """
MATCH (n) WHERE n:Country OR n:Province OR n:Region OR n:Variety OR n:Taster OR n:Descriptor OR n:Food
RETURN labels(n)[0] AS label, n.name AS name
"""
MAX_NAMES = 12
SUMMARY_ROWS = 20
SHORT_NAME = 3  # names this short ('US', 'oak') must match exactly, so "tell us" is not the US
_vocabulary: dict[int, list[tuple[str, str, re.Pattern]]] = {}


def _pattern(name: str) -> re.Pattern:
    text = name if len(name) <= SHORT_NAME else name.lower()
    flags = 0 if len(name) <= SHORT_NAME else re.IGNORECASE
    return re.compile(r"(?<!\w)" + re.escape(text) + r"(?!\w)", flags)


def _load_vocabulary(client: GraphReader) -> list[tuple[str, str, re.Pattern]]:
    """Names of the small node sets, longest first, cached once per database client."""
    key = id(client)
    if key not in _vocabulary:
        rows = client.read(VOCABULARY_QUERY, {})
        names = sorted({(r["label"], r["name"]) for r in rows if r["name"]}, key=lambda x: -len(x[1]))
        _vocabulary[key] = [(label, name, _pattern(name)) for label, name in names]
    return _vocabulary[key]


def ground(question: str, client: GraphReader) -> list[str]:
    """Exact graph names mentioned in the question, e.g. ["Variety 'Riesling'", "Country 'Germany'"]."""
    found = [f"{label} '{name}'" for label, name, pattern in _load_vocabulary(client) if pattern.search(question)]
    return found[:MAX_NAMES]


def _explain_error(client: GraphReader, cypher: str) -> str | None:
    """None if the database can plan the query, else its error message."""
    try:
        client.read(f"EXPLAIN {cypher}", {})
    except Neo4jError as exc:
        return (exc.message or str(exc))[:300]
    return None


def _generate(question: str, client: GraphReader, llm: LLM) -> tuple[str | None, str | None]:
    """(cypher, None) for a usable query, or (None, reason) when it must be rejected."""
    names = ground(question, client)
    text = llm.complete(prompts.GENERATE_SYSTEM, prompts.generation_prompt(question, names))
    problem = None
    for attempt in range(2):
        if "OUT_OF_SCOPE" in text:
            return None, "the question is not about the wine graph"
        result = guardrails.check(guardrails.clean(text))
        if result.fatal:
            return None, result.fatal
        problem = result.problem or _explain_error(client, result.cypher)
        if not problem:
            return result.cypher, None
        log.info(f"ask attempt {attempt + 1} failed: {problem}")
        if attempt == 0:
            text = llm.complete(prompts.GENERATE_SYSTEM, prompts.retry_prompt(question, names, text, problem))
    return None, f"no valid query after one retry ({problem})"


def _summarise(question: str, rows: list[dict[str, Any]], llm: LLM) -> str:
    """Short answer written from the first rows only; long strings are cut."""
    def short(value: Any) -> Any:
        return value[:200] if isinstance(value, str) else value

    sample = [{k: short(v) for k, v in row.items()} for row in rows[:SUMMARY_ROWS]]
    return llm.complete(prompts.SUMMARY_SYSTEM, prompts.summary_prompt(question, json.dumps(sample, default=str)))


def answer_question(question: str, client: GraphReader, llm: LLM | None = None) -> AskResponse:
    """Answer a natural-language question. Returns status 'rejected' when a guardrail fires."""
    if guardrails.asks_to_write(question):
        return AskResponse(question=question, status="rejected", reason="the question asks to change data")
    llm = llm or make_llm()
    cypher, reason = _generate(question, client, llm)
    if cypher is None:
        return AskResponse(question=question, status="rejected", reason=reason)
    try:
        rows = client.read(cypher, {})
    except ClientError as exc:
        if exc.code and "TimedOut" in exc.code:
            raise  # mapped to HTTP 504 by app/errors.py
        return AskResponse(question=question, cypher=cypher, status="error", reason=(exc.message or "")[:300])
    if not rows:
        return AskResponse(
            question=question, cypher=cypher, status="empty", summary="No wine in the graph matches this question."
        )
    summary = _summarise(question, rows, llm)
    return AskResponse(question=question, cypher=cypher, rows=rows, summary=summary, status="ok")


__all__ = ["answer_question", "GraphReader"]
