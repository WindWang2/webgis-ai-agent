# app/evaluation 专属测试线（G08）

`app/evaluation` 是 GIS agent 语义回归基准（ADR-0092 Phase B 起，V2–V6
逐波扩展）：语料即评测基准，**改语料 = 改基准**。在 G08 之前，它的消费
分散在 30+ 个测试文件与 scripts 中，没有专属目录、没有消费链图谱。
本目录补齐这一层：**每个语料入口都有真实的专属消费测试**，断言方向是
语料 schema 不漂移、门禁规则对正反例判定正确、被消费的路径真实存在。

## 运行

```bash
pytest tests/unit/evaluation --no-cov -q     # 全绿；离线、零 LLM、假值 env（tests/conftest.py 基线）
```

## 消费链图谱（rg 实证）

证据命令（基线 `de3f97c5`，2026-09-27 实测）：

```bash
rg -l 'from app\.evaluation|import app\.evaluation' app tests scripts
# 逐文件消费者：
rg -l "app\.evaluation\.<mod>\b" app tests scripts --glob '!app/evaluation/**'
```

### 模块 → 外部消费者（app/evaluation 之外，rg `-l` 实证）

| 模块 | 消费者 |
| --- | --- |
| anti_claim | tests/unit/gis_harness/test_conformance_corpus.py |
| cartography_axes_corpus | scripts/gis_bench_v2.py；tests/quality/test_v2_cartography_axes_corpus.py |
| case_matrix | tests/unit/test_tool_retrieval_v4.py |
| case | tests/quality/{test_quality_scenario_corpus,test_system_scenarios,test_v2_case_manifest}.py；tests/unit/gis_harness/test_v2_runner_tiers.py |
| chain_gate | tests/unit/gis_harness/test_chain_completeness_v4.py |
| chaos_corpus | tests/unit/gis_harness/test_chaos_invariants_v6.py |
| closed_loop_corpus | **app/lib/harness/scale_matrix.py**；tests/cartography/test_intent_adaptive.py；tests/unit/test_closed_loop_corpus_v6.py |
| conformance | tests/quality/test_quality_scenario_corpus.py；tests/unit/gis_harness/{test_conformance_corpus,test_runtime_corpus_v4}.py |
| evidence_corpus | scripts/gis_bench_v2.py；tests/quality/test_v2_evidence_corpus.py |
| failure_corpus | tests/unit/gis_harness/test_recovery_scenario_v5.py |
| fixtures | tests/unit/gis_harness/test_kriging_vertical_slice.py；tests/unit/test_od_flow_runtime.py |
| goal_satisfaction_corpus | tests/unit/gis_harness/test_goal_satisfaction_corpus.py |
| golden_cases | tests/unit/gis_harness/test_runtime_corpus_v4.py；tests/unit/{test_runtime_metrics,test_tool_retrieval_v4}.py |
| hard_negative_corpus | scripts/gis_bench_v2.py；tests/quality/{test_v2_case_manifest,test_v2_hard_negative_corpus}.py |
| index | scripts/gis_bench_v2.py；tests/quality/test_v2_case_manifest.py |
| methodology_corpus | **app/services/gis_harness/workflow_v4/evaluation.py**；tests/unit/gis_harness/test_methodology_corpus_v4.py |
| mission_corpus | scripts/gis_bench_v2.py；tests/quality/{test_v2_case_manifest,test_v2_mission_corpus}.py |
| mission_driver | scripts/gis_bench_v2.py；tests/quality/test_v2_mission_corpus.py |
| quality_corpus | **app/lib/harness/scale_matrix.py**；tests/quality/test_quality_scenario_corpus.py |
| reliability_corpus | tests/unit/test_reliability_security_perf_v2.py |
| replay | tests/unit/gis_harness/{test_chain_completeness_v4,test_runtime_corpus_v4}.py；tests/unit/{test_gis_trace_v3,test_reliability_security_perf_v2,test_trace_replay_v2}.py |
| report | scripts/gis_bench_v2.py；tests/quality/test_v2_report_diff.py |
| retrieval_corpus | tests/unit/test_tool_retrieval_v4_corpus.py |
| retrieval_eval_corpus | tests/perf/test_semantic_retrieval_perf_v6.py；tests/unit/test_retrieval_eval_v6.py |
| runner | scripts/gis_bench_v2.py；tests/quality/ 7 文件；tests/unit/gis_harness/ 4 文件（runner 是被消费最广的执行入口） |
| runtime_corpus | tests/unit/gis_harness/test_runtime_corpus_v4.py |
| runtime_metrics | tests/unit/{test_runtime_metrics,test_tool_retrieval_v4_corpus}.py |
| scenario_corpus | tests/unit/gis_harness/test_capability_descriptors_v7.py |
| scenarios | tests/unit/gis_harness/test_conformance_corpus.py |
| security_corpus | scripts/gis_bench_v2.py；tests/quality/test_v2_security_corpus.py |
| skill_policy_corpus | scripts/gis_bench_v2.py；tests/quality/{test_v2_case_manifest,test_v2_skill_policy_corpus}.py |

