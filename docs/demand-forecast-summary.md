# Demand Forecast (module 1): summary

Rewrite of the old console's Demand Forecast (`DemandForecastPage.tsx`, `planning/forecast.go`, `domain.go`) for
FactoryVision-Lite. The rules were read from the old product and written again in Python/React; months became weeks
and days, the historical average became the BQML ARIMA_PLUS engine (local trend + weekday model in dev).

## What was built

| Piece | Where |
|---|---|
| Snapshot store, rules, numbering, supersede, comparison, accuracy | `backend/app/forecasts.py` |
| HTTP routes | `backend/app/routes/forecasts.py` (included in `app/main.py`) |
| Engine reuse, per-customer history, 4-week backtest | `backend/app/tools/forecast.py` |
| Reset clears snapshots and activity | one line in `backend/app/twin.py` (`forecasts.clear`) |
| Error codes `FV-FC-001..005` | `backend/app/errors.py`, `docs/error-catalog.md` |
| Page | `frontend/src/pages/Forecast.tsx` (`ForecastPage`, moved out of `Modules.tsx`) |
| BQML backtest counterpart (optional) | `infra/bq_forecast.sql` |

### Endpoints

| Method + path | Returns |
|---|---|
| `POST /api/forecasts` `{window_days, horizon_weeks, skus?, per_customer?}` | full snapshot (+ `supersedes`, `used_by_plans: []`) |
| `GET /api/forecasts?status=` | `{snapshots: [headers], statuses, windows, horizons, products, customers, rules}`; seeds one snapshot when the session has none |
| `GET /api/forecasts/{id}` | full snapshot + `used_by_plans` (plan numbers) + `used_by_plan_refs` (`plan_id`, `plan_number`, `status`) |
| `GET /api/forecasts/{id}/comparison` | forecast vs orders on the book, per SKU and per line/week, with variance (DF-R52) |
| `GET /api/forecasts/{id}/vs-orders?sku=` | forecast (from the snapshot) vs firm orders vs customer schedule vs revised PO (DF-R50/R51) |
| `GET /api/forecasts/accuracy?sku=` | 4-week backtest: per week error, MAPE, bias (DF-R53) |
| `GET /api/forecasts/activity` | "Forecast generated" entries, newest first (DF-R28) |
| `PUT/PATCH/DELETE /api/forecasts/{id}` | always `409 FV-FC-004`: snapshots are read-only (DF-R25) |

Python API (fixed by `CONTRACT.md`): `generate`, `list_snapshots`, `get_snapshot`, `latest`, `qty_for`, `clear`; plus
`comparison(sid, forecast_id)`, `accuracy(sid, sku=None)`, `vs_orders`, `activity`, `on_generated(fn)` (listener for
downstream modules, e.g. a re-plan trigger).

Store: collection `forecast_snapshots`, doc id `"{session_id}:{forecast_id}"` (the doc's `forecast_id` stays
`FC-2027-02-001`). Every session numbers from 001 and the collection is shared, so a bare `forecast_id` key would let
sessions overwrite each other. Same for `forecast_activity`.

## Rules, row by row

**Generate and parameters**
- DF-R01: Generate button stores a new snapshot; disabled and labelled "Generating…" while the request runs.
- DF-R02: window 30 / 60 / 90 days (the old 3/6/12 months); anything else → `FV-FC-001` with `detail.fields[{field: "window_days"}]`. Booleans and strings are rejected too.
- DF-R03: horizon 4 / 6 / 8 weeks, default 6; period = 2027-02-01 to the last day of the last week.
- DF-R04: all bad fields are reported together in one `FV-FC-001`; an unknown SKU → `FV-DATA-001` with the field. The UI prints each field message under its input.
- DF-R05: `skus` limits the snapshot; omitted = all SKUs; an empty list is an error; duplicates are folded; order follows the master data.
- DF-R06: `per_customer` gives one line per (SKU, customer, week). The SKU's weekly forecast is split by each customer's share of the window's orders (largest remainder, so the lines add up to the SKU total), and each customer has its own history and insufficient-history flag.

**Calculation**
- DF-R10..R13, R16, R17: unchanged engine (`tools/forecast.py`): Cancelled excluded, empty days = 0, history ends 2027-01-31, BQML ARIMA_PLUS in the cloud with the local fallback, weekly totals rounded and never negative, non-working days 0.
- The window also decides the local model's fit: the last `min(window, 42)` days. The default (90) therefore fits on 42 days, exactly as `bq_forecast`, so the planted FB-ARKA story (+36.9 %, target 30..40) is the same number on the page, in the agent tool and in `test_stories.py`. BQML is trained once on 90 days, so there the window only sets the history kept and checked.
- DF-R14: `history_weeks_with_orders` = 7-day buckets (counted back from today) with qty > 0; `history_weeks` = number of buckets (13 for 90 days; the oldest bucket is partial and carries `days`).
- DF-R15: `insufficient_history` = with-orders < buckets (the old rule, months → weeks). Numbers are still produced.

