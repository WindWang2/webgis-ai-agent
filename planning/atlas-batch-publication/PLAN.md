# PLAN — 批量图册发布流水线（冻结设计 v1）

## 定位

既有生产链之上的**聚合编排层**：单张权威 MapSpec（会话 committed spec）+ AtlasPolicy → durable 批量图册 job → 逐页真实数据绑定渲染（复用 publication_export 编译面）→ 图册级 QA → PDF + 聚合 provenance manifest → exports 属主落盘 + lineage/artifact → 任务中心与 Agent 工具双入口。

**不重写**：atlas 页面规划（PublicationIR）、SVG/PDF 编译（mapspec_to_svg + WeasyPrint 链）、单页 product_manifest v1、jobs 框架、artifact registry、前端栅格导出链。

## 分层与新增文件

```
app/lib/cartography/atlas_job_spec.py        # AtlasJobSpecV1（versioned 契约）+ 页指纹
app/services/atlas_batch/__init__.py
app/services/atlas_batch/planner.py          # spec→(PublicationIR, 页绑定/指纹/统计)
app/services/atlas_batch/page_compiler.py    # 单页编译接缝调用 + 页头/页码 SVG 注入（batch 专属）
app/services/atlas_batch/qa.py               # 图册级 QA 门（证据化）
app/services/atlas_batch/manifest.py         # 图册聚合 manifest（含 per-page product_manifest 摘要）
app/services/atlas_batch/executor.py         # job 执行体：工作目录/页缓存/checkpoint/取消/重试/落盘
app/services/atlas_batch/tasks.py            # @celery_app.task run_atlas_export（durable_job 模板）
app/api/routes/atlas_jobs.py                 # POST /atlas/jobs、GET /atlas/jobs/{id}（owner-scoped）
app/tools/atlas_export_tools.py              # agent 工具 export_atlas（submit_durable_job）
frontend/lib/api/atlas-jobs.ts               # 提交/详情客户端
frontend/components/... tasks-tab/map-studio 最小 patch
tests/cartography/atlas_batch/…              # 单测/API/executor e2e/旗舰 12 区验收
```

## 关键决策（含理由）

