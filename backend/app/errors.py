from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    http_status: int
    title: str
    when: str
    action: str


_SPECS = [
    ErrorSpec("FV-REQ-001", 400, "Invalid request", "The body or query parameters fail validation.", "Fix the request; `detail` lists the invalid fields."),
    ErrorSpec(
        "FV-REQ-002",
        404,
        "Unknown module",
        "`/api/agents/{module}` names a module that does not exist.",
        "Use one of forecast, planning, mes, material, purchasing, quality, delivery.",
    ),
    ErrorSpec("FV-REQ-003", 404, "Unknown scenario", "A disruption or replay scenario is not defined.", "Use a scenario listed by the simulator."),
    ErrorSpec(
        "FV-REQ-004", 404, "Resource not found", "The request targets a path that does not exist.", "Check the URL against the API routes (`/docs` lists them)."
    ),
    ErrorSpec(
        "FV-AUTH-001", 401, "Not signed in", "Auth is required and the Firebase ID token is missing or invalid.", "Sign in with the demo account and retry."
    ),
    ErrorSpec(
        "FV-SES-001",
        400,
        "Missing session",
        "The `X-Session-Id` header is missing or malformed.",
        "The web app creates one per tab; API clients must send a UUID.",
    ),
    ErrorSpec("FV-RATE-001", 429, "Live run limit reached", "The session used all its live AI runs.", "Switch to Replay; a reset does not refill live runs."),
    ErrorSpec("FV-RATE-002", 429, "Agent question limit reached", "The session asked agents more often than allowed.", "Wait or use Replay."),
    ErrorSpec(
        "FV-DATA-001", 404, "Unknown entity", "A SKU, machine, material, vendor, lot or order ID does not exist.", "Check the ID against the master data."
    ),
    ErrorSpec(
        "FV-DATA-002",
        503,
        "Warehouse unavailable",
        "BigQuery or the local DuckDB file cannot be reached.",
        "Local: run `python -m app.db.build_local`. Cloud: check the dataset and IAM.",
    ),
    ErrorSpec("FV-SQL-001", 403, "Query rejected", "An agent SQL query is not a single read-only SELECT.", "Guardrail; agents may only read."),
    ErrorSpec(
        "FV-SQL-002",
        403,
        "Table not allowlisted",
        "An agent SQL query reads a table outside the allowlist.",
        "Guardrail; extend the allowlist only through a code change.",
    ),
    ErrorSpec(
        "FV-PLAN-001",
        422,
        "No feasible plan",
        "CP-SAT found no feasible molding plan within the time limit.",
        "Relax the scenario (allow mold moves, extra shifts) and retry.",
    ),
    ErrorSpec(
        "FV-PP-001",
        404,
        "Production plan not found",
        "The plan ID does not exist in this session.",
        "Refresh the plan list; a reset clears the session's plans.",
    ),
    ErrorSpec(
        "FV-PP-002",
        400,
        "Invalid plan period",
        "The period start or end is missing, not a date, or the end is before the start.",
        "Send ISO dates with period_end on or after period_start.",
    ),
    ErrorSpec(
        "FV-PP-003",
        409,
        "Production plan locked",
        "The plan is Confirmed, In Execution, Completed or Cancelled; a confirmed plan is a production commitment.",
        "Leave it as it is, or create a new plan for the change.",
    ),
    ErrorSpec(
        "FV-PP-004",
        404,
        "Order line not found",
        "The customer order line named for the plan demand does not exist.",
        "Pick a line from GET /api/plans/{plan_id}/demand-candidates.",
    ),
    ErrorSpec(
        "FV-PP-005",
        409,
        "Cancelled order",
        "A line of a cancelled customer order was added to a plan.",
        "Cancelled orders cannot enter a production plan.",
    ),
    ErrorSpec(
        "FV-PP-006",
        400,
        "Invalid demand quantity",
        "The demand quantity is zero or negative.",
        "Send a quantity above zero, or leave it out to take the remaining quantity.",
    ),
    ErrorSpec(
        "FV-PP-007",
        409,
        "Order line already fully planned",
        "Every unit of the order line is already in a production plan.",
        "Nothing is left to plan; release demand from another plan first.",
    ),
    ErrorSpec(
        "FV-PP-008",
        400,
        "Demand exceeds remaining quantity",
        "The demand is more than ordered minus already planned on the order line.",
        "Lower the quantity to the remaining quantity.",
    ),
    ErrorSpec(
        "FV-PP-009",
        409,
        "Order line already in the plan",
        "The same customer order line was added to one plan twice.",
        "Change the plan line's planned quantity instead.",
    ),
    ErrorSpec(
        "FV-PP-010",
        404,
        "Plan demand not found",
        "The order line to release is not demand of this plan.",
        "Refresh the plan's demand list.",
    ),
    ErrorSpec(
        "FV-PP-011",
        404,
        "Plan line not found",
        "The plan line ID does not exist on this plan.",
        "Refresh the plan.",
    ),
    ErrorSpec(
        "FV-PP-012",
        400,
        "Invalid plan line values",
        "The planned quantity is negative or the priority is outside 1 to 5.",
        "Fix the fields listed in `detail`.",
    ),
    ErrorSpec(
        "FV-PP-013",
        409,
        "Wizard step locked",
        "A later wizard step was opened before its prerequisite exists.",
        "Complete the step named in the message; going back is always allowed.",
    ),
    ErrorSpec(
        "FV-PP-014",
        400,
        "Invalid wizard step",
        "The wizard step is not 1 to 6.",
        "Send a step from 1 (Demand) to 6 (Confirmation).",
    ),
    ErrorSpec(
        "FV-PP-015",
        409,
        "Nothing to generate",
        "The plan has no plan lines, or no line has a planned quantity above zero.",
        "Add demand and set planned quantities before generating work orders.",
    ),
    ErrorSpec(
        "FV-PP-016",
        422,
        "Invalid routing",
        "A product routing failed validation, so no work order was created at all.",
        "Fix the routing listed in `detail`, then generate again.",
    ),
    ErrorSpec(
        "FV-PP-017",
        404,
        "Work order not found",
        "The work order ID does not belong to this plan.",
        "Refresh the plan's work orders.",
    ),
    ErrorSpec(
        "FV-PP-018",
        400,
        "Invalid work order resource or schedule",
        "The machine or mold cannot run the product, or the schedule fields are invalid.",
        "Choose a compatible machine and mold; planned end must follow planned start.",
    ),
    ErrorSpec(
        "FV-PP-019",
        409,
        "Work order transition not allowed",
        "The work order status change skips back, or the plan is not confirmed yet.",
        "Move work orders forward only, after the plan is confirmed.",
    ),
    ErrorSpec(
        "FV-PP-020",
        409,
        "Work orders not ready to confirm",
        "A work order lacks quantity, schedule, machine, mold or shift, or overlaps another on its machine.",
        "Complete the checklist in `detail`; no work order was confirmed.",
    ),
    ErrorSpec(
        "FV-PP-021",
        409,
        "Plan has no lines",
        "Confirmation was asked for a plan without plan lines.",
        "Add demand first.",
    ),
    ErrorSpec(
        "FV-PP-022",
        409,
        "Capacity Up Required",
        "A plan line's planned quantity is above total capacity.",
        "Request more capacity or lower the planned quantity of the SKUs named.",
    ),
    ErrorSpec(
        "FV-PP-023",
        409,
        "No work orders",
        "The plan has no generated work orders yet.",
        "Generate work orders in Step 3.",
    ),
    ErrorSpec(
        "FV-PP-024",
        409,
        "Work orders not confirmed",
        "Some of the plan's work orders are not confirmed.",
        "Confirm every work order before confirming the plan.",
    ),
    ErrorSpec(
        "FV-PP-025",
        409,
        "Plan cannot be confirmed",
        "The plan is already confirmed, or it is cancelled or completed.",
        "No retry needed.",
    ),
    ErrorSpec(
        "FV-PP-026",
        400,
        "Cancellation reason required",
        "The cancel request has no reason of at least 3 characters.",
        "Give the reason.",
    ),
    ErrorSpec(
        "FV-PP-027",
        409,
        "Plan cannot be cancelled",
        "The plan is already cancelled, or some of its work orders are in production or completed.",
        "Leave the plan; stop the running work orders first.",
    ),
    ErrorSpec(
        "FV-PP-028",
        422,
        "No feasible schedule",
        "CP-SAT found no schedule for the plan's work orders within the time limit.",
        "Lower planned quantities or widen the period, then schedule again.",
    ),
    ErrorSpec(
        "FV-PP-029",
        400,
        "Invalid forecast demand",
        "The SKU is unknown, no forecast snapshot exists, or the quantity is above the unordered forecast.",
        "Use a SKU in the snapshot and at most the unordered forecast quantity.",
    ),
    ErrorSpec(
        "FV-CAP-001",
        400,
        "Invalid capacity period",
        "The capacity period start or end is not a date, or the end is before the start.",
        "Send ISO dates with period_end on or after period_start.",
    ),
    ErrorSpec(
        "FV-CAP-002",
        404,
        "Capacity snapshot not found",
        "The CAP snapshot to recalculate does not exist in this session.",
        "List snapshots with GET /api/capacity/snapshots.",
    ),
    ErrorSpec(
        "FV-CAP-003",
        400,
        "Invalid planning utilization",
        "The planning utilization is not above 0 and at most 100 percent.",
        "Send a percentage in (0, 100].",
    ),
    ErrorSpec(
        "FV-FCST-001",
        503,
        "Forecast model missing",
        "The BQML model fv.demand_arima is not trained.",
        "Run infra/bq_forecast.sql; the tool falls back to the local model meanwhile.",
    ),
    ErrorSpec("FV-ACT-001", 404, "Action not found", "The proposal ID does not exist in this session.", "Refresh the Action Center."),
    ErrorSpec(
        "FV-ACT-002",
        409,
        "Action already decided",
        "Approve or reject was sent for a proposal that is no longer pending.",
        "No retry needed; the first decision stands.",
    ),
    ErrorSpec(
        "FV-ACT-003",
        500,
        "Action execution failed",
        "The proposal was approved but its command failed in the simulator.",
        "The action is marked failed with the reason; approve again after a reset.",
    ),
    ErrorSpec(
        "FV-ORD-001",
        409,
        "Order cannot be cancelled",
        "The customer order is shipped, completed, already cancelled, or a work order for it is in production.",
        "Leave the order as it is, or revise its ship date in Delivery.",
    ),
    ErrorSpec("FV-TWIN-001", 404, "Plant session not found", "No simulator state exists for the session.", "Call POST /api/demo/reset to create it."),
    ErrorSpec(
        "FV-TWIN-002", 400, "Unknown command", "An agent-commands message names an unknown command.", "Check the command list in app/simulation/commands.py."
    ),
    ErrorSpec(
        "FV-VIS-001",
        429,
        "Vision call limit reached",
        "The session used all its Twin Vision Analyzer calls.",
        "Cached observations stay visible; reset to start over.",
    ),
    ErrorSpec(
        "FV-VIS-002",
        429,
        "Camera debounced",
        "A frame for the same camera arrived within the debounce window.",
        "Wait; the browser should not send another frame until the window ends.",
    ),
    ErrorSpec("FV-VIS-003", 400, "Invalid frame", "The frame is not a JPEG/PNG or exceeds the size limit.", "Send a 1280x720 JPEG."),
    ErrorSpec("FV-QC-001", 400, "Invalid photo", "The QC photo is missing, not an image, or too large.", "Upload a JPEG or PNG under 8 MB."),
    ErrorSpec("FV-QC-002", 408, "Live inspection ended", "The Gemini Live stream reached its 60-second limit.", "Start a new stream with the button."),
    ErrorSpec(
        "FV-AI-001",
        503,
        "Model unavailable",
        "Vertex AI returned an error or the model is not served in this location.",
        "Retry later; check FV_MODEL_LOCATION.",
    ),
    ErrorSpec(
        "FV-AI-002",
        502,
        "Invalid model output",
        "Gemini output did not match the expected JSON schema.",
        "Retry once; the request is logged with the raw output.",
    ),
    ErrorSpec("FV-AI-003", 504, "Agent timeout", "An agent run exceeded its time budget.", "Retry or use Replay."),
    ErrorSpec("FV-PUB-001", 400, "Invalid push message", "A Pub/Sub push request is malformed.", "Pub/Sub retries; check the publisher."),
    ErrorSpec("FV-PUB-002", 403, "Push not authorised", "The push request's OIDC token is missing or invalid.", "Check the subscription's service account."),
    ErrorSpec("FV-SYS-001", 500, "Internal error", "Unexpected server error.", "Report the `request_id` from the response headers."),
]
CATALOG: dict[str, ErrorSpec] = {s.code: s for s in _SPECS}


