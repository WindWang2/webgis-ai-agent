"""多画幅产品能力包（G15；域模块注册 —— 勿回填中央文件）。

工具面真实存在（frame_product_*）；resolver 不派发为分析，仅作能力图
节点/工具绑定契约（与 temporal 控制面同门：platform 类、planned 分析态
不适用 —— 本包 status 记 planned 表示「非分析 producer」）。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [
    CapabilityDescriptor(
        id="frame_product_plan", name="多画幅产品计划",
        category="platform", status="planned",
        description=(
            "从真实数据桶/类别值/字段推导多画幅（小倍数）帧计划（只读；"
            "frame_product_plan）。"),
    ),
    CapabilityDescriptor(
        id="frame_product_bind", name="多画幅产品绑定",
        category="platform", status="planned",
        description=(
            "把会话数据绑定为多画幅产品：画幅身份登记（layout.frames）+"
            "产品契约（frame_product）+ 跨画幅共享分级（真实数据并集只算"
            "一次；frame_product_bind/unbind）。"),
    ),
    CapabilityDescriptor(
        id="frame_product_update", name="多画幅产品更新",
        category="platform", status="planned",
        description="多画幅产品键级增量更新（frame_product_update）。",
    ),
    CapabilityDescriptor(
        id="frame_product_status", name="多画幅产品状态",
        category="platform", status="planned",
        description="多画幅产品 refs-only 状态与逐画幅终验摘要（frame_product_status）。",
    ),
    CapabilityDescriptor(
        id="frame_product_unbind", name="多画幅产品解绑",
        category="platform", status="planned",
        description="解绑多画幅产品语义（frame_product_unbind）。",
    ),
]
