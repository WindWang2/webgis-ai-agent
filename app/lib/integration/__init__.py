"""Integration / 协调平台（Quality V3，Epic 10）。

并发研发的 integration authority：shared-file ownership、migration/ADR
分配协调、生成物权威、semantic integration manifest、影响选择与合并模拟。

边界（架构 01-architecture.md 不变量 1）：

- 本包是**开发者侧协调面**：只做静态分析 + 只读 git 查询，生产运行时
  （app/main.py 及其下游）不得 import 本包（结构测试
  ``tests/quality/test_integration_preflight_gate.py`` 锁定）。
- 不篡夺业务域事实源：registries / quality manifest / artifact_graph
  仍是唯一事实源，本包全部是元契约投影。
"""
