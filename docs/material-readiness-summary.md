# Material Readiness and MRP backend: summary

This is the backend for the "Readiness and MRP" section of `CONTRACT-MR.md` (rows MR-R10..R17, R20..R25, R27,
R30..R33, R42 and R43). The rules come from Factory-Vision-master (`readinessOf`, `summarise`, `resolveRequirements`,
`RunMrp`, `ReserveForWorkOrder`, `RecordConsumption`) and were rewritten in Python.

## Modules

| File | What it holds |
|---|---|
| `backend/app/readiness.py` | BOM explosion, Available/Shortage/status rules, requirement dates, sequential consumption, plan and WO checks, the readiness listing, the OEM monthly pseudo-plan, and the compact block for `modules._material` |
| `backend/app/mrp.py` | MRP runs (gross, net, recommendation, order-by date), reservations and their ledger rows, consumption variance, `clear(sid)` |
| `backend/app/routes/materials.py` | HTTP routes |
| `backend/app/materials.py` | **Thin stand-in** (`# shortcut: thin stand-in`) for the masterdata branch. It has the contract signatures. The integrator keeps the real module |

Store collections (all per session):

| Collection | Doc key |
|---|---|
| `material_checks` | `{sid}:{plan_id}`, or `{sid}:{plan_id}:{wo_id}` for a WO check |
| `mrp_runs` | `{sid}:{run_id}` |
| `material_reservations` | `{sid}:{reservation_id}` |
| `material_transactions` | `{sid}:{txn_id}`, written only through `materials.record_transaction` |

`POST /api/demo/reset` calls `mrp.clear(sid)`, which removes all four.

## Endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/api/materials/readiness?from=&to=&only_problems=` | `{from, to, formula, session_plans, kpis, plans}` |
| GET | `/api/materials/readiness/plans/{plan_id}` | Latest saved check, or `{status: "NOT_CHECKED"}` |
| POST | `/api/materials/readiness/plans/{plan_id}` | A new plan check (saved) |
| POST | `/api/materials/readiness/plans/{plan_id}/work-orders/{wo_id}` | A new WO check (saved) |
| GET | `/api/materials/requirements?plan_id=` | Exploded requirements. The default is the OEM plan for the current month |
| POST | `/api/mrp/runs` | Body `{horizon_start?, horizon_end?, plan_ids?}` creates a run |
| GET | `/api/mrp/runs` | `{runs: [...]}`, newest first, without results |
| GET | `/api/mrp/runs/latest` | The latest run, or `null` |
| GET | `/api/mrp/runs/{run_id}` | One run with its results |
| GET | `/api/materials/reservations?status=` | `{reservations: [...]}` |
| POST | `/api/materials/reservations` | Body `{plan_id, wo_id}` returns `{reservations: [...]}` |
| POST | `/api/materials/reservations/{id}/release` | The released reservation |
| GET | `/api/materials/consumption?wo_id=` | `{rows, tolerance, tolerance_pct, summary, note, source, from, to}` |

## Rules

- **Explosion (R10):** Required = qty per unit × qty × (1 + scrap % / 100), per SKU × colour. Rows for the same material are collapsed into one.
  - Coloured demand rows are scaled to the line's planned qty (largest remainder). Any remainder, and any demand with no colour, goes to `materials.color_split`.
- **Available (R11):** On hand − reserved + incoming counted by the need date. Incoming is counted only when `expected_date` ≤ required date. A late PO is listed with `counted: false`, and the note says "arrives after need date".
- **Shortage (R12):** max(required − available, 0).
- **Material status (R13):** READY when the shortage is 0, PARTIAL when 0 < available < required, SHORTAGE when available ≤ 0.
- **Plan and WO status (R14):** READY when every material is READY, SHORTAGE when none is READY, otherwise PARTIAL. `readiness_pct` = READY ÷ all × 100, rounded to 1 decimal. With no rows the status is NOT_CHECKED.
- **Requirement date (R15):** the earliest WO `planned_start` of the process that uses the material:
  - Molding for resin and masterbatch
  - Painting for paint
  - Assembly for components and packaging
  - If there is none, it falls back to the earliest line `required_date`, then to `period_start`.
- **Sequential consumption (R16):** all session WOs (excluding Cancelled plans) are ordered by `planned_start`. A WO with no start sorts after scheduled WOs on its date. Each check subtracts what earlier WOs of other plans or WOs already use (`used_before_qty`). A WO's own Active reservations are not counted twice.
- **Safety stock (R17):** `at_risk` = READY and (available − required) < safety stock.
- **Readiness listing (R20, R22, R23):**
  - It covers session plans that overlap the range and are not Cancelled or empty, plus `OEM-YYYY-MM` for each month in the range.
  - KPIs follow the old UI:
    - `readiness_pct` = ready rows ÷ all rows across plans
    - `plans_short` = the number of SHORTAGE plans
    - `materials_short` = the sum of non-ready rows
  - `only_problems` drops READY plans.
