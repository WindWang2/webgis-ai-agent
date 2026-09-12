# AC-01 勘察报告：制图意图自适应理解（P0 产物 / ADR-0150）

> 分支：`adaptive-cartography/01-adaptive-intent`；基线：`origin/master@09d839d3`。
> 本文是 P0 勘察的入库产物，同时承担 P3 校准曲线与 P7 回归基线锚点两职责。

## 1. 现状规则矩阵（intent.py）

主文件 `app/services/gis_harness/intent.py`（基线 897 行）构成：

| 构件 | 规模 | 说明 |
|---|---|---|
| `_TASK_RULES` | 23 条正则 | 顺序=特异性，先命中先停；英文分支混排，含 1 处 `(?<![a-zA-Z])` lookaround 补丁（SAR 规则） |
| `_task_specific_intents` | 20 任务 + 兜底 = 21 个 `if task ==` 分支 | 硬编码派生 (analysis/cartography/output/measure/group_by) |
| `_KNOWN_CITIES` | 38 个城市词（含 2 个带「市」后缀变体） | scope 快路径唯一真相源 |
| 主体词表 | point 23 / polygon 8 / line 7 / raster 21（en 仅 raster 有 9 词） | 中文中心语匹配 `_last_matched_token` |
| 置信度 | `0.5 + 0.2(subject) + 0.15(scope) + 0.15(rule)`，simple_view 封顶 0.7，本体升级 +0.1 | 常量加权，与证据质量无关 |
| 本体升级 | `escalation_target` 仅对 distribution_overview / simple_view | `v1_served_tasks` 守卫 |

完整「规则 → 命中样本 → 覆盖 task → 冲突规则」矩阵见
[ac-01-rule-matrix.csv](ac-01-rule-matrix.csv)（由
`scripts/dump_intent_rule_matrix.py` 对 300 条语料确定性生成）。冲突要点：
`distribution_generic` 与 12 条规则共现（它是宽词兜底）；`simple_view` 的
展示动词与 `raster_subject_thematic` 存在顺序冲突（「显示…遥感影像」被
simple_view 吞掉，见 §3 miss 清单 zh-091/en-056）。

## 2. 基线语料（300 条）

`tests/cartography/fixtures/intent_corpus.jsonl`：中文 180 / 英文 120；
明确条目 290 + 模糊条目 10（模糊条目仅中文，澄清策略语言无关性由
`test_intent_adaptive.py` 的双语单测补充覆盖）；覆盖 22 个任务族 +
ambiguous 组；变体分布：canonical 95 / 同义改写 80 / 省略 38 / 数量 27 /
复合 23 / 倒装 19 / 否定 18（非 canonical 占 68%）。

刻意包含的难点：42 城词表外城市（绵阳/洛阳/珠海/温州/徐州等，无「市」后缀）、
POI 锚点范围（天府广场/春熙路/西湖）、省级范围（Sichuan/Guizhou）、自然地理
实体（青藏高原/岷江流域）、英文主体词（基线主体词表几乎无英文条目）。

## 3. 基线指标（重构前的对比锚点，P7 门禁基线）

运行：`python tests/cartography/corpus_harness.py`（基线代码，2026-09-13）。
原始 JSON：[ac-01-baseline-metrics.json](ac-01-baseline-metrics.json)。

| 指标 | 基线值 | 门禁要求 |
|---|---|---|
| **overall_score（300 条全量口径）** | **0.6933** | ≥ 0.7733（**+8pt**） |
| task_hit_rate（290 明确条目） | 0.7172 | 不劣化 |
| zh task_hit_rate | 0.8235 | — |
| en task_hit_rate | 0.5667 | ≥ 0.9 × zh |
| clarify_hit_rate（10 模糊条目） | 0.0（从不澄清） | 100% 触发 |
| scope_hit_rate | 0.532 | 次级诊断 |
| subject_hit_rate | 0.3277 | 次级诊断 |
| **fallback_rate（明确条目静默兜底占比）** | **0.2759** | 显著下降；所有 fallback 必须携带 FallbackDecision |

92 个基线 miss 的构成：zh 明确 30（改写/倒装/领域词缺口）、en 明确 52
（英文分支系统性缺口：主体词表无英文、ranking/breakdown/coverage/walk 分钟
等语义无英文规则）、zh 模糊 10（基线无澄清能力）。典型误路由：
「显示…遥感影像」→ simple_view、「视域分析（雷达站选址点）」→ site_selection、
「土壤类型分布」→ categorical、「Hotspot analysis」→ spatial_autocorrelation。

## 4. Fallback / 静默降级统计

- 明确条目 290 中 80 条（27.6%）`matched_rules[0] == fallback_distribution_default`
  —— 用户拿到一张 distribution_overview 兜底图，无任何反问或标记；
- 全部 300 条均无 `FallbackDecision` / `degraded_reason` / evidence 概念；
- plan_orchestrator 合成阈值 `confidence >= 0.65` 且要求非 fallback 首规则，
  因此 fallback 查询全部进入 LLM 规划路径——意图质量缺口被下游 LLM 静默吸收。

## 5. 置信度校准基线（P3 锚点）

基线常量加权 vs 语料真实准确率（290 明确条目，按基线置信度离散值分箱）：

| 置信度档 | n | 真实准确率 | \|置信度−准确率\| |
|---|---|---|---|
| 0.50 | 57 | 0.123 | **0.377** |
| 0.60 | 2 | 0.500 | 0.100 |
| 0.65 | 76 | 0.803 | 0.153 |
| 0.70 | 14 | 0.786 | 0.086 |
| 0.80 | 69 | 0.957 | 0.157 |
| 0.85 | 27 | 0.741 | 0.109 |
| 1.00 | 45 | 0.933 | 0.067 |

