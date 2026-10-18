# Purchasing backend: PU rows mapped to code

Contract: `PURCHASING.md` (PU-R01..R35 are the backend rows). Module: `backend/app/purchasing_docs.py`.
Routes: `backend/app/routes/purchasing.py` (prefix `/api/purchasing`, header `X-Session-Id`, user from `X-User`, default `user`).
Tests: `backend/tests/unit/test_purchasing_rules.py` (no warehouse), `backend/tests/integration/test_purchasing_api.py` (DuckDB, HTTP).

## Vendors (R01..R05)

| Row | Code | Notes |
|---|---|---|
| R01 | `list_vendors`, `GET /vendors` | category, quoted lead time, price, supplied materials, scorecard fields |
| R02 | `get_vendor`, `GET /vendors/{id}` | `scorecard`, `materials`, `open_pos`, `received_pos` (last 10); FV-PUR-001 |
| R03 | `_scorecards`, `scorecard` | session POs count once every line is received; `session_po_count` |
| R04 | `recommend`, `choose`, `premium`, `GET /recommend` | any material in `MATERIAL_VENDORS`; scenario `vendor_extra_delay` with `sid`; FV-PUR-002 |
| R05 | `vendor_price`, `price_label` | price 0 shows "price on quotation" and never wins a tie |

## Requisitions (R06..R13)

| Row | Code | Notes |
|---|---|---|
| R06 | `next_number`, `pr_prefix` | `PR-YYMM-NNN` per session and month; key `{sid}:{pr_id}` in `purchase_requisitions` |
| R07 | `_pr_line` | suggested vendor from `recommend` unless given |
| R08 | `create_requisition`, `_mrp_lines`, `_plan_lines` | `from_mrp_run` (net > 0), `from_plan` (SHORTAGE/PARTIAL, short qty); FV-PUR-003, FV-PUR-004 |
| R09 | `PR_FLOW`, `next_status`, `transition_requisition`, `update_requisition` | FV-PUR-005 names the status; reject needs a reason (FV-PUR-011) |
| R10 | `find_duplicate`, `_flag` | material + need_by + source on an open PR (Draft/Submitted/Approved) or open session PO |
| R11 | `list_requisitions`, `get_requisition` | history `{at, by, action, from, to, reason}`; FV-PUR-006, FV-PUR-015 |
| R12 | `convert_requisition`, `group_by_vendor` | one Draft PO per vendor; `vendor_overrides` per material (FV-PUR-007) |
| R13 | PR `po_ids`, PO `pr_id` | |

## Purchase orders (R14..R24)

| Row | Code | Notes |
|---|---|---|
| R14 | `create_po`, `po_prefix` | `PO-YYMM-S-NNN`; promised = today + quoted lead time unless given |
| R15 | `create_po`, `_po_lines`, `POST /orders` | FV-PUR-001, FV-PUR-007, FV-PUR-004, FV-PUR-003 |
| R16 | `PO_FLOW`, `transition_po`, `status_after_receipt`, `update_po` | cancel refused once anything is received |
| R17 | `materials.incoming` + `open_lines` | issue adds `source: "session_po"` rows, expected = promised + vendor delay (same rule as seed); ledger type `Incoming` |
| R18 | `transition_po` | cancel/close drop the open incoming and write a negative `Incoming` ledger row |
| R19 | `list_pos`, `_seed_doc`, `_view` | session POs plus seed open POs (`origin: "seed"`, `read_only`); `received_pct`, `overdue`, `days_late` |
| R20 | `get_po` | lines with `qty`, `received_qty`, `open_qty`; receipts; history; `pr_id` |
| R21 | `expedite_po` | Issued/Partially Received only; earlier date (FV-PUR-008); reason (FV-PUR-011) |
| R22 | `summary` | `overdue` list and `kpis.overdue_pos` |
| R23 | `line_value`, `_view` | total = sum of line values |
| R24 | `po_document`, `GET /orders/{id}/document` | text/plain |

## Goods receipts (R25..R31)

| Row | Code | Notes |
|---|---|---|
| R25 | `receive`, `gr_prefix` | `GR-YYMM-S-NNN` in `goods_receipts` |
| R26 | `receipt_limit` | open x 1.05 (FV-PUR-009); FV-PUR-004; FV-PUR-005; FV-PUR-016 for a material not on the PO |
| R27 | `received_on_hand` in `materials.stock`; `open_lines`/`received_by_po` in `materials.incoming` | on hand up, incoming down by min(received, open); ledger `Receipt`, ref = GR id |
| R28 | `allocate`, `status_after_receipt` | Received when every line is at or above ordered |
| R29 | `LOT_PREFIX` | `LOT-PPT20-S-NNNN` for PP-EPDM-T20 when no lot is given |
| R30 | `list_receipts`, `GET /receipts`, `GET /orders/{id}/receipts` | newest first |
| R31 | `receive` on a seed PO | stored in the session; seed incoming for that session drops |

## Summary and integration (R32..R35)

| Row | Code | Notes |
|---|---|---|
| R32 | `modules.summary(..., sid=)` merges `purchasing_docs.summary` | keeps `scorecard`, `resin_po`, `placed`; adds `kpis {open_pos, overdue_pos, open_value_idr, pending_requisitions, draft_pos, approved_requisitions}`, `overdue`, `pending_requisitions` |
| R33 | `POST /requisitions` with `from_mrp_run` / `from_plan` | buttons belong to the UI |
| R34 | `twin.reset` calls `purchasing_docs.clear` | |
| R35 | every store doc carries `session_id`, keys `{sid}:{id}` | isolation tested in both tiers |

## Deviations from the contract text

- `list_vendors`, `get_vendor` and `recommend` follow the listed signatures and take an optional keyword `sid=None` (scenario delay and session scorecard). Without it they use seed data and no scenario delay.
- `TXN_TYPES` had no incoming type, so `"Incoming"` was added. It does not change the ledger balance.
- R05 price: a vendor's own quoted price wins for materials of its category (the two resin vendors on resin); otherwise the material's own price; otherwise 0, shown as price on quotation. Taking the material price first would give both resin vendors 31,500 and remove the premium the agent has to report.
- `arrives_before_need` is `expected <= need_by` (readiness counts incoming the same way); `recommend_po` uses `<`.
- `routes/core.py` passes `sid` to `modules.summary` (one line), because `_purchasing` builds from cached overrides and has no session.
- `approved_po` rows from `effects.extra_receipts` are dropped from `materials.incoming` once a session PO references them (po id, `action_id` field, or `ACT-...` in its note), so a PO approved in the Action Center is not counted twice.
- Extra keys beyond the contract shapes: `read_only`, `expected_delay_days`, `received_date`, `price_label`, `need_by`/`source`/`note` on PO lines; receipts returned by `receive` also carry `po_status`, `po` and `stock`.
- Error codes used: FV-PUR-001..016 (010 invalid date, 011 reason required, 012 PO not found, 013 seed PO read-only, 014 unknown action, 015 unknown status, 016 material not on PO).
