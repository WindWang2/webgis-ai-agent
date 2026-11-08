# G11 证据化地图简报 —— 后端现状审计报告

审计对象 worktree：`/home/kevin/projects/webgis/webgis-wt-g11-evidence-map-briefs-20261108T160816Z`（基于 master 8eb8aa1）。以下所有路径均为该 worktree 内的绝对路径（为节省篇幅，根目录缩写为 `$WT` = `/home/kevin/projects/webgis/webgis-wt-g11-evidence-map-briefs-20261108T160816Z`）。

---

## 1. app/lib/storymap/ 全目录

**文件**：`$WT/app/lib/storymap/{__init__.py, spec.py, story_compiler.py, camera_planner.py, export_packager.py}`（纯函数层，ADR-0196；IO 壳在 `$WT/app/services/storymap/`）

### spec.py（`$WT/app/lib/storymap/spec.py`）
- `:16` `STORYMAP_SPEC_SCHEMA_VERSION = "1.1"`（additive-only，v1.1 加 `KeyframeScene` 与 `visible_layer_ids`）
- `:19-32` `ArcRole = Literal["introduction","macro_situation","focus_dissection","dynamic_simulation","recommendation"]`；`NARRATIVE_ARC` 五段保序
- `:62` `MAX_PAYLOAD_DEPTH = 64`；`:65` `assert_json_depth(value, *, max_depth)`（深度门卫）；`:92` `strip_surrogates(value)`
- `:115` `narration_text(markdown: str) -> str`；`:123` `estimate_duration(markdown: str) -> float`
- `:142-158` `class CameraKeyframe(_StoryModel)`：`chapter_id / t(0-1) / center[lng,lat] / zoom(0-18) / pitch(0-60) / bearing(±180) / easing / scene: KeyframeScene|None`（`extra="allow"`, `allow_inf_nan=False`）
- `:161-181` `class StoryChapter`：`id / title / narrative(markdown) / arc_role / source_stage_ids: List[int] / linked_widget_ids / highlight_refs / visible_layer_ids(≤32) / duration_hint_s`
- `:184-192` `class LinkedWidget`：`id / kind: Literal["chart","table","kpi","stats"] / ref / title / chapter_id / data(dict, recharts 兼容)`
- `:195-202` `class AudioNarrative`：`chapter_id / text / lang / duration_hint_s / voice`
- `:205-213` `class StoryMapMetadata`：`title/session_id/turn_id/generated_at/summary/theme/language/engine_version`
- `:216-240` `class StoryMapSpec`：`schema_version / metadata / chapters(≥1) / camera_keyframes / linked_widgets / audio_narrative` + 校验器（chapter id 唯一、keyframe 必须引用真实 chapter）

### story_compiler.py（`$WT/app/lib/storymap/story_compiler.py`）
- `:35-41` `ARC_STAGE_BUCKETS`：18 规范阶段 → 5 弧桶（S1-2 引言、S3-8 宏观、S9-12 解剖、S13-16 推演、S17-18 建言）
- `:50` 产物 ref 词表：`ref:(chart|table|stats|grid|admin)-[A-Za-z0-9_-]{1,64}`
- `:63-69` `@dataclass TraceStep(stage_id, ts, payload)`
- `:72-125` `normalize_trace(raw: Mapping) -> List[TraceStep]` — 接受两种形状：① `GisTraceChain.as_dict()`（`stages:[{stage|stage_id, ts, **payload}]`，定义于 `$WT/app/lib/runtime/gis_trace.py:135`，`Stage(IntEnum)` 1-18 在 `:88-107`）；② `ReplayTrace` dict（`user_input/tool_calls/artifacts/final_text`，定义于 `$WT/app/lib/harness/replay/schema.py:53`）
- `:419-513` `compile_story_map(trace=None, messages=None, *, session_id="", turn_id="", title=None, scene_enabled=False) -> StoryMapSpec`（trace 优先，messages 降级；有界性门卫 + 剥代理字符）
- `:273-297` `_harvest_widgets`（ref/chart dict → LinkedWidget）；`:203-243` `_payload_bbox`；`:246-265` `_union_bbox`

