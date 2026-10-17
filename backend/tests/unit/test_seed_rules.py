from datetime import date

from app.tools.common import add_working_days, working_days_between, working_days_in_month
from simulator.seed import generator as G


def test_working_day_helpers():
    assert working_days_in_month(date(2027, 2, 1)) == 24
    assert working_days_between(date(2027, 2, 1), date(2027, 2, 8)) == 6
    assert add_working_days(date(2027, 2, 6), 1) == date(2027, 2, 8)  # skips Sunday


def test_lebaran_ramp_shape():
    assert G.fb_arka_level(date(2026, 12, 1)) == G.ARKA_FLAT
    assert G.fb_arka_level(date(2027, 1, 31)) == G.ARKA_END


def test_imm04_wear_curve_accelerates():
    early = G.imm04_cycle(10) - G.imm04_cycle(0)
    late = G.imm04_cycle(89) - G.imm04_cycle(79)
    assert late > 3 * early
    assert G.imm04_cycle(89) == G.IMM04_CYCLE[2]


def test_generator_is_deterministic():
    a, b = G.Generator(7), G.Generator(7)
    a.run()
    b.run()
    assert a.t["fact_production"] == b.t["fact_production"]
    assert a.truth == b.truth


def test_generator_rows_match_the_models():
    g = G.Generator()
    g.run()
    from app.db.models import TABLES

    for name, cols in TABLES.items():
        names = {c for c, _ in cols}
        for row in g.t[name][:5]:
            assert set(row) <= names, f"{name}: {set(row) - names}"
