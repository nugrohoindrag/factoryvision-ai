import uuid
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from app import materials
from app import purchasing_docs as pd
from app.errors import FVError
from app.store import store

SEED: list[dict[str, Any]] = [
    {
        "po_id": "PO-2702-030",
        "vendor_id": "VEN-WARNA",
        "material_id": "B-C07",
        "order_qty": 120.0,
        "unit_price_idr": 165000,
        "order_date": "2027-02-02",
        "promised_date": "2027-02-08",
        "received_date": None,
        "po_status": "Open",
        "received_qty": 0.0,
        "lot_id": None,
    },
    {
        "po_id": "PO-2701-001",
        "vendor_id": "VEN-ANDALAS",
        "material_id": "PP-EPDM-T20",
        "order_qty": 100.0,
        "unit_price_idr": 31500,
        "order_date": "2027-01-01",
        "promised_date": "2027-01-08",
        "received_date": "2027-01-12",
        "po_status": "Received",
        "received_qty": 100.0,
        "lot_id": "LOT-PPT20-0001",
    },
    {
        "po_id": "PO-2701-002",
        "vendor_id": "VEN-MERAPI",
        "material_id": "PP-EPDM-T20",
        "order_qty": 100.0,
        "unit_price_idr": 33400,
        "order_date": "2027-01-01",
        "promised_date": "2027-01-06",
        "received_date": "2027-01-06",
        "po_status": "Received",
        "received_qty": 100.0,
        "lot_id": None,
    },
]


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pd, "_seed_rows", lambda: tuple(dict(r) for r in SEED))
    monkeypatch.setattr(pd, "_delays", lambda: {"VEN-ANDALAS": 4.0, "VEN-MERAPI": 0.0})
    monkeypatch.setattr(pd, "_on_hand", lambda _sid, _mid: 1000.0)
    monkeypatch.setattr(materials, "get_material", lambda mid: {"material_id": mid})
    monkeypatch.setattr(materials, "stock", lambda *_a, **_k: [])


@pytest.fixture
def sid(offline: None) -> Iterator[str]:
    s = str(uuid.uuid4())
    yield s
    pd.clear(s)
    materials.clear(s)


def _code(fn: Any, *args: Any, **kwargs: Any) -> str:
    with pytest.raises(FVError) as e:
        fn(*args, **kwargs)
    return e.value.code


# --- numbering ------------------------------------------------------------------------------------------------------


def test_numbers_count_per_prefix() -> None:
    d = date(2027, 2, 1)
    assert pd.pr_prefix(d) == "PR-2702-"
    assert pd.po_prefix(d) == "PO-2702-S-"
    assert pd.gr_prefix(d) == "GR-2702-S-"
    assert pd.next_number([], "PR-2702-") == "PR-2702-001"
    assert pd.next_number(["PR-2702-001", "PR-2702-007", "PR-2701-020"], "PR-2702-") == "PR-2702-008"
    assert pd.next_number(["PO-2702-030"], "PO-2702-S-") == "PO-2702-S-001"
    assert pd.next_number(["LOT-PPT20-S-0009"], pd.LOT_PREFIX, 4) == "LOT-PPT20-S-0010"


# --- status flows ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "status", "to"),
    [("submit", "Draft", "Submitted"), ("approve", "Submitted", "Approved"), ("reject", "Submitted", "Rejected"), ("cancel", "Draft", "Cancelled")],
)
def test_pr_flow(action: str, status: str, to: str) -> None:
    assert pd.next_status(pd.PR_FLOW, action, status, "PR-1") == to


def test_wrong_transition_names_current_status() -> None:
    with pytest.raises(FVError) as e:
        pd.next_status(pd.PR_FLOW, "approve", "Draft", "PR-1")
    assert e.value.code == "FV-PUR-005"
    assert e.value.http_status == 409
    assert "Draft" in e.value.message
    assert e.value.detail == {"id": "PR-1", "action": "approve", "status": "Draft", "allowed_from": ["Submitted"]}
    assert _code(pd.next_status, pd.PO_FLOW, "explode", "Draft", "PO-1") == "FV-PUR-014"
    assert _code(pd.next_status, pd.PO_FLOW, "close", "Issued", "PO-1") == "FV-PUR-005"
    assert pd.next_status(pd.PO_FLOW, "close", "Partially Received", "PO-1") == "Closed"


