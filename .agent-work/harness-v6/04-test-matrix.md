# Harness V6 Test Matrix（本地验证矩阵）

## changed-scope 单测（全部本地，无 CI 依赖）

| 套件 | 覆盖 | 状态 |
|------|------|------|
| tests/unit/test_retrieval_eval_v6.py | 358 金标门：p@1≥0.53 / r@5≥0.72 / r@10≥0.78 / invalid≤0.30 / oos 弃权≥0.15 / 误弃权≤0.05 / ECE≤0.40 / kill-switch 基线 | ✅ |
| tests/unit/test_semantic_retrieval_v6.py | 扩展词/别名/方法论/否定/置信度/kill-switch 10 项 | ✅ |
| tests/unit/test_recovery_ledger_v6.py | durable 账本 11 项（写穿/回写/TTL/有界/续接/双进程/通道选择） | ✅ |
| tests/unit/gis_harness/test_trace_store_v6.py | 分段/压缩/增量读/汇聚/torn-tail 6 项 | ✅ |
| tests/unit/gis_harness/test_trace_store_v5.py | V5 契约对 V6 实现（多进程零丢行/幂等/trim/V4 兼容/pinned）8 项 | ✅ |
| tests/unit/gis_harness/test_durable_context_continuation_v6.py | 三分层/recovery_state/裁决全分支 14 项 | ✅ |
| tests/unit/gis_harness/test_observation_states_v6.py | 状态阶梯/聚合/health/map_product 键 7 项 | ✅ |
| tests/unit/gis_harness/test_chaos_invariants_v6.py | kill -9 锁释放/隔离/续接/corpus 元门 6 项 | ✅ |
| tests/unit/test_subagent_budget_class_v6.py | 档位词表/交集/token 闸 7 项 | ✅ |
| tests/perf/test_semantic_retrieval_perf_v6.py | 词表有界/全量评测 ≤60s/单次 select ≤2s | ✅（-m perf） |

## 相邻回归（全绿）

- gis_harness 全量：1013 passed
- tool_retrieval v4/v4_corpus/gis_trace_v3/pi_native_surface/pi_dynamic_surface/tool_surface v2/v3：56 passed
- recovery/taxonomy/repair/dispatch 相关：237 passed
- subagent 全族：72 passed
- descriptor/registry/retrieval 相关：456 passed

## 关键验收对照（Epic 目标）

1. `NL query → semantic retrieval → workflow evidence → tool dispatch → failure → remediation → resume/continue → rendered-state verified → final verdict`：
   hybrid 检索（W2-W4）→ TOOL_SURFACE 链事件 → dispatch seam typed failure
   （V5）→ durable ledger 记账 + 成功回写（W6）→ continuation 裁决（W8）→
   resume anchor + recovery_state 续接（W5）→ observation ladder +
   map_product.observation_health（W10）→ finalizer intent↔rendered（V5）。
2. 检索指标实质改善：p@1 +6.4pp / r@5 +7.9pp（同语料对照，钉线防回归）。
3. 低置信度不乱派发：弃权 → 不注入动态面 + 披露；oos 弃权 21.4%/误弃权 0%。
4. 跨进程/重启：ledger write-through 双进程测试；trace manifest；resume 续接。
5. 最终成功以真实 observation 支持：状态阶梯 + 恒发射 health（缺席=blocked）。
6. 断连/取消无锁泄漏/悬挂：kill -9 锁释放 + disconnect 风暴既有测试入 corpus。
