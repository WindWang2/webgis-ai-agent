# ADR-0199: GIS Pack SDK & Capability Certification v2

- Status: Proposed
- Date: 2026-09-17
- Line: `extensions/gis-pack-sdk-certification-v2`
- Related: ADR-0104 (extension platform V1), ADR-0105 (V2 worker/supply chain),
  ADR-0119 (V3 trust store/marketplace/streaming), ADR-0101/0102/0103 (tool surface),
  ADR-0182 (skill library / SkillPolicy), audit issues #1337/#1338/#1339 (skill
  code-execution boundary)

## Context

The extension platform (V1–V3) already gives third-party GIS packs: a fail-closed
manifest, bounded discovery + content fingerprints, a projection-only host
(extensions can never write authoritative registries except through
`ExtensionContext.register_*` with ledger undo), worker isolation with a default-deny
capability broker, content signing and a trust store.

What it does **not** provide:

1. **Per-capability certification.** `certify_extension` runs 12 deterministic
   pack-level checks, but nothing proves an individual tool/algorithm actually works
   (declaration ↔ implementation ↔ numeric evidence ↔ runtime probe).
2. **A gate.** Certification is CLI-only and ephemeral; a pack that fails every check
   can still be activated. Nothing binds a certification verdict to the exact bytes
   that are being loaded.
3. **Durable evidence.** No persisted, tamper-evident report that an operator can
   audit, and no upgrade/uninstall proof that deactivation leaves no orphan
   projections.

Adding a new GIS algorithm therefore still means either touching core registries or
shipping an uncertified extension — the coupling the extension platform was built to
remove.

## Decision

### D1 — Certification is a pipeline over the existing platform, not a new system

`app/extensions_platform/capability_certification.py` runs six ordered stages per
pack, reusing existing primitives (supply-chain helpers from `certification.py`,
`compute_fingerprint`, `ExtensionHost.activate/deactivate`, the SDK authoring
harness, the live `ToolRegistry` projection). No second registry, no second manifest,
no new discovery path.

### D2 — Stages: declared → schema → implementation → tests → runtime probe → lifecycle

- **supply_chain**: signature/SBOM-secret/layout/budget/protocol (existing semantics).
- **schema**: manifest-level declarations are certifiable (tools must classify
  `side_effect`; skills must validate as `SkillContract` with resolvable references).
- **implementation**: after a real activation, every declared item must exist in its
  authoritative registry. A declared-but-unimplemented capability **cannot** be
  certified.
- **tests**: pack-declared evidence is replayed (SDK `smoke_cases` via
  `run_authoring_checks`; VALIDATED/PRODUCTION algorithms need `conformance_tests`
  and `uncertainty_outputs` — same rule as the core algorithm registry).
- **runtime_probe**: manifest-declared probes execute through the *real, registered,
  permission-wrapped* callable: result assertion, deterministic replay (two calls,
  canonical-JSON digest equality), latency-class and result-size verdict buckets.
- **lifecycle**: clean deactivation plus orphan check — after deactivation, no
  namespaced projection of the pack may remain (upgrade/uninstall oracle).

Reports are byte-deterministic: fixed check order, no timestamps, no raw wall-clock
readings (latency is recorded as a verdict, not a duration).

### D3 — Evidence is fingerprint-bound and optionally tamper-evident

`.certification.json` (name in `discovery.py`, excluded from fingerprints exactly like
`signature.json`) stores the report with the pack fingerprint it was produced from.
Validation compares the stored fingerprint to a *recomputed* fingerprint — the report
file's own bytes are never hashed, so autocrlf checkouts cannot forge staleness
(lesson from commit `017d1d41`).

The gate trust model is explicit about what a local file can prove:

- `evidence` (default): an unsigned report with a matching fingerprint is accepted,
  and every acceptance surfaces a warning diagnostic. Not tamper-evident; for local
  development.
- `strict`: the report must carry an HMAC over its canonical JSON made with the
  operator certification key (`EXTENSIONS_CERTIFICATION_KEY`). Wrong key, tampered
  payload, unsigned report → fail closed. This is the production posture.

### D4 — The gate is opt-in and off by default

`HostPolicy.require_certified` (settings: `EXTENSIONS_REQUIRE_CERTIFIED`, default
`false`) rejects activation before LOADING unless a valid report exists for the
current fingerprint. `builtin_ids` are exempt (in-repo packs are certified in CI by
running this same pipeline). The certification pipeline itself activates with
`override_gate=True` — the certifier is the evidence producer and must not recurse on
the gate. Kill switch: keep the flag off; behavior is byte-identical to master.

### D5 — Manifest v1.3.0 (V4, additive)

`certification` (probes keyed by declared tool names / algorithm ids) and `skills`
(declarations whose `contract` payload must validate as a gis_harness
`SkillContract`) require `api_version >= 1.3.0`. Probe keys must reference declared
capabilities; payloads are strict JSON (no NaN/Infinity) with hard size bounds;
duplicate ids rejected. 1.0.x–1.2.x manifests parse unchanged.

Pack skills are certified and surfaced in the catalog with
`governance_tier: "candidate"`; live overlay into the SkillLibrary remains a
separate governance decision (SKILL_PACKS vocabulary and SkillPolicy trusted packs
are #1327 hot-surface concerns) — this ADR deliberately does not wire it.

### D6 — Catalog projection

`pack_catalog.py` builds a deterministic, read-only catalog: namespaces → packs →
certification status (missing/stale/valid/invalid) → bounded surface (tools ≤ tier 2,
algorithms, providers, skills as candidates). `catalog --certified-only` gives
consumers the certified view without changing any dispatch semantics.

## Consequences

- New GIS algorithms/data adapters ship as packs, get machine-verifiable capability
  evidence, and can be gated in production without touching core registries.
- Certification cost is paid at certify time, not at every activation (the gate reads
  a fingerprint-bound file; it never runs probes).
- Honesty rules: evidence-mode acceptance is loud; worker packs whose probes fail are
  reported as failures, never silently re-run in-process; `unclassified` side effects
  are uncertifiable by construction.
- Known limits (documented, deliberate): in-process execution remains
  trusted-code semantics (isolation only exists in worker mode — the pipeline probes
  through whatever execution mode the pack declared, it does not invent one); skill
  overlay is certification+catalog only; unsigned evidence is not tamper-evident by
  definition.

## Compatibility

Additive only: host api_version 1.2.0 → 1.3.0 (minor bump, all 1.0.x–1.2.x
extensions stay compatible); new manifest sections gated by the 1.3.0 floor; new
diagnostic codes appended; gate defaults off. The conformance corpus' incompatible-API
representative moves 1.3.0 → 1.4.0 for the same reason.

## Local evidence

`tests/unit/extensions_platform/`: pipeline (12), gate/persistence (16), manifest
fail-closed matrix (11), catalog (4) — plus the full extensions_platform suite
(2538 passed; the 8 failures are the pre-existing Windows environment baseline,
reproduced one-for-one on pristine `origin/master`). CLI round trip:
`certify --staged --save` → all stages pass → `catalog --certified-only` lists the
pack. Local evidence only; no online CI wait; do not auto-merge.
