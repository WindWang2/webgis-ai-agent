# BASELINE — Spatial Data Quality & Semantic Harmonization Autopilot v1

- 日期：2026-09-16
- base SHA：`faa453a8935101378c23eb6694a42c3616d9c670`（origin/master，`git fetch --all --prune` 后核对）
- worktree：`../webgis-wt-data-quality-v1`（分支 `data/spatial-quality-harmonization-v1`）
- Open PRs（执行时）：#1335 `fix/harness-claim-mission-failclosed`（占用 issue #1330–#1334）；#1336 `zcode/geoai-promptable-foundation-platform-11`（GeoPrompt/embedding-cache/frontend 热区，本方向不动）
- 远程分支仅：`master`、`fix/harness-claim-mission-failclosed`、`zcode/geoai-promptable-foundation-platform-11`
- Open issues：#1330–#1334（fail-closed，#1335 占用）；#1337–#1350（audit 跟踪 issue，61 条，非本方向 ownership）
- 勘察方式：主 agent 深读核心契约文件 + Subagent A（Explore，very thorough）全仓映射

## 与任务书词表的现状对照（关键结论）

代码库已有 ~80% 目标管道，分布于五个并行质量子系统。任务书要求的 9 类 issue 词表对照：

| 任务书码 | 现状 | 现有载体 |
|---|---|---|
| CRS_UNKNOWN | 已有 | `QualityIssueCode.CRS_MISSING`（lib/data/quality.py:38）+ audit `MISSING_CRS` |
| UNIT_AMBIGUOUS | 缺码缺检测 | `INCONSISTENT_UNIT` 码存在但**无生产者**（仅 repair 映射） |
| TIMEZONE_MISSING | **完全缺失** | 全仓无 timezone 证据检查（time_parser 静默默认 UTC） |
| GEOMETRY_INVALID | 已有 | `INVALID_GEOMETRY`/`SELF_INTERSECTION` + audit 引擎 5 维 |
| FIELD_ROLE_AMBIGUOUS | **缺码缺发射** | `semantic_profile.py` 有证据分级推理但无 issue 发射、无 planner 消费 |
| DUPLICATE | 已有 | `DUPLICATE_ROWS`/`DUPLICATE_GEOMETRIES` + rule `duplicate_features` |
| OUTLIER | 部分 | rule `raster_stats_outlier`（栅格）+ audit `NUMERIC_OUTLIER`（矢量，spatial 引擎） |
| ADMIN_MISMATCH | 缺数据质量码 | 仅 `spatial_guardrails/admin_division_verifier.py` 独立命名空间（层级校验） |
| SCHEMA/CATEGORY 域映射 | **完全缺失** | 无跨 schema 映射、无类别域映射契约 |

## Subagent A 摘要（详见 GAP_ANALYSIS.md）

五个既有质量子系统：(1) `app/lib/data/quality.py` 受控词表 + 剖析证据检查；(2) `app/services/data_quality/` 16 规则引擎 + RepairPlan/execute/autofix；(3) `spatial_quality_service.py` 5 维空间审计；(4) `spatial_quality_gate.py` pre-cartography 门；(5) `gis_harness/data_qualification.py` 五态数据资格（workflow compiler 已消费）。

核心缺口（Subagent A + 主 agent 双重确认）：
(a) 无统一前置分析 DataQualityProfile 聚合产物；(b) timezone/unit-ambiguous/role-ambiguous/admin-mismatch 无码无检测；(c) 修复无事务状态机（dry-run→apply→verify→rollback）；(d) 无 schema/类别域和谐化映射；(e) V8 capability 资格路径（qualification_v8/capability_resolution）对质量盲视。
