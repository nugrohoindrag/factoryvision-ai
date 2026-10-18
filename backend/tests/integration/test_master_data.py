"""Master data: the seed's dimension tables, with create, edit, delete and revert kept per session."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from app import masterdata
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
API = "/api/master-data"


@pytest.fixture
def h() -> Iterator[dict[str, str]]:
    sid = str(uuid.uuid4())
    yield {"X-Session-Id": sid}
    masterdata.clear(sid)


def _err(r: Any) -> str:
    code: str = r.json()["error"]["code"]
    return code


def _rows(h: dict[str, str], entity: str) -> dict[str, dict[str, Any]]:
    return {r["_key"]: r for r in client.get(f"{API}/{entity}", headers=h).json()["rows"]}


VENDOR = {"vendor_id": "VEN-TEST", "vendor_name": "PT Uji Coba", "category": "Resin", "lead_time_days": "5", "unit_price_idr": "30000"}


def test_catalog_lists_every_entity_with_a_count(h: dict[str, str]) -> None:
    r = client.get(API, headers=h)
    assert r.status_code == 200
    entities = r.json()["entities"]
    assert [e["key"] for e in entities] == [e.key for e in masterdata.ENTITIES]
    assert all(e["count"] > 0 for e in entities)


def test_each_entity_returns_the_columns_it_declares(h: dict[str, str]) -> None:
    for e in masterdata.ENTITIES:
        body = client.get(f"{API}/{e.key}", headers=h).json()
        keys = [c["key"] for c in body["columns"]]
        assert body["rows"], e.key
        assert set(keys) <= set(body["rows"][0]), e.key


def test_routings_follow_the_planner_and_are_read_only(h: dict[str, str]) -> None:
    body = client.get(f"{API}/routings", headers=h).json()
    arka = [r for r in body["rows"] if r["sku"] == "FB-ARKA"]
    assert [r["process"] for r in arka] == ["Molding", "Painting", "Assembly"]
    assert "IMM-04" in arka[0]["resources"]
    assert body["editable"] is False
    assert _err(client.post(f"{API}/routings", json={"sku": "X"}, headers=h)) == "FV-MD-005"


def test_unknown_entity_is_a_catalogued_404(h: dict[str, str]) -> None:
    r = client.get(f"{API}/nope", headers=h)
    assert r.status_code == 404
    assert _err(r) == "FV-DATA-001"


def test_create_types_the_values_and_marks_the_row(h: dict[str, str]) -> None:
    r = client.post(f"{API}/vendors", json=VENDOR, headers=h)
    assert r.status_code == 200, r.text
    row = _rows(h, "vendors")["VEN-TEST"]
    assert (row["lead_time_days"], row["unit_price_idr"], row["_status"]) == (5, 30000, "created")


def test_create_rejects_a_duplicate_key(h: dict[str, str]) -> None:
    seeded = next(iter(_rows(h, "vendors")))
    r = client.post(f"{API}/vendors", json={**VENDOR, "vendor_id": seeded}, headers=h)
    assert r.status_code == 409
    assert _err(r) == "FV-MD-002"


def test_invalid_fields_are_all_named_at_once(h: dict[str, str]) -> None:
    r = client.post(f"{API}/vendors", json={"vendor_id": "", "vendor_name": "", "lead_time_days": "-1", "unit_price_idr": "abc"}, headers=h)
    assert r.status_code == 400
    assert _err(r) == "FV-MD-001"
    fields = {f["field"] for f in r.json()["error"]["detail"]["fields"]}
    assert fields == {"vendor_id", "vendor_name", "lead_time_days", "unit_price_idr"}


def test_a_reference_must_exist(h: dict[str, str]) -> None:
    r = client.post(f"{API}/molds", json={"mold_id": "MLD-X", "sku": "NOPE", "cavity": 1, "current_machine_id": "IMM-04"}, headers=h)
    assert _err(r) == "FV-MD-001"
    assert r.json()["error"]["detail"]["fields"] == [{"field": "sku", "message": "SKU NOPE does not exist."}]


def test_update_keeps_the_key_and_marks_a_seed_row_edited(h: dict[str, str]) -> None:
    body = {"machine_id": "IMM-99", "machine_name": "IMM-04 rebuilt", "process": "Injection Molding", "tonnage": 3500}
    r = client.put(f"{API}/machines/IMM-04", json=body, headers=h)
    assert r.status_code == 200, r.text
    rows = _rows(h, "machines")
    assert "IMM-99" not in rows
    assert (rows["IMM-04"]["machine_name"], rows["IMM-04"]["_status"]) == ("IMM-04 rebuilt", "edited")


def test_bom_fills_header_and_material_name_from_what_it_names(h: dict[str, str]) -> None:
    item = next(iter(_rows(h, "bom").values()))
    body = {"item_id": "ITEM-T", "bom_id": item["bom_id"], "line_no": 99, "material_id": "PP-EPDM-T20", "qty_per_unit": 0.5}
    r = client.post(f"{API}/bom", json=body, headers=h)
    assert r.status_code == 200, r.text
    row = _rows(h, "bom")["ITEM-T"]
    assert (row["sku"], row["version"], row["material_name"]) == (item["sku"], item["version"], "Resin PP-EPDM talc 20%")


def test_delete_refuses_a_row_still_referenced(h: dict[str, str]) -> None:
    r = client.delete(f"{API}/materials/PP-EPDM-T20", headers=h)
    assert r.status_code == 409
    assert _err(r) == "FV-MD-004"
    assert any(ref["entity"] == "bom" for ref in r.json()["error"]["detail"]["references"])


def test_delete_hides_a_seed_row_and_revert_brings_it_back(h: dict[str, str]) -> None:
    assert client.delete(f"{API}/defects/FIBER_SEED", headers=h).status_code == 200
    body = client.get(f"{API}/defects", headers=h).json()
    assert "FIBER_SEED" not in {r["_key"] for r in body["rows"]}
    assert [d["_key"] for d in body["deleted"]] == ["FIBER_SEED"]
    assert client.post(f"{API}/defects/FIBER_SEED/revert", headers=h).status_code == 200
    assert "_status" not in _rows(h, "defects")["FIBER_SEED"]


def test_deleting_a_created_row_leaves_nothing_behind(h: dict[str, str]) -> None:
    client.post(f"{API}/vendors", json=VENDOR, headers=h)
    assert client.delete(f"{API}/vendors/VEN-TEST", headers=h).status_code == 200
    body = client.get(f"{API}/vendors", headers=h).json()
    assert "VEN-TEST" not in {r["_key"] for r in body["rows"]}
    assert body["deleted"] == []


def test_composite_key_rows_are_addressed_by_their_joined_key(h: dict[str, str]) -> None:
    key = next(iter(_rows(h, "mold-compatibility")))
    assert client.delete(f"{API}/mold-compatibility/{key}", headers=h).status_code == 200
    assert key not in _rows(h, "mold-compatibility")


def test_edits_stay_in_their_session_and_reset_clears_them(h: dict[str, str]) -> None:
    client.post(f"{API}/vendors", json=VENDOR, headers=h)
    other = {"X-Session-Id": str(uuid.uuid4())}
    assert "VEN-TEST" not in _rows(other, "vendors")
    assert client.post("/api/demo/reset", headers=h).status_code == 200
    assert "VEN-TEST" not in _rows(h, "vendors")
