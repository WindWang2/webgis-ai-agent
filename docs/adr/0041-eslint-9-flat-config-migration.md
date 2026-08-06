# 0041. ESLint 9 flat config migration (Next 14)

**Date:** 2026-08-06
**Status:** Accepted

## Context

The backend lint was cleaned up and gated by ruff (PRs #302/#303), but the
frontend had **no enforced lint gate**: `production.yml` carried a
`TODO(eslint-upgrade)` escape hatch (`next lint ... || true`) and eslint 8.57.1
was running with a legacy `.eslintrc.json`.

Upgrading to ESLint 9.39.5 (flat config) collides with the pinned Next.js
stack in three ways:

- `eslint-config-next@14.2.35` (matching Next 14.2.35) is a **legacy (eslintrc)
  preset** and declares `peer eslint@^7.23.0 || ^8.0.0` — a hard conflict with
  eslint 9.
- Next 14's **internal build-time lint** passes a `useEslintrc` option that was
  removed in ESLint 9 → `next build`/`next lint` throw under ESLint 9.
- Two rules of `@next/eslint-plugin-next@14.2.35` call `context.getAncestors()`,
  removed in ESLint 9 → they crash the whole lint run (upstream fix only in
  plugin 15.x, which requires Next 15).

## Decisions

1. **Flat config via FlatCompat.** `frontend/eslint.config.mjs` bridges the
   legacy `eslint-config-next` presets (`next/core-web-vitals` +
   `next/typescript`) with `@eslint/eslintrc`'s `FlatCompat`, preserving the
   exact rule set of the old `.eslintrc.json` (which was deleted). The `lint`
   script became `eslint . --max-warnings 0` (replacing `next lint`).
2. **`eslint.ignoreDuringBuilds: true` in `next.config.mjs`.** Next 14's
   build-time lint is incompatible with ESLint 9; lint ownership moved to the
   **CI gate** — a blocking `npx eslint . --max-warnings 0` step in
   `production.yml`. The guardrail is preserved, only relocated: builds no
   longer lint, CI enforces it on every push.
3. **`legacy-peer-deps=true` in `frontend/.npmrc`** to bypass the
   eslint-config-next@14 ↔ eslint 9 peer conflict. The lockfile holds the full
   resolved tree, so resolution is deterministic; the flag makes local, CI, and
   Docker (`npm ci`) behave identically. Docker's `frontend-deps` stage must
   COPY `.npmrc` for this to hold (fix `c28331a`).
4. **Disabled `@next/next/no-duplicate-head` + `no-page-custom-font`.**
   Both rules crash ESLint 9 (removed `getAncestors()`), are pages-router
   specific, and this project is App Router — an equivalent degradation.
   Re-enable when `@next/eslint-plugin-next` ≥ 15 is adopted.
5. **Behavior preserved.** The migration also removed genuine dead code in 20
   source/test files; the vitest suite (437 tests at migration time) stayed
   green, and `typecheck` + `eslint . --max-warnings 0` pass.

## Consequences

- **Builds no longer lint.** The CI ESLint gate is the single owner; if it is
  ever relaxed, lint coverage silently disappears from the build path.
- Two pages-router rules are not enforced (no impact on App Router; revisit on
  the Next 15 upgrade).
- The peer-dependency bypass is pinned by the committed lockfile; anyone
  regenerating it must keep `legacy-peer-deps=true` in `.npmrc` or `npm ci`
  fails with ERESOLVE.
- Related infra follow-ups: Docker frontend stages run on `node:22-alpine`
  (eslint 9's dependency tree requires node ≥ 22 under `engine-strict`), and
  the CI action runtime deprecation warnings were cleared (codecov v5.5.5,
  github-script v8, download-artifact v8) — see commits `c28331a`, `a415d85`,
  `eb2c223`, `3483a08`.
