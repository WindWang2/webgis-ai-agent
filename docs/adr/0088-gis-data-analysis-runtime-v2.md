# ADR-0088: GIS Data Fabric & Analysis Runtime V2

日期: 2026-08-30
状态: Accepted（本分支落地）
关联: ADR-0082（Artifact Runtime）、ADR-0083（Cost-aware Resolution）、ADR-0080（Unified GIS Runtime V3）

## Context

P0–P8 之后的 GIS Harness 已具备 Capability → Algorithm → Tool 的确定性解析与
artifact 事实记录，但数据与算法执行底座存在四类缺口：

1. **契约失验**：工具返回 `success=true` 即视为产物正确，无人比对「声明的
   output_artifact_type」与「实况产物画像」（点/面/栅格族错配静默通过）；
   AlgorithmDescriptor 声明了 `input_artifact_types` 但 resolver 从不消费。
2. **画像形状分裂**：RefDescriptor（snake_case）、Spatial Meta Profile
   （camelCase）、ArtifactRecord 三套重叠 metadata 各自演化，无统一契约。
3. **复用断层**：`cached_tool`（Redis）遇 `ref:` 参数必须拒绝缓存（ref 是
   会话内可变指针）——一切「分析 ref 数据」的工具调用跨轮次 100% 全量重算。
4. **类型词表漂移**：service 层 `infer_artifact_type` 可产出
   `feature_collection`/`chart_spec`，但二者未注册于 ArtifactTypeRegistry。

同时明确本轮**不做**的事（防第二真相）：Algorithm Registry 不变成执行引擎；
ToolRegistry 仍是唯一执行入口；ArtifactRegistry 不变 payload store；不新建
缓存框架；不大规模重写 raster 算法族。

## Decision

### 1. DatasetProfile —— 统一有界画像契约（`app/lib/gis/dataset_profile.py`）

派生 metadata，不是第二数据真相。三源 O(1) 构造器（ref_descriptor /
spatial_profile / artifact_record），字段有界（fields ≤64、geometry_types ≤8、
bbox 4 元组），未知如实缺省（`fields_status="unknown"`、`crs=""`、不虚构
EPSG:4326）。`to_resolver_profile()` 是 resolver 既有 camelCase 入参的唯一
适配出口（additive `artifactType` 键）。

### 2. 输出契约验证（`app/lib/gis/contract_validation.py`）

纯函数 `validate_output_contract(declared_types, profile)` → 有界 findings：
几何族错配（error）、未注册声明（error）、空产物（warning）、CRS 缺失
（warning）。**未知不判死**：descriptor-only 画像无几何信息时跳过几何比对。

接线点：session_plan 的 plan-apply seam（capability 声明唯一在场点）——
findings 只进 `metadata["contract_check"]` 与结构化日志，**绝不阻断**计划
路径（与 ADR-0082「注册是增值记录」同一哲学）。`artifact_dependency_report`
投影回 findings。dispatch seam 无 capability 上下文，不做声明比对。

### 3. Resolver 消费 input_artifact_types

`_check_candidate` 新增拒绝码 `input_type_mismatch:{algo}:have={t}`——仅当
画像携带**已注册**的 `artifactType` 且不在算法声明词表内时触发；未知类型
不判死。旧调用方不带该键 → 行为逐位不变。

### 4. Analysis Reuse —— artifact 层确定性复用（`app/lib/gis/analysis_reuse.py`）

- 键：`sha256(tool + canonical(args))`，canonical = 严格 JSON（无
  `default=str`——不可序列化对象宁可不可键控，杜绝内存地址进键）、
  sort_keys、256KB 尺寸闸（复用 `json_size`）。ref id 是键的组成部分。
- 作用域：仅 `AlgorithmRegistry.tool_to_capability()` 认定的分析工具；
  非 ref / inline 小参数仍由既有 `cached_tool` 承载，二者正交。
- 命中条件（§29 逐条）：ArtifactRecord `status=valid` ∧ `analysis_key` 相等
  ∧ recency ≤ 24h ∧ 产物 descriptor 存活 ∧ **输入 ref 形状指纹一致**
  （生产时记录 {ref: {feature_count, geometry_types}}，复用时复核——
  in-place overwrite 的启发式守卫；content_hash 被既有决策保留为 None，
  形状指纹是记录在案的 best-effort，不是内容寻址）。
