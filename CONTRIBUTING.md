# Contributing

The short version: `just bootstrap`, `just seed`, `just db-build`, then edit, then
`just verify`. If a gate is red, fix the code — not the gate.

## Getting there

```bash
just bootstrap     # uv sync the backend, npm ci the frontend
just seed          # 90 days of seed CSVs -> data/seed
just db-build      # Alembic migrate + load -> data/local/fv.duckdb
just serve         # http://localhost:8080
just serve-frontend
```

Python 3.12, Node 24, `just`, and `uv` are the only prerequisites. There is one
virtualenv, at the repository root; the justfile exports `UV_PROJECT_ENVIRONMENT` so
every `uv` invocation uses it. Running `uv run` by hand instead of through `just`
will quietly build a second one under `backend/`.

## Branch naming

| Prefix | For |
|---|---|
| `feat/` | new behaviour |
| `fix/` | a defect, with or without an issue number |
| `gate/` | changes a lint rule, type setting, test tier, or CI job |
| `agent/` | changes `opencode.json`, `.claude/settings.json`, or the bypass guard |
| `docs/` | no behaviour change |
| `chore/` | dependencies, tooling, housekeeping |

Examples: `feat/mcp-transport`, `fix/warehouse-empty-result`, `gate/test-tier-contract`.

Branch off `main`, not off another feature branch. CI runs on the merge result, not
on intermediate commits, so a red intermediate commit is fine — but a red tip is not.

## Before you push

```bash
just lint        # ruff check + ruff format --check
just typecheck   # mypy over the backend, tsc for the frontend
just test        # 45 unit, 20 integration
```

Paste the actual output in the PR. A claim that a gate passes is not evidence that
it passed; the output is.

`just verify` adds the build and the coverage run, plus the frontend suite. It does
**not** run the Playwright smoke test or the error-catalog drift check — those exist
only in CI, so a green `just verify` on a stale `docs/error-catalog.md` still means
you need to run `just catalog`.

## Labels

| Label | Means |
|---|---|
| `Bug` | documented behaviour is not what happens |
| `Enhancement` | does not exist yet |
| `Gate` | touches a lint rule, type setting, test tier, or CI job |
| `Agent permissions` | touches `opencode.json`, `.claude/settings.json`, or the guard |
| `Docs only` | no behaviour change |
| `Blocking` | something else cannot proceed until this lands |
| `Deferred` | deliberately not done now, reason on the PR |

`Gate` and `Agent permissions` exist because those diffs are cheap to review badly.
A rule that silently got weaker looks identical to a rule that got stronger unless
somebody is looking for it.

## Tests

Two tiers, and the tier is part of the contract:

- **`unit`** — pure logic, no I/O, no warehouse, no network. 45 tests.
- **`integration`** — real DuckDB built through the migrations, no network. 20 tests.

If you add a test, it is either one or the other. There is no third option, and a
test without a marker runs in neither tier — which is the same as not running it.
CI selects by marker, so an unmarked test is invisible there even though
`pytest` alone would run it.

`tests/e2e/test_api_smoke.py` deliberately belongs to neither tier; it runs under
Playwright.

## Adding a gate

Adding a lint rule, a type strictness setting, or a test that guards a rule is
welcome. Two things make it hold up:

1. **Prove it fires.** Break the thing it guards, confirm it goes red, restore it.
   A gate that has never been seen to reject is not known to work. Several gates in
   this repo passed while the defect they targeted was live, because nothing had
   tried to trip them.
2. **Record the decision**, not just the setting. `backend/pyproject.toml` and
   `justfile` are `deny` in `opencode.json` precisely because they define the gates;
   a rule that is removed quietly looks exactly like a rule that was never added.

## What is not built yet

The agents, the MCP transport, the forecast backend and the BigQuery path do not
exist. `README.md` has the full list. If you find yourself adding an import for one
of them, stop — that is a feature request, not a bug fix.