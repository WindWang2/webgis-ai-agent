# 工作区 UI 完整化 V9 — 勘察报告（P0）

> 线：feat/workspace-ui-v9 · ADR-0143 · 基线 origin/master `8b5b8375`
> 本文档是 P0 勘察产出：端点契约表 + 信息架构扩展点 + 复用资产 + 《复核纪要》。
> 真相源：`app/api/routes/project.py`（1,869 行 / 48 端点，实测）与 `frontend/lib/api/project.ts`。

---

## 1. 《复核纪要》（§0.2 产出，§7 进 PR 描述）

### 1.1 PR 复核（gh pr list --state all，200 条搜索）

| PR | 状态 | 关系判定 |
|---|---|---|
| #1235 lakehouse cube explorer（ADR-0141，`feat/lakehouse-ui-v9`） | OPEN | **D 线**。其 "snapshot diff" 是 lakehouse **cube 版本快照 diff**（lakehouse 目录），非项目 workspace snapshot。目录边界不重叠（其改 `components/sidebar/lakehouse/**`，本线禁入）。其 "catalog" 是数据目录浏览——本线 datasets 面板是**项目域数据集**（project datasets，`/api/v1/projects/{id}/datasets`），二者 API 面不同。无重叠。 |
| #1236 V9 lifecycle foundation（ADR-0140，`foundation/data-lifecycle-v9`） | OPEN | **后端线**（quality rule engine / lifecycle policy / gc loop）。本线纯前端、按 master 现有端点开发，禁改后端 → 不冲突。若其合并带来新端点形状变化，属协调点（见 §8）。 |
| #1149 GIS Data / Artifact / Workspace Foundation V3 | MERGED | 后端 foundation（artifact_registry 等），是本线消费的端点来源。非 UI 线。 |
| #1136 Map Product version diff workspace | MERGED | 即现有 `map-product-versions.tsx` 面板，本线 P7 只做导航整合，不动其内部。 |
| #349 / #346 workflow recovery / lineage workspace | MERGED | 即现有 `components/sidebar/workflow/` 8 文件（E 线前身），本线只读引用。 |

**结论：无在途/已并 UI 线与本线范围重叠。照单执行。**

### 1.2 issues 复核

- **#1215**（project.py restore/rerun 同步 SQLAlchemy 阻塞事件循环）：CLOSED。抽查 master 成立——`project.py` 中 restore/rerun 族 handler 已全部 `asyncio.to_thread` 包装（:813/:832/:862/:954/:959/:966/:1018/:1022 等 20+ 处）。无需本线动作。
- **#1214**（SSRF 哨兵串）：了解即可，纯后端，与本线无交集。
- **#1221**（无 session 上传项）：上传面归 upload 线，本线 P2 仅链接既有 `components/upload/upload-zone.tsx`，不重做上传、不碰 `lib/api/upload.ts`。

### 1.3 分支复核

`git branch -a | grep -iE "workspace|project-|artifact"` → 仅本线 `feat/workspace-ui-v9`（在 worktree 中签出）。无平行工作分支。

### 1.4 现状核查（§0.2.4 确认）

现有覆盖（`project-tab.tsx` 375 行 + `use-workflow-workspace.ts` 605 行 + `workflow/` 8 文件 + `map-product-versions.tsx` + `carto-memory-panel.tsx`）：
- ✅ projects 选择/创建、datasets **只读挂载列表**、workflows/revisions/runs/compare/replay/resume/rerun/promote（rerun 经 map-product-versions）、carto-memory、map-product versions+diff。
- ❌ **无 UI**：datasets CRUD / dataset 详情与双模式预览 / artifact 列表-pin-clone-下载-版本 / 血缘图可视化（现有 lineage-list 仅文字列表）/ workspace snapshots / quality-audit / quality-repair / data-gc。与本线任务书声明完全一致。

### 1.5 与任务书的偏差记录（以 master 实测为准）

