# 03 — DataProfile → ArtifactContract → DataRoleBinding → Preconditions → ScaleProfile → AlgorithmResolver → BackendSelection → ExecutionPlan: chain audit

Repo: webgis-ai-agent-harness-v4 @ 16d1c70. Read-only static audit; all paths relative to repo root. Three profile shapes coexist (V2 `DatasetProfile`, V3 `DatasetProfileV3`, camelCase "Spatial Meta Profile"); the resolver consumes only the camelCase shape, and only 2 of ~14 scientific profile datums actually reach resolver decisions today.

## (a) Current chain, hop by hop

```
[store time] session_data.store → compute_descriptor (app/schemas/ref_descriptor.py:273)
    produces RefDescriptor {feature_count, point_count, geometry_types, bbox, mvt/raster_capable,
    estimated_bytes, content_revision, filterable_fields, field_schema{type,null_count,min,max,samples},
    field_schema_complete}  (ref_descriptor.py:13-66)
    ⚠ RefDescriptor has NO crs field — CRS is structurally absent from the descriptor.

[profile producers — 3 shapes]
  P1 Spatial Meta Profile (camelCase, THE resolver dialect):
     profile_from_descriptor (app/services/spatial_meta_profiler.py:115) — O(1) descriptor projection;
        hardcodes crs=None, crs_status="unknown", temporalProfile=None  (spatial_meta_profiler.py:146-158)
     profile_geojson_source (spatial_meta_profiler.py:161) — full FC scan; only producer of crs (declared
        FC crs, :187/_declared_crs:9), null_ratio (:276), quantiles, temporalProfile (:297-312)
  P2 DatasetProfile V2 (app/lib/gis/dataset_profile.py:43) — bounded projection contract;
     to_resolver_profile (:104) is "the唯一 adapter" but emits only featureCount/geometryTypes/bbox/
     crs/artifactType/fields/fields_status — NO numericFields, NO raster, NO nulls
     DatasetProfiler service (app/services/data_profile/profiler.py:62): profile_session_ref (:102,
     shallow :140 / deep :168, LRU 256 TTL 300s, 50k-row scan), profile_raster_file (:229, rasterio
     downsampled) → DatasetProfileV3
  P3 DatasetProfileV3 (app/lib/data/profile.py:278) — richest shape: vector row_count/geometry counts/
     empty+impossible+zero-zero coords (:155-198), per-field Welford mean/std/unique/null_rate/
     temporal+unit hints (:127-152), raster dims/bands/nodata/band_stats/acquisition_time (:211-234),
     table candidate keys/coord/time columns (:246-275), CRS-kind classifier classify_crs_kind (:502),
     profile_quality COMPLETE/SAMPLED/PARTIAL/FAILED (:67)
     Produced ONLY on-demand: agent tool profile_dataset (app/tools/data_discovery.py:111-118) and
     ingest pipeline (app/services/data_ingest/pipeline.py:172-181). NEVER persisted, NEVER linked to
     an artifact: ArtifactContract.profile_ref ("Dataset Profile V3 检索键", app/lib/data/
     artifact_contract.py:198) has zero producers (grep: only artifact_contract.py defines it).

[artifact contract / lineage]
  ArtifactContract V3 (app/lib/data/artifact_contract.py:173) — read-only projection with
  geometry_kind/feature_count/crs/extent/raster RasterShape/statistics/profile_ref/lineage/produced_by.
  Bridges: from_artifact_record (:351), from_ref_descriptor (:453 — infers geometry_kind, but NO crs
  because descriptor has none), from_raster_descriptor (:551). Consumers: data_catalog/catalog.py:201,
  lineage_query.py:97, workspace/snapshot.py:232 (snapshot persists artifact_contracts),
  data_lifecycle/service.py:110 get_contract (record + fresh content_revision).
  Session truth: ArtifactRecord (app/services/artifact_registry.py:84) — crs only if descriptor
  carried it (_descriptor_fields :260 reads descriptor["crs"] which never exists → always empty).
  Lineage edges = record.inputs (artifact_registry.py:93) + ArtifactGraph (:125); provenance
  RunManifestBuilder carries input_dataset_fingerprints (app/services/provenance/manifest.py:41,164).

[data role binding + qualification]
  workflow_schema.resolve_data_roles (app/services/gis_harness/workflow_schema.py:391,467) — binds
  roles from resolver_profile fields/geometry; temporal obligation reads hasTimeField/
  temporalObservationCount (:545-587) — keys nobody produces → always "unknown" branch.
  data_qualification.qualify_data_role (app/services/gis_harness/data_qualification.py:257): 5-state
  gate per role — geometry kind (:338), numeric-field (delegates to precondition, locally enriched
  with numericFields :371-381), denominator hints (:393), null_ratio (:417-438 — reads
  fields.null_ratio which ONLY the full profiler emits; descriptor path silently has no null facts),
  sample_size ≥1 (:441), projected CRS (:453-467 only when obligation declared), temporal (:470).
  Remediation op vocabulary backed by real fns (REMEDIATION_OP_BACKING :58).

[resolver] AlgorithmResolver.resolve (app/lib/gis/algorithm_resolver.py:228)
  capability → AlgorithmRegistry.algorithms_for_capability (algorithm_registry.py:235, priority-sorted)
  → _check_candidate (:101) hard gates, in order: native (:113), tool registered (:117),
     geometry_requirements (:127 — only if the algorithm DECLARES geometry_requirements),
     input_artifact_types vs profile.artifactType (:133), min_features (:144), max_features_hint (:154),
     required_fields (:161), crs_class gate (:171-188, needs profile.crs), scientific_preconditions
     five-verdict gate (:190-210)
  → cost tiebreak score_algorithm (app/lib/gis/cost_model.py:92) under infer_execution_policy
     (:62) driven ONLY by featureCount/export/deterministic
  → algorithm_hint promotion (:313-340, e.g. "kriging" from user text), algorithm fallback chain
     (:377-402), capability fallback (:406-432), required_transformations from ;transform= hints (:365).
  Call sites:
   - planner._resolve_capabilities (app/services/gis_harness/planner.py:316) — DRAFT stage: profile=None
     (plan_from_intent :352 has no profile param; intent tool calls it bare, tools.py:464)
   - planner.finalize_with_profile (:629) → re-resolve (:680) — REAL profile here, from
     profile_from_descriptor(descriptor) (tools.py:649) or MapSpec source profile (tools.py:656-668)
   - plan_candidates._check_tools (plan_candidates.py:258) — profile threaded from
     workflow_compiler.generate_plan_candidates (workflow_compiler.py:296-298)
   - workflow_engine.resolve_step_tool (workflow_engine.py:155) — EXECUTION-TIME re-resolution:
     NO profile passed (only available_tools) → all profile-dependent gates inert at run time.

[scale/backend]
  ScaleProfile + select_backend (app/lib/gis/backend_selection.py:37,91) — variant windows
  (BackendVariant.min/max_features, algorithm_registry.py:70-71) + scale_tier + runtime strategy.
  Consumed ONLY inside tools, per-call, count recomputed ad hoc:
  advanced_spatial.py:297 (kriging counts points by iterating features AGAIN :289-296),
  terrain_analysis.py:58, point_pattern_tools.py:90, network_tools.py:45, spatial_stats.py:78,
  remote_sensing.py:97.

[execution plan]
  ExecutionPlan/ExecutionNode exist: app/services/geocompute/plan.py:217/150 — NodeCategory.INTERPOLATION
  (:46), ResourceBudget (:130), semantic fingerprint (:185). Admission: executor._admission_check
  (app/services/geocompute/executor.py:371) vs plan.budget; governor concurrency admission (:411+).
  Durable/Celery: geocompute/tasks.py:21 run_geocompute_node via submit_durable_job (durable.py:62).
  ⚠ Separate plane: the harness/tools path (advanced_spatial tools, preferred_execution_policy="CELERY"
  declared on interpolation descriptors, algorithms/interpolation.py:43 etc.) does NOT go through
  ExecutionPlan; ops._op_interpolation (app/services/geocompute/ops.py:522) wires only idw|kriging (:553).
```