# --- prices, tolerance and choice -----------------------------------------------------------------------------------


def test_vendor_price_rule() -> None:
    from app import plant

    resin, mb, pkg = plant.MATERIAL_BY_ID["PP-EPDM-T20"], plant.MATERIAL_BY_ID["MB-BLK"], plant.MATERIAL_BY_ID["PKG-RT-ARKA"]
    assert pd.vendor_price(plant.VENDOR_BY_ID["VEN-MERAPI"], resin) == 33_400
    assert pd.vendor_price(plant.VENDOR_BY_ID["VEN-MERAPI"], mb) == mb.unit_cost_idr
    assert pd.vendor_price(plant.VENDOR_BY_ID["VEN-WARNA"], plant.MATERIAL_BY_ID["P1"]) == 118_000
    assert pd.vendor_price(plant.VENDOR_BY_ID["VEN-RAKIT"], pkg) == 0
    assert pd.price_label(0) == pd.QUOTATION
    assert pd.price_label(5) is None


def test_choose_earliest_in_time_then_cheapest_and_zero_never_cheapest() -> None:
    def o(v: str, exp: str, ok: str, price: int) -> dict[str, Any]:
        return {"vendor_id": v, "expected_arrival": exp, "arrives_before_need": ok == "in time", "unit_price_idr": price, "po_value_idr": price * 10}

    opts = [o("A", "2027-02-05", "in time", 10), o("B", "2027-02-03", "in time", 12), o("C", "2027-02-01", "late", 1)]
    assert pd.choose(opts)["vendor_id"] == "B"
    assert pd.premium(pd.choose(opts), opts) == 110
    tie = [o("Z", "2027-02-03", "in time", 0), o("Y", "2027-02-03", "in time", 50)]
    assert pd.choose(tie)["vendor_id"] == "Y"
    late = [o("A", "2027-02-09", "late", 10), o("B", "2027-02-07", "late", 12)]
    assert pd.choose(late)["vendor_id"] == "B"
    assert pd.premium(o("Q", "x", "in time", 0), tie) == 0


def test_tolerance_and_receipt_status() -> None:
    assert pd.receipt_limit(100) == 105.0
    assert pd.receipt_limit(-3) == 0.0
    lines = [{"material_id": "A", "qty": 10.0, "received_qty": 0.0}, {"material_id": "B", "qty": 5.0, "received_qty": 0.0}]
    assert pd.status_after_receipt(lines) == "Issued"
    pd.allocate(lines, "A", 10.4)
    assert lines[0]["received_qty"] == 10.4
    assert pd.status_after_receipt(lines) == "Partially Received"
    pd.allocate(lines, "B", 5)
    assert pd.status_after_receipt(lines) == "Received"
    assert pd.received_pct(lines) == 100.0
    assert pd.open_qty(lines[0]) == 0.0


def test_allocate_spreads_over_lines_of_one_material() -> None:
    lines = [{"material_id": "A", "qty": 10.0, "received_qty": 0.0}, {"material_id": "A", "qty": 5.0, "received_qty": 0.0}]
    assert pd.allocate(lines, "A", 12) == 12
    assert [ln["received_qty"] for ln in lines] == [10.0, 2.0]


def test_lateness() -> None:
    assert pd.lateness("Issued", "2027-01-29", date(2027, 2, 1)) == (True, 3)
    assert pd.lateness("Issued", "2027-02-03", date(2027, 2, 1)) == (False, 0)
    assert pd.lateness("Received", "2027-01-01", date(2027, 2, 1)) == (False, 0)


def test_duplicate_matches_material_need_by_and_source() -> None:
    open_lines = [("PR-1", {"material_id": "A", "need_by": "2027-02-10", "source": "mrp:R1"})]
    assert pd.find_duplicate({"material_id": "A", "need_by": "2027-02-10", "source": "mrp:R1"}, open_lines) == "PR-1"
    assert pd.find_duplicate({"material_id": "A", "need_by": "2027-02-10", "source": "manual"}, open_lines) is None
    assert pd.find_duplicate({"material_id": "A", "need_by": "2027-02-11", "source": "mrp:R1"}, open_lines) is None


