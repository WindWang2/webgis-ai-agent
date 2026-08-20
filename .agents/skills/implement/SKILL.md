---
name: implement
description: "Implement a piece of work based on a spec or set of tickets."
disable-model-invocation: true
---

Implement the work described by the user in the spec or tickets.

Use /tdd where possible, at pre-agreed seams.

Run typechecking regularly, single test files regularly, and the full test suite once at the end.

Before committing/pushing, run `scripts/ci-local.sh --fast` (full: `scripts/ci-local.sh`). CI gates are repo-wide (`ruff check app/ tests/ main.py manage.py`, `npx eslint . --max-warnings 0`, `tsc --noEmit` incl. test files) — never lint/typecheck only the files you touched. The script is kept in lockstep with `.github/workflows/production.yml` by `tests/test_ci_local_gate_contract.py`.

Once done, use /code-review to review the work.

Commit your work to the current branch.
