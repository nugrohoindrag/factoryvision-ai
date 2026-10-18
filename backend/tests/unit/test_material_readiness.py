import uuid
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from app import materials, mrp, plans, readiness
from app.errors import FVError
from app.store import store
from app.tools import planning

MATS: list[dict[str, Any]] = [
    {"material_id": "R", "material_name": "Resin", "material_group": "Resin", "color": None, "unit": "kg", "lead_time_days": 5, "default_vendor_id": "V1"},
    {
        "material_id": "B-C01",
        "material_name": "Base C01",
        "material_group": "Basecoat",
        "color": "C01",
        "unit": "kg",
        "lead_time_days": 6,
        "default_vendor_id": "V2",
    },
    {
        "material_id": "B-C02",
        "material_name": "Base C02",
        "material_group": "Basecoat",
        "color": "C02",
        "unit": "kg",
        "lead_time_days": 6,
        "default_vendor_id": "V2",
    },
    {
        "material_id": "BRK",
        "material_name": "Bracket",
        "material_group": "Component",
        "color": None,
        "unit": "set",
        "lead_time_days": 4,
        "default_vendor_id": "V3",
    },
]


def _items(sku: str, color: str | None) -> list[dict[str, Any]]:
    out = [
        {"material_id": "R", "qty_per_unit": 2.0, "unit": "kg", "scrap_pct": 10.0, "process": "Molding", "color": None},
        {"material_id": "BRK", "qty_per_unit": 1.0, "unit": "set", "scrap_pct": 0.0, "process": "Packing", "color": None},
    ]
    if color:
        out.append({"material_id": f"B-{color}", "qty_per_unit": 0.5, "unit": "kg", "scrap_pct": 0.0, "process": "Painting", "color": color})
    return out


class World:
    def __init__(self) -> None:
        self.on_hand: dict[str, float] = {"R": 1000.0, "B-C01": 100.0, "B-C02": 0.0, "BRK": 500.0}
        self.safety: dict[str, float] = {"R": 50.0}
        self.incoming: list[dict[str, Any]] = []

    def stock(self, sid: str, material_id: str | None = None, warehouse_id: str | None = None) -> list[dict[str, Any]]:
        res: dict[str, float] = {}
        for r in store().where(readiness.RESERVATIONS, "session_id", sid):
            if r["status"] == "Active":
                res[r["material_id"]] = res.get(r["material_id"], 0.0) + float(r["qty"])
        return [
            {"material_id": m, "warehouse_id": "WH", "on_hand_qty": q, "reserved_qty": res.get(m, 0.0), "safety_stock": self.safety.get(m, 0.0)}
            for m, q in self.on_hand.items()
            if (material_id is None or m == material_id) and warehouse_id in (None, "WH")
        ]

    def incoming_rows(self, _sid: str, material_id: str | None = None) -> list[dict[str, Any]]:
        return [r for r in self.incoming if material_id is None or r["material_id"] == material_id]


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    w = World()
    monkeypatch.setattr(materials, "list_materials", lambda **_: [dict(m) for m in MATS])
    monkeypatch.setattr(materials, "bom_items_for", _items)
    monkeypatch.setattr(materials, "color_split", lambda _sku, qty: {"C01": round(qty * 0.6), "C02": round(qty) - round(qty * 0.6)})
    monkeypatch.setattr(materials, "stock", w.stock)
    monkeypatch.setattr(materials, "incoming", w.incoming_rows)
    monkeypatch.setattr(materials, "record_transaction", _record)
    monkeypatch.setattr(planning, "monthly_plan", lambda *_: {})
    return w


def _record(sid: str, row: dict[str, Any]) -> dict[str, Any]:
    doc = {**row, "session_id": sid}
    store().set(mrp.TRANSACTIONS, f"{sid}:{row['txn_id']}", doc)
    return doc


@pytest.fixture
def sid() -> Iterator[str]:
    s = str(uuid.uuid4())
    yield s
    mrp.clear(s)
    plans.clear(s)


