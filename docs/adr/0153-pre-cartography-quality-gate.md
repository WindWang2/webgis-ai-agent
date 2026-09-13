# ADR-0153: 数据自适应预处理与制图前置门禁 —— 质量门禁、op 编排与 CRS 自动识别

- 状态：Accepted（adaptive-cartography/04-data-preprocess 线交付）
- 日期：2026-09-13
- 关联：ADR-0140（数据生命周期 V9：质量规则引擎、`REMEDIATION_OPS` 词表、
  new-ref 修复语义）；勘察依据 `docs/dev/ac-04-quality-recon.md` +
  `docs/dev/ac-04-code-op-matrix.csv`；决策细则 `docs/dev/ac-04-decisions.md`

## 背景

`SpatialQualityEngine.audit_dataset` 已能发 25 个诊断码（7 个 blocking 级），
但修复侧不闭环：`repair_linkage_for_code` 经 `repair_planning._REPAIR_MAP`
产出的提案是 **plan-only，全仓不存在「提案 operation → pipeline op」的执行
调度器**；`SpatialRepairPipeline` 7 个 op 默认只跑 `make_valid + remove_empty`
（对 25 码覆盖率 4/25）；CRS 缺失/错标仅 info/warning 不阻断，`crs_transform`
依赖人工传 `source_crs`（默认 4326 易整体偏移）；拓扑重叠、缝隙、离群值、
高空值率只报不修。脏数据可以一路走进 MapSpec 与导出，直接毁图。

## 决策

### D1 制图前置质量门禁（P1）

- **挂载点**：`MapSpecLifecycleEngine.apply_mutation` 的 UpsertLayer /
  UpsertSource 突变分支内、commit 之前（`_run_quality_gate_hook`）。
  **只加钩子，不改既有突变逻辑**；与 09 线的边界：09 改评审段
  （cartographic_review），本钩子只在 pre-commit 数据质量面，冲突时本线让位。
- **三态开关**（`settings.MAP_QUALITY_GATE_MODE`，回滚面）：
  - `enforce`（默认）：blocking 级问题拒绝上图，`error_code=
    "quality_gate_blocked"`，correction_hint 携带一键修复 op 序列；
  - `advisory`：只落 `quality_advisories[]`，不拒绝；
  - `off`：完全关闭（等价于合入前行为）。
- **逃生舱**：per-intent `quality_gate_bypass=true` → block 降级放行，但
  必须记录审计事件（结构化日志 + Prometheus 计数
  `mapspec_quality_gate_events_total{event,verdict,mode}`，双写 fail-open），
  且降级事实写进持久 layer metadata —— **禁止静默放行**。
- **verdict 语义**：block ⟺ audit 自身 blocking 级码（7 个）；error/warning
  → warn（放行 + advisory + 修复计划）。拦截面与 audit 分级严格一致。
- **有界**：门禁逐要素审计默认 ≤5000（对齐 inline 载体 #687 门；超帽走
  advisory 如实披露，大载荷本就该走 ref: 载体由 ingest/Celery 路径全量审计）。
  剖析在 `asyncio.to_thread` 中执行（重算离事件循环，仓库红线）。
- **warning 级放行**：layer/source metadata 落 `quality_advisories[]`
  （≤16 条），供 07（布局）/09（视觉评审）消费。

### D2 op 编排器（P2）

- `plan_repair_ops(audit_result, ...) -> RepairOpPlan`：诊断码 → **固定顺序**
  op 序列（`CANONICAL_OP_ORDER`：remove_empty → make_valid →
  normalize_geometry_type → deduplicate → crs_transform →
  snap_within_tolerance → attribute_type_normalization → fix_topology_overlap
  → fix_gaps → drop_outliers_or_flag → attribute_drop_or_flag），禁止自由编排。
- **破坏性裁决**（dedup / type normalize / attribute 归一 / 删行）：默认关闭，
  由比率阈值（重复率 ≥5%、几何混杂 ≥20%、属性类型混杂 ≥30%、重叠对 ≥1%）
  或显式 `allow_destructive` 开启，裁决结论入 `destructive_decisions`，
  阈值不足的剔除进 `skipped_ops`（诚实披露为什么没安排）。
- **证据上限**沿用 ≤16 条；非破坏 deepcopy 语义保持（输入永不被污染）。
- 与 `repair_planning.RepairProposal`（REMEDIATION_OPS 提案层）的关系：提案层
  回答「哪些确定性修复**存在**」，计划层回答「对这个数据集**应该跑什么、
  什么参数、为什么**」——两层词表不同源，映射由 `_CODE_TO_OP` 单点维护。

### D3 CRS 自动识别（P3）

