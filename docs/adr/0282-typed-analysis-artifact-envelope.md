# ADR-0282：类型化分析产物封套 v2 与混合方法链（Typed Analysis Artifact Envelope）

**Date:** 2026-11-06
**Status:** Proposed
**Branch:** `zcode/goal-gis-analytical-foundation-vnext-20261105`
**Baselines:** ADR-0099（显式分母/科学契约）· ADR-0279（SpatialSemanticContract v1）· ADR-0281（RecoveryPolicy）· goal-02 #1869（AnalysisWorkflowContract v1，设计文档 planning/analytical-workflow-composition/）

## Context

#1869 交付的 AnalysisWorkflowContract v1 把多步分析带进了「步骤×角色×单位合同」
的薄编排层，但四个事实让跨工具混合分析仍不可科学审计：

1. **阶段产物是类型盲的**：`AnalysisArtifactEnvelope.crs` 恒为空串（工具统一返回
   `GeoAnalysisResult.to_llm_response()`，无 crs 键）；`units` 是**声明的参数单位**
   而非实测产物口径；geometry/extent/时间/nodata/coverage/ownership/provenance 全缺。
   下游步骤与 receipt 无法对账「上一步输出的 CRS/几何/口径」，换 CRS 的静默错位
   只有带 preflight 的单工具能拦，工作流缝零防线。
2. **栅格链无法进入编排**：`zonal_stats(raster_path: str)` 接受 ref，但 executor
   把所有 role 输入水合为 dict 载荷——类型不通，「重采样→掩膜→zonal→矢量→图」
   只能靠 Pi 逐工具裸调，无契约/重算/receipt。
3. **语义断链**：per-capita 分母字段硬编码 "population"（缺列=运行期 failed 而非
   plan 期 typed skipped）；角色 geometry_kinds 声明从不校验（h3 对面静默质心）；
   断网设施 5m 假面进覆盖统计且 summary 披露在 envelope 外丢失。
4. **诚实性缺口**：stale 状态词表在但无人赋值；科学终局门只是返回体字段；
   产品层 `ProductCompileResult(ok=True)` 无条件——science_blocked 仍交付成品。

## Decisions

### D1 Envelope v2：实测类型化产物元数据（additive 字段，全有界）

`AnalysisArtifactEnvelope` 新增（默认空=未知，绝不虚填）：
`schema_version=2`；`geometry_types`（≤4，FC 有界扫描/artifact descriptor）；
`extent`（bbox 4 元组，descriptor 或有界扫描）；`result_semantics`
（≤8 键，实测口径：working_crs/density_unit/rate_unit/stat_denominator——取自
结果 spatial_semantics 证据块）；`temporal`（时间标识，temporal 族对账）；
`raster`（≤8 键：nodata/resolution/band_count/dtype/valid_pixel_coverage——仅栅格
步骤）；`aggregation_basis`（封闭词表："" / one_to_one / many_to_one_sum /
m_n_join / zonal_mean / zonal_sum——声明聚合基数语义）；`provenance`
（≤6 键：tool_descriptor_fingerprint/method_version 摘要引用）；`owner_session_id`。

既有字段语义不变（units 保持=声明参数单位；实测口径在 result_semantics）。
反序列化容忍 v1 载荷（新键缺省=未知——gates 按「事实缺失=披露不阻断」处理，
权威守门仍在工具级 SpatialSemanticContract preflight，本层是纵深防御）。

### D2 Typed 衔接门（声明式 semantic_contract + 执行期核验）

`AnalysisStepSpec` 新增可选 `semantic_contract: Dict[str, str]`（键词表封闭：
`crs_class`（crs_safety 词表）/`geometry_kind`），族模板逐步声明。executor 在
水合后核验：FC 几何类型集合 vs 声明；`step:` 上游时对照上游 envelope 的
geometry_types/result_semantics。违例 → typed blocked
`AWF_INPUT_SEMANTIC_MISMATCH:<param>`（新码）。plan 期同时用
`binding.qualifier`（geometry_types/feature_count 画像事实）做前置披露：
事实在且冲突 → typed blocked `AWF_ROLE_GEOMETRY_MISMATCH:<ROLE>`（新码）；
事实缺 → 警告披露（不阻断——不虚构事实也不假装校验过）。

### D3 栅格角色类型（role_kind 直通）

`DataRoleBinding` 新增 `role_kind: str = "vector"` ∈ {vector, raster, table}。
raster 绑定在 executor **不水合为 dict**：ref/路径字符串直通参数
（zonal_stats/raster_resample 的 raster_path 原生接受 ref，registry
`_resolve_references` 既有语义）；身份=`ref@content_revision`。上游 `step:`
产物无 FC（如 resample 输出 ref）时同样直通 ref。行数预算门不适用于 raster
（栅格工具有自己的窗口读纪律）；envelope 记 raster 事实。

### D4 新族 `raster_zonal_summary`（链②）与 `network_proximity`（链③）