def _plan(
    sid: str, pid: str, *, qty: int = 100, color: str | None = "C01", wos: list[tuple[str, str, str | None]] | None = None, status: str = "Planning"
) -> None:
    line = {
        "line_id": f"L-{pid}",
        "sku": "FB-X",
        "product_name": "X",
        "demand_qty": qty,
        "forecast_qty": 0,
        "planned_qty": qty,
        "priority": 1,
        "required_date": "2027-02-20",
        "status": "Open",
        "capacity": None,
    }
    demand = [
        {
            "order_id": f"O-{pid}",
            "order_number": "CO-1",
            "line_no": 1,
            "line_id": line["line_id"],
            "sku": "FB-X",
            "qty": qty,
            "color": color,
            "requested_delivery_date": "2027-02-20",
        }
    ]
    store().set(
        plans.PLANS,
        pid,
        {
            "plan_id": pid,
            "plan_number": f"PP-{pid}",
            "session_id": sid,
            "period_start": "2027-02-01",
            "period_end": "2027-02-28",
            "status": status,
            "created_at": "2027-02-01T00:00:00+00:00",
            "wizard_step": 4,
            "origin": "user",
            "lines": [line],
            "demand": demand,
        },
    )
    for wo_id, process, start in wos or []:
        store().set(
            plans.WOS,
            f"{sid}/{wo_id}",
            {
                "wo_id": wo_id,
                "session_id": sid,
                "plan_id": pid,
                "line_id": line["line_id"],
                "sku": "FB-X",
                "process": process,
                "routing_step": 1,
                "qty": qty,
                "planned_start": start,
                "planned_end": start,
                "machine_id": "M",
                "mold_id": "MO",
                "status": "Draft",
                "confirmed": False,
            },
        )


# --- explosion and collapse -------------------------------------------------------------------------------------------


def test_required_applies_scrap() -> None:
    assert readiness.required_for(2.0, 100, 10) == pytest.approx(220.0)
    assert readiness.required_for(1.0, 50, None) == 50


def test_collapse_sums_per_material_and_merges_sources() -> None:
    rows = [
        {"material_id": "R", "required_qty": 1.5, "sources": [{"sku": "A", "color": None, "qty": 1}]},
        {"material_id": "K", "required_qty": 2.0, "sources": [{"sku": "A", "color": None, "qty": 1}]},
        {"material_id": "R", "required_qty": 2.5, "sources": [{"sku": "A", "color": None, "qty": 2}, {"sku": "B", "color": "C01", "qty": 3}]},
    ]
    out = readiness.collapse(rows)
    assert [r["material_id"] for r in out] == ["R", "K"]
    assert out[0]["required_qty"] == 4.0
    assert out[0]["sources"] == [{"sku": "A", "color": None, "qty": 3}, {"sku": "B", "color": "C01", "qty": 3}]


def test_explode_scrap_colour_and_split(world: World) -> None:
    out = {r["material_id"]: r for r in readiness.explode([{"sku": "FB-X", "color": "C01", "qty": 100}, {"sku": "FB-X", "color": None, "qty": 50}])}
    assert out["R"]["required_qty"] == pytest.approx(2 * 150 * 1.1)
    assert out["B-C01"]["required_qty"] == pytest.approx(0.5 * (100 + 30))
    assert out["B-C02"]["required_qty"] == pytest.approx(0.5 * 20)
    assert out["B-C01"]["color"] == "C01"
    assert {"sku": "FB-X", "color": "C02", "qty": 20.0} in out["B-C02"]["sources"]


