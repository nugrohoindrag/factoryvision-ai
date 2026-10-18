import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from app import actions, plant
from app.main import app
from app.tools import production_plan
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
BASE = "/api/production-plans"


@pytest.fixture
def sid() -> Iterator[str]:
    s = str(uuid.uuid4())
    yield s
    production_plan.clear(s)
    actions.clear(s)


def h(s: str) -> dict[str, str]:
    return {"X-Session-Id": s}


def _create(s: str, start: str = "2027-02-01", end: str = "2027-02-07") -> dict[str, Any]:
    r = client.post(BASE, json={"period_start": start, "period_end": end}, headers=h(s))
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def _add(s: str, plan_id: str, order_id: str, qty: int | None = None) -> Any:
    return client.post(f"{BASE}/{plan_id}/demands", json={"order_id": order_id, "demand_qty": qty}, headers=h(s))


def _week_plan(s: str) -> dict[str, Any]:
    d = _create(s)
    pid = d["plan"]["plan_id"]
    for c in [c for c in d["candidates"] if c["in_period"]]:
        d = _add(s, pid, c["order_id"]).json()
    return d


def _ready(s: str) -> dict[str, Any]:
    d = _week_plan(s)
    pid = d["plan"]["plan_id"]
    assert client.post(f"{BASE}/{pid}/work-orders/generate", headers=h(s)).status_code == 200
    r = client.post(f"{BASE}/{pid}/schedule", headers=h(s))
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def test_list_shows_the_monthly_plans_recorded_before_the_session(sid: str) -> None:
    d = client.get(BASE, headers=h(sid)).json()
    rows = {p["plan_number"]: p for p in d["plans"]}
    assert rows["PP-2027-02"]["status"] == "In Execution"
    assert rows["PP-2027-01"]["status"] == "Completed"
    assert rows["PP-2027-02"]["capacity_status"] == "Capacity Up Required"  # IMM-04 is over 100% this month
    assert all(p["baseline"] and p["line_count"] == len(plant.SKUS) for p in d["plans"])
    detail = client.get(f"{BASE}/PP-2027-02", headers=h(sid)).json()["plan"]
    assert detail["editable"] is False


def test_create_numbers_after_the_monthly_plan_and_rejects_a_bad_period(sid: str) -> None:
    first = _create(sid)["plan"]
    assert (first["plan_number"], first["status"], first["planning_utilization_pct"]) == ("PP-2027-02-002", "Draft", 80.0)
    assert _create(sid)["plan"]["plan_number"] == "PP-2027-02-003"
    assert _create(sid, "2027-03-01", "2027-03-20")["plan"]["plan_number"] == "PP-2027-03"
    r = client.post(BASE, json={"period_start": "2027-02-07", "period_end": "2027-02-01"}, headers=h(sid))
    assert r.status_code == 400
    assert r.json()["error"]["detail"]["fields"][0]["field"] == "period_end"
    r = client.post(BASE, json={"period_start": "2027-01-01", "period_end": "2027-01-20"}, headers=h(sid))
    assert "before today" in r.json()["error"]["detail"]["fields"][0]["message"]


def test_demand_aggregates_per_sku_and_raises_the_order_line_planned_qty(sid: str) -> None:
    d = _create(sid)
    pid = d["plan"]["plan_id"]
    arka = [c for c in d["candidates"] if c["sku"] == "FB-ARKA" and c["in_period"]]
    assert len(arka) >= 2
    assert all(c["outstanding_qty"] == c["ordered_qty"] - c["planned_qty"] > 0 for c in d["candidates"])
    for c in arka[:2]:
        d = _add(sid, pid, c["order_id"]).json()
    lines = [ln for ln in d["plan"]["lines"] if ln["sku"] == "FB-ARKA"]
    assert len(lines) == 1
    ln = lines[0]
    assert ln["demand_qty"] == ln["planned_qty"] == arka[0]["outstanding_qty"] + arka[1]["outstanding_qty"]
    assert [s["order_id"] for s in ln["sources"]] == [arka[0]["order_id"], arka[1]["order_id"]]
    assert ln["required_delivery_date"] == min(arka[0]["requested_delivery_date"], arka[1]["requested_delivery_date"])
    assert ln["capacity_status"] in production_plan.CAPACITY_STATUSES
    assert d["plan"]["status"] == "Planning"
    order = client.get(f"/api/orders/{arka[0]['order_number']}", headers=h(sid)).json()
    line = next(x for x in order["lines"] if x["order_id"] == arka[0]["order_id"])
    assert (line["planned_qty"], line["status"]) == (arka[0]["ordered_qty"], "Planned")
    assert any(t["plan_number"] == "PP-2027-02-002" for t in order["traceability"])
    assert all(c["order_id"] not in {a["order_id"] for a in arka[:2]} for c in d["candidates"])


