import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from app import capacity, plans
from app.main import app
from app.tools import common
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
FEB = {"period_start": "2027-02-01", "period_end": "2027-02-28"}


@pytest.fixture
def sid() -> Iterator[str]:
    s = str(uuid.uuid4())
    yield s
    plans.clear(s)
    capacity.clear(s)


def h(s: str) -> dict[str, str]:
    return {"X-Session-Id": s}


def _err(r: Any) -> str:
    code: str = r.json()["error"]["code"]
    return code


def test_workday_minutes_come_from_the_shift_master() -> None:
    from app import plant

    minutes = sum(int(str(s["planned_minutes"])) for s in plant.SHIFTS)
    assert common.MINUTES_PER_WORKDAY == minutes == 1350


def test_strict_flag_and_utilization_update_independently(sid: str) -> None:
    assert client.get("/api/capacity/settings", headers=h(sid)).json()["strict_process_sequence"] is False
    client.put("/api/capacity/settings", json={"planning_utilization_pct": 70}, headers=h(sid))
    on = client.put("/api/capacity/settings", json={"strict_process_sequence": True}, headers=h(sid)).json()
    assert on == {"planning_utilization_pct": 70, "default_planning_utilization_pct": 80.0, "strict_process_sequence": True}
    off_util = client.put("/api/capacity/settings", json={"planning_utilization_pct": 65}, headers=h(sid)).json()
    assert off_util["strict_process_sequence"] is True
    assert off_util["planning_utilization_pct"] == 65


def test_snapshot_list_filters_by_status(sid: str) -> None:
    client.post("/api/capacity/recalculate", json=FEB, headers=h(sid))
    client.post("/api/capacity/recalculate", json=FEB, headers=h(sid))
    computed = client.get("/api/capacity/snapshots", params={"status": "Computed"}, headers=h(sid)).json()["snapshots"]
    superseded = client.get("/api/capacity/snapshots", params={"status": "Superseded"}, headers=h(sid)).json()["snapshots"]
    assert [s["status"] for s in computed] == ["Computed"]
    assert [s["status"] for s in superseded] == ["Superseded"]


def test_recalculation_keeps_the_utilization_of_a_snapshot_a_plan_references(sid: str) -> None:
    first = client.post("/api/capacity/recalculate", json=FEB, headers=h(sid)).json()
    pid = client.post("/api/plans", json=FEB, headers=h(sid)).json()["plan_id"]
    p = plans._doc(sid, pid)
    p["capacity_snapshot_id"] = first["cap_id"]
    plans._save(p)
    client.put("/api/capacity/settings", json={"planning_utilization_pct": 50}, headers=h(sid))
    kept = client.post("/api/capacity/recalculate", json=FEB, headers=h(sid)).json()
    assert kept["planning_utilization_pct"] == 80.0


def test_recalculation_uses_the_current_setting_when_no_plan_references_the_snapshot(sid: str) -> None:
    client.post("/api/capacity/recalculate", json=FEB, headers=h(sid))
    client.put("/api/capacity/settings", json={"planning_utilization_pct": 50}, headers=h(sid))
    new = client.post("/api/capacity/recalculate", json=FEB, headers=h(sid)).json()
    assert new["planning_utilization_pct"] == 50.0


def test_strict_sequence_blocks_a_work_order_whose_earlier_process_is_open(sid: str) -> None:
    pid = client.post("/api/plans", json=FEB, headers=h(sid)).json()["plan_id"]
    for c in client.get(f"/api/plans/{pid}/demand-candidates", headers=h(sid)).json()["candidates"][:1]:
        client.post(f"/api/plans/{pid}/demand", json={"order_id": c["order_id"]}, headers=h(sid))
    g = client.post(f"/api/plans/{pid}/work-orders", headers=h(sid)).json()
    later = next(w for w in g["created"] if w["routing_step"] > 1)
    p = plans._doc(sid, pid)
    p["status"] = "Confirmed"
    plans._save(p)

    client.put("/api/capacity/settings", json={"strict_process_sequence": False}, headers=h(sid))
    assert client.post(f"/api/plans/{pid}/work-orders/{later['wo_id']}/status", json={"status": "In Production"}, headers=h(sid)).status_code == 200

    client.put("/api/capacity/settings", json={"strict_process_sequence": True}, headers=h(sid))
    line_wos = [w for w in g["created"] if w["line_id"] == later["line_id"]]
    last = max(line_wos, key=lambda w: w["routing_step"])
    assert _err(client.post(f"/api/plans/{pid}/work-orders/{last['wo_id']}/status", json={"status": "In Production"}, headers=h(sid))) == "FV-PP-019"
