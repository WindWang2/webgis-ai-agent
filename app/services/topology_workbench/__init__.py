"""Topology Workbench（G31）—— 跨图层拓扑质量工作台领域模块。

公共入口：service（REST/工具共用编排）、runner（执行体）、exceptions_svc
（例外/复验）、repair（修复桥/导出）。契约常量在 contracts；规则词表校验
在 ruleset；快照解析在 snapshots；科学预检/度量在 geometry_core；五类规则
求值器在 evaluators；durable job 注册在 jobs。
"""
from app.services.topology_workbench.contracts import (
    ERROR_CODES,
    RULE_TYPES,
    RULE_ROLE_SPECS,
    SEVERITIES,
)
from app.services.topology_workbench.ruleset import rule_catalog
from app.services.topology_workbench.service import WorkbenchError

__all__ = [
    "WorkbenchError", "rule_catalog", "RULE_TYPES", "RULE_ROLE_SPECS",
    "SEVERITIES", "ERROR_CODES",
]
