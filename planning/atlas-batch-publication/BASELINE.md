# BASELINE — 批量专题图册/系列制图/可复现发布流水线

- 审计基线 HEAD：`4b3629c90ee977e287f7b1fb4fd827b0fad7428c`（origin/master，PR #1867 合并后，2026-10-29）
- worktree：`../webgis-worktrees/gis-atlas-publish`，分支 `zcode/cartography-atlas-publishing-vnext`
- 审计方式：三路并行 subagent（后端链路 / 前端链路 / PR-issue 去重）+ 主 Agent 对关键契约文件逐行核实

## master 现状分级（production / partial / scaffold-only / absent）

| 能力 | 级别 | 证据（file:line @ 4b3629c90） |
|---|---|---|
| 服务端同步矢量图册 PDF（frames/category/feature 驱动 ≤20 页 + 封面/目录 + 截断披露 + 逐页过滤） | **production** | `app/services/publication_export.py:528-782`；规划 `app/lib/cartography/publication_ir.py:371-511`；`app/lib/cartography/atlas_layout.py:34-84`；路由 `app/api/routes/map.py:496-668`；测试 `tests/cartography/test_publication_atlas_pipeline.py`（15 用例）、`test_vector_pdf_route_atlas.py`、`test_publication_ir_golden.py` |
| category 驱动 = 每行政区一页（属性去重值→页，bbox 拟合纸张，值过滤页文档） | **production** | `publication_ir.py:514-577`（分组/排序/截断）+ `publication_export.py:317-376`（`_page_filtered_doc` 值过滤，文档级预过滤，**不依赖 layer.filter 求值**——#1810 不影响本链） |
| durable job 框架（状态机/取消看门狗/进度节流/幂等键/attempt retry/产物原子写） | **production（通用）** | `app/services/jobs/lifecycle.py:33-119`（JobKind 词表 agent/analysis/workflow/explorer）；`jobs/submit.py:43-`（`submit_durable_job`）；`jobs/worker.py`（`durable_job` 上下文：`checkpoint/progress/artifact/ensure_not_cancelled`）；`jobs/store.py`（1123 行，`DurableJobStore`）；API `app/api/routes/jobs.py:86-`（list/get/cancel/retry，owner-scoped） |
| artifact 登记与版本（幂等 upsert / supersede / 检索） | **production（通用）** | `app/services/artifact_registry.py:502-`（`register_artifact`）、`:635-`（`register_tool_artifact`）；`export_lineage.py:97-`（`record_export_lineage`：属主校验→register_artifact→回执） |
| 单页 Cartographic Product Manifest v1（六维语义证据） | **production** | `app/lib/cartography/product_manifest.py`（`POST /api/v1/export` session 在场时落 `{filename}.manifest.json` sidecar）；33 场景 golden `tests/cartography/test_h02_product_manifest.py` |
| 前端任务中心（3s 轮询/取消/重试/进度条/result_ref 联动） | **production** | `frontend/components/sidebar/tasks-tab.tsx`；`frontend/lib/hooks/use-job-center.ts`；`frontend/lib/api/jobs.ts:97`（JobView.kind 词表 agent\|analysis\|workflow\|explorer） |
| 前端矢量 PDF 跑腿（committed spec→ref 内联→POST vector-pdf→503 回退栅格） | **production** | `frontend/lib/map-kit/publication-export.ts:101-`、`frontend/lib/api/publication.ts:52` |
| WeasyPrint 串行互斥（饱和即 429 不排队） | production（约束） | `publication_export.py:85,439-451` |
| `ExportBatchQueue`（内存串行/重试/断点） | **scaffold-only** | `app/services/export_batch_queue.py:86`；app/ 零调用方，仅 `tests/cartography/test_publishing.py:41-93` |
| **异步/批量图册导出 job（atlas JobKind、页级 checkpoint、失败页定向重试）** | **absent** | JobKind 无 export/atlas（`lifecycle.py:46-52`）；无 handler |
| **agent 工具→服务端矢量图册链** | **absent** | `app/tools/cartography.py:1028-1119` `export_batch_maps` 只产前端命令；vector-pdf 自述 backend-only（`map.py:518-521`，audit3 C-4/#1213 登记的遗留边界） |
| **图册级 provenance manifest（页序/页指纹/跨页一致性/可复现清单）+ 产物入 artifact registry** | **absent** | 导出只落 FS+sidecar+lineage；无图册聚合 manifest |