### camera_planner.py（`$WT/app/lib/storymap/camera_planner.py`）
- `:79-125` `plan_camera_for_bbox(bbox, arc_role, *, aspect=16/9) -> Dict`（zoom/pitch/bearing/easing/aspect）
- `:199-256` `build_camera_track(keyframes, *, samples_per_leg=16) -> List[CameraSample]`（Catmull-Rom→三次贝塞尔）
- `:259-286` `validate_track(track, *, max_center_jump_deg=1.0, max_zoom_jump=1.0, max_pitch_jump=15.0, max_bearing_jump=30.0) -> List[str]`
- `:45` `scene_for_arc(arc_role) -> Dict|None`

### export_packager.py（`$WT/app/lib/storymap/export_packager.py`）
- `:24` `BUNDLE_SCHEMA_VERSION = "storybundle-1"`
- `:69-90` `sanitize_dict(value)`（敏感键词元 REDACT + URL 凭证 scrub）
- `:93-132` `build_story_bundle(spec, *, layers=None, mapspec=None, sanitize=True) -> Dict` — **StoryBundle = `{schema_version, generated_at, spec, data:{layers, mapspec}, manifest:{chapter_count, widget_count, layer_count, feature_count}}`**
- `:135` `bundle_to_json(bundle) -> str`；`:220-244` `render_standalone_html(bundle) -> str`（自解压单文件 HTML，零外链）

### 服务壳
- `$WT/app/services/storymap/story_compiler.py:14` `async compile_for_session(db, conv) -> object`（装载 ≤200 条消息；检查 mapspec raster-dem 源决定 `scene_enabled`）
- `$WT/app/services/storymap/assets.py:20` `MAX_STORY_SPEC_BYTES = 512*1024`；`:23` `save_story_spec(db, conv, spec)`（revision 单调 +1）；`:63` `load_story_spec(db, conv)`

### 测试位置
- `$WT/tests/unit/test_storymap_orchestrator.py`（67 tests，四验收面）
- `$WT/tests/unit/test_storymap_compiler_export_gaps.py`（17）、`test_storymap_spec_boundaries.py`（14）、`test_storymap_scene_v11.py`（18）
- 浏览器 e2e：`$WT/frontend/e2e/journeys/j4-storymap-share.journey.ts`（/story 播放 + URL 复制分享）

**G11 判断：部分实现**。章节/镜头/图表卡/离线 HTML 打包骨架齐备；缺口：① 数据块（chapter/widget）只有 `source_stage_ids`（trace 阶段号），没有 artifact/dataset/snapshot 版本级证据绑定结构；② 无编辑锁定/重生成；③ 无 release 冻结；④ 无服务端分享 token。

---

## 2. app/api/routes/storymap.py

文件：`$WT/app/api/routes/storymap.py`（router `prefix="/storymap"`，`main.py:1161-1164` 挂载于 `/api/v1`）

| 端点 | 行号 | 鉴权 | 说明 |
|---|---|---|---|
| `POST /storymap/compile` | `:36-55` | `get_current_user`（SEC-07） | 无状态 trace/messages → StoryMapSpec，422 边界 |
| `POST /storymap/sessions/{session_id}/compile` | `:58-68` | `require_owned_session` | 会话消息 → spec |
| `POST /storymap/export` | `:71-97` | `get_current_user` | spec+layers+mapspec → JSON bundle 或 HTML（`StoryExportRequest.format: Literal["json","html"]`） |
| `POST /storymap/sessions/{session_id}/spec` | `:103-124` | `require_owned_session` | 覆盖式保存，revision+1 |
| `GET /storymap/sessions/{session_id}/spec` | `:127-142` | `require_owned_session` | 最新资产；404 无资产；422 corrupt 标记 |

