# TEST_MATRIX — 验证矩阵

运行口径：`../webgis-ai-agent/.venv/Scripts/python.exe -m pytest <targets> -q --no-cov -p no:cacheprovider`（worktree 根）；并行 ≤ -n 2；同一时刻至多一个 heavyweight suite。

| 维度 | 目标 | 测试 |
|---|---|---|
| T1 Rule 契约 | 冻结/词表校验/指纹稳定 | test_standards_rule_v1.py |
| T2 RuleGraph | 拓扑序、环 fail-closed、未知依赖、冲突双披露、依赖失败级联 not_evaluated | test_standards_graph_v1.py |
| T3 StandardsPack | semver/fingerprint/registry resolve/向后兼容旧图（legacy fixture 无 error） | test_standards_pack_v1.py |
| T4 profile 推断 | OUTPUT_PURPOSES→medium、pageSize→print、显式覆盖优先、未知值回退 | test_standards_profile_v1.py |
| T5 义务求值 | count-vs-rate、legend unit、CVD、source/time/uncertainty、label density、required components——每条 rule kind 正反例 + not_evaluated | test_standards_evaluate_v1.py |
| T6 gate/QA | precompile block 语义（仅 explicit strict）、enabled=False 零行为、fingerprint 稳定、bounded pi_card、幂等（同输入两次一致） | test_standards_qa_v1.py |
| T7 跨模块契约 | fix_hint 路由 ⊆ AUTO_SAFE ∪ REPAIR_ACTIONS ∪ autofill；引用的 semantic_checks rule ids 存在 | test_standards_contract_v1.py |
| T8 fixtures | 200+ 夹具逐个断言 expected violations；生成器幂等（重生成 byte-identical） | test_standards_fixtures_v1.py |
| T9 catalog | 生成 vs 提交物 drift；JSON schema | test_standards_catalog_v1.py |
| T10 回归 | tests/cartography 全量（marker=cartography 子集重点）不出现新失败；失败须 master 同口径复现 | 全量回归 |
| T11 lint | ruff check 新文件 | ruff |
| T12 determinism | 关键 Oracle 验证连续两遍一致 | 手工执行两次 |

TDD 红线：先写失败测试（T1–T7 契约面），再实现；每个 rule kind 先有 intentional-violation 夹具证明可捕获，再有 clean 夹具证明不误报。
