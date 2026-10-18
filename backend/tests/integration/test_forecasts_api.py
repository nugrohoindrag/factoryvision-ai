import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from app import actions, forecasts, pubsub
from app.main import app
from app.store import store
from app.tools import forecast
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)


def _accept(_message: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True}


@pytest.fixture
def sid(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    s = str(uuid.uuid4())
    monkeypatch.setitem(pubsub._handlers, pubsub.AGENT_COMMANDS, _accept)
    yield s
    forecasts.clear(s)
    actions.clear(s)
    for p in store().where(forecasts.PLANS, "session_id", s):
        store().delete(forecasts.PLANS, p["plan_id"])


def h(s: str) -> dict[str, str]:
    return {"X-Session-Id": s}


def test_list_seeds_a_snapshot_with_options(sid: str) -> None:
    r = client.get("/api/forecasts", headers=h(sid))
    assert r.status_code == 200
    body = r.json()
    assert [s["forecast_id"] for s in body["snapshots"]] == ["FC-2027-02-001"]
    head = body["snapshots"][0]
    assert head["generated_by"] == "seed"
    assert "lines" not in head
    assert (head["window_days"], head["horizon_weeks"], head["per_customer"]) == (90, 6, False)
    assert body["windows"] == [30, 60, 90]
    assert body["horizons"] == [4, 6, 8]
    assert {p["sku"] for p in body["products"]} == set(head["skus"])


def test_seed_snapshot_keeps_the_planted_story(sid: str, ground_truth: dict[str, Any]) -> None:
    s = ground_truth["stories"]["demand_forecast"]
    snap = forecasts.latest(sid)
    change = snap["sku_summary"][s["sku"]]["change_by_final_week_pct"]
    assert s["min"] <= change <= s["max"]
    assert change == forecast.bq_forecast(s["sku"], s["horizon_weeks"])["change_by_final_week_pct"]
    weekly = forecast.bq_forecast(s["sku"], s["horizon_weeks"])["weekly_forecast"]
    assert [ln["forecast_qty"] for ln in snap["lines"] if ln["sku"] == s["sku"]] == [w["total_qty"] for w in weekly]


def test_generate_supersedes_and_detail_is_unchanged(sid: str) -> None:
    first = client.get("/api/forecasts", headers=h(sid)).json()["snapshots"][0]["forecast_id"]
    before = client.get(f"/api/forecasts/{first}", headers=h(sid)).json()
    r = client.post("/api/forecasts", json={"window_days": 90, "horizon_weeks": 6}, headers=h(sid))
    assert r.status_code == 200
    new = r.json()
    assert new["forecast_id"] == "FC-2027-02-002"
    assert new["supersedes"] == [first]
    assert new["generated_by"] == "You"
    after = client.get(f"/api/forecasts/{first}", headers=h(sid)).json()
    assert (after["status"], after["superseded_by"]) == ("Superseded", "FC-2027-02-002")
    assert after["lines"] == before["lines"]
    assert after["history"] == before["history"]
    sup = client.get("/api/forecasts?status=Superseded", headers=h(sid)).json()["snapshots"]
    assert [x["forecast_id"] for x in sup] == [first]


def test_generate_with_filter_and_per_customer(sid: str) -> None:
    r = client.post("/api/forecasts", json={"window_days": 30, "horizon_weeks": 4, "skus": ["FB-ARKA"], "per_customer": True}, headers=h(sid))
    assert r.status_code == 200
    snap = r.json()
    assert snap["skus"] == ["FB-ARKA"]
    assert {ln["customer_id"] for ln in snap["lines"]} == {"CUS-OEMA", "CUS-TIER1", "CUS-AFTM"}
    assert len(snap["history"]["FB-ARKA"]) == 5
    flagged = {ln["customer_id"] for ln in snap["lines"] if ln["insufficient_history"]}
    assert "CUS-OEMA" not in flagged
    assert flagged
    qty, ids = forecasts.qty_for(snap, "FB-ARKA", snap["period_start"], snap["period_end"])
    assert qty == sum(ln["forecast_qty"] for ln in snap["lines"])
    assert len(ids) == len(snap["lines"])


@pytest.mark.parametrize(
    ("body", "status", "code", "field"),
    [
        ({"window_days": 45}, 400, "FV-FC-001", "window_days"),
        ({"horizon_weeks": 3}, 400, "FV-FC-001", "horizon_weeks"),
        ({"window_days": "ninety"}, 400, "FV-FC-001", "window_days"),
        ({"skus": []}, 400, "FV-FC-001", "skus"),
        ({"per_customer": "maybe"}, 400, "FV-FC-001", "per_customer"),
        ({"skus": ["FB-NOPE"]}, 404, "FV-DATA-001", "skus"),
    ],
)
def test_generate_rejects_bad_input_with_field(sid: str, body: dict[str, Any], status: int, code: str, field: str) -> None:
    r = client.post("/api/forecasts", json=body, headers=h(sid))
    assert r.status_code == status
    err = r.json()["error"]
    assert err["code"] == code
    assert [f["field"] for f in err["detail"]["fields"]] == [field]


def test_status_filter_and_missing_snapshot(sid: str) -> None:
    assert client.get("/api/forecasts?status=Draft", headers=h(sid)).json()["error"]["code"] == "FV-FC-003"
    r = client.get("/api/forecasts/FC-2027-02-404", headers=h(sid))
    assert (r.status_code, r.json()["error"]["code"]) == (404, "FV-FC-002")
    assert client.get("/api/forecasts", headers={}).json()["error"]["code"] == "FV-SES-001"


def test_snapshot_is_read_only(sid: str) -> None:
    fid = client.get("/api/forecasts", headers=h(sid)).json()["snapshots"][0]["forecast_id"]
    for method in ("PATCH", "PUT", "DELETE"):
        r = client.request(method, f"/api/forecasts/{fid}", json={"lines": []}, headers=h(sid))
        assert (r.status_code, r.json()["error"]["code"]) == (409, "FV-FC-004")
    assert client.get(f"/api/forecasts/{fid}", headers=h(sid)).json()["lines"]


def test_used_by_plans_banner_data(sid: str) -> None:
    fid = forecasts.latest(sid)["forecast_id"]
    store().set(forecasts.PLANS, f"{sid}-p", {"plan_id": f"{sid}-p", "plan_number": "PP-2027-02", "session_id": sid, "forecast_id": fid, "status": "Draft"})
    body = client.get(f"/api/forecasts/{fid}", headers=h(sid)).json()
    assert body["used_by_plans"] == ["PP-2027-02"]


def test_comparison_counts_orders_and_session_orders(sid: str) -> None:
    fid = forecasts.latest(sid)["forecast_id"]
    r = client.get(f"/api/forecasts/{fid}/comparison", headers=h(sid))
    assert r.status_code == 200
    body = r.json()
    arka = next(x for x in body["rows"] if x["sku"] == "FB-ARKA")
    assert arka["actual_qty"] > 0
    assert arka["variance_qty"] == arka["actual_qty"] - arka["forecast_qty"]
    w1 = next(w for w in body["weeks"] if w["sku"] == "FB-ARKA" and w["week"] == 1)
    order = {
        "customer_id": "CUS-EXP",
        "order_channel": "MANUAL",
        "order_date": "2027-02-01",
        "requested_delivery_date": "2027-02-05",
        "lines": [{"sku": "FB-ARKA", "qty": 300, "requested_delivery_date": "2027-02-05"}],
    }
    created = client.post("/api/orders", json=order, headers=h(sid))
    assert created.status_code == 200, created.text
    again = client.get(f"/api/forecasts/{fid}/comparison", headers=h(sid)).json()
    w1b = next(w for w in again["weeks"] if w["sku"] == "FB-ARKA" and w["week"] == 1)
    assert w1b["actual_qty"] == w1["actual_qty"] + 300
    assert w1b["forecast_qty"] == w1["forecast_qty"]


def test_vs_orders_uses_the_snapshot_numbers(sid: str) -> None:
    snap = forecasts.latest(sid)
    body = client.get(f"/api/forecasts/{snap['forecast_id']}/vs-orders?sku=RB-ARKA", headers=h(sid)).json()
    assert [w["forecast_qty"] for w in body["weeks"]] == [ln["forecast_qty"] for ln in snap["lines"] if ln["sku"] == "RB-ARKA"]
    only = client.post("/api/forecasts", json={"skus": ["FB-ARKA"]}, headers=h(sid)).json()
    r = client.get(f"/api/forecasts/{only['forecast_id']}/vs-orders?sku=RB-ARKA", headers=h(sid))
    assert (r.status_code, r.json()["error"]["code"]) == (404, "FV-FC-005")


def test_accuracy_backtest(sid: str) -> None:
    body = client.get("/api/forecasts/accuracy", headers=h(sid)).json()
    assert len(body["rows"]) == 6
    row = client.get("/api/forecasts/accuracy?sku=FB-ARKA", headers=h(sid)).json()["rows"][0]
    assert row["cutoff"] == "2027-01-04"
    assert len(row["weeks"]) == 4
    assert row["mape_pct"] is not None
    assert 0 <= row["mape_pct"] < 100
    assert client.get("/api/forecasts/accuracy?sku=NOPE", headers=h(sid)).json()["error"]["code"] == "FV-DATA-001"


def test_activity_and_reset_clears_snapshots(sid: str) -> None:
    client.post("/api/forecasts", json={}, headers=h(sid))
    act = client.get("/api/forecasts/activity", headers=h(sid)).json()["activity"]
    assert act[0]["event"] == "Forecast generated"
    assert client.post("/api/demo/reset", json={"scenario": "baseline"}, headers=h(sid)).status_code == 200
    assert store().where(forecasts.COLLECTION, "session_id", sid) == []
    assert client.get("/api/forecasts/activity", headers=h(sid)).json()["activity"] == []
    again = client.get("/api/forecasts", headers=h(sid)).json()["snapshots"]
    assert [(s["forecast_id"], s["generated_by"]) for s in again] == [("FC-2027-02-001", "seed")]


def test_python_api_used_by_agents(sid: str) -> None:
    acc = forecasts.accuracy(sid, "FB-ARKA")
    assert (acc["session_id"], [r["sku"] for r in acc["rows"]]) == (sid, ["FB-ARKA"])
    fid = forecasts.latest(sid)["forecast_id"]
    assert forecasts.comparison(sid, fid)["forecast_id"] == fid
    assert store().get(forecasts.COLLECTION, fid) is None
    doc = store().get(forecasts.COLLECTION, f"{sid}:{fid}")
    assert doc is not None
    assert doc["forecast_id"] == fid
