# Combined type and module debt clearance

The first combined fast gate stopped at the existing debt ratchet. No baseline,
threshold or scanner rule was changed to make it pass. The original failure
identified 86 excess generic dictionaries, missing function annotations, a silent
Pi probe exception, an indirect-only assertion and an SVG module over 2,000
lines.

- Analysis workflow code replaces 28 newly introduced generic dictionary
  annotations with specific catalog/budget/eligibility contracts and explicit
  unknown-value boundaries. Seventy-eight focused tests and 25 coordinate and
  science tests passed. Both original performance workloads passed unchanged.
  Full FeatureCollections are not recursively revalidated or copied; existing
  NumPy/tuple values and unknown persisted science fields remain accepted.
- Workbench code replaces 45 generic dictionaries with concrete scope,
  selection-epoch, turn-chain and interaction projection contracts. Its focused
  suite passed 165 tests; six affected modules passed Mypy. Four compiler
  diagnostics were reproduced in the unmodified baseline. Explicit generic
  HTTP response models preserve the original two route schemas and arbitrary
  extension fields; four real TestClient comparisons produced identical bodies,
  and the four original route tests passed.
- Publication text, page, scale and extent outputs and label-field findings now
  have concrete structures. The shared artifact snapshot also has a typed
  declaration. Four affected modules passed focused Mypy. The bounded line-label
  anchor helper moved into the existing SVG label module; its import remains
  available through the compiler, which is now below the existing line limit.
  The combined cartography, compiler, terrain, upload and preflight sample
  passed 127 tests. The Python missing-field test now matches the same explicit
  non-substitution contract as the TypeScript test.
- Pi identity probing emits a bounded exception-type diagnostic without
  exception messages or traces. The doctor return type is explicit. Five
  focused tests, Mypy and Ruff passed.

The final combined frontend validation passed repository ESLint, both
TypeScript projects, 532 files with 4,976 passing tests and one expected failure,
and the production build. V8 coverage was 84.79% lines, 81.59% statements,
78.94% functions and 70.75% branches. All four performance files ran after the
functional project, passed their original thresholds, and emitted no timing
deferral warning. An unused test import found by the complete lint run was
removed without changing lint policy.

These results supplement the earlier PR reviews. Complete backend, generated
artifact, performance/cartography and final browser checks are separate
requirements; no production deployment or real-service coverage is claimed.
