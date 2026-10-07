"""API smoke tests via FastAPI TestClient. No network access."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from vigline.api.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=tmp_path / "api.db")
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["scheduler"] == "not_started"
    assert "no authorized source configured" in body["data_sources"]["promo_terms"]
    assert "milestone 2" in body["data_sources"]["odds"]


def test_offers_crud_empty_then_create(client):
    assert client.get("/api/offers").json() == {"offers": []}
    r = client.post("/api/offers", json={
        "platform": "DraftKings", "title": "Bet 5 get 150", "offer_type": "bonus_bet",
    })
    assert r.status_code == 201
    offer_id = r.json()["id"]
    offers = client.get("/api/offers").json()["offers"]
    assert len(offers) == 1
    detail = client.get(f"/api/offers/{offer_id}").json()
    assert detail["offer"]["title"] == "Bet 5 get 150"
    assert detail["terms_versions"] == []
    assert client.get("/api/offers/9999").status_code == 404


def test_terms_text_capture_and_idempotency(client):
    offer_id = client.post("/api/offers", json={
        "platform": "X", "title": "T", "offer_type": "bonus_bet"}).json()["id"]
    r1 = client.post(f"/api/offers/{offer_id}/terms/text",
                     json={"raw_text": "official terms v1"})
    assert r1.status_code == 201
    assert r1.json()["version_no"] == 1
    assert r1.json()["duplicate"] is False
    r2 = client.post(f"/api/offers/{offer_id}/terms/text",
                     json={"raw_text": "official terms v1"})
    assert r2.json()["duplicate"] is True
    detail = client.get(f"/api/offers/{offer_id}").json()
    assert detail["offer"]["status"] == "needs_review"
    assert len(detail["terms_versions"]) == 1


def test_terms_fetch_without_sources_unavailable(client):
    offer_id = client.post("/api/offers", json={
        "platform": "X", "title": "T", "offer_type": "bonus_bet"}).json()["id"]
    r = client.post(f"/api/offers/{offer_id}/terms/fetch",
                    json={"url": "https://example.com/terms"})
    assert r.status_code == 409
    assert "no authorized source configured" in r.json()["detail"]


def test_review_and_eligibility(client):
    offer_id = client.post("/api/offers", json={
        "platform": "X", "title": "T", "offer_type": "bonus_bet"}).json()["id"]
    r = client.post(f"/api/offers/{offer_id}/review", json={"review_state": "needs_review"})
    assert r.status_code == 200
    bad = client.post(f"/api/offers/{offer_id}/review", json={"review_state": "bogus"})
    assert bad.status_code == 422
    e = client.post(f"/api/offers/{offer_id}/eligibility", json={"state": "eligible"})
    assert e.status_code == 200


def test_wallet_lifecycle(client):
    assert client.get("/api/wallet").json() == {"records": []}
    r = client.post("/api/wallet", json={
        "platform": "DraftKings", "reward_type": "bonus_bet",
        "face_value_cents": 10000, "cash_vs_credit": "restricted_credit",
    })
    assert r.status_code == 201
    assert r.json()["source"] == "manually_entered_unverified"
    rid = r.json()["id"]
    ok = client.post(f"/api/wallet/{rid}/transition", json={"to_state": "issued"})
    assert ok.status_code == 200
    bad = client.post(f"/api/wallet/{rid}/transition", json={"to_state": "pending"})
    assert bad.status_code == 422


def test_calculator_hedge_worked_example(client):
    r = client.post("/api/calculator/hedge", json={
        "face_value_cents": 10000, "bonus_odds": "3.0", "hedge_odds": "2.0",
        "hedge_allowed": True,
    })
    body = r.json()
    assert body["refused"] is False
    assert body["hedge_stake_cents"] == 10000
    assert body["locked_cents"] == 10000
    assert body["conversion_rate"] == "1.0000"


def test_calculator_hedge_refusal(client):
    r = client.post("/api/calculator/hedge", json={
        "face_value_cents": 10000, "bonus_odds": "3.0", "hedge_odds": "2.0",
        "hedge_allowed": False, "hedge_restriction_note": "no hedging per terms",
    })
    body = r.json()
    assert body["refused"] is True
    assert "prohibit" in body["reason"]


def test_calculator_scenarios(client):
    r = client.post("/api/calculator/scenarios", json={
        "bonus_face_cents": 10000, "bonus_odds": "3.0", "hedge_odds": "2.0",
        "qualifying_stake_cents": 5000, "qualifying_odds": "2.0",
    })
    body = r.json()
    keys = {row["key"] for row in body["rows"]}
    assert {"qualifying_win", "bonus_issued", "conversion_bonus_leg_wins"} <= keys
    assert body["peak_cash_commitment_cents"] == 15000


def test_calculator_estimate_requires_probabilities(client):
    r = client.post("/api/calculator/estimate", json={
        "plan": {"bonus_face_cents": 10000, "bonus_odds": "3.0", "hedge_odds": "2.0"},
        "probabilities": None,
    })
    assert r.status_code == 422
    ok = client.post("/api/calculator/estimate", json={
        "plan": {"bonus_face_cents": 10000, "bonus_odds": "3.0", "hedge_odds": "2.0"},
        "probabilities": {"p_bonus_leg_win": "0.5"},
    })
    assert ok.status_code == 200
    assert ok.json()["label"].startswith("model estimate (inputs:")


def test_alerts_and_scheduler_status(client):
    assert client.get("/api/alerts").json() == {"alerts": []}
    run = client.post("/api/alerts/scan/run").json()
    assert run["scan_status"] == "no_data_source_configured"
    assert run["delivery"] == "delivery_disabled"
    status = client.get("/api/alerts/scheduler").json()
    assert status["running"] is False
    assert "NEW" in status["implementation"]
    ids = {j["id"] for j in status["jobs"]}
    assert ids == {"vigline_scan", "promo_expiry_check"}


def test_rankings_empty_and_metrics(client):
    body = client.get("/api/rankings", params={"metric": "peak_commitment"}).json()
    assert body["rankings"] == []
    assert client.get("/api/rankings", params={"metric": "bogus"}).status_code == 400
    metrics = client.get("/api/rankings/metrics").json()
    assert set(metrics) == {"peak_commitment", "estimated_net", "maximin", "simplicity"}


def test_web_pages_render(client):
    for path in ["/", "/wallet", "/calculator", "/alerts"]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert "Vigline" in r.text
    # dashboard empty state explains what is missing
    assert "No offers yet" in client.get("/").text
    assert "no authorized source configured" in client.get("/").text
