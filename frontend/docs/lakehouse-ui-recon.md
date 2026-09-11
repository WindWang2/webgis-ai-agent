# Lakehouse Cube Explorer UI V9 — 勘察纪要（P0）

> 线：feat/lakehouse-ui-v9 · ADR-0141 · 基线 origin/master 8b5b8375
> 本文是 P0 勘察产出：后端 REST 契约（以后端代码为准）、前端复用面、上图管线、i18n 现状。
> 契约核对日期：2026-09-11。

## 0. 复核结论（§0.2《复核纪要》）

**无重叠，照单执行。** 证据：

- `gh pr list --state all --search "lakehouse OR cube OR STAC OR 数据湖"`：全部已合并 PR 均为后端/CI 线（#1188 V8 能力面、#1225 ADR 重编号、#1229 FK seeds），**无任何 PR 声明前端 lakehouse UI**。
- `gh issue list --state all --search "lakehouse OR cube OR STAC"`：全部 CLOSED 且为后端问题（STAC 适配器 SSRF、合成条目等），无前端 UI issue。
- `git branch -a | grep lakehouse`：无其他 lakehouse 分支；同刻并行的 V9 worktree 为 foundation/api-contract-v9、foundation/data-lifecycle-v9、foundation/security-tenancy-v9（非 UI 线）。
- `grep -rn "lakehouse" frontend/{lib,components,app}`：**0 处引用**（任务书预估"1 条 compiler 注释"已随重编号消失）→ 零 UI 现状成立。
- ADR watermark：`docs/adr/` 最新 0137；**ADR-0141 空闲，本线占用**（信息架构与上图管线选型）。

### 端点规模修正

任务书预估"约 22 端点"；master 实测 **29 个**（`lakehouse.py` 17 + `lakehouse_datasets.py` 12），全部纳入 P1 typed client 范围（以后端最新代码为准，不缩水）。

### ADR 编号修正

lakehouse-v8 ADR 因 #1225 重编号，实际文件为 `docs/adr/0135-lakehouse-v8-versioned-cubes.md`（任务书所写 ADR-0130 是 schema 契约号）。本线产出 ADR-0141。

## 1. 29 端点契约表

### 1.1 对象 / cube 面（app/api/routes/lakehouse.py）

通用语义：

- 认证：`get_current_user_optional`（Bearer 可选）+ `get_owner_token`（`X-Session-Token`，SEC-08 匿名会话所有权）；session 域端点一律 `verify_session_owner`，无身份 → fail-closed 404。
- 错误信封：FastAPI `HTTPException(status, detail: string)`；typed 服务错误 → 404（不存在/不可见，`*_MISSING`/`*_NOT_FOUND`）或 400（契约违例）；revise/gc/retention 漂移 → 409。**成功响应没有统一信封**（顶层 `success: true` + 各端点自有键）。
- `project_id` 出现在请求体/查询中且非空 → 显式 400（项目域发布通道除外）。

