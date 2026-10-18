from datetime import datetime, timedelta
from typing import Any

from app import plant
from app import work_orders as wo

T0 = datetime(2027, 2, 1, 7, 0, tzinfo=plant.WIB)


def _w(status: str = "In Production", start: int = 0, hours: int = 10, qty: int = 1000, output: int = 0, **extra: Any) -> dict[str, Any]:
    return wo._norm(
        {
            "wo_id": "W1",
            "status": status,
            "machine_id": "IMM-04",
            "planned_start": (T0 + timedelta(hours=start)).isoformat(),
            "planned_end": (T0 + timedelta(hours=start + hours)).isoformat(),
            "qty": qty,
            "output_qty": output,
            "predecessor_wo_id": None,
            **extra,
        }
    )


def _at(hours: float, **kw: Any) -> wo.Floor:
    return wo.Floor(now=T0 + timedelta(hours=hours), **kw)


def test_running_at_risk_and_delayed_follow_the_clock() -> None:
    assert wo.board_status(_w(output=500), _at(5)) == "Running"  # half the time, half the pieces
    assert wo.board_status(_w(output=350), _at(5)) == "At Risk"  # 15 points behind the clock
    assert wo.board_status(_w(output=450), _at(5)) == "Running"  # 5 points behind is still on track
    assert wo.board_status(_w(output=900), _at(11)) == "Delayed"
    assert wo.board_status(_w("Released", start=2), _at(13)) == "Delayed"
    assert wo.board_status(_w("Released", start=2), _at(1)) == "Released"
    assert wo.board_status(_w("Draft"), _at(-1)) == "Scheduled"
    assert wo.board_status(_w("Completed"), _at(30)) == "Completed"


def test_approved_maintenance_blocks_the_first_two_hours_of_today() -> None:
    f = _at(1, maintenance=["IMM-04"])
    assert f.maintenance_window("IMM-04") == (T0, T0 + timedelta(minutes=120))
    assert f.maintenance_window("IMM-05") is None
    assert f.under_maintenance("IMM-04")
    assert not _at(3, maintenance=["IMM-04"]).under_maintenance("IMM-04")
    assert _at(3, machine_status={"IMM-04": "Maintenance"}).under_maintenance("IMM-04")
    assert wo.board_status(_w(), f) == "Maintenance"
    assert _at(3, machine_status={"IMM-04": "Downtime"}).down("IMM-04")


def test_overlapping_bars_get_their_own_row() -> None:
    items: list[dict[str, Any]] = [
        {"planned_start": (T0 + timedelta(hours=h)).isoformat(), "planned_end": (T0 + timedelta(hours=h + d)).isoformat()}
        for h, d in ((0, 4), (2, 4), (4, 2), (6, 1))
    ]
    assert wo._stack(items) == 2
    assert [i["row"] for i in items] == [0, 1, 0, 0]  # a row is reused as soon as its bar has ended


def test_split_part_suffixes_run_past_z() -> None:
    assert [wo._suffix(k) for k in (0, 1, 25, 26, 27)] == ["A", "B", "Z", "AA", "AB"]


def test_parent_rolls_up_its_parts() -> None:
    by_id = {
        "P": wo._norm({"wo_id": "P", "status": "Released", "children": ["P-A", "P-B"]}),
        "P-A": wo._norm({"wo_id": "P-A", "status": "Completed", "output_qty": 60}),
        "P-B": wo._norm({"wo_id": "P-B", "status": "Released", "output_qty": 0}),
    }
    wo._roll_up(by_id, by_id["P"])
    assert (by_id["P"]["status"], by_id["P"]["output_qty"]) == ("In Production", 60)
    by_id["P-B"]["status"] = "Cancelled"
    wo._roll_up(by_id, by_id["P"])
    assert by_id["P"]["status"] == "Completed"


def test_available_quantity_counts_the_parts_of_a_split_predecessor() -> None:
    by_id = {
        "M": wo._norm({"wo_id": "M", "children": ["M-A", "M-B"], "predecessor_wo_id": None, "parent_wo_id": None}),
        "M-A": wo._norm({"wo_id": "M-A", "output_qty": 30, "predecessor_wo_id": None, "parent_wo_id": "M"}),
        "M-B": wo._norm({"wo_id": "M-B", "output_qty": 20, "predecessor_wo_id": None, "parent_wo_id": "M"}),
        "P": wo._norm({"wo_id": "P", "output_qty": 15, "reject_qty": 5, "predecessor_wo_id": "M", "parent_wo_id": None}),
    }
    assert wo.available_qty(by_id, by_id["P"]) == 30
    assert wo.available_qty(by_id, by_id["M-A"]) is None
