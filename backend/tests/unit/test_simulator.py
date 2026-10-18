from typing import Any

import pytest
from app import plant
from app.errors import FVError
from app.simulation.engine import RESIN_START_KG, State, andon
from simulator.seed.generator import Generator


def run(scenario: str = "baseline", minutes: int = 120, commands: dict[int, dict[str, Any]] | None = None) -> State:
    s = State.new("s1", scenario)
    cmds = commands or {}
    for _ in range(minutes):
        if s.minute in cmds:
            s.apply(cmds[s.minute])
        s.step()
    return s


def machine(s: State, mid: str) -> dict[str, Any]:
    return next(m for m in s.snapshot()["machines"] if m["machine_id"] == mid)


def test_same_seed_same_state() -> None:
    assert run("disruption_response").full() == run("disruption_response").full()


def test_no_tick_no_change() -> None:
    s = State.new("s1")
    before = s.full()
    assert s.snapshot()["sim_minute"] == 0
    assert s.full() == before


def test_initial_state_matches_seed() -> None:
    g = Generator()
    g.run()
    assert g.truth["resin_on_hand_kg"] == RESIN_START_KG
    s = State.new("s1")
    assert s.wip["FB-ARKA"] == 6
    assert {mid: next(iter(m.mix)) for mid, m in s.machines.items() if m.mix} == plant.INITIAL_MOLDS


@pytest.mark.parametrize(
    ("status", "perf", "color"),
    [
        ("Running", 95, "green"),
        ("Running", 80, "yellow"),
        ("Setup", None, "yellow"),
        ("Downtime", None, "red"),
        ("Maintenance", None, "blue"),
        ("Idle", None, "grey"),
        ("Offline", None, "grey"),
    ],
)
def test_andon_colours(status: str, perf: float | None, color: str) -> None:
    assert andon(status, perf) == color


def test_disruption_slows_imm04_and_empties_fb_arka_rack() -> None:
    s = run("disruption_response", 60)
    m4 = machine(s, "IMM-04")
    assert m4["andon"] == "yellow"
    assert m4["performance_pct"] < 85
    assert s.wip["FB-ARKA"] < 10
    base = run("baseline", 60)
    assert machine(base, "IMM-04")["performance_pct"] > machine(s, "IMM-04")["performance_pct"]


def test_maintenance_blue_then_green_at_normal_cycle() -> None:
    s = run("disruption_response", 30, commands={10: {"name": "start_maintenance", "args": {"machine_id": "IMM-04"}}})
    assert machine(s, "IMM-04")["andon"] == "blue"
    for _ in range(110):
        s.step()
    m4 = machine(s, "IMM-04")
    assert m4["status"] == "Running"
    assert m4["andon"] == "green"
    assert m4["cycle_s"] < 72


def test_mold_move_is_a_swap_with_setup() -> None:
    s = run("baseline", 5, commands={1: {"name": "move_mold", "args": {"sku": "FB-ARKA", "to_machine_id": "IMM-05"}}})
    assert machine(s, "IMM-05")["status"] == "Setup"
    assert machine(s, "IMM-05")["andon"] == "yellow"
    for _ in range(plant.CHANGEOVER_MINUTES):
        s.step()
    assert s.machines["IMM-05"].mix == {"FB-ARKA": 1.0}
    assert s.machines["IMM-04"].mix == {"RB-ARKA": 1.0}


def test_mold_must_fit() -> None:
    s = State.new("s1")
    with pytest.raises(FVError) as e:
        s.apply({"name": "move_mold", "args": {"sku": "FB-ARKA", "to_machine_id": "IMM-01"}})
    assert e.value.code == "FV-DATA-001"


def test_hold_turns_racks_orange_and_stops_shipping() -> None:
    s = run("baseline", 30, commands={1: {"name": "hold_lot", "args": {"resin_lot_id": "LOT-PPT20-0918"}}})
    held = [r for racks in s.snapshot()["warehouse"].values() for r in racks if r["on_hold"]]
    assert held
    assert all(r["lot"] == "LOT-PPT20-0918" for r in held)
    start = State.new("s1").warehouse["FB-ARKA"][0][1]
    assert sum(r["units"] for r in s.snapshot()["warehouse"]["FB-ARKA"] if r["on_hold"]) >= start


def test_po_arrives_as_new_lot() -> None:
    po = {"name": "place_po", "args": {"qty": 160000, "lead_minutes": 20}}
    s = run("baseline", 40, commands={1: po})
    assert s.resin_kg > RESIN_START_KG
    assert s.active_lot == "LOT-PPT20-0918"  # the old lot still has stock and is not held, so it keeps running
    held = run("baseline", 40, commands={1: po, 2: {"name": "hold_lot", "args": {"resin_lot_id": "LOT-PPT20-0918"}}})
    assert held.active_lot == "LOT-PPT20-0919"


def test_unknown_command() -> None:
    with pytest.raises(FVError) as e:
        State.new("s1").apply({"name": "launch_rocket"})
    assert e.value.code == "FV-TWIN-002"


def test_conveyor_is_deterministic_and_flags_wrong_colour() -> None:
    photos = [{"photo_id": f"P{k:03d}", "sku": sku, "color": plant.PRODUCT_BY_SKU[sku].colors[0]} for k, sku in enumerate(plant.SKUS)]
    a, b = State.new("s1").snapshot(photos)["conveyor"], State.new("s2").snapshot(photos)["conveyor"]
    assert a == b
    seen = [i for k in range(0, 40, 2) for i in run("baseline", k).snapshot(photos)["conveyor"]]
    assert any(i["color"] != i["expected_color"] for i in seen)
    assert run("baseline", 30).snapshot(photos)["conveyor"][0]["seq"] == 15
