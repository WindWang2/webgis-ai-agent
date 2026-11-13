"""WP05 — 逐数字帧真实渲染与逐帧证据。

每一帧都在**同一份冻结文档副本**上：逐个 temporal 绑定层调用既有
``apply_time_window_to_doc`` 过滤 → 注入本帧真实时间标注 → 既有
``compile_mapspec_to_svg_detailed`` 编译 SVG → rsvg-convert 栅格化 →
Pillow 独立解码 → 记录窗口/要素/指纹/诊断/PNG checksum。

不使用浏览器录屏，不套用要求数值栅格的 G26 高层 ``_export_png``，
不人为加入栅格绕过 guard；静态数值栅格背景由 G26 冻结绑定承载。
"""
from __future__ import annotations

import copy
import io
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .capabilities import probe_rsvg
from .contracts import MAX_SVG_BYTES, RSVG_TIMEOUT_S, TemporalMediaError
from .freeze import canonical_json, sha256_bytes, sha256_text
from .store import frame_key


def _feature_id(feature: Dict[str, Any]) -> str:
    raw = feature.get("id")
    if raw is not None and str(raw):
        return str(raw)[:64]
    return "h" + sha256_text(canonical_json(feature))[:12]


def _source_of(doc: Dict[str, Any], layer_id: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    for layer in doc.get("layers") or []:
        if isinstance(layer, dict) and str(layer.get("id") or "") == layer_id:
            sid = layer.get("source")
            sources = doc.get("sources") if isinstance(doc.get("sources"), dict) else {}
            if isinstance(sid, str) and isinstance(sources.get(sid), dict):
                return sid, sources[sid]
    return None, None


def _features_of(doc: Dict[str, Any], layer_id: str) -> List[Dict[str, Any]]:
    _, src = _source_of(doc, layer_id)
    if not src:
        return []
    payload = src.get("inlineData") or src.get("data")
    if not isinstance(payload, dict):
        return []
    return [f for f in (payload.get("features") or []) if isinstance(f, dict)]


def _window_values(window: Dict[str, Any]) -> Tuple[Any, Any]:
    from app.services.gis_harness.temporal_products.time_filter import (
        normalize_boundary_value,
    )
    lo = normalize_boundary_value(
        window["start"], window["representation"], window["granularity"],
        window["timezone"], window["format_hint"])
    hi = normalize_boundary_value(
        window["end"], window["representation"], window["granularity"],
        window["timezone"], window["format_hint"])
    return lo, hi


def _in_window(value: Any, lo: Any, hi: Any) -> bool:
    numeric = isinstance(lo, (int, float)) and not isinstance(lo, bool)
    if numeric:
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and float(lo) <= float(value) < float(hi))
    return isinstance(value, str) and str(lo) <= value < str(hi)


