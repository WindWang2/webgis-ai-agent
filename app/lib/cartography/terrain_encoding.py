"""Terrarium elevation encoding — DEM ↔ MapLibre raster-dem data plane (ADR-0199 M3).

MapLibre 原生 terrain/hillshade 消费 **terrarium 编码**的 raster-dem 瓦片：
``elevation_m = (R*256 + G + B/256) - 32768``。

本模块是该编码的纯 numpy 实现（编码 + 解码 + 已知答案测试），fail-closed：
- 无效高程（nodata/NaN/Inf/哨兵）→ 透明像素（RGB 全 0），**绝不编码为
  0 高程**（0 是合法海拔 —— 静默编码等于伪造地形）。
- ``valid`` 掩码声称有效但值非有限 → 结构化错误（掩码与数据自相矛盾 =
  上游 bug，静默处理会掩盖它）。

哨兵 ``-9999`` 与 ``app/services/rs/stac_client.py`` 的 DEM 哨兵同值
（lib 层不反向依赖 services 层，常量在此声明、注释锚定出处）。

垂直语义边界（诚实披露）：单位 = 米（``vertical_unit: "m"`` 契约）；无
垂直基准转换（EGM96/EGM2008 大地水准面 vs 椭球高的换算不存在）—— 混源
DEM 的基准假设由调用方在 methodology 层披露（既有 ADR 同口径）。
"""
from __future__ import annotations

import numpy as np

#: 编码格式名（MapLibre raster-dem encoding 词表成员）。
TERRAIN_ENCODING = "terrarium"

#: DEM 未声明 nodata 时的哨兵值（与 rs/stac_client DEM 哨兵同值）。
DEM_SENTINEL_NODATA = -9999.0

#: terrarium 偏移（elevation + OFFSET 后才写入无符号 RGB）。
_TERRARIUM_OFFSET = 32768.0


class TerrainEncodingError(ValueError):
    """结构化编码错误（code 供降级链/质量门引用，绝不静默吞）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _validate_finite(elevation_m: np.ndarray, valid: np.ndarray) -> None:
    """valid 掩码内的值必须有限 —— 违反即上游 bug，结构化抛错。"""
    bad = valid & ~np.isfinite(elevation_m)
    if bool(np.any(bad)):
        raise TerrainEncodingError(
            "TERRAIN_NON_FINITE_ELEVATION",
            f"{int(bad.sum())} pixel(s) marked valid but non-finite "
            "(NaN/Inf) — upstream elevation field is broken; refusing to encode",
        )


def encode_terrarium(
    elevation_m: np.ndarray,
    valid: np.ndarray,
) -> np.ndarray:
    """米高程 → terrarium RGB uint8（H, W, 3）。

    ``valid`` 为 False 的像素输出 (0,0,0)（调用方渲染为透明）。编码前把
    elevation 量化到 1/256 m 分辨率（B 通道承载分数部分）。
    """
    elev = np.asarray(elevation_m, dtype=np.float64)
    mask = np.asarray(valid, dtype=bool)
    if elev.shape != mask.shape or elev.ndim != 2:
        raise TerrainEncodingError(
            "TERRAIN_SHAPE_MISMATCH",
            f"elevation {elev.shape} vs valid {mask.shape}; both must be 2-D and equal",
        )
    _validate_finite(elev, mask)

    shifted = np.where(mask, elev + _TERRARIUM_OFFSET, 0.0)
    r = np.floor(shifted / 256.0)
    g = np.floor(shifted) - r * 256.0
    b = (shifted - np.floor(shifted)) * 256.0

    rgb = np.zeros((*elev.shape, 3), dtype=np.uint8)
    rgb[..., 0] = np.where(mask, r, 0.0).astype(np.uint8)
    rgb[..., 1] = np.where(mask, g, 0.0).astype(np.uint8)
    rgb[..., 2] = np.where(mask, np.floor(b), 0.0).astype(np.uint8)
    return rgb


def decode_terrarium(rgb: np.ndarray) -> np.ndarray:
    """terrarium RGB → 米高程（float64）。透明像素 (0,0,0) 解码为
    ``-32768``（编码 0 像素的数学逆）—— 调用方应以 valid 掩码为准。"""
    arr = np.asarray(rgb)
    if arr.shape[-1] != 3:
        raise TerrainEncodingError(
            "TERRAIN_SHAPE_MISMATCH", f"expected (..., 3) RGB, got {arr.shape}"
    )
    r = arr[..., 0].astype(np.float64)
    g = arr[..., 1].astype(np.float64)
    b = arr[..., 2].astype(np.float64)
    return (r * 256.0 + g + b / 256.0) - _TERRARIUM_OFFSET
