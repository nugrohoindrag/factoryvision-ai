import pytest
from app.tools import planning


@pytest.mark.unit
@pytest.mark.parametrize(
    ("required", "available", "utilization", "expected"),
    [
        (79, 100, 80, "Within Plan"),
        (80, 100, 80, "Within Plan"),
        (81, 100, 80, "Additional Demand"),
        (100, 100, 80, "Additional Demand"),
        (101, 100, 80, "Capacity Up Required"),
        (96, 100, 95, "Additional Demand"),
        (95, 100, 95, "Within Plan"),
    ],
)
def test_group_status_follows_the_session_utilization(required: float, available: float, utilization: float, expected: str) -> None:
    assert planning.group_status(required, available, utilization) == expected
