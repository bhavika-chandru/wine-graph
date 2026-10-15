"""Static checks on generated Cypher, run before anything reaches the database.

Three outcomes for a query:
- fatal: a write or admin clause, or a question that asks to change data -> rejected, no retry
- fixable: unknown label / relationship / property -> the model gets one retry with the problem
- repaired silently: unbounded variable-length paths are capped, a LIMIT is added or lowered
The read-only transaction in the database client stays the last line of defence.
"""

import re
from dataclasses import dataclass

LABELS = {"Wine", "Variety", "Winery", "Region", "Province", "Country", "Taster", "Descriptor", "Food"}
RELATIONSHIPS = {"MADE_BY", "OF_VARIETY", "FROM", "IN", "REVIEWED", "HAS_NOTE", "PAIRS_WITH", "SIMILAR_TO"}
PROPERTIES = {
    "id", "title", "description", "points", "price", "vintage",  # Wine
    "name", "country", "province", "twitter",  # shared keys, Taster
    "wine_type", "body", "acidity", "crowd_rating", "xwines_wines",  # Variety (X-Wines)
    "style_id", "common",  # Descriptor (GDS)
    "score", "weight",  # SIMILAR_TO, PAIRS_WITH
}
MAX_HOPS = 3
DEFAULT_LIMIT = 25
MAX_LIMIT = 100

WRITE_INTENT = re.compile(r"\b(delete|drop|remove|insert|update|create|erase|wipe|truncate)\b", re.IGNORECASE)
FORBIDDEN = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|CALL|LOAD|USE|SHOW|GRANT|DENY|REVOKE"
    + r"|ALTER|START|STOP|TERMINATE)\b|\bapoc\.|\bgds\.|\bdbms\.",
    re.IGNORECASE,
)
STRING = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
NODE_LABEL = re.compile(r"\(\s*\w*\s*:\s*`?(\w+)")
REL_TYPE = re.compile(r"\[\s*\w*\s*:\s*([\w|:` ]+)")
PROPERTY = re.compile(r"\b[A-Za-z_]\w*\.([A-Za-z_]\w*)")
VAR_LENGTH = re.compile(r"\*\s*(\d*)\s*(\.\.)?\s*(\d*)(?=\s*[\]{])")
LIMIT = re.compile(r"\bLIMIT\s+(\d+)\s*;?\s*$", re.IGNORECASE)


@dataclass
class Check:
    """Result of the static checks: the (possibly repaired) query and what is wrong with it."""

    cypher: str
    fatal: str | None = None
    problem: str | None = None


def asks_to_write(question: str) -> bool:
    """True if the question itself asks to change data."""
    return bool(WRITE_INTENT.search(question))


def clean(text: str) -> str:
    """Strip markdown fences and a trailing semicolon from the model's answer."""
    text = re.sub(r"^```\w*\s*|\s*```$", "", text.strip())
    return text.strip().rstrip(";").strip()


def _bound(match: re.Match) -> str:
    low, dots, high = match.group(1), match.group(2), match.group(3)
    if not dots:  # *n or bare *
        return f"*{min(int(low), MAX_HOPS)}" if low else f"*1..{MAX_HOPS}"
    return f"*{low or 1}..{min(int(high), MAX_HOPS) if high else MAX_HOPS}"


def _with_limit(cypher: str) -> str:
    match = LIMIT.search(cypher)
    if not match:
        return f"{cypher}\nLIMIT {DEFAULT_LIMIT}"
    if int(match.group(1)) > MAX_LIMIT:
        return cypher[: match.start()] + f"LIMIT {MAX_LIMIT}"
    return cypher


def _unknown_names(code: str) -> list[str]:
    labels = set(NODE_LABEL.findall(code)) - LABELS
    rel_types = {t.strip("` :") for group in REL_TYPE.findall(code) for t in re.split(r"[|:]", group)}
    rel_types = {t for t in rel_types if t} - RELATIONSHIPS
    properties = set(PROPERTY.findall(code)) - PROPERTIES
    return [f"label :{x}" for x in sorted(labels)] + [f"relationship :{x}" for x in sorted(rel_types)] + [
        f"property .{x}" for x in sorted(properties)
    ]


def check(cypher: str) -> Check:
    """Run every static check; string literals are ignored so a wine called 'Set Point' is fine."""
    code = STRING.sub("''", cypher)
    if ";" in code:
        return Check(cypher, fatal="more than one statement")
    forbidden = FORBIDDEN.search(code)
    if forbidden:
        return Check(cypher, fatal=f"read-only endpoint: '{forbidden.group(0).strip()}' is not allowed")
    if not re.match(r"\s*(OPTIONAL\s+MATCH|MATCH|WITH|UNWIND|RETURN)\b", code, re.IGNORECASE):
        return Check(cypher, fatal="not a read query")
    if "fastrp" in code:
        return Check(cypher, problem="do not return embeddings (fastrp)")
    unknown = _unknown_names(code)
    if unknown:
        return Check(cypher, problem="not in the schema: " + ", ".join(unknown))
    repaired = _with_limit(VAR_LENGTH.sub(_bound, cypher))
    return Check(repaired)
