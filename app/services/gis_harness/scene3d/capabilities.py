"""3D 能力快照（G3D04 M3）——typed 能力发现，unavailable 必带 alternatives。

复用既有发现面（零第二注册表）：

- 工具可用性 → ``ToolRegistry``（真实注册状态）；
- 能力元数据 → ``capability_registry``（G3D04 新增 scene3d 一等能力）；
- 数据资格 → :mod:`scene3d.evidence`（raster-dem 源/高度字段证据）；
- 科学算法 → ``algorithm_registry``（terrain.viewshed 等）。

产出 :class:`Scene3DCapabilitySnapshot`：每项
``available/available_with_limits/unavailable`` + reason code +
alternatives（缺什么、可以退到哪），供 agent turn 内真实决策——
绝不只在 prompt 里列假工具。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.scene3d.evidence import EvidenceBundle

#: 能力项词表（G3D04 关心的 3D 面；G3D-V2-C 增 terrain_measurement/cut_fill_volume）。
CAPABILITY_KEYS = (
    "scene_mode",        # 2d/2.5d/3d 场景切换（SetSceneIntent 通道）
    "terrain",           # 地形呈现（需要合格 raster-dem 源）
    "extrusion",         # 立体挤出（需要已核实高度字段证据 + polygon）
    "camera",            # 相机动作（SetViewIntent 通道 + user-wins 守卫）
    "viewshed_analysis",  # DEM 视域分析（terrain.viewshed 算法 + 工具）
    "line_of_sight",     # 两点通视（#1773 工具真实探测）
    "terrain_measurement",  # 剖面/地表距离/地表面积（#1773 工具族真实探测）
    "cut_fill_volume",   # 基准面土方（G3D-V2-C 新增）
    "skyline",           # 天际线（算法未实现；typed unavailable）
    "scene_asset",       # 3D Tiles/gltf 资产挂载（G3D02 渲染面；typed unavailable）
)

_STATUS_VOCAB = ("available", "available_with_limits", "unavailable")


class CapabilityEntry(BaseModel):
    """一项 3D 能力的 typed 结论。"""

    model_config = {"extra": "forbid"}

    key: str
    status: str                       # _STATUS_VOCAB
    reason_code: str = ""
    detail: str = Field("", max_length=200)
    alternatives: List[str] = Field(default_factory=list, max_length=4)

    @property
    def unavailable(self) -> bool:
        return self.status == "unavailable"


class Scene3DCapabilitySnapshot(BaseModel):
    """一个会话/turn 的 3D 能力快照（有界、确定性、可序列化给 Pi）。"""

    model_config = {"extra": "forbid"}

    schema_version: str = "scene3d_capability.v1"
    entries: List[CapabilityEntry] = Field(default_factory=list, max_length=16)
    dem_source_ids: List[str] = Field(default_factory=list, max_length=8)
    notes: List[str] = Field(default_factory=list, max_length=4)

    def entry(self, key: str) -> Optional[CapabilityEntry]:
        for item in self.entries:
            if item.key == key:
                return item
        return None

    def unavailable_keys(self) -> List[str]:
        return [e.key for e in self.entries if e.unavailable]


def _tool_available(name: str) -> bool:
    try:
        from app.agent_pi_bridge import try_get_tool_registry

        registry = try_get_tool_registry()
        if registry is None:
            return False
        return bool(registry.has(name))
    except Exception:  # noqa: BLE001 — 发现面失败按不可用处理（fail-closed）
        return False


def probe_scene3d_capabilities(
    evidence: EvidenceBundle,
    *,
    geometry_kinds: Optional[List[str]] = None,
    medium: str = "interactive",
) -> Scene3DCapabilitySnapshot:
    """证据 + 注册表 → 3D 能力快照（纯函数，无 IO——注册表为进程内权威）。

    Args:
        evidence: :func:`verify_vertical_evidence` 的核实结论。
        geometry_kinds: 当前数据几何类型（挤出需 polygon）。
        medium: 输出媒介（静态媒介限制 3D 档）。
    """
    geometry = set(geometry_kinds or [])
    static_medium = medium not in ("interactive", "screen", "")
    entries: List[CapabilityEntry] = []
    dem_ids = evidence.dem_source_ids()

    # scene_mode：SetSceneIntent 通道始终在（引擎事务面）；静态媒介受限
    entries.append(CapabilityEntry(
        key="scene_mode",
        status="available_with_limits" if static_medium else "available",
        reason_code="SCENE_CAP_MEDIUM_STATIC" if static_medium else "SCENE_CAP_OK",
        detail="静态媒介禁透视 3D（允许 2.5d 晕渲）" if static_medium
               else "SetSceneIntent 事务通道可用",
        alternatives=["2.5d 晕渲（静态媒介经典地貌表达）"] if static_medium else [],
    ))

    if dem_ids:
        entries.append(CapabilityEntry(
            key="terrain", status="available", reason_code="SCENE_CAP_DEM_VERIFIED",
            detail=f"已核实 raster-dem 源: {', '.join(dem_ids[:3])}",
            alternatives=[],
        ))
    else:
        entries.append(CapabilityEntry(
            key="terrain", status="unavailable",
            reason_code="SCENE_CAP_NO_DEM_SOURCE",
            detail="spec 内无已注册 raster-dem 源（scene.terrain.source 无处绑定）",
            alternatives=["先经数据面注册 raster-dem 源（G3D01 DEM 管线）",
                          "hillshade 栅格衍生（无 DEM 时不可用，同样需高程数据）",
                          "降级 2d 平面表达并披露"],
        ))

    if evidence.has_height and "polygon" in geometry:
        entries.append(CapabilityEntry(
            key="extrusion", status="available", reason_code="SCENE_CAP_HEIGHT_VERIFIED",
            detail="存在已核实数值高度字段 + polygon 几何", alternatives=[],
        ))
    elif evidence.has_height:
        entries.append(CapabilityEntry(
            key="extrusion", status="unavailable",
            reason_code="SCENE_CAP_EXTRUSION_REQUIRES_POLYGON",
            detail="高度证据在，但挤出是 polygon 语义通道",
            alternatives=["点要素用符号尺寸表达数量（视觉变量替代）"],
        ))
    else:
        entries.append(CapabilityEntry(
            key="extrusion", status="unavailable",
            reason_code="SCENE_CAP_NO_HEIGHT_EVIDENCE",
            detail="无已核实高度字段证据（fail-closed，不伪造默认高度）",
            alternatives=["提供含高度字段的数据集并核实（extrusion 采样）",
                          "2d 分级设色表达同一变量"],
        ))

    entries.append(CapabilityEntry(
        key="camera", status="available", reason_code="SCENE_CAP_SETVIEW_CHANNEL",
        detail="SetViewIntent 事务通道可用；用户相机选择 user-wins 优先",
        alternatives=[],
    ))

    if _tool_available("viewshed_analysis"):
        if dem_ids or evidence.has_elevation:
            entries.append(CapabilityEntry(
                key="viewshed_analysis", status="available",
                reason_code="SCENE_CAP_VIEWSHED_READY",
                detail="viewshed_analysis 工具已注册且存在已核实 DEM 证据",
                alternatives=[],
            ))
        else:
            entries.append(CapabilityEntry(
                key="viewshed_analysis", status="unavailable",
                reason_code="SCENE_CAP_VIEWSHED_NO_DEM",
                detail="工具在但缺 DEM 数据事实（viewshed 输入是 terrain_surface）",
                alternatives=["先经数据面注册 raster-dem 源（G3D01）",
                              "缓冲区近似可见性（语义不同，须向用户披露差异）"],
            ))
    else:
        entries.append(CapabilityEntry(
            key="viewshed_analysis", status="unavailable",
            reason_code="SCENE_CAP_TOOL_NOT_REGISTERED",
            detail="viewshed_analysis 未注册（registry 无此工具）",
            alternatives=[],
        ))

    # 相邻方向边界：typed unavailable + alternatives（不假装可做）
    # G3D-V2-C（ADR-0276）：#1773 起量测工具族已在 registry——line_of_sight
    # 从「typed unavailable 待 G3D03」升级为真实探测（工具注册 + DEM 证据，
    # 与 viewshed 同口径）；新增 terrain_measurement（profile/distance/area）
    # 与 cut_fill_volume 键。skyline 仍是真不做（typed unavailable）。
    if _tool_available("line_of_sight_analysis"):
        if dem_ids or evidence.has_elevation:
            entries.append(CapabilityEntry(
                key="line_of_sight", status="available",
                reason_code="SCENE_CAP_LOS_READY",
                detail="line_of_sight_analysis 工具已注册且存在已核实 DEM 证据",
                alternatives=[],
            ))
        else:
            entries.append(CapabilityEntry(
                key="line_of_sight", status="unavailable",
                reason_code="SCENE_CAP_VIEWSHED_NO_DEM",
                detail="工具在但缺 DEM 数据事实（LOS 输入是 terrain_surface）",
                alternatives=["先经数据面注册 raster-dem 源（G3D01）",
                              "viewshed 掩膜 + 观察点（语义差异须披露）"],
            ))
    else:
        entries.append(CapabilityEntry(
            key="line_of_sight", status="unavailable",
            reason_code="SCENE_CAP_TOOL_NOT_REGISTERED",
            detail="line_of_sight_analysis 未注册（registry 无此工具）",
            alternatives=[],
        ))
    if _tool_available("terrain_profile") and _tool_available(
        "surface_area_analysis"
    ):
        if dem_ids or evidence.has_elevation:
            entries.append(CapabilityEntry(
                key="terrain_measurement", status="available",
                reason_code="SCENE_CAP_MEASUREMENT_READY",
                detail="terrain_profile/surface_distance/surface_area 工具族"
                       "已注册且存在已核实 DEM 证据",
                alternatives=[],
            ))
        else:
            entries.append(CapabilityEntry(
                key="terrain_measurement", status="unavailable",
                reason_code="SCENE_CAP_VIEWSHED_NO_DEM",
                detail="量测工具族在但缺 DEM 数据事实",
                alternatives=["先经数据面注册 raster-dem 源（G3D01）"],
            ))
    else:
        entries.append(CapabilityEntry(
            key="terrain_measurement", status="unavailable",
            reason_code="SCENE_CAP_TOOL_NOT_REGISTERED",
            detail="量测工具族未注册（registry 缺 terrain_profile/"
                   "surface_area_analysis）",
            alternatives=[],
        ))
    if _tool_available("cut_fill_volume_analysis"):
        if dem_ids or evidence.has_elevation:
            entries.append(CapabilityEntry(
                key="cut_fill_volume", status="available",
                reason_code="SCENE_CAP_CUTFILL_READY",
                detail="cut_fill_volume_analysis 工具已注册且存在已核实 DEM 证据"
                       "（基准面土方：constant/mean）",
                alternatives=[],
            ))
        else:
            entries.append(CapabilityEntry(
                key="cut_fill_volume", status="unavailable",
                reason_code="SCENE_CAP_VIEWSHED_NO_DEM",
                detail="工具在但缺 DEM 数据事实（体积输入是 terrain_surface）",
                alternatives=["先经数据面注册 raster-dem 源（G3D01）",
                              "surface_area_analysis（面积而非体积）"],
            ))
    else:
        entries.append(CapabilityEntry(
            key="cut_fill_volume", status="unavailable",
            reason_code="SCENE_CAP_TOOL_NOT_REGISTERED",
            detail="cut_fill_volume_analysis 未注册（registry 无此工具）",
            alternatives=[],
        ))
    entries.append(CapabilityEntry(
        key="skyline", status="unavailable",
        reason_code="SCENE_CAP_ALGORITHM_PENDING_G3D03",
        detail="天际线算法待实现（honest pending，不假装可做）",
        alternatives=["山体阴影/晕渲表达地形轮廓（非天际线分析）"],
    ))
    entries.append(CapabilityEntry(
        key="scene_asset", status="unavailable",
        reason_code="SCENE_CAP_PENDING_G3D02",
        detail="3D Tiles/gltf 场景资产挂载属 G3D02 渲染面（本方向 adapter 边界）",
        alternatives=["fill-extrusion 白模（本方向可用，需高度证据）"],
    ))

    snapshot = Scene3DCapabilitySnapshot(entries=entries, dem_source_ids=dem_ids[:8])
    if static_medium:
        snapshot.notes.append("medium=%s: 3d 透视档不可用" % medium)
    return snapshot
