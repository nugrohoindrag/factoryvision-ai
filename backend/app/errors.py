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
        "FV-FCST-001",
        503,
        "Forecast model missing",
        "The BQML model fv.demand_arima is not trained.",
        "Run infra/bq_forecast.sql; the tool falls back to the local model meanwhile.",
    ),
    ErrorSpec(
        "FV-FC-001",
        400,
        "Invalid forecast parameters",
        "Generate was sent a window, horizon, SKU filter or per-customer flag outside the allowed values.",
        "Fix the fields listed in `detail.fields`: window 30/60/90 days, horizon 4/6/8 weeks, a non-empty SKU list or none.",
    ),
    ErrorSpec("FV-FC-002", 404, "Forecast snapshot not found", "The forecast ID does not exist in this session.", "Pick a snapshot from GET /api/forecasts."),
    ErrorSpec(
        "FV-FC-003",
        400,
        "Invalid snapshot status filter",
        "`GET /api/forecasts?status=` names a status other than Generated or Superseded.",
        "Use Generated, Superseded, or leave the filter out.",
    ),
    ErrorSpec(
        "FV-FC-004",
        409,
        "Forecast snapshot is read-only",
        "A request tried to change or delete a stored forecast snapshot.",
        "Generate a new snapshot; the old one is superseded and keeps its numbers.",
    ),
    ErrorSpec(
        "FV-FC-005", 404, "SKU not in snapshot", "The snapshot was generated for a SKU filter that leaves this SKU out.", "Pick a SKU the snapshot covers."
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
