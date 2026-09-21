# Unified Cost / Resource / Planning Model — Spec（direction 7）

> ADR-0213 的工程规格。目标：`Candidate Plan → Unified Estimate → Feasibility →
> Resource-aware Ranking → Admission → Actual Usage → Calibration` 闭环。

## Problem Statement

planner 排序用分类档位（`estimate_for_node`），dispatch 准入用 rg.v1 数值估算
（`estimate_for_tool`），两者零互引用；计划级聚合是朴素求和；cost 维在
candidate score 与 `_COST_RANK` 双双空挂；actual 只回填 wall_time；replan/repair
只查次数不查资源令牌。资源/成本/延迟不是 plan candidate 的一等约束。

## Current Architecture（承接，不重写）

```
GraphNode ──qualify──▶ Candidates ──score(latency)──▶ CandidatePlan
                                                           │ (执行仍走 dispatch 单管线)
tool args ──▶ estimate_for_tool ──▶ ResourceDemand ──▶ governor.admit ──▶ execute ──▶ complete(actual)
```

## Ownership / Authority

- **数值估算唯一真相**：governor rg.v1 `ResourceEstimate`（先验表唯一驻留
  `governor/estimation.py`）。
- **资格裁决唯一真相**：`qualification_v8.qualify_node`（不变；资源排序绝不越过
  资格门槛）。
- **执行准入唯一真相**：`HarnessResourceGovernor.admit_and_reserve`（不变；planner
  只排序，Governor 保持最终执行准入权）。
- `ExecutionEstimate`（分类档位）降级为 rg.v1 的**派生投影**；modelops /
  geocompute 账本保持各自 L1 权威（只读不回灌）。

## Canonical Data Contracts（新增）

### PlanNode / PlanAggregate（governor/plan_aggregation.py，rg.v1 之上）

```
PlanNode:
  key: str                      # 计划节点 id（可读、bounded）
  label: str
  kind: SEQUENTIAL | PARALLEL | OPTIONAL | FALLBACK
  estimate: ResourceEstimate
  expected_attempts: int = 1    # retry 乘数（≤ MAX 8 钳位）
  cache_read_probability: float = 0.0   # [0,1]；仅折扣可缓存维
  cached_dims: tuple = (WALL_TIME_S, NETWORK_BYTES, RENDER_WORK_UNITS,
                        ESTIMATED_LLM_COST, EXTERNAL_SERVICE_CALLS)

PlanAggregate:
  estimate: ResourceEstimate    # source="plan_aggregate.v1"
  critical_path: list[str]
  parallel_peak_memory_bytes: float
  retry_tail_s: float
  cache_discount: float
  optional_pool: list[dict]     # 不进主聚合；执行则追加
  fallback_pool: list[dict]     # 触发才计费；入 retry/replan 预算
  worst_semantics: COMPARABLE|APPROXIMATE|NON_COMPARABLE|None
```

聚合语义（关键：**不能简单 sum 所有维**）：

| 维度类 | 规则 |
| --- | --- |
| WALL_TIME_S | sequential 求和；parallel 组取 max；retry 尾 `(attempts-1)×expected` |
| MEMORY_BYTES | live peak：sequential 组 max；parallel 组 sum（同时在驻） |
| 累计维（tokens/llm_cost/network/external/render_work/storage/feature/pixel） | 全路径 sum × attempts × cache 折扣 |
| unknown 维 | 保守地板参与判定（复用 DimValue.adjudged，聚合不抹 unknown） |
| OPTIONAL / FALLBACK | 不进主聚合；单列披露池 |

实现为两层分组树：`PlanNode` 按声明序聚成 groups（连续同类节点为一组），组内
kind 决定 max/sum。bounded：节点数 ≤ 64（超限截断 + disclosure）。

### 节点→ResourceEstimate 桥（gis_harness/estimate_bridge.py）

`resource_estimate_for_node(node, *, args=None)`：
- tool 面：`classify_tool(node.id, node.extras.cost)` → `estimate_for_tool(...)`（**唯一
  先验源**）；
- model 面：subsystem=REMOTE_SENSING、gpu_required=provider 推断、
  GPU_MEMORY_BYTES：extras 显式 `vram_bytes` 声明优先，否则 unknown 维
  （保守地板）、WALL slow 档；
