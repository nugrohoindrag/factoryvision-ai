from __future__ import annotations

from datetime import timedelta

from app import warehouse
from app.errors import FVError
from app.tools.common import TODAY, idr, linear_fit, pct, to_date
from simulator import plant as P

OEE_SQL = """
SELECT machine_id, shift_id,
  SUM(planned_minutes) AS planned, SUM(run_minutes) AS run_min,
  SUM(ideal_cycle_s * total_quantity) AS ideal_s, SUM(total_quantity) AS total,
  SUM(output_quantity) AS good, SUM(scrap_quantity) AS scrap, SUM(reject_quantity) AS reject
FROM fv.fact_production
WHERE production_date BETWEEN :d0 AND :d1
GROUP BY machine_id, shift_id
"""


def last_production_date():
    r = warehouse.query("SELECT MAX(production_date) AS d FROM fv.fact_production")
    return to_date(r[0]["d"])


def _oee(rows: list[dict]) -> dict:
    planned = sum(r["planned"] for r in rows)
    run = sum(r["run_min"] for r in rows)
    ideal = sum(r["ideal_s"] for r in rows)
    total = sum(r["total"] for r in rows)
    good = sum(r["good"] for r in rows)
    a = run / planned if planned else 0
    p = ideal / (run * 60) if run else 0
    q = good / total if total else 0
    return {"availability_pct": pct(a), "performance_pct": pct(p), "quality_pct": pct(q), "oee_pct": pct(a * p * q),
            "planned_minutes": round(planned), "run_minutes": round(run), "total_quantity": int(total), "output_quantity": int(good)}


def get_oee(day: str | None = None, days: int = 1, machine_id: str | None = None) -> dict:
    d1 = to_date(day) if day else last_production_date()
    d0 = d1 - timedelta(days=days - 1)
    rows = warehouse.query(OEE_SQL, {"d0": d0, "d1": d1})
    if machine_id:
        rows = [r for r in rows if r["machine_id"] == machine_id]
    by_machine = {}
    for mid in sorted({r["machine_id"] for r in rows}):
        by_machine[mid] = _oee([r for r in rows if r["machine_id"] == mid])
    by_shift = {s: _oee([r for r in rows if r["shift_id"] == s]) for s in ("A", "B", "C") if any(r["shift_id"] == s for r in rows)}
    imm = [r for r in rows if r["machine_id"].startswith("IMM")]
    return {"window": [d0.isoformat(), d1.isoformat()], "plant_molding": _oee(imm) if imm else None, "plant_all": _oee(rows),
            "by_machine": by_machine, "by_shift": by_shift, "worst_machine": min(by_machine, key=lambda m: by_machine[m]["oee_pct"]) if by_machine else None,
            "source": "fv.fact_production", "formula": "A = run/planned; P = ideal_cycle x total / run; Q = output/total"}


PARETO_SQL = """
SELECT d.reason_code, r.reason_name, r.reason_category, r.loss_category, m.area,
  COUNT(*) AS events, SUM(d.duration_minutes) AS minutes
FROM fv.fact_downtime d
JOIN fv.dim_downtime_reason r ON r.reason_code = d.reason_code
JOIN fv.dim_machine m ON m.machine_id = d.machine_id
WHERE d.production_date BETWEEN :d0 AND :d1
GROUP BY d.reason_code, r.reason_name, r.reason_category, r.loss_category, m.area
"""
LINE_STOP_IDR_PER_HOUR = {"molding": P.DOWNTIME_IDR_PER_HOUR_IMM, "painting": 9_200_000, "prep": 7_500_000, "assembly": 3_200_000, "inspection": 2_500_000}


def downtime_pareto(days: int = 30, top: int = 5, machine_id: str | None = None) -> dict:
    d1 = last_production_date()
    d0 = d1 - timedelta(days=days - 1)
    if machine_id:
        rows = warehouse.query(PARETO_SQL.replace("GROUP BY", "AND d.machine_id = :mid GROUP BY"), {"d0": d0, "d1": d1, "mid": machine_id})
    else:
        rows = warehouse.query(PARETO_SQL, {"d0": d0, "d1": d1})
    agg: dict[str, dict] = {}
    for r in rows:
        a = agg.setdefault(r["reason_code"], {"reason_code": r["reason_code"], "reason_name": r["reason_name"], "reason_category": r["reason_category"],
                                              "loss_category": r["loss_category"], "events": 0, "hours": 0.0, "loss_idr": 0})
        a["events"] += r["events"]
        a["hours"] += r["minutes"] / 60
        a["loss_idr"] += r["minutes"] / 60 * LINE_STOP_IDR_PER_HOUR[r["area"]]
    items = sorted(agg.values(), key=lambda x: -x["loss_idr"])
    total = sum(x["loss_idr"] for x in items) or 1
    cum = 0
    for x in items:
        cum += x["loss_idr"]
        x["hours"] = round(x["hours"], 1)
        x["loss_idr"] = idr(x["loss_idr"])
        x["cumulative_pct"] = pct(cum / total)
    return {"window": [d0.isoformat(), d1.isoformat()], "top": items[:top], "total_hours": round(sum(x["hours"] for x in items), 1),
            "total_loss_idr": idr(total), "rates_idr_per_hour": LINE_STOP_IDR_PER_HOUR, "source": "fv.fact_downtime x fv.dim_downtime_reason"}