def test_demand_rules_reject_duplicates_overflow_and_zero(sid: str) -> None:
    d = _create(sid)
    pid = d["plan"]["plan_id"]
    c = d["candidates"][0]
    r = _add(sid, pid, c["order_id"], c["outstanding_qty"] + 1)
    assert r.status_code == 400
    assert "more than" in r.json()["error"]["detail"]["fields"][0]["message"]
    assert _add(sid, pid, c["order_id"], 0).status_code == 400
    assert _add(sid, pid, c["order_id"], 100).status_code == 200
    r = _add(sid, pid, c["order_id"])
    assert (r.status_code, r.json()["error"]["code"]) == (409, "FV-PLAN-004")
    other = _create(sid)["plan"]["plan_id"]
    r = _add(sid, other, c["order_id"])
    assert r.status_code == 200
    assert r.json()["demand"]["demand_qty"] == c["outstanding_qty"] - 100
    r = _add(sid, _create(sid)["plan"]["plan_id"], c["order_id"])
    assert r.status_code == 400
    assert "already fully planned" in r.json()["error"]["message"]
    assert _add(sid, pid, "SO-NOPE").status_code == 404


def test_removing_demand_gives_it_back_and_drops_an_empty_line(sid: str) -> None:
    d = _create(sid)
    pid = d["plan"]["plan_id"]
    c = d["candidates"][0]
    d = _add(sid, pid, c["order_id"]).json()
    demand_id = d["demand"]["demand_id"]
    d = client.delete(f"{BASE}/{pid}/demands/{demand_id}", headers=h(sid)).json()
    assert d["plan"]["lines"] == []
    assert d["plan"]["status"] == "Draft"
    assert any(x["order_id"] == c["order_id"] and x["outstanding_qty"] == c["outstanding_qty"] for x in d["candidates"])


def test_planned_qty_is_the_planners_and_capacity_is_the_servers(sid: str) -> None:
    d = _week_plan(sid)
    pid = d["plan"]["plan_id"]
    ln = next(x for x in d["plan"]["lines"] if x["sku"] == "FB-ARKA")
    r = client.patch(f"{BASE}/{pid}/lines/{ln['line_id']}", json={"planned_qty": -1}, headers=h(sid))
    assert r.status_code == 400
    r = client.patch(f"{BASE}/{pid}/lines/{ln['line_id']}", json={"planned_qty": ln["demand_qty"] * 3, "priority": 1}, headers=h(sid))
    up = next(x for x in r.json()["plan"]["lines"] if x["line_id"] == ln["line_id"])
    assert (up["priority"], up["demand_qty"], up["planned_qty"]) == (1, ln["demand_qty"], ln["demand_qty"] * 3)
    assert up["capacity_status"] == "Capacity Up Required"
    cap = up["capacity"]
    assert cap["gap"] == up["planned_qty"] - cap["total_capacity"] > 0
    assert cap["planning_capacity"] == int(cap["total_capacity"] * 0.8)
    assert {c["machine_id"] for c in cap["contributions"]} == set(plant.MOLD_COMPAT["FB-ARKA"])
    assert r.json()["plan"]["capacity_status"] == "Capacity Up Required"


