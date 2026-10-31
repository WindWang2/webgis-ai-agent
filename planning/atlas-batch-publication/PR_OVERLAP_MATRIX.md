# PR_OVERLAP_MATRIX — 去重矩阵（基线 4b3629c90，2026-10-30 复核）

## 已合并 PR（关键项）

| PR | 重叠度 | 重叠点 | 处置 |
|---|---|---|---|
| #1755 product conformance & export parity（ADR-0228） | **高** | 单页 Cartographic Product Manifest v1 已生产接线（导出 sidecar）；33 场景 golden | 图册 manifest **聚合引用** per-page product_manifest 摘要/指纹，不重造单页语义清单；`product_manifest.py` 只读消费，不改其结构 |
| #1621 Durable Cartographic Runtime（ADR-0220） | 中 | durable 恢复/checkpoint 底座 | 页级 durable job **建在** jobs 框架（lifecycle/store/worker）之上，不另起恢复机制 |
| #1763 组合模板资产生命期 V1 | 中 | 版面模板 DB 存储/版本 | 本方向不新建模板存储；共享版式 = 既有 MapSpec layout.components + PublicationIR 共享 chrome |
| #1830 g3d05 3D 产品（StoryMap v1.1） | 低 | exporter.ts/export-chrome.ts 有机械 diff 面；story_map_assets 持久化模式 | 前端改动避开 exporter.ts 主链（新 API client + TasksTab/map-studio 最小 patch）；章节化资产模式仅参考 |
| #1762/#1759/#1756 G05/G01/H01 | 低 | 验收 DSL/数据资格/判据 | 验收走 pytest 直证；descriptor 指纹复用既有字段（content_revision/data_fingerprint） |
| #1867/#1836/#1835/#1834/#1829/#1773/#1771 G3D 系 | 无 | 三维方向 | 无冲突 |
| #1761/#1760/#1758/#1622/#1620/#1618/#1617 等 | 无 | 工具对账/科学守门/issue 清偿/交互 IR 等 | 无冲突 |

## 开放 PR

| PR | 判定 |
|---|---|
| #1868 sdo 数据准备工作台 | 无文件重叠（data_preparation/**）；可并行 |
| #1869 AnalysisWorkflowContract | 无重叠（gis_harness/analysis_workflows/**）；"步骤级 receipt"模式仅方法论相近；可并行 |

## 开放 issues（86 个全扫）

- 标题含 atlas/图册/批量/系列/publication/provenance 的：**0 个**——无能力占用。
- #1800/#1808/#1809/#1810/#1822/#1826 等导出缺陷：既有 bug 修复单，非本方向能力；本方向触碰同路径时不扩大其风险（见 BASELINE 缺陷节 + RISKS）。

## 远端分支与 planning 命名空间

- `zcode/*` 远端分支 11 个：无 cartography-atlas* 相近命名；g3dv2-*/g3d0* 均为已合并残留。
- `planning/` 现有 17 子目录 + 开放 PR 分支 2 个（analytical-workflow-composition / semantic-data-onboarding）；**atlas-batch-publication 不存在**。

## 结论

方向核心增量（异步图册 job/页级生命周期/页指纹/图册 manifest/工具与 UI 入口）**未被占用**；定位为既有生产链的**聚合编排层**：单页语义证据引 #1755、durable 底座引 jobs 框架、页面规划引 PublicationIR、渲染引 publication_export 接缝。

## 冲突敏感共享文件清单（最小 patch 纪律）

- `app/services/publication_export.py`：仅提取接缝函数（同步路径字节不变），新增代码放新模块。
- `app/services/jobs/lifecycle.py`：仅 JobKind 枚举 +1 行。
- `app/services/jobs/views.py`：JobView additive 可选字段。
- `app/tools/__init__.py` / `app/tools/registry.py`（若在）：工具注册 +1 条。
- `app/api/routes/map.py` 或 main.py：路由 include +1 条（优先新独立路由模块）。
- `frontend/lib/api/jobs.ts`：kind 词表 +1。
- `frontend/components/sidebar/tasks-tab.tsx` / `map-studio-tab.tsx`：最小 UI patch。
- `frontend/lib/i18n/messages.ts` + `frontend/messages/{en-US,zh-CN}/*.json`：新增消息域文件。
- `tests/quality/snapshots/*`：如需快照一律用项目生成器再生。
