# Capability & Tool ABI 2.0 — 设计（绑定收敛 / conformance / dispatch 证据）

- 日期：2026-09-20
- 方向：3（Capability & Tool ABI 2.0）
- 基线：`origin/master` = `5a4d4632`
- 关联：ADR-0137/0181（能力图与解析 V1）、ADR-0006/0014/0068（dispatch 单一
  拥有者）、ADR-0103（ToolDescriptor V3）、ADR-0201（扩展包认证）、#1395/#1402
  （bind 与凭证闸）、#1472（governor dispatch）
- Recon：`docs/dev/capability-abi-v2-recon.md`

## Problem Statement

capability→tool 绑定存在**两个互不对账的声明面**（算法 `tool_candidates` 派生
面 → manifest；工具 `capabilities=` 声明面 → 能力图 implements 边），导致同一
运行时有两份不一致的 provider 视图；dispatch 侧 capability 绑定只覆盖 Pi 路径
且无证据；core registry 无 conformance 闸；capability 级无 ABI 聚合画像。

## Current Architecture

```
声明面 A: AlgorithmRegistry.capabilities+tool_candidates ──→ runtime_manifest.capability_to_tools（描述性反查图）
声明面 B: ToolRegistry metadata capabilities=[...] ────────→ capability_graph REL_IMPLEMENTS 边（resolve/rank 输入）
执行面:   ToolDispatchService.dispatch（唯一拥有者）←── Pi bridge #1477 bind（仅此一处调用）
证据面:   chain stages / TurnEvidence / recovery ledger / tool_metrics（无 capability 维度）
```

## Ownership / Authority

- CapabilityRegistry：capability id 词表唯一权威（不变）。
- ToolRegistry：工具元数据唯一权威（不变）。
- **runtime_manifest v4：capability↔tool 绑定的编译期唯一汇聚点**（新增声明面
  B 的合并 + 对账闸；仍是只读投影，绝不反写 registry）。
- capability_graph：关系索引面（不变；消费两声明面，对账由 manifest 闸兜底）。
- ToolDispatchService：dispatch 期 capability 绑定的唯一调用点（新增）；
  `hotpath_convergence/capability_bind.py` 保持唯一实现。

## Canonical Data Contracts

1. `ConformanceIssue{code, severity(fatal|warning), tool, capability, detail}`
   （`app/lib/gis/capability_conformance.py`；有界，排序确定）。issue 码：
   `capability_id_dangling`（fatal）/ `capability_binding_unbacked` /
   `descriptor_metadata_incomplete` / `provider_output_contract_divergence`
   （warning）。manifest 折叠后的聚合码：`tool_capability_divergence`
   （= unbacked 聚合）与 `descriptor_metadata_incomplete`（聚合），
   输出契约分歧逐条（≤8）。
2. manifest v4 additive：
   - `_project_tool` 增 `"capabilities": sorted(declared)` 与
     `"output_semantic_type"`（绑定/契约漂移 → 指纹可见）；
   - `declared_capability_bindings: Dict[tool, List[cap]]`（溯源：与派生面
     差集即可分辨 source）；
   - `capability_to_tools` / `tool_to_capability` 合并声明面（算法优先序在前、
     声明面按字典序追加、去重）；
   - 新 issue：`capability_id_dangling`（fatal，经 conformance 校验器折叠）、
     `tool_capability_divergence`（warning，聚合单条，工具清单有界 ≤16）。
3. `ToolDispatchResult.capability_evidence: Optional[Dict]`（additive 默认
   None）：allowed/refused 双面 —— `{tool, action: allowed|refused,
   capabilities, [capability, status, reason, code], [rank, rank_capability],
   alternatives[]}`；无参数、无凭证、无 payload。
4. `TurnEvidence.add_capability_dispatch(entry)`（每 turn ≤16 条，thread-safe）。
5. `capability_abi_profile(capability_id)`（abi_version=2，derived 只读聚合）。

## State Transitions

dispatch 绑定（每次真实 dispatch，声明 capability 的工具）：
`normalize → chain-emit → dedup-acquire → CAPABILITY-BIND → guardrails →
reuse → cost-gate → governor → registry.dispatch`。
bind 结论：`allowed`（记录证据，继续）/ `refused`（释放 dedup 槽 + typed error
结果，`CAPABILITY_INELIGIBLE` code + alternatives）/ `skipped`（无声明、闸关、
图缺席、fail-open）。

## Integration Seams

- `ToolDispatchService.dispatch(+situation: Any = None)`（additive 形参；
  ChatEngine/tool_pipeline/workflow_engine/Pi 四个调用方零改动即生效）。
- Pi bridge：移除内联预检（dispatch 已绑定；typed details 经
  `raw_result` → `_slim_pi_details_payload` 自动流达）。
- `registry_validation.validate_gis_library`：折叠 manifest 的 fatal 级
  conformance 议题（与图闸同段）。
- `describe_capability`：嵌 `abi` 键。

## Failure Semantics

- conformance fatal = 引用破损（悬空 capability id）→ 启动 fail-fast
  （`GIS_MANIFEST_STRICT=0` 逃生既有）；warning = 完整度/分歧（不阻断）。
