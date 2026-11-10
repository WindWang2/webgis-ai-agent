"""画幅有效文档解析器（G15 WP04）—— 主 MapSpec + 一帧有界差异 → 该帧有效文档。

位置说明：本模块依赖 feature_pages（services）与 temporal time_filter
（services），按仓库 lib↔services 分层纪律放 services/gis_harness/
frame_products/（与 temporal_products/time_filter.py 同门）。

纯函数、确定性、输入不可变（触达路径浅拷贝 —— 与 _apply_frame_overrides /
apply_time_window_to_doc 同纪律）。解析顺序即优先级：

1. ``layerOverrides``（MapSpecFrame 逐键 deep-merge —— 既有 atlas 语义，
   本模块是单一事实；publication_export 从这里导入）；
2. 产品数据绑定 ``where``（feature_pages where AST —— 与服务端分页过滤
   同词表同求值器）；
3. 产品数据绑定 ``time_window``（temporal [start,end) 窗口 —— 帧显式时间
   锁定，全局 temporal cursor 不参与帧解析）；
4. ``shared_expression``（共享分级 breaks/method/palette 写入绑定层
   legend_spec —— 每帧得到**同一份**分级，帧内数据子集绝不重新分级）。

不变量：
- 相邻画幅互不污染（每帧独立解析，绝不共享中间可变结构）；
- 同一 (主文档, frame_id, product) 重复解析得到逐位相同的有效文档与指纹；
- 独立图例（legend="independent"）的画幅不应用共享分级；
- 一帧解析失败 → typed FrameResolutionError（带 correction hint），
  绝不静默回退整幅主文档。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.lib.cartography.plan_ir import digest_of
from app.lib.cartography.publication_ir import resolve_effective_bounds
from app.services.feature_pages import evaluate_where, parse_where_param

__all__ = [
    "FrameResolutionError",
    "FrameResolution",
    "apply_frame_overrides",
    "resolve_frame_document",
    "resolve_effective_bounds",
    "frame_by_id",
    "product_slot_by_frame",
]


class FrameResolutionError(ValueError):
    """typed 解析失败（frame 缺席/绑定悬空/过滤词表非法）。"""

    def __init__(self, code: str, message: str, *, frame_id: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.frame_id = frame_id


@dataclass(frozen=True)
class FrameResolution:
    """一帧解析产物（refs-only 摘要 + 有效文档）。"""

    frame_id: str
    document: Dict[str, Any]
    #: 有界投影指纹（同输入 ⇒ 同指纹；用于渲染缓存/逐帧对账）。
    fingerprint: str
    #: 本帧生效的解析步骤（ordered：layer_overrides / where_filter /
    #: time_window / shared_expression）。
    applied: Tuple[str, ...] = ()
    #: 绑定层过滤后剩余要素数（None = 无过滤步骤或非内联源）。
    filtered_feature_count: Optional[int] = None
    disclosures: Tuple[str, ...] = ()


# ── 步骤 1：layerOverrides（publication_export._apply_frame_overrides 迁入，
#            数值语义逐字保留 —— 导出面保持旧名兼容既有调用方）。


def apply_frame_overrides(
    doc: Dict[str, Any], frame: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """帧 layerOverrides 逐键 deep-merge（R1-M5：未提及键保留）。

    返回轻拷贝文档（layers 列表浅拷贝 + 命中层浅拷贝）—— 不改写入参。
    """
    overrides = frame.get("layerOverrides") if isinstance(frame, dict) else None
    if not isinstance(overrides, dict) or not overrides:
        return doc
    out = dict(doc)
    layers = list(out.get("layers") or [])
    new_layers: List[Dict[str, Any]] = []
    for layer in layers:
        if not isinstance(layer, dict):
            new_layers.append(layer)
            continue
        ov = overrides.get(layer.get("id"))
        if not isinstance(ov, dict):
            new_layers.append(layer)
            continue
        merged = dict(layer)
        for k, v in ov.items():
            if k != "id":  # id 不允许覆写
                merged[k] = v
        new_layers.append(merged)
    out["layers"] = new_layers
    return out


# ── 步骤 2/3 的文档面辅助（触达路径浅拷贝）。


def _layer_source(doc: Dict[str, Any], layer_id: str) -> Optional[Dict[str, Any]]:
    """绑定层 → 其 source 条目（未命中返回 None）。"""
    layers_raw = doc.get("layers")
    layers = layers_raw if isinstance(layers_raw, list) else []
    target_sid: Optional[str] = None
    for layer in layers:
        if isinstance(layer, dict) and layer.get("id") == layer_id:
            sid = layer.get("source")
            target_sid = sid if isinstance(sid, str) else None
            break
    if target_sid is None:
        return None
    sources_raw = doc.get("sources")
    sources = sources_raw if isinstance(sources_raw, dict) else {}
    src = sources.get(target_sid)
    return src if isinstance(src, dict) else None


def _inline_features(src: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    payload = src.get("inlineData")
    if not isinstance(payload, dict):
        data_raw = src.get("data")
        payload = data_raw if isinstance(data_raw, dict) else {}
    raw = payload.get("features")
    if not isinstance(raw, list):
        return None
    return [f for f in raw if isinstance(f, dict)]


def _apply_where_filter(
    doc: Dict[str, Any], layer_id: str, where: Dict[str, Any]
) -> Tuple[Dict[str, Any], int]:
    """绑定层内联要素按 where AST 过滤（求值器 = feature_pages.evaluate_where）。

    非内联源/层缺席 → FrameResolutionError（绑定悬空不静默全通过）。
    """
    try:
        parsed = parse_where_param(_canon_json(where))
    except Exception as exc:  # noqa: BLE001 — typed 统一拒绝
        raise FrameResolutionError(
            "frame_where_invalid", f"where AST 非法：{exc}") from exc

    src = _layer_source(doc, layer_id)
    if src is None:
        raise FrameResolutionError(
            "frame_layer_unavailable",
            f"绑定层 {layer_id!r} 不存在或无数据源",
        )
    feats = _inline_features(src)
    if feats is None:
        raise FrameResolutionError(
            "frame_source_not_inline",
            f"层 {layer_id!r} 的源不是内联 FeatureCollection（ref 源先水合）",
        )
    kept = [f for f in feats if evaluate_where(parsed, f)]
    if len(kept) == len(feats):
        return doc, len(kept)
    out = _copy_with_filtered_source(doc, layer_id, kept)
    return out, len(kept)


def _copy_with_filtered_source(
    doc: Dict[str, Any], layer_id: str, kept: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """过滤后的文档（触达路径浅拷贝：顶层 + layers 命中层 + sources 命中源）。"""
    layers_raw = doc.get("layers")
    layers = layers_raw if isinstance(layers_raw, list) else []
    target_sid: Optional[str] = None
    for layer in layers:
        if isinstance(layer, dict) and layer.get("id") == layer_id:
            sid = layer.get("source")
            target_sid = sid if isinstance(sid, str) else None
            break
    out = dict(doc)
    sources_raw = doc.get("sources")
    sources = dict(sources_raw) if isinstance(sources_raw, dict) else {}
    if target_sid is not None and target_sid in sources:
        src = dict(sources[target_sid])
        payload = src.get("inlineData")
        if not isinstance(payload, dict):
            data_raw = src.get("data")
            payload = data_raw if isinstance(data_raw, dict) else {}
        payload_key = "inlineData" if isinstance(src.get("inlineData"), dict) else "data"
        src[payload_key] = {**payload, "features": kept}
        sources[target_sid] = src
    out["sources"] = sources
    return out


def _canon_json(payload: Any) -> str:
    import json

    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


# ── 步骤 4：共享分级写入绑定层 legend_spec。


def _apply_shared_expression(
    doc: Dict[str, Any], layer_id: str, shared: Dict[str, Any]
) -> Tuple[Dict[str, Any], Tuple[str, ...]]:
    """共享分级（breaks/method/palette/k）覆写绑定层 legend_spec。

    只在共享域键上覆写（breaks/method/palette/palette_colors/labels/k/
    domain 标记）—— 字段/单位/nodata 保留原 spec 语义。层无 legend_spec
    （如 basemap）→ 披露跳过，不伪造图例。
    """
    breaks = shared.get("breaks")
    if not isinstance(breaks, list) or len(breaks) < 2:
        return doc, ("shared_expression_not_computed",)
    layers_raw = doc.get("layers")
    layers = layers_raw if isinstance(layers_raw, list) else []
    new_layers: List[Any] = []
    hit = False
    disclosures: List[str] = []
    for layer in layers:
        if not (isinstance(layer, dict) and layer.get("id") == layer_id
                and isinstance(layer.get("legend_spec"), dict)):
            new_layers.append(layer)
            continue
        legend = dict(layer["legend_spec"])
        legend["breaks"] = [float(b) for b in breaks]
        legend["method"] = str(shared.get("method") or legend.get("method") or "")
        legend["k"] = len(breaks) - 1
        if shared.get("palette"):
            legend["palette"] = str(shared["palette"])
        legend["shared_scope"] = True  # 渲染/图例可识别「同域分级」标记
        # 分级变了，标签与色带同步重算（与 build_graduated_spec 同格式 ——
        # palette_colors 与 breaks 类数不一致是图例/渲染失配源）。
        legend["labels"] = [
            _graduated_label(float(breaks[i]), float(breaks[i + 1]))
            for i in range(len(breaks) - 1)
        ]
        try:
            from app.lib.cartography.thematic_spec import resolve_thematic_colors

            n_classes = len(breaks) - 1
            lo = min(
                (float(b) for b in legend.get("breaks", [])), default=0.0)
            hi = max(
                (float(b) for b in legend.get("breaks", [])), default=1.0)
            colors = resolve_thematic_colors(
                str(legend.get("palette") or "Blues"), n_classes, breaks, lo, hi)
            if colors:
                legend["palette_colors"] = list(colors)
        except Exception:  # noqa: BLE001 — 色带重算失败保留原色（标签仍一致）
            pass
        hit = True
        # paint 同步重投影（spec_to_paint 是 legend→paint 的单一构造点 ——
        # 渲染器按 paint 上色；只改 legend 不改 paint 会让地图色与图例漂移）。
        try:
            from app.lib.cartography.thematic_spec import spec_to_paint

            style_method, _warn = spec_to_paint(legend)
            if style_method is not None:
                paint = dict(layer.get("paint") or {})
                paint["color"] = style_method
                new_layers.append({**layer, "legend_spec": legend,
                                   "paint": paint})
            else:
                new_layers.append({**layer, "legend_spec": legend})
        except Exception:  # noqa: BLE001 — paint 重投影失败不阻断图例盖章
            new_layers.append({**layer, "legend_spec": legend})
    if not hit:
        disclosures.append("shared_expression_layer_without_legend")
        return doc, tuple(disclosures)
    out = dict(doc)
    out["layers"] = new_layers
    return out, tuple(disclosures)


def _graduated_label(lo: float, hi: float) -> str:
    """与 thematic_spec._graduated_label 同格式的区间标签（lib 层轻实现）。"""
    def _fmt(v: float) -> str:
        if float(v).is_integer() and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.4g}"
    return f"{_fmt(lo)} – {_fmt(hi)}"


# ── 主入口。


def frame_by_id(document: Dict[str, Any], frame_id: str) -> Optional[Dict[str, Any]]:
    """layout.frames 里按 id 找画幅（身份 = id，不是下标）。"""
    layout_raw = document.get("layout")
    layout = layout_raw if isinstance(layout_raw, dict) else {}
    frames_raw = layout.get("frames")
    for f in (frames_raw if isinstance(frames_raw, list) else []):
        if isinstance(f, dict) and f.get("id") == frame_id:
            return f
    return None


def product_slot_by_frame(
    document: Dict[str, Any], frame_id: str
) -> Optional[Dict[str, Any]]:
    """frame_product.frames 里按 frame_id 找产品槽（无产品/未收录 → None）。"""
    product_raw = document.get("frame_product")
    product = product_raw if isinstance(product_raw, dict) else {}
    for f in (product.get("frames") or []):
        if isinstance(f, dict) and f.get("frame_id") == frame_id:
            return f
    return None


def _effective_fingerprint(frame_id: str, doc: Dict[str, Any]) -> str:
    """帧有效文档的有界投影指纹（结构面 + 过滤后要素计数，不含要素内容）。"""
    sources_raw = doc.get("sources")
    sources = sources_raw if isinstance(sources_raw, dict) else {}
    proj_sources: Dict[str, Any] = {}
    for key, src in sources.items():
        if not isinstance(src, dict):
            continue
        feats = _inline_features(src) if isinstance(src, dict) else None
        proj_sources[str(key)[:64]] = {
            "type": str(src.get("type") or "")[:24],
            "content_revision": src.get("content_revision"),
            "feature_count": len(feats) if feats is not None else None,
        }
    projection = {
        "frame_id": frame_id,
        "version": doc.get("version"),
        "sources": proj_sources,
        "layers": doc.get("layers"),
        "layout": doc.get("layout"),
        "thresholds": doc.get("thresholds"),
        "view": doc.get("view"),
    }
    return "frame-sha256:" + digest_of(projection)[:40]


def resolve_frame_document(
    document: Dict[str, Any],
    frame_id: str,
    *,
    apply_product_semantics: bool = True,
) -> FrameResolution:
    """主文档 + frame_id → 该画幅有效文档（确定性纯函数）。

    ``apply_product_semantics=False``：只应用 atlas 帧语义
    （layerOverrides/extent），产品绑定/共享分级不参与（atlas 旧路径）。
    """
    if not isinstance(document, dict):
        raise FrameResolutionError("document_invalid", "MapSpec 文档必须为对象")
    frame = frame_by_id(document, frame_id)
    if frame is None:
        raise FrameResolutionError(
            "frame_not_found",
            f"画幅 {frame_id!r} 不在 layout.frames（画幅身份先经帧登记建立）",
            frame_id=frame_id,
        )
    applied: List[str] = []
    disclosures: List[str] = []
    doc: Dict[str, Any] = document
    filtered_count: Optional[int] = None

    # 步骤 1：帧 layerOverrides（逐键 merge）。
    doc = apply_frame_overrides(doc, frame)
    if isinstance(frame.get("layerOverrides"), dict) and frame["layerOverrides"]:
        applied.append("layer_overrides")

    slot: Optional[Dict[str, Any]] = None
    if apply_product_semantics:
        slot = product_slot_by_frame(document, frame_id)
    if slot is not None:
        data = slot.get("data")
        if isinstance(data, dict):
            # 步骤 2：where 过滤（绑定层内联要素子集）。
            where = data.get("where")
            layer_id = str(data.get("layer_id") or "")
            if isinstance(where, dict) and where and layer_id:
                doc, kept = _apply_where_filter(doc, layer_id, where)
                filtered_count = kept
                applied.append("where_filter")
            # 步骤 3：显式时间窗（帧锁定语义；全局 cursor 不参与）。
            # 悬空绑定层 → typed 拒绝（产品路径不沿用 atlas 页的宽容回退）。
            if layer_id and _layer_source(doc, layer_id) is None:
                raise FrameResolutionError(
                    "frame_layer_unavailable",
                    f"绑定层 {layer_id!r} 不存在（帧数据绑定悬空）",
                    frame_id=frame_id,
                )
            tw = data.get("time_window")
            if isinstance(tw, dict) and tw.get("start") and tw.get("end"):
                from app.services.gis_harness.temporal_products.time_filter import (
                    apply_time_window_to_doc,
                )

                doc = apply_time_window_to_doc(doc, tw)
                applied.append("time_window")
            # 步骤 4：共享分级（独立图例画幅跳过）。
            product_raw = document.get("frame_product")
            product = product_raw if isinstance(product_raw, dict) else {}
            shared = product.get("shared_expression")
            if (
                isinstance(shared, dict)
                and slot.get("legend") in (None, "shared", "shared_plus_frame")
                and layer_id
            ):
                doc, step_disc = _apply_shared_expression(doc, layer_id, shared)
                applied.append("shared_expression")
                disclosures.extend(step_disc)

    return FrameResolution(
        frame_id=frame_id,
        document=doc,
        fingerprint=_effective_fingerprint(frame_id, doc),
        applied=tuple(applied),
        filtered_feature_count=filtered_count,
        disclosures=tuple(disclosures),
    )
