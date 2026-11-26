"""Data Fusion Authoring —— 多源空间实体融合工作台（单一编排面）。

模块布局（与 G38 data_join_authoring 同款纪律）：
- ``contracts``  —— 封闭词表 / typed 错误码 / 预算（唯一登记处）；
- ``errors``     —— typed fail-closed 错误；
- ``plan``       —— 版本化融合计划（pydantic + normalized digest）；
- ``textsim``    —— 纯文本相似度原语（归一化/编辑距离/词元重合）；
- ``matching``   —— 实体匹配引擎（blocking/评分/消歧/冲突检测）；
- ``adjudication`` —— 确定性裁决 + 审阅决定应用；
- ``service``    —— API 与 Pi 工具共用的单一编排面；
- ``executor``   —— 运行执行（装载→匹配→冲突→裁决→组装）；
- ``outputs``    —— 终态包 staging/publish/DataObject/session 台账；
- ``review``     —— 审阅决定持久化（CAS）与查询；
- ``incremental`` —— 来源刷新 → 影响面 → 保留锁定的增量重配。

不新建：任务状态机（走 analysis_tasks）、第二 MapSpec、第二数据事实源；
输出 = 不可变融合数据集版本（ref:data-fusion/<run_id>），可被既有
统计/热力/聚合/交付链直接消费。
"""
from app.services.data_fusion_authoring.contracts import (  # noqa: F401
    FUSION_PLAN_SCHEMA_VERSION,
    METHOD_VERSION,
)
