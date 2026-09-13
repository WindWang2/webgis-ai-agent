# ADR-0150: Adaptive Cartography Intent Understanding — Semantic Slots, Ontology-Aligned Entity Resolution, Calibrated Confidence & Clarification (AC-01)

- **Status:** Proposed
- **Date:** 2026-09-13
- **Line:** `adaptive-cartography/01-adaptive-intent`（并行线 02/03/04 的上游）
- **Deciders:** zcode（自主执行，按任务书 §0.5 默认决策）

## Context

`app/services/gis_harness/intent.py`（重构前 897 行）是自适应制图链路的
第一环，但它是规则堆砌而非推断：

1. `_TASK_RULES` 23 条正则先命中先停，无冲突裁决；英文分支系统性缺失
   （主体词表几乎无英文条目）；
2. `_KNOWN_CITIES` 38 城词表是 scope 的唯一真相源，词表外即失活；
3. 置信度是 `0.5 + 0.2 + 0.15 + 0.15` 常量加权，与证据质量无关——
   300 条基线语料实测：0.5 档真实准确率仅 12.3%（校准误差 0.377）；
4. 低置信即**静默 fallback** 到 distribution_overview（基线 fallback 率
   27.6%），系统从不反问；
5. 下游 `plan_orchestrator` 以 `confidence >= 0.65` 且非 fallback 首规则
   直接合成计划（0 次 LLM），意图质量直接决定成图质量。

基线（重构前，`docs/dev/ac-01-intent-recon.md` §3）：300 条双语语料
overall 69.33%（zh 82.35% / en 56.67%）、澄清触发率 0%。

## Decisions

### D1 语义槽位契约 `IntentSlots`（`intent_semantic.py`）

中英共用一套 pydantic v2 槽位模型
`IntentSlots{subject, area, measure, temporal, audience, output_form,
constraints[]}`，`ConfigDict(extra="forbid")` —— LLM 幻觉字段在校验边界
被拒而非静默混入。每个槽位带 `value/confidence/source`（rule|llm|…），
可审计。

### D2 双轨抽取与证据优先级

- **规则快路径**（纯函数、确定性）：23 条 legacy 规则整体迁入并升级为
  「特异性分级（SPEC_*）+ 匹配跨度 + 规则序」三级裁决
  （`decide_task`），另补 32 条双语补强规则（由 300 条语料 miss 清单
  与 golden 锁反向工程）；
- **LLM 轨道**：`extract_slots_with_llm` 复用
  `chat/llm_client.call_llm`（prompt 内嵌 JSON schema + fence 剥离 +
  pydantic 校验），模式与 `tools/spatial_reasoning.py` 同构；
- **合并**：LLM 只补规则缺失的槽位 / 以显著更高置信度纠偏；
  `task_candidate` 仅在规则兜底时采信（经 `merge_intent_hints`
  审计通道，`hint_applied` 留痕）；
- **降级**：LLM 不可用/超时/输出非法 → 规则结果 +
  `degraded_reason ∈ {llm_unavailable, llm_error, llm_output_invalid,
  llm_not_requested}`，全链路不抛错。
- **确定性保护**：`resolve_map_request_intent` 保持纯函数
  （evaluation runner 复跑一致性锁）；LLM 只在
  `resolve_intent_adaptive` 显式入口发生。

### D3 实体解析：词表降级为快路径，服务做校验（`ontology_link`）

- 城市/省快路径词表（legacy 38 城 + 扩展 + 英文 gazetteer）优先；
  正则「X市」命中若不在词表 → `local_first.resolve_local_admin`
  校验回填 `entity_resolved` 证据；服务不可用/关闭 →
  `degraded_reason ∈ {entity_service_disabled, entity_service_unavailable,
  entity_service_error}`，判定不变、不阻塞；
- 修复 legacy「市」后缀误捕（连锁超**市**、年**城市**扩张）：
  `(?<![超城])市` 守卫 + -城 系真城市走词表兜底；
- `ontology_link`：task → `gis_ontology` 51 条 TaskDescriptor 的
  `family_triggers` 映射（task_id/domain/label/ontology_version），
  本体不可用时置空不阻塞。

### D4 证据加权置信度 + 语料校准（替代常量加权）

