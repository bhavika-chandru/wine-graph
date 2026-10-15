"""Checks on the synthetic benchmark data (no database needed)."""

import random

from scripts.generate_bench_data import VARIETIES, build_dimensions, build_wines


def test_wines_follow_the_contracts_and_have_realistic_gaps():
    """Ids unique, points in range, some wines without region/price, Riesling present."""
    rng = random.Random(1)
    dims = build_dimensions(rng)
    wines = build_wines(rng, dims, 2000)
    assert len({w["id"] for w in wines}) == 2000
    assert all(80 <= w["points"] <= 100 for w in wines)
    assert 0.10 < sum(w["region"] is None for w in wines) / 2000 < 0.20
    assert 0.03 < sum(w["price"] is None for w in wines) / 2000 < 0.12
    assert all(1 <= len(w["notes"]) <= 6 for w in wines)
    assert "Riesling" in VARIETIES


def test_descriptor_usage_is_skewed():
    """The most common note is used far more than the rarest (supernode behaviour)."""
    rng = random.Random(2)
    wines = build_wines(rng, build_dimensions(rng), 3000)
    counts: dict[str, int] = {}
    for wine in wines:
        for note in wine["notes"]:
            counts[note] = counts.get(note, 0) + 1
    assert max(counts.values()) > 10 * min(counts.values())


def test_region_name_shared_by_two_provinces():
    """Mirrors the real data: the API must disambiguate with ?province=."""
    regions = build_dimensions(random.Random(3))["regions"]
    provinces = {r["province"] for r in regions if r["name"] == "Columbia Valley"}
    assert provinces == {"Washington", "Oregon"}
