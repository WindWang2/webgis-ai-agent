# API 契约勘察报告（P0, foundation/api-contract-v9 / ADR-0138）

生成方式：`python scripts/api_contract_recon.py`（从 `app.main:app` 路由表 dump，可重跑复核）。
数据基线：worktree 分支 `foundation/api-contract-v9`（基于 master 8b5b8375）。

## 1. 总量

- 端点总数 **220**（任务书估算 221；master 演进后实测为此值，销项以本表为准）
- 有 `response_model`：**97**（44%）
- 无 `response_model`：**123**（P2 销项表见 §6）
- 内联 BaseModel：**67** 个 / 15 文件（任务书估算 69）

## 2. 信封分布（静态启发式：response_model 名 + 路由文件源码含 ApiResponse 工厂调用）

- `detail_or_bare`: 208
- `api_response`: 12

> 注意：启发式按「文件内出现过 ApiResponse 工厂」计数，粒度是文件级，真实走信封的端点数以 P3 逐端点核验为准。

## 3. 分页分布

- `none`: 152
- `clamp_pagination`: 45
- `adhoc_limit`: 16
- `page_model`: 7

## 4. HTTP 方法异常项（语义核对进 ADR-0138）

- DELETE: 12 个 —— DELETE /api/v1/chat/sessions/{session_id}, DELETE /api/v1/tasks/jobs/{job_id}, DELETE /api/v1/tasks/{task_id}, DELETE /api/v1/tasks/status/{task_id}, DELETE /api/v1/uploads/{upload_id}, DELETE /api/v1/knowledge/document/{document_id}, DELETE /api/v1/templates/{template_id}, DELETE /api/v1/projects/{project_id}/datasets/{dataset_id}, DELETE /api/v1/projects/artifacts/{artifact_id}/pin, DELETE /api/v1/projects/{project_id}/carto-memory/{fact_id}, DELETE /api/v1/projects/{project_id}/workspace/snapshots/{snapshot_id}, DELETE /api/v1/data-fabric/sources/{source_id}
- PUT: 1 个 —— PUT /api/v1/projects/{project_id}
- PATCH: 0 个

## 5. 内联模型清单（P1 迁移来源）

- `app\api\routes\auth.py`: RegisterRequest, LoginRequest, TokenResponse, RefreshRequest
- `app\api\routes\chat.py`: ChatRequest, ChatResponse, MapStatePushRequest, CartographicRuntimeObservationRequest, MapActionAck, MapActionAckRequest, ToolExecuteRequest
- `app\api\routes\config.py`: LLMConfigRequest, LLMTestRequest, RagTestRequest
- `app\api\routes\data_fabric.py`: CreateDataSourceRequest, MaterializeRequest
- `app\api\routes\explorer.py`: StartExploreRequest, ExploreStatusResponse
- `app\api\routes\geocompute.py`: ExecutionNodeIn, ExecutionPlanIn, ExecutePlanRequest, ClusterRunResetRequest, LedgerLimitsRequest
- `app\api\routes\knowledge.py`: AddDocumentRequest, SearchRequest, DeleteRequest
- `app\api\routes\map.py`: VectorPdfRequest, GeoJSONExportRequest
- `app\api\routes\mapspec_mutations.py`: PatchLayerPresentationBody, PatchLayerStyleBody, PatchComponentBody, SetViewBody, RemoveLayerBody, RemoveComponentBody, DuplicateComponentBody, RebindComponentBody, ReorderLayersBody, SetLayoutBody, SetTimeBody, InitProjectBody, SetWorkbenchStateBody, PatchWorkbenchDeltaBody
- `app\api\routes\project.py`: MapProductRestoreRequest, MapProductForkRequest, MapProductMergeRequest, WorkspaceSnapshotSaveRequest, WorkspaceSnapshotRestoreRequest, WorkspaceSnapshotCloneRequest
- `app\api\routes\report.py`: GenerateReportRequest, ReportListResponse, ShareRequest
- `app\api\routes\task.py`: TaskStepResponse, TaskStatusResponse, TaskListResponse, TaskCancelResponse
- `app\api\routes\templates.py`: CreateTemplateRequest
- `app\api\routes\upload.py`: UploadResponse, UploadListResponse, ErrorResponse
- `app\api\routes\workflow_runtime.py`: PackageRegisterRequest, PublishRequest, InstantiateRequest, ChangeIn, ChangesRequest, RunRequest, NodeCancelRequest, CloneRequest

