from collections.abc import Iterator
from typing import Any

import pytest
from app import actions, effects, pubsub
from app.errors import FVError
from app.session import Limiter

SID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def executed(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    def record(message: dict[str, Any]) -> dict[str, Any]:
        calls.append(message)
        return {"ok": True}

    monkeypatch.setitem(pubsub._handlers, pubsub.AGENT_COMMANDS, record)
    yield calls
    actions.clear(SID)


def test_proposal_is_pending_and_executes_nothing(executed: list[dict[str, Any]]) -> None:
    a = actions.propose(SID, "quality_hold", "Hold lot", "quality", "lift 6x", {"resin_lot_id": "LOT-X"})
    assert a["status"] == "pending"
    assert a["command"] == {"name": "hold_lot", "args": {"resin_lot_id": "LOT-X"}}
    assert set(a["impact"]) == set(actions.IMPACT_KEYS)
    assert executed == []


def test_approve_publishes_command_once(executed: list[dict[str, Any]]) -> None:
    a = actions.propose(SID, "maintenance", "Maintain IMM-04", "mes", "MTBF falling", {"machine_id": "IMM-04"})
    out = actions.decide(SID, a["action_id"], approve=True, user="manager")
    assert out["status"] == "approved"
    assert out["decided_by"] == "manager"
    assert executed[0]["command"]["name"] == "start_maintenance"
    with pytest.raises(FVError) as e:
        actions.decide(SID, a["action_id"], approve=True, user="manager")
    assert e.value.code == "FV-ACT-002"
    assert len(executed) == 1


def test_reject_never_executes(executed: list[dict[str, Any]]) -> None:
    a = actions.propose(SID, "maintenance", "Maintain IMM-04", "mes", "x", {"machine_id": "IMM-04"})
    assert actions.decide(SID, a["action_id"], approve=False, user="manager")["status"] == "rejected"
    assert executed == []


def test_failed_execution_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_message: dict[str, Any]) -> dict[str, Any]:
        raise FVError("FV-TWIN-002")

    monkeypatch.setitem(pubsub._handlers, pubsub.AGENT_COMMANDS, boom)
    a = actions.propose(SID, "maintenance", "Maintain IMM-04", "mes", "x", {"machine_id": "IMM-04"})
    with pytest.raises(FVError) as e:
        actions.decide(SID, a["action_id"], approve=True, user="manager")
    assert e.value.code == "FV-ACT-003"
    assert actions.get(SID, a["action_id"])["status"] == "failed"
    actions.clear(SID)


@pytest.mark.usefixtures("executed")
def test_other_session_cannot_see_action() -> None:
    a = actions.propose(SID, "maintenance", "Maintain IMM-04", "mes", "x", {"machine_id": "IMM-04"})
    with pytest.raises(FVError) as e:
        actions.get("33333333-3333-3333-3333-333333333333", a["action_id"])
    assert e.value.code == "FV-ACT-001"


def test_plan_approval_decides_all_its_proposals(executed: list[dict[str, Any]]) -> None:
    for k in ("maintenance", "quality_hold"):
        actions.propose(SID, k, k, "orchestrator", "x", {"machine_id": "IMM-04", "resin_lot_id": "L"}, plan_id="PLAN-1")
    out = actions.decide_plan(SID, "PLAN-1", approve=True, user="manager")
    assert len(out["decided"]) == 2
    assert not out["failed"]
    assert len(executed) == 2


@pytest.mark.usefixtures("executed")
def test_overrides_follow_scenario_and_approved_commands() -> None:
    effects.set_scenario(SID, "disruption_response")
    a = actions.propose(SID, "maintenance", "m", "mes", "x", {"machine_id": "IMM-04"})
    actions.propose(SID, "quality_hold", "h", "quality", "x", {"resin_lot_id": "LOT-X"})
    actions.decide(SID, a["action_id"], approve=True, user="manager")
    o = effects.overrides(SID)
    assert o.demand_change_pct == {"FB-ARKA": 40}
    assert o.maintenance == ["IMM-04"]
    assert o.holds == []
    effects.set_scenario(SID, "baseline")


def test_limiter_caps_and_debounces() -> None:
    lim = Limiter()
    lim.take("s", "vision", 2, "FV-VIS-001")
    lim.take("s", "vision", 2, "FV-VIS-001")
    with pytest.raises(FVError) as e:
        lim.take("s", "vision", 2, "FV-VIS-001")
    assert e.value.code == "FV-VIS-001"
    lim.debounce("s", "cam", 30, "FV-VIS-002")
    with pytest.raises(FVError):
        lim.debounce("s", "cam", 30, "FV-VIS-002")
    lim.reset("s")
    assert lim.used("s", "vision") == 0