1. **「msw fixtures」**：仓库前端**无 msw 依赖**（package.json 无 msw；test/ 无 setupServer）。既有 mock 契约是 `vi.hoisted + vi.mock('@/lib/api/...')` + fixture 工厂（见 `project-tab.test.tsx`、`use-workflow-workspace.test.ts`）。本线沿用该模式建 **三态 fixture 工厂**（success/empty/error + 大列表/血缘图形状），不引入新依赖。
2. **「复用 analysis-graph-panel.tsx 做血缘图」**：实测该组件是**会话域执行 DAG 投影面板**（ADR-0097，内部自取 `/api/v1/sessions/{sid}/analysis-graph`，不接收任意图 props），与 artifact lineage（parents/consumers 边表）模型不同源。**偏差决策**：在 `components/sidebar/project/` 内自建 `lineage-graph.tsx`（SVG DAG，≥50 节点渲染断言）+ `lineage-adapter.ts`（LineageGraph → nodes/edges 适配层，为未来 C 线列级下钻留扩展点）。不改 `components/agent/analysis-graph-panel.tsx`。
3. **「comparison/ swipe 组件」**：实际路径 `components/map/comparison/comparison-view.tsx`，是深度耦合 session MapSpec/HUD store 的**工作台级覆盖层**（react-map-gl 双实例 + 相机同步），无法嵌入侧栏面板复用其内部。**偏差决策**：产物地图双屏对比**不实现**（记录于 §2.10 协调点）；地图级对比走两条真实路径——a) P7 交叉导航把用户带到既有 map workspace / Map Product 版本台账（`onViewVersionLedger` → scrollIntoView）；b) 数据集预览的 SVG 足迹图为真实几何投影（非示意），可对两数据集分别目视对比。不改 comparison-view 语义。
4. **端点数**：任务书 ~45，实测 **48**（`@router.(get|post|put|patch|delete)` 计数）。
5. **`tabular-data-grid.tsx` 位置**：实为 `components/shared/tabular-data-grid.tsx`（re-export `components/explorer/tabular-data-grid.tsx`）。§8 的 `components/table/**` 扩展点 wrapper 落在 `components/sidebar/project/` 内部 wrapper，不移动原组件。
6. **i18n**：无 `lib/i18n` → 按 §8 契约：中文硬编码 + 待收编清单（见 §8 记录）。

### 1.6 目录与 ADR

- ADR watermark（master `docs/adr/`）= **0137**；在途 PR 占 0140（#1236）/ 0141（#1235）→ 本线按任务书占 **ADR-0143**，无冲突。
- 后端零改动承诺：`git diff origin/master -- app/ migrations/` 交付前必须为空。

---

## 2. project.py 端点契约表（48 端点，实测）

全局约定：前缀 `/api/v1/projects`；分页唯一形态 `limit`(默认50,clamp 1-200)/`offset`，信封 `Page[T]{items,total,limit,offset,has_more}`；错误 FastAPI `{"detail":...}`（404 不泄露存在性）；**project.py 无任何端点返回 job_id**（run/replay/restore/gc 全同步长阻塞）；无 SSE/WS；无 staging 字段。

### 2.1 projects（4）

| 方法 | 路径 | 请求 | 响应 | 错误 |
|---|---|---|---|---|
| POST | `/` | `ProjectCreate{name≤255 必填, description?, metadata_json?}` | 201 `ProjectResponse{id,org_id?,owner_id?,name,description?,status,metadata_json,created_at,updated_at}` | 401 |
| GET | `/` | `limit?,offset?` | `Page[ProjectSummary]` | — |
| GET | `/{project_id}` | — | `ProjectResponse` | 404 |
| PUT | `/{project_id}` | `ProjectUpdate{name?,description?,status?:"active"\|"archived",metadata_json?}` | `ProjectResponse` | 401,404 |

### 2.2 datasets（3）⚠️ 无 rename / 无 preview / 无 schema / 无独立详情端点

| 方法 | 路径 | 请求 | 响应 | 错误 |
|---|---|---|---|---|
| POST | `/{project_id}/datasets` | `DatasetAttach{name≤255 必填, source_type 必填("upload"\|"layer"\|"external"\|"vector"\|"raster"), source_ref?, schema_profile?, crs?="EPSG:4326"}` | `ProjectDatasetResponse{id,project_id,name,source_type,source_ref?,schema_profile,crs?,quality_status?="unchecked",version_fingerprint?,created_at}`（唯一回 schema_profile 的地方） | 401,404 |
| DELETE | `/{project_id}/datasets/{dataset_id}` | — | `{status:"success",message}`（**软删 tombstone**，INV-DEL1；**无引用检查**） | 401,404 |
| GET | `/{project_id}/datasets` | `limit?,offset?` | `Page[ProjectDatasetSummary{id,project_id,name,source_type,crs?,quality_status?,created_at}]`（**不含 schema_profile**） | — |