三条**生产侧消费边**（语料不是 dead data，被 app 运行时直接吃）：
`closed_loop_corpus`/`quality_corpus` ← `app/lib/harness/scale_matrix.py`；
`methodology_corpus` ← `app/services/gis_harness/workflow_v4/evaluation.py`。

### 间接消费面

- `app/evaluation/__init__.py` re-export 8 个名字
  （`GISBenchmarkCase`/`NumericAssertion`/`ScriptStep`/`GOLDEN_CASES`/
  `get_all_cases`/`GISBenchmarkRunner`/`CaseResult`/`render_markdown`），
  源模块为 case / golden_cases / runner / report；
- `index.py` 内置注册 **18 个语料**（`corpus_manifest()` 可枚举），
  scripts/gis_bench_v2.py 据此生成语料清单与版本哈希。

## 死语料清单（只登记，不删除 —— 删除是破坏性操作）

**模块级：无死语料。** 31/31 个非 `__init__` 文件都有外部消费者（上表）。

**符号级（名字在定义模块之外零引用；rg `-w` 实证）：**

1. 本测试线**已激活**（此前仅被同模块内部间接使用）：
   `chain_gate.evaluate_chain_gate`、`chain_gate.load_session_chains`、
   `chaos_corpus.corpus_node_ids`、`case_matrix.get_expected_total`、
   `report.load_baseline`、`mission_driver.hermetic_mission_runtime`。
2. **类型再导出**（作为活函数的返回类型被构造消费，名字本身无外部引用，
   维护者无需行动）：`ChaosScenario`、`FailureCase`、`FailureVerdict`、
   `GoalSatisfactionCase`、`GoalCaseResult`、`CorpusRegistration`、
   `DataStateProfile`、`QualityFamily`、`ReliabilityCase`、
   `ToolReplayOutcome`、`SurfaceABResult`、`ChainComparison`、
   `ToolRetrievalCase`、`RetrievalEvalCase`、`RetrievalEvalReport`、
   `RuntimeCase`、`ScenarioTurn`、`CompositeScenario`、
   `RuntimeExecutionCase`、`RetrievalMetrics`、`SurfaceRetrievalReport`、
   `RoutingMetrics`、`ExecutionMetrics`、`GisCorrectnessMetrics`、
   `ContextMetrics`、`ScenarioPack`、`ScenarioCase`、`GISScenario`、
   `CartographyAxesCase`。
3. **真·未引用的 fixture 数据：无。** `fixtures.render_observation_clean`
   / `render_observation_overlap` / `render_observation_offscreen` 曾被
   名字级扫描误判为死符号——实际经
   `cartography_axes_corpus.py` 的
   `fixtures.get(f"render_observation_{...}")` **动态调度消费**（layout
   轴案例的 `observation_fixture` 字段）。教训：名字级 rg 扫描看不见
   getattr 动态调度，死语料判定必须辅以数据流核对。

## 测试文件 → 覆盖模块

| 测试文件 | 消费的 app/evaluation 模块 |
| --- | --- |
| test_consumption_contract.py | 全部 32 模块（导入活性）＋ `__init__` re-export ＋ index |
| test_corpus_schema_integrity.py | golden_cases、case_matrix、conformance、quality_corpus、runtime_corpus、scenario_corpus、methodology_corpus、retrieval_corpus、retrieval_eval_corpus |
| test_benchmark_factory_corpora.py | anti_claim、security_corpus、skill_policy_corpus、hard_negative_corpus、evidence_corpus、chaos_corpus、closed_loop_corpus、cartography_axes_corpus、mission_corpus |
| test_gate_chain_verdicts.py | chain_gate（正反例矩阵） |
| test_anti_claim_gates.py | anti_claim（门禁正反例 + tampered 非空洞证明） |
| test_failure_corpus_replay.py | failure_corpus（八类故障可重放 + 预算阶梯收敛） |
| test_runner_report_fixtures.py | case（schema 正负例）、fixtures、runner、report |
| test_replay_and_metrics.py | replay、runtime_metrics、reliability_corpus |
| test_goal_mission_scenarios.py | goal_satisfaction_corpus、mission_corpus、mission_driver、scenarios |

覆盖率证据（31/31 非 `__init__` 文件 ≥1 测试消费）：见对应 PR body 的
`pytest tests/unit/evaluation --cov=app/evaluation --cov-report=term` 输出。
