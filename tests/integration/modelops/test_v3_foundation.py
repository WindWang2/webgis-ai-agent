"""V3 §H：foundation 模型适配面（prompt 坐标变换 / tile 策略 / 端到端）。"""
from __future__ import annotations

import numpy as np
import pytest
from rasterio.transform import from_origin

from app.lib.modelops.foundation import (
    TILE_PROMPT_ANCHORED,
    FoundationModelCaps,
    prompt_span,
    prompt_windows,
    prompts_to_pixel,
    window_local_prompts,
)
from app.lib.modelops.promptable import PromptSpec


def test_prompts_to_pixel_geographic_transform():
    transform = from_origin(116.0, 40.0, 0.5, 0.5)  # 0.5°/px
    prompt = PromptSpec(
        points=((116.25, 39.75),),
        boxes=((116.0, 39.5, 1.0, 1.0),),
    )
    pixel = prompts_to_pixel(prompt, transform=transform)
    # 点：地图 (116.25, 39.75) → 像素 (中心口径) col=0.5, row=0.5。
    assert pixel.points[0] == pytest.approx((0.5, 0.5))
    # 框：1° 跨度 = 2 像素。
    bx, by, bw, bh = pixel.boxes[0]
    assert (bw, bh) == pytest.approx((2.0, 2.0))


def test_prompt_windows_single_vs_anchored():
    prompt = PromptSpec(points=((50, 50),))
    windows = prompt_windows(
        prompt, raster_height=1000, raster_width=1000, chip_hw=(64, 64)
    )
    assert len(windows) == 1
    row, col, h, w = windows[0]
    assert row <= 50 < row + h and col <= 50 < col + w
    # 跨距超限 → prompt 锚定多窗口（每 prompt 一个）。
    spread = PromptSpec(points=((10, 10), (900, 900)), boxes=((400, 400, 200, 200),))
    multi = prompt_windows(
        spread, raster_height=2000, raster_width=2000, chip_hw=(64, 64),
        max_window_px=512,
    )
    assert len(multi) == 3  # 2 点 + 1 框
    # 每 prompt 窗口包含其锚点。
    assert multi[0][0] <= 10 < multi[0][0] + multi[0][2]
    assert multi[1][0] <= 900 < multi[1][0] + multi[1][2]


def test_prompt_span_and_window_local():
    prompt = PromptSpec(points=((30, 40),), boxes=((100, 100, 50, 60),))
    span = prompt_span(prompt)
    assert span == (30, 40, 150, 160)
    local = window_local_prompts(prompt, row=20, col=10)
    assert local.points[0] == (20, 20)
    assert local.boxes[0] == (90, 80, 50, 60)


def test_foundation_caps_projection():
    caps = FoundationModelCaps(tile_strategy=TILE_PROMPT_ANCHORED, text_prompt=True)
    payload = caps.as_dict()
    assert payload["tile_strategy"] == "prompt_anchored"
    assert payload["text_prompt"] is True
    assert "point" in payload["prompt_modes"]


# ── 端到端：地理坐标 prompt ──────────────────────────────────────────


def test_geographic_prompt_end_to_end(service, synthetic_raster):
    """prompt_crs=True：地理坐标 prompt 与等价像素 prompt 产物逐位一致。

    （坐标变换正确性的最强 oracle——两条路径共用同一 provider 确定性。）"""
    import base64

    from app.services.modelops.engine import InferenceRequest

    # 亮块位置 (行 10:30, 列 20:45) → 地图坐标：x = 116 + col, y = 40 - row。
    # 像素 (col=30, row=20) 的地图坐标 = (146.0, 20.0)（像素角点口径）。
    geo_result = service.run_inference(
        InferenceRequest(
            model_id="tiny-promptable-seg",
            source_uri=str(synthetic_raster),
            owner_scope={"session_id": "s-geo-prompt"},
            prompt=PromptSpec(points=((146.0, 20.0),)),
            prompt_crs=True,
        )
    )
    pixel_result = service.run_inference(
        InferenceRequest(
            model_id="tiny-promptable-seg",
            source_uri=str(synthetic_raster),
            owner_scope={"session_id": "s-geo-prompt2"},
            prompt=PromptSpec(points=((30.0, 20.0),)),
            prompt_crs=False,
        )
    )
    assert geo_result.status in ("completed", "reused")
    assert pixel_result.status in ("completed", "reused")
    import rasterio

    with rasterio.open(geo_result.outputs["prompt_mask"]["path"]) as src:
        geo_mask = src.read(1)
    with rasterio.open(pixel_result.outputs["prompt_mask"]["path"]) as src:
        pixel_mask = src.read(1)
    # 变换正确 → 两种坐标系的同一 prompt 命中同一位置（掩膜一致）。
    assert geo_mask.sum() > 0
    np.testing.assert_array_equal(geo_mask, pixel_mask)
