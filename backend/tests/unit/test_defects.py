import ast
import inspect
import json
import logging

from sqlalchemy.exc import DatabaseError

from app.db.models import Base
from app.main import app
from app.tools import delivery, forecast, material


def test_every_table_declares_its_grain_as_a_primary_key():
    assert [t.name for t in Base.metadata.sorted_tables if not t.primary_key.columns] == []


def test_atp_rule_is_not_parsed_from_a_docstring():
    assert delivery.ATP_RULE_SUMMARY
    assert "\n" not in delivery.ATP_RULE_SUMMARY


def test_atp_formula_keeps_its_defining_terms():
    assert "stock on hand" in delivery.ATP_RULE_FORMULA
    assert "working days" in delivery.ATP_RULE_FORMULA


def test_unknown_route_reports_resource_not_found_not_unknown_module():
    import asyncio

    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.responses import JSONResponse

    handler = app.exception_handlers[StarletteHTTPException]
    resp = asyncio.run(handler(None, StarletteHTTPException(404, "nope")))
    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 404
    assert json.loads(resp.body)["error"]["code"] == "FV-REQ-004"
    assert json.loads(resp.body)["error"]["title"] == "Resource not found"


def test_material_ratio_is_none_when_base_rate_is_zero():
    assert material.ratio(10.0, 0.0) is None


def test_material_ratio_is_none_when_window_is_empty():
    assert material.ratio(10.0, []) is None


def _sql_calls(tree):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("query", "raw")]


def _names_bound_to_an_f_string(tree):
    bound = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None and any(isinstance(x, ast.JoinedStr) for x in ast.walk(n.value)):
            bound.update(t.id for t in ast.walk(n) if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store))
    return bound


def test_forecast_never_interpolates_sql_from_a_string():
    tree = ast.parse(inspect.getsource(forecast))
    calls = _sql_calls(tree)
    assert calls, "the module no longer queries the warehouse"
    assert [a for c in calls for a in c.args if isinstance(a, ast.JoinedStr)] == []
    assert [k for c in calls for k in c.keywords if isinstance(k.value, ast.JoinedStr)] == []
    sql_args = {a.id for c in calls for a in list(c.args) + [k.value for k in c.keywords] if isinstance(a, ast.Name)}
    assert sql_args & _names_bound_to_an_f_string(tree) == set()
    assert "{" not in forecast.BQML_SQL and "{" not in forecast.HISTORY_SQL
    assert "WHERE sku = :sku" in forecast.BQML_SQL


def test_bqml_falls_back_on_any_sqlalchemy_error_and_says_why(monkeypatch, caplog):
    def boom(sql, params=None):
        raise DatabaseError("stmt", {}, Exception("model not trained"))

    monkeypatch.setattr(forecast.warehouse, "raw", boom)
    with caplog.at_level(logging.WARNING, logger="fv"):
        assert forecast._bqml_forecast("FB-ARKA", 42) is None
    assert "BQML forecast unavailable" in caplog.text
    assert "DatabaseError" in caplog.text
    assert "ML.FORECAST" in caplog.text