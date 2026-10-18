from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import numpy as np

from app import plant

if TYPE_CHECKING:
    from collections.abc import Sequence

TODAY = plant.DEMO_TODAY
AVAILABLE_FACTOR = 0.88
MINUTES_PER_WORKDAY = sum(int(str(s["planned_minutes"])) for s in plant.SHIFTS)


def to_date(v: object) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def working_days_between(start: date, end: date) -> int:
    return sum(1 for k in range((end - start).days) if plant.is_working_day(start + timedelta(days=k)))


def working_days_in_month(month_start: date) -> int:
    nxt = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return working_days_between(month_start, nxt)


def add_working_days(start: date, n: int) -> date:
    d = start
    while n > 0:
        d += timedelta(days=1)
        if plant.is_working_day(d):
            n -= 1
    return d


def linear_fit(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float]:
    slope, intercept = np.polyfit(np.asarray(xs, float), np.asarray(ys, float), 1)
    return float(slope), float(intercept)


def idr(v: float) -> int:
    return int(round(v, -3))


def pct(v: float, nd: int = 1) -> float:
    return round(100 * v, nd)
