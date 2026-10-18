from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from app import forecasts, plant
from app.errors import FVError
from app.store import store
from app.tools import forecast
from app.tools.common import TODAY


def _daily(start: date, n: int, qty: float, gap_weeks: tuple[int, ...] = ()) -> list[tuple[date, float]]:
    out = []
    for k in range(n):
        d = start + timedelta(days=k)
        week_back = ((TODAY - d).days - 1) // 7
        out.append((d, 0.0 if week_back in gap_weeks or not plant.is_working_day(d) else qty))
    return out


def _fake_series(sku: str, window_days: int, horizon_weeks: int, *, per_customer: bool) -> forecasts.Series:
    gaps = (2, 5) if sku == "RB-CITRA" else ()
    s: forecasts.Series = {
        "history": _daily(TODAY - timedelta(days=window_days), window_days, 100.0, gaps),
        "forecast": _daily(TODAY, horizon_weeks * 7, 120.0),
        "engine": forecast.LOCAL_ENGINE,
    }
    if per_customer:
        s["by_customer"] = {
            "CUS-OEMA": _daily(TODAY - timedelta(days=window_days), window_days, 75.0),
            "CUS-AFTM": _daily(TODAY - timedelta(days=window_days), window_days, 25.0, (1, 3)),
        }
    return s


@pytest.fixture
def sid(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(forecasts, "_series", _fake_series)
    s = str(uuid.uuid4())
    yield s
    forecasts.clear(s)
    for p in store().where(forecasts.PLANS, "session_id", s):
        store().delete(forecasts.PLANS, p["plan_id"])


def test_numbering_is_per_month_and_three_digits() -> None:
    assert forecasts.next_number([], "2027-02-01") == "FC-2027-02-001"
    assert forecasts.next_number(["FC-2027-02-001", "FC-2027-02-009", "FC-2027-01-044"], "2027-02-01") == "FC-2027-02-010"
    assert forecasts.next_number(["FC-2027-01-044"], "2027-02-01") == "FC-2027-02-001"


def test_empty_weeks_count_as_zero_and_flag_insufficient_history() -> None:
    hist = _daily(TODAY - timedelta(days=30), 30, 10.0, (1,))
    buckets = forecasts.weekly_buckets(hist, TODAY)
    assert [b["days"] for b in buckets] == [2, 7, 7, 7, 7]
    assert sum(b["days"] for b in buckets) == 30
    assert buckets[-1]["week_start"] == (TODAY - timedelta(days=7)).isoformat()
    assert buckets[-2]["qty"] == 0
    assert forecasts.history_stats(buckets) == (4, 5, True)
    full = forecasts.weekly_buckets(_daily(TODAY - timedelta(days=90), 90, 10.0), TODAY)
    assert forecasts.history_stats(full) == (13, 13, False)


def test_today_is_excluded_from_history() -> None:
    hist = _daily(TODAY - timedelta(days=60), 61, 10.0)
    buckets = forecasts.weekly_buckets(hist, TODAY)
    assert all(b["week_start"] < TODAY.isoformat() for b in buckets)
    assert sum(b["qty"] for b in buckets) == sum(round(q) for d, q in hist if d < TODAY)


def test_split_by_share_keeps_the_total() -> None:
    assert forecasts.split_by_share(100, [1, 1, 1]) == [34, 33, 33]
    assert sum(forecasts.split_by_share(5169, [42834, 4471, 6424])) == 5169
    assert forecasts.split_by_share(7, [0, 0]) == [7, 0]
    assert forecasts.split_by_share(7, []) == []


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"window_days": 45}, "window_days"),
        ({"window_days": True}, "window_days"),
        ({"window_days": "90"}, "window_days"),
        ({"horizon_weeks": 5}, "horizon_weeks"),
        ({"skus": []}, "skus"),
        ({"skus": "FB-ARKA"}, "skus"),
        ({"per_customer": "yes"}, "per_customer"),
    ],
)
def test_invalid_parameters_are_rejected_per_field(sid: str, kwargs: dict[str, Any], field: str) -> None:
    with pytest.raises(FVError) as e:
        forecasts.generate(sid, **kwargs)
    assert e.value.code == "FV-FC-001"
    assert e.value.http_status == 400
    assert [f["field"] for f in e.value.detail["fields"]] == [field]  # type: ignore[index]