def test_wizard_steps_unlock_in_order(sid: str) -> None:
    d = _create(sid)
    pid = d["plan"]["plan_id"]
    steps = d["plan"]["readiness"]["steps"]
    assert [s["reachable"] for s in steps] == [True, False, False, False, False, False]
    assert "step 1" in steps[1]["blocked_by"]
    r = client.patch(f"{BASE}/{pid}", json={"wizard_step": 4}, headers=h(sid))
    assert (r.status_code, r.json()["error"]["code"]) == (409, "FV-PLAN-003")
    r = client.post(f"{BASE}/{pid}/work-orders/generate", headers=h(sid))
    assert r.json()["error"]["code"] == "FV-PLAN-003"
    _add(sid, pid, d["candidates"][0]["order_id"])
    d = client.patch(f"{BASE}/{pid}", json={"wizard_step": 3}, headers=h(sid)).json()
    assert d["plan"]["readiness"]["resume_step"] == 3
    assert [s["reachable"] for s in d["plan"]["readiness"]["steps"]] == [True, True, True, False, False, False]


def test_work_orders_one_per_process_chained_and_not_duplicated(sid: str) -> None:
    d = _week_plan(sid)
    pid = d["plan"]["plan_id"]
    g = client.post(f"{BASE}/{pid}/work-orders/generate", headers=h(sid)).json()
    lines = d["plan"]["lines"]
    assert len(g["generated"]["created"]) == 3 * len(lines)
    wos = g["plan"]["work_orders"]
    for ln in lines:
        chain = sorted((w for w in wos if w["line_id"] == ln["line_id"]), key=lambda w: w["sequence"])
        assert [w["process"] for w in chain] == ["MLD", "PNT", "ASM"]
        assert [w["predecessor_wo_id"] for w in chain] == [None, chain[0]["wo_id"], chain[1]["wo_id"]]
        assert all(w["planned_qty"] == ln["planned_qty"] for w in chain)
    assert "WO-270201-MLD-FB-ARKA" in {w["wo_id"] for w in wos}
    again = client.post(f"{BASE}/{pid}/work-orders/generate", headers=h(sid)).json()["generated"]
    assert (again["created"], len(again["existing"])) == ([], 3 * len(lines))


def test_cp_sat_schedules_molding_on_compatible_machines_within_ten_seconds(sid: str) -> None:
    d = _ready(sid)
    run = d["schedule"]
    assert run["engine"] == "OR-Tools CP-SAT"
    assert run["status"] in ("OPTIMAL", "FEASIBLE")
    assert run["solve_seconds"] < 10
    for w in d["plan"]["work_orders"]:
        assert w["status"] == "Scheduled"
        assert w["planned_start"] <= w["planned_end"]
        if w["process"] == "MLD":
            assert w["mold_id"] == plant.MOLD_ID[w["sku"]]
            assert all(r["machine_id"] in plant.MOLD_COMPAT[w["sku"]] for r in w["runs"])
            assert sum(r["qty"] for r in w["runs"]) + w["short_qty"] == w["planned_qty"]
    by_line: dict[str, dict[str, Any]] = {}
    for w in d["plan"]["work_orders"]:
        by_line.setdefault(w["line_id"], {})[w["process"]] = w
    assert all(c["PNT"]["planned_start"] > c["MLD"]["planned_start"] for c in by_line.values())
    assert d["plan"]["status"] == "Ready"
    assert d["plan"]["readiness"]["steps"][5]["reachable"]


def test_resources_accept_only_compatible_machines(sid: str) -> None:
    d = _ready(sid)
    pid = d["plan"]["plan_id"]
    wo = next(w for w in d["plan"]["work_orders"] if w["sku"] == "FB-ARKA" and w["process"] == "MLD")
    r = client.patch(f"{BASE}/{pid}/work-orders/{wo['wo_id']}", json={"machine_id": "IMM-01"}, headers=h(sid))
    assert r.status_code == 400
    assert "IMM-04" in r.json()["error"]["detail"]["fields"][0]["message"]
    other = next(m for m in plant.MOLD_COMPAT["FB-ARKA"] if m != wo["machine_id"])
    r = client.patch(f"{BASE}/{pid}/work-orders/{wo['wo_id']}", json={"machine_id": other}, headers=h(sid)).json()
    assert r["plan"]["schedule_stale"] is True
    assert r["notes"]