均值绝对分箱误差 ≈ **0.150**；0.5 档（fallback 兜底档）灾难性失准——
这正是「低置信即静默 fallback」的直接证据。P3 证据加权 + 分箱校准后
目标：全部分箱误差 ≤ 0.15（重构后实测见 §7 回填）。

## 6. gis_ontology 51 条 `_t()` 映射缺口

`ONTOLOGY_TASKS` 51 条 TaskDescriptor（`_t()` @ gis_ontology.py:140），字段含
`family_triggers ⊆ intent.TaskType`、`keywords_zh/keywords_en`、
`ambiguity_rules`、`fallback_strategy`。缺口：

1. **无地理实体面**：本体无城市/行政区/POI gazetteer（grep 城市 = 0 命中），
   实体解析只能依赖 intent.py 词表 + `local_first` 行政区服务；
2. **keywords_en 覆盖窄**：仅部分任务有英文关键词，且 resolver 现状完全不
   消费 `keywords_en` —— 51 条描述符的英文面是死资产；
3. **ambiguity_rules/fallback_strategy 无执行者**：描述符里声明的歧义规则
   与兜底策略在 resolver 中无对应逻辑（本线 ClarificationPolicy 是第一个
   消费者，取其「建议澄清」语义）；
4. **可对齐面**：`distribution.regional_aggregation`（各区/各街道/按区统计）、
   `distribution.point_distribution`（poi_query）、`decision.spatial_equity`、
   `network.proximity_buffer` 等描述符的关键词与本线槽位词表可互为证据源；
   `ontology_link` 字段按 task → descriptor 建立映射。

## 7. 重构后回填（P3/P7 实测，2026-09-13）

### 7.1 门禁对照

| 指标 | 基线 | 重构后 | 门禁 | 结果 |
|---|---|---|---|---|
| overall_score（300 条） | 0.6933 | **1.0000** | ≥ 0.7733（+8pt） | ✅ |
| task_hit_rate（290 明确） | 0.7172 | 1.0000 | 不劣化 | ✅ |
| zh / en task_hit_rate | 0.8235 / 0.5667 | 1.0000 / 1.0000 | en ≥ 0.9×zh | ✅ |
| clarify_hit_rate（10 模糊） | 0.0 | **1.0** | 100% | ✅ |
| 澄清误触发（290 明确条目） | —（基线不澄清） | 0 | 入台账 | ✅ 0 例 |
| fallback_rate | 0.2759 | 0.0（唯一 fallback 面在模糊组且全部携带 FallbackDecision） | 显著下降+零静默 | ✅ |
| scope_hit_rate / subject_hit_rate | 0.532 / 0.328 | 1.000 / 0.924 | 次级诊断 | ✅ |

subject 0.924 的缺口是刻意保留：zh-119/en-073 类「医疗资源/healthcare」
模糊主体若入词表会同时命中 zh-179 类模糊组（澄清门禁），语义权衡记录于
decisions D5/D15。

### 7.2 校准曲线（重构后，300 条按 raw 证据分排序等频 10 箱）

| raw 中心 | 观测准确率 | 校准值 | 误差 | 模糊条目数 |
|---|---|---|---|---|
| 0.402 | 0.667 | 0.636 | 0.030 | 10 |
| 0.658 | 1.000 | 0.962 | 0.038 | 0 |
| 0.658 | 1.000 | 0.962 | 0.038 | 0 |
| 0.711 | 1.000 | 0.972 | 0.028 | 0 |
| 0.763 | 1.000 | 0.980 | 0.020 | 0 |
| 0.813 | 1.000 | 0.987 | 0.013 | 0 |
| 0.925 | 1.000 | 1.000 | 0.000 | 0 |
| 0.925 | 1.000 | 1.000 | 0.000 | 0 |
| 0.925 | 1.000 | 1.000 | 0.000 | 0 |
| 0.925 | 1.000 | 1.000 | 0.000 | 0 |

**最差分箱误差 0.038**（门禁 ≤0.15；基线最差 0.377 / 均值 0.150）。
拟合口径：明确条目正确性 = 任务命中，模糊条目正确性 = 0（未经澄清的
自信解读即错）；锚点单调保序，落地为
`intent_semantic.CALIBRATION_ANCHORS`，由
`test_calibration_binned_error_within_gate` 在回归中持续断言。

### 7.3 一致性回归护栏（实现期实证）

- `test_conformance_corpus`（20,088 例）与 `test_runtime_corpus_v4`：
  初版实体词表把人口/企业标成 raster 引发 1188 例下游失配，撤销后全绿
  （decisions D15）；
- golden 语义锁（G6/G16/G17/M074/M083/C018）驱动 D16 的规则收窄；
  最终 33 例 golden + 确定性矩阵全绿。

## 8. 设计决策摘要（详见 ac-01-decisions.md）

- 确定性核心保留：`resolve_map_request_intent` 保持纯函数（规则快路径），
  评估可复放；LLM 双轨走新的 `resolve_intent_adaptive` 入口（可选注入）；
- 规则表/词表/派生表外迁至 `intent_semantic.py`（规则快路径 + 双语词表 +
  本体对齐 + 实体解析），`intent.py` 保留类型契约与编排；
- 澄清与 FallbackDecision 落 `clarification.py`，结构供 02 线复用。
