# G18 — Data Delivery (ref/revision-driven multi-format delivery) — Independent Review

- Branch: `zcode/goal-g18-dataset-delivery-20261110T131127Z`, base `816705218a5aa6202bf15d745cb31cddb3437f44` (7 commits)
- Worktree reviewed read-only: `C:\Users\wangj.KEVIN\projects\webgis-worktrees\goal-g18-dataset-delivery-20261110T131127Z`
- Reviewer did not author this code. All claims below were re-derived from the code and, where marked
  **[measured]**, from ad-hoc probes run outside the worktree.

---

## Verdict

**Conditional pass — fix 3 P1s first (unbounded `ids`-scope read; receipt under-reports applied
transformations; retry dispatches to a non-existent Celery task).** The security, version-binding,
atomicity and download-authorization cores are genuinely solid and well tested; the defects are in
resource discipline, evidence completeness and one dead dispatch path.

---

## Blocking findings

### P1-1 — Unbounded full materialization to sample 50 rows (violates invariant 3, "bounded reading")

- `app/services/data_delivery/scope.py:228` — `sample = snapshot.features()[:50]`
- `app/services/data_delivery/source_resolver.py:289-302` — `SourceSnapshot.features()` for the parquet
  carrier does `list(self.iter_feature_batches(READ_BATCH_ROWS))`, i.e. it reads **every** row group of
  the whole file into Python dicts, then throws all but 50 away.
- Reachable from: `POST /data-delivery/jobs` → `_run_delivery_job` → `_execute_delivery_sync`
  (`service.py:476`) whenever `scope.type == "ids"` and the source is `fabric_parquet`
  (`is_table=False`). `preview` does **not** hit it (`negotiate_scope` only), so the blowup happens
  inside the writer thread after submit.
- Repro **[measured]**: 100 000-row / 2.1 MB GeoParquet artifact →
  `snapshot.features()[:50]` = **6.42 s, 92.7 MB peak traced Python memory**;
  `next(snapshot.iter_feature_batches(50), [])` = **0.011 s, 0.1 MB**. Cost is linear in the source
  artifact, so a fabric-parquet artifact in the hundreds of MB becomes a multi-GB allocation in the
  shared API process. It also happens *before* the "no stable ID" check, so it is paid even when the
  delivery is about to be refused.
- Impact: memory/GC exhaustion (self-inflicted DoS surface) and a direct violation of the stated
  bounded-reading discipline. The bounded pattern already exists 40 lines away
  (`sample_parquet_geometry`, `source_resolver.py:538-552`).
- Fix: sample one bounded batch — `sample = next(snapshot.iter_feature_batches(50), [])` — or reuse the
  geometry summary already sampled in the preview/execute phases. Consider hoisting the ID-field
  detection into `negotiate_scope` so it also runs (boundedly) at preview time.

### P1-2 — Receipt under-reports transformations that were declared in preview and actually applied

- `app/services/data_delivery/service.py:522-545` — `losses_applied` is a hand-maintained conditional
  list; `service.py:546-553` gates the nested-value code on `snapshot.crs is None`, a condition with no
  causal relationship to complex-value encoding.
- `loss.py:225` (`z_preserved`), `loss.py:248` (`bool_to_string`), `loss.py:265` (`int64_downgrade`) are
  emitted at preview but appear in **no** receipt path.
- Repro **[measured]** (real service, real writers, real read-back):
  - CSV from a source with known CRS + one nested property:
    `preview.losses = ['nested_value_json_encoded']`,
    `preview.required_acknowledgments = ['nested_value_json_encoded']`,
    the CSV really contains `{"k": 1}`, and **`receipt.losses_applied == []`**.
  - Shapefile with long field names + bool + >32-bit int + Z:
    `preview.losses = ['bool_to_string','field_name_truncated','int64_downgrade','z_preserved']`;
    `receipt.losses_applied = ['field_name_mapped','field_name_truncated']`.