def test_confirm_needs_capacity_and_confirmed_work_orders_then_locks(sid: str) -> None:
    d = _ready(sid)
    pid = d["plan"]["plan_id"]
    r = client.post(f"{BASE}/{pid}/confirm", headers=h(sid))
    assert (r.status_code, r.json()["error"]["code"]) == (409, "FV-PLAN-003")
    assert "not confirmed" in r.json()["error"]["message"]
    ln = next(x for x in d["plan"]["lines"] if x["sku"] == "RB-ARKA")
    client.patch(f"{BASE}/{pid}/lines/{ln['line_id']}", json={"planned_qty": 40_000}, headers=h(sid))
    client.post(f"{BASE}/{pid}/work-orders/generate", headers=h(sid))
    client.post(f"{BASE}/{pid}/schedule", headers=h(sid))
    client.post(f"{BASE}/{pid}/work-orders/confirm", headers=h(sid))
    r = client.post(f"{BASE}/{pid}/confirm", headers=h(sid))
    assert r.status_code == 409
    assert "RB-ARKA" in r.json()["error"]["message"]
    client.patch(f"{BASE}/{pid}/lines/{ln['line_id']}", json={"planned_qty": ln["demand_qty"]}, headers=h(sid))
    client.post(f"{BASE}/{pid}/work-orders/generate", headers=h(sid))
    client.post(f"{BASE}/{pid}/schedule", headers=h(sid))
    assert client.post(f"{BASE}/{pid}/work-orders/confirm", headers=h(sid)).status_code == 200
    r = client.post(f"{BASE}/{pid}/confirm", headers=h(sid))
    assert r.status_code == 200, r.text
    plan = r.json()["plan"]
    assert plan["stored_status"] == "Confirmed"
    assert plan["status"] in ("Confirmed", "In Execution")
    assert plan["confirmed_by"] == "demo-manager"
    assert all(x["status"] == "Released" for x in plan["lines"])
    assert plan["editable"] is False
    r = _add(sid, pid, "SO-ANY")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "FV-PLAN-002")


def test_cancel_needs_a_reason_and_gives_the_demand_back(sid: str) -> None:
    d = _week_plan(sid)
    pid = d["plan"]["plan_id"]
    order_id = d["plan"]["lines"][0]["sources"][0]["order_id"]
    r = client.post(f"{BASE}/{pid}/cancel", json={"reason": "x"}, headers=h(sid))
    assert r.status_code == 400
    r = client.post(f"{BASE}/{pid}/cancel", json={"reason": "OEM moved the call-off"}, headers=h(sid))
    assert r.json()["plan"]["status"] == "Cancelled"
    assert r.json()["plan"]["cancel_reason"] == "OEM moved the call-off"
    assert order_id not in production_plan.allocations(sid)
    fresh = _create(sid)
    assert any(c["order_id"] == order_id for c in fresh["candidates"])
    r = client.post(f"{BASE}/{pid}/cancel", json={"reason": "again"}, headers=h(sid))
    assert r.json()["error"]["code"] == "FV-PLAN-002"


def test_forecast_not_yet_ordered_becomes_extra_demand(sid: str) -> None:
    pid = _create(sid, "2027-02-08", "2027-02-27")["plan"]["plan_id"]
    preview = client.get(f"{BASE}/{pid}/forecast/FB-BROMO", headers=h(sid)).json()
    assert preview["qty"] > 0
    d = client.post(f"{BASE}/{pid}/forecast-demand", json={"sku": "FB-BROMO"}, headers=h(sid)).json()
    ln = d["plan"]["lines"][0]
    assert (ln["sku"], ln["demand_qty"], ln["forecast_qty"], ln["planned_qty"]) == ("FB-BROMO", 0, preview["qty"], preview["qty"])
    assert ln["forecast_line_id"]
    d = client.post(f"{BASE}/{pid}/forecast-demand", json={"sku": "FB-BROMO", "forecast_qty": 0}, headers=h(sid)).json()
    assert d["plan"]["lines"] == []


def test_reset_removes_the_session_plans(sid: str) -> None:
    _create(sid)
    client.post("/api/demo/reset", json={"scenario": "baseline"}, headers=h(sid))
    assert all(p["baseline"] for p in client.get(BASE, headers=h(sid)).json()["plans"])