| # | Method Path | 请求 | 响应顶层键 | 错误 |
|---|---|---|---|---|
| 1 | GET `/lakehouse/objects/{data_object_id}` | query: `session_id*`, `project_id`(拒) | `success`, `data_object_id`, `manifest` | 400 / 404 |
| 2 | POST `/lakehouse/vector/scan` | `session_id*`, `ref*`, `bbox[4]*`, `columns?`, `max_rows≤200k` | scan 结果（rows/columns/截断披露） | 400 / 404(`LAKEHOUSE_REF_MISSING`) |
| 3 | POST `/lakehouse/cubes` | `session_id*`, `title`, `window_side≤8192`, `time_sources[≤512]*`（time≤64, source≤1024） | cube 构建回执（ref + durable 披露） | 400 / 404(`CUBE_SOURCE_MISSING`,`CUBE_REF_MISSING`) |
| 4 | POST `/lakehouse/cubes/window` | `session_id*`, `ref*`, `time?[2]`, `y?[2]`, `x?[2]`（至少一个有限切片；非负） | `ref`/`meta`/`bands`（band→嵌套数组，JSON 安全） | 422 / 400 / 404(`CUBE_REF_MISSING`) |
| 5 | POST `/lakehouse/cubes/revise` | `session_id*`, `ref*`, `title`, `updates[≤64]*`（band, time_index≥0, source） | 新修订回执（CoW fork） | 400 / 404(`CUBE_REF_MISSING`) |
| 6 | POST `/lakehouse/objects/{id}/verify` | body: `session_id?`, `project_id?`(拒) | `success`, `data_object_id`, `state` | 400 / 404 |
| 7 | POST `/lakehouse/cubes/rs` | `session_id*`, `title`, `sources[≤512]*`（time, source, role≤32, band?, polarization?, band_index?） | labeled cube 回执 | 400 / 404(`CUBE_SOURCE_MISSING`) |
| 8 | POST `/lakehouse/cubes/labeled/window` | `session_id*`, `ref*`, 标签维（time/band/polarization/vertical/**model**/**scenario** 各 ≤64 项）、`bbox[4]?`、`index_slices?`、`max_cells≤8M`；至少一种选择 | `variables`（name→嵌套数组）+ 计划触达块证据 | 422 / 400 / 404(`CUBE_REF_MISSING`) |
| 9 | POST `/lakehouse/publish` | `session_id*`, `project_id*`, `object_ids[≤200]*`, `tags?[≤32]` | `success` + publish 结果（幂等） | 400 / 404(`LAKEHOUSE_PUBLISH_PROJECT_MISSING`) |
| 10 | POST `/lakehouse/revoke` | `project_id*`, `object_ids[≤200]*`（**无 session_id**） | `success` + revoke 结果（tombstone） | 400 / 404 |
| 11 | GET `/lakehouse/catalog` | query: `owner_type(session\|project)*`, `owner_id*`, `session_id`, `kind`, `time_from`, `time_to`, `producer`, `include_revoked`, `limit≤200`, `offset` | catalog page（items + next_offset） | 400 / 404 |
| 12 | GET `/lakehouse/catalog/stac` | query: `owner_type*`, `owner_id*`, `session_id`, `limit(1..100)`, `offset` | STAC 1.0.0 Collection 投影 | 400 / 404 |
| 13 | POST `/lakehouse/objects/{id}/scrub` | `session_id?`, `project_id?`(拒), `mode(sample\|full)`, `sample_k≤64`, `etag_check` | `success`, `data_object_id`, + scrub 报告 | 400 / 404 |
| 14 | POST `/lakehouse/gc/plan` | `grace_hours≤720`, `session_id*`；**require_admin** | `success` + plan（token + 候选 + 预估释放量） | 400/401/403 |
| 15 | POST `/lakehouse/gc/execute` | `plan*`, `session_id*`；**require_admin**；token 重验 | `success` + 执行结果 | 409(`GCStalePlan`) / 400 |
| 16 | GET `/lakehouse/objects/{id}/lineage` | query: `session_id*` | lineage 视图（祖先链，深度/节点双闸） | 400 / 404 |
| 17 | GET `/lakehouse/projects/{project_id}/objects/{object_id}` | 路径参数 | `success` + resolved（manifest；不可解析 → **410**） | 400 / 404 / 410 |

### 1.2 dataset 版本层（app/api/routes/lakehouse_datasets.py，V8/ADR-0130→0135）

错误映射：`DATASET_NOT_FOUND`/`DATASET_VERSION_NOT_FOUND`/`DATASET_CONTENT_UNRESOLVED` → 404；`DATASET_REF_CONFLICT` → **409**；其余 → 400。全部 session 域（owner fail-closed 404）。

