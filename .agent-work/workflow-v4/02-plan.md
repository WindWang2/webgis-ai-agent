# Workflow V4 — Implementation Plan (Phase C)

13 waves，同 branch 顺序推进，每 wave：契约/测试先行 → 最小闭环 → targeted
tests → lint → 小步 commit（Conventional Commits）。

| Wave | Scope | 核心交付 | 测试 |
|---|---|---|---|
| 1 | methodology.py | 12 方法族模型（引用既有任务/能力/工件词表）+ registry + validate | test_methodology.py |
| 2 | methodology.py | MethodCandidate + qualification ranking（拒绝理由机器可读） | 同上 |
| 3 | typed_dag.py | TypedWorkflowNode/Port/Edge + 兼容性校验 + 平行安全标记 | test_typed_dag.py |
| 4 | compiler_v4.py | compile_workflow_v4（wrap 15 阶段 + 6 V4 阶段） | test_compiler_v4.py |
| 5 | obligations.py | 义务继承/provenance/幂等/composite 传播 | test_obligations_v4.py |
| 6 | package.py | WorkflowPackage semver + immutable fingerprint + 兼容性 | test_workflow_package.py |
| 7 | parameters.py | 参数解析 + provenance + 不阻塞 | test_workflow_parameters.py |
| 8 | recompute.py | 变更 → affected subgraph + reuse 判定 | test_workflow_recompute.py |
| 9 | diff.py | 语义 diff + recompute 解释 | test_workflow_diff.py |
| 10 | acquisition.py + cartography.py | 获取备选声明 + 制图表达义务 | test_acquisition_cartography.py |
| 11 | app/evaluation/methodology_corpus.py + evaluation.py | 双语语料（≥48）+ 编译器评估 harness | test_methodology_corpus.py |
| 12 | semantic_tools.py + plan_orchestrator.py | 生产接入（证据上行，零漂移） | test_workflow_v4_production.py |
| 13 | docs/adr/0118 + .agent-work 总结 | ADR + 文档 | — |

集成测试（Wave 12 后）：compile → package → diff → recompute 全链 + 确定性。
Phase D：repo lint（ruff?）+ changed-scope tests + 相关 regression。