- **Saved checks (R25):** every check is saved with `checked_at`. The listing also saves the check for each plan.
- **MRP (R30..R33):**
  - The horizon defaults to TODAY..+30, and an end before the start returns FV-MRP-001.
  - Gross comes from the explosion of the selected plans. Net = max(gross − available, 0), sorted by net descending.
  - `order_by_date` = required date − lead time. The recommendation reads "Procure X unit before date".
  - Runs are numbered `MRP-YYYY-MM-NNN` within the horizon_start month.
- **Reservations (R42):**
  - A WO is reserved once, at min(required, max(on hand, 0)) per material. Reserving it again returns the existing reservations.
  - Releasing a reservation that is not Active is a no-op.
  - Each reserve or release writes a ledger row (`Reserve` with −qty, `Release` with +qty, `balance_after` = free stock).
- **Consumption (R43):** status is NORMAL when |variance| < 0.0001 (the old repo's tolerance), OVER_CONSUMPTION when the variance is above 0, otherwise UNDER_CONSUMPTION. Actuals come from `fact_production.resin_kg`, so only resin has actuals.
  - Without `wo_id`: the last 14 resin production days, by machine × SKU. Planned = good output × resin per unit × (1 + scrap).
  - With `wo_id`: the WO's resin requirement vs production on its machine, SKU and dates.
- **Empty and error states (R27):** `latest_check` returns NOT_CHECKED, and `latest_run` returns null. Errors are coded FV-MR-001..007 and FV-MRP-001..005 (see `docs/error-catalog.md`).
- **Twin module:** `modules._material` adds `readiness`, the OEM pseudo-plan check for the current month, with the twin's `extra_receipts` applied.

## Parity with Factory-Vision-master

| Old | New |
|---|---|
| `readinessOf` | `readiness.status_of` |
| `summarise` | `readiness.summarise` |
| `resolveRequirements` | `readiness.material_row` + `_explode` (incoming is now dated) |
| `RunMrp` | `mrp.run_mrp` / `mrp.net_results` |
| `Recommendation` | `mrp.recommendation` |
| `ReserveForWorkOrder` | `mrp.reserve_work_order` (min(required, on hand), idempotent) |
| `ReleaseReservation` | `mrp.release_reservation` (no-op unless Active) |
| `RecordConsumption` | `mrp.variance_status` (tolerance 0.0001) |

These are new, with no old counterpart: requirement dates from the WO schedule, sequential consumption, the safety-stock flag, the OEM pseudo-plan, and the dated incoming.

## Deviations from the contract

- `readiness(..., *, only_problems)` is keyword-only (ruff FBT).
- The responses carry extra fields: `formula`, `period`, `origin`, `total_materials`, `ready_materials`, `short_count`, plus `used_before_qty`, `safety_stock`, `sources` and `process` on each material row.
- A WO check covers only the materials of that WO's process.
- When earlier WOs have used stock, `available_qty` is clamped at 0.
- An MRP run with no `plan_ids` and no session plans in the horizon uses the OEM pseudo-plans.
- `GET /api/materials/readiness` saves a check for each plan it lists.
- `tolerance_pct` is `null`, because the old tolerance is an absolute quantity (0.0001), not a percent. `tolerance` carries the value.
- The OEM pseudo-plan puts the whole month's demand on `period_start`, so most of its materials show PARTIAL or SHORTAGE.

## Stand-in notes (`app/materials.py`)

- It follows the real module (wt/mr-masterdata 1b63b83) in these respects:
  - `record_transaction`
  - `basecoat_unresolved` on every item from `bom_items_for`
  - `color_split` from the latest snapshot
  - the extra fields on stock rows
  - B-C07 has 615 on hand and 25 reserved (590 usable), with a 120 kg PO expected 2027-02-08
- Its data is static:
  - Basecoats B-C01..C17 have a seed of 1200 kg.
  - The other materials have fixed on-hand values.
  - It raises FV-DATA-001, because the FV-MAT codes do not exist on this branch.
- Unit tests monkeypatch it with a small fake world. The integration tests use only assertions that hold for both the stand-in and the real module.
- I checked the code against the real module with a scratch merge of wt/mr-masterdata plus this branch. The full suite passed there (373 passed). The only failure was the error catalog, which must be regenerated after merging.