| # | Method Path | 请求 | 响应顶层键 |
|---|---|---|---|
| 18 | POST `/lakehouse/datasets` | `session_id*`, `name*≤128`, `description≤512`, `default_branch=main`, `cube_contract?` | `success`, `created`, `dataset` |
| 19 | GET `/lakehouse/datasets` | query: `session_id*`, `limit≤200` | `success`, `datasets[]`, `count` |
| 20 | GET `/lakehouse/datasets/{id}` | query: `session_id*` | `success`, `dataset`, `refs[]`, `head\|null`, `descriptor` |
| 21 | GET `/lakehouse/datasets/{id}/versions` | query: `session_id*`, `branch`, `limit` | `success`, `versions[]`, `count` |
| 22 | GET `/lakehouse/datasets/{id}/versions/{vid}` | query: `session_id*` | `success` + resolved（合成 manifest + `commit` record） |
| 23 | POST `/lakehouse/datasets/{id}/commit` | `session_id*`, `branch*`, `data_object_id*(64)`, `action(commit\|rollback\|delta\|import\|workflow_publish)`, `provenance?`, `parent_version_id?`, `workflow_run_id?` | `success` + commit namedtuple 字段 |
| 24 | POST `/lakehouse/datasets/{id}/branches` | `session_id*`, `name*`, `from_version_id?` | `success`, `created`, `ref` |
| 25 | POST `/lakehouse/datasets/{id}/tags` | `session_id*`, `name*`, `version_id*(64)` | `success`, `created`, `ref`（重复 → 409） |
| 26 | POST `/lakehouse/datasets/{id}/rollback` | `session_id*`, `branch*`, `to_version_id*(64)` | `success` + rollback 结果（head 已在目标 → 400） |
| 27 | GET `/lakehouse/datasets/{id}/lineage` | query: `session_id*`, `version_id*`, `max_depth≤64` | `success` + parent 链（截断诚实披露） |
| 28 | POST `/lakehouse/datasets/{id}/retention/plan` | `session_id*`, `max_versions≤10k`, `min_age_hours` | `success` + retention dry-run 计划 |
| 29 | POST `/lakehouse/datasets/{id}/retention/execute` | `session_id*`, `plan*`（token 重验；漂移 → 409） | `success` + prune 结果 |

### 1.3 响应形态深挖（S1 勘察校准，2026-09-11）

以下为字段级实测结论（服务层代码逐个核对，**已校准进 `lib/api/lakehouse.ts`**）：

**通用嵌套形态**

- `Manifest = {schema_version:1, kind, owner_scope:{session_id}|{project_id}（恰好一键）, environment_fingerprint:64hex, content_sha256:64hex, byte_size, content_blobs:[{path,sha256,byte_size}], payload, producer(已脱敏), source_refs[], input_fingerprint}`；kind ∈ `vector_parquet|cog_raster|zarr_cube|virtual|modelops_artifact|arrow_ipc`。
- `DurableDisclosure`（cube build/revise/rs 平铺进顶层）：`{published:true, durable:"published"|"manifest_only", data_object_id:64hex, manifest:服务器位置, content_sha256, byte_size, deduped, entry_count}` **或** `{published:false, reason}` —— 其余键全部缺失，TS 必须全部 Optional。
- `LabeledProjection`：`{labeled:true, cube_schema_version:2|3, dims[], shape[], variables:{name:{dims,dtype}}, dtype, nodata, chunks, crs, crs_checked, coords_summary:{dim: grid|labels}, nodata_per_variable?}` —— **携带 `nodata_per_variable` 即 v3**；维度集 ⊆ v2 六维且无逐变量 nodata 仍写 2（id 零漂移）。
- `CatalogItem = {object_id, owner_type, owner_id, kind, title, producer_capability, producer_tool, workflow_run_id, tags[], bbox(四值全有或全 null), time_start, time_end, content_sha256, byte_size, status:"active"|"revoked", created_at}`。
- `DatasetRow = {dataset_id:64hex, owner_type, owner_id, name, description, default_branch, cube_contract, created_at}`（**无行主键**）；retention plan 携带的 `dataset_row_id` 是行 uuid，两者并存。
- `VersionRow = {version_id, parent_version_id, data_object_id, content_sha256, byte_size, branch, action, provenance, workflow_run_id, created_at}`。
- `RefRow = {ref_type:"branch"|"tag", ref_name, version_id, generation, created_at}`。
- `CommitResult = {version_id, parent_version_id, data_object_id, branch, generation, version_deduped, ref_moved}`。

**逐端点实测修正（对 §1.1/1.2 表的覆盖）**

