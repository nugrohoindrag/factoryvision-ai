from app import warehouse
from app.tools import delivery, material


def test_atp_rule_served_to_clients_is_the_constant():
    assert delivery.ATP_RULE_SUMMARY == delivery.atp_check().get("rule")


def test_stock_projection_survives_an_empty_consumption_history(monkeypatch):
    real_query = warehouse.query

    def query(sql, params=None):
        if sql == material.USE_SQL:
            return []
        return real_query(sql, params)

    monkeypatch.setattr(material.warehouse, "query", query)
    r = material.stock_projection()
    assert r["overconsumption_vs_bom_pct"] is None
    assert r["actual_rate_per_workday"] == r["bom_rate_per_workday"]
    assert len(r["projection"]) == 21


def test_stock_projection_still_reports_overconsumption_when_history_exists():
    r = material.stock_projection()
    assert 0 < r["overconsumption_vs_bom_pct"] < 12
    assert r["overconsumption_vs_bom_pct"] is not None