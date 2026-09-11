# 11 — Development Direction：Harness V8 设计输入（Phase D）

## 现有基础（V7 合并后可用，不重写）

- `capability_descriptors.py`（V7 D4）：capability/algorithm/template/component
  统一只读描述符投影 + preconditions/postconditions/cost profile/fallback 链
  + select_capabilities（硬过滤+词法种子+可靠性罚分）。
- `runtime_state_machine.py`（D1）：12 态认知闭环状态机（阶段=章节事实派生）。
- `plan_runtime.py`（D2）：指纹版本化 plan + replan 预算 + 最小重算闭包。
- `context_layers.py`（D3）：九域上下文投影 + durable 预算。
- `map_critique.py` / `intent_acceptance.py` / `display_confirmation.py` /
  `delegation.py`（D5-D7）。
- 既有：data_qualification、fallback_v3、recovery_ledger（reliability 反馈）、
  runtime_manifest（三注册表指纹投影）、registry_validation。
- ModelOps（v3 合并后）：descriptor（task_types/bands/resolution/compatibility/
  resource）、registry（owner_scope 隔离）、engine（GPU/VRAM/warm pool）、
  planning.py、reuse.py、evaluation_service.py。
- GeoCompute v8：worker capabilities、placement、resource envelope。
- Workflow v6：durable cluster runtime、capability requirements。

## V8 增量（全部 derived read-only projection，零新业务 registry）

### V8.1 Unified Capability Graph（扩展 capability_descriptors，非平行实现）

- 节点 kinds：Capability / Algorithm / Tool / Model / Workflow / Methodology /
  Template / Component / ArtifactType / ExecutionBackend / Provider。
- 边（§20 全集）：implemented_by / exposed_by / accepts / produces / invokes /
  implements / supports / requires / runs_on / contains / composed_of / binds_to /
  fallback_to。
- 存储：entity id + kind + source registry + source fingerprint + relationships；
  字段按需投影（不复制 registry 内容）。
- 缓存：source registry fingerprint → graph fingerprint → immutable projection；
  同指纹零重建（结构测试钉死）。
- 校验并入 registry_validation（dangling entity/fallback/artifact type、
  duplicate identity、descriptor mismatch、resource contradiction、
  invalid provider/model compatibility、template requirement unsatisfied、
  workflow required capability missing）→ machine-readable report。

### V8.2 Model 一等能力实体

- Graph 中 Model 节点（来自 ModelOps registry，含 owner scope 维度）+
  ModelCandidate 选择链：task taxonomy → capability → algorithms → models →
  input compatibility（sensor/bands/resolution/CRS/temporal/prompt/ROI）→
  resource requirements（VRAM/GPU/latency）→ available backend → 候选计划 →
  tool invocation（modelops_run_inference 或 workflow 包裹）。

### V8.3 Qualification Engine（统一资格判断）

- 输入：TaskContext/DataContext/MapContext/RuntimeContext/ResourceContext/
  UserConstraint；输出：eligible | ineligible(reason) | degraded(reason) |
  unknown(reason)（禁 bool）。
- 检查维度：geometry/CRS/fields/raster bands/resolution/sensor/time/data
  volume/dependency/credentials/GPU/memory/model compatibility/scientific
  assumptions/artifact compatibility。
- 落位：扩展 data_qualification.py（V7 已有 capability 粒度）到 graph 实体
  粒度；评估结论可解释（reason 结构化）。

### V8.4 ExecutionEstimate（统一资源/成本投影）

- 字段：cpu/memory/gpu/vram/io/network/estimated_tiles/estimated_rows/
  latency_class/confidence/basis（measured|declared|estimated|unknown）。
- 来源收敛：tool cost/latency_class/memory_class + algorithm ResourceEnvelope
  + ModelOps estimate + GeoCompute capacity → 单一 estimate 投影 API。
- 不复制：按需从 source registry 读取，estimate 只存派生值+基础指纹。

### V8.5 Reliability Feedback 扩展

- recovery_ledger 维度扩展：tool/model/provider/workflow/backend 五类
  bounded/decayed/scoped/typed 反馈（recent_success_rate、failure_class
  histogram、last_failure_class、oom_rate、timeout_rate、compatibility_
  failure_rate）→ candidate ranking 罚分（select_capabilities 已有钩子）。

### V8.6 规划器接线（确定性 fallback 保留）

- Intent → Context → Graph Retrieval → Qualification → Candidate Plan →
  Cost Ranking → Execution → Observation → Reliability Feedback → Repair/Replan。
- LLM 仅参与语义解释/候选偏好/解释；resource safety/owner security/
  schema validation/artifact validity/completion correctness 的裁决在
  确定性代码。

### V8 End-to-End 场景（§29 五案例）

1. 成都学校分布（point distribution，多可视化候选按上下文选择）
2. 遥感建筑提取（segmentation→model candidates→GPU plan→inference→
   vectorization→map layer→legend）
3. 大规模空间分析（resource envelope→GeoCompute placement→distributed）
4. 失败恢复（preferred model/provider fail→typed failure→reliability→
   fallback→partial recompute→complete）
5. 用户继续交互（user 手动隐藏/编辑保留，second prompt 仅重算必需部分）

## 提交策略（§31）

feat(harness): unified capability graph projection → model/workflow entities →
qualification engine → resource-aware planning → reliability feedback →
tool/model execution planning → registry parity validation →
test(harness): e2e proofs → docs(harness): architecture。
