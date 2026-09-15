# RECON — Spatial Evidence / Claim / Provenance Graph

## Parallel work

- Master tip at seed: `e21314a5` (includes #1326, StoryMap #1318 merged).
- Open PR #1327 Direction 02 Skill Policy — out of ownership. No edits under
  `skills/policy.py`, `skills/shadow.py`, `skills/hotpath.py`, or SkillPolicy APIs.
- Mission Runtime already on master (#1320) — do not reimplement.

## Vocabulary alignment

- Reuse goal_satisfaction EvidenceClass / EvidenceStatus spirit: deterministic/structural
  can back PASS; visual/assisted cannot; missing ≠ PASS.
- Artifact status vocabulary: valid/stale/superseded/expired/failed.
- Product lineage liveness: alive/expired/stale/superseded/failed/unknown.
- MapProductSpec.claims is free-text — Claim graph is the typed authority;
  narrative may cite claim_id.

## Package location

`app/services/gis_harness/evidence_claim/` mirroring goal_satisfaction / product_* style.

## Store choice

In-memory ClaimStore with optional JSON dict round-trip (same pattern as
ArtifactRecord.to_dict / SkillEvidenceRecorder). No new Alembic table in v1 —
claims are session/harness scoped like ArtifactRegistry unless later promoted.