- 权限机制：`$WT/app/services/auth_history_bridge.py:33` `verify_session_owner(db, session_id, user_id, owner_token) -> Conversation`（统一 404 不泄露存在性）；`:57` `require_owned_session(...)` FastAPI 依赖。匿名会话走 `owner_token`（`get_owner_token`）。
- 请求 schema：`$WT/app/schemas/storymap_schema.py:9` `StoryCompileRequest(session_id, turn_id, trace, messages, title)`；`:29` `StoryExportRequest(spec, layers≤64, mapspec, format)`
- **持久化**：表 `story_map_assets`，模型 `$WT/app/models/story_map_asset.py`（`conversation_id` FK CASCADE 唯一、`title`、`spec: Text`(≤512KB 服务层限)、`revision` CHECK≥1、时间戳），migration `$WT/migrations/versions/0099_story_map_assets.py`。存储引擎 = 关系库单行 JSON Text，无对象存储。

**G11 判断：部分实现**。CRUD 仅“会话级最新快照、整包覆盖”；缺项目作用域、版本化 release、锁定、分享端点。鉴权依赖与资产表模式可直接照抄扩展。

---

## 3. app/api/routes/report.py 与 app/api/report_sharing.py

`$WT/app/api/routes/report.py`（router `prefix="/reports"`，`:30`）：
- `:74-107` `POST /reports` create_report —— `verify_session_owner` + `ReportService().create_and_generate(...)`（携带 mapspec 以注入矢量 SVG）
- `:110-139` `GET /reports?session_id=`（session_id 必填防跨租户）
- `:204-220` `_check_report_owner(db, report_id, user_id, owner_token) -> Report` —— 归属经 `report.session_id → conversation.user_id` 解析，404 防存在性泄露
- `:223-261` `GET /reports/{id}`、`/download`（FileResponse + 严格 CSP `sandbox` 头，`:51-60`）

**分享 token 机制**（G11 可直接复用的完整先例）：
- `:281-311` `POST /reports/{id}/share`：`share_code = secrets.token_urlsafe(12)`，`ttl_days = clamp(body.ttl_days, 1, 30)`，`share_expires_at` 落 naive UTC（F-04），返回 `share_url = /api/v1/reports/shared/{code}/view`
- `:155-166` `GET /reports/shared/{share_code}` 公开元数据（依赖 `limit_public_report_access` 限流）
- `:169-201` `GET /reports/shared/{share_code}/view` 匿名查看文件（CSP sandbox + 路径逃逸校验 `_validate_file_path` `:38-42`）
- `:264-278` `DELETE /reports/{id}/share` 撤销 = `share_code=None; share_expires_at=None`
- `:142-152` `_share_expired(expires_at)` 过期判定（naive→UTC 容错）

`$WT/app/api/report_sharing.py`：
- `:9-13` `limit_public_report_access(request)`：每 IP `report-share:{ip}` 60 req/min，429+Retry-After
- `:16-24` `public_report_projection(report) -> dict`：**scope 模型 = 只暴露 title/format/status/file_size/created_at/expires_at**（不含 session_id/文件路径）

**G11 判断：已有完整实现（可复用）**。简报私有分享/撤销/过期/限流/公共投影可按此模式平行实现（token 列放 brief 表上即可，无需签名 URL——签名 URL 只用于 map 导出下载，见第 7 点）。

---

## 4. evidence_claim / evidence store / verification

核心包：`$WT/app/services/gis_harness/evidence_claim/`（Direction 03，`SCHEMA_VERSION = "evidence_claim.v1"`，contracts.py:16）。设计约束：**投影层，不是第二事实库**——只存 claim 身份 + 证据 stub，重载荷永远在权威库（ArtifactRegistry/ProductGraph/dataset pins/MapSpec revisions）。