1. vector/scan → **GeoJSON FeatureCollection**：`{type:"FeatureCollection", features[], properties:{row_groups_total, row_groups_read, truncated, window}}`。
2. cubes/window → `{bands:{band:[t][y][x]}, times[], crs, transform:6 参|null, nodata, ref}`。
3. cubes → `{status:"success", success, ref:"ref:cube/<16hex>", cube_id, path(服务器路径,不透明), title, times[], steps, content_fingerprints, ...DurableDisclosure}`。
4. cubes/revise → 同 build + `revision_of` + `first_step_preview`（updates>64 服务端静默截断）。
5. objects/verify → `state` 为**开放联合**：`verified|manifest_missing|blob_missing|digest_mismatch` + virtual 深度态 `virtual_children_missing|virtual_child_corrupt|virtual_owner_mismatch|virtual_child_*`。
6. cubes/rs → 同 build + `variables[]`（sorted）+ `labeled_projection`；role ∈ `optical|sar|cloud_mask|quality_mask`；optical→reflectance(time,band,y,x)、sar→sigma0(time,polarization,y,x)（pol 大写）、mask→(time,y,x) uint8。
7. cubes/labeled/window → `{variables:{name:嵌套数组}, coords:{dim:(number|string)[]}, slices, attrs:{crs, transform, nodata, nodata_per_variable, cube_schema_version, dims}（六键恒在值可 null）, selection_plan:{cells, touched_chunks, total_chunks, slices}, ref}`。
8. publish → `{published:[{object_id, artifact_id:"art_lh_<16hex>", revision_no, revision_created, deduped}], unknown[], forbidden[]}`（幂等）。
9. revoke → `{revoked[]（tombstone）, unknown[]}`；**无 session_id**。
10. catalog → `{items[], count, total:**string**（">=10000" 形态的下界）, total_bounded, limit, offset, next_offset}`。**REST 未暴露 bbox/tags 参数**（服务层支持但路由没接）。
11. catalog/stac → **包装结构** `{collection: STAC Collection, items: STAC Item[], skipped: string[]}`；投影前提 bbox 4 元 + time_start，不满足进 skipped（诚实披露，截 64 字符）。
12. scrub → `{success, data_object_id, state, chunks_total, chunks_checked, missing[], corrupt[], etag_mismatch[], etag_checked, mode}`。
13. gc/plan（admin，非 admin→403）→ `{candidates[], deletable_blobs[], protected_count, scanned_manifests, watermark(epoch 秒), grace_hours, requested_grace_hours, registry_ttl_floor_hours, token, _scan(内部缓存,巨大,忽略)}` —— **无预估释放字节量字段**。
14. gc/execute → `{deleted_manifests[], deleted_blobs[], skipped_protected_blobs[], skipped_stale}`；漂移 → 409。
15. objects/lineage → `{root, ancestors:[{id,kind,depth}]（root 不在内）, edges:[{child,parent}], truncated}`；深度≤8、节点≤10000。
16. projects/{pid}/objects/{oid} → `{success, manifest, catalog}`；不可解析 → **410**。
17. datasets/{id}/versions/{vid} → VersionRow **平铺顶层** + `{manifest, content_available, commit}` 三键。
18. datasets/{id}/lineage → `{version_id, chain:VersionRow[], depth, truncated}`。
19. retention/plan → `{dataset_row_id, dataset_id, candidates[], candidate_count, protected_by_refs, protected_by_window, scanned_versions, max_versions, min_age_hours, token}` —— 无字节量。
20. retention/execute → `{dataset_row_id, dataset_id, pruned[], pruned_count, skipped_protected[], skipped_overflow}`；漂移 → 409。

**易踩坑清单（类型化纪律）**

- 错误体**没有 typed code**：只有 `{detail: string}`，按 status 分支（400/403/404/409/410/422）。
- `total` 是字符串（有界诚实下界）；`next_offset: number|null`。
- cube 窗口键是 `bands`，labeled 窗口键是 `variables`——两套词表。
- 时间格式两套：catalog/dataset 为 `isoformat()+00:00`；STAC 投影转 `Z`。
- dataset 路径参数是 64hex 描述符 id；`name` 正则 `[A-Za-z0-9._-]{1,128}`。
- 服务器路径（`path`/`content_fingerprints` 键）出现在成功响应里——UI 不展示。
- gc plan 的 `_scan` 巨大——前端类型容忍但忽略。
- commit `provenance` 白名单 13 键，越界键 → 400。
- 422 是路由层前置校验（window 全无切片/负切片、labeled 全空选择），与服务层 400 语义重叠——两者都要处理。

