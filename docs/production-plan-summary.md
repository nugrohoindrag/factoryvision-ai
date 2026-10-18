# Production Plan backend: summary

The backend for GUIDE §4, "Checklist Production Plan" (PP-R01 to PP-R84). The shapes follow `CONTRACT.md`. The rules
come from Factory-Vision-master (`planning/*.go`, `production/generation.go`, `production/routing.go`) and were
rewritten in Python.

## Modules

| File | What it holds |
|---|---|
| `backend/app/plans.py` | Plan and WO domain: create, demand, lines, wizard readiness, WO generation, scheduling, resources, confirm, cancel, `replan`, events, the downstream view, the capacity page |
| `backend/app/capacity.py` | Capacity math (`assess`, `status_of`, `sku_capacity`), planning-utilization setting, `CAP-` snapshots with supersede |
| `backend/app/tools/planning.py` | `schedule_work_orders`: a CP-SAT model for molding, painting and assembly WOs |
| `backend/app/routes/plans.py` | HTTP routes |
| `backend/app/forecasts.py` | **Local stand-in** for the forecast branch, with the same signatures as CONTRACT.md. The integrator keeps the forecast agent's version |

Store collections (all per session, all keyed so sessions never collide):

| Collection | Doc key |
|---|---|
| `production_plans` | `plan_id`, a uuid |
| `plan_work_orders` | `{sid}/{wo_id}` |
| `plan_events` | `{sid}/{event_id}` |
| `capacity_snapshots` | `{sid}/{cap_id}` |
| `planning_settings` | `sid` |

`POST /api/demo/reset` clears all of them, and also clears the forecast snapshots.

## Endpoints

Contract endpoints:

- `GET /api/plans?status=&period_from=&period_to=`
- `POST /api/plans`
- `GET /api/plans/{id}`
- `POST /api/plans/{id}/step`
- `GET /api/plans/{id}/demand-candidates`
- `POST /api/plans/{id}/demand`
- `DELETE /api/plans/{id}/demand/{order_id}`
- `GET /api/plans/{id}/demand`
- `POST /api/plans/{id}/forecast-demand`
- `PATCH /api/plans/{id}/lines/{line_id}`
- `POST /api/plans/{id}/work-orders`
- `POST /api/plans/{id}/schedule`
- `PATCH /api/plans/{id}/work-orders/{wo_id}`
- `POST /api/plans/{id}/work-orders/confirm`
- `POST /api/plans/{id}/confirm`
- `POST /api/plans/{id}/cancel`
- `GET /api/capacity`
- `POST /api/capacity/recalculate`
- `GET /api/capacity/snapshots`

Extras:

- `GET /api/capacity/snapshots/{id}`
- `GET|PUT /api/capacity/settings` with body `{planning_utilization_pct}`
- `POST /api/plans/{id}/work-orders/{wo_id}/status` with body `{status}`. This moves a WO forward only: Confirmed → Released → In Production → Completed, and only once the plan is confirmed. It is how a plan reaches In Execution and Completed.

Integrated endpoints:

- `GET /api/orders` and `GET /api/orders/{n}` count plan demand in `planned_qty` and the derived status. The order detail also lists `production_plans`.
- `GET /api/modules/{mes|material|delivery}/summary` gains a `production_plans` block (PP-R74).

## Capacity math (PP-R42 to PP-R48)

Per machine:

- `effective = max(available − planned downtime, 0) × share`
- `capacity = floor(effective × 60 / cycle_s)`

Per SKU:

- `Total` = the sum over machines
- `Planning = floor(Total × utilization / 100)`
- `Buffer = Total − Planning`
- `Gap = max(Demand − Total, 0)`
- `Utilization = Demand / Total`

Status:

- **Within Plan** when Demand ≤ Planning
- **Additional Demand** when Demand ≤ Total
- **Capacity Up Required** otherwise

Inputs:

- **Available minutes:** working days × 1,350 × 0.88.
- **Planned downtime:** 120 minutes on a machine with approved maintenance in the period.
- **Cycle time:** ideal × measured cycle factor / yield. A machine under maintenance uses the fleet's normal factor.
- **Uncomputed machines:** a machine with no cycle time, or one outside the SKU's tonnage class, goes to `uncomputed` with a reason. It is not counted as zero.
- **Assessed quantity:** capacity is assessed on the line's planned qty, using the plan's own `utilization_pct`.

## Customer Orders integration (PP-R27, R28, R72, R73)

An order line's `planned_qty` is the sum of the demand of the plans that are not Cancelled, capped at the ordered qty. The
line is Planned when that sum reaches the ordered qty. The status is derived on every read, so:

- adding demand raises it;
- removing demand or cancelling the plan lowers it;
- confirming the plan keeps it.

## Events (PP-R74)

The smallest honest version is a session event log, `plan_events`, with these events:

- `ProductionPlanConfirmed`
- `ProductionPlanCancelled`
- `WorkOrdersGenerated`
- `CapacityGapDetected`, one per Capacity Up Required SKU in a recalculated snapshot

The MES, Material and Delivery summaries read this log. They also read their own view of the committed plans (Confirmed,
In Execution or Completed):

- **Material:** BOM explosion of the planned qty.
- **Delivery:** planned supply per SKU and required date.
- **MES:** the committed WOs.

No module changes its own numbers from it yet, and there is no Pub/Sub push.

