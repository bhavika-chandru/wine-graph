"""API tests with a fake database: status codes, response shapes, pagination."""

from types import SimpleNamespace

from app.models.ask import AskResponse
from app.routers import ask as ask_router
from tests.conftest import wine_row


def test_health_up(api):
    """Health up."""
    response = api.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "neo4j": "up"}


def test_health_down(api, fake):
    """Health down."""
    fake.is_up = False
    assert api.get("/health").status_code == 503


def test_list_wines_paginates_with_extra_row(api, fake):
    """List wines paginates with extra row."""
    fake.queue([wine_row(i) for i in range(3)])  # limit=2 -> the 3rd row signals a next page
    response = api.get("/api/wines", params={"limit": 2})
    body = response.json()
    assert response.status_code == 200
    assert len(body["items"]) == 2
    assert body["has_more"] is True
    _, params = fake.calls[0]
    assert params["limit"] == 3


def test_list_wines_last_page(api, fake):
    """List wines last page."""
    fake.queue([wine_row(1)])
    body = api.get("/api/wines", params={"limit": 2, "skip": 4}).json()
    assert body["has_more"] is False
    assert body["skip"] == 4


def test_list_wines_rejects_bad_params(api):
    """List wines rejects bad params."""
    assert api.get("/api/wines", params={"limit": 0}).status_code == 422
    assert api.get("/api/wines", params={"limit": 101}).status_code == 422
    assert api.get("/api/wines", params={"min_points": 120}).status_code == 422
    assert api.get("/api/wines", params={"min_price": 50, "max_price": 10}).status_code == 422


def test_get_wine(api, fake):
    """Get wine."""
    fake.queue(
        [
            {
                **wine_row(7),
                "description": "Earthy.",
                "taster": "Roger Voss",
                "location": ["Burgundy", "France"],
                "notes": ["earthy", "cherry"],
            }
        ]
    )
    body = api.get("/api/wines/7").json()
    assert body["id"] == 7
    assert body["notes"] == ["earthy", "cherry"]


def test_get_wine_not_found(api):
    """Get wine not found."""
    response = api.get("/api/wines/999")
    assert response.status_code == 404
    assert "not found" in response.json()["error"]


def test_get_wine_invalid_id(api):
    """Get wine invalid id."""
    assert api.get("/api/wines/abc").status_code == 422


def test_value_route_not_shadowed_by_id(api, fake):
    """Value route not shadowed by id."""
    fake.queue([{**wine_row(2), "value_score": 4.5}])
    response = api.get("/api/wines/value", params={"max_price": 20})
    assert response.status_code == 200
    assert response.json()["items"][0]["value_score"] == 4.5


def test_value_with_variety_anchors_on_variety(api, fake):
    """Value with variety anchors on variety."""
    fake.queue([])
    api.get("/api/wines/value", params={"variety": "Riesling"})
    cypher, params = fake.calls[0]
    assert "Variety {name: $variety}" in cypher
    assert params["variety"] == "Riesling"


def test_similar_uses_gds_results(api, fake):
    """Similar uses gds results."""
    fake.queue([{"n": 1}], [{**wine_row(2), "score": 0.6}])
    body = api.get("/api/wines/1/similar").json()
    assert body["method"] == "gds_node_similarity"
    assert body["items"][0]["score"] == 0.6


def test_similar_falls_back_to_cypher(api, fake):
    """Similar falls back to cypher."""
    fake.queue([{"n": 1}], [], [{**wine_row(3), "score": 0.4}])
    body = api.get("/api/wines/1/similar").json()
    assert body["method"] == "cypher_jaccard"
    assert len(fake.calls) == 3


def test_similar_unknown_wine(api, fake):
    """Similar unknown wine."""
    fake.queue([{"n": 0}])
    assert api.get("/api/wines/1/similar").status_code == 404


def test_predicted_tier(api, fake):
    """Predicted tier."""
    fake.queue([{"embedding": [0.1, 0.2], "points": 91}])
    body = api.get("/api/wines/1/predicted-tier").json()
    assert body == {
        "wine_id": 1,
        "predicted_tier": "high",
        "probability": 0.87,
        "actual_points": 91,
    }


def test_predicted_tier_without_embeddings(api, fake):
    """Predicted tier without embeddings."""
    fake.queue([{"embedding": None, "points": 91}])
    assert api.get("/api/wines/1/predicted-tier").status_code == 503


def test_region_profile_ambiguous(api, fake):
    """Region profile ambiguous."""
    fake.queue([{"province": "California"}, {"province": "Washington"}])
    response = api.get("/api/regions/Columbia Valley/profile")
    assert response.status_code == 409
    assert response.json()["candidates"] == ["California", "Washington"]


def test_region_profile(api, fake):
    """Region profile."""
    profile = {
        "region": "Napa Valley",
        "province": "California",
        "wine_count": 10,
        "avg_points": 90.5,
        "avg_price": 60.0,
        "top_varieties": [{"name": "Cabernet Sauvignon", "wines": 7}],
        "top_notes": [{"name": "oak", "wines": 6}],
    }
    fake.queue([{"province": "California"}], [profile])
    assert api.get("/api/regions/Napa Valley/profile").json()["wine_count"] == 10


