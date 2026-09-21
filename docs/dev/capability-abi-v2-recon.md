# Capability & Tool ABI 2.0 — Recon（方向 3，2026-09-20）

基线：`origin/master` = `5a4d4632`（PR #1478 合并后）。勘察方式：主线深读 +
只读 Subagent 全仓扫（双路一致）。

## 已合并能力（禁止重复施工）

| 能力 | 载体 | 状态 |
|---|---|---|
| Capability 词表权威 | `app/lib/gis/capability_registry.py`（153+ ids，域包 seeds，`induced.` 动态面） | 健康 |
| 工具描述符 ABI | `app/tools/descriptor.py` ToolDescriptor V3（side_effect 8 类 / network / deterministic / idempotent / security_tier / required_permission / requires_credentials / result_size_policy / crs_unit_semantics / capability_source 溯源 + 四指纹） | 成熟 |
| 统一能力图 | `capability_graph.py`（ADR-0137/0181，11 kind / 14 relation / 指纹缓存 / 结构审计） | 成熟 |
| 能力解析原语 | `capability_resolution.py`（resolve_capabilities / GoalRequirements / 全因子披露 / fallback 链 / describe·list 协议） | 成熟 |
| 候选规划 | `candidate_planner_v8.py` + `qualification_v8.py`（资格/成本/可靠性排序） | 成熟 |
| 确定性裁决点 | `algorithm_resolver.py`（capability→algorithm→tool，cost/科学门/backend） | 成熟 |
| 编译期清单 | `runtime_manifest.py` v3（compile once / fatal fail-fast / is_stale_plan） | 成熟但见缺口① |
| 交叉校验 | `registry_validation.py`（全库引用完整性 + 图闸 + 参数 parity） | 成熟 |
| 扩展包认证 | `capability_certification.py`（ADR-0201 六段管线，指纹绑定报告） | 不在本方向 |
| dispatch 绑定（仅 Pi） | `hotpath_convergence/capability_bind.py`（#1477，GIS_CAPABILITY_DISPATCH_BIND） | 见缺口② |
| 凭证/权限闸 | registry `CREDENTIALS_REQUIRED` / `PERMISSION_DENIED`（#1402） | 机制在、供应链未接 |
| Governor | ADR-0182 + #1472（DF cost / turn_id / durable budget） | 不触碰 |

## 真实缺口（本方向交付对齐）

1. **capability→tool 绑定双声明面漂移（A4）**：`AlgorithmRegistry.tool_candidates`
   （喂 manifest）与工具 `capabilities=` 声明（喂能力图 implements 边）互不对账。
   实测（真实 registry，345 工具）：悬空声明 **0**；声明但算法链不可达 **61 对 /
   57 工具**（`image_segmentation`×18、`workspace_state_inspection`×12、
   `plan_workflow_orchestration`×8、`admin_boundary_query`×5…）。manifest 对
   声明面全盲（`_project_tool` 只留 version/contract_version/tier/domains），
   且 manifest 有 `algorithm_dangling_capability`/`recipe_dangling_capability`
   fatal 却**没有 tool 侧对账**。
2. **bind 只覆盖 Pi 路径（A2）**：`ToolDispatchService.dispatch`（两条 agent 路径
   唯一拥有者）不绑定；ChatEngine legacy 路径零 capability 检查；situation 恒空。
3. **core conformance 缺席（A3）**：声明面一致性 / 多 provider 输出契约等价 /
   metadata 完整度分级只在扩展包认证里存在，core registry 无。
4. **dispatch 证据无 capability 维度（A5）**：链阶段 / TurnEvidence / ledger 都
   不记录「本次 dispatch 声明了哪些 capability、资格结论、为何允许/拒绝」。
5. **capability 级 ABI 聚合画像缺失（A1）**：ABI 事实散在 provider（工具）上，
   capability 级无 derived 聚合投影（deterministic 并集、凭证并集、副作用类
   并集、输出契约面、provider 溯源）。

## 偏移说明（相对原始 Prompt 的 A1-A5）

- A1/A2 的「多 provider 解析」V1 已由 ADR-0181 交付（resolve + ranking +
  dispatch bind 拒绝语义）——不重做，迁移到：bind 全路径覆盖 + 声明面收敛 +
  capability 级聚合画像。
- 「planner 不写死工具名」已达成（GoalRequirements capability 词表；prompt
  内 tool-name 字面量属横切文案工程，登记 out-of-scope，不在本 PR 动）。
- 不做：新 GIS 算法、重写 ToolDispatchService、新 plugin framework、绕过
  Governor、替换 registry 权威。