### 2.3 artifacts（5）⚠️ 无 download / 无详情 / 无 revisions 列表端点；pin/clone/lineage 路径**不含 project_id**

| 方法 | 路径 | 请求 | 响应 | 错误 |
|---|---|---|---|---|
| GET | `/{project_id}/artifacts` | `limit?,offset?` | `Page[ArtifactSummary{id,project_id,name,artifact_type,format?,crs?,created_at}]` | — |
| GET | `/artifacts/{artifact_id}/lineage` | — | 裸 dict `LineageGraph{artifact_id, parents[], consumers[]}`（parents 元素含 `source_dataset_id/source_dataset_fingerprint/depth`；BFS depth 默认 5 不可调；无顶层 nodes/edges，邻接靠 `parent_artifact_id`） | 404 |
| POST | `/artifacts/{artifact_id}/pin` | `ArtifactPinRequest{pinned?:bool=true}` 可省 body | `ArtifactPinResponse{status:"ok",artifact_id,revision_no?,content_sha256?,pinned,pinned_at?}` | 401,404 |
| DELETE | `/artifacts/{artifact_id}/pin` | — | `ArtifactPinResponse`（pinned=false） | 401,404 |
| POST | `/artifacts/{artifact_id}/clone` | 无 body | `ArtifactCloneResponse{status:"ok",artifact_id(新),source_artifact_id,name?,content_location?,content_sha256?}`（指针克隆零复制） | 401,404 |

### 2.4 workflows / runs（12，已有前端消费）

POST `/{id}/workflows`（WorkflowCreate{name,graph_spec 必填}→WorkflowResponse）；GET `/{id}/workflows`（Page）；POST `/{id}/workflows/{wf}/run`（**同步**→WorkflowRunResponse）；GET `/{id}/runs`（query workflow_id?,limit,offset→Page）；POST `/{id}/runs/compare`（query run_a_id/run_b_id→RunComparisonResponse）；GET revisions（Page）/ revisions/{rid}；GET `/{id}/runs/{run}`；POST replay{mode:exact|latest}；POST resume{allow_rerun=false}；POST rerun{from_step?,input_bindings?}（bindings 无 from_step→422）；POST `/{id}/runs/{run}/promote-artifacts`→`PromoteArtifactsResponse{status:"ok",materialized:int,artifacts:[{artifact_id,status∈promoted|already_promoted|no_session_context|session_expired|store_unavailable|quota_exceeded,content_location?}],note}`。

### 2.5 workspace snapshots（7）⚠️ 全部要求登录；list 硬上限 50 非 Page 信封；无 diff 端点

| 方法 | 路径 | 请求 | 响应 | 错误 |
|---|---|---|---|---|
| POST | `/{project_id}/workspace/snapshots` | `{session_id 1-128 必填, label?≤96, materialize?:"none"(默认)\|"claimed"\|"all"}` | `{status:"ok",project_id,home:"project"\|"session",snapshot_id,label,durable_pointers:int,materialize_skipped:str[],snapshot:dict}` | 401,404,500 |
| GET | `/{project_id}/workspace/snapshots` | query `session_id?` | `{project_id,count:int,bounded:int=50,items:[WorkspaceSnapshotSummary{snapshot_id,label?,created_at?:float(秒),artifacts:int,layers:int,project_id,home}]}` | 401,404 |
| GET | `/{project_id}/workspace/snapshots/{sid}` | query `session_id`（**必填**） | **SnapshotVerification.to_dict()**：`{snapshot_id,exists,integrity_ok,restorable,artifacts:{total,live,missing[]},layers:{total,live,missing[]},mapspec_available,integrity:{artifact_id:"verified"\|"digest_mismatch"\|"pointer_missing"\|"no_pointer"}}`（实测 `app/services/workspace/snapshot.py:170`） | 401,404 |
| POST | `/{project_id}/workspace/snapshots/{sid}/restore` | `{session_id 必填, mode:"verify"(默认)\|"register"}` | `{mode,verification:{...同上}}`；register 追加重注册/重物化结果，死 ref 降级 `expired`/`degraded` 披露 | 401,404,400 |
| POST | `/{project_id}/workspace/snapshots/{sid}/clone` | `{source_session_id 必填, target_session_id 必填}` | clone 裸 dict | 401,404 |
| DELETE | `/{project_id}/workspace/snapshots/{sid}` | query `session_id`（必填） | `{status:"deleted",snapshot_id,home}` | 401,404 |
| GET | `/{project_id}/workspace` | query `session_id?` | 裸 dict（快照数、生命周期 artifact 汇总、layer refs、durable 覆盖率 %） | 401,404 |

