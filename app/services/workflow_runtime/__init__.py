"""Semantic Workflow Runtime V5 —— 可执行 typed DAG 运行时（Epic workflow-v5）。

把 Workflow V4 编译产物（WorkflowPackage / typed DAG / RecomputePlan）从
evidence-only 升级为可执行、可恢复、可增量重算、可复用证明的运行时事实。

层次（架构见 .agent-work/workflow-v5-executable-runtime/01-architecture.md）：

    Workflow V4 Compiler（语义唯一事实源，零改动）
        ↓ WorkflowPackage
    WorkflowPackage Registry（DB 持久，semver 发布）
        ↓
    Runtime Instance Store（实例行 + 每节点行，两级 CAS）
        ↓ Binding Gate → DAG Execution Driver → GeoCompute Adapter
    Artifact Binding / Reuse Index
        ↓
    Semantic Change → affected subgraph → 增量重算 + 复用证明

红线：

- **不是第二事实源**：包内容事实源是 V4 编译器（注册时 re-emit 比对指纹）；
  会话行状态写手仍是 SessionPlan ``_mark_progress``；载荷事实源仍是
  session ref / ArtifactRegistry / DataObject —— 本运行时只存节点执行态、
  指纹与指针；
- 执行控制经适配器委托既有 GeoCompute 执行面；不可执行的节点诚实
  ``NODE_NOT_EXECUTABLE``，绝不假装执行；
- 全部有界：节点 ≤64 / attempts ≤3 / 复用索引每 owner LRU ≤128 /
  pending changes ≤16 / dispatch 并发 ≤4；
- 多租户：owner 域沿用 ``executor.owner_scope_for`` 哈希纪律，复用与
  读取绝不跨 owner。
"""
from __future__ import annotations

#: 运行时 schema 版本（参与 env_fp 与复用指纹；语义/存储变化时递增）。
RUNTIME_SCHEMA_VERSION = "1.0.0"

__all__ = ["RUNTIME_SCHEMA_VERSION"]
