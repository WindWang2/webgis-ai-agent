"""Task Data Supply — 任务驱动数据覆盖评估与供给（GOAL-20261126-02）。

把已有各层（目录/资格/ADS/检索/预检）拼成有证据的角色级数据覆盖/缺口
判定与实际供给体验。子模块：

- ``requirement``：任务数据需求合同（TDR-v1，WP1）；
- （WP2+）覆盖矩阵、供给计划编排。

红线：不复制五态资格/ADS/目录；未知 ≠ 不存在；展示级来源不入数值统计。
"""
from app.services.task_data_supply.requirement import (  # noqa: F401
    CONSUMPTION_METHODS,
    DEMAND_STATES,
    RoleDemand,
    TaskDataRequirement,
    TaskScope,
    TDR_VERSION,
    requirement_from_role_demands,
    requirement_from_workflow_v4,
    task_requirement,
)
