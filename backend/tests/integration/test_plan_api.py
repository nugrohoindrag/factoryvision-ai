import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from app import actions, capacity, forecasts, plans, pubsub
from app.errors import FVError
from app.main import app
from app.store import store
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
FEB = {"period_start": "2027-02-01", "period_end": "2027-02-28"}


@pytest.fixture
def sid() -> Iterator[str]:
    s = str(uuid.uuid4())
    yield s
    plans.clear(s)
    capacity.clear(s)
    forecasts.clear(s)
    actions.clear(s)


def h(s: str) -> dict[str, str]:
    return {"X-Session-Id": s}


def _new(sid: str, **body: Any) -> dict[str, Any]:
    r = client.post("/api/plans", json=body or FEB, headers=h(sid))
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def _cands(sid: str, pid: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = client.get(f"/api/plans/{pid}/demand-candidates", headers=h(sid)).json()["candidates"]
    return out


def _add(sid: str, pid: str, order_id: str, qty: int | None = None) -> Any:
    return client.post(f"/api/plans/{pid}/demand", json={"order_id": order_id, "qty": qty}, headers=h(sid))


def _err(r: Any) -> str:
    code: str = r.json()["error"]["code"]
    return code


def _ready_plan(sid: str, n: int = 6) -> dict[str, Any]:
    p = _new(sid)
    for c in _cands(sid, p["plan_id"])[:n]:
        assert _add(sid, p["plan_id"], c["order_id"]).status_code == 200
    assert client.post(f"/api/plans/{p['plan_id']}/work-orders", headers=h(sid)).status_code == 200
    assert client.post(f"/api/plans/{p['plan_id']}/schedule", headers=h(sid)).status_code == 200
    return p


def test_create_validates_period_and_numbers_per_month(sid: str) -> None:
    bad = client.post("/api/plans", json={"period_start": "2027-02-10", "period_end": "2027-02-01"}, headers=h(sid))
    assert bad.status_code == 400
    assert _err(bad) == "FV-PP-002"
    a, b = _new(sid), _new(sid)
    assert (a["plan_number"], b["plan_number"]) == ("PP-2027-02", "PP-2027-02-002")
    assert a["status"] == "Draft"
    assert a["wizard_step"] == 1
    assert a["utilization_pct"] == 80.0
    assert a["forecast_id"]
    assert a["readiness"]["resume_step"] == 1
    other = str(uuid.uuid4())
    try:
        assert _new(other)["plan_number"] == "PP-2027-02"
        assert client.get(f"/api/plans/{a['plan_id']}", headers=h(other)).status_code == 404
    finally:
        plans.clear(other)
    listed = client.get("/api/plans", headers=h(sid)).json()["plans"]
    assert {p["plan_number"] for p in listed} == {"PP-2027-02", "PP-2027-02-002"}
    assert {"capacity_status", "line_count", "total_planned", "age_days", "ageing"} <= set(listed[0])
    assert client.get("/api/plans", params={"status": "Confirmed"}, headers=h(sid)).json()["plans"] == []
    assert client.get("/api/plans", params={"period_from": "2027-03-01"}, headers=h(sid)).json()["plans"] == []


def test_demand_rules_aggregate_per_product_and_move_order_planned_qty(sid: str) -> None:
    p = _new(sid)
    pid = p["plan_id"]
    cands = _cands(sid, pid)
    assert cands
    assert all(c["remaining_qty"] > 0 for c in cands)
    first = cands[0]
    assert _err(_add(sid, pid, "SO-NOPE")) == "FV-PP-004"
    assert _err(_add(sid, pid, first["order_id"], 0)) == "FV-PP-006"
    over = _add(sid, pid, first["order_id"], first["remaining_qty"] + 1)
    assert _err(over) == "FV-PP-008"
    assert "already planned 0" in over.json()["error"]["detail"]["fields"][0]["message"]
    r = _add(sid, pid, first["order_id"], 10).json()
    assert r["status"] == "Planning"
    ln = r["lines"][0]
    assert (ln["demand_qty"], ln["planned_qty"], ln["priority"]) == (10, 10, 1)
    assert ln["capacity"]["status"] in (capacity.WITHIN, capacity.ADDITIONAL, capacity.UP)
    assert _err(_add(sid, pid, first["order_id"])) == "FV-PP-009"
    same_sku = [c for c in cands[1:] if c["sku"] == first["sku"]]
    if same_sku:
        r = _add(sid, pid, same_sku[0]["order_id"]).json()
        assert len([x for x in r["lines"] if x["sku"] == first["sku"]]) == 1
        line = next(x for x in r["lines"] if x["sku"] == first["sku"])
        assert line["demand_qty"] == 10 + same_sku[0]["remaining_qty"]
        assert line["required_date"] == min(first["requested_delivery_date"], same_sku[0]["requested_delivery_date"])
    order = client.get(f"/api/orders/{first['order_number']}", headers=h(sid)).json()
    oline = next(x for x in order["lines"] if x["order_id"] == first["order_id"])
    assert oline["planned_qty"] == 10
    assert order["production_plans"][0]["plan_number"] == p["plan_number"]
    other = _new(sid)
    assert next(c for c in _cands(sid, other["plan_id"]) if c["order_id"] == first["order_id"])["remaining_qty"] == first["remaining_qty"] - 10
    rest = _add(sid, other["plan_id"], first["order_id"]).json()
    assert rest["lines"][0]["demand_qty"] == first["remaining_qty"] - 10
    full = _add(sid, pid, first["order_id"])
    assert _err(full) in ("FV-PP-007", "FV-PP-009")
    done = client.get(f"/api/orders/{first['order_number']}", headers=h(sid)).json()
    assert next(x for x in done["lines"] if x["order_id"] == first["order_id"])["status"] == "Planned"
    breakdown = client.get(f"/api/plans/{pid}/demand", headers=h(sid)).json()["lines"]
    assert breakdown[0]["sources"][0]["order_id"] == first["order_id"]
    r = client.delete(f"/api/plans/{pid}/demand/{first['order_id']}", headers=h(sid))
    assert r.status_code == 200
    assert client.delete(f"/api/plans/{pid}/demand/{first['order_id']}", headers=h(sid)).status_code == 404


def test_cancelled_order_cannot_be_planned(sid: str, monkeypatch: pytest.MonkeyPatch) -> None:
    pid = _new(sid)["plan_id"]
    c = _cands(sid, pid)[-1]
    monkeypatch.setitem(pubsub._handlers, pubsub.AGENT_COMMANDS, lambda _m: {"ok": True})
    r = client.post(f"/api/orders/{c['order_number']}/cancel", json={"reason": "customer cancelled"}, headers=h(sid))
    if r.status_code != 200:
        pytest.skip("order could not be cancelled in this dataset")
    assert _err(_add(sid, pid, c["order_id"])) == "FV-PP-005"


def test_line_edits_validate_and_recompute_capacity(sid: str) -> None:
    pid = _new(sid)["plan_id"]
    c = _cands(sid, pid)[0]
    line = _add(sid, pid, c["order_id"]).json()["lines"][0]
    url = f"/api/plans/{pid}/lines/{line['line_id']}"
    assert _err(client.patch(url, json={"planned_qty": -1}, headers=h(sid))) == "FV-PP-012"
    assert _err(client.patch(url, json={"priority": 6}, headers=h(sid))) == "FV-PP-012"
    assert _err(client.patch(f"/api/plans/{pid}/lines/nope", json={"priority": 2}, headers=h(sid))) == "FV-PP-011"
    big = client.patch(url, json={"planned_qty": 900000, "priority": 3}, headers=h(sid)).json()["lines"][0]
    assert big["priority"] == 3
    assert big["capacity"]["status"] == capacity.UP
    assert big["capacity"]["gap"] == 900000 - big["capacity"]["total"]
    small = client.patch(url, json={"planned_qty": 1}, headers=h(sid)).json()["lines"][0]
    assert small["capacity"]["status"] == capacity.WITHIN


def test_wizard_steps_are_gated(sid: str) -> None:
    pid = _new(sid)["plan_id"]
    r = client.post(f"/api/plans/{pid}/step", json={"step": 3}, headers=h(sid))
    assert _err(r) == "FV-PP-013"
    assert _err(client.post(f"/api/plans/{pid}/step", json={"step": 9}, headers=h(sid))) == "FV-PP-014"
    _add(sid, pid, _cands(sid, pid)[0]["order_id"])
    assert client.post(f"/api/plans/{pid}/step", json={"step": 3}, headers=h(sid)).json()["wizard_step"] == 3
    assert client.post(f"/api/plans/{pid}/step", json={"step": 1}, headers=h(sid)).json()["wizard_step"] == 1


def test_work_order_generation_no_duplicates_and_chain(sid: str) -> None:
    pid = _new(sid)["plan_id"]
    assert _err(client.post(f"/api/plans/{pid}/work-orders", headers=h(sid))) == "FV-PP-015"
    cands = _cands(sid, pid)
    for c in cands[:3]:
        _add(sid, pid, c["order_id"])
    plan = client.get(f"/api/plans/{pid}", headers=h(sid)).json()
    zero = plan["lines"][-1]
    if len(plan["lines"]) > 1:
        client.patch(f"/api/plans/{pid}/lines/{zero['line_id']}", json={"planned_qty": 0}, headers=h(sid))
    g = client.post(f"/api/plans/{pid}/work-orders", headers=h(sid)).json()
    live = [ln for ln in plan["lines"] if not (len(plan["lines"]) > 1 and ln["line_id"] == zero["line_id"])]
    assert len(g["created"]) == 3 * len(live)
    if len(plan["lines"]) > 1:
        assert g["skipped_line_ids"] == [zero["line_id"]]
    for w in g["created"]:
        assert w["status"] == "Draft"
        assert w["wo_id"].startswith("WO-270201-")
        assert w["compatible_machines"]
    by = {w["wo_id"]: w for w in g["created"]}
    for w in g["created"]:
        if w["process"] == "Molding":
            assert w["predecessor_wo_id"] is None
            assert w["compatible_molds"] == [f"MLD-{w['sku']}"]
        else:
            assert by[w["predecessor_wo_id"]]["routing_step"] == w["routing_step"] - 1
    again = client.post(f"/api/plans/{pid}/work-orders", headers=h(sid)).json()
    assert again["created"] == []
    assert len(again["existed"]) == len(g["created"])
    assert len(again["work_orders"]) == len(g["created"])


def test_invalid_routing_is_all_or_nothing(sid: str) -> None:
    pid = _new(sid)["plan_id"]
    for c in _cands(sid, pid)[:6]:
        _add(sid, pid, c["order_id"])
    plan = client.get(f"/api/plans/{pid}", headers=h(sid)).json()
    skus = [ln["sku"] for ln in plan["lines"]]
    routes = {s: plans.routing(s) for s in skus}
    routes[skus[-1]] = [{"sequence": 1, "process": "Molding", "resources": ["IMM-01"]}, {"sequence": 3, "process": "Assembly", "resources": ["ASM-01"]}]
    with pytest.raises(FVError) as e:
        plans.generate_work_orders(sid, pid, routes)
    assert e.value.code == "FV-PP-016"
    detail: Any = e.value.detail
    assert detail["problems"][0]["code"] == "SEQUENCE_NOT_CONTINUOUS"
    assert client.get(f"/api/plans/{pid}", headers=h(sid)).json()["work_orders"] == []


def test_schedule_fills_work_orders_within_ten_seconds(sid: str) -> None:
    pid = _new(sid)["plan_id"]
    assert _err(client.post(f"/api/plans/{pid}/schedule", headers=h(sid))) == "FV-PP-023"
    for c in _cands(sid, pid):
        if c["in_period"]:
            _add(sid, pid, c["order_id"])
    client.post(f"/api/plans/{pid}/work-orders", headers=h(sid))
    t = time.time()
    r = client.post(f"/api/plans/{pid}/schedule", headers=h(sid)).json()
    assert time.time() - t < 10
    assert r["status"] in ("OPTIMAL", "FEASIBLE")
    assert r["solve_seconds"] < 10
    for w in r["work_orders"]:
        assert w["planned_start"] < w["planned_end"]
        assert w["machine_id"] in w["compatible_machines"]
        assert w["shift"] in ("A", "B", "C")
        assert w["sequence"] >= 1
        assert (w["mold_id"] is not None) == (w["process"] == "Molding")
    plan = client.get(f"/api/plans/{pid}", headers=h(sid)).json()
    assert plan["readiness"]["resume_step"] == 6


def test_work_order_edit_validation_and_confirm_checklist(sid: str) -> None:
    pid = _ready_plan(sid, 2)["plan_id"]
    wos = client.get(f"/api/plans/{pid}", headers=h(sid)).json()["work_orders"]
    mold = next(w for w in wos if w["process"] == "Molding")
    url = f"/api/plans/{pid}/work-orders/{mold['wo_id']}"
    bad_machine = next(m for m in ("IMM-01", "IMM-04") if m not in mold["compatible_machines"])
    assert _err(client.patch(url, json={"machine_id": bad_machine}, headers=h(sid))) == "FV-PP-018"
    assert _err(client.patch(url, json={"planned_end": mold["planned_start"]}, headers=h(sid))) == "FV-PP-018"
    assert _err(client.patch(url, json={"shift": "Z"}, headers=h(sid))) == "FV-PP-018"
    assert _err(client.patch(f"/api/plans/{pid}/work-orders/WO-NOPE", json={}, headers=h(sid))) == "FV-PP-017"
    other = (
        next(w for w in wos if w["process"] == "Molding" and w["wo_id"] != mold["wo_id"] and w["machine_id"] != mold["machine_id"]) if len(wos) > 3 else None
    )
    if other and other["machine_id"] in mold["compatible_machines"]:
        client.patch(
            url, json={"machine_id": other["machine_id"], "planned_start": other["planned_start"], "planned_end": other["planned_end"]}, headers=h(sid)
        )
        clash = client.post(f"/api/plans/{pid}/work-orders/confirm", headers=h(sid))
        assert _err(clash) == "FV-PP-020"
        assert any("conflict" in r for p in clash.json()["error"]["detail"]["work_orders"] for r in p["reasons"])
        client.post(f"/api/plans/{pid}/schedule", headers=h(sid))
    ok = client.post(f"/api/plans/{pid}/work-orders/confirm", headers=h(sid)).json()
    assert ok["status"] == "Ready"
    assert all(w["confirmed"] and w["status"] == "Confirmed" for w in ok["work_orders"])
    client.patch(url, json={"shift": "B"}, headers=h(sid))
    assert client.get(f"/api/plans/{pid}", headers=h(sid)).json()["status"] == "Planning"


def test_confirm_preconditions_lock_and_event(sid: str) -> None:
    empty = _new(sid)["plan_id"]
    assert _err(client.post(f"/api/plans/{empty}/confirm", headers=h(sid))) == "FV-PP-021"
    pid = _new(sid)["plan_id"]
    c = _cands(sid, pid)[0]
    line = _add(sid, pid, c["order_id"]).json()["lines"][0]
    assert _err(client.post(f"/api/plans/{pid}/confirm", headers=h(sid))) == "FV-PP-023"
    client.post(f"/api/plans/{pid}/work-orders", headers=h(sid))
    assert _err(client.post(f"/api/plans/{pid}/confirm", headers=h(sid))) == "FV-PP-024"
    client.patch(f"/api/plans/{pid}/lines/{line['line_id']}", json={"planned_qty": 900000}, headers=h(sid))
    up = client.post(f"/api/plans/{pid}/confirm", headers=h(sid))
    assert _err(up) == "FV-PP-022"
    assert up.json()["error"]["detail"]["skus"] == [line["sku"]]
    client.patch(f"/api/plans/{pid}/lines/{line['line_id']}", json={"planned_qty": line["planned_qty"]}, headers=h(sid))
    client.post(f"/api/plans/{pid}/schedule", headers=h(sid))
    client.post(f"/api/plans/{pid}/work-orders/confirm", headers=h(sid))
    done = client.post(f"/api/plans/{pid}/confirm", headers=h(sid)).json()
    assert done["status"] == "Confirmed"
    assert done["confirmed_by"]
    assert done["wizard_step"] == 6
    assert all(ln["status"] == "Released" for ln in done["lines"])
    assert _err(client.post(f"/api/plans/{pid}/confirm", headers=h(sid))) == "FV-PP-025"
    assert _err(_add(sid, pid, _cands(sid, pid)[0]["order_id"])) == "FV-PP-003"
    assert _err(client.post(f"/api/plans/{pid}/work-orders", headers=h(sid))) == "FV-PP-003"
    assert client.post(f"/api/plans/{pid}/step", json={"step": 2}, headers=h(sid)).json()["wizard_step"] == 6
    for module in ("mes", "material", "delivery"):
        down = client.get(f"/api/modules/{module}/summary", headers=h(sid)).json()["production_plans"]
        assert "ProductionPlanConfirmed" in [e["event_type"] for e in down["events"]]
        assert down["committed_plans"][0]["plan_id"] == pid
    mat = client.get("/api/modules/material/summary", headers=h(sid)).json()["production_plans"]
    assert mat["requirements"]
    mes = client.get("/api/modules/mes/summary", headers=h(sid)).json()["production_plans"]
    assert {w["plan_id"] for w in mes["work_orders"]} == {pid}


def test_cancel_rules_and_work_order_progress(sid: str) -> None:
    pid = _ready_plan(sid, 1)["plan_id"]
    c = client.get(f"/api/plans/{pid}", headers=h(sid)).json()["demand"][0]
    assert _err(client.post(f"/api/plans/{pid}/cancel", json={"reason": " "}, headers=h(sid))) == "FV-PP-026"
    wos = client.get(f"/api/plans/{pid}", headers=h(sid)).json()["work_orders"]
    url = f"/api/plans/{pid}/work-orders/{wos[0]['wo_id']}/status"
    assert _err(client.post(url, json={"status": "In Production"}, headers=h(sid))) == "FV-PP-019"
    client.post(f"/api/plans/{pid}/work-orders/confirm", headers=h(sid))
    client.post(f"/api/plans/{pid}/confirm", headers=h(sid))
    assert client.post(url, json={"status": "In Production"}, headers=h(sid)).json()["status"] == "In Execution"
    assert _err(client.post(url, json={"status": "Released"}, headers=h(sid))) == "FV-PP-019"
    blocked = client.post(f"/api/plans/{pid}/cancel", json={"reason": "volume moved"}, headers=h(sid))
    assert _err(blocked) == "FV-PP-027"
    assert blocked.json()["error"]["detail"]["count"] == 1
    pid2 = _new(sid)["plan_id"]
    other = next(x for x in _cands(sid, pid2) if x["order_id"] != c["order_id"])
    _add(sid, pid2, other["order_id"])
    order = client.get(f"/api/orders/{other['order_number']}", headers=h(sid)).json()
    assert next(x for x in order["lines"] if x["order_id"] == other["order_id"])["planned_qty"] == other["remaining_qty"]
    r = client.post(f"/api/plans/{pid2}/cancel", json={"reason": "customer moved the volume"}, headers=h(sid)).json()
    assert (r["status"], r["cancel_reason"]) == ("Cancelled", "customer moved the volume")
    assert _err(client.post(f"/api/plans/{pid2}/cancel", json={"reason": "again"}, headers=h(sid))) == "FV-PP-027"
    after = client.get(f"/api/orders/{other['order_number']}", headers=h(sid)).json()
    assert next(x for x in after["lines"] if x["order_id"] == other["order_id"])["planned_qty"] == 0
    events = [e["event_type"] for e in plans.events(sid)]
    assert "ProductionPlanCancelled" in events


def test_forecast_demand(sid: str) -> None:
    pid = _new(sid)["plan_id"]
    r = client.post(f"/api/plans/{pid}/forecast-demand", json={"sku": "FB-ARKA"}, headers=h(sid))
    assert r.status_code == 200
    ln = r.json()["lines"][0]
    assert ln["forecast_qty"] > 0
    assert ln["planned_qty"] == ln["forecast_qty"]
    assert ln["forecast_line_ids"]
    assert _err(client.post(f"/api/plans/{pid}/forecast-demand", json={"sku": "FB-ARKA", "qty": ln["forecast_qty"] + 1}, headers=h(sid))) == "FV-PP-029"
    assert _err(client.post(f"/api/plans/{pid}/forecast-demand", json={"sku": "NOPE"}, headers=h(sid))) == "FV-PP-029"
    gone = client.post(f"/api/plans/{pid}/forecast-demand", json={"sku": "FB-ARKA", "qty": 0}, headers=h(sid)).json()
    assert gone["lines"] == []
    snap = forecasts.get_snapshot(sid, gone["forecast_id"])
    assert "PP-2027-02" in snap["used_by_plans"]


def test_capacity_page_snapshots_and_settings(sid: str) -> None:
    page = client.get("/api/capacity", headers=h(sid)).json()
    assert page["period_start"] == "2027-02-01"
    assert {"total", "planning", "buffer", "utilization_pct", "gap"} <= set(page["kpis"])
    assert page["formulas"]["status"]
    row = page["rows"][0]
    assert {"sku", "demand_qty", "total", "planning", "buffer", "gap", "utilization_pct", "status", "contributions", "uncomputed"} <= set(row)
    assert _err(client.get("/api/capacity", params={"period_start": "2027-02-10", "period_end": "2027-02-01"}, headers=h(sid))) == "FV-CAP-001"
    a = client.post("/api/capacity/recalculate", json=FEB, headers=h(sid)).json()
    assert a["snapshot_id"] == a["cap_id"] == "CAP-2027-02-001"
    b = client.post("/api/capacity/recalculate", json={"snapshot_id": a["cap_id"]}, headers=h(sid)).json()
    assert b["snapshot_id"] == "CAP-2027-02-002"
    assert b["supersedes"] == ["CAP-2027-02-001"]
    snaps = client.get("/api/capacity/snapshots", headers=h(sid)).json()["snapshots"]
    assert [(s["snapshot_id"], s["status"]) for s in snaps] == [("CAP-2027-02-002", "Computed"), ("CAP-2027-02-001", "Superseded")]
    assert client.get("/api/capacity/snapshots/CAP-2027-02-001", headers=h(sid)).json()["superseded_by"] == "CAP-2027-02-002"
    assert _err(client.get("/api/capacity/snapshots/CAP-NOPE", headers=h(sid))) == "FV-CAP-002"
    assert _err(client.put("/api/capacity/settings", json={"planning_utilization_pct": 120}, headers=h(sid))) == "FV-CAP-003"
    assert client.put("/api/capacity/settings", json={"planning_utilization_pct": 70}, headers=h(sid)).json()["planning_utilization_pct"] == 70
    assert _new(sid)["utilization_pct"] == 70
    tight = client.post("/api/capacity/recalculate", json={**FEB, "planning_utilization_pct": 1}, headers=h(sid)).json()
    assert tight["kpis"]["planning"] < page["kpis"]["planning"]


def test_capacity_gap_event(sid: str) -> None:
    pid = _new(sid)["plan_id"]
    c = _cands(sid, pid)[0]
    line = _add(sid, pid, c["order_id"]).json()["lines"][0]
    client.patch(f"/api/plans/{pid}/lines/{line['line_id']}", json={"planned_qty": 900000}, headers=h(sid))
    snap = plans.recalculate_capacity(sid, "2027-02-01", "2027-02-01")
    if snap["gap_events"]:
        assert "CapacityGapDetected" in [e["event_type"] for e in plans.events(sid)]
    assert all(g["status"] == capacity.UP for g in snap["gap_events"])


def test_replan_builds_a_new_agent_plan_with_capacity_delta(sid: str) -> None:
    first = _new(sid)
    _add(sid, first["plan_id"], _cands(sid, first["plan_id"])[0]["order_id"])
    r = plans.replan(sid, reason="forecast moved", demand_change_pct={"FB-ARKA": 40})
    assert r["origin"] == "agent"
    assert r["plan_number"] == "PP-2027-02-002"
    assert r["replan_reason"] == "forecast moved"
    assert r["demand_change_pct"] == {"FB-ARKA": 40}
    assert r["lines"]
    assert r["compared_with"] == "PP-2027-02"
    assert set(r["capacity_delta"]) == {ln["sku"] for ln in r["lines"]}
    arka = next(ln for ln in r["lines"] if ln["sku"] == "FB-ARKA")
    assert arka["planned_qty"] == round((arka["demand_qty"] + arka["forecast_qty"]) * 1.4)
    assert plans.get_plan(sid, r["plan_id"])["demand_change_pct"] == {"FB-ARKA": 40}
    assert plans.list_plans(sid)[0]["plan_number"] == "PP-2027-02-002"


def test_demo_reset_clears_plans(sid: str) -> None:
    _ready_plan(sid, 1)
    client.post("/api/capacity/recalculate", json=FEB, headers=h(sid))
    assert client.post("/api/demo/reset", json={}, headers=h(sid)).status_code == 200
    assert client.get("/api/plans", headers=h(sid)).json()["plans"] == []
    assert client.get("/api/capacity/snapshots", headers=h(sid)).json()["snapshots"] == []
    assert store().where(plans.WOS, "session_id", sid) == []
    assert store().where(plans.EVENTS, "session_id", sid) == []