1. **权威 spec 来源 = 会话 committed spec**（`mapspec_store.get_mapspec(session_id)`），worker 内 `hydrate_ref_sources_sync`（既有函数）内联 ref 载体。理由：任务定义"已存在的单张权威 MapSpec"；同步路由的前端内联路径保持不动。显式 inline spec 提交 → typed 400 指向同步路由（诚实边界，不做 blob 转存 V1）。
2. **JobKind additive**：`atlas_export`。task_type=`atlas_export`。幂等：params（policy+classification_scope+dpi+session）→ idempotency_key；重复提交复用同一 job 行。
3. **页级生命周期**：每页 = `plan → compile(SVG) → 验证`；状态 `{page_id, fingerprint, status: pending|rendered|failed, error?, feature_count, bounds}` 持久化于 job 工作目录 `status.json` + 进度镜像到 `progress_message`；终态汇总进 `result_summary`（JSON，JobView additive 投影 `page_status`）。
4. **页指纹** = sha256(canonical{page IR 身份字段, 页过滤要素计数, 页文档结构指纹, target_dpi, PUBLICATION_IR_VERSION, 编译器版本键})。用途：(a) manifest 数据身份；(b) 重试/恢复时跳过未变页；(c) 跨 job 内容寻址页缓存 `exports/atlas_jobs/cache/{fp}.svg`（容量+条目双上限，mtime 逐出）——"fingerprint 变更只刷新该页"。
5. **失败语义（V1 严格模式）**：任一内容页编译失败 → job failed，逐页证据（哪页/哪类错）落 result_summary + manifest 草稿；重试（job retry 新 attempt）只重渲染 failed/指纹失效页，rendered 页直接复用缓存 SVG。**绝不**交付缺页 PDF 冒充成功；封面失败同理。
6. **分级语义显式化**：`classification_scope: "shared" | "local"`。shared = 所有页继承同一 spec legend/classification（QA 门逐页校验 legend_spec/palette/breaks 摘要一致，不一致 → fail 证据）；local = 跨页不可比，manifest 每页记录本页分类摘要 + 图册级 `cross_page_comparability: false`（V1 由 frames 驱动 + 用户显式 per-frame layerOverrides 达成；category/feature 驱动 + local → typed 400 指引，不代算分级——G2 边界）。
7. **页头/页码/页题注入仅 batch 路径**：executor 在页 SVG 根内追加确定性 `<text>`（右上"第 n/N 页"、左下页题、左上图册题小字），不动同步路由输出（goldens 字节不变）。坐标用 `paper.width_px/height_px`（编译面 4px/mm 同源）。
8. **PDF 组装复用**：提取 `publication_export` 的"命名 @page HTML + WeasyPrint + pypdf 页数"为 `_assemble_pages_pdf`（模块内函数，同步路径改为调用它——输出字节不变，由既有 golden/atlas 测试锁）；executor 直接调用。
9. **产物出库**：`exports/atlas_{job_id}_{ts}.pdf` + `atlas_{job_id}_{ts}.manifest.json` + sidecar diagnostics；`_set_export_owner`（属主）；`record_export_lineage`（worker 内 run_sync + 异步 db，best-effort 失败不阻断交付但如实记录 result_summary.lineage_recorded=false）；下载走既有 `/export/download/{filename}`。manifest 同时给 GET /atlas/jobs/{id} 详情引用。
10. **取消/清理**：`job.checkpoint()` 每页边界；cancel → 工作目录删除（临时 SVG/status 不泄漏）；缓存目录是内容寻址产物，不属于该 job 的临时文件，保留。完成 → PDF/manifest 移入 exports，工作目录删除。
11. **WeasyPrint 互斥**：组装阶段单次 `render_pdf_exclusive`；忙 → 页缓存已保住进度，attempt 内短退避重试 N 次（有界），仍忙 → job failed(typed atlas_pdf_busy) 可重试（页不重渲染）。
12. **12 区旗舰验收**：合成 12 行政区（成都式命名）+ 小学统计字段 → category 驱动 + 封面 → 13 页 PDF；pypdf 机器解析：页数==13、每页文本含区名（页题注入）与 TOC 一致、manifest 页指纹/过滤身份/要素计数与 planner 对账。

## 里程碑（对齐任务 M1-M9）

- **M1** 环境 + 基线：venv/pnpm 就绪；`tests/cartography/test_publication_atlas_pipeline.py` 等 atlas 族 + jobs 族测试全绿（记录命令与输出）。
- **M2** `atlas_job_spec.py` + JobKind/视图 additive + publication_export 接缝提取（同步测试全绿不变）。
- **M3** planner：页绑定（要素计数/过滤身份/bounds）+ 指纹 + classification_scope 契约与 QA 输入。
- **M4** page_compiler：接缝调用 + 页头注入 + 组装；`_assemble_pages_pdf` 共用。
- **M5** qa：完整性/一致性/页数相等/证据化失败。
- **M6** executor + tasks：工作目录/缓存/checkpoint/取消清理/幂等/定向重试/忙碌退避。
- **M7** 落盘 + 属主 + lineage + manifest 出库 + typed unavailable（文件缺失→404 typed）。
- **M8** 工具（registry 接线 + dispatch 真链测试）+ REST + 前端（api client/TasksTab 页状态/提交 UI/i18n）。
- **M9** 旗舰验收 + 回归（ruff/mypy/pytest 面上全绿；前端 tsc/eslint/vitest）+ 独立 review ×2 + 清偿 + PR。

## 明确不做（边界）

- 不修 #1800/#1808/#1810 等既有导出缺陷（避免与在修 agent 抢文件）；executor 对含 heat_matrix 页按失败页证据化。
- 不做 local 分级的自动计算（G2 领域）；frames+layerOverrides 的用户显式路径受支持并如实记录。
- 不改客户端栅格导出链/exporter.ts 主流程；不做 3D/StoryMap；不做模板决策 G4。
- 不新建模板存储/权限系统（复用 exports 属主 + jobs owner 三元组 + verify_session_owner）。
