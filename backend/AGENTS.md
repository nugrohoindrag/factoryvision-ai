# backend/AGENTS.md

Nested from the root `AGENTS.md`. That file wins if anything here contradicts it.

## Layout

```
app/        FastAPI service, the pydantic Settings, the SQL guardrail, the plant spec (plant.py)
simulator/  Offline tools: seed generation and the photo catalog; S311 is exempt here
tests/unit/ pure logic, no I/O
tests/integration/ real DuckDB through the migrations
tests/e2e/  Playwright, belongs to neither tier
migrations/ Alembic
```

Dependencies point one way: `simulator/` imports `app/` (the plant spec, the
schema), `app/**` never imports `simulator/`. The eight imports that used to point
the other way were removed by moving `simulator/plant.py` to `app/plant.py`. Do not
add one back; if `app` needs something from the generator, move it into `app`.

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
  verify. Code under `app/` gets no such exemption: the twin simulator
  (`app/simulation/`) uses `numpy.random.Generator`, seeded per tick, which is
  just as deterministic and is not what S311 is about.
- **Agent tools take keyword-only arguments and declare `tool_context` only when they
  use it.** ADK calls tools with keywords and injects `tool_context` by parameter
  name; it resolves annotations with `typing.get_type_hints`, so `app/agents/tools.py`
  has no `from __future__ import annotations`. Tool descriptions for Gemini live in
  `TOOL_DOCS` (data, not docstrings) and are attached at import.
- **Routes have no `from __future__ import annotations`.** FastAPI reads the
  `Annotated[..., Depends()]` aliases at runtime; with postponed annotations ruff's
  TC rules would move those imports under `TYPE_CHECKING` and break the routes.
- **The run timeline uses per-agent callbacks, not an ADK plugin.** In adk 1.14.1
  `Runner(plugins=...)` is deprecated and `App` is experimental; both warn, and
  warnings are errors here. `TRACE_CALLBACKS` in `app/agents/runner.py` is attached to
  every `LlmAgent`.
- **The SQL guardrail is an AST check, and its allowlist is the whole policy.**
  It parses with sqlglot and rejects writes, multi-statements, non-allowlisted
  tables and table functions. Two things it deliberately does not do: it validates
  the text you give it and then executes that same text (it never regenerates SQL,
  which would change dialect semantics), and it does not know whether a filter is
  *correct* — only whether it is a read of an allowlisted table.

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