## 2. 前端复用面清单

### 2.1 Rail tab 注册（P2 接入点）

| 触点 | 文件 | 本线动作 |
|---|---|---|
| tab 词表 | `lib/store/hud-types.ts:96` `LeftTab` 联合 | 追加 `'lakehouse'` |
| rail 分组 | `components/layout/nav-rail.tsx:54` `RAIL_GROUPS` | 追加一行 `{ key: 'lakehouse', icon, label: '数据湖' }` |
| 模式词表 | `lib/store/slices/workbenchSlice.ts:36` `MODE_TABS` | explore/analyze 词表追加 `'lakehouse'` |
| 面板元数据 | `components/layout/context-panel.tsx:100` `PANEL_META` + 渲染分支 | 追加 meta 行 + `<PanelErrorBoundary>` 分支 |

NavRail 已是 roving tabindex + ArrowUp/Down/Home/End 自动激活；ContextPanel 统一 PanelHeader + 280–420px 拖宽；tab 切换即卸载。**任务书"append 一行注册表项"在物理上需要上述 4 处注册行（词表/分组/meta/渲染），均为 append 级编辑，不触碰既有行。**

### 2.2 Tab 壳组织样例（data-sources-tab，577 行 + 11 文件）

- 子页签：`SUBTABS` 数组 + role=tablist roving tabindex + 方向键 activation-on-focus。
- 数据获取：`use-data-sources.ts` / `use-spatial-catalog.ts` hook（debounce 300ms + AbortController 竞态丢弃）。
- 呈现：`CatalogToolbar` → `CatalogItemCard` 列表 → `DatasetInspector` 右栏 → modal（descriptor/preview）。
- 状态三件套：`components/shared/{empty-state,loading-state,inline-notice}.tsx`（token 化，明暗双主题自动）。
- 错误：`describeApiError` / `isApiError`（transport 导出）+ `useToastStore`。
- 本线对应：`components/sidebar/lakehouse/` 目录 + `lakehouse-tab.tsx` 入口 + `use-lakehouse*.ts` hooks。

### 2.3 API 层模式（P1 对齐）

- transport：`lib/api/transport.ts` `apiFetch`（ApiError 带 status/body/requestId、X-Request-ID、超时、幂等重试 opt-in、ownerToken → X-Session-Token）。**A 线所有物，禁改。**
- Fast Path：`lib/api/get-fast-path.ts` `fastGet`（GET 去重 + 5s LRU）——catalog 列表可用。
- 无 msw 依赖（`package.json` 无 msw）：任务书所写"msw fixtures"在本仓的等价物是 **`vi.stubGlobal('fetch')` + jsonOk/jsonErr 工厂**（`lib/api/project.test.ts` 模式）与组件层 **`vi.mock('@/lib/api/…')` selector 式模块 mock**（`data-sources-tab.test.tsx` 模式）。P1 fixtures 按此惯例建 `test/lakehouse/fixtures.ts`（正常/空/错误三态 × 29 端点），**不引入新依赖**。
- 凭据：`ownerToken` 显式参数透传（ContextPanel → tab props，同 #463 语义）。

### 2.4 上图管线（P4/P7）

- 矢量：`VECTOR_TILE_THRESHOLD = 5000`（`lib/mapspec-runtime/adapter.ts`）——≤5000 要素走 GeoJSON source 直挂，>5000 走 MVT `_tileUrl`（`lib/map-kit/tile-url.ts` buildMvtTileUrl）。
- 凭据注入：`lib/map-kit/tile-auth.ts` `buildTileTransformRequest(getSessionToken)`——MapLibre 原生 fetch 不走 apiFetch，需 transformRequest 注入 Bearer/X-Session-Token；仅 first-party URL。
- 图层类型：`lib/types/layer.ts` `Layer.type = 'vector'|'raster'|'tile'|'heatmap'|'fill-extrusion'`；`addLayer`（layersSlice）挂 HUD。
- nodata 掩膜前端现状：**无现成实现**（栅格渲染走后端出图/瓦片）；P7 的 per-variable nodata 掩膜 + colorbar 是本线新增（canvas 逐帧渲染）。
- 对比：`components/map/comparison/`（swipe clip-path + 键盘 slider + 相机同步纯函数 `comparison-sync.ts`）——P6 快照 diff 复用其同步纯函数与交互模式。

