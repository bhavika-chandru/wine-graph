"""Before/after query benchmarks and PROFILE evidence.

For each case, the "before" and "after" variants are timed (median of N runs after
warm-up) and profiled (total db hits + the operators in the plan, e.g.
NodeByLabelScan -> NodeIndexSeekByRange). Results go to docs/benchmarks.md.

Needs admin credentials (NEO4J_ADMIN_USER / NEO4J_ADMIN_PASSWORD) because the index
case drops and recreates the range indexes. Run it on a dev database only.
"""

import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from neo4j import GraphDatabase, Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# pylint: disable=wrong-import-position
from app.models.common import PageParams
from app.services.base import WINE_SUMMARY_RETURN
from app.services.wine_service import WINE_DETAIL_QUERY, WineFilters, build_wine_list_query

load_dotenv()  # NEO4J_ADMIN_USER / NEO4J_ADMIN_PASSWORD from .env

RUNS = int(os.getenv("BENCH_RUNS", "10"))
WARMUP = 2
OUTPUT = Path(__file__).resolve().parents[1] / "docs" / "benchmarks.md"

# Index names agreed with Person 2 (docs/contracts.md)
CREATE_INDEXES = [
    "CREATE RANGE INDEX wine_points IF NOT EXISTS FOR (w:Wine) ON (w.points)",
    "CREATE RANGE INDEX wine_price IF NOT EXISTS FOR (w:Wine) ON (w.price)",
    "CALL db.awaitIndexes(300)",
]
DROP_INDEXES = ["DROP INDEX wine_points IF EXISTS", "DROP INDEX wine_price IF EXISTS"]


@dataclass
class Variant:
    """One version of a query, with optional setup statements run before timing."""

    label: str
    cypher: str
    params: dict[str, Any] = field(default_factory=dict)
    setup: list[str] = field(default_factory=list)


@dataclass
class Case:
    """A before/after comparison."""

    name: str
    why: str
    before: Variant
    after: Variant


@dataclass
class Measurement:
    """Timing and plan statistics of one variant."""

    median_ms: float
    db_hits: int
    operators: list[str]


def _flatten(plan: Any) -> tuple[int, list[str]]:
    """Sum db hits and list operator names over a profiled plan tree (dict or object)."""
    if plan is None:
        return 0, []
    get = (
        plan.get
        if isinstance(plan, dict)
        else lambda key, default=None: getattr(plan, key, default)
    )
    hits = int(get("dbHits", 0) or get("db_hits", 0) or 0)
    name = str(get("operatorType", "") or get("operator_type", "")).split("@", maxsplit=1)[0]
    ops = [name] if name else []
    for child in get("children", []) or []:
        child_hits, child_ops = _flatten(child)
        hits += child_hits
        ops.extend(child_ops)
    return hits, ops


def measure(session: Session, variant: Variant) -> Measurement:
    """Run setup, warm up, time RUNS executions and profile once."""
    for statement in variant.setup:
        session.run(statement).consume()
    for _ in range(WARMUP):
        session.run(variant.cypher, variant.params).consume()
    timings = []
    for _ in range(RUNS):
        start = time.perf_counter()
        list(session.run(variant.cypher, variant.params))
        timings.append((time.perf_counter() - start) * 1000)
    profiled = session.run("PROFILE " + variant.cypher, variant.params)
    list(profiled)  # Neo4j only attaches profiler stats once the rows have been read
    summary = profiled.consume()
    hits, ops = _flatten(summary.profile)
    return Measurement(statistics.median(timings), hits, ops)


# Same filters, ordering, page size and returned columns as build_wine_list_query,
# only the WHERE clause differs (catch-all "IS NULL OR" form).
NAIVE_FILTER_QUERY = """
MATCH (w:Wine)
WHERE ($min_points IS NULL OR w.points >= $min_points)
  AND ($min_price IS NULL OR w.price >= $min_price)
  AND ($max_price IS NULL OR w.price <= $max_price)
WITH DISTINCT w ORDER BY w.points DESC, w.id ASC SKIP $skip LIMIT $limit
""" + WINE_SUMMARY_RETURN

NAIVE_DETAIL_QUERY = """
MATCH (w:Wine {id: $id})
OPTIONAL MATCH (w)-[:OF_VARIETY]->(v:Variety)
OPTIONAL MATCH (w)-[:MADE_BY]->(m:Winery)
OPTIONAL MATCH (t:Taster)-[:REVIEWED]->(w)
OPTIONAL MATCH (w)-[:HAS_NOTE]->(d:Descriptor)
OPTIONAL MATCH (w)-[:FROM]->()-[:IN*1..2]->(c:Country)
RETURN w.title, v.name, m.name, t.name, c.name, collect(d.name) AS notes
"""

