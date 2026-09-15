# Adversarial Review — Spatial Evidence / Claim / Provenance Graph (Direction 03)

Reviewer stance: independent, fail-closed. Date: 2026-09-15 (UTC+8).

## Scope reviewed

`app/services/gis_harness/evidence_claim/**` + `tests/unit/gis_harness/test_evidence_claim_graph_v1.py`

## Checklist (GOAL §24)

| Risk | Finding | Severity | Disposition |
|---|---|---|---|
| false evidence | EvidenceNode is stub+ref only; projectors read ArtifactRecord/status without inventing PASS | — | OK |
| cross-tenant leakage | `resolve_live_ref` + `verify_claim(expected_tenant_id=)` reject mismatches; tested | — | OK |
| stale claim as fresh | `invalidate_affected_claims` marks STALE; freshness stage fails on STALE/EXPIRED/SUPERSEDED/MISSING | — | OK |
| claim scope mismatch | AOI + temporal checked in verify; tested | — | OK |
| unit mismatch | explicit unit compare; tested | — | OK |
| unsupported narrative claim | `ClaimType.NARRATIVE` / `narrative_claim_unverified` never reaches SUPPORTED | — | OK |
| graph cycles | DFS path-set back-edge detection; tested | — | OK |
| unbounded traversal | max_depth≤12, max_nodes≤256; 1k/10k perf smoke | — | OK |
| artifact duplication | No SecondArtifactRegistry; ClaimStore holds stubs/claims/edges only | — | OK |
| provenance divergence | Projectors wrap refs; dataset version via data-fabric pin shape | — | OK |
| false PASS | Positive-proof gate: missing refs / partial / failed stage ⇒ not SUPPORTED; tested | — | OK |

## P0 / P1

None open after red→green on cycle detection (initial iterative stack missed back-edges; fixed with path-set DFS).

## Residual / known limits (non-blocking)

1. **v1 persistence**: ClaimStore is in-memory + dict round-trip (session/harness scoped like ArtifactRegistry). No Alembic table yet — intentional to avoid SecondProvenanceDatabase.
2. **Live wiring**: Projectors exist; production tool/SSE hotpath does not yet auto-ingest every analysis result into ClaimStore (call-site integration deferred; APIs ready).
3. **MapSpec paint introspection**: Carto binding accepts explicit breaks/style_class; does not parse arbitrary MapLibre paint expressions.
4. **SkillPolicy (#1327)**: skill_id refs only; no SkillPolicy file edits.
5. **StoryMap**: `ClaimNarrativeProjection` adapter only — does not rewrite `story_compiler.py`.
6. **Uncertainty positive proof**: When `require_uncertainty=True`, unresolved uncertainty_ref blocks PASS; soft-default leaves obligation optional unless claim declares `uncertainty_ref`.

## Rollback

Revert branch / close PR. Package is additive under `evidence_claim/`; no master schema migration.