## (b) Field-by-field: profile datum → used by resolver?

| # | Datum (requirement) | Produced by | In resolver profile? | Gates a candidate today? |
|---|---|---|---|---|
| 1 | Geometry type | RefDescriptor.geometry_types (ref_descriptor.py:43); V3 geometry_type_counts (profile.py:161) | Y as `geometryTypes` (dataset_profile.py:112, profiler_projection spatial_meta_profiler.py:151) | PARTIAL — only if algo declares `geometry_requirements`; none of the 17 interpolation descriptors do (algorithms/interpolation.py has no geometry_requirements line); point support enforced indirectly via input_artifact_types only when artifactType known (resolver :133) |
| 2 | CRS class / metric requirement | Full profiler only (spatial_meta_profiler.py:187); descriptor path hardcodes None (:148); V3 has crs (profile.py:286) + classify_crs_kind (:502) — never wired | NO on the descriptor path that actually feeds finalize (tools.py:649); Y only if full profile_geojson_source ran and FC declared a crs | YES when present: resolver crs_class gate (:171-188) rejects e.g. OK/UK/TIN/RK (crs_class=PROJECTED_REQUIRED, algorithms/interpolation.py:104,193,234,315) on geographic CRS with reprojection hint; preconditions projected_crs_required / local_metric_crs_required (scientific_preconditions.py:97,114) |
| 3 | Row/feature count | RefDescriptor.feature_count; V3 row_count | Y `featureCount` | YES: min_features (kriging 8 :81, UK 12 :173, RBF 3 :133, TIN 3 :214, trend 6 :255, RK 8 :295); max_features_hint render cap (:154); execution-policy inference (cost_model.py:83-88); scale_tier (:118) |
| 4 | Raster dims/bands | V3 RasterProfileData (profile.py:211) via profiler.profile_raster_file (profiler.py:229); ArtifactContract RasterShape (artifact_contract.py:88) | NO — to_resolver_profile drops raster entirely (dataset_profile.py:110-121); precondition fact `bandCount` never populated by anyone (grep: only consumer scientific_preconditions.py:172-187) | NO |
| 5 | Temporal observation count | V3 temporal_fields (profile.py:622); full-profiler temporalProfile (spatial_meta_profiler.py:312); temporal service (app/services/temporal/profiler.py:252) | NO — `temporalObservationCount`/`hasTimeField` have zero producers (grep: only readers workflow_schema.py:545-587, data_qualification.py:133-147, anti_claim test synth :175,185) | NO — min_temporal_observations:N (:148) and temporal_field_required (:137) always PASS-on-unknown |
| 6 | Numeric fields | V3 numeric dtype fields (profile.py:131); descriptor field_schema types | NO as a resolver fact: `numericFields` key not emitted by to_resolver_profile nor by spatial_meta_profiler; data_qualification derives it into a LOCAL copy only (:371-381) | WEAK/BUG-PRONE: `numeric_field_required` (scientific_preconditions.py:72) returns INSUFFICIENT_DATA when fields are known but `numericFields` key absent — the exact shape every descriptor-derived resolver profile has. Statistics algos declaring it (algorithms/statistics.py:90,132,…) are one dry-run away from false rejection; interpolation algos currently dodge it (RK/UK declare only min_numeric_samples, which falls back to featureCount :242-266) |
| 7 | Categorical fields | V3 categorical (dataset_profile.py:54); descriptor string types | NO (only as generic `fields` dict for required_fields membership :161) | NO (binary_field_required :228 same trap as #6 — algorithms/statistics.py:385,421) |
| 8 | Missing/null profile | V3 null_count/null_rate (profile.py:132-133,473); full profiler null_ratio (spatial_meta_profiler.py:276); descriptor null_count (ref_descriptor.py:229) | NO to resolver. data_qualification reads `null_ratio` (:421) — present only on the FULL profiler shape, so the descriptor path used at finalize has no null facts and the check degenerates | NO (no interpolation precondition consumes null rates; IDW/KRIGING drivers handle nulls silently) |
| 9 | Uncertainty REQUIREMENT (demand) | Nowhere — only supply side exists: descriptor.uncertainty_outputs (algorithm_registry.py:132), kriging variance outputs (advanced_spatial.py:325-341) | NO — no demand vocabulary, no gate "user needs variance ⇒ prefer geostatistical family" | NO |
| 10 | Resource budget | cost_model thresholds (cost_model.py:34-37); ScaleProfile (backend_selection.py:48-51); ExecutionPlan ResourceBudget (plan.py:130); estimated_bytes on descriptor (ref_descriptor.py:27)/V3 | PARTIAL: featureCount drives policy/scale; estimated_bytes reaches only a backend_selection memory note (:160-163); budget exists only on the geocompute plane, never seeded from profile | PARTIAL: variant windows (kriging numpy_batched vs scipy_linalg, algorithms/interpolation.py:117-122) chosen at tool layer with a re-counted n (advanced_spatial.py:297) |
| 11 | Duplicate count (interp-specific) | NOWHERE profiled (V3 has unique_count per field, not coordinate dedup); driver does deterministic dedup (geo_analysis/interpolation.py:155) | NO | NO — kriging discovers duplicates only at run time (MIN_SAMPLES post-dedup, geo_analysis/kriging.py:148) |
| 12 | Spatial distribution / clustering | V3 evidence exists to derive it (extent, impossible/zero-zero coords profile.py:164-165) but no distribution statistic | NO | NO |
| 13 | Trend (drift) presence | V3 field mean/std exist; no trend statistic | NO | NO — OK vs UK is decided only by min_features (8 vs 12) and hint; UK's "trend明显的场 OK 有系统偏差" (algorithms/interpolation.py:100) is prose, not a gate |
| 14 | Anisotropy | Implementation-only, opt-in params (geo_analysis/kriging.py:314-353); directional_variogram algorithm exists (algorithms/interpolation.py:375) | NO | NO |

## (c) Gaps ranked by impact

1. **CRS never reaches the resolver on the live path** — `profile_from_descriptor` hardcodes `crs=None` (spatial_meta_profiler.py:148) and `RefDescriptor` has no crs field (ref_descriptor.py:13-66). The `crs_class` gates on OK/UK/TIN/RK (resolver :171-188) are therefore dead code for descriptor-driven finalize — the PROJECTED_REQUIRED discipline only survives inside tool drivers. Fix is a one-field addition to descriptor + projection.
2. **Execution-time re-resolution is profile-blind** — `workflow_engine.resolve_step_tool` (workflow_engine.py:155) re-resolves with `available_tools` only, so a recorded "kriging" step rerun against 5 points, or a polygon layer, gets none of the min_features/geometry/CRS gates. The resolver IS the designated runtime adjudication point (ADR-0092 A5, workflow_engine.py:130-141) but receives no facts there.
3. **Precondition fact keys have no producers** — `numericFields`, `valueVariance`, `bandCount`, `bandSemantics`, `binaryFields`, `numericSampleCount`, `temporalObservationCount`, `hasNegativeWeights` are consumed by scientific_preconditions.py and workflow_schema.py but no production code emits them (grep evidence in §b rows 4-7). Net effect: the entire ADR-0099 scientific gate layer mostly PASSes-on-unknown; and `numeric_field_required`/`binary_field_required` can FALSE-REJECT when fields are known but the (never-emitted) keys are absent — scientific_preconditions.py:72-81,228-239 vs the actual profile shape of spatial_meta_profiler.py:146-158.
4. **V3 profile is an island** — `DatasetProfileV3` (the only shape with null rates, variance stats, raster bands, coordinate-quality evidence, temporal fields) is computed by `DatasetProfiler` (profiler.py:102) but only surfaced through the `profile_dataset` agent tool (data_discovery.py:118); `ArtifactContract.profile_ref` (artifact_contract.py:198) is declared and never populated; nothing converts V3 → resolver facts (V2's `to_resolver_profile` itself omits numeric/raster/null, dataset_profile.py:104-121). Meanwhile the resolver consumes the LEAST informative shape.
5. **Interpolation candidate set is fragmented by capability, and nothing selects across it** — a generic "interpolate" plan only ever plans `spatial_interpolation` (planner.py:448-471 keyword gate `_interpolation_query_signals` :95-108, hint = literal "kriging" in text); TIN/RK/trend/model_compare live under separate capabilities (`triangulation_interpolation`, `regression_kriging`, `trend_surface`, `interpolation_model_selection`, capabilities/interpolation.py:27-59) that the recipe/intent layer never adds, so point-count/measure/uncertainty reasoning cannot move the request from IDW to RK or to model_compare.
6. **ScaleProfile is fed by ad-hoc recount, not by profile** — every tool re-iterates features to count points (advanced_spatial.py:289-297) instead of receiving `featureCount` from the descriptor/profile that already exists; backend variant choice therefore can't be made at plan time and isn't in plan evidence.
7. **ExecutionPlan plane has no profile linkage** — `ExecutionNode.estimate` (plan.py:120-127) is caller-supplied; admission (executor.py:371) can be satisfied with `unknown` estimates; no bridge from DatasetProfileV3/ArtifactContract.feature_count to ResourceEstimate, and `ops._op_interpolation` (ops.py:522) supports only idw|kriging vs the 17 declared interpolation algorithms.
8. **Null-rate and denominator semantics degrade silently** — data_qualification's null_ratio check (:417-438) and quantile-dependent checks only work on full-profiler profiles; the descriptor path that finalize actually uses has neither, and nothing flags the evidence loss (fields_status=unknown covers schema truncation but not stat absence).
9. **Duplicate-coordinate and spatial-distribution facts don't exist upstream** — kriging's ≥8 gate applies post-dedup at runtime (geo_analysis/kriging.py:148, interpolation.py:155); a dataset of 200 stacked duplicates resolves "OK" and fails at execution.

## (d) Recommended additive integration seam

1. **One adapter, not a second registry**: extend `DatasetProfile.to_resolver_profile` (app/lib/gis/dataset_profile.py:104) — the declared single adapter — to emit the fact keys the precondition library already defines: `numericFields`, `categoricalFields`→`binaryFields` (0/1 unique domain from V3 unique_count≤2), `hasTimeField`/`temporalObservationCount` (from V3 temporal_fields + row_count), `bandCount`/`bandSemantics` (V3 raster), `crs`. Do NOT invent new fact names; `scientific_preconditions.py:5-8` is the fact dictionary.
2. **Close the CRS hole at the source**: add `crs` to `RefDescriptor` (ref_descriptor.py:13) captured at `compute_descriptor` (:273) from FC `crs` member / ingest `declared_crs` (pipeline.py:172 normalizes it already); then `profile_from_descriptor` (spatial_meta_profiler.py:148) and `from_ref_descriptor` (artifact_contract.py:453) stop hardcoding empty. `classify_crs_kind` (profile.py:502) is the shared classifier — reuse, don't fork.
3. **Make V3 reachable, not universal**: when `finalize_with_profile`/`qualify_workflow_data_roles` need facts beyond the descriptor (deep=true), call the existing `DatasetProfiler.profile_session_ref` (profiler.py:102, revision-keyed cache already handles freshness: :123-127) and project V3→camelCase through the extended `to_resolver_profile`; keep descriptor projection as the O(1) default so the zero-scan budget holds. Populate `ArtifactContract.profile_ref` in the same write path so profiles become addressable, second-class metadata (no new store — the LRU + content_revision keying is the freshness mechanism).
4. **Fix the execution-time seam**: thread an optional `profile` (or a lazy `profile_provider` callable taking session_id+ref) into `WorkflowEngine.resolve_step_tool` (workflow_engine.py:127) — the resolver signature already accepts it (algorithm_resolver.py:232) and unknown-facts semantics keep old manifests byte-compatible.
5. **One interpolation selection surface**: instead of moving algorithms between capabilities, add a *plan-time* interpolation selector that (a) keeps `spatial_interpolation` as the planned capability, and (b) after resolver hard gates, consults profile facts to set `algorithm_hint` and capability additions: duplicates>threshold or n<8 → idw/rbf; measure numeric + n≥8 → kriging family; trend fact (future: V3 residual variance on coordinates) → universal_kriging; covariate fields≥2 → regression_kriging; uncertainty demand → prefer `uncertainty_outputs` non-empty or `interpolation_model_selection`. Home it beside the existing hint projector `_interpolation_query_signals` (planner.py:95) so text hint stays a hint and facts outrank it — the resolver's hinted-promotion block (algorithm_resolver.py:313-340) already refuses hinted algorithms that fail hard gates.
6. **Feed ScaleProfile from the plan, not the tool**: pass `ScaleProfile(feature_count=profile.featureCount, estimated_bytes=descriptor.estimated_bytes)` into plan evidence by calling `select_backend` (backend_selection.py:91) at finalize time (it's already a pure function with no I/O) and attach `BackendDecision.to_diagnostic()` (:71) to the plan; tools keep their runtime re-check as a guard, not the first computation.
7. **Bridge to ExecutionPlan minimally**: when compiling `NodeCategory.INTERPOLATION` nodes (ops.py:522), seed `ExecutionNode.estimate.rows` from profile featureCount and `dataset_fingerprints` from `ArtifactContract.fingerprint.content` — admission (executor.py:371) then stops passing vacuously on unknown. Wire the remaining interpolation algorithms by extending `_op_interpolation`'s method dispatch (ops.py:531-553) to the existing `app/lib/geo_analysis` implementations (tin_interpolation.py, trend_surface.py, regression_kriging.py, rbf_interpolation.py) rather than declaring new ops.

Non-goals (already correct): do not move gates out of AlgorithmResolver (backend_selection.py:18-20 freeze is deliberate); do not duplicate precondition semantics in data_qualification (it already delegates, data_qualification.py:234-254).
