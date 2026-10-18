import re
from typing import Any

import pytest
from app import materials, plant, warehouse
from app.errors import FVError
from app.tools import orders


def test_material_master_has_every_guide_material() -> None:
    ids = {m.material_id for m in plant.MATERIALS}
    assert {"B-BASE", "BRK-SET", "PKG-RT"}.isdisjoint(ids)
    assert {f"B-{c}" for c in ("C01", "C02", "C04", "C05", "C06", "C07", "C08", "C09", "C10", "C11", "C12", "C16", "C17")} <= ids
    assert len([m for m in plant.MATERIALS if m.material_group == "Basecoat"]) == 13
    assert {"P1", "P2", "K1", "K2", "H1", "H2", "T1", "T2", "T3", "T4", "SNS-HLD", "MB-BLK", "PP-EPDM-T20"} <= ids
    assert {f"BRK-{m}-{p}" for m in ("ARKA", "BROMO", "CITRA") for p in "FR"} <= ids
    assert {f"PKG-RT-{m}" for m in ("ARKA", "BROMO", "CITRA")} <= ids
    for m in plant.MATERIALS:
        assert m.material_group in plant.MATERIAL_GROUPS
        assert m.default_vendor_id in plant.VENDOR_BY_ID
        assert m.default_warehouse_id in plant.WAREHOUSE_BY_ID
        assert (m.color is not None) == (m.material_group == "Basecoat")
        assert m.reorder_point >= m.safety_stock > 0 or m.material_id == "PP-EPDM-T20"
    assert set(plant.MATERIAL_VENDORS) == ids


def test_resin_master_keeps_the_story_numbers() -> None:
    r = plant.MATERIAL_BY_ID["PP-EPDM-T20"]
    assert (r.safety_stock, r.unit_cost_idr, r.default_warehouse_id) == (12_000, 31_500, "WH-RM")
    legacy = {(b["sku"], b["material_id"]): b["qty_per_unit"] for b in plant.bom_lines()}
    for p in plant.PRODUCTS:
        assert legacy[(p.sku, "PP-EPDM-T20")] == round(p.shot_weight_kg * 0.97, 3)
        assert legacy[(p.sku, "MB-BLK")] == round(p.shot_weight_kg * 0.03, 3)
    assert {b["material_id"] for b in plant.bom_lines()} <= set(plant.MATERIAL_BY_ID)


def test_bom_specs_are_consistent_with_one_active_per_sku() -> None:
    assert plant.bom_problems() == []
    for h in plant.BOM_HEADERS:
        assert re.fullmatch(r"BOM-2027-\d{3}", h.bom_id)
        assert h.status in plant.BOM_STATUSES
    for p in plant.PRODUCTS:
        assert [h.status for h in plant.BOM_HEADERS if h.sku == p.sku].count("Active") == 1
    assert [h.sku for h in plant.BOM_HEADERS if h.status == "Inactive"] == ["FB-ARKA"]


def test_fb_arka_c07_matches_the_guide_example() -> None:
    bid = next(h.bom_id for h in plant.BOM_HEADERS if h.sku == "FB-ARKA" and h.status == "Active")
    items = {(i.material_id, i.process): i for i in plant.BOM_ITEMS if i.bom_id == bid and i.color in (None, "C07")}
    assert set(items) == {
        ("PP-EPDM-T20", "Molding"),
        ("MB-BLK", "Molding"),
        ("P1", "Painting"),
        ("B-C07", "Painting"),
        ("T2", "Painting"),
        ("K1", "Painting"),
        ("H1", "Painting"),
        ("T4", "Painting"),
        ("BRK-ARKA-F", "Assembly"),
        ("PKG-RT-ARKA", "Packing"),
    }
    assert (items[("B-C07", "Painting")].qty_per_unit, items[("B-C07", "Painting")].scrap_pct, items[("B-C07", "Painting")].color) == (0.3, 30.0, "C07")
    assert items[("T2", "Painting")].qty_per_unit == 0.45
    assert items[("PP-EPDM-T20", "Molding")].scrap_pct == 2.0