- `infer_crs`：按 bbox 量级 + 坐标范围 + 常见投影特征推断，覆盖
  4326 / 3857 / CGCS2000（4490 及 3° 带 4513..4533、CM 法 4534..4554）/
  UTM（326xx/327xx）。判定顺序即歧义消解顺序：度域 → GK 带号前推
  （>16×10⁶，中国独有惯例）→ UTM/GK easting 窗 → Web Mercator 包络。
- **人工声明最优先**；声明为地理 CRS 而坐标为米制量级 = 正矛盾 → 推断真实
  投影源（SUSPICIOUS_CRS / IMPOSSIBLE_LAT_LON 的根因修复路径）。
- 低置信（无锚点的 GK/UTM、退化 bbox、极端坐标）→ `low_confidence` 证据 +
  advisory，**不阻断、不猜带号**。
- `crs_transform` 不再依赖人工 `source_crs`：推断兜底由编排器把推断结果写进
  计划参数；人工值仍优先。
- 诚实现（勘察发现）：`4490` 与 `4326` 坐标量级完全同域，纯数值推断只给
  `cgcs2000_candidate` 注记，元数据级判定需名称提示（hint）。

### D4 离群值与值域剖析（P4）

- `profile_outlier_policy` → `outlier_policy ∈ {none, clip_p99, head_tail,
  log}` + `outlier_ratio` / `skew` / `zero_ratio` / `p99` / `suggested_clip`。
- **本线只剖析不裁剪**；裁剪由 03 线按契约执行（字段级断言测试钉死契约形状，
  契约里没有"裁剪执行"字段）。>3σ 拉爆色带场景的建议值 = **inlier 质量
  （剔 >3σ 点）的 p99** —— 原始 p99 被极值本身吞掉时给出的是可用建议。

### D5 修复血缘（P5）

- `repair_dataset_with_lineage` 额外返回 per-op
  `{op, before_count, after_count, area_delta, evidence[], ts}`；
  `evidence[]` 复用 Wave-4 op 级证据键（op/features_affected/failed_count，
  对齐 `data_quality.repair_execution.build_repair_evidence`）——
  **不新建第二套 provenance 结构**。「为何修」= plan.reasons（码→op）×
  lineage（op→事实）的解释链。

### D6 新 op（P6）

- `fix_topology_overlap`：mode=difference 时后入要素对先入要素让位
  （先到先得的确定性规则；difference 清空时不删除，如实计 failed），
  mode=flag 只标记；pair 预算 ≤200（对齐 audit #539 有界纪律）。
- `fix_gaps`：mode=snap 时 pairwise `shapely.snap`（容差可配，目标 CRS
  单位），mode=flag（默认）只标记。
- `drop_outliers_or_flag`：默认 flag（`ac04_quality_flags` 顶层标记键，可
  整体剥离）；drop 需 allow_destructive 且 outlier_ratio ≤2%（安全阀）。
- `attribute_drop_or_flag`（HIGH_NULL_RATIO 策略）：默认 flag；drop_column
  需 allow_destructive。
- `remove_empty` 扩展 `drop_zero_coordinates` 参数（默认 False）：Null
  Island 剔除仅在裁决显式开启时执行。
- 边界用例（空集/单要素/全重叠/预算耗尽）各有单测。

### D7 profile 契约（P7）

Spatial Profile 扩展（默认值兜底 `default_quality_profile()`，02/03 线在
本线合入前即可按此消费）：

```json
{
  "geometry_mix": {"types": {}, "dominant": null, "mix_ratio": 0.0},
  "n_valid": null,
  "extent": null,
  "crs_confidence": {"crs": null, "confidence": "unknown",
                     "low_confidence": false, "method": "not_evaluated"},
  "outlier_policy": {},
  "quality_advisories": []
}
```

`outlier_policy` / `crs_confidence` / `geometry_mix` 字段由本线定义，03 线
消费；若 03 先行以其 schema 为准并在 PR 注明。

## 后果与风险

- **行为变化**：enforce 模式下 blocking 脏数据不再能进入 MapSpec —— 这是
  本线的目的；回滚面 = `MAP_QUALITY_GATE_MODE=off`（单环境变量，零代码回滚）。
- **性能**：门禁审计是 inline 载体上的 O(F) 有界扫描（≤5000），运行于
  to_thread；ref: 载体不进门禁（由 ingest 路径负责）。
- **诚实缺口**（详见矩阵）：`DUPLICATE_PRIMARY_KEY`（pipeline 键 = geom+
  attrs，不发明主键语义）、`INVALID_GEOMETRY_SYNTAX`（不可解析几何不做
  修复）——拦截 + 披露，不硬凑。`RING_CHECK_FAILED` 经标准 GeoJSON 解析
  不可达（shapely 自动闭合 ring）——勘察发现，测试钉死该事实。
- 迁移：零（本线无落库实体，禁止新建 Alembic 迁移契约遵守）。
