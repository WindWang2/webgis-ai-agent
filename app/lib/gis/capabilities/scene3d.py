"""G3D04 场景表达 3D 能力包。

一等 3D 能力（此前 scene 工具借位 ``thematic_cartography``，catalog/
capability graph 的 3D 可发现性为零）：

- ``scene_representation``：2d/2.5d/3d 表达决策与场景档切换；
- ``terrain_scene_binding``：已核实 raster-dem 源 → scene.terrain 绑定；
- ``scene_camera_action``：场景相机动作（SetView 通道 + user-wins 守卫）。

产出/消费 artifact 复用既有词表（scene_config / raster_surface），不新增
artifact 类型；category="presentation"（plan_graph 派生 kind="scene"）。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [
    CapabilityDescriptor(
        id="scene_representation", name="三维场景表达决策",
        category="presentation", domain="cartography",
        description=(
            "用户 3D 偏好 × 垂直证据（高度字段/raster-dem）× 媒介 → "
            "2d/2.5d/3d 场景档决策与 SetSceneIntent 事务切换；显式 3D 无证据"
            "时诚实降级并保留偏好（fail-closed，不伪造高度/地形）。"),
        input_artifact_types=["raster_surface"],
        output_artifact_types=["scene_config"],
        purpose_template="三维场景表达（{mode}）",
        deterministic=True,
        status="planned",
    ),
    CapabilityDescriptor(
        id="terrain_scene_binding", name="地形场景绑定",
        category="presentation", domain="cartography",
        description=(
            "已核实 raster-dem 源绑定到 scene.terrain（source/exaggeration/"
            "elevation_ref）；无合格源时 typed unavailable + alternatives。"),
        input_artifact_types=["raster_surface"],
        output_artifact_types=["scene_config"],
        purpose_template="地形表达绑定",
        deterministic=True,
        status="planned",
    ),
    CapabilityDescriptor(
        id="scene_camera_action", name="场景相机动作",
        category="presentation", domain="cartography",
        description=(
            "3D 相机 pitch/bearing/center/zoom 动作（编译为 SetViewIntent）；"
            "用户相机选择 user-wins 优先——用户 viewport 已实时，agent 不偷改。"),
        input_artifact_types=["scene_config"],
        output_artifact_types=["scene_config"],
        purpose_template="场景相机调整",
        deterministic=True,
        status="planned",
    ),
]
