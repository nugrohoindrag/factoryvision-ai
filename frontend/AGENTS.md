# frontend/AGENTS.md

Nested from the root `AGENTS.md`. That file wins if anything here contradicts it.

Pages live in `src/pages/` (Plant Briefing, the seven module pages, Action Center),
shared pieces in `src/components/`, the API client and app state in `src/lib/`, and
the React Three Fiber digital twin in `src/twin/`. The twin is lazy-loaded with the
MES page so three.js stays out of the first bundle.

```bash
npm --prefix frontend run typecheck   # tsc -b --noEmit
npm --prefix frontend test            # vitest run
npm --prefix frontend run test:e2e    # playwright, needs a running backend
```

CI runs all three, plus `vite build`. Locally they are reachable through
`just typecheck`, `just ci-frontend` and `just serve` / `just serve-frontend`.

## What is not here

- **No linter is configured.** `just lint` is Python-only and says so. There is no
  eslint and no oxlint config in this directory. If you want one, that is its own
  change — do not add a config file as a side effect of something else, and note
  that CI will not run it until the workflow gains a step.
- **No MSW, no generated API client, no storybook.** `src/lib/api.ts` is a small
  hand-written client; `src/lib/types.ts` mirrors the response shapes by hand, so a
  backend change to a response shape is not caught by `tsc`.
- **No `tsconfig` split.** One file, `"include": ["src"]`.

## The consequence you need to know

`tsconfig.json` includes only `src`. So **`.opencode/plugins/*.ts` outside this
directory is type-checked by nothing** — not by `tsc`, and `@opencode-ai/plugin` is
not installed here either. A typo in `guard.ts` would make the bypass guard silently
do nothing and nothing would notice.

That is why the pattern table lives in `deny-patterns.ts`, which is import-free, and
why `src/lib/guard.test.ts` imports it across the repo root: at least the *table* is
type-checked and tested. The interception itself is not verified. If you touch the
guard, that is the gap to close — and note it needs a `.opencode/package.json` and a
second tsconfig, both of which do not exist yet.

## Tests

Vitest for units, Playwright for the smoke test. `guard.test.ts` is unusual: it lives
under `src/` but tests a file outside this directory. That is intentional, and the
reason is in `deny-patterns.ts`'s header comment. Do not "tidy" it back.

Playwright needs `just serve` running on :8080 first; CI starts the dev server
itself.