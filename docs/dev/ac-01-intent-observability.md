# AC-01 意图解析可观测面（P6 / ADR-0150）

## 1. 证据包 `intent_evidence`

每次意图解析（`resolve_map_request_intent` / `resolve_intent_adaptive`）
在 `MapRequestIntent.intent_evidence` 外泄：

| 字段 | 说明 |
|---|---|
| `lang` | zh / en（`detect_language`，CJK vs latin 计数） |
| `slots` | `IntentSlots` 序列化（槽位 + 每槽 source/confidence） |
| `llm_used` / `llm_requested` | LLM 轨道是否实际参与 / 是否被请求 |
| `degraded_reason` | 降级原因（空 = 无降级） |
| `confidence_components` | 四证据分量（task_evidence / slot_completeness / entity_quality / session_consistency） |
| `confidence_model` | `evidence_weighted_v1`（校准锚点版本锚定） |
| `scope_source` | `fast_path_city` / `regex_city_suffix` / `regex_district` / `regex_province_suffix` / `fast_path_province` / `fast_path_en_*` |
| `task_candidates` | 特异性裁决的候选集 (rule, task, specificity, span)——澄清选项与审计都用它 |
| `ontology_escalation` | 本体任务升级 {from, to}（无则 null） |
| `slot_conflicts` | 规则 vs LLM 槽位冲突清单（adaptive 路径） |
| `clarification` | 澄清请求全文（有则序列化） |

另散布在 intent 顶层字段：`matched_rules`（含 fallback 头标记语义）、
`ontology_link`、`degraded_reason`、`fallback_decision`、`clarification`。

## 2. 计数器（Prometheus，bounded labels）

| 计数器 | labels | 语义 |
|---|---|---|
| `gis_intent_resolve_total` | `lang ∈ {zh,en,other}` × `outcome ∈ {rule, rule_fallback, semantic, semantic_fallback}` | 解析量与命中来源分布；`*_fallback` 占比即 fallback 触发率 |
| `gis_intent_clarification_total` | `slot ∈ {task, subject, area}` | 澄清问题数按槽位（触发率 = 与 resolve_total 之比） |
| `gis_intent_degraded_total` | reason 词表（9 值，见下） | 降级率按原因 |

reason 词表（封闭，防高基数）：`llm_unavailable / llm_error /
llm_output_invalid / llm_not_requested / entity_service_unavailable /
entity_service_disabled / entity_service_error / entity_unresolved /
task_rule_miss`。

实现：`intent_semantic.py` 的 `_counter()` 工厂 + fire-and-forget
`record_*` 封装（prometheus_client 缺失或注册失败不阻塞业务）；
label 词表越界值折叠到封闭值（`other` / `rule` / `llm_error`）。

## 3. 澄清状态键

- SessionStore map_state 键：`intent_clarification_state`
- 值：`{"asked_slots": ["subject", ...]}`（单调 seq 由 store 保证）
- 语义：已澄清槽位二次请求不再追问（`ClarificationPolicy.evaluate`
  的 `asked_slots` 入参；异步适配 `load_asked_slots` / `save_asked_slots`）。

## 4. 本地验证方式

```bash
# 语料回归 + 澄清/降级指标断言（零 LLM、零网络）
python -m pytest tests/cartography/test_intent_adaptive.py -q --no-cov

# 规则矩阵（rule → 命中样本 → 冲突）确定性重生成
python tests/cartography/dump_rule_matrix.py \
    --corpus tests/cartography/fixtures/intent_corpus.jsonl \
    --out docs/dev/ac-01-rule-matrix.csv
```

运行期计数器以 `/metrics` 暴露（prometheus_client 默认 registry）。
