"""G35 能力合同：真实 encoder / rasterizer / codec probe + 图层可重现性矩阵。

纪律：能力查询返回**实测**结果，不按扩展名猜成功；依赖缺失与不可重现图层
如实列出。视频 codec 即使主机存在，只要部署面未落实也不开放（typed 拒绝）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from .contracts import (
    DEFAULT_HEIGHT, DEFAULT_WIDTH, MAX_DWELL_MS, MAX_ENCODED_FRAMES,
    MAX_FPS, MAX_HEIGHT, MAX_OBSERVATIONS, MAX_SEGMENTS,
    MAX_TEMP_BYTES, MAX_TOTAL_DISPLAY_MS, MAX_WIDTH, MEDIA_FORMATS,
    MIN_DWELL_MS, MIN_HEIGHT, MIN_WIDTH, PROBE_TIMEOUT_S,
    RENDERER_ID, RENDERER_VERSION, VIDEO_DEPLOYMENT_FLAG, VIDEO_FORMATS,
)

_RSVG_CACHE: Optional[str] = None
_RSVG_PROBED = False


def probe_rsvg() -> Optional[str]:
    """复用 G26 的能力探测口径（进程级缓存；缺席 → typed 503）。"""
    global _RSVG_CACHE, _RSVG_PROBED
    if not _RSVG_PROBED:
        _RSVG_PROBED = True
        _RSVG_CACHE = shutil.which("rsvg-convert")
    return _RSVG_CACHE


def _run_probe(argv: List[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=PROBE_TIMEOUT_S,
                              check=False)
    except (OSError, subprocess.SubprocessError):
        return -1, ""
    text = (proc.stdout or b"").decode("utf-8", "replace")
    text += (proc.stderr or b"").decode("utf-8", "replace")
    return int(proc.returncode), text[:200_000]


def probe_video_codecs() -> Dict[str, Any]:
    """真实 codec probe：只读 ``ffmpeg -encoders``，不编码任何数据。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"ffmpeg": None, "codecs": [], "error": "ffmpeg_not_installed"}
    code, text = _run_probe([ffmpeg, "-hide_banner", "-encoders"])
    if code != 0:
        return {"ffmpeg": ffmpeg, "codecs": [], "error": "ffmpeg_encoder_probe_failed"}
    wanted = {
        "mp4": ("libx264", "h264"),
        "webm": ("libvpx-vp9", "vp9"),
    }
    found: Dict[str, Any] = {}
    for fmt, (encoder, codec) in wanted.items():
        found[fmt] = {
            "encoder": encoder if encoder in text else None,
            "codec": codec,
            "available": encoder in text,
        }
    deployment = os.environ.get(VIDEO_DEPLOYMENT_FLAG, "").strip() in ("1", "true", "yes")
    return {
        "ffmpeg": "present" if ffmpeg else None,
        "probe": "ffmpeg -hide_banner -encoders (read-only)",
        "deployment_flag": VIDEO_DEPLOYMENT_FLAG,
        "deployment_enabled": deployment,
        "formats": found,
    }


def probe_capabilities() -> Dict[str, Any]:
    """编码器 / 栅格器 / 版本 / 预算上限的实测合同。"""
    from PIL import Image  # Pillow 是生产依赖；缺席时调用方捕获为 typed 错误

    rsvg = probe_rsvg()
    rsvg_version = ""
    if rsvg:
        code, text = _run_probe([rsvg, "--version"])
        rsvg_version = text.strip().splitlines()[0][:80] if code == 0 and text else ""
    video = probe_video_codecs()
    formats: List[Dict[str, Any]] = [
        {
            "name": "apng", "available": True, "core": True,
            "container": "PNG/acTL/fcTL/fdAT", "codec": "streaming APNG writer",
            "color": "lossless RGBA", "duration_unit_ms": 1,
            "alpha": True,
            "limitation": "逐帧 zlib；单帧常驻；浏览器/播放器需支持 APNG",
            "deployment": "Pillow (declared)",
        },
        {
            "name": "gif", "available": True, "core": False,
            "container": "GIF89a", "codec": "Pillow + global palette",
            "color": "256 色全局调色板（有损）", "duration_unit_ms": 10,
            "alpha": "1-bit transparency only",
            "limitation": "调色板量化与时间 10ms 量化；整片共用同一调色板避免逐帧换色",
            "fps": 0,
            "fps_note": "GIF 仅支持 fps=0（每个停留一张编码帧）；相同画面会被合并并披露",
            "deployment": "Pillow (declared)",
        },
    ]
    for fmt in VIDEO_FORMATS:
        info = (video.get("formats") or {}).get(fmt) or {}
        formats.append({
            "name": fmt, "available": False, "core": False,
            "container": fmt, "codec": info.get("codec"),
            "encoder": info.get("encoder"),
            "reason": (
                "codec 与部署依赖未同时落实：runner 镜像未声明 ffmpeg；"
                "本版不按扩展名猜编码成功"
            ),
            "probe": video,
        })
    return {
        "schema_version": "temporal_media_capabilities.v1",
        "renderer": {
            "engine": RENDERER_ID,
            "version": RENDERER_VERSION,
            "rasterizer": "rsvg-convert" if rsvg else None,
            "rasterizer_version": rsvg_version,
            "available": bool(rsvg),
            "reason": "" if rsvg else "rsvg-convert 未安装：服务端离屏帧栅格化不可用",
        },
        "pillow_version": getattr(Image, "__version__", "unknown"),
        "formats": formats,
        "video_probe": video,
        "limits": {
            "width": [MIN_WIDTH, MAX_WIDTH, DEFAULT_WIDTH],
            "height": [MIN_HEIGHT, MAX_HEIGHT, DEFAULT_HEIGHT],
            "max_observations": MAX_OBSERVATIONS,
            "max_segments": MAX_SEGMENTS,
            "max_encoded_frames": MAX_ENCODED_FRAMES,
            "dwell_ms": [MIN_DWELL_MS, MAX_DWELL_MS],
            "max_total_display_ms": MAX_TOTAL_DISPLAY_MS,
            "max_fps": MAX_FPS,
            "max_temp_bytes": MAX_TEMP_BYTES,
            "media_formats": list(MEDIA_FORMATS),
        },
    }


