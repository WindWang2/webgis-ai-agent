# 02 — Plan（实施波次）

每个 wave：实现 + 测试 + commit。测试先行或同 wave 补齐；中央校验随 wave 并入。

| Wave | 内容 | 关键产物 |
|---|---|---|
| 1 | ontology 纯加法 5 tasks + methodology 13 族（proximity）+ 候选方法（含 interp.indicator_kriging）+ family corpus 补 proximity 案例 | gis_ontology.py / methodology.py / app/evaluation/methodology_corpus.py（R1-F2/F3/F4） |
| 2 | package 骨架 + provenance ledger | app/lib/gis/methodology/{__init__,provenance}.py |
| 3 | taxonomy V2（20 类 descriptor + 对齐 + 匹配） | taxonomy.py + test_taxonomy |
| 4 | descriptor V2 增强层（44+ 方法审定 enrichment） | descriptors.py + test_descriptors |
| 5 | graph 模型 + builder（registry 投影） | graph.py + test_graph |
| 6 | graph integrity + fingerprint + diff + 预算 | graph.py + test_graph |
| 7 | 中央校验并入（methodology_intel:）+ registry parity | registry_validation.py + central test |
| 8 | qualification engine：统一报告 + DatasetProfile 适配 | qualification.py + test |
| 9 | qualification：geometry/CRS/scale 维度 | 同上 |
| 10 | qualification：categorical/continuous + temporal + nodata | 同上 |
| 11 | qualification：缺失需求 + 预处理建议 + 置信度 | 同上 |
| 12 | hard 错误锁定（Epic §5.D 五禁止项 corpus） | test_qualification 负例 |
| 13 | ranking v1：确定性混合 + 解释 | ranking.py + test |
| 14 | 双语方法语料（扩展 methodology_corpus 模式，method 级） | corpus 模块 + test |
| 15 | hard-negative + 近重复语料 | 同上 |
| 16 | abstention + 指标（Recall@k/MRR/invalid/abstention） | ranking.py + benchmark test |
| 17 | viz bridge 词表 + 桥接表 + 校验 | viz_bridge.py + test |
| 18 | viz bridge：typical_map_models 收编 + 冲突显式 | 同上 |
| 19 | TemplateSpecV2 schema | cartography/template_intelligence.py |
| 20 | data bindings / capability reqs / export constraints | 同上 |
| 21 | component intelligence：semantic_role/examples 增量 + 查询层 | component_registry.py + template_intelligence |
| 22 | layout/expression 约束接线（layout_constraints 复用） | 同上 |
| 23 | composition planner + 负例（禁 query 硬编码） | plan_composition + test |
| 24 | workflow skeleton contract（V4 typed DAG 消费投影） | service.py |
| 25 | render intent contract（MapModel + obligations 投影） | service.py / render_intent |
| 26 | KnowledgeService 门面（9 能力） | service.py + test_service |
| 27 | Harness 工具面 6 工具 + 注册 | knowledge_tools.py + test |
| 28 | explanation/evidence API（selected/rejected 全链解释） | service.py |
| 29 | feedback schema + JSONL writer（默认禁用）+ 隔离 | feedback.py + test |
| 30 | GIS case corpus（≥21 案例，Epic §9 全覆盖） | case_corpus.py + test |
| 31 | 性能/质量 benchmark（预算断言 + 指标钉值） | test_budgets + benchmark |
| 32 | ADR-0120 + docs + CHANGELOG + BENCHMARK_MANIFEST 再生成 + 收尾回归 | docs/adr/0120-*.md 等 |

Review R1（Subagent-A）在 wave 17 后介入一次（graph/qualification 核心面），
最终全量 review 在 wave 32 后；R2（Subagent-B）在 R1 修复后。
