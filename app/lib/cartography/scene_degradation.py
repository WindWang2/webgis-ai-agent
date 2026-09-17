"""Scene degradation chain — 3d → 2.5d → 2d (ADR-0201 M6).

确定性降级：把"环境/媒介不允许当前场景档"的情形投影为**低一档**的场景
配置，并给出结构化披露（code + 信息损失声明）。绝不静默降级 —— 每一跳
都可被质量门与导出 sidecar 引用。

触发词表（复用 scene_planning reason codes，单源）：
- ``SCENE_MEDIUM_STATIC``：静态媒介（print/PDF/SVG）不能承载透视 3D。
- ``SCENE_TERRAIN_UNAVAILABLE``：声明了 terrain 但高程源不可用
  （TerrainTileError / raster-dem 源缺失 / 悬空 ref）。

信息损失声明（``info_loss``）：挤出高度通道在 3d→2.5d 跳变中不可承载；
地形起伏在 terrain 失效跳变中不可承载。2d 是终端档，不再降级。

纯函数：输入 spec 不被修改（COW），同输入必同输出。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

#: 降级披露码词表（质量门/导出 sidecar 消费）。
DEGRADE_CODES = (
    "SCENE_MEDIUM_STATIC",
    "SCENE_TERRAIN_UNAVAILABLE",
)

#: 静态媒介词表（与 scene_planning 的交互媒介集合互补）。
_STATIC_MEDIA = frozenset({"print", "export_pdf", "export_png", "export_svg"})


def effective_mode(spec: Optional[Dict[str, Any]]) -> str:
    """spec → 生效场景模式（缺失/非法 = 既有 2d 语义）。"""
    scene = (spec or {}).get("scene")
    if isinstance(scene, dict):
        mode = scene.get("mode")
        if mode in ("2d", "2.5d", "3d"):
            return mode
    return "2d"


def _degradation(code: str, detail: str, info_loss: str) -> Dict[str, str]:
    return {"code": code, "detail": detail, "info_loss": info_loss}


def degrade_scene(
    spec: Dict[str, Any],
    *,
    terrain_available: bool = True,
    medium: str = "interactive",
) -> Dict[str, Any]:
    """按环境约束降级场景配置（COW；返回 {mode, scene, degradations}）。

    ``terrain_available=False`` 表示 terrain 源经运行时核实不可用（渲染
    错误/源缺失）；"无 terrain 声明"不是失效，走各自的模式语义。
    """
    scene_in = spec.get("scene")
    scene = copy.deepcopy(scene_in) if isinstance(scene_in, dict) else {"mode": "2d"}
    degradations: List[Dict[str, str]] = []

    mode = scene.get("mode")
    if mode not in ("2d", "2.5d", "3d"):
        mode = "2d"
        scene["mode"] = "2d"

    static_medium = medium in _STATIC_MEDIA

    # ── 跳 1：静态媒介剥离透视（3d → 2.5d 或就地降相机）────────────────
    if mode == "3d" and static_medium:
        mode = "2.5d" if scene.get("terrain") is not None else "2d"
        scene["mode"] = mode
        scene.pop("camera", None)
        degradations.append(
            _degradation(
                "SCENE_MEDIUM_STATIC",
                f"medium={medium!r} cannot carry perspective 3D",
                "extrusion height channel not representable in static output",
            )
        )

    # ── 跳 2：地形源不可用（3d 保挤出失地形 / 2.5d → 2d）────────────────
    if scene.get("terrain") is not None and not terrain_available:
        scene["terrain"] = None
        if mode == "2.5d":
            mode = "2d"
            scene["mode"] = "2d"
            scene.pop("camera", None)
        degradations.append(
            _degradation(
                "SCENE_TERRAIN_UNAVAILABLE",
                "terrain source failed verification at render time",
                "terrain relief not representable; planar rendering",
            )
        )

    # ── 跳 3：静态媒介的透视清零（2.5d 晕渲允许，但零透视；2d 天然零透视，
    #    不注入也不披露 —— 终端档无物可降）────────────────────────────────
    if static_medium and mode != "2d":
        camera = scene.get("camera")
        if not isinstance(camera, dict):
            camera = {"pitch": 0, "bearing": 0}
            scene["camera"] = camera
            degradations.append(
                _degradation(
                    "SCENE_MEDIUM_STATIC",
                    "static medium: perspective camera pinned to nadir",
                    "perspective readability aid removed",
                )
            )
        elif camera.get("pitch"):
            camera["pitch"] = 0
            degradations.append(
                _degradation(
                    "SCENE_MEDIUM_STATIC",
                    "camera pitch zeroed for static medium",
                    "perspective readability aid removed",
                )
            )

    return {"mode": mode, "scene": scene, "degradations": degradations}
