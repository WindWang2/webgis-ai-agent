"""Federated Spatial Query Optimizer V6（ADR-0118）。

纵向深化 V5 链式联邦：typed logical plan → statistics → spatial cost →
physical alternatives（left-deep + bushy）→ pushdown → 流式执行 → runtime
feedback。单一事实源：谓词 AST / capability / statistics / budget / adapter
契约全部复用既有模块；本包只新增优化与执行语义，绝不建第二 registry/store。
"""

from app.services.data_fabric.query.federated.logical import (
    LogicalAggregate,
    LogicalFilter,
    LogicalJoin,
    LogicalLimit,
    LogicalNode,
    LogicalProject,
    LogicalReproject,
    LogicalScan,
    LogicalSort,
    chain_to_logical,
    logical_from_dict,
)

__all__ = [
    "LogicalAggregate",
    "LogicalFilter",
    "LogicalJoin",
    "LogicalLimit",
    "LogicalNode",
    "LogicalProject",
    "LogicalReproject",
    "LogicalScan",
    "LogicalSort",
    "chain_to_logical",
    "logical_from_dict",
]