- `contracts.py:30-48` `EvidenceKind`（含 `dataset / dataset_version / artifact / map_view / chart / statistic / method / external_source` 等 18 种）
- `:51-57` `EvidenceFreshness`：`fresh/stale/superseded/expired/unknown/missing`
- `:60-81` `ClaimType`（count/mean/rate/.../narrative）；`:83-89` **`ClaimStatus`（即验证 verdict）= supported / partially_supported / unsupported / contradicted / stale / unknown**（不是 pass/fail/not_evaluated；后者是 `$WT/app/lib/runtime/evidence.py:45-53` `Outcome = succeeded/failed/cancelled/superseded/partial/not_evaluated`，turn 级终态语义）
- `:141-180` `Scope`（spatial/temporal/filter 有界投影）；`:183-216` `EvidenceNode`（`evidence_id/kind/ref/producer/version/revision/scope/method/freshness/tenant/session/metadata`）；`:219-271` `Claim`（`claim_id/claim_type/subject/value/unit/scopes/method/supporting_evidence_refs≤16/status/narrative/product_facet_id/map_layer_id`）；`:274` `RelationEdge`；`:291-320` `VerificationStep/VerificationResult`（含 `positive_proof: bool`）；`:323` `ContradictionResult`；`:340-366` `CartoBinding`（layer→style→statistic→dataset_version→mapspec_revision）
- `store.py:22-162` `ClaimStore`：**进程内有界内存库**（evidence≤4096/claims≤2048/edges≤10000），`to_dict()/from_dict()` 可序列化；`claims_for_artifact(artifact_ref)` `:90`
- `verify.py:100` `verify_claim(claim, store, *, require_uncertainty=False, records=None, expected_tenant_id="", persist_status=True) -> VerificationResult`：positive-proof 管线 resolve→liveness→freshness→semantics→method_scope→uncertainty→verdict，**缺失证据绝不提升为 supported**；`verify_all`
- `census.py:34` `project_artifact_record(record, *, tenant_id, session_id) -> EvidenceNode`（`ArtifactRecord → EvidenceNode`，`ref = artifact_id`，stat_type/unit/value 只从权威 metadata 拷贝并标 `proof_source=self_attested_metadata` 防自证）；`:91` `project_artifact_registry`；`:133` `project_dataset_version`；`:299` `resolve_live_ref`
- `claims.py:30` `claim_from_statistic(...)`；`claim_from_rank_table`；`narrative_claim_unverified`
- `query.py`：`why_claim / why_map_feature / evidence_for / claims_for_artifact / contradictions`
- 关联 artifact/trace 方式：`EvidenceNode.ref` 即 `ref:chart-…/ref:geojson-…` 等 registry ref；`EvidenceNode.version/revision` 对应 dataset_version/mapspec revision
- 测试：`$WT/tests/unit/gis_harness/test_evidence_claim_graph_v1.py`

另：`$WT/app/lib/runtime/evidence.py:119` `TurnEvidence`（turn 计时/计数/警告，`TURN_EVIDENCE` 注册表 `:386`）——观测用，与 claim 图互补。

**G11 判断：已有完整实现（数据结构+验证器可直接用）**。缺口：ClaimStore 无 DB 持久化钩子（会话内 dict round-trip），G11 需在编译时把绑定结果固化进 brief 资产或落库。

---

## 5. artifact / dataset / snapshot 体系

### 会话产物账本
`$WT/app/services/artifact_registry.py`（1201 行）：
- ref 即身份（`artifact_id = ref 字符串`，`:88` `ArtifactRecord`）：`ref:geojson-…`、`ref:chart-…`、`ref:raster/<id>`（`:290-296`）、`ref:export/<filename>`（`:328-341`）、`ref:fabric-parquet/<id>`（`:376`）、`ref:fabric-cog/<id>`（`:416`）
- `:129` `ArtifactGraph`：`consumers/producers/lineage/dependents/replacement_chain/latest_for_capability`
- 台账经 session backend alias 持久化（`LEDGER_ALIAS`，`:208-241`）；`MAX_ARTIFACT_RECORDS=128`
- `$WT/app/services/artifact_revisions.py:56` `record_revision`、`:138` `head_revision`、`:154` `list_revisions`、`:384` `pin_artifact`、`:424` `clone_artifact`
- `$WT/app/services/durable_blob_store.py:128` `BlobStore`：**内容寻址 CAS（key = 64hex sha256）**，`put_json/get_blob(expected_sha256)`，读上限 64MB，digest 校验 —— 不可变晋升目标

