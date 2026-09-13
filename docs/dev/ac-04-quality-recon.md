# ac-04 数据自适应预处理与制图前置门禁 —— P0 勘察纪要（quality recon）

- 线：`adaptive-cartography/04-data-preprocess`（ADR-0153）
- 基线：origin/master `09d839d3`（Merge PR #1254）
- 勘察方式：S1（Explore subagent）只读产出四级映射矩阵与覆盖率统计 + 主 agent 亲读
  `spatial_quality_service.py` / `spatial_repair_pipeline.py` / `mapspec/lifecycle_engine.py` /
  `mapspec/pipeline.py` / `mapspec_source.py` / `data_ingest/repair_planning.py` / ADR-0140。
- 机器可读底稿：`docs/dev/ac-04-code-op-matrix.csv`（25 个可发码 × 全链路映射）。

## 1. 现状事实（全部有代码依据）

### 1.1 诊断侧
- `SpatialQualityEngine.audit_dataset`（5 维）实际可发 **25 个** issue.code；
  其中 **blocking 级 7 个**：`INVALID_FEATURE_FORMAT`、`EMPTY_GEOMETRY`（null 分支）、
  `INVALID_GEOMETRY_SYNTAX`、`SELF_INTERSECTION`（面自交/Nested shells）、
  `INVALID_GEOMETRY`（Nested shells）、`EXTREME_COORDINATES`、`IMPOSSIBLE_LAT_LON`。
- `overall_status`：blocking>0 或 error>0 即 "blocking"（spatial_quality_service.py:827-828）。

### 1.2 修复侧四级链（audit 码 → lib 码 → REMEDIATION_OPS 提案 → pipeline op）
- `_AUDIT_CODE_TO_QUALITY_CODE` 别名 **12/25**；`repair_linkage_for_code` 依
  `repair_planning._REPAIR_MAP` 产出提案 —— **提案是 plan-only，全仓不存在
  「提案 operation → pipeline op」的调度器**（本次 P2 `plan_repair_ops` 补上该缝）。
- `SpatialRepairPipeline` 7 op，默认只有 `make_valid + remove_empty`
  （spatial_repair_pipeline.py:83）；工具路径 `repair_spatial_dataset` 默认
  `["make_valid","remove_empty","deduplicate"]`（project_tools.py:399）。
- 执行层结论：**9/25 可执行**（3 个带条件/语义收窄），**13/25 完全无映射**，
  其余仅提案无执行。
- 任务书口径的 **14 个诊断码**：MISSING_CRS、SUSPICIOUS_CRS、IMPOSSIBLE_LAT_LON、
  NULL_ISLAND、EMPTY_GEOMETRY、INVALID_GEOMETRY、RING_CHECK_FAILED、
  SELF_INTERSECTION、DUPLICATE_GEOMETRY、DUPLICATE_FEATURE、DUPLICATE_PRIMARY_KEY、
  HIGH_NULL_RATIO、TOPOLOGY_OVERLAP、NUMERIC_OUTLIER；TOPOLOGY_GAP 作为 +1 超集
  一并覆盖（P6 fix_gaps）。**4 个缺口格（只报不修）**：TOPOLOGY_OVERLAP、
  TOPOLOGY_GAP、NUMERIC_OUTLIER、HIGH_NULL_RATIO（后者有提案但执行层无对应 op）。
- 「拦得住、修不了」最大缺口：`EXTREME_COORDINATES`（blocking，无任何映射）、
  `TOPOLOGY_OVERLAP`（error，无任何映射）。

### 1.3 防重复复核（《复核纪要》，进 PR 描述）
- PR 检索（quality/repair/CRS/outlier，state=all）：相关历史为
  #315（Spatial Data Quality & Lineage Platform V1）、#322（CRS quality/snap）、
  #358（runtime repair generation-safe）、#1139/1160（data fabric/quality V2+）、
  #1230（quality/oracle CI）——均未覆盖「制图前置门禁 + op 编排调度器 + CRS 自动
  推断 + 离群剖析」组合；本线无重叠交付。
- Issue 检索（脏数据/修复/CRS/离群/拓扑/投影）：#597/#682/#680/#1110/#866 等均为
  工具边界 CRS 语义 bug（已修）；无与门禁调度器重叠的开放项。
- 分支检索：`fix/lint-quality-gate`、`foundation/quality-e2e-v9`（已合并的 CI/质量
  工程线），无进行中的修复管线分支。
- `grep repair_linkage_for_code|SpatialRepairPipeline|MISSING_CRS|NULL_ISLAND app/`：
  命中集中在 spatial_quality_service / spatial_repair_pipeline /
  data_quality/repair_execution / data_ingest/repair_planning / gis_harness/
  data_qualification / tools/ingest_tools —— 与任务书 §0.2.6 现状一致。
