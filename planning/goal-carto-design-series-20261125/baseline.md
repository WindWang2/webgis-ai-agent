# Baseline — goal-carto-design-series-20261125

- 基线：origin/master `8fe3d76634bfb7052f438325c7db3e0ef3dad040`（2026-11-20 #2266 合并；
  2026-11-26 fetch 核查仍为 head）。分支 `zcode/goal-carto-design-series-20261125`，
  worktree `/home/kevin/projects/webgis/webgis-wt-carto-design-series-20261125`。
- 开放 PR（无正式 review 线程，reviewDecision 空）：#2272 method-experiment（39 文件）、
  #2273 data-fusion（46，含 migration 0110 + models/__init__）、#2274 multiscale（40，
  触 semantic_checks.py / mapspec_mutations.py / hud/layer-style-panel / map.json）、
  #2275 scenario-harness（40，触 mapspec/mutation_primitives.py / map-studio-tab）、
  #2276 pi-native-host（21，vendor/pi + pi_compat）。#2276 与本任务文件面零重叠。
- 本任务与 #2274 同属方向 04 的互补子目标：#2274=多尺度综合算法；本任务=设计规范+
  系列。不触其文件（除 `app/tools/__init__.py` / `app/main.py` 公共接线 additive 行）。

## 真实能力矩阵（file:line 证据）

| 能力 | 状态 | 证据 |
|---|---|---|
| Design System V4 只读投影（manifest/requirement） | production，**零生产消费方** | `app/lib/cartography/design_system.py:49` build_design_system_manifest；仅 tests/cartography/test_design_system_v4.py 消费 |
| 模板版本化（不可变快照+继承+软废弃） | production | `app/services/templates/versioning.py:135` create_version（savepoint 重试）；0048/0098 两条同构先例 |
| G40 符号资产（版本/发布/退役/digest 复算/引用保护） | production | `app/services/cartographic_assets/catalog.py`（resolve_version_document:404；collect_symbol_asset_documents:689） |
| MapSpec 权威+CAS | production（会话级） | `app/services/mapspec/store.py:334`；revision=session_data `_cartographic_mutation_revision`；checkpoint `app/services/mapspec/checkpoint.py` |
| print_layout.v1 纸面作者（propose/apply/preview/export） | production | `app/services/print_layout_authoring/service.py`（propose:276 dry-run、apply:292 CAS、preview:364 真实编译、submit_export:454） |
| 真实渲染 SVG/PDF/PNG | production | `mapspec_to_svg.py`；PDF=WeasyPrint（render_pdf_exclusive:1253 进程互斥）；PNG=rsvg-convert（缺席 typed 503） |
| 批量图册（单会话多页） | production | `app/services/atlas_batch/`（指纹页缓存 256 条/512MB；QA 四门；manifest v1；pypdf 复核）；AtlasJobSpec `app/lib/cartography/atlas_job_spec.py`（classification shared/local） |
| 帧产品（单 MapSpec 多画幅+共享分级） | production | `app/services/gis_harness/frame_products/`（shared_expression.compute：跨帧并集一次分级+测量语义资格） |
| 数据集语义/角色绑定/可比性预检 | production | `app/services/dataset_semantics/`（preflight_multi_dataset:224；role_binding 六态） |
| 分类器 | production | `app/lib/cartography/classify.py:151`（5 法；>1000 子采样披露） |
| 标准层（ADR-0200 规则/包/图） | production | `app/lib/cartography/standards/`（CartographicRule 12 kinds；StandardsPack 内容寻址） |
| 语义检查 | production | `app/lib/cartography/semantic_checks.py`（#2274 在改，只读消费） |
| 早期 /goal 04 表达候选 | production | `app/lib/cartography/design_candidates.py`、`design_decision_trace.py` |
| 跨会话持久「系列」实体 | **缺失（本任务核心增量）** | 全仓无 map series 实体；AtlasJobSpec 单会话；frame_products 单文档 |
| 设计规范实体 | **缺失（本任务核心增量）** | 无 design specification 权威；只有 themes/palettes/model/component 各自 registry |

## 系列成员身份决策（WP3 设计基础）

MapSpec 权威是会话级磁盘文档（.webgis-agent/<sid>/mapspec.json + .fp/.rev sidecar）。
系列成员解析：作者期引用活会话（传播经 CAS）；freeze 期把每成员 MapSpec 文档+指纹
物化进系列冻结计划（ADR-0226 Freeze 纪律、checkpoint 同款内容寻址），生产渲染读冻结
文档过既有 compile 接缝（`publication_export.compile_publication_page`），不再依赖活
会话；会话消失的成员如实 blocked（restore 路径存在但不自动）。

## 只读依赖 / 可写路径

可写：`app/services/design_specs/`（新）、`app/services/map_series/`（新）、
`app/models/design_spec.py`+`map_series.py`（新）、`migrations/versions/g50_*.py`（新）、
`app/tools/design_spec_tools.py`+`map_series_tools.py`（新）、`app/api/routes/design_specs.py`+
`map_series.py`（新）、`frontend/components/workbench/design-series-panel.tsx`（新）+
`frontend/lib/{api,design-series}/`（新）、i18n 新 namespace、测试新目录。
公共接线（最小 additive）：`app/tools/__init__.py`、`app/main.py`、
`frontend/lib/i18n/messages.ts`、`frontend/components/sidebar/map-studio-tab.tsx`（#2275
在改——接线时如冲突以最小 diff 合并）。
只读消费：mapspec lifecycle engine、print_layout_authoring、atlas_batch（提交/缓存模式）、
cartographic_assets catalog、dataset_semantics、classify、standards、design_system manifest。

## 环境事实

- 主 checkout `.venv`（python 3.12 系）可直接驱动 worktree（pytest pythonpath=.）；
  frontend node_modules 已 symlink。cartography 基线 85 passed 3 skipped。
- 快跑命令：`.venv/bin/python -m pytest tests/unit -m cartography --no-cov -q`；
  frontend：`pnpm lint / typecheck / test`。
- 构建/测试约束：-j2、串行重型构建（同机 5 worktree）、无线上 CI 门禁。
