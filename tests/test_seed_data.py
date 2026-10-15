"""Checks on the ETL's cleaning and X-Wines join (no database needed)."""

from scripts.seed_data import (
    clean_review,
    dimensions,
    extract_notes,
    match_varieties,
    parse_list,
    parse_vintage,
    read_reviews,
    variety_profiles,
)

HEADER = ",country,description,designation,points,price,province,region_1,region_2,taster_name,"
HEADER += "taster_twitter_handle,title,variety,winery\n"
ROWS = [
    '0,Italy,"Aromas include tropical fruit, broom and dried herb. Unripened apple, citrus.",Vulkà Bianco,87,,'
    + "Sicily & Sardinia,Etna,,Kerin O’Keefe,@kerinokeefe,Nicosia 2013 Vulkà Bianco  (Etna),White Blend,Nicosia\n",
    '1,Portugal,"Ripe and fruity, with juicy red berry fruits.",Avidagos,87,15.0,Douro,,,Roger Voss,@vossroger,'
    + "Quinta dos Avidagos 2011 Avidagos Red (Douro),Portuguese Red,Quinta dos Avidagos\n",
]


def _write(tmp_path, rows):
    path = tmp_path / "reviews.csv"
    path.write_text(HEADER + "".join(rows), encoding="utf-8")
    return path


def test_notes_are_canonical_and_prefer_longer_terms():
    """Synonyms merge, plurals match, and 'black cherry' is not also counted as 'cherry'."""
    assert extract_notes("Earthy, with black cherries and cassis; oaky finish.") == [
        "earth",
        "black cherry",
        "blackcurrant",
        "oak",
    ]
    assert not extract_notes("Limestone soils.")  # 'lime' must not match inside a word


def test_vintage_from_title():
    """First plausible year, or None for non-vintage wines."""
    assert parse_vintage("Nicosia 2013 Vulkà Bianco  (Etna)") == 2013
    assert parse_vintage("Krug NV Brut Champagne") is None


def test_parse_list_handles_xwines_literals():
    """X-Wines stores lists as Python literals; bad values give an empty list."""
    assert parse_list("['Pork', 'Shellfish']") == ["Pork", "Shellfish"]
    assert not parse_list("not a list")


def test_read_reviews_cleans_and_deduplicates(tmp_path):
    """Missing price is None, missing region is None, an exact duplicate is dropped."""
    wines, dropped = read_reviews(_write(tmp_path, ROWS + [ROWS[1].replace("1,", "2,", 1)]))
    assert [w["id"] for w in wines] == [0, 1]
    assert dropped["duplicate review"] == 1
    assert wines[0]["price"] is None and wines[0]["vintage"] == 2013 and wines[0]["region"] == "Etna"
    assert wines[1]["region"] is None and wines[1]["price"] == 15.0
    assert "tropical fruit" in wines[0]["notes"] and "fruit" in wines[1]["notes"]


def test_rows_without_country_are_dropped():
    """A wine that cannot be placed in the hierarchy is skipped."""
    assert clean_review({"": "5", "country": "", "province": "", "points": "90"}) is None


def test_dimensions_are_distinct():
    """Shared nodes are listed once, with their composite keys."""
    wine = {"country": "US", "province": "California", "region": "Napa Valley", "variety": "Merlot",
            "winery": "A", "taster": None, "twitter": None, "notes": ["oak"]}
    dims = dimensions([wine, dict(wine, region=None)])
    assert dims["regions"] == [{"name": "Napa Valley", "province": "California", "country": "US"}]
    assert dims["wineries"] == [{"name": "A", "country": "US"}]
    assert not dims["tasters"]


def test_xwines_join_on_variety():
    """'Syrah/Shiraz' matches either name; foods listed by enough wines become pairings."""
    known = {"syrah": "Syrah", "merlot": "Merlot"}
    assert match_varieties(["Syrah/Shiraz", "Unknown"], known) == {"Syrah"}
    xwines = [
        {"WineID": str(i), "Grapes": "['Syrah/Shiraz']", "Harmonize": "['Beef', 'Lamb']" if i < 3 else "['Beef']",
         "Type": "Red", "Body": "Full-bodied", "Acidity": "Medium"}
        for i in range(4)
    ]
    profile = variety_profiles(xwines, known, {"0": 4.0, "1": 3.0})[0]
    assert profile["name"] == "Syrah" and profile["crowd_rating"] == 3.5
    assert profile["pairings"] == [{"food": "beef", "weight": 1.0}, {"food": "lamb", "weight": 0.75}]