def test_several_bad_fields_are_reported_together(sid: str) -> None:
    with pytest.raises(FVError) as e:
        forecasts.generate(sid, window_days=7, horizon_weeks=12)
    assert {f["field"] for f in e.value.detail["fields"]} == {"window_days", "horizon_weeks"}  # type: ignore[index]


def test_unknown_sku_is_unknown_entity(sid: str) -> None:
    with pytest.raises(FVError) as e:
        forecasts.generate(sid, skus=["FB-ARKA", "XX-NOPE"])
    assert e.value.code == "FV-DATA-001"
    assert e.value.detail["fields"][0]["field"] == "skus"  # type: ignore[index]


def test_snapshot_has_header_lines_and_history(sid: str) -> None:
    snap = forecasts.generate(sid, window_days=60, horizon_weeks=4, user="planner@example.com")
    assert snap["forecast_id"] == "FC-2027-02-001"
    assert (snap["status"], snap["superseded_by"], snap["generated_by"]) == ("Generated", None, "planner@example.com")
    assert (snap["period_start"], snap["period_end"]) == ("2027-02-01", "2027-02-28")
    assert snap["skus"] == plant.SKUS
    assert len(snap["lines"]) == len(plant.SKUS) * 4
    ln = snap["lines"][0]
    assert ln["forecast_line_id"] == f"FC-2027-02-001-{ln['sku']}-2027-02-01"
    assert ln["forecast_qty"] == 120 * 6
    assert all(isinstance(x["forecast_qty"], int) and x["forecast_qty"] >= 0 for x in snap["lines"])
    assert len(snap["history"]["FB-ARKA"]) == 9
    assert snap["insufficient_skus"] == ["RB-CITRA"]
    rb = next(x for x in snap["lines"] if x["sku"] == "RB-CITRA")
    assert (rb["history_weeks_with_orders"], rb["history_weeks"], rb["insufficient_history"]) == (7, 9, True)


def test_sku_filter_limits_lines(sid: str) -> None:
    snap = forecasts.generate(sid, skus=["RB-ARKA", "FB-ARKA", "FB-ARKA"])
    assert snap["skus"] == ["FB-ARKA", "RB-ARKA"]
    assert {x["sku"] for x in snap["lines"]} == {"FB-ARKA", "RB-ARKA"}


def test_per_customer_lines_add_up_to_the_sku_week(sid: str) -> None:
    snap = forecasts.generate(sid, skus=["FB-ARKA"], per_customer=True)
    week1 = [x for x in snap["lines"] if x["week"] == 1]
    assert {x["customer_id"] for x in week1} == {"CUS-OEMA", "CUS-AFTM"}
    assert sum(x["forecast_qty"] for x in week1) == 720
    aftm = next(x for x in week1 if x["customer_id"] == "CUS-AFTM")
    assert aftm["insufficient_history"] is True
    assert aftm["forecast_line_id"] == "FC-2027-02-001-FB-ARKA-CUS-AFTM-2027-02-01"
    assert "CUS-OEMA" in snap["history_by_customer"]["FB-ARKA"]


def test_regenerate_same_params_supersedes_and_keeps_numbers(sid: str) -> None:
    first = forecasts.generate(sid)
    other = forecasts.generate(sid, window_days=30)
    second = forecasts.generate(sid)
    assert second["forecast_id"] == "FC-2027-02-003"
    assert second["supersedes"] == [first["forecast_id"]]
    old = forecasts.get_snapshot(sid, first["forecast_id"])
    assert (old["status"], old["superseded_by"]) == ("Superseded", second["forecast_id"])
    assert old["lines"] == first["lines"]
    assert old["history"] == first["history"]
    assert forecasts.get_snapshot(sid, other["forecast_id"])["status"] == "Generated"


def test_returned_snapshot_cannot_mutate_the_stored_one(sid: str) -> None:
    snap = forecasts.generate(sid)
    snap["lines"][0]["forecast_qty"] = 999_999
    got = forecasts.get_snapshot(sid, snap["forecast_id"])
    got["lines"][1]["forecast_qty"] = -1
    again = forecasts.get_snapshot(sid, snap["forecast_id"])
    assert again["lines"][0]["forecast_qty"] == 720
    assert again["lines"][1]["forecast_qty"] == 720


