# TEST_MATRIX — 测试矩阵（TDD：先锁契约再实现）

## 新测试文件

| 文件 | 覆盖 |
|---|---|
| `tests/data/test_semantic_checks_v1.py` | 4 检测器：timezone_missing（naive/mixed/aware/非时间字段零误报）、unit_ambiguous（欠定 vs INCONSISTENT_UNIT 矛盾分流）、field_role_ambiguous（等证据竞争/样本反证/低置信未绑定披露）、admin_mismatch（变体名→最近码建议；正常码零误报）；全部确定性（同输入同 issues）+ 有界（样本帽） |
| `tests/data/test_data_quality_profile_v1.py` | 统一画像：聚合各源、确定性 digest（两遍一致）、gate 裁决（ready/degraded/blocked/unknown）、checks_run/not_run 诚实、bounded dict、词表完整性（新码都在 _REMEDIATIONS + _REPAIR_MAP） |
| `tests/unit/gis_harness/test_qualification_semantic_guard.py` | 低置信闸：metadata_derived 绑定 measure → degraded 非 eligible；无语义画像 → 行为与现状逐字节一致（feature-off）；unknown ≠ unsatisfied 保持；denominator 低置信 → 不下人均结论 |
| `tests/data/test_repair_transaction_v1.py` | RepairSession 状态机：proposed→dry_run→applied→verified；apply 失败 → failed 且源 payload bytes 不变、无部分 ref；rollback → 回指 source 的新 ref + 血缘事件 + 原 artifact 不变；确定性 session 键；幂等（同 plan 重复 apply 不产生第二 ref 链分叉） |
| `tests/unit/gis_harness/test_qualification_v8_quality.py` | V8 additive：context 无质量字段 → 输出与现状一致；blocking_issue_codes → degraded + 结构化 reason；quality_status=degraded（无 blocking）→ degraded reason 不隐藏既有 reason |
| `tests/data/test_dirty_corpus_oracle.py` | E2E Oracle（完成判据）：dirty fixture corpus → 稳定 profile（digest 两遍一致）/repair proposal/fingerprint/lineage；低置信字段不静默绑定；修复失败不破坏原 artifact；planner 因质量 block/degrade；guardrail/DataFabric 边界（guardrails 层级校验不被本方向改动所影响；fabric facts 不被画像改写） |
| `tests/perf/test_quality_profile_scale.py` | 100k 行生成式：profile 路径只采样不物化（耗时/内存记录；markers=perf） |

## 必跑回归（相邻面）

- `tests/data/test_quality_rules.py`（词表对账锁 —— 新码不破坏 RULE_TYPES 对账）
- `tests/unit/gis_harness/test_data_qualification.py`（五态回归锁）
- `tests/data/test_repair_plan_v4.py`、`tests/unit/test_semantic_gis_intelligence.py`（semantic_profile 不变量）
- `tests/unit/test_quality_gate_lifecycle.py`、spatial guardrails 相邻测试
- ruff（`ruff check app/services/data_quality app/lib/data app/services/gis_harness tests/data tests/unit/gis_harness`）

## 纪律

- pytest -n ≤2；heavyweight full suite 一次最多一个；perf 测试默认不混入 targeted 绿灯判定（marker 隔离）。
- 关键 Oracle 验证连跑两遍；不删测试/不降断言/不扩 ignore。