def test_explode_unknown_material_is_an_error(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(materials, "bom_items_for", lambda *_: [{"material_id": "NOPE", "qty_per_unit": 1, "unit": "kg", "process": "Molding"}])
    with pytest.raises(FVError) as e:
        readiness.explode([{"sku": "FB-X", "color": "C01", "qty": 1}])
    assert e.value.code == "FV-MR-005"
    assert e.value.detail == {"sku": "FB-X", "material_id": "NOPE"}


def test_line_parts_coloured_demand_and_remainder() -> None:
    line = {"line_id": "L", "sku": "S", "planned_qty": 100}
    demand: list[dict[str, Any]] = [
        {"line_id": "L", "color": "C01", "qty": 30},
        {"line_id": "L", "color": None, "qty": 20},
        {"line_id": "X", "color": "C02", "qty": 9},
    ]
    assert readiness.line_parts(line, demand) == [{"sku": "S", "color": "C01", "qty": 30}, {"sku": "S", "color": None, "qty": 70}]
    over: list[dict[str, Any]] = [{"line_id": "L", "color": "C01", "qty": 100}, {"line_id": "L", "color": "C02", "qty": 50}]
    parts = readiness.line_parts({**line, "planned_qty": 90}, over)
    assert sum(p["qty"] for p in parts) == 90
    assert parts == [{"sku": "S", "color": "C01", "qty": 60}, {"sku": "S", "color": "C02", "qty": 30}]
    assert readiness.line_parts({**line, "planned_qty": 0}, demand) == []


def test_scale_sums_exactly() -> None:
    out = readiness.scale({"a": 1, "b": 1, "c": 1}, 100)
    assert sum(out.values()) == 100
    assert readiness.scale({}, 10) == {}


# --- available, shortage, status ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("required", "available", "status"),
    [(10, 10, "READY"), (10, 11, "READY"), (0, 0, "READY"), (10, 9.99, "PARTIAL"), (10, 0.01, "PARTIAL"), (10, 0, "SHORTAGE"), (10, -5, "SHORTAGE")],
)
def test_status_boundaries(required: float, available: float, status: str) -> None:
    assert readiness.status_of(required, available) == status


def _req(qty: float) -> dict[str, Any]:
    return {"material_id": "M", "material_name": "M", "material_group": "Resin", "color": None, "unit": "kg", "required_qty": qty}


def test_available_and_shortage_formula() -> None:
    r = readiness.material_row(_req(100), "2027-02-10", {"on_hand_qty": 80, "reserved_qty": 30}, [{"po_id": "PO1", "qty": 20, "expected_date": "2027-02-10"}])
    assert r["available_qty"] == 70
    assert r["shortage_qty"] == 30
    assert r["status"] == "PARTIAL"
    assert r["incoming_counted_qty"] == 20


def test_incoming_counts_only_by_the_need_date() -> None:
    inc = [{"po_id": "PO1", "qty": 50, "expected_date": "2027-02-05"}, {"po_id": "PO2", "qty": 40, "expected_date": "2027-02-11"}]
    r = readiness.material_row(_req(100), "2027-02-10", {"on_hand_qty": 20}, inc)
    assert r["incoming_qty"] == 90
    assert r["incoming_counted_qty"] == 50
    assert [i["counted"] for i in r["incoming"]] == [True, False]
    assert r["available_qty"] == 70
    assert "PO2" in r["note"]
    assert readiness.LATE_NOTE in r["note"]


def test_own_reservations_do_not_block_the_owner() -> None:
    r = readiness.material_row(_req(50), "2027-02-10", {"on_hand_qty": 50, "reserved_qty": 50}, [], own_reserved=50)
    assert r["reserved_qty"] == 0
    assert r["status"] == "READY"


def test_at_risk_when_stock_after_use_drops_below_safety() -> None:
    r = readiness.material_row(_req(80), "2027-02-10", {"on_hand_qty": 100, "safety_stock": 30}, [])
    assert r["status"] == "READY"
    assert r["at_risk"] is True
    r = readiness.material_row(_req(60), "2027-02-10", {"on_hand_qty": 100, "safety_stock": 30}, [])
    assert r["at_risk"] is False
    r = readiness.material_row(_req(200), "2027-02-10", {"on_hand_qty": 100, "safety_stock": 30}, [])
    assert r["status"] == "PARTIAL"
    assert r["at_risk"] is False


def test_used_before_reduces_available_and_never_goes_negative() -> None:
    r = readiness.material_row(_req(50), "2027-02-10", {"on_hand_qty": 100}, [], used_before=70)
    assert r["available_qty"] == 30
    assert r["status"] == "PARTIAL"
    r = readiness.material_row(_req(50), "2027-02-10", {"on_hand_qty": 100}, [], used_before=170)
    assert r["available_qty"] == 0
    assert r["status"] == "SHORTAGE"
    assert r["shortage_qty"] == 50


def test_plan_status_and_percentage() -> None:
    def rows(*s: str) -> list[dict[str, Any]]:
        return [{"status": x} for x in s]

    assert readiness.summarise([]) == ("NOT_CHECKED", 0.0)
    assert readiness.summarise(rows("READY", "READY")) == ("READY", 100.0)
    assert readiness.summarise(rows("READY", "PARTIAL", "SHORTAGE")) == ("PARTIAL", 33.3)
    assert readiness.summarise(rows("PARTIAL", "SHORTAGE")) == ("SHORTAGE", 0.0)


