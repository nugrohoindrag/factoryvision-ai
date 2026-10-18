import pytest

from app.ai import extract, genai, vision
from app.errors import FVError
from app.session import limiter
from app.documents import DOCUMENTS, schedule_lines
from app.simulation.engine import State

SID = "44444444-4444-4444-4444-444444444444"
JPEG = b"\xff\xd8\xff" + b"0" * 100


def test_ai_off_is_a_catalogued_error(monkeypatch):
    monkeypatch.setattr(genai.settings, "ai", "off")
    with pytest.raises(FVError) as e:
        genai.generate_json("gemini-3.8-flash", ["hi"], {"type": "OBJECT"})
    assert e.value.code == "FV-AI-001"


@pytest.fixture
def fake_analyzer(monkeypatch):
    calls = []

    def analyze(camera_id, image, mime_type="image/jpeg"):
        calls.append(camera_id)
        return {"camera_id": camera_id, "observations": [{"type": "wip_low", "subject": "FB-ARKA", "value": 6, "severity": "high", "confidence": 0.9}]}

    monkeypatch.setattr(vision, "analyze", analyze)
    yield calls
    limiter.reset(SID)


def test_vision_cache_debounce_and_cap(fake_analyzer):
    snap = State.new("s").snapshot()
    r1 = vision.frame(SID, "CAM-WIP", "hash-a", JPEG, "image/jpeg", snap)
    assert r1["cached"] is False and r1["score"]["precision"] == 1.0
    r2 = vision.frame(SID, "CAM-WIP", "hash-a", JPEG, "image/jpeg", snap)
    assert r2["cached"] is True and len(fake_analyzer) == 1
    with pytest.raises(FVError) as e:
        vision.frame(SID, "CAM-WIP", "hash-b", JPEG, "image/jpeg", snap)
    assert e.value.code == "FV-VIS-002"
    limiter.reset(SID)
    from app.config import settings
    for k in range(settings.vision_calls_per_session):
        limiter._last.clear()
        vision.frame(SID, "CAM-Molding", f"cap-{k}", JPEG, "image/jpeg", None)
    limiter._last.clear()
    with pytest.raises(FVError) as e:
        vision.frame(SID, "CAM-Molding", "cap-x", JPEG, "image/jpeg", None)
    assert e.value.code == "FV-VIS-001"
    assert vision.latest(SID)["CAM-WIP"]["observations"][0]["subject"] == "FB-ARKA"


def test_invalid_frame():
    with pytest.raises(FVError) as e:
        vision.frame(SID, "CAM-WIP", "h", b"", "image/gif", None)
    assert e.value.code == "FV-VIS-003"


def test_score_against_simulator_state():
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


@pytest.mark.parametrize("model_ok,color_ok,defects,expected", [
    (True, True, [], "OK"), (True, False, [], "WRONG_COLOR"), (False, True, [], "WRONG_PART"),
    (True, True, [{"disposition": "Scrap"}], "SCRAP"), (True, True, [{"disposition": "Touch Up"}], "TOUCH_UP"),
])
def test_qc_verdict(model_ok, color_ok, defects, expected):
    assert extract.verdict(model_ok, color_ok, defects) == expected


def test_demo_po_raises_fb_arka_by_40_pct():
    fb = next(r for r in schedule_lines() if r["sku"] == "FB-ARKA")
    assert round(100 * (fb["qty"] / fb["previous_qty"] - 1)) == 40
    mime, make = DOCUMENTS["customer-po-oem-a.pdf"]
    data = make()
    assert mime == "application/pdf" and data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    assert b"FB-ARKA" in data


def test_inspect_photo_maps_defects(monkeypatch):
    monkeypatch.setattr(extract, "references", lambda *a: [])
    monkeypatch.setattr(genai, "generate_json", lambda *a, **k: {
        "model": "ARKA", "position": "Front", "same_model_as_reference": True, "same_color_as_reference": True,
        "defects": [{"defect_code": "BUBBLE", "confidence": 0.8}, {"defect_code": "NOT_A_CODE", "confidence": 0.9}], "confidence": 0.9})
    out = extract.inspect_photo(JPEG, "image/jpeg", "FB-ARKA", "C02")
    assert [d["defect_code"] for d in out["defects"]] == ["BUBBLE"]
    assert out["defects"][0]["category"] == "Material" and out["verdict"] == "SCRAP"
