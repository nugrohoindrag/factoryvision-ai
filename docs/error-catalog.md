# Error catalog

Generated from `backend/app/errors.py` (`python -m app.errors`). Do not edit by hand.

Every error response has the body `{"error": {"code", "title", "message", "detail"}}`.
Agent tools return the same object instead of raising, so the agent can explain the failure.

| Code | HTTP | Title | When | What to do |
|---|---|---|---|---|
| `FV-REQ-001` | 400 | Invalid request | The body or query parameters fail validation. | Fix the request; `detail` lists the invalid fields. |
| `FV-REQ-002` | 404 | Unknown module | `/api/agents/{module}` names a module that does not exist. | Use one of forecast, planning, mes, material, purchasing, quality, delivery. |
| `FV-REQ-003` | 404 | Unknown scenario | A disruption or replay scenario is not defined. | Use a scenario listed by the simulator. |
| `FV-REQ-004` | 404 | Resource not found | The request targets a path that does not exist. | Check the URL against the API routes (`/docs` lists them). |
| `FV-AUTH-001` | 401 | Not signed in | Auth is required and the Firebase ID token is missing or invalid. | Sign in with the demo account and retry. |
| `FV-SES-001` | 400 | Missing session | The `X-Session-Id` header is missing or malformed. | The web app creates one per tab; API clients must send a UUID. |
| `FV-RATE-001` | 429 | Live run limit reached | The session used all its live AI runs. | Switch to Replay; a reset does not refill live runs. |
| `FV-RATE-002` | 429 | Agent question limit reached | The session asked agents more often than allowed. | Wait or use Replay. |
| `FV-DATA-001` | 404 | Unknown entity | A SKU, machine, material, vendor, lot or order ID does not exist. | Check the ID against the master data. |
| `FV-DATA-002` | 503 | Warehouse unavailable | BigQuery or the local DuckDB file cannot be reached. | Local: run `python -m app.db.build_local`. Cloud: check the dataset and IAM. |
| `FV-SQL-001` | 403 | Query rejected | An agent SQL query is not a single read-only SELECT. | Guardrail; agents may only read. |
| `FV-SQL-002` | 403 | Table not allowlisted | An agent SQL query reads a table outside the allowlist. | Guardrail; extend the allowlist only through a code change. |
| `FV-PLAN-001` | 422 | No feasible plan | CP-SAT found no feasible molding plan within the time limit. | Relax the scenario (allow mold moves, extra shifts) and retry. |
| `FV-PP-001` | 404 | Production plan not found | The plan ID does not exist in this session. | Refresh the plan list; a reset clears the session's plans. |
| `FV-PP-002` | 400 | Invalid plan period | The period start or end is missing, not a date, or the end is before the start. | Send ISO dates with period_end on or after period_start. |
| `FV-PP-003` | 409 | Production plan locked | The plan is Confirmed, In Execution, Completed or Cancelled; a confirmed plan is a production commitment. | Leave it as it is, or create a new plan for the change. |
| `FV-PP-004` | 404 | Order line not found | The customer order line named for the plan demand does not exist. | Pick a line from GET /api/plans/{plan_id}/demand-candidates. |
| `FV-PP-005` | 409 | Cancelled order | A line of a cancelled customer order was added to a plan. | Cancelled orders cannot enter a production plan. |
| `FV-PP-006` | 400 | Invalid demand quantity | The demand quantity is zero or negative. | Send a quantity above zero, or leave it out to take the remaining quantity. |
| `FV-PP-007` | 409 | Order line already fully planned | Every unit of the order line is already in a production plan. | Nothing is left to plan; release demand from another plan first. |
| `FV-PP-008` | 400 | Demand exceeds remaining quantity | The demand is more than ordered minus already planned on the order line. | Lower the quantity to the remaining quantity. |
| `FV-PP-009` | 409 | Order line already in the plan | The same customer order line was added to one plan twice. | Change the plan line's planned quantity instead. |
| `FV-PP-010` | 404 | Plan demand not found | The order line to release is not demand of this plan. | Refresh the plan's demand list. |
| `FV-PP-011` | 404 | Plan line not found | The plan line ID does not exist on this plan. | Refresh the plan. |
| `FV-PP-012` | 400 | Invalid plan line values | The planned quantity is negative or the priority is outside 1 to 5. | Fix the fields listed in `detail`. |
| `FV-PP-013` | 409 | Wizard step locked | A later wizard step was opened before its prerequisite exists. | Complete the step named in the message; going back is always allowed. |
| `FV-PP-014` | 400 | Invalid wizard step | The wizard step is not 1 to 6. | Send a step from 1 (Demand) to 6 (Confirmation). |
| `FV-PP-015` | 409 | Nothing to generate | The plan has no plan lines, or no line has a planned quantity above zero. | Add demand and set planned quantities before generating work orders. |
| `FV-PP-016` | 422 | Invalid routing | A product routing failed validation, so no work order was created at all. | Fix the routing listed in `detail`, then generate again. |
| `FV-PP-017` | 404 | Work order not found | The work order ID does not belong to this plan. | Refresh the plan's work orders. |
| `FV-PP-018` | 400 | Invalid work order resource or schedule | The machine or mold cannot run the product, or the schedule fields are invalid. | Choose a compatible machine and mold; planned end must follow planned start. |
| `FV-PP-019` | 409 | Work order transition not allowed | The status change is not a step of the work order lifecycle, a guard fails (schedule, machine, previous process, output), or the plan is not confirmed yet. | Fix every reason in `detail.reasons`; work orders move on the floor only after the plan is confirmed. |
| `FV-PP-020` | 409 | Work orders not ready to confirm | A work order lacks quantity, schedule, machine, mold or shift, or overlaps another on its machine. | Complete the checklist in `detail`; no work order was confirmed. |
| `FV-PP-021` | 409 | Plan has no lines | Confirmation was asked for a plan without plan lines. | Add demand first. |
| `FV-PP-022` | 409 | Capacity Up Required | A plan line's planned quantity is above total capacity. | Request more capacity or lower the planned quantity of the SKUs named. |
| `FV-PP-023` | 409 | No work orders | The plan has no generated work orders yet. | Generate work orders in Step 3. |
| `FV-PP-024` | 409 | Work orders not confirmed | Some of the plan's work orders are not confirmed. | Confirm every work order before confirming the plan. |
| `FV-PP-025` | 409 | Plan cannot be confirmed | The plan is already confirmed, or it is cancelled or completed. | No retry needed. |
| `FV-PP-026` | 400 | Cancellation reason required | The cancel request has no reason of at least 3 characters. | Give the reason. |
| `FV-PP-027` | 409 | Plan cannot be cancelled | The plan is already cancelled, or some of its work orders are in production or completed. | Leave the plan; stop the running work orders first. |
| `FV-PP-028` | 422 | No feasible schedule | CP-SAT found no schedule for the plan's work orders within the time limit. | Lower planned quantities or widen the period, then schedule again. |
| `FV-PP-029` | 400 | Invalid forecast demand | The SKU is unknown, no forecast snapshot exists, or the quantity is above the unordered forecast. | Use a SKU in the snapshot and at most the unordered forecast quantity. |
| `FV-WO-001` | 409 | Schedule clash | A reschedule or machine change would double-book the machine or run into its approved maintenance. | Pick another time or machine; the message names the work order already booked there. |
| `FV-WO-002` | 409 | Work order cannot be split | The work order is not Confirmed, Released or In Production, was already split or is a part, has reported output, or its plan is not confirmed. | Split a confirmed work order before it reports output. |
| `FV-WO-003` | 409 | Work order not dispatchable here | The work order's plan is still being planned (use the wizard) or is final, the work order was split (act on its parts), or it is completed or cancelled. | Change an open plan in its wizard; dispatch the parts of a split work order. |
| `FV-CAP-001` | 400 | Invalid capacity period | The capacity period start or end is not a date, or the end is before the start. | Send ISO dates with period_end on or after period_start. |
| `FV-CAP-002` | 404 | Capacity snapshot not found | The CAP snapshot to recalculate does not exist in this session. | List snapshots with GET /api/capacity/snapshots. |
| `FV-CAP-003` | 400 | Invalid planning utilization | The planning utilization is not above 0 and at most 100 percent. | Send a percentage in (0, 100]. |
| `FV-CAP-004` | 400 | Invalid capacity assessment | The SKU is not a product in the plant, or the quantity is not a whole number of at least 0. | Send a SKU from the product list and a quantity of 0 or more. |
| `FV-MR-001` | 404 | Plan not found for material readiness | The production plan does not exist in this session, or an OEM-YYYY-MM id names a month with no OEM monthly plan. | Pick a plan from GET /api/materials/readiness. |
| `FV-MR-002` | 404 | Work order not found | The work order is not part of the production plan, or does not exist in this session. | Generate work orders in the plan wizard, then use an id from the plan detail. |
| `FV-MR-003` | 400 | Invalid readiness range | `from` or `to` is not an ISO date, or `to` is before `from`. | Send ISO dates with `to` on or after `from`, or leave them out for today to +30 days. |
| `FV-MR-004` | 409 | Plan is cancelled | A material check or reservation was asked for a cancelled production plan. | Cancelled plans have no material requirement; check another plan. |
| `FV-MR-005` | 422 | BOM uses an unknown material | An active BOM item points at a material that is not in the material master, so readiness cannot be computed. | Fix the BOM or the material master; readiness is never counted as zero silently. |
| `FV-MR-006` | 404 | Reservation not found | The reservation id does not exist in this session. | List reservations with GET /api/materials/reservations. |
| `FV-MR-007` | 400 | Invalid reservation status filter | `GET /api/materials/reservations?status=` names a status other than Active or Released. | Use Active, Released, or leave the filter out. |
| `FV-MRP-001` | 400 | Invalid MRP horizon | The end of the MRP horizon is before its start. | Send horizon_end on or after horizon_start, or leave both out for today to +30 days. |
| `FV-MRP-002` | 400 | Invalid MRP date | horizon_start or horizon_end is not an ISO date. | Send dates as YYYY-MM-DD. |
| `FV-MRP-003` | 404 | MRP plan not found | A plan id in plan_ids does not exist in this session. | Use plan ids from GET /api/plans or OEM-YYYY-MM for the OEM monthly plan. |
| `FV-MRP-004` | 409 | MRP plan is cancelled | A plan id in plan_ids belongs to a cancelled plan. | Leave cancelled plans out of the run. |
| `FV-MRP-005` | 404 | MRP run not found | The MRP run id does not exist in this session. | List runs with GET /api/mrp/runs. |
| `FV-FCST-001` | 503 | Forecast model missing | The BQML model fv.demand_arima is not trained. | Run infra/bq_forecast.sql; the tool falls back to the local model meanwhile. |
| `FV-FC-001` | 400 | Invalid forecast parameters | Generate was sent a window, horizon, SKU filter or per-customer flag outside the allowed values. | Fix the fields listed in `detail.fields`: window 30/60/90 days, horizon 4/6/8 weeks, a non-empty SKU list or none. |
| `FV-FC-002` | 404 | Forecast snapshot not found | The forecast ID does not exist in this session. | Pick a snapshot from GET /api/forecasts. |
| `FV-FC-003` | 400 | Invalid snapshot status filter | `GET /api/forecasts?status=` names a status other than Generated or Superseded. | Use Generated, Superseded, or leave the filter out. |
| `FV-FC-004` | 409 | Forecast snapshot is read-only | A request tried to change or delete a stored forecast snapshot. | Generate a new snapshot; the old one is superseded and keeps its numbers. |
| `FV-FC-005` | 404 | SKU not in snapshot | The snapshot was generated for a SKU filter that leaves this SKU out. | Pick a SKU the snapshot covers. |
| `FV-ACT-001` | 404 | Action not found | The proposal ID does not exist in this session. | Refresh the Action Center. |
| `FV-ACT-002` | 409 | Action already decided | Approve or reject was sent for a proposal that is no longer pending. | No retry needed; the first decision stands. |
| `FV-ACT-003` | 500 | Action execution failed | The proposal was approved but its command failed in the simulator. | The action is marked failed with the reason; approve again after a reset. |
| `FV-ORD-001` | 409 | Order cannot be cancelled | The customer order is shipped, completed, already cancelled, or a work order for it is in production. | Leave the order as it is, or revise its ship date in Delivery. |
| `FV-TWIN-001` | 404 | Plant session not found | No simulator state exists for the session. | Call POST /api/demo/reset to create it. |
| `FV-TWIN-002` | 400 | Unknown command | An agent-commands message names an unknown command. | Check the command list in app/simulation/commands.py. |
| `FV-VIS-001` | 429 | Vision call limit reached | The session used all its Twin Vision Analyzer calls. | Cached observations stay visible; reset to start over. |
| `FV-VIS-002` | 429 | Camera debounced | A frame for the same camera arrived within the debounce window. | Wait; the browser should not send another frame until the window ends. |
| `FV-VIS-003` | 400 | Invalid frame | The frame is not a JPEG/PNG or exceeds the size limit. | Send a 1280x720 JPEG. |
| `FV-QC-001` | 400 | Invalid photo | The QC photo is missing, not an image, or too large. | Upload a JPEG or PNG under 8 MB. |
| `FV-QC-002` | 408 | Live inspection ended | The Gemini Live stream reached its 60-second limit. | Start a new stream with the button. |
| `FV-AI-001` | 503 | Model unavailable | Vertex AI returned an error or the model is not served in this location. | Retry later; check FV_MODEL_LOCATION. |
| `FV-AI-002` | 502 | Invalid model output | Gemini output did not match the expected JSON schema. | Retry once; the request is logged with the raw output. |
| `FV-AI-003` | 504 | Agent timeout | An agent run exceeded its time budget. | Retry or use Replay. |
| `FV-AGT-001` | 503 | Forecast feature unavailable | An agent asked for forecast vs actual or forecast accuracy and this build has no such function. | Answer from the forecast snapshot instead; the feature ships with the Forecast module. |
| `FV-AGT-002` | 409 | Re-plan not needed | An agent proposed a re-plan but the current plan already uses the latest forecast snapshot and customer PO demand. | No action; propose a re-plan only when check_replan_trigger reports a reason. |
| `FV-AGT-010` | 404 | Material not in MRP run | A draft PO was asked for a material that has no result row in the MRP run. | Pick a material from the run's results, or run MRP again. |
| `FV-AGT-011` | 409 | Nothing to order | A draft PO was asked for a material whose MRP net requirement is zero. | No PO is needed; the material is covered by stock and dated incoming. |
| `FV-AGT-012` | 422 | No default vendor | The material has no known default vendor, so a draft PO cannot be built. | Set the material's default vendor in the material master. |
| `FV-AGT-020` | 409 | Nothing to requisition | A requisition was proposed from an MRP run that has no material with a net requirement above zero. | No requisition is needed; run MRP again after the plans change. |
| `FV-AGT-021` | 422 | Vendor does not supply the material | A purchase order was proposed with a vendor that is not listed for the material. | Pick a vendor from recommend_vendor, or leave the vendor empty to use the recommended one. |
| `FV-AGT-022` | 422 | Quantity must be above zero | A purchase order was proposed with a quantity of zero or less. | Use the net quantity from the MRP run or the readiness shortage. |
| `FV-AGT-023` | 409 | PO cannot be expedited | An expedite was proposed for a PO that is not Issued or Partially Received, or for a seed PO. | Expedite only open session POs; list them with list_purchase_orders. |
| `FV-AGT-024` | 422 | Expedite date not earlier | The new promised date of an expedite is not earlier than the PO's current promised date. | Give a date before the current promised date. |
| `FV-PUB-001` | 400 | Invalid push message | A Pub/Sub push request is malformed. | Pub/Sub retries; check the publisher. |
| `FV-PUB-002` | 403 | Push not authorised | The push request's OIDC token is missing or invalid. | Check the subscription's service account. |
| `FV-SYS-001` | 500 | Internal error | Unexpected server error. | Report the `request_id` from the response headers. |
| `FV-MAT-001` | 404 | No active BOM | The product has no Active bill of materials in date, so its material need cannot be computed. | Activate a BOM for the SKU in master data; readiness is never computed as zero. |
| `FV-MAT-002` | 404 | Unknown material | The material id is not in the material master. | Use an id from GET /api/master/materials. |
| `FV-MAT-003` | 400 | Colour not made for this product | The colour is not one of the colours of the SKU's model. | Pick a colour from GET /api/master/colors for that SKU. |
| `FV-MAT-004` | 404 | Unknown warehouse | The warehouse id is not in the warehouse master. | Use WH-RM, WH-PNT or WH-CMP (GET /api/master/warehouses). |
| `FV-MAT-005` | 404 | Unknown BOM | The BOM id is not in master data. | Use a bom_id from GET /api/master/boms. |
| `FV-MAT-006` | 404 | Unknown product | The SKU is not in the product master. | Use a SKU from the product list. |
| `FV-MAT-007` | 500 | BOM uses an unknown material | An item of the active BOM points at a material that is not in the material master. | Fix the BOM item in master data; the SKU and material are in the detail. |
| `FV-MAT-008` | 400 | Unknown material group | The material group filter is not one of the material groups. | Use Resin, Masterbatch, Primer, Basecoat, Clear, Hardener, Thinner, Component or Packaging. |
| `FV-MAT-009` | 400 | Unknown BOM status | The BOM status filter is not Draft, Active or Inactive. | Use Draft, Active or Inactive. |
| `FV-MD-001` | 400 | Invalid master data | A created or edited master data row has a missing key, a value of the wrong type, a negative number or an unknown reference. | Fix the fields listed in `detail.fields`. |
| `FV-MD-002` | 409 | Duplicate key | A master data row with this key already exists. | Use another key, or edit the existing row. |
| `FV-MD-003` | 404 | Unknown master data row | No row with this key exists in this master data. | Reload the list; the row may have been deleted. |
| `FV-MD-004` | 409 | Row still referenced | The row is used by other master data (for example a material in a BOM), so deleting it would leave a broken reference. | Change or delete the rows listed in `detail.references` first. |
| `FV-MD-005` | 400 | Read-only master data | The master data is derived from other master data (product routings) and cannot be edited directly. | Edit the source it is derived from instead. |
| `FV-PUR-001` | 404 | Unknown vendor | The vendor id is not in the vendor master. | Use a vendor_id from GET /api/purchasing/vendors. |
| `FV-PUR-002` | 404 | No vendor for material | No vendor in the vendor master supplies this material. | Add the material to a vendor before buying it. |
| `FV-PUR-003` | 400 | No lines | A requisition, purchase order or goods receipt has no lines. | Send at least one line with a material and a quantity. |
| `FV-PUR-004` | 400 | Quantity must be positive | A line quantity is zero or negative. | Send a quantity greater than zero. |
| `FV-PUR-005` | 409 | Action not allowed in this status | The requisition or purchase order is in a status that does not allow this action. | Check `detail.status` and `detail.allowed_from`; refresh the document and retry. |
| `FV-PUR-006` | 404 | Requisition not found | The purchase requisition id does not exist in this session. | List requisitions with GET /api/purchasing/requisitions. |
| `FV-PUR-007` | 400 | Vendor does not supply material | The chosen vendor is not a supplier of this material. | Pick one of the vendors in `detail.valid` (GET /api/purchasing/recommend lists them). |
| `FV-PUR-008` | 400 | Expedite date not earlier | The new promised date is not earlier than the current promised date. | Send a date before the current one. |
| `FV-PUR-009` | 400 | Receipt over tolerance | A received quantity is more than the open quantity plus the 5% tolerance. | Receive at most `detail.max_qty`; raise a new PO for the rest. |
| `FV-PUR-010` | 400 | Invalid date | A date field is not an ISO date. | Send dates as YYYY-MM-DD. |
| `FV-PUR-011` | 400 | Reason required | A reject or expedite was sent without a reason. | Give a reason of at least 3 characters. |
| `FV-PUR-012` | 404 | Purchase order not found | The PO id is neither a session PO nor a seed PO. | List purchase orders with GET /api/purchasing/orders. |
| `FV-PUR-013` | 409 | Seed PO is read-only | The action edits, issues, closes, cancels or expedites a seed PO from the warehouse. | Only receiving is allowed on seed POs; raise a session PO instead. |
| `FV-PUR-014` | 400 | Unknown action | The document action is not one of the actions listed in `detail.valid`. | Use one of `detail.valid`. |
| `FV-PUR-015` | 400 | Unknown status | The status filter is not a requisition or PO status. | Use one of `detail.valid`. |
| `FV-PUR-016` | 400 | Material not on PO | A goods-receipt line names a material that is not on the purchase order. | Receive only the PO's materials. |
