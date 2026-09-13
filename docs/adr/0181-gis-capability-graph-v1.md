# ADR-0181: Harness — GIS Capability Graph V1（能力图激活与深化）

- 状态：Accepted（本地验证）
- 日期：2026-09-13
- 关联：ADR-0137（Harness V8 unified capability runtime，本 ADR 的直接前序）、
  ADR-0134（V7 结构化检索）、ADR-0151（recipe 资格/降级链）、
  ADR-0161（learnable intent & recipe affinity）、ADR-0171~0174（ADS source
  registry / 检索 / fallback）
- 编号协调：0180 留给 typed-tool-surface 线（其决策文档 D7 预告占用）
- 基线：`origin/master` = `580b33e9`（#1271/#1272 合并后）

## 背景

ADR-0137 交付了统一能力图（六段投影、封闭词表、fingerprint 缓存），
但勘察（`docs/dev/capability-graph-recon.md`）确认三个缺口：

1. **生产零调用**：`qualification_v8` / `candidate_planner_v8` 只有测试
   caller —— 能力图不是 planning 的一等输入，是文档摆设；
2. **投影不全**：recipe（164）/ product template（9）/ map component
   （21）/ data fabric adapter（15）四段 provider 面不在图上；关系词表
   13 种中 6 种定义了但从不发射；无 conflicts 语义；
3. **验证不足**：无环检测、无孤儿能力 / 不可达工具 / 无消费者 artifact /
   弃用暴露的结构审计（C0 审计无机器面）。

## 决策

### D1 — 激活而非重建（防重复施工）

不建第 N 套 registry / planner。V1 全部产出是 ADR-0137 纪律的延伸：
图仍是无业务语义复制的**只读派生投影**；`resolve_capabilities` 是对
qualification_v8 + candidate_planner_v8 排序内核 + capability_status
聚合语义的**编排门面**，不重写排序。

### D2 — 四段 provider 投影（节点 kind 全部激活）

- recipe → `workflow` 节点（requires → preferred/optional capability；
  composed_of → default_components；fallback_to → ADR-0151 FallbackLink）；
- product template → `template` 节点（requires → layer_roles
  source_capability；composed_of → default_components；binds_to →
  source_artifact）；
- map component → `component` 节点（requires → dependencies；
  **conflicts_with → conflicts**（新关系，词表 13→14）；binds_to →
  compatible_artifact_types）；
- data fabric adapter → `provider` 节点（pushdown 旗标入 extras；
  **不发明 adapter→capability 映射表** —— 无声明源，声明面出现时补边）。
- tool 节点 extras 增资格面投影（status/side_effect/network/deterministic/
  idempotent/scale_class/cost/security_tier/deprecation_of）；
  `deprecation_of` → fallback_to 边（弃用链）。
- capability registry 增 owner 级声明（additive 全默认）：
  `offline_capable`（None=未声明，由 provider 面推导）、
  `incompatible_with`（validate 校验 + conflicts_with 边）。
- `source_fingerprints()` 增四段新来源键 —— 缓存诚实性不降级。

### D3 — Situation = QualificationContext（additive 三面）

`offline` / `auth_tier` / `budget_cost_class` 进 V8 六面上下文（全默认，
既有构造点零破坏）；qualify_node 增三条检查：offline × tool.network →
`offline_network_required`；security_tier/tier > auth_tier →
`auth_tier_insufficient`；cost 档位超预算 → `budget_exceeded`。
缺席面不裁决（None/"" = 未约束），不猜。

### D4 — resolve_capabilities（能力层规划原语）

```
resolve_capabilities(goal_requirements, situation)
  -> CapabilityResolution{decisions[capability → status, ranked providers,
     rejected(w/ reasons), degraded_alternatives, missing, make_available,
     why], conflicts, status_summary}
```

- provider 候选 = 图上 tools ∪ models；资格 = qualify_node；排序因子
  全披露：latency 档位 + degraded 罚 0.5 + 可靠性罚分（既有 ledger）+
  offline 本地加成 −0.25 + destructive 副作用罚 0.25 + 数据规模失配罚
  0.25；tie-break (score, kind, id) —— 同 Situation 决策 deterministic。
- **无隐藏降级**：ineligible/degraded 必须携带 fallback 替代（链深 ≤2）
  或 make_available 提示（含被拒 provider 的修复 hint）。
- 能力聚合资格 `capability_status` 是单点语义：any eligible → eligible；
  否则 degraded > unknown > ineligible。
- 能力级互斥（incompatible_with）双能力同 goal 时进 `conflicts` 披露。
- LLM 不参与裁决；输出是可序列化计划证据；执行仍走 ToolRegistry
  单一管线（Pi-as-host 不变；不复制 tool loop）。

### D5 — 生产接线（真实调用链，非孤儿模块）