- Impact: the receipt is the post-hoc evidence artifact (`data_delivery_schema.py:220-236`, "实际证据,
  非承诺"). A user auditing "what did this delivery actually change to my data" gets an incomplete
   answer, and the module's own one-to-one claim (`loss.py:1-6`, "保证声明与实际改变一一对应（WP11
  oracle 验证）") is false in practice. No test covers the correspondence — the tests only assert a
  handful of individual codes.
- Fix: derive the receipt list from the `LossAnalysis` that the executor already recomputes at
  `service.py:435` (e.g. `[l.code for l in losses.losses]`) unioned with the writer-measured codes,
  instead of a parallel hand-written list that has already drifted. Keep the writer's measured codes
  (`formula_prefix_sanitized`, `field_name_mapped`) as the authoritative ones where they carry counts.

### P1-3 — Retry dispatches to a Celery task that does not exist → permanently-`queued` job

- `app/services/data_delivery/service.py:280` — `dispatch_spec={"task": "data_delivery.execute", ...}`.
  Repo-wide grep finds exactly one occurrence of that string (this line); `celery_app.tasks` contains
  no delivery task **[measured]**; the G18 executor is in-process only
  (`asyncio.create_task(_run_delivery_job)`).
- `submit_delivery` does not pass `max_retries`, so `DurableJobStore.build` defaults it to 3
  (`app/services/jobs/store.py:161`) and `app/services/jobs/views.py:142-143` therefore advertises a
  **failed** data-delivery job as `retryable=True`.
- Repro: fail a delivery (e.g. the writer-crash fault injection) → `POST /api/v1/jobs/{id}/retry` →
  `start_retry` sets `queued` → `celery_app.send_task("data_delivery.execute", args=[params])`
  (`app/api/routes/jobs.py:277-283`) → no consumer → the job sits in `queued` forever. Without a
  broker, `send_task` raises and the job is honestly marked failed, so the bug only manifests where a
  broker/worker exists — which is the production shape.
- Impact: a non-terminal job that can never advance — precisely the failure mode that route's own
  comment calls worse than not offering retry ("只改状态而不入队会留下一个永远不推进的 job（比不提供
  retry 更糟）"). Also violates invariant 9's "failure terminal states reliable" in the retry
  direction.
- Fix (any one): omit `dispatch_spec` entirely (retry then returns `no_dispatch_spec`, an honest
  refusal); or pass `max_retries=0` on create; or register a real `data_delivery.execute` Celery task.

---

## Advisory

### P2

**P2-1 — `MAX_OUTPUT_BYTES` is declared but never enforced on real output.**
`contracts.py:21` caps output at 512 MiB; `service.py:138-139` only clamps the *estimate*; the sole
runtime guard is `_check_output_budget` (`service.py:803-808`) against `MAX_TEMP_DISK_BYTES` (1 GiB),
and it is **not called on the fast path** (`service.py:481-491`). `docs/features/data-delivery.md`
advertises "输出 512MiB". Fix: enforce the output cap on the sum of published member bytes with
`ERR_OUTPUT_BUDGET_EXCEEDED`, and call the budget check around the fast-path copy.

**P2-2 — The "additive" contract snapshot refresh sweeps in unrelated API drift.**
`tests/quality/snapshots/openapi.json` (+4 686/−… lines) and `api_contract_fields.json` contain 11
changes with no corresponding code change in this branch: `UndoAgentCommitBody`, `/export/diagnostics`
description + parameters, upload `attribution`/`license`, `HealthResponse.agent_runtime_mode`/`_reason`,
`PatchComponentBody.component_type`, `VectorPdfRequest.page_profile`,
`VectorPdfExportResponse.constraint_layout`. Master's snapshot was already stale; regenerating it
absorbed that drift. G18's *own* delta is purely additive (5 paths, 16 schemas, 0 removals) and the
byte-exact gate passes, so this is hygiene/reviewability, not correctness — but it destroys diff
isolation and collides directly with open PR #2022, which also regenerates the same file. Recommend
splitting the unrelated drift into its own commit.

**P2-3 — Open-PR file overlaps.** `frontend/components/sidebar/data-sources-tab.tsx` is touched by both
G18 and PR #2032 (UI redesign); `frontend/messages/{en-US,zh-CN}/project.json` by G18 and #2022;
`app/main.py`, `app/tools/__init__.py`, `docs/api-docs.md` by G18/#2021/#2022;
`tests/quality/snapshots/openapi.json` by G18/#2022. G18's frontend edits themselves are additive-only
(new optional props, new buttons, one new dialog component, new i18n namespace files) — verified by
diff — so the risk is merge conflict, not semantic clash.

### P3

- **P3-1 `requested: 0` for filter scope.** `scope.py:213` (`ScopeStats.requested` defaults to 0) +
  `service.py:939-953`. **[measured]** receipt:
  `{"type":"filter", ..., "delivered":3, "source_rows":5, "requested":0}` — reads as "0 requested".
  Fix: set `requested = delivered` (or omit the key) for filter plans.
- **P3-2 `durable:"oversized"` reported for non-size failures.** `store.py:143-148` swallows every
  exception into `None`, and `service.py:636` maps `None → "oversized"`. A `project_artifact` source
  submitted without `project_id` yields an empty `owner_scope`, which `data_object.py:189-191` rejects
  with `DataObjectError` — the receipt then claims "oversized" for what is a missing-owner-scope
  failure. Fix: distinguish the two outcomes.
- **P3-3 Project branch of `_job_authorize` inherits the "legacy public project is world-visible"
  semantic.** `service.py:1018-1023` → `project_service.py:23-62`: a project with `owner_id IS NULL`
  and `org_id IS NULL` is readable by *any* caller, including anonymous ones (the download route only
  requires *some* credential, `data_delivery.py:172-173`). A job registered to such a project is
  therefore readable/downloadable cross-user. Not exploitable in a deployment where projects always
  carry an owner/org, and it matches the repo-wide list/detail semantics — but it is a real
  authorization widening for the download path specifically.
- **P3-4 `fast_path_byte_copy` is a free-form marker inside `losses_applied`** (`service.py:490`), a
  field typed and documented as a bounded loss-code vocabulary (`contracts.py:62-79`). Consumers that
  filter by vocabulary see an unknown code.
- **P3-5 NaN/Inf → `""` in CSV is an undisclosed value transformation.** `writers.py:402-404`. It is
  documented in the schema sidecar, but the preview loss list never mentions it, so it is not part of
  the pre-execution disclosure (invariant 4's "no silent fixes").
- **P3-6 CSV `geometry_wkt` may be reprojected but the sidecar records no CRS.**
  `writers.py:465-478` — the sidecar has no `crs` key even though the geometry may have been
  transformed to `crs_out`. The receipt carries it, but a user who keeps only the CSV + sidecar has
  no CRS.
- **P3-7 Non-`DeliveryError` escapes → 500 instead of 404/422.** `service.py:1053`
  (`DeliveryReceipt(**receipt_data)`) raises `ValidationError` on a readable-but-malformed receipt,
  and `service.py:1048`/`1147` call `.get()` on `result_summary` without a dict check. Reachable only
  with a corrupted/foreign receipt file.
- **P3-8 `_list_delivery_jobs_sync` is N+1** (`service.py:1116` — one new session + one receipt read
  per row, up to `limit=200`).
- **P3-9 Cancel in the `atomic_publish → mark_succeeded_sync` window leaves an orphan final
  directory.** `atomic_publish` (`service.py:605`) succeeds, then `mark_succeeded_sync` is refused
  (`cancelling → completed` is not in the transition table) and the job converges to `cancelled`. The
  files are fully written (so invariant 9's "no half-published files" holds and download is correctly
  404), but the published directory is never cleaned and `discard_staging` is a no-op at that point.
- **P3-10 A job whose runner never starts stays `pending` forever.** If the process dies between
  `DurableJobStore.create` and `asyncio.create_task`, the only recovery is a resubmit of the identical
  spec (the comment at `service.py:284-286` acknowledges this). There is no reaper for
  runner-less `pending` rows.
- **P3-11 `X-Content-Sha256` echoes the receipt's recorded hash** (`data_delivery.py:184-185`) rather
  than recomputing it at download time.
- **P3-12 `probe_format("csv")` gates the CSV writer on GDAL's CSV driver** (`contracts.py:131`,
  `164-175`) even though `CSVWriter` uses Python's `csv` module — a false-unavailable path if a build
  lacks that driver.
- **P3-13 `ScopePlan.id_field` is dead.** `negotiate_scope` never assigns it, and `_ids`
  (`scope.py:238`) re-derives identity via `extract_stable_id(f, None)` rather than the detected
  field. Consistent today, but the two can disagree when some features carry top-level `id` and others
  only `properties._wfid`.
- **P3-14 The fabric-parquet byte-copy fast path has no UI entry.** The sidebar materialize path uses
  the GeoJSON session lane (`res.ref_id`), so the fast lane is reachable only via REST/tools. Not a
  defect — the delivered data is still real and authorized — but the headline optimization is
  API-only.
- **P3-15 `id_field` in `DeliveryScope` is accepted and ignored** (see P3-13); either honour it or
  reject it to avoid implying it is used.

---

## Verified correct

1. **Source authorization fails closed for all four kinds, before any read.** `session_ref` /
   `fabric_parquet` / `table_rows` → `verify_session_access` → `verify_session_owner`;
   `project_artifact` → `ProjectService.get_project_with_auth`. Unknown kind → typed failure
   (`source_resolver.py:483-484`).
2. **No path traversal from user input.** `fabric_parquet_path` (`artifact_registry.py:384-399`)
   charset-whitelists *both* `session_id` and the artifact id (`..` and separators rejected at the
   boundary); refs are never string-concatenated into paths; `delivery_id` is
   `secrets.token_hex(16)`; `filename` is ASCII-sanitized (`contracts.py:110-113`).
3. **Missing source CRS never defaults to 4326.** GeoJSON is hard-blocked (`loss.py:167-172`);
   other formats require an explicit `allow_unknown_source_crs` and then disclose
   `crs_label_unknown` in *both* preview and receipt (verified by test and probe).
4. **GeoJSON stays RFC 7946 / WGS84.** Target CRS is validated to 4326/WGS84/OGC:CRS84
   (`loss.py:173-178`); the implicit reprojection path really transforms coordinates
   (`_make_transform` → shapely + pyproj `always_xy=True`); `_verify_output` re-asserts
   "transformed ⇒ target is 4326" after writing (`service.py:920-923`); control-point oracle exists
   (`test_writers_roundtrip.py:212-259`) and passes.
5. **Version binding holds.** Frozen fingerprint from submit is re-checked at execution
   (`service.py:354-360`); `verify_unchanged` re-reads the session payload and compares fingerprints,
   re-hashes the parquet file with sha256, and re-verifies the artifact revision digest
   (`source_resolver.py:304-328`). The snapshot is resolved **once** and handed to the executor, so
   the scope filter and the writer provably read the same version.
6. **"full" never means the source set.** Truncated snapshots produce "当前快照含 N 行（物化时被截断；
   total=…）—— 不代表原数据全集" **[measured]**; estimates are labelled "估算非实测；字节为格式启发式";
   a 100-row sample of a 5 000-row result is never called full.
7. **Selection-integrity gate works.** `matched_count > len(ids)` without a replayable predicate is
   blocked; `subset_ack` delivers the listed subset with `subset_of_selection=true` in the receipt;
   with a predicate, the full selection is replayed (80 rows, not the 50-ID prefix) — all covered by
   tests.
8. **Zero rows after filtering → typed failure, never an empty file sold as success**
   (`service.py:554-557`); GeoParquet `close()` refuses an empty dataset (`writers.py:383-387`).
9. **`_verify_output` uses genuinely independent decode paths** — streaming `json.JSONDecoder` for
   GeoJSON, `pyarrow.parquet` metadata for GeoParquet, `pyogrio.list_layers`/`read_info` for GPKG and
   FGB, ZIP extraction + `pyogrio` per `.shp` for the shapefile ZIP, `csv.reader` for CSV — and
   compares read-back rows to the writer's count. The truncation fault-injection test proves it
   catches a real half-write.
10. **Shapefile ZIP is complete and non-duplicative.** One `.shp/.shx/.dbf/.prj/.cpg` set per layer,
    member names unique across batches and layers, counts conserve **[measured]**: mixed
    point/line/null-geometry → 3 written = 3 read back, and null-geometry rows survive (they land in
    an `other` layer). Z is genuinely preserved (GDAL writes `Point Z`), so the `z_preserved`
    disclosure is accurate.
11. **Shapefile field-name handling is injective and bounded.** Auto-map is ≤10 bytes with numeric
    disambiguation; explicit maps are validated for coverage, target legality and injectivity
    (`loss.py:65-130`); output-name collisions are refused (`service.py:698-700`).
12. **CSV conventions are stable and documented**: field order preserved, UTF-8, `null → ""`,
    `bool → true/false`, `NaN/Inf → ""` (sidecar-documented), complex values JSON-encoded,
    spreadsheet-safe sanitization counted per cell and recorded; the sidecar is a downloadable member.
13. **GeoParquet geo metadata carries the CRS** and the fast path is a true byte copy — output bytes
    are identical to the source artifact and the source sha is recorded as the receipt fingerprint
    (test + read-back).
14. **Download is a properly gated read.** Member must match a receipt entry verbatim with no `/`,
    `\` or `..` (`service.py:1160-1167`); ASCII-safe `Content-Disposition`; correct MIME with the
    `.json` override for the CSV sidecar; 404 for unauthorized / not-completed / missing member /
    missing file. `DataObject` manifest reads enforce `owner_scope_allows`
    (`data_object.py:107-119`, `lakehouse.py:141-143`) — **the content hash is not a cross-user
    download token.**
15. **Idempotent reuse cannot bypass authorization.** The owner component of the execution key is
    `project_id || session_id || user_id`, and every submit re-runs the full preview (hence the full
    source authorization) before the reuse short-circuit; a reused row is only readable by callers who
    pass `_job_authorize`.
16. **Cancel/failure terminal states are reliable.** Two-phase cancel (DB fact + in-process token),
    token checked between batches; the transition table forbids `cancelling → completed`, so a late
    success converges to `cancelled`; staging is discarded on every failure and cancel path; no
    "correct-extension half-written" final directory is producible (publish is a same-volume
    `os.replace` of a fully-verified staging tree).
17. **`_job_authorize` fails closed for anonymous callers** (`user_id=None`, `owner_token=None`):
    `authorize_session_write` returns False for a user-bound conversation with no user
    (`auth.py:174-176`), so the session branch cannot be satisfied by a bare session id.
18. **Old protocols untouched.** No diff in `export`/`map`/atlas code; `/data-delivery` is a distinct
    prefix; the materialization change is additive-only (`path`/`bytes`/`content_sha256`/`durable`
    passthrough) and `data_delivery` is an additive `DataObject` kind
    (`data_object.py:182-186`).
19. **Pi surface is honest.** Tools registered with `side_effect="pure"` vs `"state_mutation"`,
    `produced_refs`, `output_semantic_type="ref"`, explicit `failure_modes`; loss acks are enforced
    server-side and the tool path cannot bypass them (covered by
    `test_tool_submit_enforces_ack`).
20. **Row cap enforced twice** — preview blocking (`service.py:150-154`) and a runtime backstop
    (`service.py:508-511`).

---

## Test evidence

| Command | Result |
|---|---|
| `python -m pytest tests/delivery -q -p no:cacheprovider --no-cov` | **48 passed** (146.86 s) |
| `cd frontend && npx vitest run components/data-delivery test/i18n` | **6 files / 36 tests passed** (20.08 s) |
| `python -m pytest tests/quality/test_api_compatibility.py tests/unit/api_contract/test_field_contract.py tests/unit/api_contract/test_response_model_coverage.py -q` | **17 passed** (33.65 s) — byte-exact OpenAPI snapshot gate green |
| `python -m pytest tests/test_endpoint_scope_matrix.py -q` | **8 passed** (25.04 s) |

Ad-hoc probes (scripts kept outside the worktree, in `/tmp/g18probe`, worktree untouched):

- Memory/timing of `snapshot.features()[:50]` vs a bounded first-batch read on a 100 k-row parquet
  (P1-1).
- Preview-vs-receipt loss correspondence for CSV (nested value) and Shapefile (bool / int64 / Z) —
  real `preview_delivery` → `submit_delivery` → runner → `get_delivery_job` → read-back (P1-2).
- Shapefile ZIP member inventory, null-geometry handling and Z preservation via the real
  `ShapefileGroupWriter` / `PyogrioWriter` paths.
- Filter-scope receipt shape and 100-row-sample labelling.
- `probe_all_formats()` / `writer_versions()` runtime probe.
- Celery task registry check (no `data_delivery.*` task) for P1-3.
- Old-vs-new `openapi.json` set-difference and recursive diff (proved G18's own delta is additive and
  isolated the 11 unrelated drift entries).

Note on the passing tests: `tests/delivery` is a genuinely good suite — real SQLite (sync + async),
real pyogrio/pyarrow/pyproj, real fault injection, real control points. It does **not** contain a test
for preview↔receipt loss correspondence, for the `ids`-scope read cost, or for the retry path, which
is exactly where the three P1s live.

---

## Environment-blocked items

- None of the G18 tests are environment-blocked on this machine.
- The two known baseline failures named in the task were confirmed pre-existing and unrelated without
  needing a stash: the ADR 0286/0287 duplicates exist identically at base `816705218` and on the
  branch (`git ls-tree` on both), and G18 adds zero files under `docs/adr/`;
  `tests/unit/gis_tool_conformance/test_bounded_modelops_results.py` (missing `fcntl` on Windows)
  touches no G18 file.

---

## Round 2（复审，独立 reviewer #2，未参与实现与一轮评审）

**Verdict: PASS** —— 三个 P1 实质解决（P1-1/P1-3 完全；P1-2 修复漏报，残留一项
P2 多报 NEW-1，已由作者当场修复并加回归）；若干 P3 建议，无阻断。

### 逐项确认

- **P1-1 有界读取 —— 已修复且完整。** scope.py ids 路径改用
  `snapshot.sample_features(50)`；`features()` 无残留大表调用方；
  `iter_batches` 惰性读取上界为单个 row group（dict 转换受 limit 截断）；
  table_rows 分支为内存切片（正确）。回归：monkeypatch `features()` 抛错后
  ids 范围仍可交付。
- **P1-2 回执漏报 —— 已修复（除 NEW-1）。** service.py 合并分析声明的全部
  损失码（除 writer 计数的 formula_prefix_sanitized）；`crs is None` 误闸
  已移除；nan_inf_to_empty 逐单元格计数披露；coordinate_precision_reduced
  可达回执。
- **P1-3 重试入队不存在的任务 —— 已修复且完整。** max_retries=0 +
  无 dispatch_spec；心跳随阶段推进落库；既有 lifespan sweep
  （sweep_stale 5min / sweep_orphans 1h）覆盖崩溃行；重试经
  retryable=False 诚实拒绝，恢复路径 = 同规格重提（幂等键释放后新建行），
  无永久卡死。
- **P2 输出字节硬帽 —— 正确。** 各 writer 主路径解析正确；Shapefile 运行期
  由 1GiB 临时盘帽兜底、close 时成员合计硬捕；两帽互不重复计数；失败路径
  全部清理 staging。

### Round 2 新发现及处置

- **NEW-1（P2，已修复）：** 字段名损失码多报/重复 —— writer 对任何非空
  field_map 发射 field_name_mapped，与分析码合并后出现重复
  （显式映射 ×2）或把自动截断误标为 mapped。修复：字段名码由
  analyze_losses 单点声明，writer 不再发射；回归
  test_field_name_codes_no_duplicate_or_mislabel（自动/显式两种路径 +
  去重断言）。
- **NEW-2（P3，已修复）：** copy_mode 经 extra="allow" 隐式透传、未进
  OpenAPI。已在 DeliveryReceipt 声明 `copy_mode: str = "reencode"`。
- **NEW-3（P3，接受）：** z_preserved/null_geometry_preserved 的计数来自
  ≤2000 行样本（下界；保留语义本身逐行成立）。session_ref 源为全量画像。
- **NEW-4（P3，已修复）：** coordinate_precision 对 GeoParquet/GPKG 等
  格式曾静默无效。修复：预览阻断（该选项仅 GeoJSON/CSV 兑现），回归
  test_coordinate_precision_blocked_for_binary_formats。
- **NEW-5（P3，接受）：** 心跳随进度推进更新；单批 >5min 的极端场景理论
  上可能被误判 stale（READ_BATCH_ROWS=2000 下不可见）。

### Round 1 P3 处置

关闭：filter requested、durable 三态、fast_path→copy_mode、CSV sidecar
crs、损坏回执 typed 500、discard_final、orphan sweep 覆盖、id_field 接线。
部分：NaN/Inf 进回执（预览仍静默）。保留：P3-3（历史公共项目可见性）、
P3-8（列表 N+1）、P3-11（回写 sha）、P3-12（GDAL 门控 CSV 探测）、
P3-14（fast path 仅 API）。

### 复核通过项（抽样）

执行期版本绑定复核；快照一次解析供 filter+writer 共用；下载成员名闸
（无 `/ \ ..`）；DataObject owner_scope_allows（内容哈希 ≠ 跨用户令牌）；
Shapefile ZIP 成员唯一/计数守恒；GeoJSON RFC 7946 + 独立反读；行帽双重
强制；"full ≠ 源全集"标注。**旧协议零改动**（map.py/export 无 diff；
materialization 仅 +6 additive 键）。**快照增量可证明 additive**（+5 条
data-delivery 路径，0 净删除；±6500 行为既有陈旧项重排，与 PR #2022 的
同文件再生成存在冲突面 —— PR 说明已注明）。

### 测试证据（Round 2）

| 命令 | 结果 |
|---|---|
| pytest tests/delivery -q --no-cov | 55 passed |
| contract/snapshot 5 文件 | 28 passed（字节级 OpenAPI 闸绿） |
| frontend tsc --noEmit | clean |
| vitest components/data-delivery | 4 passed |

### 环境阻塞（既有，非本任务）

- tests/test_adr_numbering_contract.py：ADR 0286/0287 撞号（base 即存在）。
- tests/unit/gis_tool_conformance/test_bounded_modelops_results.py：
  Windows fcntl 缺失 7 errors（base 即存在）。