def test_group_by_vendor_with_override() -> None:
    lines = [
        {"material_id": "PP-EPDM-T20", "suggested_vendor_id": "VEN-MERAPI"},
        {"material_id": "K1", "suggested_vendor_id": "VEN-KILAU"},
        {"material_id": "H1", "suggested_vendor_id": "VEN-KILAU"},
    ]
    assert {k: len(v) for k, v in pd.group_by_vendor(lines).items()} == {"VEN-MERAPI": 1, "VEN-KILAU": 2}
    assert set(pd.group_by_vendor(lines, {"PP-EPDM-T20": "VEN-ANDALAS"})) == {"VEN-ANDALAS", "VEN-KILAU"}


def test_input_checks() -> None:
    assert _code(pd.check_qty, 0) == "FV-PUR-004"
    assert _code(pd.check_qty, "x") == "FV-PUR-004"
    assert pd.check_qty("2.5") == 2.5
    assert _code(pd.parse_date, "02/10/2027", "need_by") == "FV-PUR-010"
    assert pd.parse_date(None, "need_by") is None
    assert _code(pd.check_reason, " no", "reject") == "FV-PUR-011"


def test_scorecard_counts_session_rows() -> None:
    rows = [
        {"order_date": "2027-01-01", "promised_date": "2027-01-05", "received_date": "2027-01-07"},
        {"order_date": "2027-01-01", "promised_date": "2027-01-05", "received_date": "2027-01-05", "origin": "session"},
    ]
    card = pd.scorecard(rows)
    assert card == {
        "po_count": 2,
        "on_time_pct": 50.0,
        "avg_delay_days": 1.0,
        "max_delay_days": 2,
        "effective_lead_time_days": 5.0,
        "session_po_count": 1,
    }
    assert pd.scorecard([]) is None


# --- store-backed flows ---------------------------------------------------------------------------------------------


def test_recommend_any_material(sid: str) -> None:
    r = pd.recommend("PP-EPDM-T20", 1000, "2027-02-09", sid=sid)
    by = {o["vendor_id"]: o for o in r["options"]}
    assert by["VEN-ANDALAS"]["expected_arrival"] == "2027-02-12"
    assert by["VEN-ANDALAS"]["arrives_before_need"] is False
    assert by["VEN-MERAPI"]["expected_arrival"] == "2027-02-06"
    assert r["recommended_vendor_id"] == "VEN-MERAPI"
    assert r["premium_vs_cheapest_idr"] == (33_400 - 31_500) * 1000
    paint = pd.recommend("B-C07", 10, None)
    assert paint["recommended_vendor_id"] == "VEN-WARNA"
    assert paint["options"][0]["unit_price_idr"] == 165_000
    assert _code(pd.recommend, "NOPE", 1, None) == "FV-MAT-002"
    assert _code(pd.recommend, "K1", 0, None) == "FV-PUR-004"