- `RecipeRegistry.select_candidates(+situation)`：第 12 层能力资格罚分
  （必需失格数, 可选失格数）插在项目记忆层之后、priority 之前；
  `situation=None`（全部历史调用点缺省）逐位一致。
- `webgis_map_intent` / `webgis_map_product`（Shared 确定性入口，两条
  宿主路径汇点）：构造 task 语义 situation；候选层 + 证据附加 + guidance
  资格摘要行。
- `plan_orchestrator`（合成 + LLM 附着两路）：同语义 situation。
- `MapProductPlanner.plan_from_intent / finalize_with_profile(+situation)`：
  附 `plan.capability_evidence`（bounded）；finalize 以 profile 数据事实
  刷新；必需能力失格 → methodology_warnings 留痕（`CAPABILITY_INELIGIBLE_*`，
  只披露不改 fallback 路由 —— capability 级降级纳入 ADR-0151 链是后续项）。
- kill switch：`GIS_CAPABILITY_PLANNING_V1=0`（默认开）回退逐位历史行为；
  memo 键含 situation digest（同 session 不同情境不串证据）。

### D6 — 结构审计（先可观测，再收紧）

`validate_graph` 增 `_structural_audit`（全 warning 级，发现数 ≤512）：
cycle_detected（**不含** implements 向上闭合边与对称 conflicts 边 ——
`cap→implemented_by→algo→exposed_by→tool→implements→cap` 是跨 registry
一致性闭环，不是矛盾环）、orphan_capability、unreachable_tool、
artifact_no_consumer、exposes_deprecated_tool。registry_validation 的图闸
**只折叠 error 级**（parity 闸语义不变）；warning 级经生成目录披露。
C0 首轮发现（63+ 孤儿能力、`density_surface↔grid_binning` fallback 环）
记录于 CAPABILITY_CATALOG.md，数据治理后续逐段收敛。

### D7 — 生成物（C8）

`scripts/gen_capability_catalog.py` → `docs/science/CAPABILITY_CATALOG.md`
（确定性字节、无时间戳）；artifact_graph.DECLARED 登记（inputs 覆盖全部
事实源）；.gitignore scripts 白名单 + ownership.json regenerate 规则
（parity 闸绿灯）。ledger 采用**单条合并**刷新 —— master 现存 16 项
预存 stale 属 `fix/ci-adaptive-hygiene`（#1270）修复面，本任务不吞入。

### D8 — 收敛语义（C6 一句话契约）

capability graph 是唯一**索引面**：算法库=计算能力，模板库=地图产品/组件
能力，tools=执行 adapter，Data Fabric/ADS=数据供给 provider，recipes=
能力组合的工作流面。模板不伪装成 tool，tool metadata 不复制进 recipe，
capability 词汇不第二声明。

## 兼容性

- 零 migration、零新表、零 schema 变更；capability registry 两个字段
  additive 全默认。
- 既有消费方不受影响：V7 `select_capabilities` / tool_surface_v3 rerank
  不动；`select_candidates` 缺省逐位一致（有回归测试）；
  registry_validation 只折叠 error 的行为对现存数据无 diff（当前 0 error）。
- 方向 4（typed tool surface）：本 ADR 只交付只读查询协议
  （describe_capability / list_capabilities），不动 pi surface。
- 方向 5（execution graph）：消费 `CapabilityResolution.to_dict()`
  （requirements + provider choice），调度不在本 ADR 范围。

## 测试

- `tests/unit/gis_harness/test_capability_graph_v1.py`（21）：四段投影 /
  新边 / 查询面 / 合成图逐类审计 / 在线 registry 零 error / owner 字段。
- `tests/unit/gis_harness/test_capability_resolution.py`（20）：解析原语 /
  determinism / offline-auth-budget 三面 / 无隐藏降级 / 冲突披露 / 只读
  协议 / 第 12 层（合成 registry 钉死语义）/ plan 证据 / kill switch。
- `tests/unit/gis_harness/test_capability_benchmark.py`（12）：8 场景
  前后对比（收窄比 avg 0.842、offline 0.125、invalid-attempt 节省最高
  0.667、context bytes 127~211B、双跑逐字节 deterministic）。
- 既有回归：capability graph v8 / descriptors v7 / parity / eligibility v4 /
  plan_candidates / planner / tool_surface / recipe downgrade / harness
  tools 全绿（230+ 用例）；生成物闸 + ownership parity 绿。

## 已知限制 / 后续

- 孤儿能力（63+）与 fallback 环是**历史声明债**，目录已披露；逐域收敛
  （补算法/工具声明或退役词汇）是独立治理线。
- adapter→capability 声明面出现后补 binds_to 边；capability 级降级纳入
  ADR-0151 fallback 链（当前仅披露）。
- ExecutionEstimate 的 measured basis 待性能台账回填（承 ADR-0137）。
