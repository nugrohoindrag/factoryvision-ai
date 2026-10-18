"""The planning agent's draft plan: computed for a period, proposed in the Action Center, a Draft plan only after approval."""

import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from app import actions, capacity, forecasts, plans
from app.agents import tools as agent_tools
from app.agents.agents import module_agents
from app.agents.tools import MODULE_TOOLS
from app.main import app
from app.simulation import commands
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from app.simulation.engine import State

client = TestClient(app, raise_server_exceptions=False)
WEEK = {"period_start": "2027-02-01", "period_end": "2027-02-07"}


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


def _draft(sid: str, **body: Any) -> dict[str, Any]:
    r = client.post("/api/plans/draft", json={**WEEK, **body}, headers=h(sid))
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def _propose(sid: str, **body: Any) -> dict[str, Any]:
    r = client.post("/api/plans/draft/propose", json={**WEEK, **body}, headers=h(sid))
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


class Ctx:
    def __init__(self, sid: str) -> None:
        self.state: dict[str, Any] = {"session_id": sid, "plan_id": "PLAN-d", "sources": []}


def test_draft_takes_open_orders_due_in_the_period_and_saves_nothing(sid: str) -> None:
    d = _draft(sid, include_forecast=False)
    assert d["summary"]["order_lines"] > 0
    assert d["summary"]["forecast_qty"] == 0
    assert all(x["forecast_qty"] == 0 for x in d["lines"])
    assert sum(x["demand_qty"] for x in d["lines"]) == d["summary"]["order_qty"]
    assert [x["priority"] for x in d["lines"]] == sorted(x["priority"] for x in d["lines"])
    assert all(1 <= x["priority"] <= plans.MAX_PRIORITY for x in d["lines"])
    dates = [x["required_date"] for x in d["lines"]]
    assert dates == sorted(dates)  # firm orders, earlier delivery first
    assert all(x["capacity_status"] in (capacity.WITHIN, capacity.ADDITIONAL, capacity.UP) for x in d["lines"])
    assert client.get("/api/plans", headers=h(sid)).json()["plans"] == []
    assert actions.list_actions(sid) == []


def test_forecast_lines_come_after_firm_orders(sid: str) -> None:
    d = _draft(sid)
    firm = [x["priority"] for x in d["lines"] if x["demand_qty"] > 0]
    only_forecast = [x["priority"] for x in d["lines"] if x["demand_qty"] == 0]
    if firm and only_forecast:
        assert max(firm) <= min(only_forecast)
    assert d["summary"]["forecast_qty"] == sum(x["forecast_qty"] for x in d["lines"])


def test_a_line_above_capacity_is_planned_at_what_fits(sid: str, monkeypatch: pytest.MonkeyPatch) -> None:
    real = capacity.sku_capacity

    def tight(demand: dict[str, int], *a: Any, **k: Any) -> dict[str, dict[str, Any]]:
        out = real(demand, *a, **k)
        sku = "FB-ARKA"
        if sku in out:
            out[sku] = capacity.assess([], 80.0, demand[sku]) | {"total": 100, "planning": 80}
            out[sku]["status"] = capacity.status_of(demand[sku], 100, 80)
        return out

    monkeypatch.setattr(capacity, "sku_capacity", tight)
    d = _draft(sid, include_forecast=False)
    arka = next(x for x in d["lines"] if x["sku"] == "FB-ARKA")
    assert arka["planned_qty"] == 100
    assert arka["capacity_status"] == capacity.ADDITIONAL
    gap = next(g for g in d["gaps"] if g["sku"] == "FB-ARKA")
    assert gap["gap_qty"] == arka["demand_qty"] - 100
    assert gap["short_of_orders"] == arka["demand_qty"] - 100
    assert "FB-ARKA is planned at capacity" in plans.draft_rationale(d)


def test_a_bad_period_is_refused(sid: str) -> None:
    r = client.post("/api/plans/draft", json={"period_start": "2027-02-10", "period_end": "2027-02-01"}, headers=h(sid))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == plans.INVALID_PERIOD


