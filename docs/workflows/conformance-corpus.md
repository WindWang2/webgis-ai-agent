# 一致性语料库方法论（Conformance Corpus Methodology）

## 定位

`app/evaluation/conformance.py` + `app/evaluation/anti_claim.py` +
`app/evaluation/scenarios.py` 构成 Goal C 的离线一致性面：

| 层 | 规模 | 判定内容 |
| --- | --- | --- |
| Conformance corpus | **20,088** plan-tier 案例（59 族 × 双语 × 12 scope × 9 句式） | task / recipe / 核心能力 / 警告码的语义身份不变量；V3 追加本体任务匹配 / 数据资格 / 回退层 / 规划确定性（opt-in 契约） |
| Anti-claim plan cases | 7 | 反声明：无分母/无准则/无受体/噪声不得出现 |
| Workflow contract cases | 11 | 义务状态 / 角色绑定 / 语义降级 / 阻断 / verdict V2 |
| Scenario benchmarks | 7 场景（plan+execute+contract 复合） | 代表性端到端走查 |

与既有 306 案例（golden + matrix，ADR-0092）互补：那里锁定通用产品族，
本库把覆盖推到专业工作流与多语言/多 scope/多句式表述。

## 生成哲学：「人工审定期望 × 确定性输入扩展」

**期望表（family）是人工审定的工件**，输入扩展只改变表述、不改变语义
身份：

- `ConformanceFamily`（59 个 = V2 基线 47 + V3 扩容 12，按 24 个领域包
  全覆盖）：每族声明 expected_task / expected_recipe / expected_capabilities /
  expected_warning_codes（+ 显式声明的 alternative_tasks/recipes）；
  V3 追加 `expected_ontology_task`（本体 top-1 匹配锁，仅当匹配无歧义
  时声明）；
- 扩展维度：语言（zh/en）× scope（12 个：无/市×7/区/省/省+市/双城）×
  句式（9 个：直接/口语展示/疑问/报告/请分析/的情况/我想了解/如何/帮忙
  做一下）—— 全部包装词逐一核实不携带任务语义；
- id 确定性：`CF-<family>-<lang><i>-<scope>-<utterance>`，构造期去重断言。

**语义不变量**：同族所有表述必须解析到同一产品族。任何失败 = 产品语义
回归（或 routing 缺口 —— 语料库的另一半价值是把它们逼出来）。

## 契约层（WorkflowContractCase）

显式 recipe + 数据画像 → `compile_workflow`（15 阶段，V3）→ 断言：

- 角色绑定状态（bound / external / degraded / unresolved）；
- 义务评估状态（satisfied / warning / degraded / blocked）；
- method_blockers / data_blockers；
- 警告码在场/禁用；
- FallbackDecision reason codes；
- **verdict V2**：在合成「渲染完美」完成面上断言 BLOCKED_BY_METHOD /
  READY_WITH_WARNINGS —— 「漂亮地图掩盖不了方法不成立」的可执行形态。

## 覆盖 sweep

`build_v2_recipe_coverage_sweep()`：全部 147 个 V2 recipe 逐一跑完 15 阶段
编译（registry 覆盖烟测）；blocked 只允许来自科学义务（诚实阻断），
不允许编译器自身失败。

## 运行

```bash
# 全量（~20s，离线，零 LLM；V3 起挂 perf 标记 —— unfiltered 运行自跳过，
# 资源红线：20k 全量不进默认门，#664 约定）
pytest -m perf tests/unit/gis_harness/test_conformance_corpus.py::test_corpus_full_run_green

# 默认车道：确定性分层抽样（543 案例 ≈3s）+ 领域切片
pytest tests/unit/gis_harness/test_conformance_corpus.py -q

# 分片（CI 友好）
python -c "from app.evaluation.conformance import build_conformance_corpus; \
           print(len(build_conformance_corpus(domains=['terrain'])))"
```

性能契约：plan-tier ≤ ~5ms/case（无网络、无全量扫描、planner memo 关闭）。

## V3 扩容说明（20088 案例）

- **维度扩展**：scope 5→12（重庆/北京/上海/广州/杭州/深圳/省+市）、
  句式 4→9（请分析 / 的情况 / 我想了解 / 如何 / 帮忙做一下）—— 每个新
  包装词经批量核验不改变既有族的语义身份；
- **新语义族 12 个**：路网中心性、火灾风险、人口暴露、仓储选址、OD
  矩阵、分类构成、两期对比（卷帘）、SAR 斑点滤波、年际对比、TWI、空间
  回归、插值不确定性 —— 部分依赖 V3 保守本体任务升级（见
  [semantic-workflow-v3.md](./semantic-workflow-v3.md)）；
- **V3 指标契约（opt-in）**：`expected_ontology_task`（本体 top-1 锁）、
  `qualification_profile` + `expected_qualification`（资格四态复评）、
  `expected_fallback_tier`（回退层裁决）、`check_determinism`（双跑对齐）；
- **资源分层**：默认车道 = 确定性分层抽样（每 37 例取 1）+ 领域切片；
  全量 = `pytest -m perf` 显式运行。
