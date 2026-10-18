# backend/AGENTS.md

Nested from the root `AGENTS.md`. That file wins if anything here contradicts it.

## Layout

```
app/        FastAPI service, the pydantic Settings, the SQL guardrail
simulator/  Seed-data generation; numpy, so S311 is exempt here
tests/unit/ pure logic, no I/O
tests/integration/ real DuckDB through the migrations
tests/e2e/  Playwright, belongs to neither tier
migrations/ Alembic
```

`app/**` imports from `simulator/` in eight places. That direction is wrong and
pre-existing. Do not add a ninth, and do not fix it as a side effect — it is its
own change with its own review.

## Rules specific to this directory

- **No docstrings in `app/` or `tests/`.** The code is the reference. The short
  version: the ATP supply formula lived only in a docstring, and reformatting it
  would have silently changed an API response. `migrations/env.py` keeps one and
  `migrations/versions/` keeps Alembic's generated revision header — both
  deliberate, both outside that rule.
- **`pyproject.toml` here holds only `[tool.pytest.ini_options]`.** Ruff, mypy and
  coverage live in the root `pyproject.toml`, because those three resolve config by
  walking up from the working directory while pytest does not. A test enforces this:
  adding a second `[tool.*]` table here fails `test_backend_pyproject_has_no_tool_tables`.
- **`pytest` must run with `backend/` as the working directory.** That is where the
  only `[tool.pytest.ini_options]` lives. CI passes `working-directory: backend` for
  this reason, and getting it wrong is not a warning — `pythonpath` silently stops
  applying and `-m unit` stops filtering.
- **`S311` (`random`) is exempted for `simulator/` only.** Every alternative was
  evaluated and rejected: replacing the RNGs broke 2 of 7 story tests, and
  regenerating `ground_truth.json` would have changed the numbers that job exists to
  verify.
- **The SQL guardrail is a regex.** It rejects statement shapes it does not
  recognise. It does not parse, and it will not stop a CTE. Do not describe it as
  protection you can rely on for query semantics.

## Data access

All domain reads go through `warehouse.query()`, which takes a single allowlisted
`SELECT` with a `LIMIT`. There is no `raw()` — it was deleted, and `bqml_forecast`
now owns the one statement that could not go through it. If you need a new statement
shape, add it to the allowlist explicitly rather than reintroducing a general escape
hatch.

## Configuration

`Settings` and `.env.example` are compared in both directions, values included.
Adding a field means adding the row, or the suite goes red. `FV_MODEL_LOCATION`
keeps a nested `_env` fallback for `GOOGLE_CLOUD_LOCATION` on purpose — the Google
client libraries read that name themselves.

Two variables are still read via `os.environ.get` in `app/db/engine.py` and are not
covered by that guard: `FV_LOCAL_DB` and `FV_DATABASE_URL`. Treat them as debt.

## DuckDB is opened with external access off

`make_engine` passes `enable_external_access=false`. DuckDB's `read_csv`,
`read_parquet` and `read_text` reach the filesystem from inside SQL, so a
guardrail that inspects text is only one layer; this is the other.

`build_local.py` is the single caller that opts out with
`allow_external_access=True`, because it loads the seed CSVs through a temp
view. Opening the local warehouse with external access on would make
`just db-build` fail.

DuckDB refuses two connections to the same file with different configuration.
If you add a test that needs a different config, give it its own file rather
than reusing `data/local/fv.duckdb`.

## Migrations

`migrations/**` is `deny` for a reason: the migration history is the contract with
production, and an edit to a file that has already run is not a migration. Add a new
one and let a human review it.