#: 可离屏重现的源类型（冻结内联矢量 / G26 冻结数值栅格背景）。
_INLINE_VECTOR_SOURCES = frozenset(("geojson", "vector"))
_NON_REPRODUCIBLE_SOURCES = frozenset((
    "raster", "wms", "wmts", "pmtiles", "model3d", "hillshade",
    "data_fabric", "terrain", "mvt",
))


def layer_support(document: Dict[str, Any], temporal: Dict[str, Any]) -> Dict[str, Any]:
    """输入/层类型支持矩阵：逐层给出可冻结路径或 typed 阻断原因。

    - 时序绑定层必须是带 ``inlineData.features`` 的内联矢量源；MVT 瓦片样本
      不能冒充全图动画；
    - 非绑定内联矢量层作为固定静态内容随每帧一起编译；
    - ``numeric_raster`` 有已提交 ``raster_style`` 时复用 G26 冻结绑定做静态
      背景（同一冻结背景贯穿全部帧）；无表达则不可重现；
    - 外部底图 / 3D / hillshade / 时变栅格 / cube 一律不可重现。
    """
    sources = document.get("sources") if isinstance(document.get("sources"), dict) else {}
    layers = document.get("layers") if isinstance(document.get("layers"), list) else []
    bindings = [str(b.get("layer_id") or "") for b in (temporal.get("layers") or [])
                if isinstance(b, dict)]
    render_path = str(temporal.get("render_path") or "client_filter")
    representation = str(temporal.get("time_representation") or "")

    supported: List[Dict[str, str]] = []
    unsupported: List[Dict[str, str]] = []
    static_raster: List[str] = []
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        lid = str(layer.get("id") or "")
        sid = layer.get("source")
        src = sources.get(sid) if isinstance(sid, str) else None
        stype = str((src or {}).get("type") or "") if isinstance(src, dict) else ""
        bound = lid in bindings
        if not isinstance(src, dict):
            unsupported.append({"layer_id": lid, "reason": "source_ref_dangling"})
            continue
        if stype in _INLINE_VECTOR_SOURCES:
            payload = src.get("inlineData") or src.get("data")
            if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
                unsupported.append({"layer_id": lid,
                                    "reason": "inline_features_unavailable"})
                continue
            if bound:
                if render_path not in ("client_filter",):
                    unsupported.append({"layer_id": lid,
                                        "reason": f"render_path_{render_path}_not_freezable"})
                    continue
                if not representation:
                    unsupported.append({"layer_id": lid,
                                        "reason": "time_representation_unbound"})
                    continue
            supported.append({"layer_id": lid,
                              "kind": "temporal_filtered" if bound else "static_inline",
                              "source_type": stype})
            continue
        if stype == "numeric_raster" and isinstance(layer.get("raster_style"), dict):
            static_raster.append(lid)
            supported.append({"layer_id": lid, "kind": "frozen_static_raster",
                              "source_type": stype})
            continue
        tiles = src.get("tiles") or ([src.get("url")] if src.get("url") else [])
        template = tiles[0] if isinstance(tiles, list) and tiles \
            and isinstance(tiles[0], str) else ""
        external = bool(template) and any(
            token in template for token in ("{x}", "{y}", "{z}", "http"))
        if stype == "numeric_raster":
            reason = "numeric_raster_without_expression"
        elif bound and stype in ("mvt", "pmtiles", "vector_tiles"):
            # 只有当前瓦片样本，不能宣称全图动画：要求可冻结完整支持路径。
            reason = "mvt_tile_sample_not_full_extent"
        elif external and stype in _NON_REPRODUCIBLE_SOURCES:
            reason = "external_tiles_licence_not_embedded"
        elif stype in _NON_REPRODUCIBLE_SOURCES:
            reason = "time_varying_raster_not_freezable"
        else:
            reason = f"source_type_unsupported:{stype[:32]}"
        unsupported.append({"layer_id": lid, "reason": reason})
    return {
        "supported_layers": supported,
        "unsupported_layers": unsupported,
        "static_raster_layers": static_raster,
        "temporal_bindings": bindings,
        "render_path": render_path,
        "representation": representation,
        "complete": not unsupported,
    }


__all__ = ["probe_rsvg", "probe_capabilities", "probe_video_codecs", "layer_support"]