def test_requisition_flow_and_history(sid: str) -> None:
    pr = pd.create_requisition(sid, lines=[{"material_id": "K1", "qty": 50, "need_by": "2027-02-20"}], user="ana")
    assert pr["pr_id"] == "PR-2702-001"
    assert pr["status"] == "Draft"
    ln = pr["lines"][0]
    assert ln["suggested_vendor_id"] == "VEN-KILAU"
    assert ln["source"] == "manual"
    assert ln["duplicate_of"] is None
    edited = pd.update_requisition(sid, pr["pr_id"], [{"material_id": "K1", "qty": 70, "need_by": "2027-02-20"}], "ana")
    assert edited["lines"][0]["qty"] == 70
    pd.transition_requisition(sid, pr["pr_id"], "submit", user="ana")
    assert _code(pd.update_requisition, sid, pr["pr_id"], [], "ana") == "FV-PUR-005"
    assert _code(pd.transition_requisition, sid, pr["pr_id"], "reject", user="boss") == "FV-PUR-011"
    done = pd.transition_requisition(sid, pr["pr_id"], "approve", user="boss")
    assert done["status"] == "Approved"
    assert done["approved_by"] == "boss"
    assert [(h["action"], h["from"], h["to"], h["by"]) for h in done["history"]] == [
        ("create", None, "Draft", "ana"),
        ("edit", "Draft", "Draft", "ana"),
        ("submit", "Draft", "Submitted", "ana"),
        ("approve", "Submitted", "Approved", "boss"),
    ]
    assert _code(pd.transition_requisition, sid, pr["pr_id"], "cancel", user="x") == "FV-PUR-005"
    assert pd.create_requisition(sid, lines=[{"material_id": "K2", "qty": 1}])["pr_id"] == "PR-2702-002"
    assert [p["pr_id"] for p in pd.list_requisitions(sid, "Approved")] == [pr["pr_id"]]
    assert _code(pd.list_requisitions, sid, "Bogus") == "FV-PUR-015"
    assert _code(pd.get_requisition, sid, "PR-2702-999") == "FV-PUR-006"


def test_requisition_validation(sid: str) -> None:
    assert _code(pd.create_requisition, sid, lines=[]) == "FV-PUR-003"
    assert _code(pd.create_requisition, sid, lines=[{"material_id": "K1", "qty": -1}]) == "FV-PUR-004"
    assert _code(pd.create_requisition, sid, lines=[{"material_id": "K1", "qty": 1, "suggested_vendor_id": "VEN-X"}]) == "FV-PUR-001"
    assert _code(pd.create_requisition, sid, lines=[{"material_id": "K1", "qty": 1, "suggested_vendor_id": "VEN-WARNA"}]) == "FV-PUR-007"


def test_duplicate_flag_against_open_pr_and_po(sid: str) -> None:
    a = pd.create_requisition(sid, lines=[{"material_id": "K1", "qty": 5, "need_by": "2027-02-20"}])
    b = pd.create_requisition(sid, lines=[{"material_id": "K1", "qty": 9, "need_by": "2027-02-20"}])
    assert b["lines"][0]["duplicate_of"] == a["pr_id"]
    pd.transition_requisition(sid, a["pr_id"], "cancel", user="u")
    pd.transition_requisition(sid, b["pr_id"], "cancel", user="u")
    c = pd.create_requisition(sid, lines=[{"material_id": "K1", "qty": 9, "need_by": "2027-02-20"}])
    assert c["lines"][0]["duplicate_of"] is None
    po = pd.create_po(sid, vendor_id="VEN-KILAU", lines=[{"material_id": "K1", "qty": 3, "need_by": "2027-02-21"}])
    d = pd.create_requisition(sid, lines=[{"material_id": "K1", "qty": 9, "need_by": "2027-02-21"}])
    assert d["lines"][0]["duplicate_of"] == po["po_id"]


def test_convert_makes_one_po_per_vendor(sid: str) -> None:
    pr = pd.create_requisition(
        sid,
        lines=[
            {"material_id": "PP-EPDM-T20", "qty": 1000, "need_by": "2027-02-20"},
            {"material_id": "K1", "qty": 10},
            {"material_id": "H1", "qty": 4},
        ],
    )
    assert _code(pd.convert_requisition, sid, pr["pr_id"], user="u") == "FV-PUR-005"
    pd.transition_requisition(sid, pr["pr_id"], "submit", user="u")
    pd.transition_requisition(sid, pr["pr_id"], "approve", user="u")
    assert _code(pd.convert_requisition, sid, pr["pr_id"], vendor_overrides={"K1": "VEN-BAJA"}, user="u") == "FV-PUR-007"
    out = pd.convert_requisition(sid, pr["pr_id"], vendor_overrides={"PP-EPDM-T20": "VEN-ANDALAS"}, user="u")
    assert out["po_ids"] == ["PO-2702-S-001", "PO-2702-S-002"]
    got = pd.get_requisition(sid, pr["pr_id"])
    assert got["status"] == "Converted"
    assert got["po_ids"] == out["po_ids"]
    pos = [pd.get_po(sid, x) for x in out["po_ids"]]
    assert [p["vendor_id"] for p in pos] == ["VEN-ANDALAS", "VEN-KILAU"]
    assert [len(p["lines"]) for p in pos] == [1, 2]
    assert all(p["pr_id"] == pr["pr_id"] and p["status"] == "Draft" for p in pos)
    assert pos[0]["total_value_idr"] == 1000 * 31_500
    assert pos[1]["total_value_idr"] == sum(ln["value_idr"] for ln in pos[1]["lines"])