### 2.6 quality（2）⚠️ 请求体需 GeoJSON（前端聚合 data-fabric preview 获取）

| 方法 | 路径 | 请求 | 响应 | 错误 |
|---|---|---|---|---|
| POST | `/{project_id}/quality-audit` | body `{geojson:FC 必填}`；query `crs?="EPSG:4326"` | `{dataset_id,total_features,issue_summary:{info,warning,error,blocking},issues:[{dimension,code,level,message,feature_index?,details?}],overall_status:"passed"\|"warning"\|"blocking",truncated:bool,truncated_count:int,truncation_details?}`（issues 可达 5000，靠截断披露） | 400,404 |
| POST | `/{project_id}/repair` | `{geojson 必填, operations?:["make_valid","remove_empty",...], session_id?, source_ref?, dataset_id?, issue_codes?:str[]}` | `{project_id,operations_applied,ops_evidence,repair_logs,logs_count,feature_count,feature_count_before,repaired_ref,ref_registration_error,repair_evidence,lineage_status:"recorded"\|"skipped"\|"dataset_not_found"\|"error",lineage_artifact_id?,lineage_error?,repaired_geojson_preview(≤50 features)}` | 401,400,404 |

### 2.7 data-usage / gc（3）⚠️ 均同步；无 staging/回滚端点；宽限期=字段展示

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| GET | `/{project_id}/data-usage` | — | `{project_id,usage:{bytes,artifact_count,revision_bytes},limits:{max_bytes,max_artifact_count,max_revision_bytes_per_artifact},quota:{allowed,reason},retention:{policy(含 grace_hours),upcoming_candidates:int,upcoming_candidate_blobs:int}}` |
| POST | `/{project_id}/data-gc/plan` | —（dry-run） | `{project_id,scoped_to_project,retention:{policy,disabled,candidate_revision_count,candidate_blob_count,candidate_blob_bytes,protected_counts:dict,protection_scan_truncated:bool,candidate_revisions:[{artifact_id,revision_no,age_days,byte_size}](≤64),candidate_blobs:[{sha_prefix(12),byte_size}](≤64)},promotion_store_gc:{scoped_to_project,grace_hours?,deletable_count,deletable_bytes,deletable:[{sha_prefix(16),bytes}](≤64)}}` |
| POST | `/{project_id}/data-gc/execute` | `{confirm:bool}`（false/缺省→400） | `{project_id,retention:{deleted_revisions[](≤64),deleted_revision_count,deleted_blobs[](≤64),deleted_blob_count,bytes_freed,skipped_protected_count,skipped_protected[]},orphan_revisions:{deleted_count,deleted_revision_ids[](≤64)},skipped_protected:[{key,reason}]}` |

### 2.8 map-products（9，已有 `lib/api/map-product.ts` 全量消费）+ carto-memory（3，已有 `carto-memory-panel.tsx` 消费）

map-products：GET list（Page，newest first）/ GET `{version_no}`（含 compute_plan≤64、artifact_ids、diff_summary）/ GET `{from}/diff/{to}`（5 维布尔 + analysis_recomputation_expected + details）/ POST create / GET open / POST restore{mode:style_only|full, session_id 必填}（full=同步重放 run）/ POST fork / POST merge / POST rerun（style-only 409）。carto-memory：GET（无分页全量 facts + counts）/ DELETE `{fact_id}`（软删 retired）/ POST `{fact_id}/activate`。

