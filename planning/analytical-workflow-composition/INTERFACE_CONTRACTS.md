# INTERFACE_CONTRACTS — 接口冻结（M2 契约，实现前定稿）

> 红线继承：不造第二 PlanGraph / 第二 Recipe 真相源 / 第二执行真相。编排层是**薄包装**：
> 步骤执行一律 `registry.dispatch`（ToolDispatchService 同一缝，ref 铸造/复用/guardrail 全继承，
> 先例 #1867 run_3d_analysis scene_orchestration_tools.py:739）。

## 1. 模块布局（全部新增文件，零既有文件语义改动）

```
app/services/gis_harness/analysis_workflows/     # 新子包（全仓 0 命中，非影子模块）
├── __init__.py
├── contracts.py      # AnalysisWorkflowContract v1 / AnalysisStepSpec / AnalysisArtifactEnvelope / typed 码
├── families.py       # 5 族步骤模板（只引用既有 recipe/capability/工具名，编译期校验悬空引用 fatal）
├── orchestrator.py   # build_workflow_plan（纯函数）+ 数据角色绑定 + 科学前置裁决（typed blocked）
├── executor.py       # execute_workflow（registry.dispatch 真实分发、有界、可取消、幂等）
├── recompute.py      # step_fingerprint / invalidate / plan_recompute（选择性局部重算）
├── evidence.py       # StepReceipt（MethodUseContract × 参数 × 输入指纹 × 警告）+ G02 三段对账投影
└── state.py          # 会话内持久（session store 单键 analysis_workflow.v1，恢复/去重写）
app/tools/analysis_workflow_tools.py             # 4 个 Pi-native 工具（注册尾部接线）
app/services/gis_harness/skills/library/core/analysis_workflows.yaml   # 5 族 procedure/义务声明包
```

## 2. 核心契约（pydantic，schema_version 进指纹）

### AnalysisWorkflowContract（薄；不是 PlanGraph——无节点调度语义，只有「族×步骤×数据合同」）
```python
schema_version: int = 1                     # analysis_workflow.v1
workflow_id: str                            # "awf.<family>.<intent_hash12>"
family: str                                 # admin_facility_distribution | service_accessibility | hazard_exposure | temporal_change | statistical_zones
label_zh: str
recipe_refs: tuple[str, ...]                # 引用既有 recipe id（不复制）
steps: tuple[AnalysisStepSpec, ...]         # 有序；显式 depends_on
data_contracts: dict[str, DataRoleBinding]  # role → {ref|missing, dataset_hint, qualifier}
science_context: ScienceContext             # metric_crs_policy/units/timezone/denominator_policy
recompute_dimensions: tuple[str, ...]       # 引用 workflow_schema.RECOMPUTE_DIMENSIONS 词表
risk_notes: tuple[str, ...]                 # 成本与风险披露（元数据，不阻断）
visualization_hints: tuple[str, ...]        # 仅结果元数据（制图裁决仍在 converter/planner）
```

### AnalysisStepSpec
```python
step_id: str                                # "<family>.<n>.<slug>"
tool_name: str                              # 既有工具名（注册表校验，不新造算子）
capability: str                             # 既有 capability id
role_inputs: dict[str, str]                 # 形参名 → data role（"pois"→subject）
step_params: dict[str, Any]                 # 静态参数（travel_time_minutes=15 等）
unit_contract: dict[str, str]               # 参数单位声明（km/m/min），执行前校验
depends_on: tuple[str, ...]
preconditions: tuple[str, ...]              # scientific_preconditions 码（G02 词表）
output_roles: tuple[str, ...]               # 产物可扮演的下游角色
fallback: str                               # "" | "buffer_proxy_disclosed" 等显式降级名
```

### AnalysisArtifactEnvelope（结果封套；大 FC 走 ref:，由 dispatch 缝铸造——本层只记录）
```python
step_id: str; workflow_id: str
ref: str                                    # ref:geojson-* / ref:raster/*（session store）
crs: str; units: dict[str, str]
feature_count: int; empty: bool
stats: dict[str, Any]                       # 透传工具 stats（count/sum/rate…）
method_receipt: StepReceipt                 # § evidence
warnings: tuple[str, ...]
lineage_inputs: tuple[str, ...]             # 上游 ref（≤16，对齐 artifact_registry）
fingerprint: str                            # § recompute
```