**Snapshot**
- DF-R20, R22, R23: header (window, horizon, SKUs, per-customer, engine, method, generated at/by, period, history range, status) + lines + the weekly history that produced them + per-SKU summary (change, peak, totals). The detail page renders from the stored doc, nothing is recomputed.
- DF-R21: `FC-2027-02-NNN`, max existing number for the month + 1, three digits, per session; generation is serialised by a lock.
- DF-R24: a new snapshot with the same period, window, horizon, SKU set and per-customer flag supersedes the older Generated one(s): `status=Superseded`, `superseded_by`, `superseded_at`; the new one lists them in `supersedes`.
- DF-R25: only those three status fields are ever written after creation; the store returns copies, and write verbs on the route answer `FV-FC-004`.
- DF-R26: `Generated` / `Superseded`; `?status=` accepts either (case-insensitive), anything else → `FV-FC-003`.
- DF-R27: `POST /api/demo/reset` deletes the session's snapshots and activity; the next list seeds `FC-2027-02-001` (`generated_by: "seed"`).
- DF-R28: each generate writes an activity entry (superseded ids, line count, insufficient count, params) and calls the `on_generated` listeners; a failing listener is logged and does not fail the generate.

**Page** (`Forecast.tsx`)
- DF-R30 rule sentence under the title; R31 snapshot chips `FC-… · status` (⚠ when a SKU is thin), newest first, status filter; R32 newest Generated opens by default, a user pick is kept while it is listed; R33/R34 SKU dropdown `FB-ARKA — Front bumper ARKA (MPV)`; R35 KPIs: change by last week, peak week, customer PO change, firm coverage, insufficient-history SKU count, snapshot status; R36 history + forecast line chart with zero weeks drawn as zero; R37 weekly history table and forecast table under the chart, plus history average and forecast total; R38 one-sentence method from the snapshot; R39 badge "Insufficient history: only X of N weeks have orders" per SKU and per customer; R40 banner "Used by Production Plan: PP-2027-02. This snapshot will not change…"; Superseded snapshots get a banner naming the replacement; R41 empty state with a Generate button (and "No Superseded snapshot. Show all" under a filter); R42 error message + Retry for the list and the detail; R43 bars per SKU by the final week.
- DF-R50/R51 forecast vs firm orders vs customer schedule vs revised-by-PO, with the forecast taken from the snapshot, so approving a PO moves only "Revised by PO".
- DF-R52 forecast vs actual orders per SKU with variance; actual = orders on the book with a requested delivery in the week (session orders included, Cancelled excluded).
- DF-R53 4-week backtest: model fitted up to 2027-01-04, compared with what was ordered; MAPE and bias per SKU.
- DF-R54: the agent reads `latest()`, `get_snapshot()` (lines, history, `sku_summary`, `calendar_notes`, `method`), `comparison()` and `accuracy()`.

## Parity with the old console, and deliberate differences

Matched: allowed lookback list and per-field rejection; current period excluded; empty periods = 0; Cancelled excluded;
months-with-history count and the `< lookback` insufficient rule (flag, not hide); per-customer lines; product filter;
`PREFIX-NNN` numbering per month; insert, then supersede same-question Generated snapshots, lines never touched;
audit entry + event with superseded ids, line count and insufficient count; status list filter; detail with plans
using it; comparison endpoint; 404 for a missing snapshot; UI header sentence, lookback chips, disabled button while
running, generate notice/error, error state with Retry, empty state with Generate, snapshot chips "number · status",
newest selected, metric cards (period/lookback, insufficient count, status with superseded note), used-by-plan banner,
`SKU — name`, insufficient badge "only X of N", zero periods visible, formula sentence.

Different, on purpose:
- Average → engine forecast (ADR-20 → BQML, guide DF-R13); rounding to nearest instead of `ceil(average)`.
- Months → weeks/days (guide); the 30-day window gives a partial oldest week.
- Period start/end inputs → horizon 4/6/8 weeks from today (guide DF-R03).
- Async job + polling → one synchronous request (guide DF-R07 ➖).
- Supersede key adds horizon, SKU set and per-customer to period + window: a different horizon or SKU filter is a different question (guide DF-R24 "same parameters").
- Per-customer: share split of the SKU forecast instead of a separate average per customer, so the lines add up to the SKU forecast.
- Unknown status filter → `FV-FC-003` (old: silently ignored); unknown SKU → `FV-DATA-001` (old: silently no line), both per guide DF-R04.
- Comparison counts orders by requested delivery date per week (old: order date per period), because the forecast is built on requested delivery dates.
- Newest *Generated* opens by default (old: newest of any status), per guide DF-R32.

## How it was tested

- `backend/tests/unit/test_forecasts.py` (27 tests, no warehouse: `_series` is faked): numbering, weekly buckets with zero weeks and partial week, today excluded, insufficient history, share split, every invalid field, several bad fields at once, unknown SKU, snapshot shape and line ids, SKU filter, per-customer totals, supersede keeps numbers, returned copies cannot mutate the store, seed + newest-first + status filter + bad status, `latest`, missing snapshot and other session, independent numbering per session, `used_by_plans`, `qty_for` overlap, `clear`, activity + listeners, method text.
- `backend/tests/integration/test_forecasts_api.py` (18 tests, real DuckDB): seeding and options, planted FB-ARKA story equals `bq_forecast` line by line, supersede over HTTP, filter + per-customer, catalog errors per field, status filter / 404 / session header, read-only verbs, used-by-plan data, comparison picks up a newly entered order, vs-orders uses snapshot numbers and `FV-FC-005`, backtest, activity and reset, Python API names and session-keyed doc ids.
- `frontend/src/pages/Forecast.test.tsx` (8 tests): helpers, default view (rules, header, banner, KPIs, history table, method, revised PO, accuracy, variance), insufficient badge on SKU switch, status filter + Superseded banner, generate body and notice, field errors, error + Retry, empty state.
- `tests/integration/test_stories.py` stays green.