### dataset 版本
`$WT/app/models/lakehouse_datasets.py:107` `LakehouseDatasetVersion`：`version_id = commit manifest sha256`（内容寻址幂等）、`parent_version_id`（DAG）、`data_object_id`、`content_sha256`、`action ∈ commit/rollback/delta/import/workflow_publish`；`:171` `LakehouseDatasetRef`：branch 走 generation CAS，**tag 创建后不可变**。

### project_runtime 快照（freeze/inspect/resume）
- 路由 `$WT/app/api/routes/project_runtime.py`：`POST /projects/{pid}/runtime-snapshots`（`:75-144` freeze）、`GET .../{sid}`（`:168` inspect = validation preview）、`POST .../{sid}/resume`（`:213` verify-first 恢复）、`DELETE`（`:263`）；鉴权三重门：`get_project_with_auth`（跨 org 404）→ `_verify_session_access`（SEC-08）→ created_by 二道守卫
- 契约 `$WT/app/services/project_runtime/contract.py:171` `ProjectRuntimeSnapshotV1`（`snapshot_id/project_id/session_id/created_by/freeze_reason ∈ {explicit_save, named_checkpoint, publish}/workspace_snapshot_id/mapspec_doc+mapspec_fingerprint+mutation_revision/source_bindings/datasets/plan/policy/disclosure`）；`:99` `SourceBinding`（`source_id/kind ∈ source|chart|table/ref/promotion_class/durable_pointer/live_payload_sha256`）；`:121` `DatasetDescriptor(version_fingerprint)`；`:87` `FREEZE_REASONS`
- `$WT/app/services/project_runtime/freeze.py:58` `_consistent_cut`（session mutation 锁内读 mapspec+revision+plan）→ promotion（ref 晋升 BlobStore）→ 组合 workspace snapshot → `$WT/app/services/project_runtime/store.py:118` 原子落盘 manifest（要么完整要么不存在）
- `$WT/app/services/project_runtime/validation.py`：`validate_runtime_snapshot` + `build_recovery_plan`（状态词表 `RESUME_READY/READY_WITH_STALE_INPUTS/REQUIRES_RECOMPUTE/MISSING_ARTIFACT/...`，contract.py:62-77）

**不可变性保证**：CAS blob（sha256 键+digest 校验读）+ canonical-JSON 指纹 + 内容寻址 dataset version + 原子写 manifest + tag 不可变 + lifecycle GC 引用计数（`$WT/app/services/artifact_lifecycle.py`）。

**G11 判断：已有完整实现**。“不可变 release 冻结”直接用 `freeze_reason="publish"` 快照 + mapspec_fingerprint + source_bindings 的 durable_pointer 即可，不建新体系。

---

## 6. Agent 工具注册链

- **ToolRegistry**：`$WT/app/tools/registry.py:475` `class ToolRegistry`；`:496-523` `tool(name, description, *, param_descriptions, args_model, tier, domains, execution_policy, timeout, version, contract_version, cost, **descriptor_kwargs)` 装饰器（tier 1=always-on / 2=domain 触发 / 3=显式发现）；`:547` `register(...)`（含显式 `parameters=` JSON schema）；`:865` `get_schemas()`；`:1145` `async dispatch(name, arguments, session_id)`；`:2097` `list_tools()`
- **ToolDispatchService**：`$WT/app/services/tool_dispatch_service.py:240` `class ToolDispatchService(*, registry, fire_broadcast, session_data)`（去重/并发闸/单飞）
- **Pi 接线**：`$WT/app/agent_pi_bridge.py:206` `set_tool_registry` / `:227` `get_tool_registry` / `:480` `dispatch_tool(request: PiToolRequest)`（Pi HTTP 回调适配器，turn token 校验 + 结果缓存）；执行入口 `$WT/app/api/routes/pi_tools.py:51` `POST /pi-tools/execute`（`:36` bridge secret 校验）；前端目录经 `$WT/app/api/routes/chat.py:2452` `{"tools": get_registry().get_schemas()}`
- **注册链挂载点**：`$WT/app/tools/__init__.py` `_TOOL_MODULES` 列表（约 `:40-120`）+ `:124` `init_tools(registry)`（lifespan 逐模块 import 并调用 `register_xxx(registry)`）——**新增工具的正确挂载点 = 新模块 + 追加 `_TOOL_MODULES` 条目**
- **既有 report 工具**：`$WT/app/tools/report.py:104` `register_report_tools(registry)` → `generate_analysis_report(format, title, session_id)`（tier=2, domains=["report"], 显式 `parameters=` 只暴露 format/title；**`session_id` 形参名精确匹配 registry 注入探针**，LLM 不可伪造；`side_effect="artifact_creation"`）
- **storymap agent 工具：不存在**（`$WT/app/tools/` 全目录 grep "story" 无结果）——storymap 目前只经 REST 路由触达。