def test_po_flow_issue_receive_close(sid: str) -> None:
    assert _code(pd.create_po, sid, vendor_id="VEN-X", lines=[{"material_id": "K1", "qty": 1}]) == "FV-PUR-001"
    assert _code(pd.create_po, sid, vendor_id="VEN-KILAU", lines=[{"material_id": "P1", "qty": 1}]) == "FV-PUR-007"
    assert _code(pd.create_po, sid, vendor_id="VEN-KILAU", lines=[{"material_id": "K1", "qty": 0}]) == "FV-PUR-004"
    assert _code(pd.create_po, sid, vendor_id="VEN-KILAU", lines=[]) == "FV-PUR-003"
    po = pd.create_po(sid, vendor_id="VEN-KILAU", lines=[{"material_id": "K1", "qty": 100}])
    assert po["po_id"] == "PO-2702-S-001"
    assert po["promised_date"] == "2027-02-09"
    assert po["currency"] == "IDR"
    assert pd.open_lines(sid) == []
    assert _code(pd.receive, sid, po["po_id"], [{"material_id": "K1", "qty": 1}]) == "FV-PUR-005"
    pd.update_po(sid, po["po_id"], lines=[{"material_id": "K1", "qty": 200}], note="more")
    issued = pd.transition_po(sid, po["po_id"], "issue", user="u")
    assert issued["status"] == "Issued"
    assert _code(pd.update_po, sid, po["po_id"], note="x") == "FV-PUR-005"
    assert pd.open_lines(sid)[0]["qty"] == 200
    txns = store().where(materials.TRANSACTIONS, "session_id", sid)
    assert [(t["type"], t["qty"]) for t in txns] == [("Incoming", 200.0)]
    assert _code(pd.receive, sid, po["po_id"], [{"material_id": "K1", "qty": 211}]) == "FV-PUR-009"
    assert _code(pd.receive, sid, po["po_id"], [{"material_id": "K2", "qty": 1}]) == "FV-PUR-016"
    assert _code(pd.receive, sid, po["po_id"], [{"material_id": "K1", "qty": 0}]) == "FV-PUR-004"
    assert _code(pd.receive, sid, po["po_id"], []) == "FV-PUR-003"
    g = pd.receive(sid, po["po_id"], [{"material_id": "K1", "qty": 80}], received_date="2027-02-03")
    assert g["gr_id"] == "GR-2702-S-001"
    assert g["lot_id"] is None
    assert g["po_status"] == "Partially Received"
    assert pd.open_lines(sid)[0]["qty"] == 120
    assert pd.received_on_hand(sid) == {("K1", "WH-PNT"): 80.0}
    assert _code(pd.transition_po, sid, po["po_id"], "cancel", user="u") == "FV-PUR-005"
    closed = pd.transition_po(sid, po["po_id"], "close", user="u")
    assert closed["status"] == "Closed"
    assert pd.open_lines(sid) == []
    kinds = sorted((t["type"], t["qty"]) for t in store().where(materials.TRANSACTIONS, "session_id", sid))
    assert kinds == [("Incoming", -120.0), ("Incoming", 200.0), ("Receipt", 80.0)]
    receipt = next(t for t in store().where(materials.TRANSACTIONS, "session_id", sid) if t["type"] == "Receipt")
    assert receipt["ref"].startswith(g["gr_id"])
    assert receipt["balance_after"] == 1080.0


