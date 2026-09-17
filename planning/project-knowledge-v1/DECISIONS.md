# DECISIONS — ProjectKnowledgeProjection v1 (ADR-style; most conservative, most compatible choice wins)

**D1 — Storage backend: one new bounded table `project_knowledge_entries`, following the ADR-0183/ADR-0069 store pattern.**
Sync SQLAlchemy model + migration (≥0093), DAO free functions that only flush (caller commits), `org_id` NOT NULL, composite index `(org_id, project_id, entity_kind, status)`, partial unique index for active rows on the natural key, per-project row budget (default 400, mirroring `SCOPE_BUDGET[SCOPE_PROJECT]`, `gis_memory/store.py:48-53`).
*Alternatives*: (a) reuse `gis_spatial_memories` with new kinds — rejected: closed kind vocab + CheckConstraint, write-gate evidence sources don't apply to identity projections, would corrupt memory retrieval semantics; (b) in-memory index only — rejected: not durable, can't support invalidation contracts or cross-mission reuse after restart; (c) column on `projects` — rejected: unbounded JSON, no isolation of statuses. (a)+(b) hybrid rejected for v1 complexity.

**D2 — ID/namespace scheme: projection rows carry no new identity beyond a surrogate `pkx_<16hex>`; all semantic identity is borrowed.**
Every entry stores `(org_id, project_id, entity_kind, subject, authority_store, authority_id, version_token, content_digest, bbox, temporal_label, status, verdict, refs[≤8])`. `authority_store ∈ {project_dataset, artifact, artifact_revision, map_product, workflow, workflow_revision, mission, claim, gis_memory, carto_fact, session_ref}`; `authority_id` is the existing id verbatim (`ds_*`, artifact uuid, `ref:*`, `msn-*`, `(project_id,version_no)` encoded as `mp:<project>:<no>`, claim_id, memory_id). Fingerprint columns mirror existing digest semantics (`version_fingerprint`, `content_sha256`, `product_fingerprint`) — the projection never recomputes content digests.
*Alternatives*: minting a new global knowledge-id namespace — rejected: creates a second identity truth and breaks "back-reference to authoritative store" oracle.

**D3 — Staleness/invalidation: lazy fingerprint re-validation on read (primary) + explicit invalidation on known mutation seams (secondary). No new background worker.**
Each entry stores the observed `version_token`/digest at index time. On retrieval, the projector re-reads the *cheap* authoritative aggregate (single scalar query per candidate batch, the `get_project_fingerprint` cost pattern, `project_service.py:234-290`) and demotes mismatched entries to `status=stale` (write-back) — the same "cache is correct even without invalidation" contract as `project_context_cache` (`project_service.py:66-83`). Explicit `invalidate_project_knowledge(project_id, authority_store, authority_id)` is called from seams this branch owns (rebuild/attach/index-refresh paths) and, additively, via `register_ref_invalidation_hook` for session-ref OVERWRITE/ROLLBACK (observer never affects the authority, `ref_lifecycle.py:60-94`). Claim-status staleness is re-checked on read from the live ClaimStore when the process still holds it; a missing ClaimStore entry demotes `verified_by` edges to `unknown`, never `supported` (positive-proof invariant).
*Alternatives*: event-driven invalidation via #1355 `spatial_events` — deferred: that PR is unmerged and default-off; we keep our invalidation correct without it and can subscribe to the ledger later. Background sweeper — rejected: new failure surface, violates "no second runtime".

**D4 — Reuse verdict rules: fingerprint-and-scope equality, never name similarity.**
For a requested scope (AOI ref/bbox + temporal label + method/capability + input dataset fingerprints):
- `exact`: all of — asset status valid/head; content digest equal to current authoritative digest; AOI equal-or-contained with `Scope.overlaps`-style conservative equality (no wildcard when both set, `evidence_claim/contracts.py:165-181`); temporal label equal; producing method/capability equal (`ArtifactLineage.producing_capability/algorithm`); all upstream `source_dataset_fingerprint`s current; claims attached are `supported`.
- `recompute_partial`: digest/AOI/temporal match but ≥1 upstream fingerprint stale or a claim is `stale/unknown` — returned with the explicit stale-cause list.
- `not_reusable`: everything else (including "similar name, different AOI/year" — the negative oracle).
Partial-name/text matching from `retrieval.py:_subject_bonus` is explicitly **not** part of verdicts; text score may only *order* candidates inside a verdict, never upgrade one.
*Alternatives*: embedding/name-similarity reuse — forbidden by the brief and by the repo's positive-proof culture; verdict-without-scope-check — rejected (mis-reuse oracle).

