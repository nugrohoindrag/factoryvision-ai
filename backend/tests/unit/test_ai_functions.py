from collections.abc import Iterator
from typing import Any

import pytest
from app.ai import extract, genai, vision
from app.config import settings
from app.documents import DOCUMENTS, schedule_lines
from app.errors import FVError
from app.session import limiter
from app.simulation.engine import State

SID = "44444444-4444-4444-4444-444444444444"
JPEG = b"\xff\xd8\xff" + b"0" * 100


def test_ai_off_is_a_catalogued_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ai", "off")
    with pytest.raises(FVError) as e:
        genai.generate_json("gemini-3.8-flash", ["hi"], {"type": "OBJECT"})
    assert e.value.code == "FV-AI-001"


@pytest.fixture
def fake_analyzer(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    calls: list[str] = []

    def analyze(camera_id: str, _image: bytes, _mime_type: str = "image/jpeg") -> dict[str, Any]:
        calls.append(camera_id)
        return {"camera_id": camera_id, "observations": [{"type": "wip_low", "subject": "FB-ARKA", "value": 6, "severity": "high", "confidence": 0.9}]}

    monkeypatch.setattr(vision, "analyze", analyze)
    yield calls
    limiter.reset(SID)


def test_vision_cache_debounce_and_cap(fake_analyzer: list[str]) -> None:
    snap = State.new("s").snapshot()
    r1 = vision.frame(SID, "CAM-WIP", "hash-a", JPEG, "image/jpeg", snap)
    assert r1["cached"] is False
    assert r1["score"]["precision"] == 1.0
    r2 = vision.frame(SID, "CAM-WIP", "hash-a", JPEG, "image/jpeg", snap)
    assert r2["cached"] is True
    assert len(fake_analyzer) == 1
    with pytest.raises(FVError) as e:
        vision.frame(SID, "CAM-WIP", "hash-b", JPEG, "image/jpeg", snap)
    assert e.value.code == "FV-VIS-002"
    limiter.reset(SID)
    for k in range(settings.vision_calls_per_session):
        limiter.forget_debounce(SID)
        vision.frame(SID, "CAM-Molding", f"cap-{k}", JPEG, "image/jpeg", None)
    limiter.forget_debounce(SID)
    with pytest.raises(FVError) as e:
        vision.frame(SID, "CAM-Molding", "cap-x", JPEG, "image/jpeg", None)
    assert e.value.code == "FV-VIS-001"
    assert vision.latest(SID)["CAM-WIP"]["observations"][0]["subject"] == "FB-ARKA"


def test_invalid_frame() -> None:
    with pytest.raises(FVError) as e:
        vision.frame(SID, "CAM-WIP", "h", b"", "image/gif", None)
    assert e.value.code == "FV-VIS-003"


def test_score_against_simulator_state() -> None:
    s = State.new("s", "disruption_response")
    for _ in range(60):
        s.step()
    snap = s.snapshot()
    assert ("machine_slow", "IMM-04") in vision.truth(snap)
    good = [{"type": "machine_slow", "subject": "IMM-04"}]
    bad = [{"type": "machine_down", "subject": "IMM-02"}]
    assert vision.score(good, snap, "CAM-Molding")["precision"] == 1.0
    assert vision.score(good + bad, snap, "CAM-Molding")["precision"] == 0.5
    assert "wip_low:FB-ARKA" in vision.score([], snap, "CAM-WIP")["missed"]


@pytest.mark.parametrize(
    ("model_ok", "color_ok", "defects", "expected"),
    [
        (True, True, [], "OK"),
        (True, False, [], "WRONG_COLOR"),
        (False, True, [], "WRONG_PART"),
        (True, True, [{"disposition": "Scrap"}], "SCRAP"),
        (True, True, [{"disposition": "Touch Up"}], "TOUCH_UP"),
    ],
)
def test_qc_verdict(*, model_ok: bool, color_ok: bool, defects: list[dict[str, Any]], expected: str) -> None:
    assert extract.verdict(model_ok=model_ok, color_ok=color_ok, defects=defects) == expected


def test_demo_po_raises_fb_arka_by_40_pct() -> None:
    fb = next(r for r in schedule_lines() if r["sku"] == "FB-ARKA")
    assert round(100 * (fb["qty"] / fb["previous_qty"] - 1)) == 40
    mime, make = DOCUMENTS["customer-po-oem-a.pdf"]
    data = make()
    assert mime == "application/pdf"
    assert data.startswith(b"%PDF-1.4")
    assert data.rstrip().endswith(b"%%EOF")
    assert b"FB-ARKA" in data


def test_inspect_photo_maps_defects(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_references(*_args: object) -> list[dict[str, Any]]:
        return []

    def fake_gemini(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "model": "ARKA",
            "position": "Front",
            "same_model_as_reference": True,
            "same_color_as_reference": True,
            "defects": [{"defect_code": "BUBBLE", "confidence": 0.8}, {"defect_code": "NOT_A_CODE", "confidence": 0.9}],
            "confidence": 0.9,
        }

    monkeypatch.setattr(extract, "references", no_references)
    monkeypatch.setattr(genai, "generate_json", fake_gemini)
    out = extract.inspect_photo(JPEG, "image/jpeg", "FB-ARKA", "C02")
    assert [d["defect_code"] for d in out["defects"]] == ["BUBBLE"]
    assert out["defects"][0]["category"] == "Material"
    assert out["verdict"] == "SCRAP"
