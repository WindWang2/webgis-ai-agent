# Workflow Recipe DSL V2

V2 在 `CartographyRecipe`（制图方法契约，V1 字段完全不变）上 additive 地
挂载一个可选的 `WorkflowProfile`（`recipe.workflow`）。`workflow=None` 的
recipe 行为与历史完全一致（V1 seed 即是）。

## 模型总览（app/services/gis_harness/workflow_schema.py）

```text
CartographyRecipe
├── schema_version: int = 1          # V2 recipe 声明 2
└── workflow: WorkflowProfile | None
    ├── schema_version = 2
    ├── domain / workflow_family      # 领域包归属 / 工作流族
    ├── keywords_zh / keywords_en     # 专业路由词（倒排索引）
    ├── data_roles: [DataRoleRequirement]
    ├── obligations: [ScientificObligation]
    ├── completion_requirements: [CompletionRequirement]
    ├── fallback_policies: [WorkflowFallbackPolicy]
    ├── required_disclosures: [str]   # 触发时必须用户可见的义务码
    ├── recompute_dimensions: [str]   # ⊆ data/algorithm/parameter/style/output
    └── evidence_requirements: [str]  # 证据种类要求（如 calibration_evidence）
```

## DataRoleRequirement（数据角色，C3）

```python
DataRoleRequirement(
    role="denominator",            # ⊆ DATA_ROLES（15 个，见 data-roles.md）
    required=True,
    acquisition="local",           # local | data_fabric | user_upload | derived
    capability_hint="admin_boundary_query",   # 供给 capability（存在性校验）
    accepted_artifact_types=("admin_aggregate_table",),
    geometry_kinds=("polygon",),
    missing_policy="block",        # block（阻断结论）| degrade（降级+披露）
    reason_code="",                # 空 → DATA_ROLE_MISSING_<ROLE>
    degrade_disclosure="",         # 降级时的用户可见披露
    must_not_guess=True,           # LLM/模型不得编造该数据
)
```

解析语义（`resolve_data_roles`，确定性）：

| 条件 | status |
| --- | --- |
| 调用方显式绑定（bound_refs） | `bound` |
| profile 字段证据（denominator 字段在场） | `bound` |
| acquisition ∈ {data_fabric, user_upload} | `external`（规划期不可证伪，不假设缺失） |
| capability_hint 存在于 registry | `bound` |
| 其余 + policy=degrade | `degraded`（强制披露） |
| 其余 | `unresolved`（block 策略 → data_blocker） |

## ScientificObligation（科学义务，C4）

```python
ScientificObligation(
    obligation_id="kriging_min_samples",
    kind="precondition",           # precondition|denominator|temporal|
                                   # uncertainty|transformation|disclosure
    precondition_id="min_numeric_samples:8",   # kind=precondition 时必填，
                                               # 必须命中算法层已注册 id
    warning_code="KRIGING_INSUFFICIENT_SAMPLES",  # 稳定机器可读码
    on_violation="degrade_with_disclosure",  # block_method|degrade_with_disclosure|warn
)
```

**联动不重复**：`kind=precondition` 直接调用
`app/lib/gis/scientific_preconditions.py::evaluate_precondition`（五值裁决
PASS / PASS_WITH_WARNINGS / REQUIRES_TRANSFORM / INSUFFICIENT_DATA /
INVALID_METHOD），profile 缺事实 → PASS（unknown ≠ unsatisfied）。

规划期披露语义：

| kind | 规划期（无运行证据）行为 |
| --- | --- |
| precondition | 委托算法层按 profile 事实裁决 |
| denominator / temporal | 角色/字段证据判定；不足 → warning / blocked |
| transformation / disclosure | **披露即浮出**（warning）——「是否满足」未知 ≠ 「不该披露」 |
| uncertainty | unknown（完成契约在运行期核验） |

## WorkflowFallbackPolicy（语义回退，C6）

```python
WorkflowFallbackPolicy(
    reason_code="KRIGING_INSUFFICIENT_SAMPLES",
    from_element="kriging_surface", to_element="point_map",
    downgrade_class="degraded",    # equivalent|approximation|proxy|degraded|not_allowed
    disclosure="样本不足：不出插值面，仅呈现采样点分布（禁止伪造表面）。",
    blocks_completion=False,       # not_allowed 降级必须 True
)
```

与 V1 `RecipeFallback`（制图元素级资格回退）并行；触发的回退最终都产出
结构化 `FallbackDecision`（V2 增补 `downgrade_class` / `disclosure`
字段）——**模型 fallback 不得隐去语义降级**。

## CompletionRequirement（完成契约声明侧，C7）

七维词表（`COMPLETION_DIMENSIONS`，与 `completion/contracts.py` 平铺表
parity 锁定）：`data / analysis / science / cartography / observed_map /
methodology_disclosure / uncertainty_disclosure`。

声明侧随 recipe 定义；证据侧由完成管线在 `derive_product_verdict(
result, warnings, chapter=chapter)` 中核验，输出
`completion_dimensions` 与最终 verdict。

## 指纹（C12）

`recipe_content_fingerprint(recipe)` = canonical JSON（sort_keys，无时间
戳）的 SHA256。`RecipeRegistry.content_fingerprint()` 聚合全部 recipe；
经 `runtime_manifest` 的 recipe 投影参与 manifest 指纹 → workflow 语义
变化必然使旧计划 stale（`is_stale_plan`）。