**G11 判断：挂载机制完整，storymap/brief 工具面待开发**。

---

## 7. 地图导出服务

- **栅格 PDF**：`$WT/app/api/routes/map.py:719` `POST /export/pdf`（前端 canvas 图 → A4 底图 PDF）；`:236` `upload_map_export`；`:435` `get_export_diagnostics`；`:778` `GET /export/download/{filename}`（**签名 URL：`exp` + `sig` 查询参数**，`_export_download_url` `:109`）；`:832` `POST /export/geojson`
- **矢量 PDF / publication 链**：`$WT/app/api/routes/map.py:522` `export_map_as_vector_pdf`（MapSpec → 真矢量 PDF；503 `vector_pdf_unavailable`、429 `vector_pdf_busy`、413 载荷上限、400 typed）
- **渲染引擎**：`$WT/app/services/mapspec_to_svg.py:1030` `compile_mapspec_to_svg(...)`、`:1044` `compile_mapspec_to_svg_detailed(...)`（MapSpec → SVG，制图渲染 v6）；`$WT/app/services/publication_export.py:136` `compile_publication_page`、`:224` `assemble_pages_pdf`、`:656` `hydrate_ref_sources(payload, session_id)`（**ref 载体源内联水合——离线导出关键先例**）、`:720` `validate_publication_payload`、`:752` `render_publication_pdf`、`:297` `PublicationPdfResult`（含 `constraint_layout / spec_fingerprint / layout_version / lineage / atlas / atlas_pages`）；`$WT/app/services/publication_render_worker.py` `render_publication_bounded`（WeasyPrint 进程级串行槽）
- **出版约束链（G04 / ADR-0283，#1975 评审修账）**：`$WT/app/lib/cartography/publication_constraint_layout.py:54` `ConstraintLayoutReport(TypedDict)`、`:156` `evaluate_constraint_layout`、`:176` `resolve_cartographer_layout`、`:202` `prepare_publication_document`；parity 台账 `$WT/docs/cartography/RENDER_EXPORT_PARITY.md`（`#1975 评审修账` 出现在 `:25`）
- **MapSpec 存储**：`$WT/app/services/mapspec_store.py:95` `MapSpecStore.get_mapspec/save_mapspec`（`mutation_revision` + `expected_revision` 乐观并发）；单例 `$WT/app/services/mapspec/store.py mapspec_store_instance`
- **批量/图集**：`$WT/app/api/routes/atlas_jobs.py:115` `POST /atlas/jobs`、`:199` `GET /atlas/jobs/{job_id}`；`$WT/app/services/export_batch_queue.py:98` `run_batch`
- **产品版本账本**：`$WT/app/services/map_product_service.py:130` `MapProductService.record_version(db, project_id, *, mapspec_fingerprint, artifact_ids, ..., lineage_kind)` —— 服务端永远自算指纹/diff，项目级单调 version_no（发布时间线即证据）

