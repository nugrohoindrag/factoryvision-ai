import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from app import capacity, plans, plant
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


def h(s: str) -> dict[str, str]:
    return {"X-Session-Id": s}


def _err(r: Any) -> str:
    code: str = r.json()["error"]["code"]
    return code


def test_assess_one_sku_matches_the_capacity_formulas(sid: str) -> None:
    sku = next(iter(plant.MOLD_COMPAT))
    client.put("/api/capacity/settings", json={"planning_utilization_pct": 80}, headers=h(sid))
    r = client.get("/api/capacity/assess", params={"sku": sku, "qty": 1000, **FEB}, headers=h(sid))
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["sku"] == sku
    assert a["qty"] == 1000
    assert a["period_start"] == "2027-02-01"
    assert a["period_end"] == "2027-02-28"
    assert a["demand"] == 1000
    assert a["planning"] == a["total"] * 80 // 100
    assert a["buffer"] == a["total"] - a["planning"]
    assert a["gap"] == max(1000 - a["total"], 0)
    assert a["status"] == capacity.status_of(1000, a["total"], a["planning"])
    assert a["formulas"] == capacity.FORMULAS


def test_assess_zero_quantity_is_within_plan(sid: str) -> None:
    sku = next(iter(plant.MOLD_COMPAT))
    a = client.get("/api/capacity/assess", params={"sku": sku, "qty": 0, **FEB}, headers=h(sid)).json()
    assert a["demand"] == 0
    assert a["gap"] == 0
    assert a["status"] == capacity.WITHIN


def test_assess_rejects_unknown_sku_and_negative_quantity(sid: str) -> None:
    sku = next(iter(plant.MOLD_COMPAT))
    assert _err(client.get("/api/capacity/assess", params={"sku": "NOPE-SKU", "qty": 1, **FEB}, headers=h(sid))) == "FV-CAP-004"
    assert _err(client.get("/api/capacity/assess", params={"sku": sku, "qty": -5, **FEB}, headers=h(sid))) == "FV-CAP-004"


def test_assess_rejects_a_bad_period(sid: str) -> None:
    sku = next(iter(plant.MOLD_COMPAT))
    bad = {"period_start": "2027-02-10", "period_end": "2027-02-01"}
    assert _err(client.get("/api/capacity/assess", params={"sku": sku, "qty": 1, **bad}, headers=h(sid))) == "FV-CAP-001"
