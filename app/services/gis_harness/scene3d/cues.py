"""3D 表达 cue 解析（G3D04 M2）——确定性、双语、保守。

红线（M2）：

- **默认不推断 3D**："分布情况/密度/热力"类话语无 3D cue → ``dimension``
  保持未表达；3D 只来自显式词面（3d/三维/立体/白模/地形起伏…）。
- **纯函数**：同一话语必得同一 cue 集；无 IO、无 LLM、无状态。
- **词面即证据**：cue 只记录"用户说了什么"，不记录"数据是否允许"——
  证据核实是 ``scene3d.evidence`` 的职责，两层绝不混淆。

产出 :class:`Scene3DCues`（值对象），由 build.py 投影进 Requirement IR
``SceneSpec``（genesis 派生 + edit cue patch 两条路径共用本模块）。
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from pydantic import BaseModel

# ── 词面（双语，保守高精度优先） ─────────────────────────────────────

_DIMENSION_3D_RE = re.compile(r"3\s*d|三维|3\s*维|立体")
_DIMENSION_2D_RE = re.compile(r"2\s*d|二维|平面(地图|视图|模式)|俯视(平面)?|top[- ]?down")
_DIMENSION_25D_RE = re.compile(r"2\.5\s*d|晕渲|山体阴影(地图)?|hillshade|relief shading")
_TERRAIN_RE = re.compile(
    r"地形|地势起伏|地形起伏|起伏|高程模型|晕渲|山体阴影|dem\b|terrain|relief")
# "立体地形/三维地形"同时命中 3D 与地形——正确语义（3d + terrain），无需排除。
_EXTRUSION_RE = re.compile(
    r"白模|建筑(物)?(立体|挤出|拉伸|高度)模型?|立体建筑|(立体)?拉伸|挤出|"
    r"extrusion|building.*(3d|height|extrud)")
_EXAGGERATION_RE = re.compile(
    r"(?P<qualifier>垂直|高程|地形)?夸张(?:系数|地形|起伏)?"
    r"(?:改成|改为|调(?:整)?到?|设为|为|至|调整至)?\s*"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*倍?")
_PITCH_RE = re.compile(
    r"俯仰|低角度|贴近地面看|仰视|鸟瞰|俯视角?|pitch", re.IGNORECASE)
_PITCH_NUM_RE = re.compile(
    r"(?:俯仰(?:角)?|pitch)\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:度|°|deg(?:rees)?)?",
    re.IGNORECASE)
# 方位词最长优先（"东南"不得被 "东" 截断匹配）
_COMPASS_ALT = r"(东北|东南|西北|西南|正东|正南|正西|正北|东|南|西|北)"
_BEARING_FROM_RE = re.compile(
    r"从" + _COMPASS_ALT + r"边?(方[向位])?\s*[看望拍观察]")
_BEARING_TO_RE = re.compile(r"(?:朝|面向|看向)" + _COMPASS_ALT + r"\s*方?向?")
_ROTATE_RE = re.compile(r"旋转到|转到|换个角度|转动(地图|视角)|rotate (?:to )?(?:the )?view")
_MEDIUM_PRINT_RE = re.compile(r"打印|印刷|print\b", re.IGNORECASE)
_MEDIUM_PDF_RE = re.compile(r"pdf", re.IGNORECASE)
_MEDIUM_PNG_RE = re.compile(r"png", re.IGNORECASE)
_MEDIUM_SVG_RE = re.compile(r"svg", re.IGNORECASE)
_MEDIUM_STATIC_RE = re.compile(r"静态图|静态地图|static map")
# 分析目标（typed；与 SceneAnalysisGoalVocab 对齐）
_GOAL_RE = re.compile(r"视域|可视域|通视|可见(性|范围)|瞭望|viewshed|line[- ]of[- ]sight", re.IGNORECASE)
_SKYLINE_RE = re.compile(r"天际线|skyline", re.IGNORECASE)
_PROFILE_RE = re.compile(r"地形剖面|高程剖面|剖面图|terrain profile|elevation profile", re.IGNORECASE)
_LOS_RE = re.compile(r"通视|两点.*(可)?见|视线遮挡|line[- ]of[- ]sight", re.IGNORECASE)

# 方位词 → 罗盘方位角（bearing：0=北，顺时针）
_COMPASS: dict[str, float] = {
    "北": 0.0, "正北": 0.0, "东北": 45.0, "东": 90.0, "正东": 90.0,
    "东南": 135.0, "南": 180.0, "正南": 180.0, "西南": 225.0,
    "西": 270.0, "正西": 270.0, "西北": 315.0,
}


class Scene3DCues(BaseModel):
    """一次话语的 3D cue 抽取结论（值对象；``None``/空 = 该面无表达）。"""

    model_config = {"extra": "forbid"}

    dimension: str = ""                # "" | "2d" | "2.5d" | "3d"
    terrain_requested: Optional[bool] = None
    extrusion_requested: Optional[bool] = None
    exaggeration: Optional[float] = None
    camera_pitch: Optional[float] = None
    camera_bearing: Optional[float] = None   # 归一化到 [-180, 180]
    bearing_source: str = ""           # from_direction | to_direction | ""（审计面）
    medium: str = ""                   # "" | interactive 词表
    analysis_goals: Tuple[str, ...] = ()

    def has_any(self) -> bool:
        return bool(
            self.dimension or self.terrain_requested is not None
            or self.extrusion_requested is not None or self.exaggeration is not None
            or self.camera_pitch is not None or self.camera_bearing is not None
            or self.medium or self.analysis_goals
        )


def _norm_bearing(deg: float) -> float:
    """归一化到 [-180, 180]（MapSpec/MapLibre bearing 契约）。"""
    deg = deg % 360.0
    if deg > 180.0:
        deg -= 360.0
    return deg


def extract_scene3d_cues(query: str) -> Scene3DCues:
    """话语 → 3D cue 集（纯函数，确定性；无 cue → 全空，绝不推断）。"""
    q = (query or "").strip()
    ql = q.lower()
    if not q:
        return Scene3DCues()

    # ── 维度（互斥裁决：显式 3d > 显式 2.5d > 显式 2d；无词面 → ""） ──
    dimension = ""
    if _DIMENSION_3D_RE.search(ql):
        dimension = "3d"
    elif _DIMENSION_25D_RE.search(ql):
        dimension = "2.5d"
    elif _DIMENSION_2D_RE.search(ql):
        dimension = "2d"

    terrain: Optional[bool] = None
    if _TERRAIN_RE.search(ql):
        terrain = True
    elif dimension == "2d" and _DIMENSION_2D_RE.search(ql) and "地形" not in ql:
        terrain = False

    extrusion: Optional[bool] = None
    if _EXTRUSION_RE.search(ql):
        extrusion = True

    exaggeration: Optional[float] = None
    m = _EXAGGERATION_RE.search(ql)
    if m:
        try:
            exaggeration = float(m.group("value"))
        except (TypeError, ValueError):  # pragma: no cover — 正则保证数字
            exaggeration = None
        # 垂直/高程/地形夸张只作用于地形——带限定词即表达地形意图
        if m.group("qualifier") and terrain is None:
            terrain = True

    camera_pitch: Optional[float] = None
    m = _PITCH_NUM_RE.search(ql)
    if m:
        try:
            camera_pitch = min(85.0, max(0.0, float(m.group(1))))
        except (TypeError, ValueError):  # pragma: no cover — 正则保证数字
            camera_pitch = None
    # 无数字的俯仰/视角词面只表达"想调相机"意图——不在此造角度，由
    # 决策核按模式推荐档赋值（pitch_cue_present 保留意图存在性判定）。

    camera_bearing: Optional[float] = None
    bearing_source = ""
    m = _BEARING_FROM_RE.search(q)
    if m:
        camera_pos = _COMPASS.get(m.group(1))
        if camera_pos is not None:
            # "从西南方向看"：相机位于西南、看向东北 → 朝向 = 方位+180°
            camera_bearing = _norm_bearing(camera_pos + 180.0)
            bearing_source = "from_direction"
    if camera_bearing is None:
        m = _BEARING_TO_RE.search(q)
        if m:
            looking = _COMPASS.get(m.group(1))
            if looking is not None:
                camera_bearing = _norm_bearing(looking)
                bearing_source = "to_direction"

    # 媒介：显式格式词优先（pdf/png/svg 是比"静态"更具体的表达）
    medium = ""
    if _MEDIUM_PDF_RE.search(ql):
        medium = "export_pdf"
    elif _MEDIUM_PNG_RE.search(ql):
        medium = "export_png"
    elif _MEDIUM_SVG_RE.search(ql):
        medium = "export_svg"
    elif _MEDIUM_PRINT_RE.search(ql) or _MEDIUM_STATIC_RE.search(ql):
        medium = "print"

    goals: list[str] = []
    if _GOAL_RE.search(ql) or _LOS_RE.search(ql):
        goals.append("viewshed")
    if _SKYLINE_RE.search(ql):
        goals.append("skyline")
    if _PROFILE_RE.search(ql):
        goals.append("terrain_profile")
    if _LOS_RE.search(ql):
        goals.append("line_of_sight")

    return Scene3DCues(
        dimension=dimension,
        terrain_requested=terrain,
        extrusion_requested=extrusion,
        exaggeration=exaggeration,
        camera_pitch=camera_pitch,
        camera_bearing=camera_bearing,
        bearing_source=bearing_source,
        medium=medium,
        analysis_goals=tuple(dict.fromkeys(goals))[:4],
    )


def pitch_cue_present(cues: Scene3DCues, query: str = "") -> bool:
    """话语是否显式提及俯仰/视角调节（cue 抽取存在性判定，纯函数）。"""
    if query:
        return bool(_PITCH_RE.search(query.lower()) or _ROTATE_RE.search(query.lower()))
    return False


def rotation_cue_present(query: str) -> bool:
    return bool(_ROTATE_RE.search((query or "").lower()))