### 2.9 跨族聚合端点（本线前端聚合依据，只读引用）

- `GET /api/v1/data-fabric/catalog/{item_id}/preview?limit≤100`（需登录）→ `{dataset_id,features[],total_count,schema_info,metadata}`；413 过大 / 502 源故障 / 400。**用途**：dataset 表格预览 + 质量审计/修复的 GeoJSON 来源（§0.3 前端聚合条款）。
- `GET /api/v1/data-fabric/catalog/{item_id}/descriptor` → schema 描述。
- 任务中心（只读展示族）：`/api/v1/tasks/jobs`（`use-job-center` 已封装）。

### 2.10 端点缺口 → 协调点清单（进 PR 描述）

| 任务书预期 | 后端现实 | 本线处置 |
|---|---|---|
| dataset 重命名 | 无端点 | 不做；协调点（需 `PATCH /datasets/{id}`） |
| dataset schema/详情端点 | 无（schema_profile 仅 attach 响应返回一次） | 详情面板展示 list 字段 + attach 时捕获的 schema_profile（会话内）；行数/大小/所有权/可见性无端点来源，不展示；数据集级血缘入口无反向索引端点，不提供——均列协调点 |
| dataset「上传入口链接」 | upload 区在另一 sidebar tab（rail 状态归布局层，本线目录边界外） | 文字指路提示；真实跨 tab 深链列入协调点（需 rail tab 状态协同） |
| artifact 下载端点 | 无（下载保护仅覆盖 `/api/v1/export/download/*`） | 下载中心降级为「存储引用 + sha256 复制」；协调点（需 artifact download 端点接入 authenticated-download） |
| artifact revisions 历史端点 / 版本对比（双栏 diff + 地图双屏） | 无（pin 响应含 revision_no/content_sha256 单点） | pin 回执记录 revision 信息；版本维度对比由 Map Product 版本台账承接（P7 `onViewVersionLedger` 滚动导航）；协调点 |
| artifact 筛选（类型/时间/pinned） | 列表行无 pinned 字段 | 类型筛选（客户端）+ 时间排序（最新/最早）已实现；pinned 筛选无数据来源——pin 态为本会话易失镜像，协调点 |
| 快照 diff 端点 | 无 | **前端聚合**：两快照 verify 报告 + list 元数据结构化对比（counts/missing/integrity/mapspec） |
| 快照 clone | 有端点（`POST …/snapshots/{sid}/clone`，source/target session 必填） | 已实现：时间线节点「克隆」动作 + 目标会话输入 |
| restore/gc 进度 job | 无 job_id（同步） | 长超时 + busy + 结果报告展示；`expired/degraded/skipped_protected` 如实披露；协调点（durable job 化后接 use-job-center） |
| gc 回滚/staging | 无 staging 字段 | 展示 `grace_hours` 宽限期 + upcoming_candidates 预告；协调点 |
| quality repair「dry-run 结果树」 | repair 端点无 dry-run 参数 | 审计报告即预检依据：执行前列出将应用的操作 + 两段确认；协调点（repair dry-run 参数） |
| P8 visual snapshot（明/暗） | visual 走廊为 Playwright 全页截图（`test/visual/capture.mjs`），按 §0.4 仅最终门禁跑一次 | 最终门禁随 workbench 走廊统一取证；新面板不单独建基线（侧栏组件，RTL+a11y 断言覆盖），台账记录 |

## 3. 信息架构与扩展点

### 3.1 现有 store（use-workflow-workspace.ts）纪律（新 hooks 必须沿用）

- 每视图切换 `AbortController` + 自增 generation（stale 响应不回写）
- 写操作 action lock（防双提交）+ epoch（写后列表刷新守卫）
- 有界轮询：仅 active 状态轮询（RUN_POLL_INTERVAL_MS=3000 / MAX=40），`document.hidden` 暂停、visibilitychange 补拉
- 卸载统一 abort + 清 timer
- 错误面：`parseApiErrorDetail` / `ApiTimeoutError` 特判

### 3.2 本线新增信息架构（project-tab 内 append-only tab 区）

