"""相机动作的 user-wins 裁决（G3D04 M4/M6）。

M4 红线：**不得偷改用户镜头**。agent 相机动作（fly_camera）在与用户
相机选择冲突时必须 typed 抑制（``user_wins_suppressed``），不是静默
跳过、更不是覆盖。

判定输入（全部既有权威面，零新真相）：

1. **IR 相机锁**：Requirement IR ``scene_camera`` 锁（用户显式 pitch/
   bearing 选择）→ 对应轴 agent 永久抑制，直到用户 remove_lock。
2. **Interaction IR viewport**：用户 ``viewport_settled``（含 pitch/
   bearing）与当前 MapSpec ``view`` 的偏离——用户已手动调镜头但 spec
   尚未提交 → 整体抑制（agent 不得把镜头拉回）。

相机变化**不失效分析结果**（视域与镜头无关）——那是 staleness 的
职责边界，本模块只裁相机。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

#: 相机轴词表。
CAMERA_AXES = ("pitch", "bearing", "center", "zoom")

#: viewport 与 spec view 的数值容差（浮点/插值噪声带）。
_EPS_PITCH = 0.5
_EPS_BEARING = 0.5
_EPS_ZOOM = 0.01
_EPS_CENTER = 1e-4


class CameraPlan(BaseModel):
    """相机动作裁决（apply=可下发 SetViewIntent；suppressed=typed 抑制）。"""

    model_config = {"extra": "forbid"}

    schema_version: str = "scene3d_camera.v1"
    status: str                                  # apply | user_wins_suppressed
    reason_code: str = ""                        # SCENE_CAMERA_* 词表
    detail: str = Field("", max_length=200)
    suppressed_axes: List[str] = Field(default_factory=list, max_length=4)
    view: Dict[str, Any] = Field(default_factory=dict)   # 可下发的 SetView 载荷
    user_viewport: Optional[Dict[str, Any]] = None       # 审计面：用户当前镜头

    @property
    def suppressed(self) -> bool:
        return self.status == "user_wins_suppressed"


def _matches_preference(axis: str, requested: Any, preference: Dict[str, Any]) -> bool:
    """请求值是否等于用户显式偏好（数值容差内）。"""
    want = _num(preference.get(axis))
    got = _num(requested)
    if want is None or got is None:
        return False
    tolerance = _EPS_PITCH if axis == "pitch" else _EPS_BEARING
    return abs(got - want) <= tolerance


def _request_matches_viewport(requested: Dict[str, Any], viewport: Dict[str, Any]) -> bool:
    """请求的**全部相机轴**与用户当前 viewport 等值（center 容差内）。"""
    if not requested:
        return False
    for axis, tolerance in (("pitch", _EPS_PITCH), ("bearing", _EPS_BEARING),
                            ("zoom", _EPS_ZOOM)):
        if axis in requested:
            want = _num(viewport.get(axis))
            got = _num(requested[axis])
            if want is None or got is None or abs(got - want) > tolerance:
                return False
    if "center" in requested:
        vp_center = viewport.get("center")
        if isinstance(vp_center, (list, tuple)) and len(vp_center) >= 2:
            if _center_differs(requested["center"], vp_center):
                return False
        else:
            return False    # 用户 viewport 带 center 请求才谈得上同步
    return True


def _num(value: Any) -> Optional[float]:
    try:
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _center_differs(a: Any, b: Any) -> bool:
    if not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple)):
        return False
    if len(a) < 2 or len(b) < 2:
        return False
    ax, ay = _num(a[0]), _num(a[1])
    bx, by = _num(b[0]), _num(b[1])
    if None in (ax, ay, bx, by):
        return False
    return abs(ax - bx) > _EPS_CENTER or abs(ay - by) > _EPS_CENTER


def user_viewport_diverges(
    interaction_snapshot: Optional[Dict[str, Any]],
    spec_view: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """用户 viewport 与 spec view 的偏离检测（返回偏离的 viewport，无偏离 None）。

    只有**相机语义字段**参与（center/zoom/pitch/bearing）；bounds 是
    派生面不参与。数值比较带噪声容差。
    """
    if not isinstance(interaction_snapshot, dict):
        return None
    viewport = interaction_snapshot.get("viewport")
    if not isinstance(viewport, dict):
        return None
    spec = spec_view if isinstance(spec_view, dict) else {}
    diverged = False
    # 用户 viewport 携带的相机字段即用户镜头状态；spec 缺该轴（用户调了
    # 但未提交）或数值偏离（> 容差）都算"用户镜头活跃"。
    vp_pitch, sp_pitch = _num(viewport.get("pitch")), _num(spec.get("pitch"))
    if vp_pitch is not None and (sp_pitch is None
                                 or abs(vp_pitch - sp_pitch) > _EPS_PITCH):
        diverged = True
    vp_bearing, sp_bearing = _num(viewport.get("bearing")), _num(spec.get("bearing"))
    if vp_bearing is not None and (sp_bearing is None
                                   or abs(vp_bearing - sp_bearing) > _EPS_BEARING):
        diverged = True
    vp_zoom, sp_zoom = _num(viewport.get("zoom")), _num(spec.get("zoom"))
    if vp_zoom is not None and (sp_zoom is None
                                or abs(vp_zoom - sp_zoom) > _EPS_ZOOM):
        diverged = True
    vp_center = viewport.get("center")
    if isinstance(vp_center, (list, tuple)) and len(vp_center) >= 2:
        # 与标量轴同语义：spec 缺 center（用户平移未提交）也算活跃
        if _center_differs(vp_center, spec.get("center")) or not isinstance(
                spec.get("center"), (list, tuple)):
            diverged = True
    return viewport if diverged else None


def plan_camera(
    requested: Dict[str, Any],
    *,
    interaction_snapshot: Optional[Dict[str, Any]] = None,
    spec_view: Optional[Dict[str, Any]] = None,
    camera_locks: Optional[List[str]] = None,
    user_camera: Optional[Dict[str, Any]] = None,
) -> CameraPlan:
    """裁决一次 agent 相机动作（纯函数）。

    Args:
        requested: 请求的相机载荷（pitch/bearing/center/zoom 子集）。
        interaction_snapshot: ``project_interaction_snapshot`` 产物。
        spec_view: 当前 MapSpec ``view`` dict。
        camera_locks: IR ``scene_camera`` 锁的值列表（"pitch"/"bearing"）。
        user_camera: IR SceneSpec 的用户显式相机偏好
            （{pitch, bearing}）——锁定的轴上，**等于用户偏好值的下发是
            执行用户自己的选择**（user-wins 一致），偏离值才是偷改。

    Returns:
        :class:`CameraPlan`——``status=apply`` 时 ``view`` 是净化的
        SetViewIntent 载荷；被锁轴剔除后的空动作也按 suppressed 报。
    """
    locks = {str(v) for v in (camera_locks or []) if str(v) in ("pitch", "bearing")}
    preference = user_camera if isinstance(user_camera, dict) else {}
    clean: Dict[str, Any] = {}
    for axis in CAMERA_AXES:
        if axis in requested and requested[axis] is not None:
            clean[axis] = requested[axis]

    # ── 轴级锁：偏离用户显式偏好的下发才被锁（等值=执行用户选择） ──
    suppressed_axes = sorted(
        axis for axis in ("pitch", "bearing")
        if axis in clean and axis in locks
        and not _matches_preference(axis, clean[axis], preference))

    # ── 整体抑制：用户镜头已偏离 spec（手动调节未提交） ─────────────────
    # 例外（review P2-2）：请求值与用户**当前 viewport** 逐轴等值 = 把
    # spec 同步到用户镜头（user-wins 一致动作），放行并标注 sync 语义。
    diverged = user_viewport_diverges(interaction_snapshot, spec_view)

    if diverged is not None and clean:
        if _request_matches_viewport(clean, diverged):
            remaining = {a: v for a, v in clean.items() if a not in locks}
            if remaining:
                return CameraPlan(
                    status="apply",
                    reason_code="SCENE_CAMERA_SYNC_TO_USER_VIEWPORT",
                    detail="请求值与用户当前 viewport 等值——按用户镜头同步 spec"
                           "（user-wins 一致动作，非程序性运镜）",
                    suppressed_axes=sorted(set(clean) & {"pitch", "bearing"} & locks),
                    view=remaining,
                    user_viewport=diverged,
                )
        return CameraPlan(
            status="user_wins_suppressed",
            reason_code="SCENE_CAMERA_USER_VIEWPORT_ACTIVE",
            detail="用户 viewport 与 spec 偏离（手动调镜头未落 spec）——"
                   "agent 相机动作被 user-wins 抑制；如需程序性运镜，"
                   "先与用户确认或等用户镜头回同步",
            suppressed_axes=sorted(set(clean) & {"pitch", "bearing"}),
            view={},
            user_viewport=diverged,
        )
    if suppressed_axes:
        remaining = {a: v for a, v in clean.items() if a not in suppressed_axes}
        if remaining:
            return CameraPlan(
                status="apply",
                reason_code="SCENE_CAMERA_AXES_LOCKED_PARTIAL",
                detail=f"用户相机锁命中 {suppressed_axes}，已剔除；其余轴照常下发",
                suppressed_axes=suppressed_axes,
                view=remaining,
            )
        return CameraPlan(
            status="user_wins_suppressed",
            reason_code="SCENE_CAMERA_AXES_LOCKED",
            detail=f"请求轴 {sorted(clean)} 全部被用户 scene_camera 锁保护",
            suppressed_axes=suppressed_axes,
            view={},
        )
    if not clean:
        return CameraPlan(
            status="user_wins_suppressed",
            reason_code="SCENE_CAMERA_EMPTY_REQUEST",
            detail="无可下发的相机字段",
            view={},
        )
    return CameraPlan(
        status="apply", reason_code="SCENE_CAMERA_OK",
        detail="无用户相机冲突，可下发 SetViewIntent", view=clean,
    )