**D5 — ProjectContextCard budget: ≤1600 chars rendered, ≤12 items, ids+summary+refs only.**
Card = header (project id/name) + per-item one line `kind · subject · verdict · authority_id · ref-pointer`. No payloads, no CoT, no memory values beyond 80-char summaries (same bound as `_summary_for`, `proactive_retriever.py:201-226`); untrusted strings `_xml_fence`-escaped and rendered per-line fail-open with an omission receipt (the `[GIS_MEMORY]` render discipline, `gis_memory/projection.py:122-165`). Numbers chosen: 1100 (memory block) < 1600 (project-wide, covers datasets+artifacts+missions) < chat env block budgets; item cap 12 keeps the card under ~1 turn-token-percent.
*Alternatives*: 1100 shared with GIS_MEMORY — too tight for whole-project; 2048 (VALUE_CHAR_BUDGET) — per-item budget, wrong axis.

**D6 — Kill-switch flag: `GIS_PROJECT_KNOWLEDGE` (default `0` = OFF).**
Follows the `_env_truthy` os.getenv convention (`hotpath_convergence/flags.py:12-16`); when off: routers return 404-shaped empty (endpoints still registered but short-circuit to disabled response, or router not included — choose router-not-included for zero surface, matching `GIS_MISSION_HOTPATH` default-off philosophy), ingestion hooks are no-ops with zero hot-path cost (flag checked before any work, as #1355 documents). Rationale for default-OFF: the feature changes what the LLM can see pre-mission (behavior-changing), unlike `GIS_MISSION_RUNTIME` (infrastructure, default-on).
*Alternatives*: default-on like mission runtime — rejected: new LLM-visible context must be opt-in first release; settings-module flag — repo convention for these subsystem flags is env, not settings.

**D7 — API surface (all under `/api/v1/projects/{project_id}/knowledge`, tenant-gated).**
`GET /card` (bounded ProjectContextCard); `GET /search?q=&kind=&bbox=&temporal=` (bounded top-k with reasons); `POST /rebuild` (idempotent reindex of authoritative stores; returns counts, never payloads); `GET /reuse-candidates?goal=&aoi=&temporal=&method=` (verdict-tagged candidates). Router included in `app/main.py` next to `project_routes` (:813) only when flag on; openapi snapshot refreshed. Reads/writes go through `ProjectService.get_project_with_auth` first — isolation enforcement point is the project gate + `org_id` equality predicate on every projection query (both layers, mirroring gis_memory fail-closed org stamping).
*Alternatives*: top-level `/knowledge` (collides with existing RAG `knowledge` router, `main.py:806`); unscoped global endpoints — rejected by tenancy.

**D8 — Isolation & red lines.**
The projection is **never** a second authoritative store: it stores only pointers + digests + verdicts; deleting a projection row must never affect the authoritative row; rebuild-from-source must be idempotent and sufficient (oracle: back-references resolvable). Content Rules: no raw payloads, no CoT, no secrets (`assert_no_secrets` sanitizer pattern), value budgets enforced at write, sensitive rows excluded from retrieval by default. It does not re-implement GIS Memory (reads it), EvidenceGraph (references claim ids), ArtifactRegistry (references refs/rows), Mission runtime (references mission refs), or SkillPolicy (references capability triples).

**D9 — Migration & parallel-work coordination.**
Migration number ≥ `0093` (0092 is taken by open PR #1355 `spatial_events`). No edits to `project_artifact_promotion.py` / `map_product_service.py` / `mission_runtime/**` / `evidence_claim/**` / `gis_memory/**` (see PARALLEL_OWNERSHIP.md). Rebase onto #1335 (claim fail-closed semantics) and ideally #1355 before merge.


## 修订（实现与评审后）

- **D3 修订（hook 接线）**：`register_project_knowledge_hook()` 由项目知识路由
  `_gate` 在 flag-on 首次使用时懒注册（幂等），hook 本体带 flag 双重门（关 = 零行为）。
  失效正确性以检索期 lazy 复核为主，观察者仅增值推送（ref_lifecycle R6 同构）。
- **D5 备注**：检索扫描上限为 200 active 条（行预算硬上界 400）；超出需 kind 过滤查询。
  典型项目投影远小于 200，预算是上界而非常态。
- **D6/D7 偏离（实现期决定）**：路由采用「无条件注册 + flag off 时 503」
  （与 ADR-0197 mission runtime 同型），而非 D7 原定的 router-not-included ——
  保证 openapi 快照与 flag 无关（快照稳定性）且端点可发现；kill-switch 语义
  （off = 零工作、503）不变。
- **P1-1（review）**：上游 ref-tag token 为空（权威 lineage 指纹 NULL）→ 一律
  `upstream_unverified`，绝不产生正向 reason / exact —— 「未知 ≢ 一致」。
