from typing import Any

import pytest
from app import warehouse
from app.tools import delivery, material


def test_atp_rule_served_to_clients_is_the_constant() -> None:
    assert delivery.atp_check().get("rule") == delivery.ATP_RULE_SUMMARY


def test_stock_projection_survives_an_empty_consumption_history(monkeypatch: pytest.MonkeyPatch) -> None:
    real_query = warehouse.query

    def query(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if sql == material.USE_SQL:
            return []
        return real_query(sql, params)

    monkeypatch.setattr(warehouse, "query", query)
    r = material.stock_projection()
    assert r["overconsumption_vs_bom_pct"] is None
    assert r["actual_rate_per_workday"] == r["bom_rate_per_workday"]
    assert len(r["projection"]) == 21


def test_stock_projection_still_reports_overconsumption_when_history_exists() -> None:
    r = material.stock_projection()
    assert 0 < r["overconsumption_vs_bom_pct"] < 12
    assert r["overconsumption_vs_bom_pct"] is not None