- **raster_zonal_summary**：`resample`（raster_resample，optional）→
  `zonal_stats`（zones=role:boundary，raster=role:subject[raster kind]——
  subject 词表注释本就含「栅格主题」）→ 矢量结果 ref（MapSpec-ready，
  hints: administrative_choropleth）。optional resample 剔除时 zonal 确定性
  回接 role:subject。角色：subject(required, raster)/boundary(required,
  polygon)。
- **network_proximity**（链③ 独立族——service_accessibility 的 subject 语义
  是设施点，与最近设施的「需求点」语义冲突，并入会造成角色二义）：
  `nearest_facility`（network_closest_facility：incidents=role:subject、
  facilities=role:facility、network=role:network）→ `accessibility_summary`
  （spatial_aggregate：numerator=Σtotal_cost / count 分母=区均网络成本——
  均值语义，库层 rate_unit=count_ratio_not_rate 反伪装率标签钉住）。
  角色新增 `facility`（DATA_ROLES additive）。
- **service_accessibility 强化**：isochrone 步族默认
  `exclude_unreachable=True`——脱离路网设施的 5m 占位面不进覆盖统计
  （计数进 stats.excluded_unreachable；工具默认 False 保持 master 行为，
  调用方可参数覆盖）。**R1 评审后注**：early 草案的 require_reachable
  参数名被此实现取代；经 `layer_ids` 引入的已挂载图层不在产品门管辖内
  （门覆盖显式 ref 参数面——见 D6 已知边界）。
- **admin_facility_distribution**：per_capita_rate 分母字段名**只**从
  role:denominator 的 qualifier.field 解析（默认 "population" 兼容旧行为；
  R1 评审 F-2：其它角色的 qualifier 绝不可劫持分母列）；qualifier.fields
  声明且目标列不在其中 → plan 期 typed skipped `AWF_DENOMINATOR_MISSING`
  （修复断点 6）。

### D5 幂等指纹升版 + stale 赋值 + 科学终审态

- `FINGERPRINT_SCHEMA 1→2`（语义相关实测字段进指纹——旧持久态自然失效重算，
  诚实代价，一次性）。
- recompute 工具：显式 role/param 变更后把漂移步骤在持久 state 中标
  `state="stale"`（不再是只有返回清单；verdict_of 已把 stale 归入非 satisfied）。
- run 结束时全步骤终态 → 写 `state.science_validation =
  {final_gate: pass|blocked|not_evaluated, checked_at_turn}`（G02 run_science_final_gate
  投影进状态机，status 工具透出）。

### D6 产品最终门（partial ≠ final，kill switch）

新模块 `analysis_workflows/product_gate.py` 纯函数
`assess_ref_eligibility(states, ref)`：ref 源自某 workflow envelope 且该 run
非 satisfied（或 science_validation=blocked）→ `{eligible: false, run_status,
reason_code}`。`webgis_map_product` 挂载图层前调用（经注入 helper，默认启用；
`GIS_AWF_PRODUCT_GATE=0` bit 级回退 master 行为）：不合格 ref → 该图层
降级披露（不挂载为成品层 + guidance），全部图层不合格才 success=False（既有
语义保持）。改动是 gis_harness/tools.py 的最小 additive 钩子，单独 commit，
契约测试锁定旧行为在开关关闭时逐字节不变。

### D7 dispatch repeated 复用（断点 7 修复）

executor 对 `status="repeated"`：若同 run 内存在同 step_fingerprint 的已
satisfied 步骤 → 复用其 envelope（步骤级幂等，跨工具调用语义正确）；否则按
可重试失败披露（reason 保留），绝不把空载荷当成功。

### D8 不做

不改 ToolDispatchService 核心/`_resolve_references` 语义；不建第二执行器/
第二语义契约（复用 SpatialSemanticContract 证据块与 crs_safety 词表）；不动
Pi bridge；不把 10 万预算门上限调高（预算估计只做 plan 期披露与
typed blocked 码，真正的 spill/chunk 属工具层既有纪律）；不修 S96/S103/S105
issues 指认的同文件行为；不改 MapSpec schema。

## Consequences

- 兼容：全部 additive（envelope/step/binding 新字段默认值、新族、新步骤
  optional、新 typed 码仅在新路径发射）；`FINGERPRINT_SCHEMA=2` 是唯一的
  既有行为变化（旧态重算一次）；产品门默认开但有 bit 级回退。
- 科学性：链②栅格统计进入「契约+指纹+receipt」治理；跨步骤 CRS/几何/口径
  可审计；断网假面、分母缺列、几何错配在 plan/run 期 typed 阻断。
- 性能：envelope 有界扫描（≤2000 特征采样，与 preflight 同界）；栅格零
  水合；perf lane 锁新开销（baseline 策略 fail-closed）。
- 测试：契约单测 + 三链 E2E（fixtures_data 合成数据）+ science_oracles 新域
  （断网已知答案/跨180聚合/无效多边形/zonal 覆盖率）+ G05 三新场景
  （高风险设施统计/栅格行政区汇总/最近医院网络距离）。
