"""Multiscale scene planning — 2D / 2.5D / 3D deterministic decision (ADR-0199 M1).

目的/数据/媒介驱动的场景规划：把**已核实的证据**（elevation 证据、height 属性
证据、几何类型、媒介、动效偏好）投影为一个确定性的 SceneDecision。

词表（与 DEGRADATION/QUALITY 模块共享，单源在此）：
- ``2d``   平面地图（无地形、无挤出）。
- ``2.5d`` 地形/晕渲呈现（terrain 或 hillshade），要素自身无垂直挤出。
- ``3d``   要素垂直挤出（fill-extrusion 高度通道激活）；terrain 可选共呈。

fail-closed 铁律（Oracle）：
- 无 height 属性证据 → 绝不产出挤出决策（不伪造默认高度）。
- 无 elevation 证据 → 绝不产出 terrain 决策（不虚构地形）。
- 静态媒介（print/PDF/SVG）→ 绝不 3d（透视比例尺不可靠 + SVG 不可矢量化），
  但允许 2.5d 晕渲（经典地貌制图）。

纯函数：同输入必同输出；无 I/O、无全局状态。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# 模式词表与垂直夸张硬上限的契约口径单源在 mapspec_schema（v1.4）。
from app.lib.cartography.mapspec_schema import (
    MAX_TERRAIN_EXAGGERATION,
    SCENE_MODES,
)

SceneMode = Literal["2d", "2.5d", "3d"]

#: 交互媒介（允许透视/动效）。
_INTERACTIVE_MEDIA = frozenset({"interactive", "screen"})

#: 超过该值时垂直尺度失真必须披露（1.5 是既有前端默认，恰在阈值上）。
EXAGGERATION_DISCLOSURE_THRESHOLD = 1.5

#: 产品级相机安全带（≠ MapSpec 硬上限 85；比 StoryMap 叙事上限 60 收敛）。
SAFE_PITCH_3D = 50.0
SAFE_BEARING_3D = -15.0

#: 场景决策 reason code 词表（确定性、可回放、可被质量门/降级引用）。
REASON_CODES = (
    "SCENE_EXTRUSION_NO_HEIGHT_EVIDENCE",
    "SCENE_TERRAIN_NO_ELEVATION_EVIDENCE",
    "SCENE_EXTRUSION_REQUIRES_POLYGON",
    "SCENE_MEDIUM_STATIC",
    "SCENE_MODE_3D_EVIDENCED",
    "SCENE_MODE_2_5D_TERRAIN",
    "SCENE_MODE_2D_BASELINE",
    "SCENE_REDUCED_MOTION",
    "SCENE_EXAGGERATION_DISTORTS_SCALE",
    "SCENE_EXAGGERATION_CLAMPED",
    "SCENE_ANALYSIS_MEASUREMENT_PREFERRED",
)


class SceneReason(BaseModel):
    """一条决策理由（结构化、可审计；禁止自由文本代替 code）。"""

    code: str
    detail: str


class SceneIntent(BaseModel):
    """场景规划输入 —— 只承载**已核实**的证据与显式意图，不承载猜测。

    调用方（工具/规划器）负责证据核实：``has_height_attribute_evidence`` 必须
    来自真实字段采样（如 extrusion_model.analyze_height_field_distribution 的
    valid>0 结论），不得默认 True。
    """

    model_config = {"extra": "forbid"}

    purpose: Literal[
        "exploration", "analysis", "communication", "storytelling", "comparison", "monitoring"
    ] = "analysis"
    medium: Literal["interactive", "screen", "print", "export_png", "export_pdf", "export_svg"] = (
        "interactive"
    )
    motion: Literal["full", "reduced"] = "full"
    geometry_kinds: List[Literal["point", "line", "polygon", "raster"]] = Field(
        default_factory=list
    )
    feature_count: Optional[int] = None
    #: 要素 height 属性字段证据（数值有效样本 > 0 的已核实结论）。
    has_height_attribute_evidence: bool = False
    #: DEM/terrain 源证据（已注册 raster-dem ref 或已验证 tile 源）。
    has_elevation_evidence: bool = False
    #: 显式垂直挤出意图（用户/产品计划）。
    vertical_extrusion_intent: bool = False
    #: 显式地形意图（用户/产品计划）。
    terrain_intent: bool = False
    #: 请求的垂直夸张系数（建议值；越界钳制并披露）。
    exaggeration_request: Optional[float] = None


class SceneDecision(BaseModel):
    """场景规划输出 —— 确定性、可回放、证据可追溯。"""

    mode: SceneMode
    extrusion: bool
    terrain: bool
    recommended_pitch: float
    recommended_bearing: float
    recommended_exaggeration: float = 1.0
    camera_transition_ms: int = 800
    reasons: List[SceneReason] = Field(default_factory=list)

    def reason_codes(self) -> List[str]:
        return [r.code for r in self.reasons]


def plan_scene(intent: SceneIntent) -> SceneDecision:
    """SceneIntent → SceneDecision 的确定性投影（决策表，无副作用）。"""
    reasons: List[SceneReason] = []
    geometry = set(intent.geometry_kinds or [])

    # ── 证据门（fail-closed：先裁证据，再谈意图）─────────────────────────
    wants_extrusion = intent.vertical_extrusion_intent
    if wants_extrusion and not intent.has_height_attribute_evidence:
        wants_extrusion = False
        reasons.append(
            SceneReason(
                code="SCENE_EXTRUSION_NO_HEIGHT_EVIDENCE",
                detail="extrusion intent present but no verified height attribute "
                "evidence; refusing to fabricate heights (fail-closed)",
            )
        )
    if wants_extrusion and "polygon" not in geometry:
        wants_extrusion = False
        reasons.append(
            SceneReason(
                code="SCENE_EXTRUSION_REQUIRES_POLYGON",
                detail="extrusion is a polygon semantics channel; "
                f"geometry_kinds={sorted(geometry)} has no polygon",
            )
        )

    wants_terrain = intent.terrain_intent
    if wants_terrain and not intent.has_elevation_evidence:
        wants_terrain = False
        reasons.append(
            SceneReason(
                code="SCENE_TERRAIN_NO_ELEVATION_EVIDENCE",
                detail="terrain intent present but no verified DEM/elevation "
                "evidence; refusing to invent relief (fail-closed)",
            )
        )

    # ── 媒介门（静态媒介绝不透视 3D；晕渲 2.5d 合法）────────────────────
    static_medium = intent.medium not in _INTERACTIVE_MEDIA
    if wants_extrusion and static_medium:
        wants_extrusion = False
        reasons.append(
            SceneReason(
                code="SCENE_MEDIUM_STATIC",
                detail=f"medium={intent.medium!r} cannot carry perspective 3D "
                "(unreliable scale + non-vectorizable); degrading extrusion",
            )
        )

    # ── 模式裁决（挤出 ⇒ 3d；仅地形 ⇒ 2.5d；否则 2d）────────────────────
    if wants_extrusion:
        mode: SceneMode = "3d"
        reasons.append(
            SceneReason(
                code="SCENE_MODE_3D_EVIDENCED",
                detail="extrusion evidenced (height attribute verified + polygon "
                "geometry + interactive medium)",
            )
        )
    elif wants_terrain:
        mode = "2.5d"
        reasons.append(
            SceneReason(
                code="SCENE_MODE_2_5D_TERRAIN",
                detail="terrain evidenced; features stay flat (relief rendering only)",
            )
        )
    else:
        mode = "2d"
        reasons.append(
            SceneReason(
                code="SCENE_MODE_2D_BASELINE",
                detail="no evidenced vertical channel; baseline planar map",
            )
        )

    # ── 相机推荐 ──────────────────────────────────────────────────────────
    if mode == "3d":
        pitch, bearing = SAFE_PITCH_3D, SAFE_BEARING_3D
    elif mode == "2.5d":
        # 静态媒介零透视；交互媒介给轻透视增强地形可读性。
        pitch, bearing = (0.0, 0.0) if static_medium else (20.0, 0.0)
    else:
        pitch, bearing = 0.0, 0.0

    # ── 垂直夸张（默认诚实 1.0；请求值越界钳制 + 失真披露）──────────────
    exaggeration = 1.0
    if wants_terrain and intent.exaggeration_request is not None:
        req = float(intent.exaggeration_request)
        if req <= 0:
            req = 1.0
        clamped = min(req, MAX_TERRAIN_EXAGGERATION)
        if clamped != req:
            reasons.append(
                SceneReason(
                    code="SCENE_EXAGGERATION_CLAMPED",
                    detail=f"exaggeration request {req} clamped to {MAX_TERRAIN_EXAGGERATION}",
                )
            )
        exaggeration = clamped
    if wants_terrain and exaggeration > EXAGGERATION_DISCLOSURE_THRESHOLD:
        reasons.append(
            SceneReason(
                code="SCENE_EXAGGERATION_DISTORTS_SCALE",
                detail=f"exaggeration {exaggeration} distorts vertical scale; "
                "must be disclosed alongside any scale representation",
            )
        )

    # ── 动效（reduced motion 是可访问性，不是降维）──────────────────────
    transition_ms = 0 if intent.motion == "reduced" else 800
    if intent.motion == "reduced":
        reasons.append(
            SceneReason(
                code="SCENE_REDUCED_MOTION",
                detail="reduced motion requested; camera transitions disabled "
                "(mode unchanged)",
            )
        )

    # ── 分析型目的的测量偏好披露（不改变模式，只披露读数风险）──────────
    if mode == "3d" and intent.purpose in ("analysis", "comparison"):
        reasons.append(
            SceneReason(
                code="SCENE_ANALYSIS_MEASUREMENT_PREFERRED",
                detail="3D perspective degrades area/distance reading; prefer 2D "
                "view for measurement tasks (disclosed, not forced)",
            )
        )

    return SceneDecision(
        mode=mode,
        extrusion=wants_extrusion,
        terrain=wants_terrain,
        recommended_pitch=pitch,
        recommended_bearing=bearing,
        recommended_exaggeration=exaggeration,
        camera_transition_ms=transition_ms,
        reasons=reasons,
    )


def decision_to_scene_config(decision: SceneDecision) -> Dict[str, Any]:
    """SceneDecision → MapSpec v1.4 ``scene`` 配置投影（M2 消费）。

    terrain 源 id 由调用方绑定（此处只投决策面，不指名数据源 —— 单一数据面
    纪律：sources 才是数据事实源）。
    """
    cfg: Dict[str, Any] = {"mode": decision.mode}
    if decision.terrain:
        cfg["terrain"] = {
            "exaggeration": decision.recommended_exaggeration,
            "vertical_unit": "m",
        }
    if decision.reason_codes():
        cfg["reason_code"] = decision.reason_codes()[0]
    if decision.recommended_pitch:
        cfg["camera"] = {
            "pitch": decision.recommended_pitch,
            "bearing": decision.recommended_bearing,
            "transition_ms": decision.camera_transition_ms,
        }
    return cfg