class FVError(Exception):
    def __init__(self, code: str, message: str | None = None, detail: object = None) -> None:
        if code not in CATALOG:
            msg = f"error code {code} is not in the catalog"
            raise KeyError(msg)
        self.spec = CATALOG[code]
        self.code = code
        self.message = message or self.spec.title
        self.detail = detail
        super().__init__(f"{code}: {self.message}")

    @property
    def http_status(self) -> int:
        return self.spec.http_status

    def body(self) -> dict[str, dict[str, object]]:
        return {"error": {"code": self.code, "title": self.spec.title, "message": self.message, "detail": self.detail}}

    def tool_result(self) -> dict[str, dict[str, object]]:
        return self.body()


def render_markdown() -> str:
    lines = [
        "# Error catalog",
        "",
        "Generated from `backend/app/errors.py` (`python -m app.errors`). Do not edit by hand.",
        "",
        'Every error response has the body `{"error": {"code", "title", "message", "detail"}}`.',
        "Agent tools return the same object instead of raising, so the agent can explain the failure.",
        "",
        "| Code | HTTP | Title | When | What to do |",
        "|---|---|---|---|---|",
    ]
    lines.extend(f"| `{s.code}` | {s.http_status} | {s.title} | {s.when} | {s.action} |" for s in _SPECS)
    return "\n".join(lines) + "\n"


DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "error-catalog.md"

if __name__ == "__main__":
    DOC_PATH.write_text(render_markdown(), encoding="utf-8")
    sys.stdout.write(f"wrote {DOC_PATH}\n")
