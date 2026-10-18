import uuid
from collections.abc import Iterator

import pytest
from app import actions, capacity, plans, plant
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
FEB = {"period_start": "2027-02-01", "period_end": "2027-02-28"}


@pytest.fixture
def sid() -> Iterator[str]:
    s = str(uuid.uuid4())
    yield s
    actions.clear(s)
    plans.clear(s)
    capacity.clear(s)


def h(s: str) -> dict[str, str]:
    return {"X-Session-Id": s}


def test_pending_mold_move_is_linked_to_its_sku_on_the_capacity_row(sid: str) -> None:
    page = client.get("/api/capacity", params=FEB, headers=h(sid)).json()
    sku = page["rows"][0]["sku"]
    other = next(r["sku"] for r in page["rows"] if r["sku"] != sku) if len(page["rows"]) > 1 else None
    to = plant.MOLD_COMPAT[sku][0]
    doc = actions.propose(sid, "mold_move", f"Move {sku} mold to {to}", "planning", "capacity", {"sku": sku, "to_machine_id": to})

    rows = {r["sku"]: r for r in client.get("/api/capacity", params=FEB, headers=h(sid)).json()["rows"]}
    assert rows[sku]["proposals"] == [{"action_id": doc["action_id"], "title": doc["title"], "status": "pending"}]
    if other is not None:
        assert rows[other]["proposals"] == []


def test_decided_proposals_are_not_linked(sid: str) -> None:
    page = client.get("/api/capacity", params=FEB, headers=h(sid)).json()
    sku = page["rows"][0]["sku"]
    to = plant.MOLD_COMPAT[sku][0]
    doc = actions.propose(sid, "mold_move", "Move", "planning", "capacity", {"sku": sku, "to_machine_id": to})
    actions.decide(sid, doc["action_id"], approve=False, user="tester")

    rows = {r["sku"]: r for r in client.get("/api/capacity", params=FEB, headers=h(sid)).json()["rows"]}
    assert rows[sku]["proposals"] == []


def test_snapshots_do_not_store_live_proposals(sid: str) -> None:
    page = client.get("/api/capacity", params=FEB, headers=h(sid)).json()
    sku = page["rows"][0]["sku"]
    actions.propose(sid, "mold_move", "Move", "planning", "capacity", {"sku": sku, "to_machine_id": plant.MOLD_COMPAT[sku][0]})
    snap = client.post("/api/capacity/recalculate", json=FEB, headers=h(sid)).json()
    assert all("proposals" not in r for r in snap["rows"])