```
project-tab（容器，保持既有 projects/workflows/runs/compare 视图）
└── 新增资产区 tab 条（append-only）：数据集 | 产物 | 快照 | 质量 | 数据回收
    ├── project/dataset-manager.tsx      （P2 列表/详情/CRUD/双模式预览）
    ├── project/artifact-center.tsx      （P3 列表/详情/版本/血缘/下载/pin/clone/对比）
    ├── project/snapshot-timeline.tsx    （P4 时间轴/diff/restore 进度）
    ├── project/quality-panel.tsx        （P5 审计/修复）
    ├── project/gc-panel.tsx             （P6 dry-run/执行/回滚观察）
    ├── project/lineage-graph.tsx        （P3 血缘 SVG DAG + adapter）
    └── project/cross-nav.tsx            （P7 面包屑/上下文侧栏/定位动作）
```

状态 hooks：`lib/hooks/use-project-assets.ts`（datasets/artifacts/snapshots/quality/gc 各 slice，复用 §3.1 纪律）。

## 4. 复用资产接口（实测签名）

| 资产 | 位置 | 关键接口 |
|---|---|---|
| 虚拟化表格 | `components/shared/tabular-data-grid.tsx` → re-export `components/explorer/tabular-data-grid.tsx` | `TabularDataGridProps{ data?: rows|GeoJSON|QueryResult, features?, columns?, totalCount?, loading?, defaultPageSize?, pageSizeOptions?, enableSearch?, enableSort?, onRowClick? }` |
| 虚拟行 | `lib/hooks/use-virtual-rows.ts` | `useVirtualRows(...)` → `VirtualRowsResult` |
| 认证下载 | `lib/api/authenticated-download.ts` | `downloadWithAuth(...)`, `triggerBlobDownload(blob, filename)`, `isProtectedDownloadUrl`, `filenameFromUrl` |
| Job 中心模式 | `lib/hooks/use-job-center.ts` | 有界轮询（poll_after_ms、隐藏暂停、3 连错熔断、requestId+sessionId 陈旧保护）——restore/gc 进度联动照此模式 |
| 空态/加载/徽章 | `components/shared/{empty-state,loading-state,status-badge}.tsx` | 直接复用 |
| 确认操作 | `components/shared/confirm-action.tsx` | 两段式确认按钮（arm→confirm，250ms 防双击）；危险操作另叠 `confirm-dialog.tsx` |
| 快路径 GET | `lib/api/get-fast-path.ts` | `fastGet<T>(path, {forceRefresh, signal, params, ttlMs, label})` + `invalidateCache(prefix)` |
| 传输层 | `lib/api/transport.ts`（禁改） | `apiFetch<T>(path, {method, body, signal, timeoutMs, label})` → `ApiError{status, body, retryable}` |
| 上传入口 | `components/upload/upload-zone.tsx` | P2 仅链接跳转，不复刻 |

## 5. 测试模式（实测）

- mock：`vi.hoisted` API mock + `vi.mock('@/lib/api/...')`；fixture 工厂函数（`makeProject/makeWorkflow/page<T>` 先例）。
- 压力先例：`test/workbench-stress-500.test.tsx`、`workbench-virtual-10k.test.tsx` → 本线 100k 行虚拟化压力用例照此模式。
- visual：`test/visual/capture.mjs` + `workbench-layout-corpus.test.tsx`（明/暗快照走廊）。
- 覆盖率 ratchet 已全局：lines 75 / functions 70 / statements 75 / branches 60（vitest.config.ts）。

## 6. 风险与缺口（P0 结论）

1. **血缘图模型错配**：后端 lineage = 边表（parents/consumers），无坐标/层级；前端需自算 DAG 层次 → `lineage-adapter.ts` 承担（深度字段 `depth` 可用则优先）。
2. **大列表无分页端点风险**：见契约表逐端点标注；无分页的列表端点前端强制 `limit` + 虚拟化兜底。
3. **危险操作**：delete dataset / restore snapshot / gc execute 必须 `confirm-dialog`（不可绕过：disabled 态由 busy 锁控制）+ 结果回执 toast。
4. **restore/gc 是否返回 job 句柄**：见契约表标注；若是，进度联动按 use-job-center 模式（自管轮询，不引入新 WS）。