**G11 判断：已有完整实现**。简报的“地图视图块静态渲染”复用 `compile_mapspec_to_svg + publication_export`；“发布预检”复用 constraint_layout report + runtime snapshot validation。

---

## 8. 数据库迁移体系

- 目录：`$WT/migrations/{env.py, script.py.mako, versions/}`（73 个 revision；`alembic.ini` 在仓库根）
- 命名习惯：`NNNN_slug.py`，且 `revision` 字符串 = `"NNNN_slug"`（如 `revision = "0103_workflow_run_session"`）；多 head 用 `*_merge_NNNN_multi_epic_heads.py`；另有少量 hash 式（`6ef479051297_conversation_owner_token.py`）与 `g1109_legacy_owner_tokens.py`（老分支，down=0022）
- **单 head：`0103_workflow_run_session`**（`$WT/migrations/versions/0103_workflow_run_session.py:4`，down_revision = `"0102_owner_token_hashes"`）→ **下一个迁移应为 `0104_<slug>.py`**
- 近头链条：`0099_story_map_assets → 0100_spatial_watch_consistency → 0101_cluster_reservation_bigint → 0102_owner_token_hashes → 0103_workflow_run_session`
- 迁移一致性测试：`$WT/tests/test_alembic_metadata.py`

---

## 9. 已有测试体系

- **后端**：`$WT/tests/`（顶层大量 `test_*.py` + 子目录 `unit/ integration/ acceptance/ cartography/ harness_replay/ quality/ jobs/ data/ fixtures/ lab/ perf/ ...`），`pytest.ini` 在根
- conftest：`$WT/tests/conftest.py`（520 行）——`:288` `_init_isolated_test_db`（会话级隔离库）、`:316` `_pin_auth_bypass_off`（默认封死鉴权旁路）、`:385` `_offline_embedding_model`（fail-fast 提示 stub `embed_texts`）、`:425` 单例 reset、`:499` fabric fixtures、`:501` OGC fake server
- **真实 Pi RPC + stub LLM 的写法**：`$WT/tests/test_pi_e2e.py:1-60` —— 注入 MagicMock `PiRpcClient`（预置 events 队列：`tests/fixtures/pi_mocks.py` 的 `make_token_event/make_tool_call_event/make_agent_end/...`），驱动 `PiBridge.stream_prompt` 全链 SSE 映射；无需真实 LLM key
- **浏览器 e2e**：`$WT/frontend/playwright.config.ts` + `$WT/frontend/e2e/{journeys,fixtures,helpers,tools}`；模式开关 `$WT/frontend/e2e/helpers/mode.ts:11`（`E2E_MODE=real` 才打真后端，mock 双胞胎跑 PR lane，real 跑 nightly：`$WT/tests/test_ci_playwright_lane.py`、`$WT/frontend/tests/e2e-lanes`）；真实后端种子 `$WT/frontend/e2e/helpers/real-chat.ts`（`seedConversationViaApi/loginViaApi`）；journeys j1–j12，其中 **j4 = storymap 分享契约**
- 前端单测在组件旁（`$WT/frontend/app/story/*.test.tsx`）

---

## 10. docs/ 命名习惯（storymap / report / publication）

- **ADR**：`$WT/docs/adr/NNNN-slug.md`（最新 0287，含 `RENUMBERING_MAP.md`）。相关：`0147-frontend-ux-depth-command-registry-storymap-chapters.md`、`0196-autonomous-storymap-orchestrator.md`、`0023-encapsulate-report-status-lifecycle-saga.md`、`0126-cartographic-rendering-v6-publication-engine.md`、`0157-publication-grade-export-v10.md`、`0283-publication-constraint-layout-and-label-disclosure.md`、`0226-durable-project-cartographic-runtime-v1.md`、`0275-3d-cartographic-product-composition.md`、`0284-typed-analysis-artifact-envelope.md`
- **特性工作文档**：`$WT/docs/dev/<epic前缀>-<topic>-<design|decisions|ledger|recon|review>.md`（如 `f02-gis-intent-requirement-ir-design.md`、`ac-03-...`、`g01-pi-native-runtime-pr-body.md`）；storymap 既有 `docs/dev/storymap-orchestrator-spec.md`（无前缀）。**G11 建议：`docs/adr/0288-….md` + `docs/dev/g11-evidence-map-briefs-{design,decisions,ledger}.md`**
- **领域文档**：`$WT/docs/cartography/RENDER_EXPORT_PARITY.md`、`$WT/docs/api-docs.md`、`$WT/docs/quality/…`

