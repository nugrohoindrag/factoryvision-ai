# End-to-end tests (top of the pyramid)

Opt-in: they need a running API (and, for agent runs, Vertex AI credentials).

```bash
FV_E2E_BASE_URL=http://localhost:8080 pytest -m e2e
```

`test_api_smoke.py` runs the judges' Replay path (no Gemini). `test_agents_live.py` is the agent eval: one live
Disruption Response that must find the planted stories; add `FV_E2E_LIVE=1` (billed). Browser
flows (the 8-step demo) live in `frontend/e2e/` and run with Playwright.
