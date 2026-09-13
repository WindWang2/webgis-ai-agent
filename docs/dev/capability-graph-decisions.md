# Capability Graph V1 — Decision Log

约定：每条决策记录 备选 → 选择 → 理由 → 回滚面。执行中新增决策追加在文末，不改写旧条目。

## D1 — 任务定位：从"新建能力图"改为"激活 + 深化既有 V8 图"

任务书假设：方向 3 需要从零设计 CapabilityDescriptor + graph。
实际代码：ADR-0137（V8）已交付 capability_graph.py（六段投影、封闭词表）、
qualification_v8、candidate_planner_v8；V7 描述符已在 tool_surface_v3 生产在用。
**调整**：C0-C9 全部按"审计→补投影→补验证→接生产→生成物→基准"执行；
`resolve_capabilities` = 对 qualification_v8 + candidate_planner_v8 + recipes eligibility
的统一编排门面，不重写排序内核。
理由：防重复施工（任务书硬约束 4）；ADR-0137 已声明"零新业务 registry"。
回滚面：新门面独立模块 + kill switch，V8 内核不动。

## D2 — 生产接线点：planner.plan_from_intent 链 + plan_orchestrator 合成路径

备选：(a) 只接 ChatEngine plan_orchestrator；(b) 只接 Pi 面 pi_native_surface；
(c) 接 `plan_from_intent`（webgis_map_intent / webgis_map_product 共享确定性入口）+ `_synth_plan_from_harness`。
**选择 (c)**。理由：pi-host-seams.md 认定 webgis_map_intent 是 Shared 工具——两条宿主路径
都汇到这里；plan_candidates_v8/qualification_v8 的孤儿问题在这一层修复即同时覆盖两路径；
Pi 动态 surface 归方向 4（tts 线已锁定 worktree），本任务不动。
回滚面：`GIS_CAPABILITY_PLANNING_V1=0` 时逐位回到既有行为；capability 解析只**附加**证据字段
（`plan.capability_evidence`），不改变 recipe 选择结果的第一排序语义（见 D4）。

## D3 — ADR 编号取 0181，让出 0180

勘察：tts 线（方向 4）decisions D7 明确拟占 ADR-0180（当前未建文件）。
本任务取 **ADR-0181**，避免并行线撞号。若最终 tts 未用 0180，留空无害。

## D4 — resolve_capabilities 的排序语义：ability-first，recipe 保持既有权威

capability 候选排序采用 deterministic tie-break（score, kind, id）承袭 candidate_planner_v8；
但 **recipe 选择仍以 RecipeRegistry.select_candidates 十一层稳定排序为权威**，
capability 解析结果作为：(1) 资格预检（ineligible → 提前 fallback 链，替代事后失败重规划）；
(2) 工具面 narrowing 证据；(3) plan.capability_evidence 可解释证据附加。
理由：不破坏 ADR-0151 已验收的 fallback 语义；避免双权威。
风险控制：同 session 下 select_candidates 的输入不变，输出不变——有回归测试钉死。

## D5 — 投影纪律：四段新投影全部只读派生，owner 处补声明

recipe → `workflow` 节点（kind 词表已有）；product template → `template` 节点；
component → `component` 节点（dependency→composed_of、conflict→新关系 conflicts_with）；
data fabric adapter → `provider` 节点（binds_to capability 面）。
新增 metadata 只写在最接近 owner 的声明处：
- capability registry（app/lib/gis/capability_registry.py）增 optional 字段：
  offline_capable / side_effect_level / rollback / evidence_outputs / incompatible_with；
- 其余（tier/latency/network/deterministic/version/deprecation）从 tool descriptor 既有字段投影，不复制。
`source_fingerprints()` 为每个新段登记来源指纹，缓存语义保持诚实。

## D6 — 验证扩展：环 / 孤儿 / 无消费者 / deprecated 引用 进 validate_graph

新增检查全部为 **warning 级**（不阻断启动），error 级维持现状（dangling/duplicate/词表外）；
orphan capability（无任何 provider 路径）与 unreachable tool 也先 warning + 生成目录披露。
理由：master 现存 153 capability/327 tool 中可能有历史 orphan，error 化会把本任务变成
全库数据清洗；先可观测，再逐段收紧（记入后续接口点）。

## D7 — QualificationContext 扩展为 Situation 载体（additive optional）

新增 offline / budget(可选 cost ceiling) / auth_tier 字段 + data size 已有。
qualification 增两条检查：offline 时 network-dependent → degraded(原因)；
auth_tier 不足 tier 要求 → ineligible(原因)。旧构造点零破坏（全默认值）。
不复刻 El EligibilityContext（recipes 域）——两者经 resolve_capabilities 编排，不合并类型。

## D8 — Provider resolution 规则（C5）

capability → provider 候选 = 图上 implemented_by(algorithm→tool) ∪ exposed_by(tool)
∪ implements(model) ∪ composed_of(recipe) ∪ binds_to(adapter)。
选择权重：资格（硬门）→ 数据规模适配（supports_large_data/scale_class）→ local-first
（network=offline 优先当 offline 场景）→ cost 档位 → side_effect 温和优先 →
历史可靠性（reliability_from_ledger / recipe affinity，有事实才计分）→ 确定性 tie-break。
降级（fallback/substitute）必须携带 reason code 出现在证据里——无隐藏降级。

## D9 — 生成物：scripts/gen_capability_catalog.py → docs/science/CAPABILITY_CATALOG.md

沿 ALGORITHM_CATALOG 同款纪律：确定性字节、登记 artifact_graph、check_generated_staleness 闸。
不生成 JSON 第二事实源——catalog 文档是投影披露，事实仍在 registry。

## D10 — 方向 4/5 接口让位

- 方向 4（typed tool surface）：本任务只交付只读查询协议
  （`capability_query.py`：describe_capability / list_capabilities / resolve_capabilities 的
  read-only 序列化面），不改 pi_native_surface，不接管字节预算。
- 方向 5（execution graph）：resolve_capabilities 输出 capability requirements + provider choice
  （纯数据，可序列化），不含调度；execution graph 可直接消费。

## D11 — 资源纪律

迭代用 focused pytest + --no-cov；全量回归最多一轮（-n 2）；next build 最终阶段至多 1 次
（本任务预计不触 frontend；若不动 frontend/lib 则不跑）。

---
（执行期新增决策追加于此）