RANGE_QUERY = """
MATCH (w:Wine) WHERE w.points >= 95 AND w.price <= 40
RETURN w.title, w.points, w.price ORDER BY w.points DESC LIMIT 20
"""

FULL_RESULT_QUERY = (
    "MATCH (w:Wine) WHERE w.points >= 85 RETURN w.id, w.title ORDER BY w.points DESC"
)
PAGED_QUERY = FULL_RESULT_QUERY + " SKIP $skip LIMIT $limit"


def build_cases(session: Session) -> list[Case]:
    """The comparisons shown in the README and the defense."""
    sample = session.run("MATCH (w:Wine)-[:HAS_NOTE]->() RETURN w.id AS id LIMIT 1").single()
    wine_id = sample["id"] if sample else 0
    page = PageParams(skip=0, limit=20)
    rewritten, rewritten_params = build_wine_list_query(
        WineFilters(min_points=95, max_price=30), page
    )
    return [
        Case(
            "Range filter: no index vs range index",
            "Without an index the planner scans every Wine node; with a range index it seeks.",
            Variant("no index", RANGE_QUERY, setup=DROP_INDEXES),
            Variant("range index", RANGE_QUERY, setup=CREATE_INDEXES),
        ),
        Case(
            "Optional filters: `$x IS NULL OR` vs query built from needed clauses",
            "Both return the same columns for the same filters (points >= 95, price <= 30). "
            + "`$x IS NULL OR w.points >= $x` cannot be answered from an index, so the planner scans "
            + "every Wine; building only the needed clauses lets it seek on the points index.",
            Variant(
                "catch-all WHERE",
                NAIVE_FILTER_QUERY,
                {"min_points": 95, "min_price": None, "max_price": 30, "skip": 0, "limit": 21},
            ),
            Variant("rewritten", rewritten, rewritten_params),
        ),
        Case(
            "Wine detail: OPTIONAL MATCH chain vs pattern comprehensions",
            "For a single wine both are a few milliseconds: the chain only multiplies rows by the "
            + "number of notes, which is small. Pattern comprehensions are kept for correctness, not "
            + "speed: they never duplicate rows, so no collect()/DISTINCT is needed, which matters in "
            + "list queries where several multi-valued relationships are combined.",
            Variant("OPTIONAL MATCH chain", NAIVE_DETAIL_QUERY, {"id": wine_id}),
            Variant("pattern comprehensions", WINE_DETAIL_QUERY, {"id": wine_id}),
        ),
        Case(
            "Pagination: full result vs SKIP/LIMIT",
            "Returning every match transfers and serialises thousands of rows; a page of 20 "
            + "lets the planner stop early (Top-K sort).",
            Variant("no pagination", FULL_RESULT_QUERY),
            Variant("LIMIT 20", PAGED_QUERY, {"skip": 0, "limit": 20}),
        ),
    ]


def render(results: list[tuple[Case, Measurement, Measurement]]) -> str:
    """Markdown report for the README."""
    lines = [
        "# Query benchmarks",
        "",
        f"Median of {RUNS} runs after {WARMUP} warm-up runs. Generated by `make bench`.",
        "",
        "| Case | Before | After | Speed-up | db hits before -> after |",
        "|---|---|---|---|---|",
    ]
    for case, before, after in results:
        speedup = before.median_ms / after.median_ms if after.median_ms else float("inf")
        lines.append(
            f"| {case.name} | {case.before.label}: {before.median_ms:.1f} ms "
            f"| {case.after.label}: {after.median_ms:.1f} ms | x{speedup:.1f} "
            f"| {before.db_hits:,} -> {after.db_hits:,} |"
        )
    lines.append("")
    for case, before, after in results:
        lines += [
            f"## {case.name}",
            "",
            case.why,
            "",
            f"- **{case.before.label}** plan: {' > '.join(before.operators)}",
            f"- **{case.after.label}** plan: {' > '.join(after.operators)}",
            "",
        ]
    return "\n".join(lines)


def main() -> None:
    """Run every case and write docs/benchmarks.md."""
    uri = os.getenv("NEO4J_BENCH_URI", "bolt://localhost:7687")
    auth = (os.getenv("NEO4J_ADMIN_USER", "neo4j"), os.getenv("NEO4J_ADMIN_PASSWORD", ""))
    with GraphDatabase.driver(uri, auth=auth) as driver, driver.session() as session:
        results = []
        for case in build_cases(session):
            before = measure(session, case.before)
            after = measure(session, case.after)
            results.append((case, before, after))
            print(f"{case.name}: {before.median_ms:.1f} ms -> {after.median_ms:.1f} ms")
        for statement in CREATE_INDEXES:  # always leave the indexes in place
            session.run(statement).consume()
    OUTPUT.write_text(render(results), encoding="utf-8")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
