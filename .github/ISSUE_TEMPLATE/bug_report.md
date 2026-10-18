---
name: Bug report
about: Something behaves differently from how it is documented
title: ''
labels: bug
assignees: ''
---

## What you expected

<!-- The documented behaviour. Cite README.md, AGENTS.md, docs/, or the docstring
     you took it from — a bug against an undocumented expectation is a feature
     request. -->

## What happened

## Steps to reproduce

```
1.
2.
3.
```

## Which gate would have caught it

<!-- Required. If no gate covers this, that is the more important finding — say
     so here and it becomes a candidate for a new gate.

     Gates that exist: ruff, mypy, unit tests, integration tests, the error-catalog
     drift check, the .env.example <-> Settings drift check, and CI on pull_request.
-->

- [ ] An existing gate covers it
- [ ] No gate covers it — a gate is missing

## Environment

```
just --version
uv run --python 3.12 python --version
git rev-parse --short HEAD
```

## Logs

<!-- Redact secrets. .env files are denied to agents and should not be pasted here. -->