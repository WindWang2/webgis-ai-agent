# Capability Graph V1 — Ledger

每个 milestone 一节：目标 / 改动 / 契约 / 测试 / 证据 / 兼容 / 回滚 / 未解。

## M0 — Phase 0 勘察（2026-09-13）

- 目标：执行时基线核验 + 防重复对账。
- 改动：docs/dev/capability-graph-{recon,decisions,ledger}.md（本文件）。
- 关键事实：基线 580b33e9；V8 图存在但 qualification/candidates 生产零调用；
  四段（recipe/template/component/adapter）未投影；六关系未发射；四类图验证缺失；
  ADR 最高 0179，tts 线拟占 0180 → 本任务 0181。
- 测试：无（勘察阶段）。
- 回滚：纯文档。
- 未解：planner.py / candidate_planner_v8.py 精读在 M1 完成后补充（已完成，
  补充结论见 recon §2/§3：AlgorithmResolver 是生产裁决点，方向 4 线仅 docs 提交）。

## M1 — 四段投影 + 新边 + 结构审计（commit 11f3a19f）

- 目标：C0/C1/C2 —— 图覆盖 recipe/template/component/adapter provider 面；
  补 conflicts/deprecation 边；补四类结构验证。
- 改动：capability_registry.py（+offline_capable/incompatible_with +validate）、
  capability_graph.py（build §6-§9、REL_CONFLICTS_WITH、tool extras 资格面、
  弃用 fallback_to 边、provider 查询面、_structural_audit）、
  registry_validation.py（图闸只折叠 error 级）。
- 新/改契约：GRAPH_RELATIONS 13→14；GraphNode.extras 资格面（tool 12 键、
  capability 7 键）；CapabilityGraph.capability_providers/conflicts_of_capability/
  workflows_for_capability/templates_for_capability。
- 测试：test_capability_graph_v1.py 21 用例全绿；既有 43 用例（v8/v7/parity）全绿。
- 证据：在线图 938→957 节点 / 3108→3128+ 边、8 kind 全投影、0 error；
  C0 发现 ≥63 孤儿能力 + 1 真实 fallback 环（density_surface↔grid_binning）。
- 兼容：parity 闸（issues==[]）语义不变；V8 词表纯增量。
- 回滚：整体 revert capability_graph.py + capability_registry.py 两文件。
- 未解：孤儿/环的历史声明债（披露于目录，治理后续）。

## M2 — resolve_capabilities 门面 + 生产接线（commit a5b972ca）

- 目标：C3/C4/C5/C7/方向4接口 —— situation 三面 + 统一解析原语 + 真实接线。
- 改动：capability_resolution.py（新）、qualification_v8.py（Context+3 字段、
  qualify_node+3 检查、to_dict）、recipes.py（select_candidates 第 12 层）、
  planner.py（capability_evidence 字段、plan_from_intent/finalize 接线、memo 键）、
  tools.py（map_intent/map_product 构造 situation + guidance）、
  plan_orchestrator.py（合成/LLM 附着两路）。
- 新/改契约：GoalRequirements/ProviderCandidate/CapabilityDecision/
  CapabilityResolution（可序列化有界）；kill switch GIS_CAPABILITY_PLANNING_V1；
  select_candidates(+situation) 第 12 层（None 逐位一致）；
  plan.capability_evidence；finalize 必需能力失格 → CAPABILITY_INELIGIBLE_* 警告。
- 测试：test_capability_resolution.py 20 用例全绿；planner/eligibility/
  candidates/tool_surface/recipe downgrade/harness tools 233 用例全绿。
- 证据：offline 场景网络工具拒（offline_network_required）、本地工具存活；
  同输入双跑 JSON 逐字节一致；select_candidates(None)≡基线（回归钉死）。
- 兼容：two host paths（Pi 经 webgis_execute → map_intent；ChatEngine 经
  orchestrator）同语义；方向 4 只读协议不越界。
- 回滚：GIS_CAPABILITY_PLANNING_V1=0 运行时回退；revert 门面+接线文件静态回退。
- 未解：capability 级降级未纳入 ADR-0151 链（当前披露面）；adapter 无
  capability 声明面（不发明映射）。

## M3 — 生成目录 + 治理登记（commit f5ad99a9）

- 目标：C8 —— 确定性生成目录 + 三处治理登记。
- 改动：scripts/gen_capability_catalog.py（新）、docs/science/
  CAPABILITY_CATALOG.md（生成）、artifact_graph.py（DECLARED 登记）、
  docs/quality/generated-artifacts.json（单条合并）、.gitignore（scripts
  白名单）、docs/integration/ownership.json（regenerate 规则）。
- 测试：generated_artifact_graph + quality_v3_coordination 相关闸全绿
  （除 master 预存 4 失败：ledger_is_current / watermark / ratchet /
  preflight —— 干净 master 对照归因，#1270 修复面）。
- 证据：同注册表状态字节相同；C0 发现全量披露于目录。
- 回滚：revert 生成器 + 登记三处。
- 未解：master 16 项预存 stale（#1270 拥有刷新权）。

## M4 — Benchmark（commit cd737c0a）

- 目标：C9 —— 8 场景前后对比。
- 改动：tests/unit/gis_harness/test_capability_benchmark.py（新）。
- 指标：avg narrowing 0.842（offline 0.125）；invalid-attempt 节省最高
  0.667；context bytes 127~211B（bounded）vs 全 provider 名单；双跑逐字节
  deterministic 8/8；fallback 披露率 100%（huge feature count 场景 degraded
  + scale 因子双披露）。
- 回滚：纯测试。

## M5 — ADR-0181 + 文档收敛（本 commit）

- 改动：docs/adr/0181-gis-capability-graph-v1.md（新）；decisions 追加
  D12/D13；本 ledger 全量更新。
- 未解（后续接口点）：孤儿能力逐域治理；adapter 声明面；capability 级
  fallback 纳入 ADR-0151 链；measured basis 回填；方向 5 调度消费。

## M6 — 独立 review + P1/P2 修复（review pass commit）

- 四轴复核（Subagent B，APPROVE-WITH-FIXES）：Spec / Architecture /
  Reliability / Performance-Security。发现 1×P1、4×P2、11×P3；P1/P2
  全部修复，P3 修 8 项、明确不修 1 项（layer-12 per-call 缓存，实测
  1.4ms，记后续）、其余为记录性说明。处置明细见 decisions D15。
- 关键修复：to_bounded_context 溢出截断（P1）；kill switch 下沉
  select_candidates/planner 双层并强化测试为逐字节基线对照；
  build_situation 保留 0/False 观察事实；图冷构建 lifespan 预热 + 时长
  日志；registry_validation warning 走 logger（有界）；deprecated
  provider 排序因子；fallback 重规划线程 situation；map_intent
  contract_version 2→3。
- 测试：capability 四套件 80 passed；audit4 tools meta（cv3 pin）随改；
  planner/tool_surface/harness tools 重跑全绿（见 M6 回归记录）。
- 回归基线归因（干净 master 对照）：test_golden_cases_no_semantic_
  regression（G4）与 test_component_lifecycle trio 变体为 **master 预存
  失败**，与本任务无关；本分支在 gis_harness + chat 面零回归。