class _Ctx:
    def __init__(self, s: str) -> None:
        self.state = {"session_id": s, "plan_id": "ASK-test", "sources": []}


@pytest.fixture
def approving(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import pubsub, twin

    monkeypatch.setitem(pubsub._handlers, pubsub.AGENT_COMMANDS, twin.on_command)


def test_agent_draft_takes_open_orders_and_forecast_and_saves_nothing(sid: str) -> None:
    d = client.post(f"{BASE}/draft", json={"period_start": "2027-02-01", "period_end": "2027-02-07", "include_forecast": False}, headers=h(sid)).json()
    due = [c for c in _create(sid)["candidates"] if c["in_period"]]
    production_plan.clear(sid)
    assert d["summary"]["order_lines"] == len(due)
    assert d["summary"]["forecast_qty"] == 0
    assert [ln["priority"] for ln in d["lines"]] == list(range(1, len(d["lines"]) + 1))
    assert all(ln["capacity_status"] != "Capacity Up Required" for ln in d["lines"])
    assert production_plan.session_plans(sid) == []
    assert "no number comes from the language model" in d["source"]


def test_agent_draft_plans_a_line_above_capacity_at_what_fits(sid: str) -> None:
    d = client.post(f"{BASE}/draft", json={"period_start": "2027-02-08", "period_end": "2027-02-27"}, headers=h(sid)).json()
    assert d["gaps"], "three weeks of ARKA forecast exceed one machine per mold"
    for g in d["gaps"]:
        ln = next(x for x in d["lines"] if x["sku"] == g["sku"])
        assert g["planned_qty"] == ln["planned_qty"] <= ln["total_capacity"]
        assert g["gap_qty"] == g["wanted_qty"] - g["planned_qty"] > 0
    assert d["summary"]["worst_capacity_status"] != "Capacity Up Required"


def test_agent_draft_needs_approval_then_becomes_a_draft_plan(sid: str, approving: None) -> None:
    body = {"period_start": "2027-02-01", "period_end": "2027-02-07"}
    r = client.post(f"{BASE}/draft/propose", json=body, headers=h(sid)).json()
    a = r["action"]
    assert (a["kind"], a["status"], a["origin_agent"]) == ("production_plan_draft", "pending", "planning")
    assert all(p["baseline"] for p in client.get(BASE, headers=h(sid)).json()["plans"])
    done = client.post(f"/api/actions/{a['action_id']}/approve", headers=h(sid)).json()
    assert done["status"] == "approved"
    mine = [p for p in client.get(BASE, headers=h(sid)).json()["plans"] if not p["baseline"]]
    assert len(mine) == 1
    plan = client.get(f"{BASE}/{mine[0]['plan_id']}", headers=h(sid)).json()["plan"]
    assert (plan["created_by"], plan["approved_by"], plan["status"]) == ("production_planning_agent", "demo-manager", "Planning")
    assert {ln["sku"]: ln["planned_qty"] for ln in plan["lines"]} == {x["sku"]: x["planned_qty"] for x in r["draft"]["lines"]}
    assert plan["editable"] is True


def test_planning_agent_tools_read_plans_and_only_propose(sid: str) -> None:
    from app.agents import tools

    ctx = _Ctx(sid)
    assert any(p["plan_number"] == "PP-2027-02" for p in tools.get_production_plans(tool_context=ctx)["plans"])
    d = tools.draft_production_plan(period_start="2027-02-01", period_end="2027-02-07", include_forecast=True, tool_context=ctx)
    assert "args" not in d
    assert d["lines"]
    bad = tools.draft_production_plan(period_start="2027-02-07", period_end="2027-02-01", include_forecast=True, tool_context=ctx)
    assert bad["error"]["code"] == "FV-REQ-001"
    r = tools.propose_draft_plan(period_start="2027-02-01", period_end="2027-02-07", include_forecast=True, rationale="Week 1 call-offs.", tool_context=ctx)
    assert r["status"].startswith("pending")
    a = actions.get(sid, r["action_id"])
    assert a["rationale"].startswith("Week 1 call-offs.")
    assert production_plan.session_plans(sid) == []
