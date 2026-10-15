"""Tests for the benchmark report helpers (no database needed)."""

from scripts.benchmark import Case, Measurement, Variant, _flatten, render


def test_flatten_sums_hits_and_lists_operators():
    """Db hits are summed over the whole plan tree."""
    plan = {
        "operatorType": "ProduceResults@neo4j",
        "dbHits": 0,
        "children": [{"operatorType": "NodeIndexSeekByRange@neo4j", "dbHits": 42, "children": []}],
    }
    assert _flatten(plan) == (42, ["ProduceResults", "NodeIndexSeekByRange"])
    assert _flatten(None) == (0, [])


def test_render_contains_table_and_plans():
    """The report shows timings, speed-up and plan operators."""
    case = Case("Index", "why", Variant("no index", "Q"), Variant("index", "Q"))
    before = Measurement(120.0, 130_000, ["NodeByLabelScan"])
    after = Measurement(4.0, 300, ["NodeIndexSeekByRange"])
    report = render([(case, before, after)])
    assert "x30.0" in report
    assert "130,000 -> 300" in report
    assert "NodeByLabelScan" in report and "NodeIndexSeekByRange" in report
