from datetime import date, datetime

from app.config import settings
from app.db.models import TABLES
from simulator import plant as P


def test_sixteen_machines_and_six_imm():
    assert len(P.MACHINES) == 16
    assert P.IMM_IDS == ["IMM-01", "IMM-02", "IMM-03", "IMM-04", "IMM-05", "IMM-06"]


def test_arka_molds_fit_only_3500t():
    for sku in ("FB-ARKA", "RB-ARKA"):
        assert {P.MACHINE_BY_ID[m].tonnage for m in P.MOLD_COMPAT[sku]} == {3500}


def test_initial_molds_are_compatible_and_unique():
    assert sorted(P.INITIAL_MOLDS.values()) == sorted(P.SKUS)
    for mid, sku in P.INITIAL_MOLDS.items():
        assert mid in P.MOLD_COMPAT[sku]


def test_shift_boundaries():
    assert P.shift_of(datetime(2027, 2, 1, 7, 0, tzinfo=P.WIB)) == "A"
    assert P.shift_of(datetime(2027, 2, 1, 15, 0, tzinfo=P.WIB)) == "B"
    assert P.shift_of(datetime(2027, 2, 1, 23, 30, tzinfo=P.WIB)) == "C"
    assert P.shift_of(datetime(2027, 2, 2, 6, 59, tzinfo=P.WIB)) == "C"


def test_demo_day_is_a_working_monday():
    assert P.DEMO_TODAY.weekday() == 0
    assert not P.is_working_day(date(2027, 1, 31))


def test_defect_and_reason_taxonomy():
    assert len(P.DEFECT_CLASSES) == 25
    assert {d[5] for d in P.DEFECT_CLASSES} <= set(P.DISPOSITIONS)
    assert {r[3] for r in P.DOWNTIME_REASONS} <= {"Machine", "Material", "Process", "Quality", "People", "Planning"}
    for baseline in ("MACHINE_BREAKDOWN", "PLANNED_MAINTENANCE", "NO_MATERIAL", "WAITING_OPERATOR", "CHANGEOVER"):
        assert baseline in P.REASON_BY_CODE


def test_bom_covers_every_sku_with_resin():
    resin = {b["sku"] for b in P.bom_lines() if b["material_id"] == "PP-EPDM-T20"}
    assert resin == set(P.SKUS)


def test_models_cover_every_table():
    assert len(TABLES) == 25
    assert ("output_quantity", "INT64") in TABLES["fact_production"]


def test_no_banned_models():
    for m in (settings.model_orchestrator, settings.model_module, settings.model_vision, settings.model_live):
        assert not m.startswith("gemini-2.5")