## 6. P2 销项表：无 response_model 端点全量清单

| method | path | tag | file |
|---|---|---|---|
| GET | `/metrics` |  | `.venv\Lib\site-packages\prometheus_fastapi_instrumentator\instrumentation.py` |
| GET | `/api/v1/health` |  | `app\api\routes\health.py` |
| GET | `/api/v1/health/live` |  | `app\api\routes\health.py` |
| GET | `/api/v1/ready` |  | `app\api\routes\health.py` |
| GET | `/api/v1/status/detailed` |  | `app\api\routes\health.py` |
| GET | `/api/v1/version` |  | `app\api\routes\version.py` |
| GET | `/api/v1/layers/data/{ref_id}` | 图层数据 | `app\api\routes\layer.py` |
| GET | `/api/v1/layers/data/{ref_id}/feature/{feature_id}` | 图层数据 | `app\api\routes\layer.py` |
| GET | `/api/v1/layers/data/{ref_id}/tiles/{z}/{x}/{y}.mvt` | 图层数据 | `app\api\routes\layer.py` |
| GET | `/api/v1/layers/descriptor/{ref_id}` | 图层数据 | `app\api\routes\layer.py` |
| GET | `/api/v1/layers/data/{ref_id}/raster-tiles/{z}/{x}/{y}.png` | 图层数据 | `app\api\routes\layer.py` |
| GET | `/api/v1/layer-types` | 元数据 | `app\api\routes\layer.py` |
| GET | `/api/v1/reports/shared/{share_code}/view` | 报告生成 | `app\api\routes\report.py` |
| GET | `/api/v1/reports/{report_id}/download` | 报告生成 | `app\api\routes\report.py` |
| POST | `/api/v1/chat/stream` | 对话 | `app\api\routes\chat.py` |
| GET | `/api/v1/chat/sessions` | 对话 | `app\api\routes\chat.py` |
| GET | `/api/v1/chat/sessions/{session_id}` | 对话 | `app\api\routes\chat.py` |
| GET | `/api/v1/chat/sessions/{session_id}/map-state` | 对话 | `app\api\routes\chat.py` |
| GET | `/api/v1/chat/sessions/{session_id}/chart-artifacts/{ref_id}` | 对话 | `app\api\routes\chat.py` |
| GET | `/api/v1/chat/sessions/{session_id}/table-artifacts/{ref_id}` | 对话 | `app\api\routes\chat.py` |
| GET | `/api/v1/chat/sessions/{session_id}/plan` | 对话 | `app\api\routes\chat.py` |
| POST | `/api/v1/chat/sessions/{session_id}/map-state` | 对话 | `app\api\routes\chat.py` |
| POST | `/api/v1/chat/sessions/{session_id}/cartographic-observation` | 对话 | `app\api\routes\chat.py` |
| POST | `/api/v1/chat/sessions/{session_id}/map-action-ack` | 对话 | `app\api\routes\chat.py` |
| GET | `/api/v1/chat/skills` | 对话 | `app\api\routes\chat.py` |
| DELETE | `/api/v1/chat/sessions/{session_id}` | 对话 | `app\api\routes\chat.py` |
| GET | `/api/v1/chat/tools` | 对话 | `app\api\routes\chat.py` |
| POST | `/api/v1/chat/tools/execute` | 对话 | `app\api\routes\chat.py` |
| POST | `/api/v1/chat/sessions/{session_id}/workflow-resume-anchor` |  | `app\api\routes\workflow_resume.py` |
| POST | `/api/v1/chat/workflow-resume/{anchor_id}` |  | `app\api\routes\workflow_resume.py` |
| POST | `/api/v1/export` | 地图制图 | `app\api\routes\map.py` |
| GET | `/api/v1/export/diagnostics/{filename}` | 地图制图 | `app\api\routes\map.py` |
| POST | `/api/v1/export/vector-pdf` | 地图制图 | `app\api\routes\map.py` |
| POST | `/api/v1/export/pdf` | 地图制图 | `app\api\routes\map.py` |
| GET | `/api/v1/export/download/{filename}` | 地图制图 | `app\api\routes\map.py` |
| POST | `/api/v1/export/geojson` | 地图制图 | `app\api\routes\map.py` |
| GET | `/api/v1/tasks/status/{task_id}` | 任务管理 | `app\api\routes\task.py` |
| DELETE | `/api/v1/tasks/status/{task_id}` | 任务管理 | `app\api\routes\task.py` |
| GET | `/api/v1/uploads/{upload_id}/geojson` |  | `app\api\routes\upload.py` |
| DELETE | `/api/v1/uploads/{upload_id}` |  | `app\api\routes\upload.py` |
| GET | `/api/v1/config/llm` | 配置管理 | `app\api\routes\config.py` |
| POST | `/api/v1/config/llm` | 配置管理 | `app\api\routes\config.py` |
| POST | `/api/v1/config/llm/test` | 配置管理 | `app\api\routes\config.py` |
| POST | `/api/v1/config/rag/test` | 配置管理 | `app\api\routes\config.py` |
| GET | `/api/v1/config/skills` | 配置管理 | `app\api\routes\config.py` |
| POST | `/api/v1/config/skills/upload` | 配置管理 | `app\api\routes\config.py` |
| POST | `/api/v1/config/skills/refresh` | 配置管理 | `app\api\routes\config.py` |
| GET | `/api/v1/explorer/stream/{task_id}` | 探索引擎 | `app\api\routes\explorer.py` |
| GET | `/api/v1/templates` |  | `app\api\routes\templates.py` |
| GET | `/api/v1/templates/{template_id}` |  | `app\api\routes\templates.py` |
| POST | `/api/v1/templates` |  | `app\api\routes\templates.py` |
| DELETE | `/api/v1/templates/{template_id}` |  | `app\api\routes\templates.py` |
| GET | `/api/v1/sessions/{session_id}/raster/{raster_id}.png` | raster | `app\api\routes\raster.py` |
| DELETE | `/api/v1/projects/{project_id}/datasets/{dataset_id}` | Project Workspace | `app\api\routes\project.py` |
| GET | `/api/v1/projects/{project_id}/map-products/{from_version_no}/diff/{to_version_no}` | Project Workspace | `app\api\routes\project.py` |
| GET | `/api/v1/projects/{project_id}/map-products/{version_no}/open` | Project Workspace | `app\api\routes\project.py` |
| POST | `/api/v1/projects/{project_id}/map-products/{version_no}/restore` | Project Workspace | `app\api\routes\project.py` |
| POST | `/api/v1/projects/{project_id}/map-products/{version_no}/rerun` | Project Workspace | `app\api\routes\project.py` |
| POST | `/api/v1/projects/{project_id}/quality-audit` | Project Workspace | `app\api\routes\project.py` |
| POST | `/api/v1/projects/{project_id}/repair` | Project Workspace | `app\api\routes\project.py` |
| GET | `/api/v1/projects/artifacts/{artifact_id}/lineage` | Project Workspace | `app\api\routes\project.py` |
| GET | `/api/v1/projects/{project_id}/data-usage` | Project Workspace | `app\api\routes\project.py` |
| POST | `/api/v1/projects/{project_id}/data-gc/plan` | Project Workspace | `app\api\routes\project.py` |
| POST | `/api/v1/projects/{project_id}/data-gc/execute` | Project Workspace | `app\api\routes\project.py` |
| GET | `/api/v1/projects/{project_id}/carto-memory` | Project Workspace | `app\api\routes\project.py` |
| DELETE | `/api/v1/projects/{project_id}/carto-memory/{fact_id}` | Project Workspace | `app\api\routes\project.py` |
| POST | `/api/v1/projects/{project_id}/carto-memory/{fact_id}/activate` | Project Workspace | `app\api\routes\project.py` |
| GET | `/api/v1/projects/{project_id}/workspace/snapshots/{snapshot_id}` | Project Workspace | `app\api\routes\project.py` |
| POST | `/api/v1/projects/{project_id}/workspace/snapshots/{snapshot_id}/restore` | Project Workspace | `app\api\routes\project.py` |
| POST | `/api/v1/projects/{project_id}/workspace/snapshots/{snapshot_id}/clone` | Project Workspace | `app\api\routes\project.py` |
| GET | `/api/v1/projects/{project_id}/workspace` | Project Workspace | `app\api\routes\project.py` |
| POST | `/api/v1/data-fabric/sources` | Data Fabric / 数据织网 | `app\api\routes\data_fabric.py` |
| GET | `/api/v1/data-fabric/sources` | Data Fabric / 数据织网 | `app\api\routes\data_fabric.py` |
| GET | `/api/v1/data-fabric/sources/{source_id}` | Data Fabric / 数据织网 | `app\api\routes\data_fabric.py` |
| DELETE | `/api/v1/data-fabric/sources/{source_id}` | Data Fabric / 数据织网 | `app\api\routes\data_fabric.py` |
| POST | `/api/v1/data-fabric/sources/{source_id}/probe` | Data Fabric / 数据织网 | `app\api\routes\data_fabric.py` |
| POST | `/api/v1/data-fabric/sources/{source_id}/sync` | Data Fabric / 数据织网 | `app\api\routes\data_fabric.py` |
| GET | `/api/v1/data-fabric/catalog` | Data Fabric / 数据织网 | `app\api\routes\data_fabric.py` |
| GET | `/api/v1/data-fabric/catalog/{item_id}` | Data Fabric / 数据织网 | `app\api\routes\data_fabric.py` |
| GET | `/api/v1/data-fabric/catalog/{item_id}/descriptor` | Data Fabric / 数据织网 | `app\api\routes\data_fabric.py` |
| GET | `/api/v1/data-fabric/catalog/{item_id}/preview` | Data Fabric / 数据织网 | `app\api\routes\data_fabric.py` |
| POST | `/api/v1/data-fabric/catalog/{item_id}/explain` | Data Fabric / 数据织网 | `app\api\routes\data_fabric.py` |
| GET | `/api/v1/data-fabric/catalog/{item_id}/tiles/{z}/{x}/{y}.pbf` | Data Fabric / 数据织网 | `app\api\routes\data_fabric.py` |
| POST | `/api/v1/data-fabric/catalog/{item_id}/query` | Data Fabric / 数据织网 | `app\api\routes\data_fabric.py` |
| POST | `/api/v1/data-fabric/materialize` | Data Fabric / 数据织网 | `app\api\routes\data_fabric.py` |
| GET | `/api/v1/extensions/marketplace/packages/{package_id}/versions/{version}/download` |  | `app\api\routes\extensions_marketplace.py` |
| POST | `/api/v1/geocompute/plans/validate` | GeoCompute / 执行平面,GeoCompute / 执行平面 | `app\api\routes\geocompute.py` |
| POST | `/api/v1/geocompute/plans/execute` | GeoCompute / 执行平面,GeoCompute / 执行平面 | `app\api\routes\geocompute.py` |
| POST | `/api/v1/geocompute/plans/runs` | GeoCompute / 执行平面,GeoCompute / Cluster Runtime V6 | `app\api\routes\geocompute.py` |
| GET | `/api/v1/geocompute/runs` | GeoCompute / 执行平面,GeoCompute / Cluster Runtime V6 | `app\api\routes\geocompute.py` |
| GET | `/api/v1/geocompute/cluster/metrics` | GeoCompute / 执行平面,GeoCompute / Cluster Runtime V6 | `app\api\routes\geocompute.py` |
| GET | `/api/v1/geocompute/runs/{run_id}` | GeoCompute / 执行平面,GeoCompute / 执行平面 | `app\api\routes\geocompute.py` |
| POST | `/api/v1/geocompute/plans/runs/{run_id}/cancel` | GeoCompute / 执行平面,GeoCompute / 执行平面 | `app\api\routes\geocompute.py` |
| POST | `/api/v1/geocompute/runs/{run_id}/cancel` | GeoCompute / 执行平面,GeoCompute / 执行平面 | `app\api\routes\geocompute.py` |
| GET | `/api/v1/geocompute/runs/{run_id}/summary` | GeoCompute / 执行平面,GeoCompute / 执行平面 | `app\api\routes\geocompute.py` |
| GET | `/api/v1/geocompute/runs/{run_id}/events` | GeoCompute / 执行平面,GeoCompute / Cluster Runtime V7 | `app\api\routes\geocompute.py` |
| GET | `/api/v1/geocompute/cluster/workers` | GeoCompute / 执行平面,GeoCompute / Cluster Runtime V7 | `app\api\routes\geocompute.py` |
| GET | `/api/v1/geocompute/cluster/runs/stuck` | GeoCompute / 执行平面,GeoCompute / Cluster Runtime V7 | `app\api\routes\geocompute.py` |
| POST | `/api/v1/geocompute/cluster/runs/{run_id}/reset` | GeoCompute / 执行平面,GeoCompute / Cluster Runtime V7 | `app\api\routes\geocompute.py` |
| POST | `/api/v1/geocompute/cluster/ledger/limits` | GeoCompute / 执行平面,GeoCompute / Cluster Runtime V7 | `app\api\routes\geocompute.py` |
| POST | `/api/v1/geocompute/plans/drift-check` | GeoCompute / 执行平面,GeoCompute / 执行平面 | `app\api\routes\geocompute.py` |
| POST | `/api/v1/workflow-runtime/packages/register` | Workflow Runtime V5 | `app\api\routes\workflow_runtime.py` |
| POST | `/api/v1/workflow-runtime/packages/{package_id}/publish` | Workflow Runtime V5 | `app\api\routes\workflow_runtime.py` |
| GET | `/api/v1/workflow-runtime/packages` | Workflow Runtime V5 | `app\api\routes\workflow_runtime.py` |
| GET | `/api/v1/workflow-runtime/packages/{package_id}/versions` | Workflow Runtime V5 | `app\api\routes\workflow_runtime.py` |
| POST | `/api/v1/workflow-runtime/instances` | Workflow Runtime V5 | `app\api\routes\workflow_runtime.py` |
| POST | `/api/v1/workflow-runtime/instances/{instance_id}/run` | Workflow Runtime V5 | `app\api\routes\workflow_runtime.py` |
| POST | `/api/v1/workflow-runtime/instances/{instance_id}/cancel` | Workflow Runtime V5 | `app\api\routes\workflow_runtime.py` |
| POST | `/api/v1/workflow-runtime/instances/{instance_id}/changes` | Workflow Runtime V5 | `app\api\routes\workflow_runtime.py` |
| GET | `/api/v1/workflow-runtime/instances/{instance_id}` | Workflow Runtime V5 | `app\api\routes\workflow_runtime.py` |
| GET | `/api/v1/workflow-runtime/instances/{instance_id}/recompute-plan` | Workflow Runtime V5 | `app\api\routes\workflow_runtime.py` |
| GET | `/api/v1/workflow-runtime/instances` | Workflow Runtime V5 | `app\api\routes\workflow_runtime.py` |
| GET | `/api/v1/workflow-runtime/instances/{instance_id}/events` | Workflow Runtime V5 | `app\api\routes\workflow_runtime.py` |
| GET | `/api/v1/workflow-runtime/instances/{instance_id}/nodes/{node_id}` | Workflow Runtime V5 | `app\api\routes\workflow_runtime.py` |
| POST | `/api/v1/workflow-runtime/instances/{instance_id}/nodes/{node_id}/retry` | Workflow Runtime V5 | `app\api\routes\workflow_runtime.py` |
| POST | `/api/v1/workflow-runtime/instances/{instance_id}/nodes/cancel` | Workflow Runtime V5 | `app\api\routes\workflow_runtime.py` |
| POST | `/api/v1/workflow-runtime/instances/{instance_id}/clone` | Workflow Runtime V5 | `app\api\routes\workflow_runtime.py` |
| GET | `/api/v1/workflow-runtime/instances/{instance_id}/debug` | Workflow Runtime V5 | `app\api\routes\workflow_runtime.py` |
| GET | `/api/v1/local-data/admin/{level}/boundary` |  | `app\api\routes\local_data.py` |
| GET | `/api/v1/local-data/admin/children` |  | `app\api\routes\local_data.py` |
| GET | `/api/v1/local-data/osm/catalog` |  | `app\api\routes\local_data.py` |
| GET | `/api/v1/local-data/osm/features` |  | `app\api\routes\local_data.py` |
| GET | `/api/v1/static/{file_path:path}` | 静态文件 | `app\api\routes\static.py` |