- ADR 阅读：ADR-0140（数据生命周期 V9：质量规则引擎 + autofix 闭环，new-ref 语义、
  `REMEDIATION_OPS` 单一词表）；本线复用其词表与 new-ref 红线，不新建第二套修复词表。
  `data_quality/autofix.py` 是另一条（规则引擎键控）执行器，本线不合并它，
  但保持 op 词表同源（fail-fast 守卫已存在）。

### 1.4 编号与冲突契约
- ADR watermark = 0147，**ADR-0153 未占用**，按任务书使用 **ADR-0153**。
- Alembic：本线零迁移（无落库实体；门禁证据走内存/日志/profile 字段）。
- lifecycle：`apply_mutation` 已有 `pre_commit_check` seam（lifecycle_engine.py:1166）；
  本线在其后**加钩子不改突变逻辑**，与 09 线（评审段）无交叠；若冲突本线让位。

## 2. 14×7 矩阵要点（详表见 CSV）

| audit 码 | lib 码 | 提案 op | pipeline op | 判定 |
|---|---|---|---|---|
| MISSING_CRS | crs_missing | reproject(需声明) | crs_transform | 可执行*（人工声明，本线 P3 去人工化） |
| SUSPICIOUS_CRS | crs_suspicious | reproject(需确认) | crs_transform | 可执行* |
| IMPOSSIBLE_LAT_LON | impossible_coordinates | reproject(需声明) | crs_transform | 可执行* |
| NULL_ISLAND | zero_coordinates | filter_null/drop_zero_zero | — | **仅提案无执行**（缺口） |
| EMPTY_GEOMETRY | empty_geometry | filter_null/drop_empty | remove_empty | 可执行（默认内） |
| INVALID_GEOMETRY | invalid_geometry | repair_geometry | make_valid | 可执行（默认内） |
| RING_CHECK_FAILED | invalid_geometry | repair_geometry | make_valid | 可执行（默认内） |
| SELF_INTERSECTION | self_intersection | repair_geometry | make_valid | 可执行（默认内） |
| DUPLICATE_GEOMETRY | duplicate_geometries | repair_geometry/dedup | deduplicate | 可执行\*（属性不同删不掉；非默认） |
| DUPLICATE_FEATURE | duplicate_geometries | repair_geometry/dedup | deduplicate | 可执行（非默认） |
| DUPLICATE_PRIMARY_KEY | duplicate_rows | repair_geometry/dedup | — | **语义不对应**（缺口） |
| HIGH_NULL_RATIO | null_heavy_field | filter_null/target_field | — | **仅提案无执行**（缺口） |
| TOPOLOGY_OVERLAP | — | — | — | **完全无映射**（缺口） |
| TOPOLOGY_GAP | — | — | (snap 不可达) | **完全无映射**（缺口） |
| NUMERIC_OUTLIER | — | — | — | **完全无映射**（缺口） |

默认组合（make_valid+remove_empty）对 25 码覆盖率 **4/25**。

## 3. 现有测试覆盖（回归面）
- `tests/unit/test_spatial_quality_engine.py`（审计冒烟 + GIS-16/17 阈值 + #539 预算）
- `tests/unit/test_spatial_quality_issue539_cap.py`（拓扑截断契约）
- `tests/unit/test_audit_crs_passthrough.py`、`tests/unit/test_gis_crash_bugs.py`、
  `tests/unit/test_quality_dedup.py`
- `tests/data/test_repair_plan_v4.py`（W3 映射 + 证据有界 + new-ref 非破坏）
- `tests/data/test_ingest_seam_v4.py`（码→提案映射对账）
- `tests/unit/gis_harness/test_data_qualification.py`（词表/背书对账）
- 缺口：无任何测试覆盖「提案→pipeline 执行」调度、TOPOLOGY_*/EXTREME/OUTLIER 的
  修复语义 —— 本线补齐。

## 4. 「脏数据 → 错误出图」5 类可复现现象（P0 第 4 项，断言式复现）
落在 `tests/unit/test_dirty_data_cartographic_effects.py`（P8 阶段跑）：
1. **自交面 → 填充错/洞丢失**：bowtie 几何 area 计算虚高、make_valid 后面积修正
   （断言 area delta>0）。
2. **重复几何 → 压盖/双画**：同 WKB 双要素进渲染集合，symbol 计数 = 2（断言
   feature 计数），dedup 后 = 1。
3. **混合几何类型 → 单图层只画一类**：Polygon+Point 混合图层 type 混杂
   （geometry_mix>0），归一后单类型。
4. **缺/错 CRS → Null Island / 偏移**：投影坐标误标 4326 → IMPOSSIBLE_LAT_LON +
   suggestedView 拉爆；推断重投影后 bbox 回到量级范围。
5. **>3σ 离群 → 色带拉爆**：单极值字段 min/max 跨 4 个量级，等距分类断点被极值
   吞没（断言 p99 建议值把断点拉回主体区间）。
