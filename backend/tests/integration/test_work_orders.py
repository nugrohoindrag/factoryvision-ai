"""Work Orders and the Production Board: the shop-floor lifecycle of a confirmed plan's work orders."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from app import actions, capacity, forecasts, plans
from app.main import app
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


def _err(r: Any) -> str:
    code: str = r.json()["error"]["code"]
    return code


def _plan(sid: str, skip: int = 0, *, confirm: bool = True) -> str:
    """A plan with one order line (three work orders: molding, painting, assembly), scheduled and optionally confirmed."""
    pid: str = client.post("/api/plans", json=FEB, headers=h(sid)).json()["plan_id"]
    cands = client.get(f"/api/plans/{pid}/demand-candidates", headers=h(sid)).json()["candidates"]
    assert client.post(f"/api/plans/{pid}/demand", json={"order_id": cands[skip]["order_id"]}, headers=h(sid)).status_code == 200
    assert client.post(f"/api/plans/{pid}/work-orders", headers=h(sid)).status_code == 200
    assert client.post(f"/api/plans/{pid}/schedule", headers=h(sid)).status_code == 200
    if confirm:
        assert client.post(f"/api/plans/{pid}/work-orders/confirm", headers=h(sid)).status_code == 200
        r = client.post(f"/api/plans/{pid}/confirm", headers=h(sid))
        assert r.status_code == 200, r.text
    return pid


def _wos(sid: str, pid: str) -> dict[str, dict[str, Any]]:
    rows = client.get(f"/api/work-orders?plan_id={pid}", headers=h(sid)).json()["work_orders"]
    return {w["process"]: w for w in rows if not w["parent_wo_id"]}


def _move(sid: str, wo_id: str, to: str, reason: str = "") -> Any:
    return client.post(f"/api/work-orders/{wo_id}/transition", json={"to": to, "reason": reason}, headers=h(sid))


def _output(sid: str, wo_id: str, good: int, reject: int = 0, scrap: int = 0) -> Any:
    return client.post(f"/api/work-orders/{wo_id}/output", json={"output_qty": good, "reject_qty": reject, "scrap_qty": scrap}, headers=h(sid))


def test_list_shows_the_chain_and_counts(sid: str) -> None:
    pid = _plan(sid)
    d = client.get("/api/work-orders", headers=h(sid)).json()
    assert d["total"] == 3
    assert d["counts"]["Confirmed"] == 3
    wos = _wos(sid, pid)
    assert wos["Painting"]["predecessor_wo_id"] == wos["Molding"]["wo_id"]
    assert wos["Painting"]["available_qty"] == 0
    assert wos["Molding"]["available_qty"] is None
    assert all(w["executing"] for w in wos.values())
    one = client.get(f"/api/work-orders/{wos['Painting']['wo_id']}", headers=h(sid)).json()
    assert one["predecessors"][0]["wo_id"] == wos["Molding"]["wo_id"]
    assert one["successors"][0]["wo_id"] == wos["Assembly"]["wo_id"]
    assert one["demand"]
    assert {t["to"] for t in one["transitions"]} == {"Released", "In Production", "Cancelled"}
    assert client.get("/api/work-orders?q=nothing-like-this", headers=h(sid)).json()["work_orders"] == []


def _strict(sid: str, *, on: bool) -> None:
    assert client.put("/api/capacity/settings", json={"strict_process_sequence": on}, headers=h(sid)).status_code == 200


def test_lifecycle_guards_name_every_unmet_condition(sid: str) -> None:
    pid = _plan(sid)
    w = _wos(sid, pid)
    mold, paint = w["Molding"]["wo_id"], w["Painting"]["wo_id"]
    _strict(sid, on=True)
    r = _move(sid, paint, "In Production")
    assert (r.status_code, _err(r)) == (409, "FV-PP-019")
    assert any(f"Strict process sequence is on: the earlier process ({mold})" in x for x in r.json()["error"]["detail"]["reasons"])
    assert _move(sid, mold, "Released").json()["status"] == "Released"
    started = _move(sid, mold, "In Production").json()
    assert started["status"] == "In Production"
    assert started["actual_start"] == "2027-02-01T07:00+07:00"
    assert _err(_move(sid, paint, "In Production")) == "FV-PP-019"  # strict: started is not enough, it must be completed
    _strict(sid, on=False)  # off allows any order (capacity-decisions.md D2)
    r = _move(sid, mold, "Completed")
    assert any("No output reported" in x for x in r.json()["error"]["detail"]["reasons"])
    assert _output(sid, mold, 120, reject=3, scrap=1).json()["output_qty"] == 120
    assert _move(sid, paint, "In Production").json()["status"] == "In Production"
    r = _output(sid, paint, 100, reject=25)
    assert (r.status_code, r.json()["error"]["detail"]["fields"][0]["field"]) == (400, "output_qty")
    assert "Only 120 pcs" in r.json()["error"]["message"]
    assert _output(sid, paint, 110, reject=10).json()["available_qty"] == 0
    done = _move(sid, mold, "Completed").json()
    assert done["status"] == "Completed"
    assert done["actual_end"]
    assert [x["action"] for x in done["history"]][:2] == ["Complete", "Output reported"]
    assert client.get(f"/api/plans/{pid}", headers=h(sid)).json()["status"] == "In Execution"
    r = _move(sid, mold, "Released")
    assert "not a step" in r.json()["error"]["message"]


def test_cancel_needs_a_reason_and_recall_goes_back(sid: str) -> None:
    pid = _plan(sid)
    asm = _wos(sid, pid)["Assembly"]["wo_id"]
    assert _move(sid, asm, "Released").status_code == 200
    assert _move(sid, asm, "Confirmed").json()["status"] == "Confirmed"
    r = _move(sid, asm, "Cancelled", "x")
    assert "reason" in r.json()["error"]["message"]
    out = _move(sid, asm, "Cancelled", "Customer moved the call-off").json()
    assert (out["status"], out["cancel_reason"]) == ("Cancelled", "Customer moved the call-off")


def test_plan_completes_when_every_work_order_is_done(sid: str) -> None:
    pid = _plan(sid)
    w = _wos(sid, pid)
    for proc in ("Molding", "Painting", "Assembly"):
        assert _move(sid, w[proc]["wo_id"], "In Production").status_code == 200, proc
        assert _output(sid, w[proc]["wo_id"], 50).status_code == 200
    _move(sid, w["Assembly"]["wo_id"], "Completed")
    _move(sid, w["Painting"]["wo_id"], "Completed")
    assert client.get(f"/api/plans/{pid}", headers=h(sid)).json()["status"] == "In Execution"
    _move(sid, w["Molding"]["wo_id"], "Completed")
    assert client.get(f"/api/plans/{pid}", headers=h(sid)).json()["status"] == "Completed"


def test_open_plan_work_orders_belong_to_the_wizard(sid: str) -> None:
    pid = _plan(sid, confirm=False)
    wid = next(iter(_wos(sid, pid).values()))["wo_id"]
    r = _move(sid, wid, "Released")
    assert _err(r) == "FV-PP-019"
    assert "plan wizard" in r.json()["error"]["message"]
    r = client.post("/api/production-board/dispatch", json={"action": "REPRIORITISE", "wo_id": wid, "priority": 2}, headers=h(sid))
    assert (r.status_code, _err(r)) == (409, "FV-WO-003")


def test_the_plan_status_endpoint_uses_the_same_guards(sid: str) -> None:
    pid = _plan(sid)
    paint = _wos(sid, pid)["Painting"]["wo_id"]
    _strict(sid, on=True)
    r = client.post(f"/api/plans/{pid}/work-orders/{paint}/status", json={"status": "In Production"}, headers=h(sid))
    assert _err(r) == "FV-PP-019"
    assert r.json()["error"]["detail"]["reasons"]


def test_split_divides_exactly_and_runs_parts_in_parallel_on_other_cells(sid: str) -> None:
    pid = _plan(sid)
    asm = _wos(sid, pid)["Assembly"]
    qty, url = asm["qty"], f"/api/work-orders/{asm['wo_id']}/split"
    r = client.post(url, json={"parts": [{"qty": qty - 1, "machine_id": "ASM-01"}, {"qty": 2, "machine_id": "ASM-02"}]}, headers=h(sid))
    assert (r.status_code, r.json()["error"]["detail"]["fields"][0]["field"]) == (400, "parts")
    r = client.post(url, json={"parts": [{"qty": qty - 1, "machine_id": "ASM-01"}, {"qty": 1, "machine_id": "IMM-04"}]}, headers=h(sid))
    assert r.json()["error"]["detail"]["fields"][0]["field"] == "parts[1].machine_id"
    half = qty // 2
    d = client.post(url, json={"parts": [{"qty": half, "machine_id": "ASM-01"}, {"qty": qty - half, "machine_id": "ASM-02"}]}, headers=h(sid)).json()
    a, b = d["children_detail"]
    assert (a["wo_id"], b["wo_id"]) == (f"{asm['wo_id']}-A", f"{asm['wo_id']}-B")
    assert a["qty"] + b["qty"] == qty
    assert a["planned_start"] == b["planned_start"]  # different cells run side by side
    assert _err(client.post(url, json={"parts": [{"qty": 1}, {"qty": qty - 1}]}, headers=h(sid))) == "FV-WO-002"
    assert "act on its parts" in _move(sid, asm["wo_id"], "Released").json()["error"]["message"]


def test_a_molding_split_runs_parts_one_after_the_other(sid: str) -> None:
    pid = _plan(sid)
    mold = _wos(sid, pid)["Molding"]
    other = next(m for m in mold["compatible_machines"] if m != mold["machine_id"])
    half = mold["qty"] // 2
    d = client.post(
        f"/api/work-orders/{mold['wo_id']}/split", json={"parts": [{"qty": half}, {"qty": mold["qty"] - half, "machine_id": other}]}, headers=h(sid)
    ).json()
    a, b = d["children_detail"]
    assert b["planned_start"] == a["planned_end"]  # one mold, so one machine at a time
    paint = _wos(sid, pid)["Painting"]["wo_id"]
    assert _move(sid, a["wo_id"], "In Production").status_code == 200
    _output(sid, a["wo_id"], 40)
    assert client.get(f"/api/work-orders/{paint}", headers=h(sid)).json()["available_qty"] == 40
    parent = client.get(f"/api/work-orders/{mold['wo_id']}", headers=h(sid)).json()
    assert (parent["status"], parent["output_qty"]) == ("In Production", 40)


def test_board_flags_clashes_between_plans_and_dispatch_refuses_new_ones(sid: str) -> None:
    p1, p2 = _plan(sid, 0), _plan(sid, 1)
    b = client.get("/api/production-board?days=7", headers=h(sid)).json()
    assert [ln["id"] for ln in b["lanes"]][:6] == ["IMM-01", "IMM-02", "IMM-03", "IMM-04", "IMM-05", "IMM-06"]
    assert {"PNT-01", "ASM-01", "ASM-02", "ASM-03"} <= {ln["id"] for ln in b["lanes"]}
    assert b["summary"]["work_orders"] == 6
    clash = [c for c in b["conflicts"] if c["type"] == "MACHINE_CONFLICT"]
    assert clash, b["conflicts"]
    assert clash[0]["blocking"] is True
    lane = next(ln for ln in b["lanes"] if any(i["id"] in clash[0]["item_ids"] for i in ln["items"]))
    assert lane["rows"] >= 2  # the clashing bars are stacked, not drawn over each other
    w1, w2 = _wos(sid, p1)["Painting"], _wos(sid, p2)["Painting"]
    body = {"action": "RESCHEDULE", "wo_id": w2["wo_id"], "planned_start": w1["planned_start"], "planned_end": w1["planned_end"]}
    r = client.post("/api/production-board/dispatch", json=body, headers=h(sid))
    assert (r.status_code, _err(r)) == (409, "FV-WO-001")
    assert w1["wo_id"] in r.json()["error"]["message"]
    late = {**body, "planned_start": "2027-02-27T07:00+07:00", "planned_end": "2027-02-27T09:00+07:00", "reason": "after the clash"}
    moved = client.post("/api/production-board/dispatch", json=late, headers=h(sid)).json()
    assert (moved["planned_start"], moved["shift"]) == ("2027-02-27T07:00+07:00", "A")
    assert moved["history"][0]["action"] == "Reschedule"
    bad = {**body, "planned_end": body["planned_start"]}
    assert client.post("/api/production-board/dispatch", json=bad, headers=h(sid)).json()["error"]["detail"]["fields"][0]["field"] == "planned_end"
    r = client.post("/api/production-board/dispatch", json={"action": "REASSIGN_MACHINE", "wo_id": w2["wo_id"], "machine_id": "IMM-01"}, headers=h(sid))
    assert r.json()["error"]["detail"]["fields"][0]["field"] == "machine_id"
    r = client.post("/api/production-board/dispatch", json={"action": "REPRIORITISE", "wo_id": w2["wo_id"], "priority": 9}, headers=h(sid))
    assert r.status_code == 400
    assert (
        client.post("/api/production-board/dispatch", json={"action": "REPRIORITISE", "wo_id": w2["wo_id"], "priority": 1}, headers=h(sid)).json()["priority"]
        == 1
    )


def test_board_views_and_filters(sid: str) -> None:
    _plan(sid)
    for view, first in (("process", "Molding"), ("shift", "A")):
        b = client.get(f"/api/production-board?view={view}&days=7", headers=h(sid)).json()
        assert b["lanes"][0]["id"] == first
    day = client.get("/api/production-board?view=day&days=7", headers=h(sid)).json()
    assert [ln["id"] for ln in day["lanes"]] == sorted(ln["id"] for ln in day["lanes"])
    only = client.get("/api/production-board?process=Painting&days=7", headers=h(sid)).json()
    assert {i["process"] for ln in only["lanes"] for i in ln["items"]} == {"Painting"}
    assert client.get("/api/production-board?days=2", headers=h(sid)).status_code == 400
    assert client.get("/api/production-board?view=line", headers=h(sid)).status_code == 400
    empty = client.get("/api/production-board?date=2027-06-01", headers=h(sid)).json()
    assert empty["summary"]["work_orders"] == 0
