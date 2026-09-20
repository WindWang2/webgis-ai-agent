# ADR-0204: Harness Trace/Replay 闭环 + 决策溯源 v2

- 状态: Accepted
- 日期: 2026-09-20
- 线: harness/trace-replay-closed-loop-v2（方向 8）
- 关联: ADR-0183（replay/benchmark/explainability v1 —— 本线全部复用其契约）、ADR-0103 §十（18 阶段链）、ADR-0181（capability resolution）、V9-decision-chain/#1395（dispatch bind）、ADR-0159（ratchet/质量事实）

## 1. 背景与偏移说明

本方向原始任务书预设 T1-T6 六个工作包。勘察（2026-09-20，origin/master=5a4d4632）确认 ADR-0183 v1 已完整落地：ReplayTrace v1 打包层、settle 缝录制器、T1 证据级 + T2 变异级离线重放、140 场景语料、10 型故障注入、ratchet 接流、explain/triage/bench CLI、72 个 replay 测试。**机械重做即是重复建设**，故按任务书规则第 7 条，工作重心迁移到 v1 遗留的结构性缺口（r10 ledger 未解决项 + 勘察新发现），即本 ADR 的四个决策。

真实缺口（勘察证据）：

1. **录制件是 write-only 的**：`maybe_record_turn` 在生产 settle 缝采集了消毒、有界、自包含的 ReplayTrace，但 `OfflineReplayer` 只吃编排语料（Scenario），没有任何「录制轨迹 → 可重放场景」的转换器。真实流量无法自动沉淀为回归用例——「录制→重放」闭环断裂。
2. **决策溯源无统一记录**：`PlanCandidate` 有 9 维 scores + rejection_reasons，`CapabilityDecision` 有 rejected/degraded_alternatives/why，`QualificationReason{check,observed,expected,hint}` 是结构化 reason code 先例——但 `planner.py` 发射进链的 CANDIDATE_WORKFLOWS 只有候选 **id 串**（scores/rejection reasons 全部落链外），无 decision_id、无 per-decision 输入指纹、无 policy 版本。explain 的 rejected_options 披露因此退化为 id 级。
3. **无 registry/manifest drift 检测、无 decision delta**：`replay_bench --baseline` 只比整体 `replay_digest`（pass/fail），回答不了任务书 DoD#3「benchmark 能定位『哪里变了』」；录制时与重放时的 capability registry 漂移完全不可见。
4. **replay 基线不落地**：无 commit 基线文件、gate 是 opt-in 手动路径，回归 ratchet 对 golden 场景不生效。

## 2. 决策

### 决策一：DecisionRecord = 链上载荷的 additive 形态，不是新链、不是第二 trace

`app/lib/runtime/decision_record.py` 提供纯函数 `decision_record(...)`：`{decision_id, schema_version, kind, inputs_digest, inputs(有界投影), selected, alternatives[:8], reason_codes[:6], evidence_refs[:8], policy_version}`。约束：

- **decision_id 确定性**：`dec_<sha256(kind+turn_token+inputs_digest+index)[:12]>`——同输入同 id，录制与重放产生的同一决策可跨 run 对齐（decision delta 的对齐键）。
- **决策 riding 既有阶段**：plan_selection 落 CANDIDATE_WORKFLOWS 载荷、capability_resolution 落 SELECTED_WORKFLOW 附加记录（`phase="capability_resolution"`，emit_chain 追加）、dispatch bind 拒绝落 TOOL_CALLS 附加记录。不加新 Stage、不改链 schema（payload additive）。
- **有界**：候选详情用既有 `PlanCandidate.to_bounded_dict()`（每条 ~600B × ≤8）；reason_codes 复用 QualificationReason 四元组形状；全 payload 过既有 `bound_meta` 消毒。
- **生产发射点**（全部在既有 try/except 记录面纪律内）：`planner.py` CANDIDATE_WORKFLOWS 块（补 candidate_details + decision）；`planner.py` capability resolution 两处（计划/finalize）；`agent_pi_bridge.py` dispatch bind 拒绝路径。

### 决策二：ReplayTrace v1 additive 演进（不 bump schema_version）

- 新增 `decisions` 字段：`build_trace` 从链内带 `decision` 标记键的记录提取（`_decisions_from_chain`）。v1 文件（无该字段）读取照旧；v2 文件被 v1 代码读时进 `_unknown_fields` 原样保留——双侧兼容由既有 additive-only 纪律背书。
- `situation_revision` 从预留变实接：取首个 plan_selection 决策的 `inputs.situation`（QualificationContext 有界投影，即 context projection version）。缺席（旧链/无决策）保持 None——诚实降级。
- `env.registry_digest`（录制时 capability registry 规范投影 sha256）由 recorder 填充，供重放期 drift 检测。
- `GisTraceChain.as_dict()` 增加 `schema_version: 1` 常量字段（链记录本体此前无版本；manifest 层 version:1 不动）——读取侧零行为变化。