### 2.5 其他复用

- 血缘渲染：`components/agent/analysis-graph-panel.tsx`（只读 DAG 卡片式渲染 + 状态配色映射）——P8 参考其渲染结构（cube→dataset→source 链为线性祖先链，比 DAG 简单）。
- 表格：`components/shared/tabular-data-grid.tsx`（虚拟表格）——P4 结果面板复用。
- 图表：`components/chat/chart-core.tsx`（recharts 主题化 + 18 种 kind）——P4 统计摘要复用。
- 图例：`components/map/legends/continuous-legend.tsx`——P7 colorbar 联动复用。
- reduced-motion：`lib/hooks/use-prefers-reduced-motion.ts`——P7 播放器遵守。
- 虚拟行：`lib/hooks/use-virtual-rows.ts`——长列表复用。
- 确认对话框：`components/shared/confirm-dialog.tsx`——P6 publish/revoke 用。

### 2.6 测试基线

- vitest 4 + RTL + jsdom；`test/setup.ts` 全局；coverage thresholds **75/70/75/60**（lines/functions/statements/branches）。
- visual：`test/visual/capture.mjs` Playwright 驱动真实 app（API 拦截 fixture 化）× 4 viewport × 明暗主题。
- 命令：`pnpm typecheck`（双 tsconfig）、`pnpm lint --max-warnings 0`、`pnpm test -- --run <scope>`（日常 scope 化，禁全量）。

### 2.7 i18n

`messages/` 目录**不存在** → G 线框架未合。P0–P9 全部文案硬编码中文（与 NavRail/ContextPanel 既有文案同风格），PR 协调点列「待 G 收编清单」。

## 3. 端点缺口与协调点（不自行补后端）

1. **【联调实证 · 后端 bug】`Conversation.session_id` 属性不存在**：真实后端（worktree 启动 `uvicorn app.main:app`，sqlite）上，凡在守卫后解引用 `conv.session_id` 的 lakehouse 端点全部 500（`AttributeError: 'Conversation' object has no attribute 'session_id'`）——波及 dataset 版本层全部 12 端点、cubes POST 族、objects/{id}/lineage 等（模型主键是 `id`，见 `app/models/db_model.py:212`）。疑似改名重构遗留（V8 e2e 以 mock conv 通过，未暴露）。**建议后端线修复：`str(conv.session_id)` → `str(conv.id)`**。前端不受阻：所有错误按 status 分支，500 落 InlineNotice 优雅降级；catalog / STAC / fail-closed 404 已实测真实可用。
2. **catalog 缺 bbox/tags 检索参数**：schema `CatalogSearchQuery` 定义了 `bbox`/`tags`，但 REST GET 端点未暴露这两个 query 参数（只支持 kind/time/producer）。UI 按实际暴露参数做服务端过滤 + 客户端收窄；差距记协调点。
3. **无快照 diff 端点**：快照对比 = 前端拉两个版本解析响应做本地字段 diff（P6 已实现；位图级双屏对比复用 P7 管线，入口提示见 version-workbench）。
4. **gc/retention execute 为 admin/重验语义**：UI 只读展示 + 计划，执行动作归 C/F 线（任务书 §2 P8 已划定）。实测非 admin 调 gc/plan 返回 **401**（勘察先验 403）——UI 按 status 无关的消息展示，不受影响。
5. **cube 时序上图无瓦片端点**：window 读返回 JSON 数组，前端 canvas 渲染（P7 选型，见 ADR-0141；README「No Raster Push」红线一致）。
6. **真实空域 catalog 返回 `total_bounded:false`**（fixtures 空态写的是 true）——类型 boolean 兼容，UI 仅用于 title 提示，无行为分支。
7. **i18n**：`messages/` 不存在，G 线框架未合 —— 全部文案硬编码中文，待 G 收编（清单 = lakehouse 目录下全部用户可见字符串）。
8. **visual capture 会话恢复路径在本机不填充**（既有 map-legends surface 同样表现，非本线引入）——lakehouse surfaces 以诚实空态留档；信息密度 fixtures 已就位（catalog/datasets GET），会话恢复修复后即生效。

