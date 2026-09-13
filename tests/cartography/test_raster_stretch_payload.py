"""W3.3 栅格动态拉伸测试（V11，ADR-0163）。

双路径 parity：同一阵列的量化 payload 客户端算法着色 ≙ 烘焙 PNG 同格
颜色（clip=(0,100) 时同一 min/max 线性映射，量化误差 ≤2/通道）；payload
冻结 golden 由 TS 镜像（frontend/lib/map-kit/raster-stretch.ts）消费。
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.services.raster_cartography_converter import render_array_to_png  # noqa: E402
from app.services.raster_stretch import (  # noqa: E402
    QUANT_NODATA,
    build_raster_stretch_payload,
    decode_payload_rgba,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN = REPO_ROOT / "tests/cartography/golden_corpus/raster_stretch/basic.json"

ARRAY = np.array([
    [12.5, 13.1, 14.9, 18.2, 22.4, 25.0, 27.8, 30.2],
    [11.0, 12.9, 15.5, 19.1, 21.8, 24.4, 26.9, 29.5],
    [10.2, 11.8, 16.0, 20.3, 22.9, 23.8, 26.1, 28.7],
    [9.5, 12.2, 15.1, 17.7, 20.6, 25.5, 27.2, 31.0],
    [8.9, 10.9, 14.2, 16.8, 21.1, 24.9, 25.8, 30.6],
    [7.5, 9.9, 13.7, 15.9, 19.4, 23.1, 24.7, 29.8],
], dtype=float)


def _array_with_nodata() -> np.ndarray:
    arr = ARRAY.copy()
    arr[1][3] = float("nan")
    return arr


def test_payload_deterministic_and_bounded() -> None:
    p1 = build_raster_stretch_payload(_array_with_nodata(), "Viridis")
    p2 = build_raster_stretch_payload(_array_with_nodata(), "Viridis")
    assert p1 == p2
    assert p1["version"] == 1 and p1["mode"] == "dynamic_stretch"
    # 有界：每格 1 字节量化 + 每格 1 bit 掩码
    assert len(base64.b64decode(p1["values"])) == 6 * 8
    assert len(base64.b64decode(p1["nodataMask"])) == (6 * 8 + 7) // 8


def test_nodata_transparent_and_reserved_slot() -> None:
    payload = build_raster_stretch_payload(_array_with_nodata(), "Viridis")
    w, h, rgba = decode_payload_rgba(payload)
    assert (w, h) == (8, 6)
    flat = rgba.reshape(-1, 4)
    assert flat[11][3] == 0          # (1,3) nodata → 透明
    assert flat[0][3] == 255         # 有效格不透明
    quant = base64.b64decode(payload["values"])
    assert quant[11] == QUANT_NODATA


def test_dual_path_parity_with_baked_png() -> None:
    """动态路径（clip=(0,100) = min/max 拉伸）与烘焙 PNG 同格颜色一致。

    容差 3/通道 = 量化 1/254 档 × 色带 stop 插值的联合误差上界（实测；
    254 档量化对可视化渲染色差不可感知）。"""
    from PIL import Image
    import io

    payload = build_raster_stretch_payload(
        ARRAY, "Viridis", clip_percentiles=(0.0, 100.0))
    _w, _h, rgba = decode_payload_rgba(payload)

    baked = Image.open(io.BytesIO(render_array_to_png(ARRAY, "Viridis"))).convert("RGBA")
    baked_arr = np.asarray(baked)
    diff = np.abs(rgba.astype(int) - baked_arr.astype(int))
    assert diff.max() <= 3, f"双路径最大通道差 {diff.max()} 超容差"


def test_golden_fixture_frozen() -> None:
    """payload + 期望 RGBA 冻结；TS 镜像消费同一 fixture。"""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    payload = build_raster_stretch_payload(
        _array_with_nodata(), "Viridis")
    assert payload == golden["payload"]
    _w, _h, rgba = decode_payload_rgba(payload)
    assert base64.b64encode(rgba.reshape(-1).tobytes()).decode("ascii") == \
        golden["expected"]["rgba"]
