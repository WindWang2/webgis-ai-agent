"""Representation 决策（G3D04 M3）——用户偏好 × 数据资格 × 媒介 → 2d/2.5d/3d。

分层纪律（不造第二决策表）：

1. **用户偏好层**（本模块）：从 Requirement IR ``SceneSpec`` 读用户显式
   表达（dimension/terrain/extrusion/exaggeration/medium），合成喂给
   决策核的 ``SceneIntent``——显式 3D 无证据时**不撤回用户偏好**，而是
   记录 :class:`DegradationNote`（偏好保留 + 降级原因入 IR）。
2. **决策核**（既有 ``plan_scene``）：证据门/媒介门/模式裁决/相机推荐/
   夸张钳制的唯一权威——本模块绝不复写其裁决逻辑。
3. **零推断红线**：无用户 cue 时 ``dimension=""`` → 决策核按证据默认
   （"分布情况"类话语不会变成 3D）。

输出 :class:`RepresentationDecision`：mode/extrusion/terrain + 用户偏好
尊重情况 + 降级注记 + 可直接交给 ``set_map_scene`` 的 scene dict。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.lib.cartography.scene_planning import (
    SceneDecision,
    SceneIntent,
    SceneMode,
    decision_to_scene_config,
    plan_scene,
)
from app.services.gis_harness.scene3d.capabilities import Scene3DCapabilitySnapshot
from app.services.gis_harness.scene3d.evidence import EvidenceBundle

#: 降级 reason code 词表（写入 SceneSpec.degrade_reason / 决策 reasons）。
DEGRADE_CODES = (
    "SCENE_USER_3D_NO_HEIGHT_EVIDENCE",   # 用户要挤出/3D 但无高度证据
    "SCENE_USER_3D_MEDIUM_STATIC",        # 用户要 3D 但媒介静态
    "SCENE_USER_TERRAIN_NO_DEM",          # 用户要地形但无 DEM 证据
    "SCENE_USER_EXTRUSION_NO_POLYGON",    # 用户要挤出但几何非 polygon
)


class DegradationNote(BaseModel):
    """用户显式偏好被降级的一条注记（偏好保留，降级必须可见）。"""

    model_config = {"extra": "forbid"}

    code: str
    user_preference: str = Field("", max_length=32)
    applied_mode: str
    detail: str = Field("", max_length=200)


class RepresentationDecision(BaseModel):
    """3D 表达决策（决策核结论 + 用户偏好尊重面 + scene 配置投影）。"""

    model_config = {"extra": "forbid"}

    schema_version: str = "scene3d_decision.v1"
    mode: SceneMode
    extrusion: bool
    terrain: bool
    user_dimension: str = ""              # 用户显式偏好（""=未表达；降级不撤回）
    user_dimension_respected: bool = True  # 决策模式 == 用户偏好
    degradations: List[DegradationNote] = Field(default_factory=list, max_length=4)
    reason_codes: List[str] = Field(default_factory=list, max_length=8)
    recommended_pitch: float = 0.0
    recommended_bearing: float = 0.0
    recommended_exaggeration: float = 1.0
    camera_transition_ms: int = 800
    scene_config: Dict[str, Any] = Field(default_factory=dict)

    @property
    def degraded(self) -> bool:
        return bool(self.degradations)


def decide_representation(
    *,
    user_dimension: str = "",
    terrain_requested: Optional[bool] = None,
    extrusion_requested: Optional[bool] = None,
    exaggeration: Optional[float] = None,
    medium: str = "interactive",
    purpose: str = "analysis",
    reduced_motion: bool = False,
    geometry_kinds: Optional[List[str]] = None,
    evidence: Optional[EvidenceBundle] = None,
    capabilities: Optional[Scene3DCapabilitySnapshot] = None,
    dem_source_id: Optional[str] = None,
) -> RepresentationDecision:
    """用户偏好 + 证据 + 能力 → 决策（纯函数；决策核是 plan_scene）。

    Args:
        user_dimension: IR ``scene.dimension``（""=未表达，零推断）。
        terrain_requested/extrusion_requested/exaggeration: IR 显式表达。
        medium: IR ``output.medium``（缺省 interactive）。
        evidence/capabilities: :mod:`scene3d.evidence` / :mod:`capabilities`
            结论（缺省按"无证据"fail-closed）。
        dem_source_id: terrain 绑定的 raster-dem 源 id（决策含地形且源
            核实时写入 scene_config.terrain.source；缺省取第一个核实源）。
    """
    ev = evidence or EvidenceBundle()
    caps = capabilities
    geometry = list(geometry_kinds or [])

    # ── 用户偏好 → 决策核输入（证据只来自核实结论） ──────────────────
    wants_3d = user_dimension == "3d"
    wants_extrusion = bool(extrusion_requested or (wants_3d and ev.has_height))
    wants_terrain = bool(
        terrain_requested
        or (user_dimension in ("2.5d", "3d") and ev.has_elevation)
        or (user_dimension == "" and False))  # 零推断：未表达不默认地形

    intent = SceneIntent(
        purpose=purpose if purpose in {
            "exploration", "analysis", "communication", "storytelling",
            "comparison", "monitoring"} else "analysis",
        medium=medium if medium in {
            "interactive", "screen", "print", "export_png", "export_pdf",
            "export_svg"} else "interactive",
        motion="reduced" if reduced_motion else "full",
        geometry_kinds=[g for g in geometry if g in ("point", "line", "polygon", "raster")],
        has_height_attribute_evidence=ev.has_height,
        has_elevation_evidence=ev.has_elevation,
        vertical_extrusion_intent=wants_extrusion,
        terrain_intent=wants_terrain,
        exaggeration_request=exaggeration,
    )
    core: SceneDecision = plan_scene(intent)

    # ── 用户偏好尊重面：决策核降级 vs 用户显式偏好的对账 ──────────────
    degradations: List[DegradationNote] = []
    core_codes = set(core.reason_codes())
    if wants_3d and core.mode != "3d":
        if "SCENE_EXTRUSION_NO_HEIGHT_EVIDENCE" in core_codes:
            degradations.append(DegradationNote(
                code="SCENE_USER_3D_NO_HEIGHT_EVIDENCE",
                user_preference="3d", applied_mode=core.mode,
                detail="用户显式 3D，但无已核实高度字段证据——不伪造高度；"
                       "偏好保留于 requirement，可补数据后重判"))
        elif "SCENE_MEDIUM_STATIC" in core_codes:
            degradations.append(DegradationNote(
                code="SCENE_USER_3D_MEDIUM_STATIC",
                user_preference="3d", applied_mode=core.mode,
                detail="用户显式 3D，但媒介为静态（透视比例尺不可靠）——"
                       "诚实降级，偏好保留"))
        elif "SCENE_EXTRUSION_REQUIRES_POLYGON" in core_codes:
            degradations.append(DegradationNote(
                code="SCENE_USER_EXTRUSION_NO_POLYGON",
                user_preference="3d", applied_mode=core.mode,
                detail="用户显式 3D，但挤出需 polygon 几何——偏好保留"))
        else:  # pragma: no cover — 其余 3d 降级路径（防御性披露）
            degradations.append(DegradationNote(
                code="SCENE_USER_3D_NO_HEIGHT_EVIDENCE",
                user_preference="3d", applied_mode=core.mode,
                detail="用户显式 3D 未被决策采纳；偏好保留"))
    if wants_terrain and not core.terrain:
        degradations.append(DegradationNote(
            code="SCENE_USER_TERRAIN_NO_DEM",
            user_preference=user_dimension or "terrain", applied_mode=core.mode,
            detail="用户显式地形表达，但无已核实 raster-dem 源——"
                   "不虚构地形；偏好保留于 requirement"))
    # 能力快照的 unavailable 面（如 capability 探测额外否决）——补充披露
    if caps is not None:
        for key in ("terrain", "extrusion"):
            entry = caps.entry(key)
            if entry is not None and entry.unavailable:
                note_codes = {d.code for d in degradations}
                if key == "terrain" and "SCENE_USER_TERRAIN_NO_DEM" not in note_codes \
                        and wants_terrain:
                    degradations.append(DegradationNote(
                        code="SCENE_USER_TERRAIN_NO_DEM",
                        user_preference="terrain", applied_mode=core.mode,
                        detail=f"capability 面报告 terrain unavailable: "
                               f"{entry.reason_code}"))
                if key == "extrusion" and wants_extrusion \
                        and "SCENE_USER_3D_NO_HEIGHT_EVIDENCE" not in note_codes \
                        and core.mode != "3d":
                    degradations.append(DegradationNote(
                        code="SCENE_USER_3D_NO_HEIGHT_EVIDENCE",
                        user_preference="3d", applied_mode=core.mode,
                        detail=f"capability 面报告 extrusion unavailable: "
                               f"{entry.reason_code}"))

    scene_config = decision_to_scene_config(core)
    # terrain 绑定：决策含地形时把已核实 raster-dem 源 id 写入 source
    if core.terrain and "terrain" in scene_config:
        chosen = dem_source_id or (ev.dem_source_ids()[:1] or [None])[0]
        if chosen and chosen in ev.dem_source_ids():
            scene_config["terrain"]["source"] = chosen
            scene_config["terrain"]["elevation_ref"] = chosen
        else:
            # 无已核实源 → 诚实省略 terrain（SetSceneIntent 会拒绝悬空 source）
            scene_config.pop("terrain", None)
            if not any(d.code == "SCENE_USER_TERRAIN_NO_DEM" for d in degradations):
                degradations.append(DegradationNote(
                    code="SCENE_USER_TERRAIN_NO_DEM",
                    user_preference=user_dimension or "terrain",
                    applied_mode="2.5d" if core.mode == "2.5d" else core.mode,
                    detail="决策含地形但无可绑定 raster-dem 源 id——"
                           "scene 配置省略 terrain"))
                if core.mode == "2.5d":
                    # 地形是 2.5d 的唯一垂直通道：无源即退 2d
                    scene_config["mode"] = "2d"

    return RepresentationDecision(
        mode=core.mode,
        extrusion=core.extrusion,
        terrain=core.terrain and "terrain" in scene_config,
        user_dimension=user_dimension,
        user_dimension_respected=(user_dimension in ("", core.mode)),
        degradations=degradations[:4],
        reason_codes=core.reason_codes()[:8],
        recommended_pitch=core.recommended_pitch,
        recommended_bearing=core.recommended_bearing,
        recommended_exaggeration=core.recommended_exaggeration,
        camera_transition_ms=core.camera_transition_ms,
        scene_config=scene_config,
    )
