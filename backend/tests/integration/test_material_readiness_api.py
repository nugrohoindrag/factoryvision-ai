import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from app import actions, capacity, forecasts, materials, mrp, plans
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
FEB = {"period_start": "2027-02-01", "period_end": "2027-02-28"}


@pytest.fixture
def sid() -> Iterator[str]:
    s = str(uuid.uuid4())
    yield s
    mrp.clear(s)
    materials.clear(s)
    plans.clear(s)
    capacity.clear(s)
    forecasts.clear(s)
    actions.clear(s)


def h(s: str) -> dict[str, str]:
    return {"X-Session-Id": s}


def _ok(r: Any) -> Any:
    assert r.status_code == 200, r.text
    return r.json()


def _err(r: Any) -> str:
    code: str = r.json()["error"]["code"]
    return code


def _scheduled_plan(sid: str, n: int = 3) -> dict[str, Any]:
    p = plans.create_plan(sid, FEB["period_start"], FEB["period_end"])
    for c in plans.demand_candidates(sid, p["plan_id"])[:n]:
        plans.add_demand(sid, p["plan_id"], c["order_id"])
    plans.generate_work_orders(sid, p["plan_id"])
    plans.schedule(sid, p["plan_id"], time_limit_s=5.0)
    return plans.get_plan(sid, p["plan_id"])


def test_readiness_lists_session_plan_and_oem_pseudo_plan(sid: str) -> None:
    empty = _ok(client.get("/api/materials/readiness", headers=h(sid)))
    assert empty["session_plans"] == 0
    assert [p["plan_id"] for p in empty["plans"]] == ["OEM-2027-02"]
    p = _scheduled_plan(sid)
    out = _ok(client.get("/api/materials/readiness", headers=h(sid)))
    ids = [x["plan_id"] for x in out["plans"]]
    assert p["plan_id"] in ids
    assert "OEM-2027-02" in ids
    k = out["kpis"]
    assert k["plans_monitored"] == len(out["plans"])
    assert k["materials_short"] == sum(x["short_count"] for x in out["plans"])
    assert set(k) == {"readiness_pct", "plans_monitored", "plans_short", "materials_short"}
    row = next(x for x in out["plans"] if x["plan_id"] == p["plan_id"])
    assert row["status"] in {"READY", "PARTIAL", "SHORTAGE"}
    assert row["plan_number"] == p["plan_number"]
    probs = _ok(client.get("/api/materials/readiness?only_problems=true", headers=h(sid)))
    assert all(x["status"] != "READY" for x in probs["plans"])
    assert _err(client.get("/api/materials/readiness?from=2027-02-10&to=2027-02-01", headers=h(sid))) == "FV-MR-003"


def test_plan_check_uses_work_order_dates_and_is_saved(sid: str) -> None:
    p = _scheduled_plan(sid)
    pid = p["plan_id"]
    assert _ok(client.get(f"/api/materials/readiness/plans/{pid}", headers=h(sid)))["status"] == "NOT_CHECKED"
    r = _ok(client.post(f"/api/materials/readiness/plans/{pid}", headers=h(sid)))
    starts: dict[str, str] = {}
    for w in p["work_orders"]:
        starts[w["process"]] = min(starts.get(w["process"], w["planned_start"]), w["planned_start"])[:10]
    resin = next(m for m in r["materials"] if m["material_id"] == "PP-EPDM-T20")
    assert resin["required_date"] == starts["Molding"]
    paint = next(m for m in r["materials"] if m["material_group"] == "Basecoat")
    assert paint["required_date"] == starts["Painting"]
    assert r["required_date"] == min(m["required_date"] for m in r["materials"])
    for m in r["materials"]:
        assert m["available_qty"] == pytest.approx(max(m["on_hand_qty"] - m["reserved_qty"] + m["incoming_counted_qty"] - m["used_before_qty"], 0), abs=1e-3)
        assert m["shortage_qty"] == pytest.approx(max(m["required_qty"] - m["available_qty"], 0), abs=1e-3)
    assert r["readiness_pct"] == round(sum(1 for m in r["materials"] if m["status"] == "READY") / len(r["materials"]) * 100, 1)
    saved = _ok(client.get(f"/api/materials/readiness/plans/{pid}", headers=h(sid)))
    assert saved["checked_at"] == r["checked_at"]
    assert _err(client.get("/api/materials/readiness/plans/nope", headers=h(sid))) == "FV-MR-001"