## Re-plan (PP-R81)

`replan(sid, *, reason, forecast_id=None, demand_change_pct=None, user="agent")` builds the plan in these steps:

1. It creates a plan for the current month with origin `agent`.
2. It adds all open order lines in the period.
3. It adds the unordered forecast per SKU.
4. It applies `demand_change_pct` to planned qty.
5. It assesses capacity.

`reason` and `demand_change_pct` are stored on the plan doc as `replan_reason` and `demand_change_pct`. The function
returns the plan detail plus:

- `capacity_delta`: `{sku: {before_gap_pct, after_gap_pct}}`, where `gap_pct = utilization − 100`. It is compared with the latest plan that is not cancelled, or with the same demand without the change.
- `compared_with`.

The approval flow, through the Action Center, belongs to the agentic branch.

## Parity with Factory-Vision-master

Matched:

- **Locks:** Confirmed, In Execution, Completed and Cancelled plans cannot be edited (`assertEditable`).
- **Create:** an end date before the start date is rejected (an equal date is allowed). Utilization is copied onto the plan, `wizard_step` is 1 and the status is Draft.
- **Add demand:**
  - The checks run in the old order: plan, order line, cancelled order, default qty = outstanding, qty ≤ 0, qty > outstanding, duplicate.
  - qty ≤ 0 has two messages: "already fully planned (x of y)" or "must be more than zero". The message for qty > outstanding shows the ordered and already-planned qty.
  - Demand aggregates into one line per product. A new line gets priority = line count + 1 and the earliest required date.
  - Planned follows demand while the two are equal.
- **Remove demand:** the line is deleted when no demand is left. Otherwise demand is re-summed and planned is clamped to it.
- **Update line:** planned < 0 is rejected, and capacity is recomputed on the server.
- **Wizard readiness:** the old step-by-step messages, the furthest reachable step, a valid range of 1 to 6, and going back always allowed.
- **Capacity:** the `AssessCapacity` formula, the uncomputed rule ("not zero") and `CapacityStatusOf`.
- **Capacity snapshots:** one row per product with demand in the period. A recalculation creates a new snapshot, and the old one becomes Superseded with `superseded_by`. There is one `CapacityGapDetected` per Up SKU.
- **WO generation:**
  - Locked statuses are rejected. A plan with no lines is rejected (409).
  - Every routing is validated before anything is written, all-or-nothing, with the codes `NO_ROUTING`, `ROUTING_STEP_INACTIVE`, `PROCESS_INACTIVE`, `SEQUENCE_DUPLICATE`, `SEQUENCE_NOT_CONTINUOUS` and `NO_MAPPABLE_RESOURCE`.
  - Lines are ordered by priority, then product. Lines with planned ≤ 0 are skipped and reported in `skipped_line_ids`.
  - There is one WO per active process, with qty = planned.
  - An existing (line, process) WO is reported in `existed` and reused as the predecessor, so there are no duplicates.
  - WOs form a predecessor chain and start in Draft.
- **WO confirm checklist:** planned qty, start and end, machine, mold (molding only), shift, and no open schedule conflict. If any WO fails, none is confirmed.
- **Confirm:** "already confirmed", and Cancelled or Completed plans cannot be confirmed. Then the checks are: no lines, Up SKUs (named), no WOs, unconfirmed WOs. Effects: Confirmed with by and at, `wizard_step` 6, lines Released, and the `ProductionPlanConfirmed` event with the period, confirmer, orders and planned total.
- **Cancel:** a reason is required. An already cancelled plan is rejected. A plan with WOs In Production or Completed is rejected, with the count. Demand is returned to the order lines. Emits `ProductionPlanCancelled`.

Intentional differences:

- **Plan numbers:** `PP-YYYY-MM`, then `-002`. Capacity snapshots: `CAP-YYYY-MM-NNN`. CONTRACT.md and the GUIDE fix these formats; the old ones were `PLAN-YYYYMM-NNN` and `CAP-YYYYMM-NNN`.
- **Capacity sharing:** the old engine gave each product the full minutes of every compatible machine. Here, the SKUs whose molds sit in the same tonnage class share that class's minutes in proportion to the minutes they need, and the cycle time is the measured one. Without this, the shared IMM-04/05 load (the 18% story) would not show. The story test still uses `planning.get_capacity`, unchanged.
- **Plan utilization:** capacity uses the plan's own `utilization_pct`. The old code read the tenant setting, which contradicts PP-R06.
- **Order status:** it is derived and not monotonic, so cancelling a plan returns its orders to Received, as PP-R73 asks. The old status only ever moved forward.
- **Plan status:** Draft = no lines, Planning = has lines, Ready = the confirm checks pass (derived). In Execution and Completed follow the WO statuses. The old code had no rule for Ready. Because of this, `replan` returns a plan with lines in the Planning status, not Draft.
- **Locked step changes:** a step change on a locked plan returns the detail without saving it, instead of a 409, so the read-only wizard can still move between steps.
- **Remove demand:** it also recomputes `required_date`.
- **Line priority:** it is capped at 5, because the contract limits priority to 1..5.
- **Scheduling:** CP-SAT fills the schedule (PP-R66). The old version was manual. Any edit to a WO, line or schedule clears that WO's confirmation.
- **Operator assignment:** skipped, because it is ➖ in the GUIDE.
