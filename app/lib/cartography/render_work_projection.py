"""RenderWorkProjection —— MapSpec → versioned RenderWorkInput 纯投影
（F13，ADR-0214 D1；消费 R13 估工模型 ADR-0182）。

#1484 留下的缺口：``RenderWorkInput`` 与 ``render_formula.v1`` 只有估工
模型和 governor seam，没有任何从真实 MapSpec 生产输入的代码 —— render
细化通道在派发面恒缺席。本模块补上**生产投影**：

    MapSpec（desired state，唯一真相）
      → RenderWorkProjection（versioned 派生投影，绑 revision/fingerprint）
      → RenderWorkInput（既有形状，复用不重定义）
      → estimate_render / governor admission

纪律：

- **零扫描**：只读 spec 骨架（layers/sources/layout 的 ID/计数/元数据）
  与源级 ``profile.featureCount`` / ``imageSize``；绝不触碰 inlineData /
  GeoJSON 本体（大字节不进投影，更不进 agent context）；
- **确定性**：同 spec 必同投影（排序迭代；数值只来自公式系数与计数）；
- **单调性**：层/要素/标注/组件递增 ⇒ work 递增（与 render_formula.v1
  的单调性测试同款锁定）；
- **诚实披露**：源缺 profile 时按保守先验估并置 ``features_estimated``；
  词表外/不支持的图层类型进 ``unsupported_layer_types``。

边界：本模块不做准入裁决、不做缓存、不懂 session —— 缓存与供给在
:mod:`app.services.governor.render_projection`。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.lib.cartography.layer_capability import disclose_unsupported
from app.services.governor.render_budget import RenderWorkInput

#: 投影契约版本（演进时升位；消费方按版本解释字段）
PROJECTION_SCHEMA_VERSION = "render_work_projection.v1"

#: 源缺 profile.featureCount 时的每层保守要素先验（与 data-plane LoadPlan
#: 的保守档同量级；必须带 features_estimated 披露，绝不静默当 0 —— 0 会
#: 让准入面系统性低估大图层）。
UNKNOWN_FEATURES_PER_LAYER = 2_000

#: 默认画布先验（spec 无画布尺寸语义；导出 DPI 由调用方供给）。
DEFAULT_CANVAS_WIDTH = 1920
DEFAULT_CANVAS_HEIGHT = 1080

#: notes 披露上限（防异常 spec 撑爆投影载荷）。
_MAX_NOTES = 8
#: 投影可对账的最大图层数（超过按 maxFeatures 预算语义截断计数并披露）。
_MAX_COUNTED_LAYERS = 4096

_RASTER_SOURCE_TYPES = frozenset({"raster", "raster-dem"})


@dataclass(frozen=True)
class RenderWorkProjection:
    """versioned 渲染工作量投影（纯数据，可序列化）。"""

    schema_version: str
    mapspec_revision: int
    mapspec_fingerprint: str
    work_input: RenderWorkInput
    layers_total: int
    layers_visible: int
    #: 源缺 profile → 要素数按先验估算（诚实披露，绝不静默）
    features_estimated: bool
    #: 词表外/不支持的图层类型（稳定序去重）
    unsupported_layer_types: Tuple[str, ...]
    #: 有界披露（先验估算、截断、异常形状）
    notes: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        """有界 dict 投影（receipt 面；不含 RenderWorkInput 私有形状）。"""
        wi = self.work_input
        return {
            "schema_version": self.schema_version,
            "mapspec_revision": self.mapspec_revision,
            "mapspec_fingerprint": self.mapspec_fingerprint,
            "layers_total": self.layers_total,
            "layers_visible": self.layers_visible,
            "features_estimated": self.features_estimated,
            "unsupported_layer_types": list(self.unsupported_layer_types),
            "notes": list(self.notes),
            "work": {
                "layer_count": wi.layer_count,
                "feature_count": wi.feature_count,
                "label_count": wi.label_count,
                "source_count": wi.source_count,
                "canvas_pixels": wi.canvas_pixels,
                "raster_layer_count": wi.raster_layer_count,
                "chart_count": wi.chart_count,
                "floating_components": wi.floating_components,
                "export_dpi": wi.export_dpi,
            },
        }


def _count_labels(layer: Dict[str, Any]) -> int:
    """层标注预算：label 在场按 1 计 + zoomBands 档数（每档一组碰撞面）。"""
    label = layer.get("label")
    if not isinstance(label, dict):
        return 0
    bands = label.get("zoomBands")
    n = 1
    if isinstance(bands, list):
        n += len(bands)
    return n


def _source_feature_count(source: Any) -> Optional[int]:
    """源 → profile.featureCount（缺失/非法 → None，由调用方走先验）。"""
    if not isinstance(source, dict):
        return None
    profile = source.get("profile")
    if not isinstance(profile, dict):
        return None
    raw = profile.get("featureCount")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return max(0, int(raw))


def project_render_work(
    mapspec: Optional[dict],
    *,
    revision: int = 0,
    fingerprint: str = "",
    canvas_width: int = DEFAULT_CANVAS_WIDTH,
    canvas_height: int = DEFAULT_CANVAS_HEIGHT,
    export_dpi: Optional[float] = None,
) -> RenderWorkProjection:
    """MapSpec 骨架 → versioned RenderWorkProjection（纯函数）。

    ``mapspec=None``/空 spec = 空图投影（不抛错 —— 投影失败必须以空图
    保守值显式存在，绝不中断派发链）。
    """
    if not isinstance(mapspec, dict):
        mapspec = {}
    notes: List[str] = []
    features_estimated = False

    layers = [
        ly for ly in (mapspec.get("layers") or [])
        if isinstance(ly, dict)
    ]
    layers_declared = len(layers)
    truncated = False
    if len(layers) > _MAX_COUNTED_LAYERS:
        layers = layers[:_MAX_COUNTED_LAYERS]
        truncated = True
        notes.append(f"layers truncated at {_MAX_COUNTED_LAYERS}")

    sources = mapspec.get("sources")
    source_map: Dict[str, Any] = sources if isinstance(sources, dict) else {}
    visible_layers = [
        ly for ly in layers
        if not (isinstance(ly.get("visible"), bool) and ly.get("visible") is False)
    ]

    # 要素预算：逐可见层找 source 绑定 → profile.featureCount；缺失走
    # 先验并披露。同源多（子）层共享计数（计数属源，不按层重复累加 ——
    # 簇/heatmap/标注子层是同源的多投影而非多份数据）。source=""（设计内
    # 哨兵，background 等）无数据面 —— 跳过计数，绝不触发先验估算。
    feature_total = 0
    seen_source_ids: set = set()
    for ly in visible_layers:
        sid = str(ly.get("source") or "")
        if not sid:
            continue
        if sid in seen_source_ids:
            continue
        seen_source_ids.add(sid)
        count = _source_feature_count(source_map.get(sid))
        if count is None:
            features_estimated = True
            feature_total += UNKNOWN_FEATURES_PER_LAYER
        else:
            feature_total += count
    if features_estimated and len(notes) < _MAX_NOTES:
        notes.append(
            "feature counts estimated (source profile missing) at "
            f"{UNKNOWN_FEATURES_PER_LAYER}/layer prior"
        )

    label_total = sum(_count_labels(ly) for ly in visible_layers)

    # 栅格面：栅格/地形源逐个入账；imageSize 缺失按画布面积保守计。
    raster_layer_count = 0
    raster_pixels = 0
    for src in source_map.values():
        if not isinstance(src, dict) or src.get("type") not in _RASTER_SOURCE_TYPES:
            continue
        raster_layer_count += 1
        size = src.get("imageSize")
        if isinstance(size, list) and len(size) == 2 and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
            for v in size
        ):
            raster_pixels += int(float(size[0]) * float(size[1]))
        else:
            raster_pixels += canvas_width * canvas_height

    # 组件面：layout.components 的 enabled 组件；floating（placement.mode）
    # 与 chart 逐类计 —— 与前端 resolveMapComponents 的 floating 语义同源。
    layout = mapspec.get("layout")
    components = (
        layout.get("components") if isinstance(layout, dict) else None
    ) or []
    chart_count = 0
    floating_count = 0
    for comp in components:
        if not isinstance(comp, dict) or comp.get("enabled") is False:
            continue
        ctype = str(comp.get("type") or "")
        if "chart" in ctype:
            chart_count += 1
        placement = comp.get("placement")
        if isinstance(placement, dict) and placement.get("mode") == "floating":
            floating_count += 1

    if truncated and len(notes) < _MAX_NOTES:
        notes.append("layer count truncated for projection budget")

    work_input = RenderWorkInput(
        layer_count=len(visible_layers),
        feature_count=feature_total,
        label_count=label_total,
        source_count=len(source_map),
        pixel_width=max(0, int(canvas_width)),
        pixel_height=max(0, int(canvas_height)),
        chart_count=chart_count,
        floating_components=floating_count,
        raster_layer_count=raster_layer_count,
        raster_pixels=raster_pixels,
        export_dpi=export_dpi,
    )
    return RenderWorkProjection(
        schema_version=PROJECTION_SCHEMA_VERSION,
        mapspec_revision=max(0, int(revision or 0)),
        mapspec_fingerprint=str(fingerprint or ""),
        work_input=work_input,
        layers_total=layers_declared,
        layers_visible=len(visible_layers),
        features_estimated=features_estimated,
        unsupported_layer_types=tuple(disclose_unsupported(mapspec)),
        notes=tuple(notes[:_MAX_NOTES]),
    )


__all__ = [
    "PROJECTION_SCHEMA_VERSION",
    "UNKNOWN_FEATURES_PER_LAYER",
    "DEFAULT_CANVAS_WIDTH",
    "DEFAULT_CANVAS_HEIGHT",
    "RenderWorkProjection",
    "project_render_work",
]
