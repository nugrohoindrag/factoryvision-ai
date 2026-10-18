from datetime import date, datetime

import pytest
from app import plant
from app.config import Settings
from app.db.models import TABLES
from pydantic import ValidationError


def test_sixteen_machines_and_six_imm() -> None:
    assert len(plant.MACHINES) == 16
    assert plant.IMM_IDS == ["IMM-01", "IMM-02", "IMM-03", "IMM-04", "IMM-05", "IMM-06"]


def test_arka_molds_fit_only_3500t() -> None:
    for sku in ("FB-ARKA", "RB-ARKA"):
        assert {plant.MACHINE_BY_ID[m].tonnage for m in plant.MOLD_COMPAT[sku]} == {3500}


def test_initial_molds_are_compatible_and_unique() -> None:
    assert sorted(plant.INITIAL_MOLDS.values()) == sorted(plant.SKUS)
    for mid, sku in plant.INITIAL_MOLDS.items():
        assert mid in plant.MOLD_COMPAT[sku]


def test_shift_boundaries() -> None:
    assert plant.shift_of(datetime(2027, 2, 1, 7, 0, tzinfo=plant.WIB)) == "A"
    assert plant.shift_of(datetime(2027, 2, 1, 15, 0, tzinfo=plant.WIB)) == "B"
    assert plant.shift_of(datetime(2027, 2, 1, 23, 30, tzinfo=plant.WIB)) == "C"
    assert plant.shift_of(datetime(2027, 2, 2, 6, 59, tzinfo=plant.WIB)) == "C"


def test_demo_day_is_a_working_monday() -> None:
    assert plant.DEMO_TODAY.weekday() == 0
    assert not plant.is_working_day(date(2027, 1, 31))


def test_defect_and_reason_taxonomy() -> None:
    assert len(plant.DEFECT_CLASSES) == 25
    assert {d[5] for d in plant.DEFECT_CLASSES} <= set(plant.DISPOSITIONS)
    assert {r[3] for r in plant.DOWNTIME_REASONS} <= {"Machine", "Material", "Process", "Quality", "People", "Planning"}
    for baseline in ("MACHINE_BREAKDOWN", "PLANNED_MAINTENANCE", "NO_MATERIAL", "WAITING_OPERATOR", "CHANGEOVER"):
        assert baseline in plant.REASON_BY_CODE


def test_bom_covers_every_sku_with_resin() -> None:
    resin = {b["sku"] for b in plant.bom_lines() if b["material_id"] == "PP-EPDM-T20"}
    assert resin == set(plant.SKUS)


def test_models_cover_every_table() -> None:
    assert len(TABLES) == 28
    assert ("output_quantity", "INT64") in TABLES["fact_production"]
    assert "dim_warehouse" in TABLES
    assert "bom_header" in TABLES
    assert "bom_item" in TABLES


def test_shutdown_model_is_rejected_at_construction() -> None:
    with pytest.raises(ValidationError, match=r"gemini-2\.5"):
        Settings(model_orchestrator="gemini-2.5-flash")
    with pytest.raises(ValidationError, match=r"gemini-2\.5"):
        Settings(model_module="gemini-2.5-flash")
    with pytest.raises(ValidationError, match=r"gemini-2\.5"):
        Settings(model_vision="gemini-2.5-flash")
    with pytest.raises(ValidationError, match=r"gemini-2\.5"):
        Settings(model_live="gemini-2.5-flash")