def test_work_order_check_and_requirements(sid: str) -> None:
    p = _scheduled_plan(sid, n=1)
    pid = p["plan_id"]
    mold = next(w for w in p["work_orders"] if w["process"] == "Molding")
    r = _ok(client.post(f"/api/materials/readiness/plans/{pid}/work-orders/{mold['wo_id']}", headers=h(sid)))
    assert r["wo_id"] == mold["wo_id"]
    assert {m["material_group"] for m in r["materials"]} <= {"Resin", "Masterbatch"}
    assert r["required_date"] == mold["planned_start"][:10]
    assert _err(client.post(f"/api/materials/readiness/plans/{pid}/work-orders/WO-NOPE", headers=h(sid))) == "FV-MR-002"
    req = _ok(client.get(f"/api/materials/requirements?plan_id={pid}", headers=h(sid)))
    assert sum(x["qty"] for x in req["lines"]) == sum(ln["planned_qty"] for ln in p["lines"])
    assert all(x["required_qty"] > 0 for x in req["requirements"])
    oem = _ok(client.get("/api/materials/requirements", headers=h(sid)))
    assert oem["plan_id"] == "OEM-2027-02"


def test_incoming_after_need_date_is_not_counted(sid: str, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _scheduled_plan(sid, n=1)
    real = materials.incoming

    def fake(s: str, material_id: str | None = None) -> list[dict[str, Any]]:
        extra = [
            {"po_id": "PO-EARLY", "material_id": "PP-EPDM-T20", "qty": 1000.0, "expected_date": "2027-01-31"},
            {"po_id": "PO-LATE", "material_id": "PP-EPDM-T20", "qty": 5000.0, "expected_date": "2027-12-31"},
        ]
        return [r for r in [*real(s, material_id), *extra] if material_id is None or r["material_id"] == material_id]

    monkeypatch.setattr(materials, "incoming", fake)
    r = readiness_check(sid, p["plan_id"])
    resin = next(m for m in r["materials"] if m["material_id"] == "PP-EPDM-T20")
    counted = {i["po_id"]: i["counted"] for i in resin["incoming"]}
    assert counted["PO-EARLY"] is True
    assert counted["PO-LATE"] is False
    assert "PO-LATE" in resin["note"]
    assert "arrives after need date" in resin["note"]


def readiness_check(sid: str, pid: str) -> dict[str, Any]:
    out: dict[str, Any] = _ok(client.post(f"/api/materials/readiness/plans/{pid}", headers=h(sid)))
    return out


def test_cancelled_plan_is_excluded(sid: str) -> None:
    p = _scheduled_plan(sid, n=1)
    plans.cancel(sid, p["plan_id"], "test")
    out = _ok(client.get("/api/materials/readiness", headers=h(sid)))
    assert p["plan_id"] not in [x["plan_id"] for x in out["plans"]]
    assert _err(client.post(f"/api/materials/readiness/plans/{p['plan_id']}", headers=h(sid))) == "FV-MR-004"


def test_mrp_run_endpoints(sid: str) -> None:
    assert _ok(client.get("/api/mrp/runs/latest", headers=h(sid))) is None
    p = _scheduled_plan(sid)
    run = _ok(client.post("/api/mrp/runs", json={}, headers=h(sid)))
    assert run["run_id"] == "MRP-2027-02-001"
    assert run["plan_ids"] == [p["plan_id"]]
    nets = [r["net_qty"] for r in run["results"]]
    assert nets == sorted(nets, reverse=True)
    for r in run["results"]:
        assert r["net_qty"] == pytest.approx(max(r["gross_qty"] - r["available_qty"], 0), abs=1e-3)
        assert (r["recommendation"] is None) == (r["net_qty"] <= 0)
    assert run["shortage_materials"] == sum(1 for r in run["results"] if r["net_qty"] > 0)
    oem = _ok(client.post("/api/mrp/runs", json={"plan_ids": ["OEM-2027-02"]}, headers=h(sid)))
    assert oem["run_id"] == "MRP-2027-02-002"
    assert [r["run_id"] for r in _ok(client.get("/api/mrp/runs", headers=h(sid)))["runs"]] == ["MRP-2027-02-002", "MRP-2027-02-001"]
    assert _ok(client.get("/api/mrp/runs/latest", headers=h(sid)))["run_id"] == "MRP-2027-02-002"
    assert _ok(client.get("/api/mrp/runs/MRP-2027-02-001", headers=h(sid)))["results"] == run["results"]
    assert _err(client.get("/api/mrp/runs/MRP-2027-02-099", headers=h(sid))) == "FV-MRP-005"
    bad = client.post("/api/mrp/runs", json={"horizon_start": "2027-02-10", "horizon_end": "2027-02-01"}, headers=h(sid))
    assert bad.status_code == 400
    assert _err(bad) == "FV-MRP-001"
    assert _err(client.post("/api/mrp/runs", json={"plan_ids": ["nope"]}, headers=h(sid))) == "FV-MRP-003"


def test_reservations_and_ledger(sid: str) -> None:
    p = _scheduled_plan(sid, n=1)
    mold = next(w for w in p["work_orders"] if w["process"] == "Molding")
    body = {"plan_id": p["plan_id"], "wo_id": mold["wo_id"]}
    got = _ok(client.post("/api/materials/reservations", json=body, headers=h(sid)))["reservations"]
    assert got
    assert all(r["status"] == "Active" for r in got)
    again = _ok(client.post("/api/materials/reservations", json=body, headers=h(sid)))["reservations"]
    assert {r["reservation_id"] for r in again} == {r["reservation_id"] for r in got}
    resin = next(r for r in got if r["material_id"] == "PP-EPDM-T20")
    stock = materials.stock(sid, material_id="PP-EPDM-T20")
    assert sum(s["reserved_qty"] for s in stock) >= resin["qty"] - 0.1
    txns = [t for t in materials.transactions(sid, "PP-EPDM-T20") if t["type"] in ("Reserve", "Release")]
    assert [t["type"] for t in txns] == ["Reserve"]
    rel = _ok(client.post(f"/api/materials/reservations/{resin['reservation_id']}/release", headers=h(sid)))
    assert rel["status"] == "Released"
    assert [r["reservation_id"] for r in _ok(client.get("/api/materials/reservations?status=Released", headers=h(sid)))["reservations"]] == [
        resin["reservation_id"]
    ]
    assert _err(client.post("/api/materials/reservations/RSV-99999/release", headers=h(sid))) == "FV-MR-006"
    assert _err(client.get("/api/materials/reservations?status=Nope", headers=h(sid))) == "FV-MR-007"


def test_consumption_variance(sid: str) -> None:
    hist = _ok(client.get("/api/materials/consumption", headers=h(sid)))
    assert hist["rows"]
    assert hist["tolerance"] == 0.0001
    assert "tolerance_pct" in hist
    assert hist["source"]
    assert sum(hist["summary"].values()) == len(hist["rows"])
    for r in hist["rows"]:
        assert r["variance_qty"] == pytest.approx(r["actual_qty"] - r["planned_qty"], abs=1e-3)
        assert r["status"] in {"NORMAL", "OVER_CONSUMPTION", "UNDER_CONSUMPTION"}
    p = _scheduled_plan(sid, n=1)
    mold = next(w for w in p["work_orders"] if w["process"] == "Molding")
    wo = _ok(client.get(f"/api/materials/consumption?wo_id={mold['wo_id']}", headers=h(sid)))
    assert [r["material_id"] for r in wo["rows"]] == ["PP-EPDM-T20"]
    assert _err(client.get("/api/materials/consumption?wo_id=WO-NOPE", headers=h(sid))) == "FV-MR-002"


def test_reset_clears_and_summary_has_readiness(sid: str) -> None:
    _scheduled_plan(sid, n=1)
    _ok(client.post("/api/mrp/runs", json={}, headers=h(sid)))
    _ok(client.get("/api/materials/readiness", headers=h(sid)))
    _ok(client.post("/api/demo/reset", json={}, headers=h(sid)))
    assert _ok(client.get("/api/mrp/runs", headers=h(sid)))["runs"] == []
    assert _ok(client.get("/api/materials/readiness/plans/OEM-2027-02", headers=h(sid)))["status"] == "NOT_CHECKED"
    block = _ok(client.get("/api/modules/material/summary", headers=h(sid)))["readiness"]
    assert block["plan_id"] == "OEM-2027-02"
    assert block["status"] in {"READY", "PARTIAL", "SHORTAGE"}