def test_propose_creates_a_pending_action_and_no_plan(sid: str) -> None:
    out = _propose(sid, include_forecast=False)
    a = out["action"]
    assert (a["status"], a["kind"], a["module"]) == ("pending", "production_plan_draft", "planning")
    assert a["command"]["name"] == "create_draft_plan"
    assert a["command"]["args"]["demands"]
    assert client.get("/api/plans", headers=h(sid)).json()["plans"] == []


def test_approval_opens_a_draft_plan_once(sid: str) -> None:
    out = _propose(sid, include_forecast=False)
    d, a = out["draft"], out["action"]
    done = actions.decide(sid, a["action_id"], approve=True, user="ppic@vidad.example")
    assert done["status"] == "approved", done
    res = done["execution"]["result"]
    p = client.get(f"/api/plans/{res['plan_id']}", headers=h(sid)).json()
    assert p["status"] in ("Draft", "Planning")
    assert (p["origin"], p["agent_kind"], p["created_by"], p["approved_by"]) == ("agent", "draft", plans.AGENT, "ppic@vidad.example")
    assert len(p["demand"]) == d["summary"]["order_lines"]
    assert {ln["sku"]: ln["planned_qty"] for ln in p["lines"]} == {x["sku"]: x["planned_qty"] for x in d["lines"]}
    assert {ln["sku"]: ln["priority"] for ln in p["lines"]} == {x["sku"]: x["priority"] for x in d["lines"]}
    again = commands.execute(cast("State", SimpleNamespace(session_id=sid)), {**a["command"], "action_id": a["action_id"], "decided_by": "ppic@vidad.example"})
    assert again["plan_id"] == res["plan_id"]
    assert len(client.get("/api/plans", headers=h(sid)).json()["plans"]) == 1


def test_an_old_draft_takes_only_what_is_still_open(sid: str) -> None:
    out = _propose(sid, include_forecast=False)
    first = out["action"]["command"]["args"]["demands"][0]
    other = client.post("/api/plans", json=WEEK, headers=h(sid)).json()
    assert client.post(f"/api/plans/{other['plan_id']}/demand", json={"order_id": first["order_id"]}, headers=h(sid)).status_code == 200
    done = actions.decide(sid, out["action"]["action_id"], approve=True, user="ppic")
    res = done["execution"]["result"]
    assert any(first["order_id"] in n for n in res["notes"])
    p = client.get(f"/api/plans/{res['plan_id']}", headers=h(sid)).json()
    assert all(d["order_id"] != first["order_id"] for d in p["demand"])


def test_rejecting_runs_nothing(sid: str) -> None:
    a = _propose(sid, include_forecast=False)["action"]
    assert actions.decide(sid, a["action_id"], approve=False, user="ppic")["status"] == "rejected"
    assert client.get("/api/plans", headers=h(sid)).json()["plans"] == []


def test_agent_tools_draft_and_only_propose(sid: str) -> None:
    names = {f.__name__ for f in MODULE_TOOLS["planning"]}
    assert {"draft_production_plan", "propose_draft_plan"} <= names
    assert "propose_draft_plan" in str(module_agents()["planning"].instruction)
    d = agent_tools.draft_production_plan(period_start=WEEK["period_start"], period_end=WEEK["period_end"], include_forecast=False, tool_context=Ctx(sid))
    assert "args" not in d
    assert d["lines"]
    bad = agent_tools.draft_production_plan(period_start="2027-02-10", period_end="2027-02-01", include_forecast=False, tool_context=Ctx(sid))
    assert bad["error"]["code"] == plans.INVALID_PERIOD
    r = agent_tools.propose_draft_plan(
        period_start=WEEK["period_start"], period_end=WEEK["period_end"], include_forecast=False, rationale="Week 1 call-offs.", tool_context=Ctx(sid)
    )
    assert r["status"].startswith("pending")
    a = actions.list_actions(sid, "pending")[0]
    assert a["rationale"].startswith("Week 1 call-offs.")
    assert client.get("/api/plans", headers=h(sid)).json()["plans"] == []