def test_over_receipt_within_tolerance_completes(sid: str) -> None:
    po = pd.create_po(sid, vendor_id="VEN-MERAPI", lines=[{"material_id": "PP-EPDM-T20", "qty": 1000}], issue=True)
    g = pd.receive(sid, po["po_id"], [{"material_id": "PP-EPDM-T20", "qty": 1050}])
    assert g["po_status"] == "Received"
    assert g["lot_id"] == "LOT-PPT20-S-0001"
    assert pd.open_lines(sid) == []
    g2 = pd.create_po(sid, vendor_id="VEN-MERAPI", lines=[{"material_id": "PP-EPDM-T20", "qty": 10}], issue=True)
    assert pd.receive(sid, g2["po_id"], [{"material_id": "PP-EPDM-T20", "qty": 4}])["lot_id"] == "LOT-PPT20-S-0002"
    assert pd.receive(sid, g2["po_id"], [{"material_id": "PP-EPDM-T20", "qty": 4}], lot_id="MY-LOT")["lot_id"] == "MY-LOT"
    assert [r["gr_id"] for r in pd.list_receipts(sid)] == ["GR-2702-S-003", "GR-2702-S-002", "GR-2702-S-001"]
    assert len(pd.list_receipts(sid, g2["po_id"])) == 2


def test_cancel_issued_removes_incoming(sid: str) -> None:
    po = pd.create_po(sid, vendor_id="VEN-KILAU", lines=[{"material_id": "K1", "qty": 10}], issue=True)
    assert len(pd.open_lines(sid)) == 1
    out = pd.transition_po(sid, po["po_id"], "cancel", user="u")
    assert out["status"] == "Cancelled"
    assert pd.open_lines(sid) == []


def test_expedite(sid: str) -> None:
    po = pd.create_po(sid, vendor_id="VEN-ANDALAS", lines=[{"material_id": "PP-EPDM-T20", "qty": 10}])
    assert _code(pd.expedite_po, sid, po["po_id"], "2027-02-03", "faster please") == "FV-PUR-005"
    pd.transition_po(sid, po["po_id"], "issue", user="u")
    assert po["promised_date"] == "2027-02-08"
    assert _code(pd.expedite_po, sid, po["po_id"], "2027-02-08", "faster please") == "FV-PUR-008"
    assert _code(pd.expedite_po, sid, po["po_id"], "2027-02-03", "") == "FV-PUR-011"
    out = pd.expedite_po(sid, po["po_id"], "2027-02-04", "line stop risk", user="boss")
    assert out["promised_date"] == "2027-02-04"
    assert out["expected_date"] == "2027-02-08"
    h = out["history"][-1]
    assert (h["action"], h["by"], h["reason"], h["old_promised_date"], h["new_promised_date"]) == (
        "expedite",
        "boss",
        "line stop risk",
        "2027-02-08",
        "2027-02-04",
    )


def test_seed_po_is_read_only_but_receivable(sid: str) -> None:
    seed = pd.get_po(sid, "PO-2702-030")
    assert seed["origin"] == "seed"
    assert seed["status"] == "Issued"
    assert seed["read_only"] is True
    for action in ("issue", "close", "cancel"):
        assert _code(pd.transition_po, sid, "PO-2702-030", action, user="u") == "FV-PUR-013"
    assert _code(pd.expedite_po, sid, "PO-2702-030", "2027-02-03", "faster") == "FV-PUR-013"
    assert _code(pd.get_po, sid, "PO-0000-000") == "FV-PUR-012"
    g = pd.receive(sid, "PO-2702-030", [{"material_id": "B-C07", "qty": 50}])
    assert g["origin"] == "seed"
    assert pd.received_by_po(sid) == {("PO-2702-030", "B-C07"): 50.0}
    assert pd.get_po(sid, "PO-2702-030")["status"] == "Partially Received"
    assert pd.get_po(str(uuid.uuid4()), "PO-2702-030")["status"] == "Issued"
    assert [p["po_id"] for p in pd.list_pos(sid) if p["origin"] == "seed"] == ["PO-2702-030"]