@pytest.mark.parametrize("sku", plant.SKUS)
def test_every_colour_has_a_basecoat_and_its_family_thinner(sku: str) -> None:
    p = plant.PRODUCT_BY_SKU[sku]
    bid = next(h.bom_id for h in plant.BOM_HEADERS if h.sku == sku and h.status == "Active")
    for c in p.colors:
        mine = {i.material_id for i in plant.BOM_ITEMS if i.bom_id == bid and i.color == c}
        assert mine == {f"B-{c}", plant.FAMILY_THINNER[plant.COLOR_FAMILY[c]]}
    plain = {i.material_id for i in plant.BOM_ITEMS if i.bom_id == bid and i.color is None}
    assert plant.MODEL_PAINT_SYSTEM[p.model]["primer"] in plain
    assert ("SNS-HLD" in plain) == (p.position == "Rear")


def test_colours_per_model_and_sku() -> None:
    assert plant.model_colors()["ARKA"] == list(plant.ARKA_COLORS)
    assert plant.sku_colors("RB-CITRA") == list(plant.CITRA_COLORS)
    assert plant.sku_colors("XX") == []
    assert set(plant.BASECOAT_COLORS) == set(plant.COLOR_FAMILY)


def _snap(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(warehouse, "query", lambda _sql, _p=None: rows)


def test_color_split_uses_hourly_use_and_sums_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    _snap(monkeypatch, [{"sku": "FB-CITRA", "color": c, "hourly_use": w} for c, w in zip(plant.CITRA_COLORS, (3.0, 2.0, 1.0, 1.0), strict=True)])
    s = materials.color_split("FB-CITRA", 100.4)
    assert sum(s.values()) == 100
    assert s == {"C01": 43, "C06": 29, "C17": 14, "C16": 14}
    assert sum(materials.color_split("FB-CITRA", 3).values()) == 3


def test_color_split_falls_back_to_equal_shares(monkeypatch: pytest.MonkeyPatch) -> None:
    _snap(monkeypatch, [])
    assert materials.color_split("FB-ARKA", 6) == dict.fromkeys(plant.ARKA_COLORS, 1)
    assert sum(materials.color_split("FB-BROMO", 10).values()) == 10
    with pytest.raises(FVError) as e:
        materials.color_split("XX", 1)
    assert e.value.code == "FV-MAT-006"


def test_colour_check_raises_fv_mat_003() -> None:
    materials.check_color("FB-ARKA", "C07")
    materials.check_color("FB-ARKA", None)
    with pytest.raises(FVError) as e:
        materials.check_color("FB-ARKA", "C17")
    assert e.value.code == "FV-MAT-003"
    assert e.value.http_status == 400


def _order(lines: list[dict[str, Any]]) -> dict[str, Any]:
    return {"customer_id": "CUS-EXP", "order_channel": "MANUAL", "order_date": "2027-02-01", "requested_delivery_date": "2027-02-12", "lines": lines}


def test_order_validation_checks_colour_per_line() -> None:
    errs, warns = orders.validate_order(_order([{"sku": "FB-ARKA", "color": "C07", "qty": 10}, {"sku": "FB-ARKA", "color": "C02", "qty": 5}]), {"orders": []})
    assert errs == []
    assert warns == []
    errs, _ = orders.validate_order(_order([{"sku": "FB-ARKA", "color": "C17", "qty": 10}]), {"orders": []})
    assert [e["field"] for e in errs] == ["lines.0.color"]
    assert "C17" in errs[0]["message"]
    errs, _ = orders.validate_order(_order([{"sku": "FB-ARKA", "color": "C07", "qty": 1}, {"sku": "FB-ARKA", "color": "C07", "qty": 2}]), {"orders": []})
    assert [e["field"] for e in errs] == ["lines.1.sku"]
    errs, warns = orders.validate_order(_order([{"sku": "RB-BROMO", "qty": 3}]), {"orders": []})
    assert errs == []
    assert any("No colour on RB-BROMO" in w for w in warns)


def test_new_order_lines_carry_colour() -> None:
    o = orders.new_order(_order([{"sku": "FB-CITRA", "color": "C16", "qty": 4}, {"sku": "RB-CITRA", "qty": 2}]), "You", "2027-02-01T00:00:00+00:00")
    assert [ln["color"] for ln in o["lines"]] == ["C16", None]
    assert {p["sku"]: p["colors"] for p in orders.products()}["FB-ARKA"] == list(plant.ARKA_COLORS)
