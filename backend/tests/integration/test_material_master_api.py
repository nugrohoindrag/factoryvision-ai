import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from app import actions, capacity, effects, forecasts, materials, plans, plant, pubsub
from app.errors import FVError
from app.main import app
from app.store import store
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)


def _accept(_message: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True}


@pytest.fixture
def sid(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    s = str(uuid.uuid4())
    monkeypatch.setitem(pubsub._handlers, pubsub.AGENT_COMMANDS, _accept)
    yield s
    materials.clear(s)
    for r in store().where(materials.RESERVATIONS, "session_id", s):
        store().delete(materials.RESERVATIONS, f"{s}:{r['reservation_id']}")
    plans.clear(s)
    capacity.clear(s)
    forecasts.clear(s)
    actions.clear(s)


def h(s: str) -> dict[str, str]:
    return {"X-Session-Id": s}


def _code(r: Any) -> str:
    code: str = r.json()["error"]["code"]
    return code


def _row(rows: list[dict[str, Any]], mid: str) -> dict[str, Any]:
    return next(r for r in rows if r["material_id"] == mid)


def test_master_materials_and_warehouses_come_from_the_warehouse() -> None:
    r = client.get("/api/master/materials").json()
    assert len(r["materials"]) == len(plant.MATERIALS)
    assert {m["material_id"] for m in r["materials"]} == set(plant.MATERIAL_BY_ID)
    paint = client.get("/api/master/materials", params={"group": "Basecoat"}).json()["materials"]
    assert len(paint) == 13
    assert all(m["color"] and m["default_warehouse_id"] == "WH-PNT" for m in paint)
    bad = client.get("/api/master/materials", params={"group": "Glue"})
    assert (bad.status_code, _code(bad)) == (400, "FV-MAT-008")
    assert [w["warehouse_id"] for w in client.get("/api/master/warehouses").json()["warehouses"]] == ["WH-RM", "WH-PNT", "WH-CMP"]
    with pytest.raises(FVError) as e:
        materials.get_material("B-BASE")
    assert e.value.code == "FV-MAT-002"


def test_master_colors_list_models_skus_and_families() -> None:
    r = client.get("/api/master/colors").json()
    assert r["models"]["BROMO"] == list(plant.BROMO_COLORS)
    assert r["skus"]["FB-ARKA"] == list(plant.ARKA_COLORS)
    c07 = next(c for c in r["colors"] if c["color"] == "C07")
    assert c07 == {"color": "C07", "family": "Metallic", "basecoat_id": "B-C07", "thinner_id": "T2", "models": ["ARKA"]}


def test_master_boms_filter_and_detail() -> None:
    boms = client.get("/api/master/boms").json()["boms"]
    assert len(boms) == 7
    active = client.get("/api/master/boms", params={"status": "Active"}).json()["boms"]
    assert sorted(b["sku"] for b in active) == sorted(plant.SKUS)
    arka = client.get("/api/master/boms", params={"sku": "FB-ARKA"}).json()["boms"]
    assert [(b["version"], b["status"]) for b in arka] == [("v1.0", "Inactive"), ("v2.0", "Active")]
    one = client.get(f"/api/master/boms/{arka[1]['bom_id']}").json()
    assert one["colors"] == sorted(plant.ARKA_COLORS)
    assert all(i["material_name"] and i["material_group"] for i in one["items"])
    assert [i["line_no"] for i in one["items"]] == list(range(1, len(one["items"]) + 1))
    assert _code(client.get("/api/master/boms/BOM-1999-001")) == "FV-MAT-005"
    assert _code(client.get("/api/master/boms", params={"status": "Live"})) == "FV-MAT-009"
    assert _code(client.get("/api/master/boms", params={"sku": "XX"})) == "FV-MAT-006"


def test_active_bom_and_colour_items() -> None:
    b = materials.active_bom("FB-ARKA")
    assert (b["bom_id"], b["version"]) == ("BOM-2027-002", "v2.0")
    c07 = materials.bom_items_for("FB-ARKA", "C07")
    assert {i["material_id"] for i in c07 if i["color"]} == {"B-C07", "T2"}
    assert not any(i["basecoat_unresolved"] for i in c07)
    none = materials.bom_items_for("FB-ARKA", None)
    assert none
    assert all(i["color"] is None and i["basecoat_unresolved"] for i in none)
    with pytest.raises(FVError) as e:
        materials.bom_items_for("FB-ARKA", "C16")
    assert e.value.code == "FV-MAT-003"
    with pytest.raises(FVError) as e:
        materials.active_bom("XX-1")
    assert e.value.code == "FV-MAT-001"
    assert "XX-1" in e.value.message


def test_bom_with_unknown_material_is_an_error_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    real = materials._boms()
    broken = tuple(
        {**b, "items": [*b["items"], {**b["items"][0], "material_id": "GHOST", "material_name": None}]} if b["sku"] == "RB-CITRA" else b for b in real
    )
    monkeypatch.setattr(materials, "_boms", lambda: broken)
    with pytest.raises(FVError) as e:
        materials.active_bom("RB-CITRA")
    assert e.value.code == "FV-MAT-007"
    assert e.value.detail == {"sku": "RB-CITRA", "bom_id": "BOM-2027-007", "materials": ["GHOST"]}


def test_color_split_reads_the_stock_snapshot() -> None:
    s = materials.color_split("FB-ARKA", 1000)
    assert sum(s.values()) == 1000
    assert list(s) == list(plant.ARKA_COLORS)
    assert s["C02"] > s["C07"] > s["C05"]


def test_inventory_covers_every_material_with_planted_conditions(sid: str) -> None:
    r = client.get("/api/inventory", headers=h(sid)).json()
    rows = r["rows"]
    assert len(rows) == len(plant.MATERIALS)
    assert r["kpis"]["materials"] == len(plant.MATERIALS)
    assert r["kpis"]["incoming_pos"] == 4
    assert r["kpis"]["value_idr"] == sum(x["value_idr"] for x in rows)
    for x in rows:
        assert x["available_qty"] == round(x["on_hand_qty"] - x["reserved_qty"] + x["incoming_qty"], 1)
        assert x["below_reorder"] == (x["on_hand_qty"] - x["reserved_qty"] < x["reorder_point"])
    resin = _row(rows, "PP-EPDM-T20")
    assert (resin["warehouse_id"], resin["incoming_qty"]) == ("WH-RM", 0)
    brk = _row(rows, "BRK-ARKA-F")
    assert brk["below_reorder"]
    assert brk["incoming"][0]["source"] == "open_po"
    c07 = _row(rows, "B-C07")
    assert c07["on_hand_qty"] - c07["reserved_qty"] == 590
    assert c07["incoming"][0]["expected_date"] > "2027-02-06"
    assert {x["material_id"] for x in rows if x["below_reorder"]} == {"B-C07", "BRK-ARKA-F"}
    assert r["kpis"]["below_reorder"] == 2
    paint = client.get("/api/inventory", params={"warehouse_id": "WH-PNT", "group": "Thinner"}, headers=h(sid)).json()["rows"]
    assert [x["material_id"] for x in paint] == ["T1", "T2", "T3", "T4"]
    assert _code(client.get("/api/inventory", params={"warehouse_id": "WH-X"}, headers=h(sid))) == "FV-MAT-004"
    assert _code(client.get("/api/inventory", params={"material_id": "NOPE"}, headers=h(sid))) == "FV-MAT-002"


def test_b_c07_is_enough_for_this_week_but_not_at_plus_40(sid: str) -> None:
    open_c07 = [ln for o in plans.order_view(sid)["orders"] for ln in o["lines"] if ln["sku"].endswith("-ARKA") and ln.get("color") == "C07"]
    open_c07 = [ln for ln in open_c07 if "2027-02-01" <= ln["requested_delivery_date"] <= "2027-02-06"]
    per_unit = next(i for i in materials.bom_items_for("FB-ARKA", "C07") if i["material_id"] == "B-C07")
    k = per_unit["qty_per_unit"] * (1 + per_unit["scrap_pct"] / 100)
    week = sum(ln["ordered_qty"] for ln in open_c07) * k
    plus40 = sum(ln["ordered_qty"] * (1.4 if ln["sku"] == "FB-ARKA" else 1) for ln in open_c07) * k
    free = _row(materials.stock(sid, "B-C07"), "B-C07")
    usable = free["on_hand_qty"] - free["reserved_qty"]
    assert week < usable < plus40


def test_incoming_dates_open_pos_and_includes_approved_pos(sid: str, monkeypatch: pytest.MonkeyPatch) -> None:
    inc = materials.incoming(sid)
    assert {i["material_id"] for i in inc} == {"BRK-ARKA-F", "B-C07", "P1", "K1"}
    for i in inc:
        assert i["expected_date"] >= i["promised_date"]
    base = effects.overrides(sid)
    o = effects.Overrides(**{**base.as_dict(), "extra_receipts": [{"po_id": "PO-S-1", "material_id": "B-C07", "qty": 300.0, "expected_date": "2027-02-03"}]})
    monkeypatch.setattr(effects, "overrides", lambda _sid: o)
    mine = materials.incoming(sid, "B-C07")
    assert [(i["po_id"], i["source"]) for i in mine] == [("PO-S-1", "approved_po"), ("PO-2702-030", "open_po")]
    row = _row(materials.stock(sid, "B-C07"), "B-C07")
    assert row["incoming_qty"] == 420


def test_session_reservations_raise_reserved(sid: str) -> None:
    before = _row(materials.stock(sid, "P1"), "P1")
    doc = {"reservation_id": "R-1", "session_id": sid, "material_id": "P1", "warehouse_id": "WH-PNT", "qty": 100.0, "status": "Active"}
    store().set(materials.RESERVATIONS, f"{sid}:R-1", doc)
    store().set(materials.RESERVATIONS, f"{sid}:R-2", {**doc, "reservation_id": "R-2", "status": "Released"})
    after = _row(materials.stock(sid, "P1"), "P1")
    assert after["reserved_qty"] == before["reserved_qty"] + 100
    assert after["available_qty"] == before["available_qty"] - 100
    other = _row(materials.stock(str(uuid.uuid4()), "P1"), "P1")
    assert other["reserved_qty"] == before["reserved_qty"]


def test_transactions_ledger_ends_at_on_hand_and_takes_session_rows(sid: str) -> None:
    resin = client.get("/api/inventory/transactions", params={"material_id": "PP-EPDM-T20"}, headers=h(sid)).json()["transactions"]
    assert resin
    assert {t["type"] for t in resin} <= {"Receipt", "Issue"}
    on_hand = _row(materials.stock(sid, "PP-EPDM-T20"), "PP-EPDM-T20")["on_hand_qty"]
    assert resin[-1]["balance_after"] == pytest.approx(on_hand, abs=1)
    k1 = materials.transactions(sid, "K1")
    assert [(t["type"], t["ref"]) for t in k1] == [("Adjust", "Opening balance (stock take)")]
    materials.record_transaction(sid, {"material_id": "K1", "type": "Reserve", "qty": -50, "ref": "WO-1"})
    k1 = materials.transactions(sid, "K1")
    assert [t["type"] for t in k1] == ["Adjust", "Reserve"]
    assert k1[-1]["balance_after"] == k1[0]["balance_after"]
    assert k1[-1]["source"] == "session"
    assert materials.transactions(str(uuid.uuid4()), "K1")[-1]["type"] == "Adjust"
    materials.clear(sid)
    assert len(materials.transactions(sid, "K1")) == 1
    with pytest.raises(ValueError, match="transaction type"):
        materials.record_transaction(sid, {"material_id": "K1", "type": "Move", "qty": 1})


def test_demo_reset_clears_the_ledger(sid: str) -> None:
    materials.record_transaction(sid, {"material_id": "T1", "type": "Adjust", "qty": 5, "ref": "count"})
    assert client.post("/api/demo/reset", json={"scenario": "baseline"}, headers=h(sid)).status_code == 200
    assert store().where(materials.TRANSACTIONS, "session_id", sid) == []


NEW = {
    "customer_id": "CUS-EXP",
    "order_channel": "MANUAL",
    "order_date": "2027-02-01",
    "requested_delivery_date": "2027-02-12",
    "lines": [{"sku": "FB-ARKA", "color": "C07", "qty": 300}, {"sku": "FB-ARKA", "color": "C02", "qty": 100}],
}


def test_order_colour_is_validated_and_shown(sid: str) -> None:
    bad = client.post("/api/orders", json={**NEW, "lines": [{"sku": "FB-ARKA", "color": "C17", "qty": 5}]}, headers=h(sid))
    assert (bad.status_code, _code(bad)) == (400, "FV-MAT-003")
    assert bad.json()["error"]["detail"]["fields"][0]["field"] == "lines.0.color"
    v = client.post("/api/orders/validate", json={**NEW, "lines": [{"sku": "FB-ARKA", "color": "C17", "qty": 5}]}, headers=h(sid)).json()
    assert v["errors"][0]["field"] == "lines.0.color"
    r = client.post("/api/orders", json=NEW, headers=h(sid))
    assert r.status_code == 200, r.text
    o = r.json()["order"]
    assert [(ln["sku"], ln["color"]) for ln in o["lines"]] == [("FB-ARKA", "C07"), ("FB-ARKA", "C02")]
    listed = client.get("/api/orders", headers=h(sid)).json()
    assert {p["sku"]: p["colors"] for p in listed["products"]}["FB-ARKA"] == list(plant.ARKA_COLORS)
    p = client.post("/api/plans", json={"period_start": "2027-02-01", "period_end": "2027-02-28"}, headers=h(sid)).json()
    cands = client.get(f"/api/plans/{p['plan_id']}/demand-candidates", headers=h(sid)).json()["candidates"]
    mine = next(c for c in cands if c["order_number"] == o["order_number"] and c["color"] == "C07")
    assert client.post(f"/api/plans/{p['plan_id']}/demand", json={"order_id": mine["order_id"]}, headers=h(sid)).status_code == 200
    src = client.get(f"/api/plans/{p['plan_id']}/demand", headers=h(sid)).json()["lines"][0]["sources"]
    assert src[0]["color"] == "C07"


def test_seed_orders_carry_colours_of_their_model(sid: str) -> None:
    lines = [ln for o in plans.order_view(sid)["orders"] for ln in o["lines"]]
    assert all(ln["color"] in plant.sku_colors(ln["sku"]) for ln in lines)