### Typed 码（decline/blocked，fail-closed）
`AWF_DATA_ROLE_MISSING_<ROLE>`（复用 DATA_ROLE_MISSING 语义，工作流层前缀）、
`AWF_NO_NETWORK_FOR_ACCESSIBILITY`（无路网不得给等时数值结论；可建议 buffer 但注明不同科学含义）、
`AWF_DENOMINATOR_MISSING`（无分母只给原始计数）、
`AWF_TEMPORAL_MISMATCH`（双期年份/CRS/范围未对齐）、
`AWF_TOOL_UNREGISTERED`、`AWF_UNIT_CONTRACT_VIOLATION`、`AWF_BUDGET_EXCEEDED`、
`AWF_CARDINALITY_UNBOUNDED`（M:N join 未收口禁止直接当分母）。

## 3. 4 个 Pi-native 工具（app/tools/analysis_workflow_tools.py）

| 工具 | 语义 | tier | 关键行为 |
|---|---|---|---|
| `webgis_analysis_workflow_plan` | 场景→契约+步骤 DAG+资格裁决 | 1 | 纯规划，零执行；数据缺失 typed blocked + alternatives；不猜测 ref |
| `webgis_analysis_workflow_run` | 执行 pending 步骤（可单步/全量） | 2 | 每步 registry.dispatch；结果封套+receipt；嵌套 geojson 提升外层铸 ref（#1867 同构）；预算/行数上限 typed |
| `webgis_analysis_workflow_status` | 状态/产物/lineage/失效视图 | 1 | 只读，有界投影 |
| `webgis_analysis_workflow_recompute` | 输入/参数变更→受影响子图重算 | 2 | fingerprint diff；style-only 零重算；复用未失效步骤产物 |

工具描述里显式「何时不用」；参数单位在 param_descriptions 声明；不做第二套参数校验（内层工具 apply_contract 是权威，编排层只做单位/角色合同级核验）。**不发射新前端 command 字符串**（复用 run 语义，工具名即 command——实现期核实 catalogue 不变量 #535 是否命中；若命中则同步登记 catalogue.ts 切片）。

## 4. 重算指纹（幂等键）

```
step_fp = sha256(canonical_json({
  "schema": 1, "tool": tool_name, "method_version": <algorithm registry 版本>,
  "params": step_params ∪ resolved_role_refs, "units": unit_contract,
  "science": {metric_crs_policy, denominator_policy, timezone},
  "input_fps": [upstream step fp…],        # 内容链（非 ref 字符串——同内容换 ref 不重算）
}))
```
- 输入内容指纹复用 `analysis_reuse.snapshot_input_shapes` / ref 内容摘要既有真相，不新造哈希语义。
- 失效传播：`invalidate(workflow, changed_dims) → {stale_step_ids}` 只沿 depends_on 下游闭包；`style` 维度永不进科学失效。
- 取消/失败步骤状态 `failed/cancelled` 持久于 state，恢复（重启/新 turn）后 resume 不重复写产物（幂等：同 fp 已有封套→跳过执行直接引用）。

## 5. Evidence（M8）

- 每步 `StepReceipt`：`{step_id, algorithm_id, method_use_contract_digest, params_applied, input_fps, output_fp, scientific_evidence_keys, warnings, executed_at_turn}`——Agent 读到的「已执行证明」，未执行步骤不得出现在 receipt 列表（防文字推断）。
- 终局：`workflow_evidence_summary(workflow)` 聚合各步 receipt → 喂 G02 `run_science_final_gate` 的输入投影（对齐 plan/execute/final 三段一致）。
- 回放：状态块带 `run_log`（有界 ≤24 条：step/turn/duration/outcome），G05 场景可断言。

## 6. 对既有面的最小适配（预测 + 实际核实后修正）

| 面 | 改动 | 性质 |
|---|---|---|
| app/tools/__init__.py 或注册入口 | 尾部 `register_analysis_workflow_tools` | append-only |
| gis_harness/tools.py | 不改（新工具独立文件） | — |
| skills loader | 自动 glob core/*.yaml，无索引改动 | additive |
| recipe packs | 5 族 recipe 增 `workflow_family` 引用键（可选字段，旧 recipe 零漂移） | additive |
| workflow_schema.py | 仅当需要新 recompute 维度词时 additive（尽量零改动） | 最小 |
| catalogue.ts / snapshots / docs 生成索引 | 仅在幽灵命令/生成物门禁命中时同步 | 最小 |

## 7. 明确不做

不重写 spatial_join/overlay/H3/kriging/isochrone；不动 PlanGraph/Recipe DSL V2/MethodUseContract/Pi ABI/V5 runtime 内核；不实现 SKATER/REDCAP 等新分区算法（统计分区族只用既有 H3/网格/行政/EB 算子组合）；不做新数据导入/前端编辑/新制图模板；不翻转 `GIS_WORKFLOW_RUNTIME_AUTORUN`。