---

以下 §7–§10 为手写叙事段（脚本重跑时 `--skip-md` 保护，不会覆盖本段）。

## 7. 文档漂移实测（P6 输入）

| 项 | docs/api-docs.md | 代码实际 | 判定 |
|---|---|---|---|
| 全局限流 | `每客户端 IP 60 次 / 60 秒`（§概述与通用约定，行 45） | `app/main.py:582` `max_requests=240, window_seconds=60` | **漂移确认**，生成器落地后随之消除 |
| lakehouse 章节 | 缺失 | 路由存在（V7/V8 特性） | 确认缺失 |
| geocompute 章节 | 缺失 | 路由存在 | 确认缺失 |
| workflow-runtime 章节 | 缺失 | 路由存在 | 确认缺失 |
| 错误信封 | 同时记载 `{"detail"}` 与 `ApiResponse{code,success,message,data}`（行 43-44） | 双轨并存 | 由 P3 统一 |

## 8. #1217 现状复核（字段级契约闸）

- 已落地（master 抽查）：`frontend/lib/api/upload.ts:29-39` TS 类型已镜像可空 `crs: string | null` 与 additive 可选字段。
- 未落地：字段级契约闸。现状仅有 path 级快照闸（`tests/quality/test_api_compatibility.py` + `tests/quality/snapshots/openapi.json`，`API_SNAPSHOT_UPDATE=1` 刷新），schema 字段级变化对 TS 侧无强制同步。
- 本线承接：P8 落地字段级闸（后端半边：response_model JSON schema 快照字段级 diff）；TS 类型再生成受 §8 前端边界约束，列为深化项不动手，接口不 breaking 保证 D/E/F/H 线前端安全。

## 9. 方法异常项语义核对（供 ADR-0138 讨论）

- 12 个 DELETE：见 §4 清单。DELETE 为主的状态变更端点，符合 REST 语义，无需补齐动作。
- 1 个 PUT：PUT 语义应为全量替换，需核对该端点是否幂等全量替换语义（在 ADR 中逐条裁定）。
- 0 个 PATCH：部分更新统一走 `/mapspec_mutations` 的 patch 动作族（POST 承载），已覆盖部分更新场景；不强行引入 PATCH 方法，理由与例外清单写入 ADR-0138。

## 10. 与任务书数字的差异说明

任务书估算基于较早 master：221 端点 / 163 无 response_model / 69 内联模型。实测（本分支基线）：**220 端点 / 123 无 response_model / 67 内联模型 / 16 文件**。差异源于 master 演进（PR #123 已为 chat 会话分页 + 部分端点挂模等）。P1/P2 销项以本报告与 `api-contract-matrix.csv` 为准。