def test_region_profile_unknown(api):
    """Region profile unknown."""
    assert api.get("/api/regions/Atlantis/profile").status_code == 404


def test_region_profile_wrong_province(api, fake):
    """Region profile wrong province."""
    fake.queue([{"province": "California"}])
    response = api.get("/api/regions/Napa Valley/profile", params={"province": "Oregon"})
    assert response.status_code == 404


def test_taster_bias(api, fake):
    """Taster bias."""
    groups = [
        {
            "variety": "Merlot",
            "reviews": 10,
            "taster_avg": 86.0,
            "others_avg": 88.0,
            "deviation": -2.0,
        },
        {
            "variety": "Syrah",
            "reviews": 30,
            "taster_avg": 89.0,
            "others_avg": 88.0,
            "deviation": 1.0,
        },
    ]
    fake.queue([{"n": 1}], groups)
    body = api.get("/api/tasters/Roger Voss/bias", params={"limit": 1}).json()
    assert body["total_reviews"] == 40
    assert body["overall_deviation"] == (-2.0 * 10 + 1.0 * 30) / 40
    assert len(body["items"]) == 1 and body["has_more"] is True


def test_taster_unknown(api, fake):
    """Taster unknown."""
    fake.queue([{"n": 0}])
    assert api.get("/api/tasters/Nobody/bias").status_code == 404


def test_pairings_lowercases_food(api, fake):
    """Pairings lowercases food."""
    fake.queue([{"n": 1}], [{**wine_row(4), "pairing_weight": 0.8}])
    body = api.get("/api/pairings", params={"food": "Lamb", "max_price": 30}).json()
    assert body["items"][0]["pairing_weight"] == 0.8
    cypher, params = fake.calls[1]
    assert params["food"] == "lamb"
    assert "w.price <= $max_price" in cypher


def test_pairings_unknown_food(api, fake):
    """Pairings unknown food."""
    fake.queue([{"n": 0}])
    assert api.get("/api/pairings", params={"food": "pizza"}).status_code == 404


def test_styles_not_computed(api):
    """Styles not computed."""
    assert api.get("/api/styles").status_code == 503


def test_styles(api, fake):
    """Styles."""
    fake.queue([{"id": 3, "size": 12, "top_notes": ["oak", "vanilla"]}])
    assert api.get("/api/styles").json()["items"][0]["id"] == 3


def test_style_detail(api, fake):
    """Style detail."""
    fake.queue([{"notes": ["oak", "vanilla"]}], [wine_row(5)])
    body = api.get("/api/styles/3").json()
    assert body["notes"] == ["oak", "vanilla"]
    assert body["example_wines"][0]["id"] == 5


def test_style_unknown(api, fake):
    """Style unknown."""
    fake.queue([{"notes": []}])
    assert api.get("/api/styles/99").status_code == 404


def test_ask_not_implemented_yet(api):
    """Ask not implemented yet."""
    assert api.post("/api/ask", json={"question": "Best Riesling under 20?"}).status_code == 503


def test_ask_validation(api):
    """Ask validation."""
    assert api.post("/api/ask", json={"question": "x"}).status_code == 422


def test_ask_ok_and_rejected(api, monkeypatch):
    """Ask ok and rejected."""

    def fake_answer(question, _client):
        if "delete" in question.lower():
            return AskResponse(question=question, status="rejected", reason="write clause DELETE")
        return AskResponse(
            question=question,
            cypher="MATCH (w:Wine) RETURN w.title LIMIT 5",
            rows=[{"w.title": "X"}],
            summary="One wine.",
            status="ok",
        )

    monkeypatch.setattr(ask_router, "answer_question", fake_answer)
    ok = api.post("/api/ask", json={"question": "Best Riesling?"})
    assert ok.status_code == 200 and ok.json()["provenance"]["rows"] == "verified (database)"
    rejected = api.post("/api/ask", json={"question": "Delete all wines"})
    assert rejected.status_code == 400 and rejected.json()["status"] == "rejected"


def test_ask_requires_api_key_when_configured(api, monkeypatch):
    """Ask requires api key when configured."""
    fake_settings = SimpleNamespace(api_key="secret")
    monkeypatch.setattr("app.deps.get_settings", lambda: fake_settings)
    assert api.post("/api/ask", json={"question": "Best Riesling?"}).status_code == 401
    response = api.post(
        "/api/ask", json={"question": "Best Riesling?"}, headers={"X-API-Key": "secret"}
    )
    assert response.status_code == 503  # key accepted; agent stub not implemented yet


def test_openapi_lists_all_endpoints(api):
    """Swagger docs are generated for every route."""
    paths = api.get("/openapi.json").json()["paths"]
    assert {"/health", "/api/wines/{wine_id}/similar", "/api/styles", "/api/ask"} <= set(paths)
