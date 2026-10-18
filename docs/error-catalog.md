# Error catalog

Generated from `backend/app/errors.py` (`python -m app.errors`). Do not edit by hand.

Every error response has the body `{"error": {"code", "title", "message", "detail"}}`.
Agent tools return the same object instead of raising, so the agent can explain the failure.

| Code | HTTP | Title | When | What to do |
|---|---|---|---|---|
| `FV-REQ-001` | 400 | Invalid request | The body or query parameters fail validation. | Fix the request; `detail` lists the invalid fields. |
| `FV-REQ-002` | 404 | Unknown module | `/api/agents/{module}` names a module that does not exist. | Use one of forecast, planning, mes, material, purchasing, quality, delivery. |
| `FV-REQ-003` | 404 | Unknown scenario | A disruption or replay scenario is not defined. | Use a scenario listed by the simulator. |
| `FV-REQ-004` | 404 | Resource not found | The request targets a path that does not exist. | Check the URL; `/api/health` is the only endpoint today. |
| `FV-AUTH-001` | 401 | Not signed in | Auth is required and the Firebase ID token is missing or invalid. | Sign in with the demo account and retry. |
| `FV-SES-001` | 400 | Missing session | The `X-Session-Id` header is missing or malformed. | The web app creates one per tab; API clients must send a UUID. |
| `FV-RATE-001` | 429 | Live run limit reached | The session used all its live AI runs. | Switch to Replay or reset the demo session. |
| `FV-RATE-002` | 429 | Agent question limit reached | The session asked agents more often than allowed. | Wait or use Replay. |
| `FV-DATA-001` | 404 | Unknown entity | A SKU, machine, material, vendor, lot or order ID does not exist. | Check the ID against the master data. |
| `FV-DATA-002` | 503 | Warehouse unavailable | BigQuery or the local DuckDB file cannot be reached. | Local: run `python -m app.db.build_local`. Cloud: check the dataset and IAM. |
| `FV-SQL-001` | 403 | Query rejected | An agent SQL query is not a single read-only SELECT. | Guardrail; agents may only read. |
| `FV-SQL-002` | 403 | Table not allowlisted | An agent SQL query reads a table outside the allowlist. | Guardrail; extend the allowlist only through a code change. |
| `FV-PLAN-001` | 422 | No feasible plan | CP-SAT found no feasible molding plan within the time limit. | Relax the scenario (allow mold moves, extra shifts) and retry. |
| `FV-FCST-001` | 503 | Forecast model missing | The BQML model fv.demand_arima is not trained. | Run infra/bq_forecast.sql; the tool falls back to the local model meanwhile. |
| `FV-ACT-001` | 404 | Action not found | The proposal ID does not exist in this session. | Refresh the Action Center. |
| `FV-ACT-002` | 409 | Action already decided | Approve or reject was sent for a proposal that is no longer pending. | No retry needed; the first decision stands. |
| `FV-ACT-003` | 500 | Action execution failed | The proposal was approved but its command failed in the simulator. | The action is marked failed with the reason; approve again after a reset. |
| `FV-TWIN-001` | 404 | Plant session not found | No simulator state exists for the session. | Call POST /api/demo/reset to create it. |
| `FV-TWIN-002` | 400 | Unknown command | An agent-commands message names an unknown command. | Check the command list in simulator/commands.py. |
| `FV-VIS-001` | 429 | Vision call limit reached | The session used all its Twin Vision Analyzer calls. | Cached observations stay visible; reset to start over. |
| `FV-VIS-002` | 429 | Camera debounced | A frame for the same camera arrived within the debounce window. | Wait; the browser should not send another frame until the window ends. |
| `FV-VIS-003` | 400 | Invalid frame | The frame is not a JPEG/PNG or exceeds the size limit. | Send a 1280x720 JPEG. |
| `FV-QC-001` | 400 | Invalid photo | The QC photo is missing, not an image, or too large. | Upload a JPEG or PNG under 8 MB. |
| `FV-QC-002` | 408 | Live inspection ended | The Gemini Live stream reached its 60-second limit. | Start a new stream with the button. |
| `FV-AI-001` | 503 | Model unavailable | Vertex AI returned an error or the model is not served in this location. | Retry later; check FV_MODEL_LOCATION. |
| `FV-AI-002` | 502 | Invalid model output | Gemini output did not match the expected JSON schema. | Retry once; the request is logged with the raw output. |
| `FV-AI-003` | 504 | Agent timeout | An agent run exceeded its time budget. | Retry or use Replay. |
| `FV-PUB-001` | 400 | Invalid push message | A Pub/Sub push request is malformed. | Pub/Sub retries; check the publisher. |
| `FV-PUB-002` | 403 | Push not authorised | The push request's OIDC token is missing or invalid. | Check the subscription's service account. |
| `FV-SYS-001` | 500 | Internal error | Unexpected server error. | Report the `request_id` from the response headers. |
