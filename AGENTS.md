# AGENTS.md

Rules for an agent working in this repository. Each one exists because something
went wrong without it. If you find a rule that is not explained here, that is a gap
worth fixing.

## What this is

A FastAPI backend serving deterministic questions about a synthetic factory
dataset, plus a React frontend. Data layer: SQLAlchemy over BigQuery in the cloud
and DuckDB locally, same SQL both ways. Seven planted data stories in
`evals/ground_truth.json` are the point of the exercise.

**Not built: the agents, the MCP transport, the forecast backend, the BigQuery
path.** They are absent, not broken. `README.md` lists the gaps. If you find
yourself importing one, you are writing a feature, not a fix.

## Start here

```bash
just bootstrap     # uv sync backend, npm ci frontend
just seed          # 90 days of seed CSVs -> data/seed
just db-build      # migrate + load -> data/local/fv.duckdb
just verify        # the full local gate surface
```

Python 3.12, Node 24. Use `just`, not `uv run` directly — the recipes export
`UV_PROJECT_ENVIRONMENT` and `COVERAGE_RCFILE`, and a bare `uv run` builds a second
virtualenv under `backend/`.

## The gates

Run these before claiming anything works. Paste the output, do not assert it.

```bash
just lint        # ruff check + ruff format --check
just typecheck   # mypy (backend) + tsc (frontend)
just test        # 45 unit + 20 integration
```

**Why each one exists:**

| Gate | What it caught when it was first switched on |
|---|---|
| `ruff` | 126 findings, mostly missing annotations and import order |
| `mypy` | `Duplicate module named "conftest"` — several conftest files, no `__init__.py` |
| unit tier | an `os.environ.get` bypass that no type checker can see |
| integration tier | a 404 that reported `Unknown module` for every path |
| catalog drift | `docs/error-catalog.md` is generated; it silently went stale |
| `.env.example` drift | three env vars had been renamed by an `env_prefix` change |
| `justfile` ↔ `ci.yml` drift | CI ran pytest from the wrong directory, so `-m unit` selected nothing while the job stayed green |

A gate that has never been seen to reject is not known to work. Several of these
passed while the defect they targeted was live, because nothing had tried to trip
them. If you add a gate, break the thing it guards, confirm it goes red, restore
it, and say so in the PR.

## Test tiers

Two tiers and the tier is part of the contract:

- **`unit`** (`-m unit`, 45 tests) — pure logic. No I/O, no warehouse, no network.
- **`integration`** (`-m integration`, 20 tests) — real DuckDB built through the
  migrations, no network.

A test with no marker runs in **neither** tier. Locally you might run it by
accident and believe it is covered; CI selects by marker and will never see it.

`backend/tests/e2e/test_api_smoke.py` is deliberately outside both, under
Playwright.

Unit tests depend on `data/local/fv.duckdb` being absent, not present. That is how
"this test must not touch the warehouse" is enforced: if the database is missing,
a unit test that reaches for it fails.

## Permissions you will hit

`opencode.json` and `.claude/settings.json` deny paths by default. This is not
obstructive — each one protects something specific.

| Denied | Why |
|---|---|
| `pyproject.toml`, `justfile` | they define the gates; editing them changes what "passing" means without changing any code |
| `backend/tests/**` (ask) | the cheapest defence against an agent rewriting the suite it is being measured against |
| `backend/migrations/**` | schema changes need a human; the migrations are the contract with production |
| `Read(**/.env)` | secrets. `.env.example` is the readable copy and is explicitly allowed |
| `Bash(git push:*)`, `git reset --hard`, `git clean`, `gh:*` | you should not be publishing or destroying work |
| `Bash(rm:*)` | no unbounded deletion, even in a worktree |
| `Bash(pip install:*)`, `npm install` | dependencies are declared in `backend/pyproject.toml` and `frontend/package.json`. Ad-hoc installs make the lockfile lie |

**If you need one of these, say so in the PR and explain the case.** Do not work
around it. Do not edit the permission files either — that is denied too, which is
deliberate: the wall has no self-service door.

The bypass guard in `.opencode/plugins/` blocks `--no-verify`, force push,
`reset --hard`, dependency installs and similar by pattern match. **It is not a
security boundary** — it raises the cost of a reflexive bypass, and its table is
tested but its runtime enforcement is not. `CODEOWNERS` plus required status checks
is the real boundary.

## Two command surfaces

`justfile` and `.github/workflows/ci.yml` both describe how to run the backend, and
they were written in different commits — so they drifted, and the drift was not
caught by anything until a review happened to compare them. `test_ci_surface.py` now
does that comparison. It asserts that every `ruff`, `mypy` and `pytest` command CI
runs is also reachable from a recipe, that pytest in CI runs with `backend/` as the
working directory, and that CI exports `UV_PROJECT_ENVIRONMENT` as an absolute path.

That last one is not a formality: uv resolves a relative `UV_PROJECT_ENVIRONMENT`
against the *project* directory, so `.venv` builds a second, stale `backend/.venv`.

**If you add a check to one surface, add it to the other.** The gate tells you when
you forget, but it will not tell you why you should have.

## Traps

- **`docs/error-catalog.md` is generated.** Edit `backend/app/errors.py`, then run
  `just catalog`. CI runs this as a diff gate.
- **No docstrings in `backend/app/` or `backend/tests/`.** The code is the
  reference. This was not aesthetic: the ATP supply formula lived only in a
  docstring, and reformatting it would have silently changed an API response.
  Two survive outside that scope and are deliberate: `migrations/env.py`, and the
  revision header Alembic generates into `migrations/versions/`.
- **`.env.example` is compared against `Settings.model_fields` in both
  directions, values included.** Adding a setting means adding the row.
- **`FV_MODEL_LOCATION` reads `GOOGLE_CLOUD_LOCATION` as a fallback** and is
  deliberately not renamed — the Google client libraries read that name themselves.
- **`app/**` imports from `simulator/` in eight places.** Wrong direction, but
  pre-existing. Do not add a ninth.
- **The SQL guardrail parses, it does not pattern-match.** `check_sql` builds a
  sqlglot AST and inspects it: the root must be a single `SELECT`, no write node may
  appear anywhere, and every referenced table must be allowlisted. Table functions
  (`read_csv`, `read_parquet`, `read_text`) are rejected explicitly — an earlier
  regex could not see them, because it only extracted `fv.<name>`. If you add a
  query shape, check what the AST calls it before assuming it is a table.

## When a gate is red

Fix the code. Not the gate.

If a gate is wrong, that is a real finding and it belongs in the PR under
"Deliberate deviations", with the reason. Silencing a linter to make a run green is
the single thing this repository exists to prevent, and there is a documented
precedent here: a test that asserted a live 404 bug was updated rather than
reverted, because reverting the fix to keep a test green is worse than a red test.

See `CONTRIBUTING.md` for branch naming and labels, and
`.github/pull_request_template.md` for what a PR has to contain.