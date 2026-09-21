# Decisions — Harness Trace/Replay 闭环 + 决策溯源 v2 (方向 8)

配套: `docs/adr/0204-trace-replay-closed-loop-decision-provenance.md`、ADR-0183 及其 recon/decisions/ledger。
本文记录实现级契约与验收矩阵；勘察事实底座见 ADR-0212 §1。

## 契约

### DecisionRecord（链上载荷，additive）

```json
{
  "decision": {
    "schema_version": 1,
    "decision_id": "dec_<sha256[:12]>",
    "kind": "plan_selection | capability_resolution | capability_dispatch_denial",
    "inputs_digest": "<sha256[:16]>",
    "inputs": { "...有界投影（≤32 键）": "..." },
    "selected": "<id>",
    "alternatives": [{"id": "...", "score": 0.0, "status": "...", "reason_codes": [...]}],
    "reason_codes": [{"check": "...", "observed": "...", "expected": "...", "hint": "..."}],
    "evidence_refs": ["recipe:...", "capability:..."],
    "policy_version": "..."
  }
}
```

- decision_id 确定性（turn_token + kind + inputs_digest + index），跨 run 对齐键。
- 发射点：planner CANDIDATE_WORKFLOWS（plan_selection）、planner SELECTED_WORKFLOW 附加记录 `phase=capability_resolution`（计划 + finalize 两处）、bridge TOOL_CALLS 附加记录（capability_dispatch_denial）。

### ReplayTrace v1 additive 字段

- `decisions: [{kind, decision_id, stage, selected, alternatives, reason_codes, policy_version, inputs_digest}]`（≤16 条，自链提取）。
- `situation_revision`: 首个 plan_selection 决策的 `inputs.situation`（QualificationContext 投影）；缺席 None。
- `env.registry_digest`: capability registry 规范投影 sha256（recorder 填充）。

### Scenario（roundtrip 产物）

- `scenario_id = rec-<session[:24]>-<turn[:24]>`；`category = "recorded"`；多轮 = 同 session traces 按 created_at_epoch 排序。
- ops ← trace.tool_calls（含生产 dispatch 面顶层 `geojson_ref` 提取的收据）；**T2 变异级对录制件不适用**（链上 MAP_MUTATIONS 只有 command 名/action_id，无参数）——recorded 场景恒 evidence-level，变异级回归仍是语料场景职责。
- `tags += ["degraded"]` 当 args digest-only / 收据缺席；决策索引与 `registry_digest` 随场景携带（bench 重放期 drift/delta 归因面）。

### drift / delta

- `capability_registry_digest()`: graph nodes(id/kind/status/version) + capability edges → canonical sha256。
- `diff_decisions(base, cur)` → `[{type: selected_changed|alternatives_changed|reason_codes_changed|added|removed, kind, decision_id?, baseline?, current?}]`（决策级归因，非裸 digest 漂移）。

## 验收矩阵

| 验收项 | 证据 |
|---|---|
| 真实 turn 生成含决策的 canonical trace | planner/bridge 发射 + build_trace 提取 + 单测 |
| recorded trace 可确定性重放 | roundtrip 单测：同 trace 两次重放 replay_digest 相等 |
| 多轮 trace 链接 | traces_to_scenario → 两 turn 共享 harness 证据累积单测 |
| 决策级「哪里变了」 | diff_decisions 单测 + bench compare 集成单测 |
| registry drift 显现 | 改 fixture registry digest → bench 报告 registry_drift 单测 |
| dispatch bind 决策可重放 | dispatch_backed 场景 bind-gate 重放单测 |
| replay 无生产副作用 | roundtrip 重放前后 BASE_STORAGE_DIR / 沙箱目录断言（沿用 + 新增） |
| 秘密不进 decisions | reason_codes/inputs 过 sanitize 单测 |
| golden 基线 ratchet 生效 | committed baseline + CLI drift → exit 1 单测（含失败消息可读） |
| 大 ref 不内联 | trace 预算/降级既有测试 + decisions 有界单测 |

## 资源纪律

- 新增测试全打 `cartography` marker（离线、无 Node/LLM/网络），低并发（`-n 0`）。
- bench 顺序执行维持（视觉裁判 env 进程级状态）。
