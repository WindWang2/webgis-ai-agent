"""Data Quality —— 质量规则引擎 + 修复计划/执行证据面（V9 升为子系统）。

历史（Wave-4）：``repair_plan`` / ``repair_execution`` —— 质量发现 → 修复
计划 → 显式执行的证据链。V9（ADR-0140 前身段，任务书 P1）补齐规则面：

- ``rules``           —— 规则 DSL / 16 类内置规则注册表（封闭词表）；
- ``rule_functions``  —— 纯函数判定实现（vector/raster 双族）；
- ``engine``          —— evaluate（同步小数据集）/ durable job（大数据集）
  双路径 + QualityReport 落库（migration 0046）；
- ``autofix``         —— autofixable 子集的确定性修复（dry-run 优先、
  new-ref 语义）；
- ``metrics``         —— 规则耗时/命中率 prometheus 指标。

红线（延续）：修复词表单一事实源仍是
``app.services.gis_harness.data_qualification.REMEDIATION_OPS``；计划绝不
自动执行 —— 执行只经显式调用（工具 / REST），且产出新载荷不覆写源。
"""
from app.services.data_quality.repair_plan import (
    RepairPlan,
    RepairStep,
    build_repair_plan,
    compute_plan_id,
)

__all__ = [
    "RepairPlan",
    "RepairStep",
    "build_repair_plan",
    "compute_plan_id",
]