- 失败语义：失败调用不注册 → 天然不可复用；复用查找任何异常 → miss 照常
  执行（复用是纯加速，绝不改变失败语义）。`GIS_ANALYSIS_REUSE=0` 整体关闭。
- 结果形态：复用命中返回与首次相同的产物 ref，摘要如实披露「未重复计算」；
  不重放 MapSpec 显示授权（首次已授权，重放会重复挂层）。

### 5. 质量证据（`app/lib/geo_analysis/evidence.py`）

`build_quality_evidence()` 有界 dict（≤16 键、值截断）：input/output/dropped
计数、working_crs、approximate 声明。`GeoAnalysisResult` 新增 additive
`evidence` 字段（缺省 None，旧站点行为不变），经 `to_llm_response` 与
slim summary 分支（`_PRESERVED_META_KEYS` 增 `quality_evidence`）有界透传。
首批评点站点：buffer_smart / spatial_aggregate / h3_binning / calculate_nearest。

### 6. 类型词表收编 + 单位契约硬化

`feature_collection`/`chart_spec` 注册为正式 artifact 类型（service 层可产出
的每个 artifact_type 必须是注册词——守卫测试锁定）。`unit_requirements`
限定封闭词表（meters/kilometers/degrees/pixels/seconds），validate() 对词表
外声明报 issue；`approximate` 与 `deterministic` 判定为正交（精度折衷 vs
可复现性），不做静态矛盾判定，随机性披露由 descriptor 声明者负责（§27）。

## Alternatives Considered

- **内容寻址复用**（payload sha256 进键）：store 热路径成本被 #666 明确
  拒绝；形状指纹 + descriptor 探测是零热路径成本的次优解。
- **新 AlgorithmExecutor / GISExecutor 统一执行层**：与 ToolRegistry 竞争
  第二 runtime，违反 invariant，否决。
- **SpatialExecutionContext 新对象**：`to_utm_gdf` 家族已是事实权威
  （声明 CRS、UTM 选带、跨 antimeridian、极地、make_valid、memoization），
  包装即第二真相；本轮以 ADR 记录契约 + golden 测试锚定。
- **raster 窗口化重写**：zonal（rasterstats）已窗口化；raster_calculator
  全量读是真实缺口但重写超出本轮，记 Deferred。

## Trade-offs

- 形状指纹守卫对「同形状不同内容」的 overwrite 不可见（受限于无
  content_hash）；记录在案，分析 ref 当前无 overwrite 生产方。
- 复用命中不重放显示授权：需要新图层时应显式调用图层/模板工具——诚实
  且避免重复挂层，代价是「重跑同一分析并期待自动重挂」不再隐式发生。
- 契约验证只在 plan-apply seam：dispatch 直连（无计划上下文）的工具产物
  无 declared-type 比对，仅前缀推断。

## Compatibility

- 全部 additive：`DatasetProfile`/`contract_validation`/`analysis_reuse`/
  `evidence` 均为新模块；resolver 新拒绝码仅在画像携带已知 artifactType 时
  触发；`GeoAnalysisResult.evidence` 缺省 None；artifact metadata 新键对
  旧读者不可见。旧客户端/旧调用方行为逐位不变。

## Performance

- 复用命中：O(128) 账本扫描 + ≤8 次 O(1) descriptor 探测，无 payload 读取；
  benchmark（tests/benchmarks/test_data_runtime_v2_perf.py）以调用计数锚定
  「命中 → registry.dispatch 0 次」与「探测次数 ≤8」。
- 契约验证/画像构建：O(1) 于 feature_count（bench 断言输出结构不变性）。
- 150k 回归：slim 后载荷 <64KB、无坐标数组（§18 锚定）。

## Failure Semantics

- 复用查询异常 → miss 照常执行（绝不阻塞/绝不谎报成功）；
- 契约验证失败 → findings 缺席（下次注册重验）；
- 空结果（feature_count=0）是合法产物：契约层 warning 披露，可复用。

## Migration

零迁移：无 schema 变更、无新 endpoint、无新 store；ArtifactRegistry metadata
新键（analysis_key/input_shapes/contract_check）对旧读者透明。

## Future Work

- content_hash 离线计算落地后，形状指纹升级为内容寻址；
- raster_calculator 窗口化；raster 双时相 change detection 的对齐校验；
- 契约验证覆盖 dispatch seam（需 dispatch 携带 capability 上下文）；
- 质量证据接入 ArtifactRegistry metadata（当前仅在响应载荷）。