def build_compile_doc(plan: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """冻结副本；partial 时**显式移除**不可重现层并登记完整遗漏清单。"""
    doc = copy.deepcopy(plan["frozen"]["document"])
    omitted = list(plan["partial"]["omitted_layers"])
    if not omitted:
        return doc, omitted
    drop = {str(o.get("layer_id")) for o in omitted}
    kept = [layer for layer in (doc.get("layers") or [])
            if not (isinstance(layer, dict) and str(layer.get("id") or "") in drop)]
    doc["layers"] = kept
    return doc, omitted


def _time_window(plan: Dict[str, Any], segment: Dict[str, Any],
                 layer_id: str, content_window: Dict[str, str],
                 frame_id: str) -> Dict[str, Any]:
    tc = plan["temporal_contract"]
    return {
        "frame_id": frame_id,
        "label": segment["time_label"],
        "start": content_window["start"],
        "end": content_window["end"],
        "time_field": tc["time_field"],
        "time_representation": tc["time_representation"],
        "granularity": tc["granularity"],
        "timezone": tc["timezone"],
        "time_format_hint": tc.get("time_format_hint") or "",
        "layer_id": layer_id,
        "representation": tc["time_representation"],
        "format_hint": tc.get("time_format_hint") or "",
    }


def _render_window_expected_ids(plan: Dict[str, Any], layer_id: str,
                                content_window: Dict[str, str]) -> List[str]:
    """渲染侧自校验：从**未过滤**的冻结文档重算本窗应出现的要素 id。

    注意：这不是独立 oracle —— 它复用 `normalize_boundary_value`（与过滤同一
    实现），只用于发现「相邻不同观测却同画面」等渲染侧异常。权威内容 oracle
    是 `verify.py::_oracle_expected_ids`，用独立的时间解析路径重算。
    """
    tc = plan["temporal_contract"]
    probe = {
        "start": content_window["start"], "end": content_window["end"],
        "representation": tc["time_representation"],
        "granularity": tc["granularity"], "timezone": tc["timezone"],
        "format_hint": tc.get("time_format_hint") or "",
    }
    lo, hi = _window_values(probe)
    field = str(tc["time_field"])
    out = []
    for feature in _features_of(plan["frozen"]["document"], layer_id):
        value = (feature.get("properties") or {}).get(field)
        if _in_window(value, lo, hi):
            out.append(_feature_id(feature))
    return sorted(out)


def _compile_svg(plan: Dict[str, Any], frame_doc: Dict[str, Any]) -> Any:
    from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

    viewport = plan["frozen"]["viewport"]
    bounds = viewport.get("extent") if isinstance(viewport, dict) else None
    return compile_mapspec_to_svg_detailed(
        frame_doc,
        target_dpi=int(plan["canvas"]["dpi"]),
        width=int(plan["canvas"]["width"]),
        height=int(plan["canvas"]["height"]),
        padding=24,
        include_chrome=True,
        bounds=bounds,
        raster_bindings=plan["frozen"].get("raster_bindings") or None,
    )


def _rasterize(svg: str, plan: Dict[str, Any], out: Path,
               cancel: Optional[Any] = None) -> bytes:
    rsvg = probe_rsvg()
    if not rsvg:
        raise TemporalMediaError(
            "PNG_RASTERIZER_UNAVAILABLE", "rsvg-convert is not installed",
            status_code=503)
    if len(svg.encode("utf-8")) > MAX_SVG_BYTES:
        raise TemporalMediaError("SVG_TOO_LARGE",
                                 "frame SVG exceeds bounded compile budget",
                                 status_code=413)
    argv = [rsvg, "-w", str(int(plan["canvas"]["width"])),
            "-h", str(int(plan["canvas"]["height"])),
            "-o", str(out), "--keep-aspect-ratio", "-"]
    if cancel is not None and cancel():
        raise TemporalMediaError("CANCELLED", "media job cancelled before rasterizing",
                                 status_code=409)
    try:
        proc = subprocess.run(argv, input=svg.encode("utf-8"),
                              capture_output=True, timeout=RSVG_TIMEOUT_S,
                              cwd=str(out.parent), check=False)
    except subprocess.TimeoutExpired as exc:
        raise TemporalMediaError("RENDER_TIMEOUT",
                                 "frame rasterizer exceeded bounded timeout",
                                 status_code=504) from exc
    except OSError as exc:
        raise TemporalMediaError("RENDER_FAILED", "frame rasterizer could not start",
                                 status_code=502) from exc
    if proc.returncode != 0 or not out.is_file():
        raise TemporalMediaError(
            "RENDER_FAILED", "SVG rasterization failed (non-zero exit or no PNG)",
            status_code=502,
            details={"stderr": (proc.stderr or b"").decode("utf-8", "replace")[:200]})
    return out.read_bytes()


def render_digital_frame(
    plan: Dict[str, Any], segment: Dict[str, Any], *,
    compile_doc: Dict[str, Any], omitted_layers: List[Dict[str, str]],
    frames_dir: Path, svgs_dir: Path,
    held_windows: Dict[str, Dict[str, str]],
    cancel: Optional[Any] = None,
) -> Dict[str, Any]:
    """渲染一张数字帧并返回可复核证据（PNG 字节写 frames_dir，不进内存常驻）。"""
    from PIL import Image
    from app.services.gis_harness.temporal_products.time_filter import (
        apply_time_window_to_doc,
    )

    key = frame_key(plan, segment)
    frame_id = f"g35-{int(segment['index']):04d}"
    # 沿用显示：画面来自真实观测窗，标注仍写明本段真实缺期身份与沿用来源。
    content_window = (held_windows.get(str(segment["held_from"]))
                      if segment["kind"] == "held" else dict(segment["window"]))
    if content_window is None:
        raise TemporalMediaError("HELD_SOURCE_MISSING",
                                 "held display has no source observation window",
                                 status_code=409)
    tc = plan["temporal_contract"]
    bindings = [str(b.get("layer_id")) for b in
                (plan["frozen"]["temporal"].get("layers") or [])
                if isinstance(b, dict)]
    if not bindings:
        raise TemporalMediaError("NO_TEMPORAL_BINDING",
                                 "temporal contract has no layer binding",
                                 status_code=422)

    frame_doc = copy.deepcopy(compile_doc)
    layer_windows: List[Dict[str, Any]] = []
    for layer_id in bindings:
        window = _time_window(plan, segment, layer_id, content_window, frame_id)
        before = len(_features_of(frame_doc, layer_id))
        frame_doc = apply_time_window_to_doc(frame_doc, window)
        after_features = _features_of(frame_doc, layer_id)
        lo, hi = _window_values(window)
        # 内容 oracle：过滤未生效（含只过滤第一层）必须当场失败，不发布。
        # 无窗内要素是诚实空态；只有「窗外要素仍在图里」才是失败。
        field = str(tc["time_field"])
        escaped = [f for f in after_features
                   if not _in_window((f.get("properties") or {}).get(field), lo, hi)]
        if escaped:
            raise TemporalMediaError(
                "TIME_FILTER_NOT_APPLIED",
                f"temporal window did not filter layer {layer_id!r}; refusing a "
                "frame whose content would not match its time label",
                status_code=409,
                details={"layer_id": layer_id, "escaped": len(escaped),
                         "before": before, "after": len(after_features),
                         "window": [str(lo), str(hi)]})
        layer_windows.append({
            "layer_id": layer_id,
            "window": {"start": content_window["start"],
                       "end": content_window["end"],
                       "normalized": [str(lo), str(hi)]},
            "features_before": before,
            "features_after": len(after_features),
            "feature_ids": sorted(_feature_id(f) for f in after_features)[:64],
            "expected_feature_ids": _render_window_expected_ids(
                plan, layer_id, content_window)[:64],
        })

    # 时间标注：把本段身份注入 frames 并盖章 cursor（既有 _temporal_note 消费）。
    temporal = frame_doc.get("temporal")
    if not isinstance(temporal, dict):
        temporal = {}
    temporal = dict(temporal)
    # 显示窗口恒为本段（缺期）窗口：沿用段的内容来自 held_from 观测，但图上
    # 的时间窗口注记必须写本段真实缺期窗，否则「2020 缺期」却印着 2019 窗口。
    display_window = dict(segment["window"])
    frames = [dict(f) for f in (temporal.get("frames") or []) if isinstance(f, dict)]
    frames.append({
        "id": frame_id, "label": segment["time_label"],
        "start": display_window["start"], "end": display_window["end"],
    })
    temporal["frames"] = frames
    temporal["cursor_frame"] = frame_id
    temporal["enabled"] = bool(plan["display"]["time_display"] != "none")
    frame_doc["temporal"] = temporal

    comp = _compile_svg(plan, frame_doc)
    svg = comp.svg
    if segment["time_label"]:
        needle = f"时间: {segment['time_label']}"
        if needle not in svg:
            raise TemporalMediaError(
                "TIME_LABEL_MISMATCH",
                "compiled SVG time annotation does not match this frame's "
                "observation identity (label/content drift refused)",
                status_code=409,
                details={"expected": needle[:120]})
    if getattr(comp, "truncated_features", False) or getattr(comp, "timed_out", False):
        raise TemporalMediaError(
            "FRAME_INCOMPLETE",
            "vector compiler reported truncated features or timeout; "
            "refusing a partial digital frame",
            status_code=409)

    frames_dir.mkdir(parents=True, exist_ok=True)
    svgs_dir.mkdir(parents=True, exist_ok=True)
    # SVG 是逐帧时间标注/内容的可读证据；验证器独立读取并重算 checksum。
    (svgs_dir / f"{key}.svg").write_text(svg, encoding="utf-8")
    out = frames_dir / f"{key}.png"
    png = _rasterize(svg, plan, out, cancel=cancel)
    digest = "sha256:" + sha256_bytes(png)
    try:
        with Image.open(io.BytesIO(png)) as im:
            im.load()
            size = list(im.size)
            mode = im.mode
    except Exception as exc:  # noqa: BLE001
        raise TemporalMediaError("PNG_UNDECODABLE",
                                 "rendered digital frame is not a decodable PNG",
                                 status_code=502) from exc
    if size != [int(plan["canvas"]["width"]), int(plan["canvas"]["height"])]:
        raise TemporalMediaError("FRAME_SIZE_MISMATCH",
                                 "digital frame size differs from the media plan",
                                 status_code=502, details={"size": size})

    diagnostics = [dict(d) for d in (getattr(comp, "diagnostics", None) or [])][:24]
    return {
        "schema_version": "temporal_media_digital_frame.v1",
        "digital_frame_id": key,
        "segment_index": int(segment["index"]),
        "kind": segment["kind"],
        "observation_id": segment["observation_id"],
        "observation_label": segment["observation_label"],
        "observation_window": {"start": segment["observation_start"],
                               "end": segment["observation_end"]},
        "content_window": dict(content_window),
        "display_window": dict(display_window),
        "time_label": segment["time_label"],
        "identity": segment["identity"],
        "missing_period": bool(segment["missing_period"]),
        "held_from": segment["held_from"],
        "time_display": plan["display"]["time_display"],
        "layers": layer_windows,
        "expected_feature_ids_by_layer": {
            w["layer_id"]: w["expected_feature_ids"] for w in layer_windows},
        "feature_count": sum(w["features_after"] for w in layer_windows),
        "omitted_layers": omitted_layers,
        "time_annotation_verified": bool(segment["time_label"]),
        "svg_sha256": "sha256:" + sha256_text(svg),
        "png_sha256": digest,
        "size": size,
        "png_mode": mode,
        "png_bytes": len(png),
        "png_path": out.name,
        "renderer": {
            "engine": plan["frozen_fingerprints"]["renderer"]["engine"],
            "version": plan["frozen_fingerprints"]["renderer"]["version"],
            "rasterizer": "rsvg-convert",
            "feature_count": getattr(comp, "feature_count", None),
        },
        "fingerprints": {
            "source": plan["frozen_fingerprints"]["document"],
            "style": plan["frozen_fingerprints"]["style"],
            "viewport": plan["frozen_fingerprints"]["viewport"],
            "temporal": plan["frozen_fingerprints"]["temporal"],
        },
        "diagnostics": diagnostics,
        "rendered_component_types": list(
            getattr(comp, "rendered_component_types", None) or []),
    }


__all__ = ["build_compile_doc", "render_digital_frame", "frame_key"]