def test_range_validation() -> None:
    assert readiness.parse_range(None, None) == (date(2027, 2, 1), date(2027, 3, 3))
    for bad in (("2027-02-10", "2027-02-01"), ("nope", None)):
        with pytest.raises(FVError) as e:
            readiness.parse_range(*bad)
        assert e.value.code == "FV-MR-003"


# --- plan and work-order checks -------------------------------------------------------------------------------------


def test_requirement_date_per_process_and_fallback(world: World, sid: str) -> None:
    _plan(sid, "p1", wos=[("W-MOLD", "Molding", "2027-02-03T06:00:00"), ("W-PAINT", "Painting", "2027-02-05T06:00:00")])
    r = readiness.check_plan(sid, "p1")
    dates = {m["material_id"]: m["required_date"] for m in r["materials"]}
    assert dates == {"R": "2027-02-03", "B-C01": "2027-02-05", "BRK": "2027-02-20"}
    assert r["required_date"] == "2027-02-03"
    _plan(sid, "p2")
    assert {m["required_date"] for m in readiness.check_plan(sid, "p2")["materials"]} == {"2027-02-20"}


def test_plan_check_statuses_and_saved(world: World, sid: str) -> None:
    _plan(sid, "p1", qty=100, color="C02")
    assert readiness.latest_check(sid, "p1")["status"] == "NOT_CHECKED"
    r = readiness.check_plan(sid, "p1")
    by = {m["material_id"]: m["status"] for m in r["materials"]}
    assert by == {"R": "READY", "BRK": "READY", "B-C02": "SHORTAGE"}
    assert r["status"] == "PARTIAL"
    assert r["readiness_pct"] == 66.7
    assert [m["material_id"] for m in r["shortages"]] == ["B-C02"]
    saved = readiness.latest_check(sid, "p1")
    assert saved["checked_at"] == r["checked_at"]
    assert saved["status"] == "PARTIAL"


def test_sequential_consumption_later_work_order_sees_what_is_left(world: World, sid: str) -> None:
    world.on_hand["R"] = 300.0
    _plan(sid, "p1", qty=100, wos=[("W-A", "Molding", "2027-02-02T06:00:00")])
    _plan(sid, "p2", qty=100, wos=[("W-B", "Molding", "2027-02-04T06:00:00")])
    first = readiness.check_work_order(sid, "p1", "W-A")
    second = readiness.check_work_order(sid, "p2", "W-B")
    assert [m["material_id"] for m in first["materials"]] == ["R"]
    assert first["materials"][0]["status"] == "READY"
    assert first["required_date"] == "2027-02-02"
    assert second["materials"][0]["used_before_qty"] == pytest.approx(220)
    assert second["materials"][0]["available_qty"] == pytest.approx(80)
    assert second["materials"][0]["status"] == "PARTIAL"
    assert second["status"] == "SHORTAGE"
    assert readiness.check_plan(sid, "p2")["materials"][0]["used_before_qty"] == pytest.approx(220)
    assert readiness.check_plan(sid, "p1")["materials"][0]["used_before_qty"] == 0


def test_wo_check_only_takes_its_process_materials(world: World, sid: str) -> None:
    _plan(sid, "p1", wos=[("W-ASM", "Assembly", "2027-02-06T06:00:00")])
    r = readiness.check_work_order(sid, "p1", "W-ASM")
    assert [m["material_id"] for m in r["materials"]] == ["BRK"]
    with pytest.raises(FVError) as e:
        readiness.check_work_order(sid, "p1", "W-NOPE")
    assert e.value.code == "FV-MR-002"


def test_unknown_and_cancelled_plans(world: World, sid: str) -> None:
    for pid in ("nope", "OEM-2030-01", "OEM-bad"):
        with pytest.raises(FVError) as e:
            readiness.check_plan(sid, pid)
        assert e.value.code == "FV-MR-001"
    _plan(sid, "pc", status="Cancelled")
    with pytest.raises(FVError) as e:
        readiness.check_plan(sid, "pc")
    assert e.value.code == "FV-MR-004"