---

## G11 后端最小增量清单（结论）

G11 的底座几乎全部在场：StoryMapSpec 章节模型（§1）、会话资产表（§2）、分享 token 全套（§3）、claim/证据验证图（§4）、CAS blob + publish freeze + dataset 版本（§5）、工具挂载链（§6）、SVG/PDF 渲染 + 约束预检 + ref 水合（§7）。需新增的是“把它们串成简报（brief）域”的胶合层，而非第二事实库：

1. **新 lib 层 `$WT/app/lib/storymap/brief_spec.py`（或 `app/lib/mapbrief/`）**：`MapBriefSpec`（additive 扩展 StoryMapSpec）+ 每数据块 `EvidenceBinding{artifact_ref, dataset_version_ref, snapshot_id, claim_id, verification: ClaimStatus}`；复用 `evidence_claim.census/verify` 做编译期绑定与 verdict（不新建 store，绑定结果随 brief 资产持久化）。
2. **新服务壳 `$WT/app/services/storymap/brief_compiler.py`**：ReplayTrace/ArtifactRegistry/ClaimStore → MapBriefSpec；trace 取自 `$WT/app/services/gis_harness/trace_store.py`，artifact ref 取自 `artifact_registry`。
3. **编辑锁定/重生成**：复用 `$WT/app/services/collab/leases.py LeaseRegistry`（acquire/renew/release/snapshot，session 作用域）作 brief 级写锁；重生成 = 复跑 brief_compiler + revision+1（沿用 `save_story_spec` 单调 revision 先例）。
4. **发布预检**：组合 `validate_runtime_snapshot + build_recovery_plan`（project_runtime/validation.py）与 `ConstraintLayoutReport`（publication_constraint_layout.py）→ brief precheck 响应。
5. **不可变 release**：`save_runtime_snapshot(freeze_reason="publish")` 复用为 brief release 冻结（mapspec_fingerprint + source_bindings durable_pointer + map_product_version_no）；brief release 行只存指针。
6. **离线导出**：扩展 `$WT/app/lib/storymap/export_packager.py`：bundle 增 `evidence_manifest`（bindings + fingerprints），复用 `hydrate_ref_sources` 内联图层/图表源，输出 HTML + 旁挂 JSON manifest。
7. **私有分享与撤销**：按 report.py 模式给 brief 表加 `share_code/share_expires_at` 列 + create/revoke/view 端点 + `limit_public_report_access` 限流 + `public_*_projection`。
8. **路由与迁移**：`$WT/app/api/routes/storymap.py` 增 brief CRUD/lock/release/export/share 端点（鉴权照抄 `require_owned_session` + project 三重门）；新迁移 `0104_map_briefs.py`（head 是 `0103_workflow_run_session`）。
9. **Agent 工具**：新 `$WT/app/tools/storymap_tools.py` `register_storymap_tools(registry)`（compile_brief / regenerate_block / publish_brief；`session_id` 走注入探针约定），追加进 `$WT/app/tools/__init__.py:_TOOL_MODULES`。
10. **测试与文档**：`$WT/tests/unit/test_mapbrief_*.py`（照 storymap 四件套）+ 新 e2e journey（照 j4）；`docs/adr/0288-*.md` + `docs/dev/g11-evidence-map-briefs-*.md`。

一句话：唯一真正“待开发”的是块级证据绑定模型（#1/#2）与其持久化/分享端点（#7/#8）；锁定、预检、冻结、离线打包、渲染、分享撤销、工具挂载全部有可直接复用的成熟先例。