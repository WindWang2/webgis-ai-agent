# Root runtime findings — 2026-11-06

Continuation of the initial 152-issue inventory. These are finding-level dispositions; an aggregate remains open until all owners reconcile its rows. Paths are relative to `app/services/` unless stated otherwise.

| Issue | Finding | Disposition and evidence |
|---|---|---|
| 1816 | S3-001 | Fixed: `plan_mode.py` binds the challenge, approval and execution to the complete tool/argument template fingerprint. Dynamic tier-3 `${step.path}` targets explicitly reject until concrete values are proposed for user confirmation. SEC03 behavioral tests cover normal approval, mutation and dynamic-target rejection. |
| 1816 | S3d-001 | Fixed: report SVG compilation explicitly caps features at the server export ceiling and both compiler/wall-clock budgets at30s; client thresholds cannot raise either ceiling. |
| 1816 | S3d-002 | Fixed: report downloads use connection-pinned safe HTTP, reject redirects and external SVG references. Shared with S100-010/011. |
| 1821 | S5-027 | Fixed: `app/lib/harness/pi_agent_harness.py` retains bounded result and mutation projections; recursion/node limits prevent deep/cyclic argument expansion. Evaluation status and fingerprint facts survive oversized results. Behavioral retention regression and harness tests pass except independently tracked fixture migrations. |
| 1952 | S51-005 | Fixed: `model_runtime/routing.py` avoids a cooled-down configured primary when a capable healthy fallback exists; explicit operator preference remains authoritative. |
| 1952 | S51-006 | Fixed: `chat/execution_engine.py` records provider success only after full stream exhaustion; cancellation closes the inner stream without resetting provider health. |
| 1952 | S51-007 | Fixed by core: map-state persistence occurs inside the session critical section. |
| 1952 | S51-008 | Fixed: `chat/engine_manager.py` retains the owner token across cached engine turns; explicit session cleanup removes it. |
| 1952 | S51-009 | Fixed: `chat/llm_client.py` assigns unique IDs to provider tool calls that omit their IDs. |
| 1952 | S51-010 | Fixed: `chat/no_progress.py` hashes full canonical arguments; long tails, nested objects and distinct numeric values no longer collapse. |
| 1952 | S51-011 | Fixed: output token budget takes the minimum of descriptor, role profile and legacy configuration ceilings. |
| 1952 | S51-012 | Fixed: background callback checks cancellation before calling `Future.exception()`. |
| 1952 | S51-013 | Fixed: model POST retries are restricted to pre-dispatch connection/pool failures; read/write/status failures are not replayed after potential billing. |
| 1952 | S51-014 | Fixed: empty retrimmed stream terminates cleanly instead of leaking StopAsyncIteration. |
| 1952 | S51-015 | Fixed: surface-gate failure rejects with a typed retryable response and records a metric; it cannot silently bypass tool qualification. |
| 1952 | S52-004 | Fixed: `chat/pi_rpc_client.py` readiness checks child termination and cleans up on bounded startup failure. |
| 1952 | S52-005 | Fixed: oversized RPC stdout fails pending calls immediately and terminates the child. |
| 1952 | S52-006 | Fixed: Pi models/settings files use fsynced temporary files and atomic replace. |
| 1952 | S52-007 | Fixed: `chat/pi_native_surface.py` parses JSON object proxy arguments; malformed/nonobject values reject rather than becoming empty arguments. |
| 1952 | S52-008 | Fixed: surface qualification preserves required native tools; dispatch remains the execution authority. |
| 1952 | S52-009 | Fixed: strict surface compilation distinguishes over-budget specs from compilation failures; compatibility callers retain their existing return contract. |
| 1952 | S52-010 | Fixed: presentation tools cannot advance domain completion merely by overlapping the core-step name. |
| 1952 | S52-011 | Fixed: `chat/pi_input_gate.py` recognizes PEP604 scalar unions. |
| 1952 | S52-012 | Fixed: ABI summary projection accepts strings only and sanitizes errors/paths. |
| 1952 | S53-L01 | Fixed: `chat/tool_pipeline.py` reports milliseconds, not seconds under a millisecond field. |
| 1952 | S53-L02 | Fixed: invalid argument JSON/nonobject input rejects before tool execution. |
| 1952 | S53-L03 | Fixed: negation matching excludes the demonstrated 非常/无人机 false positives. |
| 1952 | S53-L04 | Fixed: OD/NDVI/MAD matching no longer requires accidental leading spaces. |
| 1952 | S53-L05 | Fixed: semantic retrieval skips non-model-visible tools and caps query length. |
| 1952 | S53-L06 | Fixed: semantic index caches8 registry snapshots under a lock, caches empty registries, and queries coherent name/vector tuples. |
| 1952 | S53-L07 | Fixed: tool compression retains output format. |
| 1952 | S53-L08 | Fixed: SSE producer uses a64-item queue with awaited backpressure and cancellation cleanup. |
| 1952 | S53-L09 | Fixed: tiny UTF-8 byte caps remain hard limits, including truncation markers; duplicate obligations do not consume repeated space. |
| 1952 | S54-L01 | Fixed: active-tool and turn-control markers are not arbitrarily character-trimmed. |
| 1952 | S54-L02 | Fixed: fenced/scrubbed context recomputes token estimates and fingerprints. |
| 1952 | S54-L03 | Fixed: legacy GIS memory projection enters the same scrub/fence boundary. |
| 1952 | S54-L04 | Fixed: receipts and pool accounting derive from final post-ceiling context items. |
| 1953 | S96b-005 | Fixed: `mvt.py` simplifies in projected half-pixel units at low zoom, not geographic degrees. |
| 1953 | S96b-006 | Fixed: spatial-index invalidation tombstones survive while an affected build is in flight, even beyond60s; deterministic threaded regression. |
| 1953 | S96b-007 | Fixed: session invalidation does not discard unrelated sessions' in-flight indexes. |
| 1953 | S97-001 | Fixed read-error transaction cleanup in quota queries; Medium S3d-003 write admission is separately owned by core and cannot be closed by this Low fix. |
| 1953 | S97-002 | Fixed: destructive approval is bound to exact tool/arguments; see1816 S3-001. |
| 1953 | S97b-L07 | Fixed: sibling drain does not count error results as successful steps. |
| 1953 | S97b-L08 | Fixed: failed result storage does not invent an alias; reused last results retain content-based identity. |
| 1953 | S97b-L09 | Fixed: offline LLM probe requires2xx and RAG probe checks actual local cache completeness without downloads/model initialization. |
| 1953 | S97b-L10 | Fixed: offline inventory checks active model cache files and includes the complete resolved pnpm-lock package inventory in the SBOM. |
| 1953 | S98-001 | Fixed: workspace materialization exposes newly written bytes; repeated content-addressed blobs do not double-charge quota. |
| 1953 | S98-002 | Fixed: quota availability IO is offloaded from the event loop. |
| 1953 | S98-003 | Fixed: session-ref tags/evidence retain session identity; lifecycle invalidation matches exact session+authority, processes both direct/tagged dependencies and moves DB IO to a bounded worker queue. Legacy unscoped projections remain subject to lazy liveness validation. |
| 1953 | S98-004 | Fixed: freeze captures live payload digests even without durable pointers; resume rejects changed inputs and discloses old manifests without digests as stale. |
| 1953 | S98-005 | Fixed: snapshot deletion requires a matching creator; ownerless legacy snapshots require explicit project-owner/admin authority. |
| 1953 | S98-006 | Fixed: knowledge-card failure warnings only consume active entries. |
| 1953 | S98-007 | Not a current defect: `MapSpecLayer` consumes source IDs, not session refs; ref-bearing source/layout positions are rewritten. Layer label/filter literals may legitimately contain `ref:` text and must not be recursively rewritten. The issue explicitly left the alleged layer shape unverified. |
| 1953 | S98-008 | Fixed: authenticated resume route and full UUID session IDs; anonymous ownerless resume is not an exposed route. |
| 1953 | S99-007 | Fixed: runtime manifest dataset validation filters project_id. |
| 1953 | S99-008 | Fixed: deleted projects are excluded from authorization lookup; invalid update status rejects at schema boundary. |
| 1953 | S99-009 | Fixed: organization user listings include their own org-less private projects. |
| 1953 | S99-010 | Fixed: provider health uses a rolling minute window; tracked network errors retain the error-dictionary contract. |
| 1953 | S99-011 | Fixed: feature offset with no limit returns the remaining tail. |
| 1953 | S99-012 | Fixed demonstrated latent defects: hillshade requires explicit finite positive spacing in elevation units, bivariate requires3×3, legend reflects chosen palette. Optional converter modes remain an explicit API, not a claim that production auto-selects them. |
| 1953 | S99-013 | Fixed: raster PNG writes validate the same ID alphabet as reads before creating directories. |
| 1953 | S99-014 | Fixed: raster stretch quantization reaches the last palette stop; golden fixture updated and independently checked against exact endpoint RGBA. |
| 1953 | S99-015 | Fixed: nonintersecting color tiles return/cache transparent tiles without warning storms. |
| 1953 | S99-016 | Fixed: explicit tile band selections require1 or3; two-band files without explicit selection use band1. |
| 1953 | S99-017 | Fixed: RAG engine deletion enforces tenant ownership under the store lock. Soft deletion prevents search immediately; physical compaction remains the existing retention policy, not an immediate erasure guarantee. DB-failure compensation uses the same owner and checks deletion success. |
| 1953 | S100-007 | Fixed: ref payload cache uses nonrepeating invalidation epochs even after tombstone pruning. |
| 1953 | S100-008 | Fixed: artifact-event task completion promptly removes retained task references. |
| 1953 | S100-009 | Fixed: report module docstring precedes imports. |
| 1953 | S100-010 | Fixed: safe report URL fetch uses guarded transport and passes bytes to WeasyPrint, preventing secondary DNS/redirect fetches. |
| 1953 | S100-011 | Fixed: report SVG rejects external HTTP(S) references while retaining safe inline images/fragments. |
| 1953 | S100-012 | Fixed: final report state retries with a fresh transaction; exhausted persistence failures report failure; stale processing rows expire on authorized reads. Tests cover both one-failure recovery and two-failure rejection. |

## Validation

- Root scope/report/raster/confirmation regression batch:122 passed.
- Pi native live-registry/ToolSpec/RPC batch:46 passed.
- Later root permission/report-layout/security/cache regressions:76 passed.
- Harness retention/report integration:246 passed, with3 existing fixture/contract mismatches assigned to their owners; the legitimate base_layer/user_location whitelist omission was corrected and its regression now passes. Full integration is still pending.

## UI discovery

The real production browser showed CARTO API-key-required tiles despite successful HTTP responses. Default selection now uses OpenFreeMap's documented `/styles/liberty` endpoint; credential-gated CARTO providers are hidden unless `NEXT_PUBLIC_CARTO_BASEMAP_KEY` is present at build time. Corrected persisted selections, generated profile projection, resource key propagation, Docker build arguments and Compose forwarding are covered by53 frontend+9 Python tests. Actual screenshot verification follows the fresh production build.

Primary sources: [CARTO key requirement](https://www.carto.com/basemaps/apikey/index.html), [OpenFreeMap quick start](https://openfreemap.org/quick_start/).
