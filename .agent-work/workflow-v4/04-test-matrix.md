# Workflow V4 — Test Matrix (Phase D 实测)

## 分层覆盖

| 层级 | 文件 | 数量 | 结果 |
|---|---|---|---|
| 方法族/资格引擎 | test_methodology_v4.py | 13 | ✅ |
| Typed DAG | test_typed_dag_v4.py | 6 | ✅ |
| Compiler V4 | test_compiler_v4.py | 6 | ✅ |
| 义务继承+包版本 | test_obligations_package_v4.py | 10 | ✅ |
| 参数/重算/diff/获取/制图 | test_workflow_v4_semantics.py | 15 | ✅ |
| 双语语料+评估 | test_methodology_corpus_v4.py | 9 | ✅ |
| 生产接入集成 | test_workflow_v4_production.py | 6 | ✅ |
| 性能/资源预算 | test_workflow_v4_budget.py | 4 | ✅ |
| 中央校验收编 | test_methodology_central_validation.py | 2 | ✅ |
| **V4 合计** | | **71** | **全绿** |

## 回归面

| 范围 | 结果 |
|---|---|
| tests/unit/gis_harness/ 全量（含既有 845） | 916 passed ✅ |
| tests/unit/test_plan_orchestrator.py | 17 passed ✅ |
| tests/unit/test_semantic_gis_intelligence.py | ✅（并入批量） |
| tests/unit/test_reproducible_gis_runtime.py | 26 passed ✅ |
| test_workflow_guards.py（164 recipe 锁 + manifest 锁） | 17 passed ✅ |

## 静态检查

| 检查 | 结果 |
|---|---|
| `ruff check app/ tests/`（repo 全量） | All checks passed ✅ |
| `validate_gis_library()`（全库交叉引用） | 0 issues ✅ |

## 特殊维度

- **确定性**：同输入双编译 `to_bounded_dict()` 逐字段相等 + 包指纹一致
  （test_v4_deterministic / test_package_emit_reproducible_fingerprint /
  evaluation deterministic 维）。
- **有界性**：V4 产物 < 64KB；DAG ≤ 64 节点/128 边；义务链/参数/角色
  全部截断断言。
- **负路径**：非法参数回落+披露、空查询工具拒绝、未映射任务诚实 skip、
  乱码请求证据 None 不崩溃。
- **性能**：首编译 < 2s 护栏（实测 ~0.05s）；全语料 37 表述×双编译
  < 5s（实测 ~2.2s）；结构性预算（阶段数/节点数）锁定。
- **科学/制图确定性**：资格裁决五态语义与 V3 一致（unknown ≠ 不满足）；
  制图义务同输入同义务。

## 覆盖率（V4 新模块）

| 模块 | 覆盖率 |
|---|---|
| cartography.py / obligations.py / \_\_init\_\_.py | 100% |
| acquisition.py / package.py / compiler_v4.py | 96-97% |
| methodology.py / typed_dag.py / evaluation.py | 92-93% |
| parameters.py / recompute.py / diff.py | 89-91% |