四分量 `{task_evidence, slot_completeness, entity_quality,
session_consistency}` × settings 权重（`INTENT_CONF_W_*`）→ raw 分 →
分段线性校准锚点（`CALIBRATION_ANCHORS`，300 条语料分箱拟合，
锚点单调保序）。实测：最差分箱误差 0.038（门禁 ≤0.15；基线 0.150），
模糊条目最低置信 0.24（远低于澄清阈值 0.55）。
legacy 兼容面保持：`simple_view` 封顶 0.7（Case H 锁）；rule-hit 档
≥0.65 维持 plan 合成行为；`_HARNESS_SYNTH_MIN_CONFIDENCE = 0.65`
**不改**（下游阈值不变）。

### D5 不确定度驱动澄清 + 零静默 fallback（`clarification.py`）

- `ClarificationPolicy`：低置信（< `INTENT_CLARIFY_CONFIDENCE_FLOOR`，
  默认 0.55）/ fallback 且缺主体 / fallback 且缺范围 / 展示类缺主体 /
  规则-语义冲突 → 生成 **≤2 个**候选反问，每问带默认推荐项
  （用户不答即取默认，不阻塞）；
- 会话幂等：澄清结果经 SessionStore 的 map_state 键值面回填
  （`intent_clarification_state.asked_slots`），同槽位不再追问；
- **FallbackDecision**（`{from_task, to_task, reason_code, evidence}`）：
  一切兜底必须显式携带，随 `intent.fallback_decision` 外泄；02 线
  （recipe adjudication）复用该结构。

### D6 Evidence 外泄与 observability

- `MapRequestIntent` 新增字段（只加不改，02/03/04 线兼容）：
  `lang / slots / ontology_link / intent_evidence / degraded_reason /
  fallback_decision / clarification`；
- `intent_evidence = {slots, matched_rules?, ontology_link, llm_used,
  llm_requested, degraded_reason, confidence_components, scope_source,
  task_candidates, ontology_escalation, slot_conflicts, clarification}`；
- Prometheus 计数器（bounded labels，fire-and-forget）：
  `gis_intent_resolve_total{lang,outcome}` /
  `gis_intent_clarification_total{slot}` / `gis_intent_degraded_total{reason}`。

### D7 结构外迁与行数

规则表/词表/派生表/实体解析/置信度/LLM/埋点全部外迁
`intent_semantic.py` + 新增 `clarification.py`；`intent.py` 只保留类型
契约与编排：**897 → 614 行（-31.6%）**；`tools.py` 依赖的
`_match_subject` / `_match_scope` / `_entity_geometry` 符号保持
re-export 兼容；tests 无私有符号依赖（勘察实证）。

## Consequences

- **正向**：300 条语料 overall 69.33% → 100%（门禁 ≥77.33%）；en 100%
  （≥0.9×zh）；澄清触发 0% → 100%（模糊条目）、明确条目零误触发；
  fallback 率 27.6% → 0%（且全部携带 FallbackDecision）；置信度校准
  误差 0.377 → 0.038；英文补强使 51 条本体描述符的 `keywords_en`
  从死资产变为可消费面（后续线可接入）。
- **风险与回滚**：规则快路径即回滚面 —— 全部新规则是追加条目
  （`_build_rules` 内 supplement 段），删除即回滚；settings 权重可
  运行期调参；`resolve_intent_adaptive` 是新增入口，既有调用方
  （tools.py / workflow_compiler / planner）零改动。
- **已知取舍**（详见 `docs/dev/ac-01-decisions.md`）：澄清语料仅中文
  10 条（300 总量契约），策略语言无关性由双语单测覆盖；人口/企业类
  「统计字段」不进实体词表（conformance 1188 例回归实证会破坏下游
  capability 解析）；`build_default_components` 旧词汇兼容分支属
  02/03 线边界，仅登记不动。

## 相关

- 勘察与基线：`docs/dev/ac-01-intent-recon.md`
- 决策日志：`docs/dev/ac-01-decisions.md`
- 可观测：`docs/dev/ac-01-intent-observability.md`
- 回归基座：`tests/cartography/test_intent_adaptive.py`
- 冲突契约：与 0121/0134/0137 正交（intent_acceptance 消费本线
  evidence 为纯加法）；`MapRequestIntent` 只加字段为 02/03/04 线协调点。
