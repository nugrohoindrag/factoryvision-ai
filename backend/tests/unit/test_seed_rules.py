from datetime import date

from app.tools.common import add_working_days, working_days_between, working_days_in_month
from simulator.seed import generator


def test_working_day_helpers() -> None:
    assert working_days_in_month(date(2027, 2, 1)) == 24
    assert working_days_between(date(2027, 2, 1), date(2027, 2, 8)) == 6
    assert add_working_days(date(2027, 2, 6), 1) == date(2027, 2, 8)


def test_lebaran_ramp_shape() -> None:
    assert generator.fb_arka_level(date(2026, 12, 1)) == generator.ARKA_FLAT
    assert generator.fb_arka_level(date(2027, 1, 31)) == generator.ARKA_END


def test_imm04_wear_curve_accelerates() -> None:
    early = generator.imm04_cycle(10) - generator.imm04_cycle(0)
    late = generator.imm04_cycle(89) - generator.imm04_cycle(79)
    assert late > 3 * early
    assert generator.imm04_cycle(89) == generator.IMM04_CYCLE[2]


def test_generator_is_deterministic() -> None:
    a, b = generator.Generator(7), generator.Generator(7)
    a.run()
    b.run()
    assert a.t["fact_production"] == b.t["fact_production"]
    assert a.truth == b.truth


def test_generator_rows_match_the_models() -> None:
    g = generator.Generator()
    g.run()
    from app.db.models import TABLES

    for name, cols in TABLES.items():
        names = {c for c, _ in cols}
        for row in g.t[name][:5]:
            assert set(row) <= names, f"{name}: {set(row) - names}"