HEALTH_SQL = """
SELECT production_date AS d, SUM(actual_cycle_s * total_quantity) / SUM(total_quantity) AS cycle_s,
  SUM(run_minutes) AS run_min, SUM(scrap_quantity) AS scrap, SUM(total_quantity) AS total, MAX(ideal_cycle_s) AS ideal
FROM fv.fact_production WHERE machine_id = :mid GROUP BY d ORDER BY d
"""
EVENTS_SQL = "SELECT production_date AS d, reason_code, duration_minutes FROM fv.fact_downtime WHERE machine_id = :mid ORDER BY d"


def machine_health(machine_id: str = "IMM-04", fit_days: int = 21) -> dict:
    if machine_id not in P.MACHINE_BY_ID:
        return FVError("FV-DATA-001", f"unknown machine {machine_id}", {"valid": list(P.MACHINE_BY_ID)}).tool_result()
    daily = warehouse.query(HEALTH_SQL, {"mid": machine_id})
    events = warehouse.query(EVENTS_SQL, {"mid": machine_id})
    alarm_row = warehouse.query("SELECT cycle_alarm_s FROM fv.dim_machine WHERE machine_id = :mid", {"mid": machine_id})
    alarm = alarm_row[0]["cycle_alarm_s"] if alarm_row else None
    if not daily:
        return {"machine_id": machine_id, "note": "no production history"}
    days = [to_date(r["d"]) for r in daily]
    recent = [r for r in daily if to_date(r["d"]) > days[-1] - timedelta(days=fit_days)]
    xs = [(to_date(r["d"]) - days[-1]).days for r in recent]
    slope, intercept = linear_fit(xs, [r["cycle_s"] for r in recent])
    days_to_alarm = None
    if alarm and slope > 0.01:
        days_to_alarm = round(max(0.0, (alarm - intercept) / slope), 1)
    weekly = []
    end = days[-1]
    for w in range(12, -1, -1):
        w1 = end - timedelta(days=7 * w)
        w0 = w1 - timedelta(days=6)
        run_h = sum(r["run_min"] for r in daily if w0 <= to_date(r["d"]) <= w1) / 60
        n = sum(1 for e in events if w0 <= to_date(e["d"]) <= w1)
        weekly.append({"week_end": w1.isoformat(), "run_hours": round(run_h, 1), "downtime_events": n, "mtbf_hours": round(run_h / n, 1) if n else None})
    m_first = [x["mtbf_hours"] for x in weekly[:6] if x["mtbf_hours"]]
    m_last = [x["mtbf_hours"] for x in weekly[-2:] if x["mtbf_hours"]]
    ideal = daily[-1]["ideal"]
    current = intercept
    risk = "low"
    if days_to_alarm is not None and days_to_alarm <= 10:
        risk = "high" if days_to_alarm <= 7 else "medium"
    reasons = {}
    for e in events:
        if to_date(e["d"]) > end - timedelta(days=28):
            reasons[e["reason_code"]] = reasons.get(e["reason_code"], 0) + 1
    return {
        "machine_id": machine_id, "as_of": end.isoformat(), "ideal_cycle_s": ideal, "current_cycle_s": round(current, 2),
        "cycle_trend_s_per_day": round(slope, 3), "cycle_alarm_s": alarm,
        "days_to_alarm": days_to_alarm,
        "predicted_breakdown_date": (TODAY + timedelta(days=round(days_to_alarm))).isoformat() if days_to_alarm is not None else None,
        "breakdown_risk": risk,
        "performance_now_pct": pct(ideal / current) if current else None,
        "mtbf_hours_first_6_weeks": round(sum(m_first) / len(m_first), 1) if m_first else None,
        "mtbf_hours_last_2_weeks": round(sum(m_last) / len(m_last), 1) if m_last else None,
        "weekly_mtbf": weekly,
        "recent_downtime_reasons_28d": reasons,
        "scrap_rate_last7_pct": pct(sum(r["scrap"] for r in daily[-7:]) / max(1, sum(r["total"] for r in daily[-7:])), 2),
        "method": f"linear fit of daily cycle time over the last {fit_days} days, extrapolated to the cycle alarm (ideal x 1.20); MTBF = run time / downtime events",
        "source": "fv.fact_production, fv.fact_downtime, fv.dim_machine",
    }


def fleet_health() -> list[dict]:
    out = []
    for mid in P.IMM_IDS:
        h = machine_health(mid)
        out.append({k: h.get(k) for k in ("machine_id", "current_cycle_s", "ideal_cycle_s", "cycle_trend_s_per_day", "days_to_alarm", "breakdown_risk", "mtbf_hours_last_2_weeks")})
    return out
