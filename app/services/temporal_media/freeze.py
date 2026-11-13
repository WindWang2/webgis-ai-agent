"""WP04 — 源 / 表达 / 地理窗口冻结。

一次读冻结（复用 G26 ``_frozen_document`` 的 CAS 口径）：记录 MapSpec 修订、
文档指纹、temporal 绑定指纹、图层表达/图例/标签指纹、地理窗口、渲染器与
字体版本。每一帧只在这份冻结副本上做时间过滤，不逐帧 mutation 主会话游标；
发布前复检修订/文档指纹，中途变化一律中止，绝不发布混合 revision 的作品。
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List, Optional

from .capabilities import layer_support, probe_rsvg
from .contracts import RENDERER_ID, RENDERER_VERSION, TemporalMediaError

_FONT_CACHE: Optional[str] = None


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def font_version() -> str:
    """服务端字体版本（尽力探测；失败如实 unknown，不伪造）。"""
    global _FONT_CACHE
    if _FONT_CACHE is not None:
        return _FONT_CACHE
    import subprocess

    try:
        proc = subprocess.run(
            ["fc-match", "-f", "%{family}|%{style}", "sans-serif"],
            capture_output=True, timeout=5, check=False)
        value = (proc.stdout or b"").decode("utf-8", "replace").strip()[:80]
        _FONT_CACHE = value or "unknown"
    except (OSError, subprocess.SubprocessError):
        _FONT_CACHE = "unknown"
    return _FONT_CACHE


def _style_payload(document: Dict[str, Any]) -> Dict[str, Any]:
    """冻结表达面：图层 paint/legend_spec/label + layout 组件（不含 temporal）。"""
    layers = []
    for layer in document.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        layers.append({
            "id": layer.get("id"),
            "type": layer.get("type"),
            "paint": layer.get("paint"),
            "layout": layer.get("layout"),
            "legend_spec": layer.get("legend_spec"),
            "raster_style": layer.get("raster_style"),
            "filter": layer.get("filter"),
            "minzoom": layer.get("minzoom"),
            "maxzoom": layer.get("maxzoom"),
        })
    layout = document.get("layout") if isinstance(document.get("layout"), dict) else {}
    return {"layers": layers, "layout": layout, "view": document.get("view")}


def _iter_coordinates(value: Any):
    """递归收集几何坐标（点/线/面/多几何），带深度与数量上限。"""
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and all(isinstance(v, (int, float))
                                   and not isinstance(v, bool) for v in value[:2]):
            yield float(value[0]), float(value[1])
            return
        for child in value[:4096]:
            yield from _iter_coordinates(child)


def _all_feature_extent(document: Dict[str, Any]) -> Optional[List[float]]:
    """冻结文档**全部**内联要素的并集范围：所有帧共用，绝不逐帧重算。"""
    xs: List[float] = []
    ys: List[float] = []
    sources = document.get("sources") if isinstance(document.get("sources"), dict) else {}
    for src in sources.values():
        if not isinstance(src, dict):
            continue
        payload = src.get("inlineData") or src.get("data")
        if not isinstance(payload, dict):
            continue
        for feature in (payload.get("features") or [])[:20000]:
            if not isinstance(feature, dict):
                continue
            for x, y in _iter_coordinates((feature.get("geometry") or {}).get("coordinates")):
                xs.append(x)
                ys.append(y)
    if len(xs) < 2:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def _viewport(document: Dict[str, Any],
              raster_bindings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """固定地理窗口（P0）：**必须**给出可传给编译器的 4 元素 extent。

    编译器在 bounds=None 时会按「当前帧文档」重新推导范围；时序过滤后每帧
    要素不同 → 逐帧平移/缩放。因此冻结时从**全部**要素（含静态栅格声明范围）
    求一次并集，作为所有帧共同的 bounds；显式 layout frame extent 优先。
    """
    layout = document.get("layout") if isinstance(document.get("layout"), dict) else {}
    frames = layout.get("frames")
    if isinstance(frames, list):
        for frame in frames:
            if isinstance(frame, dict) and isinstance(frame.get("extent"), list) \
                    and len(frame["extent"]) == 4:
                return {"source": "layout.frames[].extent",
                        "extent": [float(v) for v in frame["extent"]]}
    view = document.get("view") if isinstance(document.get("view"), dict) else {}
    boxes: List[List[float]] = []
    features_extent = _all_feature_extent(document)
    if features_extent:
        boxes.append(features_extent)
    for binding in (raster_bindings or {}).values():
        bounds = binding.get("source_bounds") if isinstance(binding, dict) else None
        if isinstance(bounds, list) and len(bounds) == 4:
            boxes.append([float(v) for v in bounds])
    if boxes:
        extent = [min(b[0] for b in boxes), min(b[1] for b in boxes),
                  max(b[2] for b in boxes), max(b[3] for b in boxes)]
        if extent[2] <= extent[0]:
            extent[2] = extent[0] + 1e-6
        if extent[3] <= extent[1]:
            extent[3] = extent[1] + 1e-6
        return {"source": "frozen_all_features_union", "extent": extent,
                "center": view.get("center"), "zoom": view.get("zoom"),
                "bearing": view.get("bearing")}
    raw = view.get("bounds")
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        return {"source": "spec.view.bounds", "extent": [float(v) for v in raw],
                "center": view.get("center"), "zoom": view.get("zoom")}
    return {"source": "unfixable", "extent": None,
            "center": view.get("center"), "zoom": view.get("zoom")}


def build_frozen(
    session_id: str,
    document: Dict[str, Any],
    revision: int,
    *,
    raster_bindings: Optional[Dict[str, Any]] = None,
    unbound_raster: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """纯函数：从一次读到的文档构造冻结快照（测试可直接注入合成文档）。"""
    temporal = document.get("temporal")
    if not isinstance(temporal, dict) or not temporal.get("enabled"):
        raise TemporalMediaError(
            "NO_TEMPORAL_BINDING",
            "session has no enabled temporal_view.v1 binding; bind a temporal "
            "view before requesting animation media",
            status_code=409,
        )
    frames = temporal.get("frames")
    if not isinstance(frames, list) or len(frames) < 2:
        raise TemporalMediaError(
            "INSUFFICIENT_OBSERVATIONS",
            "at least two real observation buckets are required for animation",
            status_code=409,
        )
    for frame in frames:
        if not isinstance(frame, dict) or not all(
                isinstance(frame.get(k), str) and frame[k]
                for k in ("id", "label", "start", "end")):
            raise TemporalMediaError(
                "INVALID_TEMPORAL_CONTRACT",
                "temporal frame is missing id/label/start/end", status_code=422)
    if str(temporal.get("schema_version") or "") != "temporal_view.v1":
        raise TemporalMediaError(
            "UNKNOWN_TEMPORAL_VERSION",
            f"unsupported temporal schema_version {temporal.get('schema_version')!r}",
            status_code=422)

    # 深拷：冻结后即使调用方（live 会话/用户平移换样式）继续改动原文档，
    # 本作品的源、表达、窗口与后续每帧仍来自同一快照，不拼半片。
    document = copy.deepcopy(document)
    temporal = document.get("temporal")  # 与冻结副本同源，避免两份真相
    support = layer_support(document, temporal)
    doc_fp = sha256_text(canonical_json(document))
    style = _style_payload(document)
    bindings = raster_bindings or {}
    viewport = _viewport(document, bindings)
    if not viewport.get("extent"):
        raise TemporalMediaError(
            "VIEWPORT_UNFIXABLE",
            "no deterministic geographic extent can be frozen from the map; "
            "refusing an animation whose window would be refit per frame",
            status_code=409)
    frozen: Dict[str, Any] = {
        "schema_version": "temporal_media_frozen.v1",
        "session_id": session_id,
        "mapspec_revision": int(revision),
        "document": document,
        "document_fingerprint": doc_fp,
        "temporal": temporal,
        "temporal_fingerprint": sha256_text(canonical_json(temporal)),
        "source_ref": str(temporal.get("source_ref") or ""),
        "content_revision": temporal.get("content_revision"),
        "style_fingerprint": sha256_text(canonical_json(style)),
        "viewport": viewport,
        "viewport_fingerprint": sha256_text(canonical_json(viewport)),
        "renderer": {
            "engine": RENDERER_ID,
            "version": RENDERER_VERSION,
            "rasterizer": "rsvg-convert" if probe_rsvg() else None,
        },
        "font_version": font_version(),
        "raster_bindings": bindings,
        "unbound_raster": unbound_raster or [],
        "layer_support": support,
    }
    return frozen


async def freeze_document(
    session_id: str, expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """生产冻结：CAS 修订 → 单读 MapSpec → 构造快照。"""
    from app.services.mapspec_store import mapspec_store
    from app.services.raster_authoring.service import current_revision

    revision = await current_revision(session_id)
    if expected_revision is not None and int(expected_revision) != revision:
        raise TemporalMediaError(
            "CAS_SUPERSEDED",
            f"expected MapSpec revision {expected_revision} != current {revision}",
            status_code=409,
            details={"expected": expected_revision, "current": int(revision)},
        )
    doc = await mapspec_store.get_mapspec(session_id)
    if not isinstance(doc, dict) or not doc:
        raise TemporalMediaError(
            "SOURCE_UNAVAILABLE", "session has no authoritative MapSpec",
            status_code=404)
    frozen = build_frozen(session_id, doc, int(revision))

    static_raster = frozen["layer_support"].get("static_raster_layers") or []
    if static_raster:
        # 复用 G26 冻结绑定（同一背景贯穿全部帧）；解析失败不静默丢层。
        try:
            from app.services.raster_authoring.publication_bindings import (
                freeze_raster_bindings,
            )
            bound = await freeze_raster_bindings(
                session_id, doc, revision=int(revision),
                trusted_dispatch=True, on_unresolvable="disclose")
        except Exception as exc:  # noqa: BLE001 — 解析失败转为可披露的 unbound
            bound = {"bindings": {}, "unbound": [
                {"layer_id": lid, "code": "raster_binding_failed",
                 "message": type(exc).__name__} for lid in static_raster]}
        frozen["raster_bindings"] = bound.get("bindings") or {}
        frozen["unbound_raster"] = bound.get("unbound") or []
        frozen = build_frozen(
            session_id, doc, int(revision),
            raster_bindings=frozen["raster_bindings"],
            unbound_raster=frozen["unbound_raster"])
    return frozen


async def recheck_freeze(session_id: str, frozen: Dict[str, Any]) -> None:
    """发布前复检（WP04/场景 17）：源或修订变化 → 中止，不发布混合作品。"""
    from app.services.mapspec_store import mapspec_store
    from app.services.raster_authoring.service import current_revision

    revision = await current_revision(session_id)
    if int(revision) != int(frozen["mapspec_revision"]):
        raise TemporalMediaError(
            "SOURCE_CHANGED_MID_RENDER",
            "MapSpec revision changed while media frames were rendering; "
            "the product was discarded instead of publishing mixed revisions",
            status_code=409,
            details={"frozen": frozen["mapspec_revision"], "current": int(revision)},
        )
    doc = await mapspec_store.get_mapspec(session_id)
    if sha256_text(canonical_json(doc)) != frozen["document_fingerprint"]:
        raise TemporalMediaError(
            "SOURCE_CHANGED_MID_RENDER",
            "frozen MapSpec content changed before publication; product discarded",
            status_code=409)


__all__ = [
    "canonical_json", "sha256_text", "sha256_bytes", "font_version",
    "build_frozen", "freeze_document", "recheck_freeze",
]
