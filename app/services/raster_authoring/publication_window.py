"""G26 出版窗口渲染：冻结表达 × 目标像素网格的地理对齐栅格嵌入。

语义与 ``raster_tile_service.render_raster_tile`` 同源（同一窗口读 /
overview / 跨瓦片冻结 stretch / 全通道有效才绘制 / masked→alpha / opacity
单点应用），但目标是**出版画布的一个窗口**而非 256px 瓦片：

- 目标网格 = 页面地图区（EPSG:3857 仿射 —— 与 ``mapspec_to_svg`` 的
  proj_x（线性 lon）/ proj_y（线性 mercator-y）投影链同构）；
- 源 CRS ≠ 3857 时 ``rasterio.warp.reproject`` 到目标网格；无 CRS 源按瓦片
  服务同语义视为已处 3857（warn-once + 披露），绝不猜投影；
- 部分相交只渲染交集（coverage mask 拒绝无源像元），完全不相交返回透明
  1×1 + ``overlap="none"`` 披露 —— 绝不夹到边缘取无关像元；
- 旋转 affine（b/d≠0）按角点足迹包络渲染，warp 负责精确像元归属，空角由
  coverage mask 透明化并披露；
- 像素上界：``max_side``（默认 2048，同 G20 快照纪律）+ GDAL ``out_shape``
  解码约束（超大窗口经 overview 降采样，解码内存 = 输出网格）；
- ``stat_key``（mtime_ns, size）冻结文件身份：导出期间源文件被改写 →
  typed ``STALE_BINDING``，不渲染新内容冒充冻结快照。
"""
from __future__ import annotations

import hashlib
import io
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np

from app.schemas.raster_spec import (
    MAX_RASTER_SNAPSHOT_SIDE,
    RasterExpressionError,
    RasterExpressionSpec,
)
from app.services.raster_authoring.palettes import colormap_lut

logger = logging.getLogger(__name__)

#: 输出网格长边上限（与 G20 snapshot 同纪律：提高 DPI 不增加遥感细节）。
MAX_PUBLICATION_WINDOW_SIDE = MAX_RASTER_SNAPSHOT_SIDE

#: 单层解码输出像元上界（含波段）。GDAL ``out_shape`` 降采样使解码内存
#: 约等于输出网格；本上界防病态宽窗口 + 多波段叠加超出宿主预算。
MAX_DECODE_PIXELS = 22_000_000

#: 窗口渲染缓存：内容寻址（path+stat+style+revision+窗口+网格），
#: 多帧/多槽复用同一窗口不重复解码/编码（A17 性能面）。键含 stat 身份 →
#: 文件改写自然换键；条目数上限兜底内存。
_WINDOW_CACHE: Dict[str, WindowRenderResult] = {}
_WINDOW_CACHE_LOCK = threading.Lock()
_WINDOW_CACHE_MAX = 16


def invalidate_publication_window_cache() -> int:
    """失效全部窗口缓存（测试隔离/低内存止损用）。"""
    with _WINDOW_CACHE_LOCK:
        dropped = len(_WINDOW_CACHE)
        _WINDOW_CACHE.clear()
        return dropped


@dataclass(frozen=True)
class WindowRenderResult:
    """一个数值栅格层在一个目标网格上的可嵌入渲染。"""

    png_bytes: bytes
    bounds_wsen: Tuple[float, float, float, float]
    """该 PNG 覆盖的 EPSG:4326 范围（放置矩形的事实源；north-up 轴对齐）。"""
    bounds_3857: Tuple[float, float, float, float]
    width: int
    height: int
    overlap: str = "full"  # full | partial | none
    all_nodata: bool = False
    constant: bool = False
    truncated: bool = False
    grid: str = "mercator-window"
    notes: Tuple[str, ...] = field(default_factory=tuple)