def test_readiness_kpis_exclude_cancelled_and_filter_problems(world: World, sid: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(planning, "monthly_plan", lambda m, *_: {"FB-X": 10} if m == date(2027, 2, 1) else {})
    _plan(sid, "p1", qty=100, color="C02")
    _plan(sid, "pc", status="Cancelled")
    out = readiness.readiness(sid)
    assert [p["plan_id"] for p in out["plans"]] == ["OEM-2027-02", "p1"]
    assert out["kpis"]["plans_monitored"] == 2
    assert out["session_plans"] == 1
    oem = next(p for p in out["plans"] if p["origin"] == "oem")
    assert oem["plan_number"] == "OEM monthly plan 2027-02"
    total = sum(p["total_materials"] for p in out["plans"])
    assert out["kpis"]["materials_short"] == sum(p["short_count"] for p in out["plans"])
    assert out["kpis"]["readiness_pct"] == round((total - out["kpis"]["materials_short"]) / total * 100, 1)
    world.on_hand["B-C02"] = 1000.0
    world.on_hand["B-C01"] = 1000.0
    probs = readiness.readiness(sid, only_problems=True)
    assert probs["plans"] == []


# --- MRP ------------------------------------------------------------------------------------------------------------


def test_horizon_validation_and_default() -> None:
    assert mrp.parse_horizon(None, None) == (date(2027, 2, 1), date(2027, 3, 3))
    with pytest.raises(FVError) as e:
        mrp.parse_horizon("2027-02-10", "2027-02-09")
    assert e.value.code == "FV-MRP-001"
    with pytest.raises(FVError) as e:
        mrp.parse_horizon("x", None)
    assert e.value.code == "FV-MRP-002"
    assert mrp.parse_horizon("2027-02-10", "2027-02-10") == (date(2027, 2, 10), date(2027, 2, 10))


def test_run_numbering_is_per_month() -> None:
    assert mrp.next_run_id([], date(2027, 2, 1)) == "MRP-2027-02-001"
    assert mrp.next_run_id(["MRP-2027-02-001", "MRP-2027-02-002", "MRP-2027-03-001"], date(2027, 2, 15)) == "MRP-2027-02-003"
    assert mrp.next_run_id(["MRP-2027-02-001"], date(2027, 3, 1)) == "MRP-2027-03-001"


def test_order_by_and_recommendation() -> None:
    assert mrp.order_by("2027-02-10", 6) == "2027-02-04"
    assert mrp.recommendation(1234.5, "kg", "2027-02-04") == "Procure 1,234.5 kg before 2027-02-04"
    assert mrp.recommendation(0, "kg", "2027-02-04") is None


def test_net_requirement_sorted_by_net_desc(world: World) -> None:
    ctx = readiness.context("none")
    ctx.incoming = {"R": [{"po_id": "P", "qty": 100, "expected_date": "2027-02-05"}, {"po_id": "Q", "qty": 999, "expected_date": "2027-03-01"}]}
    gross: list[dict[str, Any]] = [
        {"material_id": "R", "material_name": "Resin", "color": None, "unit": "kg", "gross_qty": 1500.0, "required_date": "2027-02-06", "sources": []},
        {"material_id": "BRK", "material_name": "Bracket", "color": None, "unit": "set", "gross_qty": 100.0, "required_date": "2027-02-06", "sources": []},
        {"material_id": "B-C02", "material_name": "Base", "color": "C02", "unit": "kg", "gross_qty": 10.0, "required_date": "2027-02-06", "sources": []},
    ]
    out = mrp.net_results(gross, ctx)
    assert [r["material_id"] for r in out] == ["R", "B-C02", "BRK"]
    assert out[0]["available_qty"] == 1100
    assert out[0]["net_qty"] == 400
    assert out[0]["order_by_date"] == "2027-02-01"
    assert out[0]["recommendation"] == "Procure 400 kg before 2027-02-01"
    assert out[2]["net_qty"] == 0
    assert out[2]["recommendation"] is None


def test_run_mrp_stores_and_lists(world: World, sid: str) -> None:
    _plan(sid, "p1", qty=1000, color="C01", wos=[("W-A", "Molding", "2027-02-02T06:00:00")])
    run = mrp.run_mrp(sid)
    assert run["run_id"] == "MRP-2027-02-001"
    assert run["plan_ids"] == ["p1"]
    r = {x["material_id"]: x for x in run["results"]}
    assert r["R"]["gross_qty"] == pytest.approx(2200)
    assert r["R"]["net_qty"] == pytest.approx(1200)
    assert r["R"]["required_date"] == "2027-02-02"
    assert run["shortage_materials"] == sum(1 for x in run["results"] if x["net_qty"] > 0)
    second = mrp.run_mrp(sid, plan_ids=["p1"])
    assert second["run_id"] == "MRP-2027-02-002"
    assert [x["run_id"] for x in mrp.list_runs(sid)] == ["MRP-2027-02-002", "MRP-2027-02-001"]
    assert mrp.latest_run(sid)["run_id"] == "MRP-2027-02-002"  # type: ignore[index]
    assert mrp.get_run(sid, "MRP-2027-02-001")["results"]
    for bad, code in ((["nope"], "FV-MRP-003"),):
        with pytest.raises(FVError) as e:
            mrp.run_mrp(sid, plan_ids=bad)
        assert e.value.code == code
    with pytest.raises(FVError) as e:
        mrp.get_run(sid, "MRP-1999-01-001")
    assert e.value.code == "FV-MRP-005"


# --- reservations ---------------------------------------------------------------------------------------------------


def test_reservation_is_idempotent_and_release_returns_qty(world: World, sid: str) -> None:
    world.on_hand["R"] = 150.0
    _plan(sid, "p1", qty=100, wos=[("W-A", "Molding", "2027-02-02T06:00:00")])
    got = mrp.reserve_work_order(sid, "p1", "W-A")
    assert [(r["material_id"], r["qty"], r["status"]) for r in got] == [("R", 150.0, "Active")]
    again = mrp.reserve_work_order(sid, "p1", "W-A")
    assert [r["reservation_id"] for r in again] == [r["reservation_id"] for r in got]
    assert world.stock(sid, "R")[0]["reserved_qty"] == 150
    led = store().where(mrp.TRANSACTIONS, "session_id", sid)
    assert [(t["type"], t["qty"], t["balance_after"]) for t in led] == [("Reserve", -150.0, 0.0)]
    out = mrp.release_reservation(sid, got[0]["reservation_id"])
    assert out["status"] == "Released"
    assert out["released_at"]
    assert world.stock(sid, "R")[0]["reserved_qty"] == 0
    assert mrp.release_reservation(sid, got[0]["reservation_id"])["released_at"] == out["released_at"]
    assert sorted(t["type"] for t in store().where(mrp.TRANSACTIONS, "session_id", sid)) == ["Release", "Reserve"]
    assert [r["status"] for r in mrp.list_reservations(sid, "Released")] == ["Released"]
    assert mrp.list_reservations(sid, "Active") == []
    with pytest.raises(FVError) as e:
        mrp.release_reservation(sid, "RSV-99999")
    assert e.value.code == "FV-MR-006"
    with pytest.raises(FVError) as e:
        mrp.list_reservations(sid, "Bogus")
    assert e.value.code == "FV-MR-007"


def test_clear_removes_every_collection(world: World, sid: str) -> None:
    _plan(sid, "p1", qty=10, wos=[("W-A", "Molding", "2027-02-02T06:00:00")])
    readiness.check_plan(sid, "p1")
    mrp.run_mrp(sid)
    mrp.reserve_work_order(sid, "p1", "W-A")
    mrp.clear(sid)
    for coll in (readiness.CHECKS, mrp.RUNS, mrp.RESERVATIONS, mrp.TRANSACTIONS):
        assert store().where(coll, "session_id", sid) == []


# --- consumption variance -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("variance", "status"), [(0.0, "NORMAL"), (0.00009, "NORMAL"), (-0.00009, "NORMAL"), (0.0001, "OVER_CONSUMPTION"), (-0.5, "UNDER_CONSUMPTION")]
)
def test_variance_categories(variance: float, status: str) -> None:
    assert mrp.variance_status(variance) == status


def test_variance_row() -> None:
    r = mrp.variance_row(200.0, 210.0)
    assert r == {"planned_qty": 200.0, "actual_qty": 210.0, "variance_qty": 10.0, "variance_pct": 5.0, "status": "OVER_CONSUMPTION"}
    assert mrp.variance_row(0.0, 1.0)["variance_pct"] == 0.0
