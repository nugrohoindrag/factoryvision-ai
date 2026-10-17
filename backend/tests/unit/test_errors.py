import re

import pytest

from app.errors import CATALOG, DOC_PATH, FVError, render_markdown


def test_codes_follow_the_pattern():
    for code, spec in CATALOG.items():
        assert re.fullmatch(r"FV-[A-Z]{2,5}-\d{3}", code)
        assert 400 <= spec.http_status <= 599


def test_error_body_shape():
    body = FVError("FV-ACT-002", detail={"action_id": "a1"}).body()
    assert body == {"error": {"code": "FV-ACT-002", "title": "Action already decided", "message": "Action already decided", "detail": {"action_id": "a1"}}}


def test_unknown_code_is_a_programming_error():
    with pytest.raises(KeyError):
        FVError("FV-NOPE-999")


def test_catalog_doc_is_in_sync():
    assert DOC_PATH.read_text(encoding="utf-8") == render_markdown(), "run: python -m app.errors"