def test_vendor_scorecard_counts_received_session_po(sid: str) -> None:
    base = {v["vendor_id"]: v for v in pd.list_vendors()}
    assert base["VEN-MERAPI"]["po_count"] == 1
    assert base["VEN-KILAU"]["po_count"] == 0
    assert base["VEN-KILAU"]["avg_delay_days"] is None
    po = pd.create_po(sid, vendor_id="VEN-MERAPI", lines=[{"material_id": "PP-EPDM-T20", "qty": 10}], issue=True)
    pd.receive(sid, po["po_id"], [{"material_id": "PP-EPDM-T20", "qty": 10}], received_date="2027-02-08")
    v = pd.get_vendor("VEN-MERAPI", sid=sid)
    assert v["scorecard"]["po_count"] == 2
    assert v["scorecard"]["session_po_count"] == 1
    assert v["scorecard"]["avg_delay_days"] == 1.0
    assert v["received_pos"][0]["po_id"] == po["po_id"]
    assert pd.list_vendors()[1]["po_count"] == 1
    assert _code(pd.get_vendor, "VEN-NONE") == "FV-PUR-001"


def test_summary_and_session_isolation(sid: str) -> None:
    other = str(uuid.uuid4())
    pd.create_requisition(sid, lines=[{"material_id": "K1", "qty": 5}])
    pr = pd.create_requisition(sid, lines=[{"material_id": "K2", "qty": 5}])
    pd.transition_requisition(sid, pr["pr_id"], "submit", user="u")
    po = pd.create_po(sid, vendor_id="VEN-KILAU", lines=[{"material_id": "K1", "qty": 10}], promised_date="2027-01-20", issue=True)
    s = pd.summary(sid)
    assert s["kpis"]["pending_requisitions"] == 1
    assert [p["pr_id"] for p in s["pending_requisitions"]] == [pr["pr_id"]]
    assert po["po_id"] in [p["po_id"] for p in s["overdue"]]
    assert s["kpis"]["overdue_pos"] == len(s["overdue"])
    assert s["kpis"]["open_value_idr"] >= 10 * 142_000
    o = pd.summary(other)
    assert o["kpis"]["pending_requisitions"] == 0
    assert pd.list_requisitions(other) == []
    assert all(p["origin"] == "seed" for p in pd.list_pos(other))
    assert pd.open_lines(other) == []
    assert _code(pd.get_requisition, other, pr["pr_id"]) == "FV-PUR-006"
    assert _code(pd.get_po, other, po["po_id"]) == "FV-PUR-012"
    pd.clear(sid)
    assert pd.list_requisitions(sid) == []
    assert all(p["origin"] == "seed" for p in pd.list_pos(sid))


def test_list_pos_filters(sid: str) -> None:
    pd.create_po(sid, vendor_id="VEN-KILAU", lines=[{"material_id": "K1", "qty": 10}])
    pd.create_po(sid, vendor_id="VEN-MERAPI", lines=[{"material_id": "PP-EPDM-T20", "qty": 10}], issue=True)
    assert [p["vendor_id"] for p in pd.list_pos(sid, status="Draft")] == ["VEN-KILAU"]
    assert {p["vendor_id"] for p in pd.list_pos(sid, vendor_id="VEN-WARNA")} == {"VEN-WARNA"}
    assert [p["po_id"] for p in pd.list_pos(sid, material_id="PP-EPDM-T20")] == ["PO-2702-S-002"]
    assert pd.list_pos(sid, overdue=True) == []
    assert _code(pd.list_pos, sid, "Nope") == "FV-PUR-015"
    assert _code(pd.list_pos, sid, vendor_id="VEN-X") == "FV-PUR-001"


def test_action_reference_marks_session_po(sid: str) -> None:
    po = pd.create_po(sid, vendor_id="VEN-MERAPI", lines=[{"material_id": "PP-EPDM-T20", "qty": 10}], note="Action Center ACT-abc123")
    assert pd.session_po_refs(sid) == {po["po_id"], "ACT-abc123"}


def test_po_document_text(sid: str) -> None:
    po = pd.create_po(sid, vendor_id="VEN-RAKIT", lines=[{"material_id": "PKG-RT-ARKA", "qty": 10}], note="rush")
    text = pd.po_document(sid, po["po_id"])
    assert po["po_id"] in text
    assert "PT Rakit Kemasan" in text
    assert "on quotation" in text
    assert "Note: rush" in text
    assert "(seed)" in pd.po_document(sid, "PO-2702-030")
