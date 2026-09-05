# 完成契约与 Verdict V2（Completion / Verdict Contract）

完成判断从「工具跑过」提升为**七维可审计状态**；七维之上的单字产品裁决
词表保持冻结（V1 兼容），新增的 workflow 契约硬违反可以**向下**压档。

## 七维（COMPLETION_DIMENSIONS）

| 维度 | 问题 | 证据源 |
| --- | --- | --- |
| `data` | 必选数据角色覆盖完整？无数据族阻断？ | workflow_contract.data_blockers + 数据族 findings |
| `analysis` | 能力 DAG 关键节点完成？ | needs_execution 等 execution findings |
| `science` | 科学义务满足（方法成立）？ | workflow_contract.method_blockers / blocked 义务 |
| `cartography` | 主专题图层与组件落地？ | layer_status / component_status / layer findings |
| `observed_map` | 前端实测渲染有效？ | render_status ∈ {verified, not_applicable} |
| `methodology_disclosure` | 触发的方法论义务都已披露？ | 义务警告码 ⊆ 章节警告码 |
| `uncertainty_disclosure` | 不确定性已披露？ | 无 blocked 的 uncertainty 义务 |

输出：`derive_product_verdict(...)["completion_dimensions"]`（bool×7，
additive 键，旧读者忽略零漂移）。

## Verdict 词表（冻结）

`READY / READY_WITH_WARNINGS / NEEDS_REPAIR / BLOCKED_BY_DATA / BLOCKED_BY_METHOD`

推导（纯函数 `derive_product_verdict(result, methodology_warnings, *,
chapter=None)`）：

```text
failed + 全数据族错误              → BLOCKED_BY_DATA
failed + 有非数据族错误            → BLOCKED_BY_METHOD
needs_repair / pending            → NEEDS_REPAIR
complete + 零警告（含方法论警告）   → READY
complete + 有警告                  → READY_WITH_WARNINGS
# ── Workflow V2 追加（压档不升档）─────────────────────────────
complete 档 + workflow data_blockers   → BLOCKED_BY_DATA
complete 档 + workflow method_blockers → BLOCKED_BY_METHOD
```

红线：**任何「方法不成立」不能通过漂亮地图掩盖** —— 即使渲染完美
（合成 complete + render verified 面），science 维硬违反也会裁决
BLOCKED_BY_METHOD。可断言形态见 `WC-kriging-blocked-no-field` 契约案例。

## 数据流

```text
planner.finalize_with_profile
  → _evaluate_workflow_contract(recipe, profile)
      → plan.workflow_contract        # 有界摘要（schema_version/roles/
                                      # obligations/blockers，stage=finalize）
      → plan.methodology_warnings     # 义务/角色披露（幂等去重）
      → plan.fallbacks                # 语义回退（downgrade_class+disclosure）
completion/pipeline.maybe_finalize_map_product
  → map_product_block(..., chapter=gis_chapter)
      → map_product.product_verdict   # V2（含 completion_dimensions）
```

chapter 无 `workflow_contract`（纯 V1 recipe / 旧会话）时行为与历史一致。
