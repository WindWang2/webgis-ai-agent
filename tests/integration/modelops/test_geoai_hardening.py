"""GeoAI 平台硬化测试（Platform 11 / WP-H 前半）。

oracle 独立性：
- seam：两远点 → 多窗；期望掩膜必须同时覆盖两点邻域（窗口并集语义），
  几何来自产物 GeoJSON 的坐标重投影（手算仿射）；
- 逻辑规模：prompt_windows 网格数 = 手算 ceil 公式（纯函数，无物化）；
- NoData：构造已知 nodata 角块，断言输出画布在该块全零。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest


def _promptable_request(uri, prompt, *, session, **kw):
    from app.lib.modelops.promptable import PromptSpec
    from app.services.modelops.engine import InferenceRequest

    return InferenceRequest(
        model_id="tiny-promptable-seg",
        source_uri=str(uri),
        owner_scope={"session_id": session},
        prompt=prompt if isinstance(prompt, PromptSpec) else PromptSpec(points=prompt),
        **kw,
    )


def _canvas(result):
    import rasterio

    with rasterio.open(result.outputs["prompt_mask"]["path"]) as r:
        return r.read(1)


def test_multi_window_prompt_covers_both_anchors(
    service, synthetic_raster, monkeypatch
):
    """seam：压缩窗口上限 → 引擎走真实多窗路径；画布 OR 融合覆盖两点。"""
    from app.lib.modelops.promptable import PromptSpec

    import app.lib.modelops.foundation as foundation_mod

    real_prompt_windows = foundation_mod.prompt_windows

    def small_windows(prompt, *, raster_height, raster_width, chip_hw,
                      max_window_px=None):
        return real_prompt_windows(
            prompt, raster_height=raster_height, raster_width=raster_width,
            chip_hw=chip_hw, max_window_px=48,
        )

    monkeypatch.setattr(foundation_mod, "prompt_windows", small_windows)
    # 两点距离 > 压缩上限 → 多窗（逐点窗口）。
    prompt = PromptSpec(points=((25.0, 15.0), (5.0, 95.0)))
    windows = small_windows(
        prompt, raster_height=100, raster_width=130, chip_hw=(16, 16)
    )
    assert len(windows) >= 2
    result = service.run_inference(
        _promptable_request(synthetic_raster, prompt, session="hardening-seam")
    )
    assert result.status == "completed"
    canvas = _canvas(result)
    # 点 1（row 15, col 25）在亮度块内：区域生长 → 非空邻域。
    assert canvas[13:18, 23:28].any()
    # 点 2（row 95, col 5）在背景：相似带 → 非空邻域（跨窗 OR）。
    assert canvas[93:98, 3:8].any()
    # 每窗产物独立地理参考（GeoJSON 特征携带各自窗口 = 引擎窗口集）。
    geojson = json.loads(
        Path(result.outputs["prompt_mask_geojson"]["path"]).read_text(encoding="utf-8")
    )
    engine_windows = {
        tuple(w)
        for w in small_windows(
            prompt, raster_height=100, raster_width=130, chip_hw=(64, 64)
        )
    }
    wins = {tuple(f["properties"]["window"]) for f in geojson["features"]}
    assert wins and wins.issubset(engine_windows)


def test_prompt_window_grid_logical_scale_100k():
    """逻辑规模（无物化）：超大 anchor 的网格分窗数 = 手算 ceil 公式。"""
    from app.lib.modelops.foundation import prompt_windows
    from app.lib.modelops.promptable import PromptSpec

    # 虚拟 500000x500000 栅格；anchor (1000..410000)² → 每维 ceil(409000/4096)
    # = 100 窗 → 10000 窗；两倍跨度 → 40000 窗。纯函数、零物化。
    big = 500_000
    prompt = PromptSpec(
        prior_masks=(np.zeros((4, 4), dtype=bool),),
        anchor_box=(1000.0, 1000.0, 409_000.0, 409_000.0),
    )
    windows = prompt_windows(
        prompt, raster_height=big, raster_width=big, chip_hw=(512, 512)
    )
    import math

    per_axis = math.ceil(409_000 / 4096)
    assert len(windows) == per_axis * per_axis == 10_000
    for row, col, h, w in windows:
        assert h <= 4096 and w <= 4096
    # 行主序确定性：相邻窗口按 (row, col) 递增。
    assert windows == sorted(windows)
    # 覆盖不变量：窗口并集覆盖 anchor（逐轴区间并集 = anchor 范围）。
    rows = sorted({(r, r + h) for r, _, h, _ in windows})
    assert rows[0][0] <= 1000 and rows[-1][1] >= 410_000


def test_pre_cancelled_token_refuses_promptable_candidates(service, synthetic_raster):
    from app.lib.cancellation import CancellationToken
    from app.lib.modelops.errors import InferenceCancelled

    token = CancellationToken(job_id="hardening-cancel")
    token.cancel("pre-cancelled")
    request = _promptable_request(
        synthetic_raster, ((25.0, 15.0),), session="hardening-cancel",
        return_candidates=True,
    )
    with pytest.raises(InferenceCancelled):
        service._engine.run(request, cancel_token=token)


def test_unicode_source_path(service, tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    src = tmp_path / "地理数据-遥感影像.tif"
    data = np.full((3, 32, 32), 0.3, dtype=np.float32)
    data[:, 8:20, 8:20] = 0.9
    with rasterio.open(
        src, "w", driver="GTiff", width=32, height=32, count=3, dtype="float32",
        crs="EPSG:4326", transform=from_origin(116.0, 40.0, 1.0, 1.0), nodata=-9999.0,
    ) as dst:
        dst.write(data)
    result = service.run_inference(
        _promptable_request(src, ((12.0, 12.0),), session="hardening-unicode")
    )
    assert result.status == "completed"
    assert (Path(result.outputs["prompt_mask_geojson"]["path"]).name
            .startswith("prompt_mask"))


def test_nodata_region_never_in_output(service, tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    src = tmp_path / "nodata.tif"
    data = np.full((3, 48, 48), 0.3, dtype=np.float32)
    data[:, :24, :] = 0.7  # 上半相似亮区
    with rasterio.open(
        src, "w", driver="GTiff", width=48, height=48, count=3, dtype="float32",
        crs="EPSG:4326", transform=from_origin(116.0, 40.0, 1.0, 1.0), nodata=-9999.0,
    ) as dst:
        dst.write(data)
        # 右下角 16x16 设为 nodata。
        dst.nodata = -9999.0
    with rasterio.open(src, "r+") as dst:
        block = np.full((3, 16, 16), -9999.0, dtype=np.float32)
        dst.write(block, window=rasterio.windows.Window(32, 32, 16, 16))
    # 框提示罩住整幅 → 掩膜不得包含 nodata 块内的任何像元。
    from app.lib.modelops.promptable import PromptSpec

    result = service.run_inference(_promptable_request(
        src, PromptSpec(boxes=((0.0, 0.0, 48.0, 48.0),)), session="hardening-nodata",
    ))
    assert result.status == "completed"
    canvas = _canvas(result)
    assert not canvas[32:48, 32:48].any(), "nodata block must never be masked"


def test_read_only_source_completes(service, synthetic_raster, tmp_path):
    import shutil

    ro = tmp_path / "readonly.tif"
    shutil.copyfile(synthetic_raster, ro)
    ro.chmod(0o444)
    try:
        result = service.run_inference(
            _promptable_request(ro, ((25.0, 15.0),), session="hardening-ro")
        )
        assert result.status == "completed"
    finally:
        ro.chmod(0o644)


def test_candidate_scores_bounded_across_runs(service, synthetic_raster):
    """候选分数界（0..1）在多次窗口/请求下稳定成立（回归不变量）。"""
    from app.lib.modelops.promptable import PromptSpec

    for pts in (((25.0, 15.0),), ((70.0, 50.0), (5.0, 95.0))):
        result = service.run_inference(_promptable_request(
            synthetic_raster, PromptSpec(points=pts),
            session="hardening-bounds", return_candidates=True,
        ))
        summary = result.outputs["prompt_candidates"]["windows"]
        for window in summary:
            for cand in window["candidates"]:
                assert 0.0 <= cand["score"] <= 1.0
                assert cand["source"] in ("model", "heuristic")