- algorithm 面：complexity → latency 档 → 同一先验表。

`estimate_for_node` 改为**先调桥、再投影分类档位**（wall range → latency_class；
memory range → memory 档），basis 语义保留（declared/estimated/unknown），新增
`measured` 挂点（R5 回填后消费）。既有字段/签名不变（向后兼容）。

**Parity 不变式**：同一 tool（同名同 cost 档）经 planner 桥与经 dispatch
`_build_demand` 产出的 WALL_TIME_S/MEMORY_BYTES range 完全一致（单测锁定）。

### CalibrationStore（governor/calibration.py）

- 进程级 bounded：≤256 keys × 每 key 每 dim ring 64 样本；thread-safe。
- `record(tool_key, dim, expected, actual)`（expected≤0 或 actual<0 丢弃）。
- `stats(tool_key)` → per-dim {n, mean_ratio, max_ratio}；`snapshot()`/`restore()`
  供离线脚本；`suggest_priors(snapshot)` → 建议文件（**绝不自动应用**；先验更新
  只能显式改表/manifest 并评审）。

### loop_budget（gis_harness/loop_budget.py）

`loop_retry_admissible(session_id, loop) -> (bool, reason)`：governor
`RetryBudget.retry_allowed`（replan→RetryClass.PI、repair→SELF_HEAL、
deepen/requalify→DATA_FABRIC）令牌闸；`loop_charge(session_id, loop)` 实扣。
次数闸（LOOP_BUDGETS）保持在既有调用点不动。governor 缺席/异常 → 令牌闸
放行（fail-open 到既有次数语义，governor 为内存态无降级账本）。runtime_repair
的令牌拒绝按 exhausted 披露但**不递增** durable repair 计数（count_usage=False，
`token_denial_reason` 披露）—— 全局令牌池被他 session 耗尽不得无执行烧穿
本会话 durable 预算（review P2-7）。

## State Transitions / Failure Semantics / Idempotency

- 聚合是纯函数：同输入必同输出；无 IO 无锁。
- 桥是纯函数；graph extras 缺失 → unknown 档 + 保守地板（诚实披露，不猜）。
- calibration record 幂等无害（环形覆盖）；store 异常绝不影响 dispatch 结果
  （与 complete 记账同纪律：try/except + 计数）。
- loop charge 在预算闸**通过后**、回路动作执行前调用一次；拒绝路径零副作用。

## Security / Permission / Resource / Cost

- 无新外部面、无新权限；估算输入全部来自既有 registry/graph 元数据。
- aggregate 节点数与 cache 维度封闭词表，杜绝标签基数失控。
- 校准建议文件不进生产 hot path（人工评审后才可能改表）。

## Observability

- CandidatePlan dict 增加 `estimate`（bounded 摘要）与 `selection`（因子分解）；
- plan_aggregate.source 可追溯每维证据；
- calibration 经 `governor.snapshot()["calibration_keys"]` 暴露有界键数
  （Prometheus gauge 化为 follow-up）。

## Backward Compatibility / Migration

- `estimate_for_node` / `plan_candidates_v8` / `capability_resolution` 对外签名不变；
  Candidate 新增字段全部带默认值（dataclass 尾部追加）。
- `sum_estimates` 保留（既有调用方 tests）；新代码用 `aggregate_plan`。
- 无 DB/配置迁移；无 feature flag（纯增量 + 排序因子变化，deterministic）。

## Rollback

单 commit 粒度逻辑单元；排序行为可用 `GIS_RESOURCE_AWARE_RANK=0` kill-switch
回退到 latency-only（env 读取在 planner 单点）。

## Acceptance Matrix（DoD 映射）

| DoD | 证据 |
| --- | --- |
| 1 planner 消费统一 estimate | candidate score 因子含 cost/memory（test_resource_aware_selection） |
| 2 ≥2 类任务多 plan 选择 | admin_boundary_query / dataset_ingest live 压力分化用例 + 合成图压力翻转兜底（CI 无 registry 时保底证据） |
| 3 node/tool 口径统一 | parity 测试（同 tool 两侧 range 一致） |
| 4 retry/replan/repair 消耗预算 | 聚合 retry 尾 + loop_budget 双闸测试 |
| 5 Governor 保留执行准入权 | planner 只排序；dispatch 管线零改动（admission 测试既有全绿） |
