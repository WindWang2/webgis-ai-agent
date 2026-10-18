"""Representation-at-Scale 协议（方向 5 / C2）—— 语义缩放的单一裁决层。

定位与所有权（不新建第二真相）：

- **带边界 / dense 阈值 / 候选优先序**：``scale_rules.SCALE_TIERS`` 唯一
  权威（与 ``label_plan.DEFAULT_ZOOM_BANDS`` 双契约锁定）；
- **标注密度 / 字号比**：``label_plan.DEFAULT_ZOOM_BANDS``（经
  ``layer.label.zoomBands`` 消费）唯一权威，本模块只读引用不复制；
- **场景 LOD（terrain/符号尺度）**：``scene_lod.LOD_BANDS`` 唯一权威，
  本模块只读投影（``lod_for_zoom``）；
- **几何×数量语义提示**（multipart/质心/栅格分辨率）：``gis.scale_semantics``
  唯一权威，本模块不重复阈值；
- 本模块唯一拥有的新事实：**表达协议词表**（``REPRESENTATION_MODES``）、
  ``RepresentationAtScale`` 决策工件、zoom 窗口计划
  （``RepresentationWindow``）及其确定性推导 / 归一化 / 查询函数。

协议链（全部确定性纯函数，零 IO，同输入恒同输出）::

    dataset descriptor（几何族 × 要素数 × 视口 × 媒介）+ user pins
        ↓ representation_at_scale()
    RepresentationAtScale（windows 计划 + 提示 + reason/evidence + 披露）
        ↓ project_mapspec_representation()（C3 落笔面）
    MapSpec layer.representation.windows / layer.visibility
        ↓ 前端 carto-ir/representation.ts + compiler/runtime（C4）
    MapLibre 原生 minzoom/maxzoom 门（零 per-frame JS 切换）
        ↓ export 按 export zoom 求活跃窗口（C6 parity）

纪律：
- **视觉聚合 ≠ 分析聚合**：cluster/heatmap/grid 窗口带
  ``AggregationSemantics(scope="visual_only")``，任何窗口都不得被当作
  分析结果口径；披露面固定文案锁定。
- **user-wins**：``user_pin`` 合法即整程生效（单一窗口覆盖 [0,24)）；
  不合法（几何族不适用 / 不在词表）→ 保留计算计划 + 结构化披露，
  绝不静默改写用户选择、也绝不静默丢 pin。
- **artifact 诚实降级**：``aggregate_grid`` / ``generalized`` 需要
  预计算数据工件（capability seam）。工件不可用时不发明低质量算法，
  回退到渲染端可执行表达并披露 ``REPR.FALLBACK.NO_ARTIFACT``。
- zoom 手势不是 MapSpec mutation：窗口计划在 authoring 期一次性声明，
  runtime 只按声明切换表达。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from app.lib.cartography.label_plan import DEFAULT_ZOOM_BANDS
from app.lib.cartography.scale_rules import (
    SCALE_TIERS,
    ScaleTier,
    resolve_scale_tier,
)
from app.lib.cartography.scene_lod import lod_for_zoom
from app.lib.cartography.symbology_v2 import compute_pixel_density

#: 表达模式词表（协议核心词表；跨语言锁定：schema 常量、contract_manifest、
#: frontend contract-manifest.ts 三向一致，漂移即测试红）。
#: - raw：逐要素原生表达（点圆/线/面/栅格原样）；
#: - point_cluster：MapLibre 源级聚合（簇圆+计数，渲染端可执行）；
#: - visual_heatmap：密度热力（渲染端可执行；视觉密度，非分析 KDE）；
#: - aggregate_grid：预计算格网聚合（artifact-backed，capability seam）；
#: - generalized：预计算泛化工件（artifact-backed，capability seam）；
#: - hidden：该窗口不渲染（detail visibility 门，必带披露与 reason）。
REPRESENTATION_MODES: Tuple[str, ...] = (
    "raw",
    "point_cluster",
    "visual_heatmap",
    "aggregate_grid",
    "generalized",
    "hidden",
)

#: 渲染端可自行执行的模式（无需后端工件）。
RENDERER_EXECUTABLE_MODES: Tuple[str, ...] = (
    "raw",
    "point_cluster",
    "visual_heatmap",
)

#: artifact-backed 模式（capability seam：须调用方声明可用）。
ARTIFACT_MODES: Tuple[str, ...] = ("aggregate_grid", "generalized")

#: MapModel 表达 id → 协议模式（SCALE_TIERS.point_dense_candidates 词表
#: 与本协议词表的桥；point_overlay/proportional_symbol 的原生表达=raw）。
_MAP_MODEL_TO_MODE: Dict[str, str] = {
    "visual_heatmap": "visual_heatmap",
    "point_cluster": "point_cluster",
    "aggregate_grid": "aggregate_grid",
    "point_overlay": "raw",
    "proportional_symbol": "raw",
}

#: zoom 域（MapLibre 契约，与 carto-ir/visibility.ts MAPLIBRE_ZOOM_* 一致）。
ZOOM_DOMAIN_MIN = 0.0
ZOOM_DOMAIN_MAX = 24.0

#: 每层窗口数硬上限（归一化后实际 ≤4+合并；上限防病理文档放大）。
MAX_WINDOWS = 8

#: 视觉聚合固定披露（分析口径隔离 —— 文案锁定，测试断言在场）。
VISUAL_AGGREGATION_DISCLOSURE = (
    "cluster/heatmap/grid 为视觉聚合（scope=visual_only）——"
    "簇计数/热度不是分析结果口径，分析请走分析通道"
)


class AggregationSemantics(BaseModel):
    """聚合窗口的统计语义声明（防「视觉聚合误当分析结果」）。

    本协议只产生视觉聚合：``method=count``（簇/格网内要素计数）、
    ``scope=visual_only``、无面积归一（``area_normalization="none"``）。
    分析聚合（rate/密度归一等）属分析通道，永不出现在表达窗口里。
    """

    method: str = "count"
    scope: str = "visual_only"
    denominator: str = "features_per_cell"
    area_normalization: str = "none"


class RepresentationWindow(BaseModel):
    """zoom 半开窗口 [min_zoom, max_zoom) × 表达模式（协议原子）。"""

    min_zoom: float
    max_zoom: float
    mode: str
    #: 该窗口触发的原因码（窗口级，非整层）。
    reason_codes: List[str] = Field(default_factory=list)


class RepresentationAtScale(BaseModel):
    """一次表达计划裁决工件（zoom 无关的完整计划 + 参考点快照）。

    ``windows`` 覆盖 [0,24) 连续半开窗口（``normalize_windows`` 保证）；
    ``*_at_reference`` 字段是参考 zoom 处的快照（advisory，方便 QA/报告
    单值消费；权威仍是 windows 计划）。
    """

    geometry_family: str
    feature_count: int
    windows: List[RepresentationWindow] = Field(default_factory=list)
    #: 参考带（authoring/导出 zoom 所在带）。
    tier: str = ""
    #: 参考 zoom 处的活跃模式（= windows 查询结果）。
    mode_at_reference: str = "raw"
    #: 边界泛化档建议（scale_rules 权威值的只读投影）。
    boundary_detail: str = "standard"
    #: 标注密度比（label_plan 权威值的只读投影 @ 参考 zoom）。
    label_top_ratio: float = 1.0
    #: 点符号尺度比（scene_lod 权威值的只读投影 @ 参考 zoom）。
    symbol_size_ratio: float = 1.0
    #: terrain 源分辨率上限建议（scene_lod 只读投影；2D 图不消费）。
    terrain_maxzoom_hint: Optional[int] = None
    #: 栅格金字塔/降采样建议（raster 族；advisory）。
    raster_overview_hint: Optional[str] = None
    #: 视觉聚合语义（含聚合模式窗口时在场）。
    aggregation: Optional[AggregationSemantics] = None
    #: 结构 zoom 门投影（min_zoom/max_zoom/hints —— C3 落笔面）。
    visibility_gate: Dict[str, Any] = Field(default_factory=dict)
    reason_codes: List[str] = Field(default_factory=list)
    evidence: Dict[str, Any] = Field(default_factory=dict)
    disclosures: List[str] = Field(default_factory=list)
    #: 用户 pin（有效=单一窗口；无效=保留记录+披露，见模块头 user-wins）。
    user_pin: Optional[str] = None


# ── 几何族归一 ───────────────────────────────────────────────────────────


def normalize_geometry_family(geometry: str) -> str:
    """几何描述 → 协议族（point/line/polygon/raster/unknown）。

    与 ``DatasetProfile.geometry_kind`` / ``scale_actions`` 的 pointish
    判定同口径：``point``/``multi_point`` 前缀 → point；line/polygon/
    raster 原样；其余 unknown（诚实：不猜）。
    """
    g = (geometry or "").strip().lower()
    if g.startswith("point") or g == "multi_point":
        return "point"
    if g.startswith("line") or g == "multi_line":
        return "line"
    if g.startswith("polygon") or g in ("multi_polygon", "multipolygon"):
        return "polygon"
    if g == "raster":
        return "raster"
    return "unknown"


# ── 窗口归一化 / 查询（后端 QA 与 C6 导出共用）─────────────────────────


def normalize_windows(
    windows: Sequence[Any],
) -> Optional[List[RepresentationWindow]]:
    """窗口列表 → 规范形（排序/钳制/合并同模式邻窗），非法 → None。

    合法性：每窗 mode ∈ 词表、有限数值域、钳制后 min<max；窗口合并后
    必须连续覆盖 [0,24)（缺口的计划无从消费 —— 诚实拒绝）。输出恒为
    合并后的最小窗口序列（≤MAX_WINDOWS，超限 → None）。
    """
    if not windows:
        return None
    rows: List[Tuple[float, float, str, List[str]]] = []
    for w in windows:
        if isinstance(w, RepresentationWindow):
            mn, mx, mode, codes = w.min_zoom, w.max_zoom, w.mode, list(w.reason_codes)
        elif isinstance(w, dict):
            mn = w.get("min_zoom")
            mx = w.get("max_zoom")
            mode = w.get("mode")
            codes = list(w.get("reason_codes") or [])
        else:
            return None
        if not isinstance(mode, str) or mode not in REPRESENTATION_MODES:
            return None
        if not isinstance(mn, (int, float)) or not isinstance(mx, (int, float)):
            return None
        mn_f, mx_f = float(mn), float(mx)
        # 钳制到 [0,24] 后复核 min<max（文档契约「钳制后 min<max」）：整体
        # 越界窗（两端都 <0 或都 >24，如 [-10,-5] / [25,30]）钳制后退化为
        # 零宽 [0,0)/[24,24)，与 [8,8] 同为空窗，必须拒；部分越界仍合法
        # （[-5,8]→[0,8]、[20,30]→[20,24]）。
        cmn = max(ZOOM_DOMAIN_MIN, min(ZOOM_DOMAIN_MAX, mn_f))
        cmx = max(ZOOM_DOMAIN_MIN, min(ZOOM_DOMAIN_MAX, mx_f))
        if not (cmn < cmx):
            return None
        rows.append((cmn, cmx, mode, codes))
    rows.sort(key=lambda r: r[0])
    if rows[0][0] > ZOOM_DOMAIN_MIN or rows[-1][1] < ZOOM_DOMAIN_MAX:
        return None
    # 连续性：每窗起点必须等于前窗终点（钳制后 [0,24) 无缺口）。
    merged: List[List[Any]] = []
    cursor = ZOOM_DOMAIN_MIN
    for mn, mx, mode, codes in rows:
        if abs(mn - cursor) > 1e-9:
            return None
        cursor = mx
        if merged and merged[-1][2] == mode:
            merged[-1][1] = mx
            merged[-1][3] = _merge_codes(merged[-1][3], codes)
        else:
            merged.append([mn, mx, mode, list(codes)])
    if len(merged) > MAX_WINDOWS:
        return None
    return [
        RepresentationWindow(min_zoom=mn, max_zoom=mx, mode=mode, reason_codes=codes)
        for mn, mx, mode, codes in merged
    ]


def _merge_codes(a: List[str], b: List[str]) -> List[str]:
    out = list(a)
    for c in b:
        if c not in out:
            out.append(c)
    return out[:8]


def representation_mode_at_zoom(
    windows: Sequence[Any], zoom: float
) -> Optional[str]:
    """窗口计划 + zoom → 活跃模式（半开区间；越界钳到首/末窗）。

    非法计划 → None（消费方回退层缺省 + 证据披露，不猜）。
    """
    normalized = normalize_windows(windows)
    if normalized is None:
        return None
    z = min(ZOOM_DOMAIN_MAX - 1e-9, max(ZOOM_DOMAIN_MIN, float(zoom)))
    for w in normalized:
        if w.min_zoom <= z < w.max_zoom:
            return w.mode
    return normalized[-1].mode


def _window_reason(tier_name: str, mode: str) -> List[str]:
    return [f"REPR.WINDOW.{tier_name.upper()}.{mode.upper()}"]


# ── 计划推导 ─────────────────────────────────────────────────────────────


def _point_mode_for_tier(
    tier: ScaleTier,
    *,
    feature_count: int,
    density: float,
    artifact_modes: "frozenset[str]",
) -> Tuple[str, List[str]]:
    """点族在单个 tier 窗口内的模式（与 scale_actions 判据同口径）。

    判据与 :func:`scale_rules.scale_actions` 一致（dense = 数量超带阈值
    或像素密度 > 8.0）；dense 时按带候选优先序取首个可执行模式（渲染端
    可执行 ∪ 可用 artifact）；全部不可执行 → raw + 诚实降级码（绝不
    hidden —— 可读内容不因表达切换失败而消失）。
    """
    dense = feature_count > tier.dense_threshold or density > 8.0
    if not dense:
        return "raw", _window_reason(tier.name, "raw")
    for candidate in tier.point_dense_candidates:
        mode = _MAP_MODEL_TO_MODE.get(candidate)
        if mode is None:
            continue
        if mode in artifact_modes or mode in RENDERER_EXECUTABLE_MODES:
            return mode, _window_reason(tier.name, mode)
    return "raw", _window_reason(tier.name, "raw") + [
        "REPR.FALLBACK.NO_EXECUTABLE_CANDIDATE"]


def representation_at_scale(
    *,
    geometry: str,
    feature_count: int = 0,
    reference_zoom: float = 11.0,
    viewport_px_w: int = 1280,
    viewport_px_h: int = 720,
    user_pin: Optional[str] = None,
    artifact_modes: Sequence[str] = (),
    allow_detail_visibility_hidden: bool = False,
) -> RepresentationAtScale:
    """dataset descriptor + user pin → 表达计划（确定性）。

    参数：
    - ``reference_zoom``：参考 zoom（authoring/导出视角；只影响快照字段
      与带内判定的密度口径 —— 窗口计划本身覆盖全 zoom 域）；
    - ``artifact_modes``：调用方声明**已实际可用**的 artifact 模式
      （capability seam；未声明却需要的窗口诚实降级）；
    - ``allow_detail_visibility_hidden``：线/面族的 detail-visibility 门
      是否允许落为 ``hidden`` 窗口（默认 False —— 只提示不整层藏；
      打开时 hidden 窗口必带披露）。该开关由落笔面（converter）按
      「用户未显式要求该层始终在场」等语境决定，协议层不猜。

    user_pin：在词表且几何族适用 → 单一窗口整程生效（user-wins）；
    否则保留计划 + 披露（不静默改、不静默丢）。
    """
    family = normalize_geometry_family(geometry)
    count = max(0, int(feature_count)) if isinstance(feature_count, (int, float)) else 0
    artifacts = frozenset(m for m in artifact_modes if m in ARTIFACT_MODES)
    tier = resolve_scale_tier(reference_zoom)
    codes: List[str] = [f"REPR.TIER.{tier.name.upper()}"]
    disclosures: List[str] = []
    windows: List[RepresentationWindow] = []
    aggregation: Optional[AggregationSemantics] = None
    pin_record: Optional[str] = user_pin if user_pin else None

    # user_pin 合法性：词表内 + 几何族可执行/可用。
    pin_effective = False
    if user_pin is not None:
        if user_pin not in REPRESENTATION_MODES:
            disclosures.append(
                f"user_pin={user_pin!r} 不在表达词表 —— 保留 pin 记录，"
                "按计算计划执行（REPR.PIN.UNKNOWN）")
            codes.append("REPR.PIN.UNKNOWN")
        elif not _mode_allowed_for_family(user_pin, family, artifacts):
            disclosures.append(
                f"user_pin={user_pin!r} 不适用于几何族 {family}（或 artifact "
                "不可用）—— 保留 pin 记录，按计算计划执行（REPR.PIN.INCOMPATIBLE）")
            codes.append("REPR.PIN.INCOMPATIBLE")
        else:
            pin_effective = True
            codes.append("REPR.PIN.HONORED")
            windows = [RepresentationWindow(
                min_zoom=ZOOM_DOMAIN_MIN, max_zoom=ZOOM_DOMAIN_MAX,
                mode=user_pin, reason_codes=["REPR.PIN.HONORED"],
            )]

    if not pin_effective:
        if family == "point":
            density = compute_pixel_density(count, viewport_px_w, viewport_px_h)
            for t in SCALE_TIERS:
                mode, why = _point_mode_for_tier(
                    t,
                    feature_count=count,
                    density=density,
                    artifact_modes=artifacts,
                )
                if "REPR.FALLBACK.NO_EXECUTABLE_CANDIDATE" in why:
                    disclosures.append(
                        f"{t.name} 带无可用聚合候选（artifact={sorted(artifacts) or '无'}）"
                        "——回退 raw 表达")
                windows.append(RepresentationWindow(
                    min_zoom=t.min_zoom, max_zoom=t.max_zoom, mode=mode,
                    reason_codes=why,
                ))
        elif family in ("line", "polygon"):
            # C5 保守策略：矢量非点族默认 raw（choropleth/street 原生即
            # 合法世界级表达）；artifact 泛化可用且数量超带阈值 → generalized；
            # detail-visibility 门仅在显式开启时可落 hidden（必带披露）。
            for t in SCALE_TIERS:
                overloaded = count > t.dense_threshold
                # street_detail_minzoom 语义只存在于线族（街网细节），且只在
                # 该带自身 street hint > 0 的带生效（street 带 hint=0 —— 用户
                # 已 zoom 到街区，隐藏街网等于空图；超载留给 QA 警告不藏层）。
                # 面族（choropleth）整层隐藏会反转为空图 —— 永不自动 hidden。
                hideable = (
                    family == "line"
                    and allow_detail_visibility_hidden
                    and t.visibility_hints.get("street_detail_minzoom", 0.0) > 0.0
                )
                # 泛化工件是**粗尺度替代品**（预抽稀版本）——street 带（最细
                # 尺度）用它等于「放大了看低清版」，语义错误；street 带超载
                # 留 raw + QA 警告码。
                artifact_fit = overloaded and "generalized" in artifacts and t.name != "street"
                if artifact_fit:
                    mode, why = "generalized", _window_reason(t.name, "generalized")
                elif overloaded and hideable:
                    mode, why = "hidden", _window_reason(t.name, "hidden")
                    disclosures.append(
                        f"{t.name} 带线要素 {count} 超阈值 {t.dense_threshold}"
                        "——detail visibility 门（隐藏至更细 zoom），非数据删除")
                elif overloaded:
                    mode, why = "raw", _window_reason(t.name, "raw")
                    codes.append(f"REPR.OVERLOADED.{t.name.upper()}")
                else:
                    mode, why = "raw", _window_reason(t.name, "raw")
                windows.append(RepresentationWindow(
                    min_zoom=t.min_zoom, max_zoom=t.max_zoom, mode=mode,
                    reason_codes=why,
                ))
        elif family == "raster":
            windows = [RepresentationWindow(
                min_zoom=ZOOM_DOMAIN_MIN, max_zoom=ZOOM_DOMAIN_MAX,
                mode="raw", reason_codes=["REPR.WINDOW.RASTER.RAW"],
            )]
        else:
            windows = [RepresentationWindow(
                min_zoom=ZOOM_DOMAIN_MIN, max_zoom=ZOOM_DOMAIN_MAX,
                mode="raw", reason_codes=["REPR.WINDOW.UNKNOWN_FAMILY.RAW"],
            )]
            disclosures.append(f"几何族 {geometry!r} 未识别 —— 保守 raw 表达")

    normalized = normalize_windows(windows) or [RepresentationWindow(
        min_zoom=ZOOM_DOMAIN_MIN, max_zoom=ZOOM_DOMAIN_MAX,
        mode="raw", reason_codes=["REPR.FALLBACK.NORMALIZE_FAILED"],
    )]
    if any(w.mode != "raw" for w in normalized):
        aggregation = AggregationSemantics()
        disclosures.append(VISUAL_AGGREGATION_DISCLOSURE)

    mode_at_ref = next(
        (w.mode for w in normalized
         if w.min_zoom <= min(ZOOM_DOMAIN_MAX - 1e-9,
                              max(ZOOM_DOMAIN_MIN, reference_zoom)) < w.max_zoom),
        normalized[-1].mode,
    )
    label_band = next(
        (b for b in DEFAULT_ZOOM_BANDS
         if b.min_zoom <= min(ZOOM_DOMAIN_MAX - 1e-9,
                              max(ZOOM_DOMAIN_MIN, reference_zoom)) < b.max_zoom),
        DEFAULT_ZOOM_BANDS[-1],
    )
    lod = lod_for_zoom(reference_zoom)
    density_ev = compute_pixel_density(count, viewport_px_w, viewport_px_h)

    raster_hint = None
    if family == "raster":
        raster_hint = "overview_pyramid_preferred"

    return RepresentationAtScale(
        geometry_family=family,
        feature_count=count,
        windows=normalized,
        tier=tier.name,
        mode_at_reference=mode_at_ref,
        boundary_detail=tier.boundary_detail,
        label_top_ratio=label_band.top_ratio,
        symbol_size_ratio=float(lod.get("symbol_scale", 1.0)),
        terrain_maxzoom_hint=int(lod["terrain_maxzoom"]) if "terrain_maxzoom" in lod else None,
        raster_overview_hint=raster_hint,
        aggregation=aggregation,
        visibility_gate=_visibility_gate_projection(normalized),
        reason_codes=codes,
        evidence={
            "reference_zoom": reference_zoom,
            "viewport_px": [viewport_px_w, viewport_px_h],
            "pixel_density": density_ev,
            "artifact_modes": sorted(artifacts),
            "tier_dense_threshold": tier.dense_threshold,
        },
        disclosures=disclosures[:8],
        user_pin=pin_record,
    )


def _mode_allowed_for_family(mode: str, family: str, artifacts: frozenset) -> bool:
    """pin/窗口模式对几何族的适用性（确定性词表判定）。"""
    if mode in ("aggregate_grid", "generalized"):
        return mode in artifacts and family != "raster"
    if mode == "point_cluster" or mode == "visual_heatmap":
        return family == "point"
    if mode == "hidden":
        return family in ("line", "polygon")
    return True  # raw：任何族


def _visibility_gate_projection(
    windows: List[RepresentationWindow],
) -> Dict[str, Any]:
    """窗口计划 → 结构 zoom 门投影（C3 落笔面）。

    - 首窗 hidden → ``min_zoom`` = 首个非 hidden 窗起点（层在低 zoom
      不存在 —— MapLibre minzoom 原生门）；门源是线族 detail visibility
      时同时写 ``hints.street_detail_minzoom``（渲染端 hint 门控路径与
      结构门同值 —— 双路径一致由 semantic check 核验）；
    - 末窗 hidden → ``max_zoom`` = 末个非 hidden 窗终点；
    - 无 hidden 窗 → 空门（计划由 per-mode 层的门表达，层本身全程存在）。
    """
    visible_windows = [w for w in windows if w.mode != "hidden"]
    if not visible_windows:
        # 全域 hidden（病理计划）：诚实空窗标记，消费方披露不渲染。
        return {"min_zoom": ZOOM_DOMAIN_MAX, "max_zoom": ZOOM_DOMAIN_MAX}
    first = visible_windows[0]
    last = visible_windows[-1]
    gate: Dict[str, Any] = {}
    if first.min_zoom > ZOOM_DOMAIN_MIN:
        gate["min_zoom"] = first.min_zoom
        # 线族 detail 门的标准来源（与 SCALE_TIERS 街道细节语义同源）：
        # 门值落在带分界（14.0）即 street_detail_minzoom 语义。
        street_tier = next(
            (t for t in SCALE_TIERS
             if t.visibility_hints.get("street_detail_minzoom") == first.min_zoom),
            None,
        )
        if street_tier is not None:
            gate["hints"] = {"street_detail_minzoom": first.min_zoom}
    if last.max_zoom < ZOOM_DOMAIN_MAX:
        gate["max_zoom"] = last.max_zoom
    return gate


# ── 导出裁决（C6：固定 zoom 的一次决策；前端孪生同语义）────────────────


def zoom_gate_of_layer(layer: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """spec 层 dict → 结构 zoom 门（与 frontend carto-ir/visibility.ts 同语义）。

    - 显式 ``visibility.min_zoom``/``max_zoom`` 钳制 [0,24]；
    - 门控 hint（``street_detail_minzoom``）作为下限参与 max 合成；
    - min ≥ max 的病态门 → 空门（渲染器无从表达空窗口 —— 诚实开放）；
    - 非数值 hint / 未知键忽略（前端 ignoredHints 披露；此处只取门值）。
    """
    vis = layer.get("visibility") if isinstance(layer.get("visibility"), dict) else {}
    if not vis:
        return {"min_zoom": None, "max_zoom": None}

    def _clamp(v: Any) -> Optional[float]:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        fv = float(v)
        if fv != fv or fv in (float("inf"), float("-inf")):  # NaN/inf fail-closed
            return None
        return min(ZOOM_DOMAIN_MAX, max(ZOOM_DOMAIN_MIN, fv))

    explicit_min = _clamp(vis.get("min_zoom"))
    explicit_max = _clamp(vis.get("max_zoom"))
    floors: List[float] = []
    hints = vis.get("hints") if isinstance(vis.get("hints"), dict) else {}
    for key, value in hints.items():
        if key == "street_detail_minzoom":
            clamped = _clamp(value)
            if clamped is not None:
                floors.append(clamped)

    floor_candidates = [*floors] + ([explicit_min] if explicit_min is not None else [])
    min_zoom = max(floor_candidates) if floor_candidates else None
    max_zoom = explicit_max
    if (
        min_zoom is not None
        and max_zoom is not None
        and min_zoom >= max_zoom
    ):
        return {"min_zoom": None, "max_zoom": None}
    return {"min_zoom": min_zoom, "max_zoom": max_zoom}


def export_adjudicate_layer(layer: Dict[str, Any], zoom: float) -> str:
    """导出裁决：层在固定 zoom 的去留与表达（与前端孪生逐条对齐）。

    返回词表：
    - ``"render"``：按声明层型渲染；
    - ``"render_heatmap"``：活跃窗为热力（circle 层按热力近似渲染 ——
      孪生的 heatmap 分支自带 layer_approximated_heatmap 披露）；
    - ``"gated"``：结构 zoom 门不含该 zoom（layer_gated_by_zoom 披露）；
    - ``"hidden"``：表达窗口 hidden / 显式隐藏（layer_representation_hidden）。
    """
    if not isinstance(layer, dict):
        return "render"
    layout = layer.get("layout") if isinstance(layer.get("layout"), dict) else {}
    if layout.get("visibility") == "none" or layer.get("visible") is False:
        # W4 既有语义（孪生静默跳过）—— 本裁决面返回 hidden 供诊断统一。
        return "hidden"
    gate = zoom_gate_of_layer(layer)
    if gate["min_zoom"] is not None and zoom < gate["min_zoom"]:
        return "gated"
    if gate["max_zoom"] is not None and zoom >= gate["max_zoom"]:
        return "gated"
    windows = (layer.get("representation") or {}).get("windows") \
        if isinstance(layer.get("representation"), dict) else None
    if windows:
        mode = representation_mode_at_zoom(windows, zoom)
        if mode == "hidden":
            return "hidden"
        if mode == "visual_heatmap" and layer.get("type") == "circle":
            return "render_heatmap"
    return "render"


def export_zoom_of_mapspec(mapspec: Dict[str, Any], default: float = 8.0) -> float:
    """导出 zoom = spec.view.zoom（authored；与前端 options.zoom??view.zoom
    缺省 8 同口径）。非有限数值 fail-closed 到 default。"""
    view = mapspec.get("view") if isinstance(mapspec, dict) else None
    raw = view.get("zoom") if isinstance(view, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    fv = float(raw)
    return fv if fv == fv and abs(fv) != float("inf") else default


# ── MapSpec 落笔投影（C3）───────────────────────────────────────────────


def project_mapspec_representation(
    decision: RepresentationAtScale,
) -> Optional[Dict[str, Any]]:
    """RepresentationAtScale → ``layer.representation`` 声明 dict。

    只有计划**非平凡**（存在非 raw 窗口）才返回声明 —— 平凡 raw 层
    落笔等于零信息（旧 spec 行为不变原则）。输出可直接 json 序列化。
    """
    if all(w.mode == "raw" for w in decision.windows):
        return None
    return {
        "windows": [
            {
                "min_zoom": w.min_zoom,
                "max_zoom": w.max_zoom,
                "mode": w.mode,
                "reason_codes": list(w.reason_codes),
            }
            for w in decision.windows
        ],
    }


def tier_of_zoom(zoom: float) -> str:
    """zoom → 带名（QA/报告便捷面；权威仍是 resolve_scale_tier）。"""
    return resolve_scale_tier(zoom).name


__all__ = [
    "REPRESENTATION_MODES",
    "RENDERER_EXECUTABLE_MODES",
    "ARTIFACT_MODES",
    "AggregationSemantics",
    "RepresentationWindow",
    "RepresentationAtScale",
    "VISUAL_AGGREGATION_DISCLOSURE",
    "ZOOM_DOMAIN_MIN",
    "ZOOM_DOMAIN_MAX",
    "MAX_WINDOWS",
    "normalize_geometry_family",
    "normalize_windows",
    "representation_mode_at_zoom",
    "representation_at_scale",
    "project_mapspec_representation",
    "zoom_gate_of_layer",
    "export_adjudicate_layer",
    "export_zoom_of_mapspec",
    "tier_of_zoom",
]
