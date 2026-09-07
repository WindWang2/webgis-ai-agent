"""Data Quality —— Wave-4 修复计划 / 修复执行证据面。

审计 08 §4.3（repair-plan state machine MISSING）：质量发现 → 修复计划 →
已执行修复 → 新修订 → 复查 之间没有任何持久对象串联。本包补上这一环：

- ``repair_plan``      —— RepairPlan 实体（plan-only；确定性 plan_id）；
- ``repair_execution`` —— 显式执行缝（新 ref + 有界 repair_evidence，
  绝不静默覆写源载荷）。

红线：修复词表单一事实源仍是
``app.services.gis_harness.data_qualification.REMEDIATION_OPS``（经
``app.services.data_ingest.repair_planning`` 的 W3 映射表），本包不发明
第四套词表；计划绝不自动执行 —— 执行只经显式调用（工具 / REST）。
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
