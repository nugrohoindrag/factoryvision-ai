# End-to-end tests (top of the pyramid)

Opt-in: they need a running API (and, for agent runs, Vertex AI credentials).

```bash
FV_E2E_BASE_URL=http://localhost:8080 pytest -m e2e
```

API flows land here as the endpoints are built (twin tick, Disruption Response, approve). Browser
flows (the 8-step demo) live in `frontend/e2e/` and run with Playwright.