- dispatch bind：fail-open 纪律承袭 #1477（图缺席/异常/闸关 → 放行）；
  拒绝仅当 INELIGIBLE 且存在 eligible 替代（无替代 → 诚实放行给
  registry/governor 闸）。
- 证据写入绝不阻断调度（try/except 全包）。

## Idempotency / Replay

- bind 是纯读判定，重放确定性：同 (tool, situation, graph 指纹) → 同结论；
- `capability_evidence` 序列化有界 → 随 raw_result 入 trace/replay；
- manifest 指纹覆盖声明面 → 绑定漂移 → `is_stale_plan` 判 stale。

## Security / Permission

- 证据只含 id/code/score，无参数、无凭证、无用户内容；
- bind 拒绝文本不泄露内部 registry 结构（只列候选 id 与资格 reason）；
- 凭证/权限的执行闸仍在 registry `_dispatch_impl`（#1402），bind 不复制
  授权语义。

## Resource / Cost

- bind 开销 = 图缓存命中 + 纯函数资格检查（无 LLM）；无声明工具零成本早退。
  修正（review RB-P3）：reliability 因子读 durable ledger 的 session 聚合
  （stat + mtime 缓存，未命中才解析文件）—— 每次声明工具 dispatch 有 ~ms
  级同步读，与 #1477 Pi 路径既有开销一致；若 p99 显著再考虑 to_thread；
- manifest 编译增一次线性扫描（工具 345 / 能力 153 量级）+ 纯函数 conformance；
- TurnEvidence 每 turn ≤16 条有界。

## Observability

- 拒绝路径：typed `CAPABILITY_INELIGIBLE`（details 携带 alternatives/excluded）；
- 允许路径：`TurnEvidence.capability_dispatches`（为什么允许、备选是谁）；
- 启动路径：manifest issues 聚合披露分歧面。

## Backward Compatibility

- `MANIFEST_VERSION 3→4`（指纹全变 → 旧持久计划诚实判 stale，既有语义）；
- `ToolDispatchResult`/`TurnEvidence`/manifest 新字段全部 additive 默认值；
- bind 行为与 #1477 逐位一致（同 kill switch、同拒绝条件），只是调用点从
  Pi 单点迁到 dispatch 单点（legacy 路径新增该闸 = 修复而非破坏）。

## Migration Plan

单 PR 原子落地；无数据迁移；`test_scientific_contracts_vnext` 的
`MANIFEST_VERSION == 3` 钉死同步改 4（版本号语义=绑定面进指纹）。

## Rollback / Feature Flag

- `GIS_CAPABILITY_DISPATCH_BIND=0`：bind 全路径关闸（含新 legacy 覆盖）；
- `GIS_MANIFEST_STRICT=0`：conformance fatal 降级为日志；
- 声明面合并无独立开关（编译期纯投影；回滚 = revert）。

## Acceptance Matrix

| # | 场景 | 断言 |
|---|---|---|
| 1 | 2+ provider 同 capability（真实 registry ×3 类能力） | resolve/plan 确定性多候选；manifest capability_to_tools 含全部 provider |
| 2 | 声明面悬空 id | manifest fatal `tool_dangling_capability`；strict 启动拒 |
| 3 | 声明面分歧（61 对现状） | 合并进反查图 + 聚合 warning 单条 + 溯源字典 |
| 4 | 绑定漂移 | 声明变化 → manifest 指纹变化 → is_stale_plan |
| 5 | preferred provider 不可用（ledger 失败计数） | 排序翻转；bind 拒绝时 alternatives 披露替代 |
| 6 | permission denial（required_permission 未授予） | registry `PERMISSION_DENIED`；证据链可见 |
| 7 | credentials missing（requires_credentials 未 present） | registry `CREDENTIALS_REQUIRED` |
| 8 | geometry 不兼容（situation.geometry_kinds 冲突） | bind 拒绝 + eligible 替代披露 |
| 9 | CRS 约束（crs_class 冲突） | 同上 |
| 10 | 预算超限（budget_cost_class） | 资格 `budget_exceeded` → 替代选择 |
| 11 | provider health degraded | reliability penalty → 排序/拒绝证据 |
| 12 | 取消契约 | 拒绝发生在任何执行前（dedup 槽释放、零副作用）；abi profile 披露 cancellation 类 |
| 13 | 扩展工具生命周期注册 | 注入 registry 后 manifest 刷新可见声明绑定 |
| 14 | 确定性 provider 选择 | 同输入反复解析同序（tie-break by score,kind,id） |
| 15 | metadata 完整度 | 声明 capability 但缺 network/deterministic/side_effect → warning 分级 |
| 16 | 多 provider 输出契约分歧 | warning（非阻断、可观测） |

## Out of Scope（显式不做）

- prompt/plan 内 tool-name 字面量的词表化（横切文案工程，另行立项）；
- 凭证供应链接线（session → present_tool_credentials 生产布线）；
- governor cost 元数据丰富化；扩展包认证语义；新 GIS 算法；
- ToolDispatchService 结构重写；capability_graph 关系词表扩展。