### 决策三：roundtrip —— 录制轨迹回到重放器（闭环核心）

`app/lib/harness/replay/roundtrip.py`：

- `trace_to_scenario(trace) -> Scenario`：tool_calls → ScenarioOps（arguments/result 均已是消毒后的有界形状，T1 证据级重放所需的 receipt 形状齐全）；mutations.records → T2 mutation specs（可重放时）；verdict/outcome 不进 expect（重放重推导，防 over-constrain 假红）；expect 默认空 + 结构事实（ops 数、非 error 数）。
- `traces_to_scenario(traces) -> Scenario`：同 session 多条 trace 按 `created_at_epoch` 排序合并为 multi-turn turns——**多轮 trace 链接**（重放器共享 harness 的会话语义天然承载跨轮情境持续性验证）。
- 副作用保障：roundtrip 场景进 `OfflineReplayer.replay_scenario`，复用既有 `_sandboxed_mutation_store` + `run_token` + offline judge env——**绝不写真实 session/map**。
- 录制件缺args（digest-only）/无 receipt 形状时：诚实降级为 evidence-only 重放并在场景 `tags` 标注 `degraded`，不伪造。

### 决策四：drift 检测 + decision delta + committed 基线

- `app/lib/harness/replay/drift.py`：`capability_registry_digest()`（graph 节点/边/version 的 canonical sha256）；重放 recorded-roundtrip 场景时比较 `trace.env.registry_digest` vs 当前 → bench 报告 `registry_drift`（drift ≠ fail，但**显式披露**——digest 漂移的归因面）。
- `diff_decisions(baseline, current)`：按 decision_id/kind 对齐，输出 `{kind: selected_changed|alternatives_changed|reason_codes_changed|added|removed, decision_id, baseline, current}`——回答「哪里变了」。
- bench compare：digest 漂移条目附 decision-level delta（基线条目新增 `decision_digests`/`decision_count`）；`replay.*` metrics 族新增 `decision_count` / `capability_denials` 行（走既有 ratchet 通道）。
- **committed 基线** `tests/fixtures/replay/baseline.json`（140 场景 digest + green 计数 + corpus_version + seed）：`scripts/replay_bench.py --baseline` 对提交基线校验，drift/red → exit 1。基线内容是提交语料的确定性函数，与既有「fixture↔生成器逐字节 parity 钉」共同构成防漂移双闸。基线更新是人工显式提交（不自动更新——承袭 ADR-0159/0183 纪律）。

### 决策五：T3 dispatch 级重放 = capability-bind gate 重放（诚实降级范围）

`dispatch_backed=true` 的场景从恒 `not_run` 变为实装：以场景携带的 `tool_registry` fixture（工具名 → capabilities 声明）重放 **dispatch bind gate**（生产同函数 `check_tool_capability_at_dispatch`），比对 allow/deny 裁决 + alternatives（expect["dispatch"] 白名单可钉）。**receipt 重发经 ToolDispatchService 假服务仍不做**（需要完整 registry/tenancy 服务面，离线约束下成本远超收益；v1 决策三的否决案继续成立）——以 `deferred_levels=["receipt_redispatch"]` 诚实披露，与「作者声明了却跑不了」的 `not_run`（毒化 ok）区分。这是「dispatch 决策层」的确定性回归牙齿，不是执行层重放。

## 3. 不做（边界）

- 不建新 trace 体系/新存储（四层 trace 分工维持）；trace 不当聊天历史。
- 不做前端 inspector UI（r10 D10 接口点继续预留）。
- 不做 LLM judge 真值、不要求网络。
- 不自动激活 ratchet 基线 / 不自动更新 committed baseline。
- per-tool governor estimate/actual 入链（资源估算 vs 实测回灌校准）—— seam 在 tool_dispatch_service 深处，超出本 PR 边界，记 follow-up。

## 4. 后果

- 正面：真实流量可沉淀为回归语料（闭环）；决策证据全链可解释（scores/rejections 进 trace）；digest 漂移可归因到具体决策与 registry 版本；golden 场景基线入库、gate 可执行。
- 代价/风险：链 payload 增重（候选详情 ≤5KB/turn，受 max_per_stage=8 与 512KB trace 预算双界）；recorder 多一次 registry digest 计算（opt-in 路径，非热路径）。
- 回滚：删去发射点 payload 扩展（additive 键，读取侧自容忍）+ roundtrip/drift 为纯新增文件；committed baseline 删除即回 v1 行为。