## 4. 交付台账（任务 → 文件 → 测试 → 证据）

| 任务 | 交付文件（新） | 测试 | 证据 |
|---|---|---|---|
| §0 复核/契约 | `frontend/docs/lakehouse-ui-recon.md`（本文） | — | 29 端点字段级契约表（S1 实测）|
| P1 typed client | `lib/api/lakehouse.ts` | `lib/api/lakehouse.test.ts`（43 用例：每端点正常+错误+传输语义） | scope 跑全绿 |
| P1 fixtures | `test/lakehouse/fixtures.ts` | 被全部 lakehouse 测试消费 | 正常/空/错误三态 × 29 端点 |
| P2 rail tab | `lib/store/hud-types.ts`(+1 词表)、`components/layout/nav-rail.tsx`(+1 行)、`lib/store/slices/workbenchSlice.ts`(explore/analyze 词表 +1)、`components/layout/context-panel.tsx`(+meta 行 +渲染分支) | `components/layout/nav-rail.test.tsx`（更新 explore 词表期望） | typecheck 全绿；既有 store/layout 测试全绿 |
| P2 tab 壳 | `components/sidebar/lakehouse/lakehouse-tab.tsx`（6 子页签 roving tabindex） | `lakehouse-tab.test.tsx`（tablist WAI-APG/目录三态/动线/分页） | 13 用例 |
| P3 目录 | `use-lakehouse-catalog.ts`、`catalog-toolbar.tsx`、`catalog-item-card.tsx`、`object-detail-panel.tsx` | tab 测试覆盖（三态/动线/分页/manifest 拉取） | `total` 字符串下界诚实呈现 |
| P3 datasets | `use-lakehouse-datasets.ts`、`datasets-panel.tsx` | tab 测试（project 域诚实空态、清单→详情→版本历史） | — |
| P4 查询 | `query-forms.tsx`、`query-pane.tsx`、`query-results.tsx`、`lib/hooks/use-lakehouse-history.ts` | `query-pane.test.tsx`（主链/本地校验/上图/历史）、`raster-pipeline.test.ts`（buildRequest 校验） | window/labeled 全空选择本地 422 等价拒绝 |
| P4 结果/上图 | `lib/map-kit/raster-canvas.ts` | `raster-pipeline.test.ts`（统计/掩膜/色带/像素） | HeatmapRasterSource 通道挂层断言 |
| P5 STAC | `stac-explorer.tsx` | `panels.test.tsx`（collection/条目展开/assets/skipped/links 分页/几何上图） | — |
| P6 版本 | `version-workbench.tsx` | `panels.test.tsx`（publish 确认+幂等报告、403 持久报告、tombstone、diff 双栏高亮+双屏） | ConfirmDialog 复用 |
| P7 播放器 | `lib/map-kit/raster-timeline.ts`、`timeline-player.tsx` | `raster-timeline.perf.test.ts`（5 用例：绝对预算门控/丢帧策略/LRU/懒加载/跳步去重）、`timeline-player.test.tsx`（reduced-motion/键盘/colorbar） | LAKEHOUSE_PERF_DEDICATED=1 专用跑取证（S2 台账） |
| P8 运维 | `ops-panel.tsx` | `panels.test.tsx`（检视/血缘/scrub 状态/GC 只读纪律——有 plan 无 execute/403 警告） | 执行动作不在本线 |
| P9 视觉 | `test/visual/capture.mjs`（+2 surfaces +fixtures +clickSubTab） | Playwright 专用跑 | light/dark × 4 viewport × lakehouse-catalog/datasets |
| 契约文档 | ADR-0141 `docs/adr/0141-lakehouse-cube-explorer-ui.md`、`CHANGELOG.md` Unreleased 条目 | — | — |

**里程碑取证**：P3 后全量 307 文件/2918 测试/lines 79.16%；P6 后全量 311 文件/2952 测试/lines 78.55%（branches 68.51 / functions 78.05 / statements 81.42，门禁 75/60/70/75 全过）。typecheck 双 tsconfig 0 error；`pnpm lint --max-warnings 0` 绿。后端零改动：`git diff origin/master -- app/ migrations/` 为空。
