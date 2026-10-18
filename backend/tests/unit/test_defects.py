from __future__ import annotations

import ast
import inspect
import json
import logging
from typing import TYPE_CHECKING, Any

from app import warehouse
from app.db.models import Base
from app.main import app, http_error
from app.tools import delivery, forecast, material
from sqlalchemy.exc import DatabaseError
from starlette.requests import Request

if TYPE_CHECKING:
    import pytest
    from starlette.responses import JSONResponse


def test_every_table_declares_its_grain_as_a_primary_key() -> None:
    assert [t.name for t in Base.metadata.sorted_tables if not t.primary_key.columns] == []


def test_atp_rule_is_not_parsed_from_a_docstring() -> None:
    assert delivery.ATP_RULE_SUMMARY
    assert "\n" not in delivery.ATP_RULE_SUMMARY


def test_atp_formula_keeps_its_defining_terms() -> None:
    assert "stock on hand" in delivery.ATP_RULE_FORMULA
    assert "working days" in delivery.ATP_RULE_FORMULA


def test_unknown_route_reports_resource_not_found_not_unknown_module() -> None:
    import asyncio

    from starlette.exceptions import HTTPException as StarletteHTTPException

    assert app.exception_handlers[StarletteHTTPException] is http_error
    resp: JSONResponse = asyncio.run(http_error(Request({"type": "http"}), StarletteHTTPException(404, "nope")))
    assert resp.status_code == 404
    error = json.loads(bytes(resp.body))["error"]
    assert error["code"] == "FV-REQ-004"
    assert error["title"] == "Resource not found"


def test_material_ratio_is_none_when_base_rate_is_zero() -> None:
    assert material.ratio(10.0, 0.0) is None


def test_material_ratio_is_none_when_window_is_empty() -> None:
    assert material.ratio(10.0, []) is None


def _sql_calls(tree: ast.Module) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("query", "raw", "bqml_forecast")]


def _names_bound_to_an_f_string(tree: ast.Module) -> set[str]:
    bound: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None and any(isinstance(x, ast.JoinedStr) for x in ast.walk(n.value)):
            bound.update(t.id for t in ast.walk(n) if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store))
    return bound


def test_forecast_never_interpolates_sql_from_a_string() -> None:
    tree = ast.parse(inspect.getsource(forecast))
    calls = _sql_calls(tree)
    assert calls, "the module no longer queries the warehouse"
    assert [a for c in calls for a in c.args if isinstance(a, ast.JoinedStr)] == []
    assert [k for c in calls for k in c.keywords if isinstance(k.value, ast.JoinedStr)] == []
    sql_args = {a.id for c in calls for a in list(c.args) + [k.value for k in c.keywords] if isinstance(a, ast.Name)}
    assert sql_args & _names_bound_to_an_f_string(tree) == set()
    assert "{" not in warehouse.BQML_FORECAST_SQL
    assert "{" not in forecast.HISTORY_SQL
    assert "WHERE sku = :sku" in warehouse.BQML_FORECAST_SQL


def test_bqml_falls_back_on_any_sqlalchemy_error_and_says_why(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def boom(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        raise DatabaseError("stmt", {}, Exception("model not trained"))

    monkeypatch.setattr(warehouse, "bqml_forecast", boom)
    with caplog.at_level(logging.WARNING, logger="fv"):
        assert forecast._bqml_forecast("FB-ARKA", 42) is None
    assert "BQML forecast unavailable" in caplog.text
    assert "DatabaseError" in caplog.text
    assert "ML.FORECAST" in caplog.text