def _cache_key(path: str, stat_key: Tuple[int, int], spec: RasterExpressionSpec,
               view_bounds: Sequence[float], out_w: int, out_h: int) -> str:
    raw = "|".join([
        path, str(stat_key[0]), str(stat_key[1]), spec.style_id,
        spec.data_revision,
        ",".join(f"{float(v):.7f}" for v in view_bounds),
        str(int(out_w)), str(int(out_h)),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _transparent_result(notes: Tuple[str, ...]) -> WindowRenderResult:
    import struct
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (1, 1), (0, 0, 0, 0)).save(buf, format="PNG")
    return WindowRenderResult(
        png_bytes=buf.getvalue(), bounds_wsen=(0.0, 0.0, 0.0, 0.0),
        bounds_3857=(0.0, 0.0, 0.0, 0.0), width=1, height=1,
        overlap="none", all_nodata=True, notes=notes,
    )


def render_publication_window(
    path: str,
    stat_key: Tuple[int, int],
    spec_payload: Mapping[str, Any],
    view_bounds_wsen: Sequence[float],
    out_w: int,
    out_h: int,
    *,
    max_side: int = MAX_PUBLICATION_WINDOW_SIDE,
    decode_budget_px: int = MAX_DECODE_PIXELS,
) -> WindowRenderResult:
    """冻结表达 → 目标网格上的 RGBA PNG（地理对齐；见模块 docstring）。"""
    import os

    # ── 冻结身份复核（文件被改写 → 拒绝渲染，绝不冒充冻结快照）──────
    try:
        stat = os.stat(path)
    except OSError as exc:
        raise RasterExpressionError(
            "SOURCE_UNAVAILABLE", "frozen raster file is no longer readable",
            status_code=404,
        ) from exc
    if (stat.st_mtime_ns, stat.st_size) != (int(stat_key[0]), int(stat_key[1])):
        raise RasterExpressionError(
            "STALE_BINDING",
            "the raster file changed after the export snapshot was frozen",
            status_code=409,
            correction_hint="Re-freeze the export from the current data revision.",
        )

    # ── 规范表达解析（style_id 篡改检测复用 finalize 契约）────────────
    try:
        spec = RasterExpressionSpec.model_validate(dict(spec_payload)).finalize()
    except RasterExpressionError:
        raise
    except Exception as exc:  # noqa: BLE001 - 非法 payload 一律 typed
        raise RasterExpressionError(
            "SOURCE_NOT_NUMERIC_RASTER",
            "frozen raster expression payload is not a valid RasterExpressionSpec",
        ) from exc

    view = [float(v) for v in view_bounds_wsen]
    if len(view) != 4 or not all(np.isfinite(view)) or view[0] >= view[2] \
            or view[1] >= view[3]:
        raise RasterExpressionError(
            "RENDER_FAILED", "invalid publication view bounds for raster window",
        )

    key = _cache_key(path, (stat.st_mtime_ns, stat.st_size), spec, view, out_w, out_h)
    with _WINDOW_CACHE_LOCK:
        hit = _WINDOW_CACHE.get(key)
    if hit is not None:
        return hit

    notes: list[str] = []
    out_w = max(1, int(out_w))
    out_h = max(1, int(out_h))
    scale = min(1.0, max_side / float(max(out_w, out_h)))
    eff_w = max(1, int(round(out_w * scale)))
    eff_h = max(1, int(round(out_h * scale)))
    truncated = scale < 1.0

    try:
        result = _render_window(
            path, spec, view, eff_w, eff_h, truncated, notes, decode_budget_px,
        )
    except RasterExpressionError:
        raise
    except Exception as exc:  # noqa: BLE001 - 渲染失败不缓存、不伪装
        logger.warning("[g26-window] render failed for %s: %s", path, exc)
        raise RasterExpressionError(
            "RENDER_FAILED", "publication raster window could not be rendered",
            status_code=502,
        ) from exc

    with _WINDOW_CACHE_LOCK:
        if len(_WINDOW_CACHE) >= _WINDOW_CACHE_MAX:
            _WINDOW_CACHE.clear()
        _WINDOW_CACHE[key] = result
    return result


def _render_window(
    path: str,
    spec: RasterExpressionSpec,
    view: list[float],
    out_w: int,
    out_h: int,
    truncated: bool,
    notes: list[str],
    decode_budget_px: int,
) -> WindowRenderResult:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio import warp as rio_warp
    from rasterio.windows import Window, from_bounds

    from app.services.raster_authoring.render import render_style
    from app.services.raster_tile_service import (
        _normalize_channel,
        _resampling_for,
    )

    style = render_style(spec)
    resampling = _resampling_for(style.resampling)

    def _valid_mask(arr: np.ndarray, nodata_val) -> np.ndarray:
        """瓦片服务 `_channel_valid_mask` 同语义 + 哨兵容差。

        GDAL warp 以浮点回写 dst_nodata（实测 float32 下 -9999.0 可回写为
        -9998.999），逐位相等比较会把哨兵当有效值 → 黑填充冒充实测。容差
        atol=max(1e-3, |nodata|·1e-6) 与 GDAL 自身 NODATA 判定同量级。
        """
        if nodata_val is None:
            valid = np.ones(arr.shape, dtype=bool)
        else:
            tol = max(1e-3, abs(float(nodata_val)) * 1e-6)
            valid = np.abs(arr.astype("float64") - float(nodata_val)) > tol
        if np.issubdtype(arr.dtype, np.floating):
            valid = valid & np.isfinite(arr)
        return valid

    from app.lib.geo_raster.env import open_raster, rasterio_env

    with rasterio_env(), open_raster(path) as src:
        # 与瓦片服务同语义：无 CRS → 视为已处 EPSG:3857（warn-once）。
        crs_less = src.crs is None
        src_crs = "EPSG:3857" if crs_less else str(src.crs)
        if crs_less:
            notes.append("no_crs_assumed_3857")

        # 目标网格 = 视图（EPSG:4326）的 3857 包络（proj_x 线性 lon、
        # proj_y 线性 merc_y ⇒ 画布对 3857 为仿射）。
        try:
            view_3857 = rio_warp.transform_bounds(
                "EPSG:4326", "EPSG:3857", view[0], view[1], view[2], view[3])
        except Exception as exc:  # noqa: BLE001
            raise RasterExpressionError(
                "RENDER_FAILED", "view bounds could not be projected to EPSG:3857",
            ) from exc

        # 源侧窗口 = 视图 3857 包络在源 CRS 下的范围 ∩ 源栅格。
        try:
            win_src_bounds = rio_warp.transform_bounds(
                "EPSG:3857", src_crs, *view_3857)
        except Exception as exc:  # noqa: BLE001
            raise RasterExpressionError(
                "INVALID_CRS",
                f"source CRS {src_crs!r} could not be used to window the export view",
                correction_hint="Register the raster with a supported CRS or export without this layer.",
            ) from exc

        rotated = bool(getattr(src.transform, "b", 0.0)) or \
            bool(getattr(src.transform, "d", 0.0))
        if rotated:
            # 旋转 affine：from_bounds 的轴对齐窗口不成立 —— 角点足迹包络
            # 过采样（warp 精确归属像元，空角由 coverage 透明）+ 披露。
            notes.append("rotated_affine_envelope")
            corners = [
                src.transform * (0, 0), src.transform * (src.width, 0),
                src.transform * (src.width, src.height), src.transform * (0, src.height),
            ]
            xs = [c[0] for c in corners]
            ys = [c[1] for c in corners]
            win_src_bounds = (min(xs), min(ys), max(xs), max(ys))

        win_raw = from_bounds(*win_src_bounds, transform=src.transform)
        try:
            win = win_raw.round_offsets().round_shape().intersection(
                Window(0, 0, src.width, src.height))
        except rasterio.errors.WindowError:
            return _transparent_result(("no_overlap_with_view",))
        if win.width <= 0 or win.height <= 0:
            return _transparent_result(("no_overlap_with_view",))

        # 视图 ∩ 源 = 部分相交（理想窗口越出源边界）。coverage mask 只在
        # 此情形生成（完全内部窗口保持满覆盖，无取整边缘伪影）。
        # 容差 0.5px：from_bounds/transform_bounds 的浮点抖动不算真部分
        # （瓦片服务同判据无此容差是因为瓦片 bounds 恒在源内不贴边）。
        eps = 0.5
        is_partial = (
            win_raw.col_off < -eps or win_raw.row_off < -eps
            or win_raw.col_off + win_raw.width > src.width + eps
            or win_raw.row_off + win_raw.height > src.height + eps
        )
        overlap = "partial" if (is_partial or rotated) else "full"

        indexes = tuple(int(b) for b in style.bands)
        if any(b < 1 or b > src.count for b in indexes):
            raise RasterExpressionError(
                "BAND_OUT_OF_RANGE",
                "committed expression references a band absent from the frozen data revision",
                status_code=409,
            )
        count = len(indexes)

        # 解码约束：out_shape 降采样（GDAL 咨询 overview）—— 解码内存 ≈
        # 输出网格；再叠加像元预算（防病态宽窗口）。
        win_bounds = rasterio.windows.bounds(win, src.transform)
        dec_scale = min(
            1.0,
            max_side_ref(out_w, out_h) / float(max(win.width, win.height)),
            (decode_budget_px / float(count)) ** 0.5
            / float(max(win.width, win.height)),
        )
        dec_w = max(1, int(round(win.width * dec_scale)))
        dec_h = max(1, int(round(win.height * dec_scale)))
        win_data = src.read(
            indexes=indexes, window=win, out_shape=(count, dec_h, dec_w),
            resampling=resampling,
        )
        win_transform = rasterio.transform.from_bounds(*win_bounds, dec_w, dec_h)

        nodata_val = src.nodata
        # 目标网格 = 交集窗口的 3857 包络 @ (out_w, out_h)。
        try:
            dst_bounds_3857 = rio_warp.transform_bounds(
                src_crs, "EPSG:3857", *win_bounds)
        except Exception as exc:  # noqa: BLE001
            raise RasterExpressionError(
                "INVALID_CRS",
                f"source window could not be projected to EPSG:3857 ({src_crs!r})",
                correction_hint="Reproject the raster to EPSG:4326/3857 or export without this layer.",
            ) from exc
        dst_transform = rasterio.transform.from_bounds(
            *dst_bounds_3857, out_w, out_h)

        if src_crs == "EPSG:3857":
            # 同 CRS：read 的 out_shape 网格即目标网格（仅有分辨率差）。
            if (dec_w, dec_h) != (out_w, out_h):
                dst_data = np.zeros((count, out_w, out_h), dtype=win_data.dtype)
                rio_warp.reproject(
                    source=win_data, destination=dst_data,
                    src_transform=win_transform, src_crs=src_crs,
                    dst_transform=dst_transform, dst_crs="EPSG:3857",
                    resampling=resampling,
                    src_nodata=nodata_val, dst_nodata=nodata_val,
                )
            else:
                dst_data = win_data
        else:
            dst_data = np.zeros((count, out_w, out_h), dtype=win_data.dtype)
            rio_warp.reproject(
                source=win_data, destination=dst_data,
                src_transform=win_transform, src_crs=src_crs,
                dst_transform=dst_transform, dst_crs="EPSG:3857",
                resampling=resampling,
                src_nodata=nodata_val, dst_nodata=nodata_val,
            )

        # Coverage mask：源真实覆盖的像元（部分相交/旋转包络的空角透明）。
        if is_partial or rotated:
            win_mask = np.ones((dec_h, dec_w), dtype=np.uint8) * 255
            coverage = np.zeros((out_w, out_h), dtype=np.uint8)
            rio_warp.reproject(
                source=win_mask, destination=coverage,
                src_transform=win_transform, src_crs=src_crs,
                dst_transform=dst_transform, dst_crs="EPSG:3857",
                resampling=Resampling.nearest, src_nodata=0, dst_nodata=0,
            )
            coverage_mask = coverage > 0
        else:
            coverage_mask = np.ones((out_w, out_h), dtype=bool)

    # ── 冻结 stretch 归一（与瓦片/G20 快照同数学；全通道有效才绘制）──
    channels = []
    per_channel_valid = []
    for i in range(count):
        arr = dst_data[i]
        valid = _valid_mask(arr, nodata_val) & coverage_mask
        lo, hi = style.stretch[i]
        # _normalize_channel 的 vmin/vmax 缺省为窗口局部 min/max —— 冻结
        # stretch 必须显式传入（绝不重估）。
        channels.append(_normalize_channel(arr, valid, float(lo), float(hi)))
        per_channel_valid.append(valid)
    common_valid = np.logical_and.reduce(per_channel_valid)
    all_nodata = not bool(common_valid.any())
    lo_all = min(p[0] for p in style.stretch)
    hi_all = max(p[1] for p in style.stretch)

    if all_nodata:
        notes.append("all_nodata_or_no_overlap_in_view")
        alpha = np.zeros((out_w, out_h), dtype=np.uint8)
        if count >= 3:
            rgb = np.dstack([channels[0], channels[1], channels[2]])
        else:
            rgb = np.dstack([channels[0]] * 3)
    elif count >= 3:
        alpha = np.where(common_valid, 255, 0).astype(np.uint8)
        rgb = np.dstack([channels[0], channels[1], channels[2]])
    else:
        alpha = np.where(common_valid, 255, 0).astype(np.uint8)
        lut = colormap_lut(style.colormap or "gray", 256)
        if lut is None:
            raise RasterExpressionError(
                "UNKNOWN_COLORMAP", f"colormap {style.colormap!r} has no LUT",
                status_code=502,
            )
        rgb = lut[channels[0]]

    if style.opacity < 1.0:
        alpha = np.rint(alpha.astype(np.float32) * float(style.opacity)).astype(np.uint8)
    rgba = np.dstack([rgb, alpha])

    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG", compress_level=6)

    try:
        bounds_wsen = rio_warp.transform_bounds(
            "EPSG:3857", "EPSG:4326", *dst_bounds_3857)
    except Exception:  # noqa: BLE001 - 3857→4326 数值稳健
        bounds_wsen = (dst_bounds_3857[0], dst_bounds_3857[1],
                       dst_bounds_3857[2], dst_bounds_3857[3])
        notes.append("bounds_fallback_3857")

    if truncated:
        notes.append("resolution_capped_no_extra_remote_sensing_detail")
    return WindowRenderResult(
        png_bytes=buf.getvalue(),
        bounds_wsen=tuple(float(v) for v in bounds_wsen),  # type: ignore[arg-type]
        bounds_3857=tuple(float(v) for v in dst_bounds_3857),  # type: ignore[arg-type]
        width=out_w, height=out_h,
        overlap=overlap,
        all_nodata=all_nodata,
        constant=bool(hi_all <= lo_all),
        truncated=truncated,
        grid="mercator-window",
        notes=tuple(notes),
    )


def max_side_ref(out_w: int, out_h: int) -> float:
    """解码降采样参考边（输出网格长边；防越界读全幅）。"""
    return float(max(out_w, out_h))


__all__ = [
    "MAX_PUBLICATION_WINDOW_SIDE",
    "MAX_DECODE_PIXELS",
    "WindowRenderResult",
    "render_publication_window",
    "invalidate_publication_window_cache",
]