## 关键契约快照（实现直接消费）

- `AtlasPolicy`（`publication_ir.py:74-93`）：driver∈{frames,category,feature}、layer_id、category_property、features_per_page(1-500)、page_budget(1-20)、include_cover、atlas_title(≤160)。
- `PublicationIR`（`:144-159`）：pages[PageLayoutIR{page_id,title,page_number,page_count,paper,bounds,frame_index,filter_layer_id/property/value,feature_offset/limit,cover,component_ir}]、degradations、`ir_fingerprint()`。
- `render_publication_pdf(payload, title, max_frames, max_labels, target_dpi, atlas)`（`publication_export.py:528`）：unhydrated 拒绝→plan→逐页（封面 `_render_toc_svg:263` / 帧覆盖 `_apply_frame_overrides:235` / 过滤 `_page_filtered_doc:317`）→`compile_mapspec_to_svg_detailed`（96MB 累积预算 `:77`）→命名 @page HTML→`render_pdf_exclusive:439`→pypdf 页数。返回 `PublicationPdfResult`（page_count/diagnostics/component_coverage/spec_fingerprint/atlas_pages）。
- `submit_durable_job`（`jobs/submit.py:43`）：幂等键 `build_execution_key(task_type, session_id, params)`；返回 `{status,job_id,task_id,message,idempotent_reuse}`；origin contextvar 携带 owner/org/project/agent 链。
- worker 模板（NDVI）：`@celery_app.task(bind=True)` + `with durable_job(self, job_id) as job:` 内 `job.progress()/checkpoint()/ensure_not_cancelled()` + `finish_job(job_id, result, result_ref=)`（`app/services/spatial_tasks.py:77-166`）。
- `AnalysisTask` 表（`db_model.py:96-`）：`parameters`(JSON)/`progress`/`progress_message`(255)/`result_summary`(JSON)/`idempotency_key`(unique)/`job_kind`(20)。
- `JobView`（`jobs/views.py:31-59`）：kind/name/status/progress/message/cancellable/retryable/result_ref/error…（安全摘要面）。
- 导出落盘模式（`map.py:612-668`）：`_exports_dir_write()` + `_set_export_owner(filename, user_id)` + sidecar `*.diagnostics.json` + `_record_lineage`→`record_export_lineage`（session/owner 校验→artifact_registry）。
- 下载：`GET /api/v1/export/download/{filename}`（`map.py:743`，属主 fail-closed）。

## 既有缺陷与本方向的关系（不代修，防引入新风险）

- **#1800**（`svg_charts.py:436` heat_matrix `fill-opacity` 双重格式化 ValueError）：已复核当前 master 仍在。图册页含 heat_matrix chart 组件会崩——executor 页级失败会按"失败页"处理并证据化（不静默空白），测试避开该 kind；不在本 PR 修（归 #1800）。
- **#1810**（mapspec-to-svg 忽略 layer.filter）：服务端 atlas 链用**文档级预过滤**（`_page_filtered_doc`），分页语义不依赖 filter 求值；但 spec 若在过滤图层上另声明 layer.filter，页内会画出本应被过滤的要素——现有行为，不因本方向改变；披露于 RISKS。
- **#1808**（经纬网 cosφ 轴错位）：graticule 绘制既有缺陷，本方向不改绘制数学；含 graticule 组件的图册页继承既有行为（已登记 issue，不在本 PR 范围）。

## 运行环境核实

- Python：仓库根 `pyproject.toml` + uv/venv（实现时核实）；前端 `frontend/` pnpm + Turbopack；测试 pytest + vitest。
- WeasyPrint 在本机可用性待 M1 实测（`python -c "import weasyprint"`）；缺席则矢量链测试走 `PublicationUnavailableError` 既有降级路径并如实披露。
