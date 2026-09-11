# ADR-0137: Harness V8 — Unified GIS Capability / Tool / Model Runtime

- 状态：Accepted（本地验证；随 full-master-audit-2026-09 / Harness V8 交付）
- 日期：2026-09-11
- 关联：ADR-0134（Harness V7 agentic runtime）、ADR-0136（ModelOps V3）、
  ADR-0133（GeoCompute V8）、ADR-0119（Harness V6 semantic autonomy）

## 背景

V7 交付了 runtime state machine、plan runtime、context layers、map critique
与 capability_descriptors（capability/algorithm/template/component 的统一
只读描述符投影 + 结构化检索）。但 Agent 规划仍缺一个真正统一的
「我现在有哪些能力（以及它们之间怎么连接）」视图：

- **Model 不是能力实体**（audit3 B-10 / #1212）：ModelOps 有完整
  descriptor/registry，但 capability→algorithm→tool 解析链到 tool 为止，
  模型只能靠工具关键词发现；`image_segmentation` capability 同名异义
  （k-means 统计法 vs 模型推理）。
- **资格判断分散**：V7 的 precondition 硬过滤在 capability 粒度；
  modelops 的 check_compatibility 在工具被点名后事后执行；两者口径
  已有一次漂移事故（B-6 度/米）。
- **资源/成本信息分散**：tool cost/latency_class、algorithm
  ResourceEnvelope、ModelOps estimate、GeoCompute capacity 四处声明，
  无统一投影，也无「测量 vs 声明 vs 估计」的诚实披露。
- **可靠性反馈只有工具维度**。

## 决策（全部 derived read-only projection，零新业务 registry）

### D1 Unified Capability Graph（`gis_harness/capability_graph.py`）

- 节点 kinds 封闭词表：capability / algorithm / tool / model / workflow /
  methodology / template / component / artifact_type / execution_backend /
  provider。关系词表封闭（13 种：implemented_by / exposed_by / accepts /
  produces / invokes / implements / requires / runs_on / contains /
  composed_of / binds_to / fallback_to / executed_by）。
- 节点只存 identity（id/kind/source_registry）+ 有界检索摘要；字段按需
  从 source registry 读 —— 图不复制 registry 内容（不是第 N+1 个事实源）。
- 来源：capability registry（153，含新 model_* 域包 8 个）、algorithm
  registry（222）、tool registry（327，manifest 同款惰性初始化）、
  ModelOps registry（model → capability implements 边 + 兼容性摘要）、
  artifact registry。
- 缓存纪律：source registry fingerprint → graph fingerprint → immutable
  projection；同指纹零重建（结构测试 `test_same_fingerprint_zero_rebuild`
  钉死）。构建有界（MAX_NODES=4096 / MAX_EDGES=20000，截断记 issue）。
- `validate_graph()` 机器闸：dangling endpoint / duplicate identity /
  词表外 kind/relation → 接入 `registry_validation.validate_gis_library()`
  （error 级 fatal）。

### D2 Model 一等能力实体（#1212 验收锚点）

- `capabilities/modelops.py` 域包注册 8 个 `model_*` capability
  （模型推理能力的稳定词汇锚点，拆分统计法与学习法语义）。
- modelops task_type → capability 词汇映射单表收敛
  （`_MODELOPS_TASK_TO_CAPABILITY`）。
- 核心查询：`models_for_capability(cap)`（implements 入边），
  `tools_for_capability(cap)`（algorithm 链 + 直接 implements）。
- 兼容性摘要随 model 节点投影（task_types / input_bands /
  resolution_range / provider_ref / owner_scope_key）。

### D3 Qualification Engine（`gis_harness/qualification_v8.py`）

- 六面上下文（Task/Data/Map/Runtime/Resource/UserConstraint）→ 结构化
  结论：eligible | ineligible(reason) | degraded(reason) | unknown(reason)。
  禁 bool；每个非 eligible 结论携带 {check, observed, expected, hint}。
- 检查维度：raster bands / resolution range（地理 CRS → degraded 的
  resolution_unknown，与 R1-C3/B-6 同口径）/ temporal inputs / GPU /
  owner scope / latency constraint / tier 确认披露 / min_features /
  data volume。
- 纯函数：resource safety / owner security 裁决在确定性代码（LLM 不参与）。

### D4 ExecutionEstimate（统一资源/成本投影）

- 字段：cpu/memory/gpu/vram/io/network/estimated_tiles/estimated_rows/
  latency_class/confidence + **basis**（measured | declared | estimated |
  unknown，逐维度披露）。
- 来源收敛：tool 声明档位（declared）、model provider 语义（declared）、
  algorithm complexity（estimated）；无据维度 = unknown（不把猜测伪装成
  测量）；confidence 按有据维度占比。

### D5 Reliability Feedback 扩展（`candidate_planner_v8.reliability_penalty_v8`）

- V7 的 tool 维度聚合扩展到任意 entity key（tool:x / model:x@v /
  provider:y）—— ledger 键前缀匹配（`{entity}||{failure_class}`）；
  bounded（≤32 entries）/ decayed / typed 语义承自 recovery ledger 既有
  实现（成功清零、TTL 过期重置）；罚分 = min(1, fails/4)，驱动候选排序。

### D6 候选规划（`candidate_planner_v8.plan_candidates_v8`）

- 链路：capability →（model 面 implements + tool 面 algorithm 链）→
  资格过滤（ineligible 剔除并披露原因）→ 成本排序（latency 档位 +
  degraded 罚 0.5 + 可靠性罚分）→ 确定性 tie-break（score, kind, id）。
- 产出是**计划证据**（可序列化），不是第二执行真相 —— 执行仍走
  ToolRegistry/ToolDispatchService 单一管线。确定性 fallback 保留
  （LLM 只参与语义解释/候选偏好）。

## 兼容性

- 零 migration、零新表。kill switch：`GIS_CAPABILITY_GRAPH_V8=0` 回退。
- capability registry +8 域包条目（additive，validate 干净）；
  registry_validation 增图级闸（error 级发现需为零）。
- 现有消费方（V7 select_capabilities / tool_surface_v3）不受影响 ——
  model_* capability 自然进入 V7 检索语料（同 registry 来源）。

## 测试（tests/unit/gis_harness/test_capability_graph_v8.py，19 用例）

- 结构：全 kind 投影 / 词表封闭 / validate 零 error / **同指纹零重建**。
- Model 实体：models_for_capability 查询链 / 兼容性摘要 / 统计-模型词汇拆分。
- 资格：bands 不兼容 ineligible + reason / 兼容 eligible / 地理 CRS
  degraded + resolution_unknown / latency 约束。
- Estimate：逐维度 basis 披露 / confidence 有界。
- 场景切片（§29）：Case 1（点分布多候选：kde_surface/kde_contours/
  heatmap_data 同列，不硬编码「=热力图」）/ Case 2（建筑提取模型链 +
  bands 不兼容排除）/ Case 4（失败反馈罚分改变排序）/ 确定性排序 /
  未知 capability 诚实排除。

## 已知限制 / 后续

- workflow / methodology / template / component 节点 kinds 已在词表，
  来源投影随各 registry 的图消费需求逐段接入（当前构建已含 capability/
  algorithm/tool/model/artifact 五段）。
- ExecutionEstimate 的 measured basis 待性能台账接入后回填；
  estimated_tiles/rows 需数据上下文（planner 调用侧提供）。
- LLM 侧接线（候选偏好解释）在 plan evidence 消费端，独立迭代。