def test_list_seeds_one_snapshot_newest_first_and_filters(sid: str) -> None:
    seeded = forecasts.list_snapshots(sid)
    assert [(s["forecast_id"], s["generated_by"]) for s in seeded] == [("FC-2027-02-001", "seed")]
    assert "lines" not in seeded[0]
    assert "history" not in seeded[0]
    forecasts.generate(sid)
    assert [s["forecast_id"] for s in forecasts.list_snapshots(sid)] == ["FC-2027-02-002", "FC-2027-02-001"]
    assert [s["forecast_id"] for s in forecasts.list_snapshots(sid, "superseded")] == ["FC-2027-02-001"]
    assert [s["forecast_id"] for s in forecasts.list_snapshots(sid, "Generated")] == ["FC-2027-02-002"]
    with pytest.raises(FVError) as e:
        forecasts.list_snapshots(sid, "Draft")
    assert e.value.code == "FV-FC-003"


def test_latest_is_newest_generated(sid: str) -> None:
    a = forecasts.latest(sid)
    assert a["forecast_id"] == "FC-2027-02-001"
    forecasts.generate(sid, window_days=30)
    assert forecasts.latest(sid)["forecast_id"] == "FC-2027-02-002"


def test_missing_snapshot_and_other_session(sid: str) -> None:
    snap = forecasts.generate(sid)
    with pytest.raises(FVError) as e:
        forecasts.get_snapshot(sid, "FC-2027-02-404")
    assert e.value.code == "FV-FC-002"
    with pytest.raises(FVError):
        forecasts.get_snapshot(str(uuid.uuid4()), snap["forecast_id"])


def test_sessions_number_independently(sid: str) -> None:
    other = str(uuid.uuid4())
    try:
        forecasts.generate(sid)
        assert forecasts.generate(other)["forecast_id"] == "FC-2027-02-001"
        assert forecasts.get_snapshot(sid, "FC-2027-02-001")["session_id"] == sid
    finally:
        forecasts.clear(other)


def test_used_by_plans_reads_production_plans(sid: str) -> None:
    snap = forecasts.generate(sid)
    store().set(forecasts.PLANS, "p1", {"plan_id": "p1", "plan_number": "PP-2027-02", "session_id": sid, "forecast_id": snap["forecast_id"], "status": "Draft"})
    store().set(forecasts.PLANS, "p2", {"plan_id": "p2", "plan_number": "PP-2027-02-002", "session_id": sid, "forecast_id": "FC-OTHER", "status": "Draft"})
    got = forecasts.get_snapshot(sid, snap["forecast_id"])
    assert got["used_by_plans"] == ["PP-2027-02"]
    assert got["used_by_plan_refs"][0]["status"] == "Draft"


def test_qty_for_sums_overlapping_weeks(sid: str) -> None:
    snap = forecasts.generate(sid, horizon_weeks=8)
    qty, ids = forecasts.qty_for(snap, "FB-ARKA", "2027-02-01", "2027-02-28")
    assert qty == 4 * 720
    assert ids == [f"FC-2027-02-001-FB-ARKA-{d}" for d in ("2027-02-01", "2027-02-08", "2027-02-15", "2027-02-22")]
    qty, ids = forecasts.qty_for(snap, "FB-ARKA", "2027-02-07", "2027-02-08")
    assert (qty, len(ids)) == (1440, 2)
    assert forecasts.qty_for(snap, "FB-ARKA", "2027-06-01", "2027-06-30") == (0, [])


def test_clear_removes_snapshots_and_activity(sid: str) -> None:
    forecasts.generate(sid)
    assert forecasts.activity(sid)[0]["event"] == "Forecast generated"
    forecasts.clear(sid)
    assert store().where(forecasts.COLLECTION, "session_id", sid) == []
    assert forecasts.activity(sid) == []


def test_generate_records_activity_and_notifies(sid: str, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(forecasts, "_listeners", [lambda _s, h: seen.append(h["forecast_id"]), lambda _s, _h: 1 / 0])
    forecasts.generate(sid)
    second = forecasts.generate(sid)
    assert seen == ["FC-2027-02-001", "FC-2027-02-002"]
    act = forecasts.activity(sid)
    assert act[0]["forecast_id"] == second["forecast_id"]
    assert act[0]["superseded"] == ["FC-2027-02-001"]


def test_method_text_names_the_engine() -> None:
    assert "last 30 days" in forecasts.method_text(forecast.LOCAL_ENGINE, 30)
    assert "last 42 days of the 90-day window" in forecasts.method_text(forecast.LOCAL_ENGINE, 90)
    assert "ARIMA_PLUS" in forecasts.method_text(forecast.BQML_ENGINE, 60)
