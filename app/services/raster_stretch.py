"""Raster Dynamic Stretch — 栅格拉伸客户端化（V11 W3.3，ADR-0163，缺口 G9 半边）。

V10 现状：栅格色带**烘焙进 PNG**（``render_array_to_png``）—— 换色带/改拉伸
必须服务端重渲染。本模块下发「数据 + 拉伸参数」：

- 服务端只做**一次确定性量化**：有限值按百分位（缺省 P2–P98）截断后线性
  归一到 uint8（base64 载荷，有界：每格 1 字节）；拉伸参数（min/max/
  clip 百分位/色带 stops/nodata 约定）随 payload 下发；
- 前端 :func:`applyRasterStretch`（``frontend/lib/map-kit/raster-stretch.ts``
  镜像）用同一算法实时重渲染 —— 换色带/改拉伸零请求；
- **烘焙保留**：``render_array_to_png`` 不动，仍是导出/离线兜底；
- **双路径 parity**：同一阵列的量化值经客户端算法着色，与烘焙 PNG 的同格
  颜色一致（测试锁定：动态路径 min/max 拉伸与烘焙的 min/max 拉伸是同一
  线性映射的两次实现 —— stop 插值同式，颜色差 ≤1/255 通道）。

确定性：量化与映射均纯函数（无随机）；payload JSON 可序列化。
"""
from __future__ import annotations

import base64
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

from app.lib.cartography.palettes import COLOR_PALETTES

STRETCH_PAYLOAD_VERSION = 1

#: 缺省截断百分位（P2–P98：抑制孤立极值对动态范围的挤占）。
DEFAULT_CLIP_PERCENTILES: Tuple[float, float] = (2.0, 98.0)

#: 保留量化档：255 = nodata（与前端镜像同约定）。
QUANT_NODATA = 255


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def palette_stops(palette: str) -> List[str]:
    """色带 stops（未知色带回落缺省 —— 与烘焙同口径）。"""
    return list(COLOR_PALETTES.get(palette) or COLOR_PALETTES["Viridis"])


def build_raster_stretch_payload(
    array: np.ndarray,
    palette: str = "Viridis",
    *,
    clip_percentiles: Sequence[float] = DEFAULT_CLIP_PERCENTILES,
) -> Dict[str, Any]:
    """数值阵列 → 动态拉伸 payload（确定性、JSON 可序列化、有界）。

    - ``values``：uint8 量化网格（base64；255 = nodata 保留档）；
    - ``stretch``：``{valueMin, valueMax, clipLo, clipHi}`` —— 前端把
      量化档反归一时使用（重建绝对值域）；
    - ``nodataMask``：每格 1 bit 的 base64 位图（列主序与 values 同布局）；
    - ``paletteHex``：色带 stops（前端同式插值）。
    """
    arr = np.asarray(array, dtype=float)
    if arr.ndim != 2 or arr.size == 0:
        # 严格契约（评审 finding）：烘焙宽容是导出兜底的需要；payload 构建
        # 是生产决策面，静默 1x1 只会把调用方 bug 推迟到渲染端。
        raise ValueError("raster stretch payload 需要 2D 非空数值阵列")
    height, width = arr.shape

    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        clip_lo, clip_hi = 0.0, 0.0
    else:
        lo_pct, hi_pct = clip_percentiles
        clip_lo = float(np.percentile(finite, lo_pct))
        clip_hi = float(np.percentile(finite, hi_pct))
    span = (clip_hi - clip_lo) or 1.0

    nodata = ~np.isfinite(arr)
    norm = np.where(nodata, 0.0, (arr - clip_lo) / span)
    quant = np.clip(np.rint(norm * (QUANT_NODATA - 1)), 0, QUANT_NODATA - 2)
    quant = np.where(nodata, QUANT_NODATA, quant).astype(np.uint8)

    bits = np.packbits(nodata.reshape(-1))
    return {
        "version": STRETCH_PAYLOAD_VERSION,
        "mode": "dynamic_stretch",
        "width": int(width),
        "height": int(height),
        "palette": palette,
        "paletteHex": palette_stops(palette),
        "stretch": {
            "clipLo": clip_lo,
            "clipHi": clip_hi,
            "clipPercentiles": [float(clip_percentiles[0]), float(clip_percentiles[1])],
        },
        "values": base64.b64encode(quant.reshape(-1).tobytes()).decode("ascii"),
        "nodataMask": base64.b64encode(bits.tobytes()).decode("ascii"),
    }


def decode_payload_rgba(payload: Dict[str, Any]) -> Tuple[int, int, np.ndarray]:
    """payload → (width, height, RGBA uint8 数组)（服务端侧参照实现）。

    与前端 ``applyRasterStretch`` 同算法（parity 锚）：量化档反归一 →
    色带 stops 线性插值 → nodata 透明。行主序（rows=height）。
    """
    width = int(payload["width"])
    height = int(payload["height"])
    stops = [_hex_to_rgb(h) for h in payload["paletteHex"]]
    quant = np.frombuffer(base64.b64decode(payload["values"]), dtype=np.uint8)
    quant = quant.reshape(height, width)
    mask = np.unpackbits(
        np.frombuffer(base64.b64decode(payload["nodataMask"]), dtype=np.uint8),
        count=height * width,
    ).reshape(height, width).astype(bool)

    # clipLo/clipHi 已在服务端量化时使用（payload.stretch 供绝对值域重建
    # 与图例标注）；解码侧着色只需量化档 → stops 插值。

    valid = (quant != QUANT_NODATA) & ~mask
    norm = np.where(valid, quant.astype(float) / (QUANT_NODATA - 1), 0.0)

    n_stops = len(stops)
    rgb_stops = np.array(stops, dtype=float)
    scaled = np.clip(norm * (n_stops - 1), 0, n_stops - 1)
    lower = np.floor(scaled).astype(int)
    upper = np.clip(lower + 1, 0, n_stops - 1)
    frac = (scaled - lower)[..., None]
    rgb = rgb_stops[lower] * (1 - frac) + rgb_stops[upper] * frac
    # floor(x+0.5)（四舍五入）与 TS 侧 Math.floor(v+0.5) 逐分支一致（parity
    # 锚）：np.rint 是银行家舍入（x.5 取偶），JS Uint8ClampedArray 是四舍
    # 五入 —— 两者在 x.5 偶边界分歧，故双端显式用 floor(x+0.5)。
    rgb = np.clip(np.floor(rgb + 0.5), 0, 255).astype(np.uint8)
    alpha = np.where(valid, 255, 0).astype(np.uint8)
    # nodata 契约 = 透明黑（RGB 置零 + alpha=0；与 TS 镜像的 continue 语义
    # 一致 —— parity 锚）。
    rgb = np.where(valid[..., None], rgb, 0).astype(np.uint8)
    rgba = np.dstack([rgb, alpha])
    return width, height, rgba


__all__ = [
    "STRETCH_PAYLOAD_VERSION",
    "DEFAULT_CLIP_PERCENTILES",
    "QUANT_NODATA",
    "palette_stops",
    "build_raster_stretch_payload",
    "decode_payload_rgba",
]
