"""GeoAI E2E 验证（Platform 11 / WP-H 后半）。

- provenance replay：同 artifact 两次独立 run（屏蔽整 run reuse）→
  身份相关 manifest 段（model/provider/input/preprocess/postprocess）与
  输出几何逐字段一致（run_id/时间戳除外）；
- known-answer 矩阵：点/框/多边形/参考层 × 已知合成几何 → 掩膜脚印
  期望（手导，不来自被测实现）；
- provider 缺席：text prompt 对未声明 text 的 provider = typed 拒绝。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.lib.modelops.errors import CompatibilityError


def _req(uri, prompt, *, session, **kw):
    from app.services.modelops.engine import InferenceRequest

    return InferenceRequest(
        model_id="tiny-promptable-seg",
        source_uri=str(uri),
        owner_scope={"session_id": session},
        prompt=prompt,
        **kw,
    )


def _geojson_features(result):
    return json.loads(
        Path(result.outputs["prompt_mask_geojson"]["path"]).read_text(encoding="utf-8")
    )["features"]


IDENTITY_SECTIONS = ("model", "provider", "input", "preprocess", "postprocess",
                     "tile_plan", "prompt", "prompt_audit")


def test_provenance_replay_identity_stable(service, synthetic_raster, monkeypatch):
    from app.lib.modelops.geo_prompt import GeoPromptArtifact

    artifact = GeoPromptArtifact(
        crs="EPSG:4326",
        polygons=(((118.0, -8.0), (123.0, -8.0), (123.0, -13.0), (118.0, -13.0)),),
        target=None,
    )
    compiled = service.compile_geo_prompt(artifact.to_payload(), str(synthetic_raster))

    def run_once(session):
        return service.run_inference(_req(
            synthetic_raster, compiled["prompt"], session=session,
            prompt_artifact_id=compiled["artifact_id"],
            prompt_audit=compiled["audit"],
        ))

    # 复用键含 owner scope（隔离设计）→ replay 必须同 session；
    # 第二次运行屏蔽整 run reuse 以检验"真重放"而非缓存命中。
    first = run_once("replay")
    monkeypatch.setattr(service._reuse, "lookup", lambda key, owner_scope: None)
    second = run_once("replay")
    assert first.status == second.status == "completed"
    for section in IDENTITY_SECTIONS:
        assert first.manifest[section] == second.manifest[section], section
    # reuse_key 全字段一致（同模型/输入/prompt identity → 同指纹）。
    assert first.reuse_key == second.reuse_key
    # 输出几何一致（确定性重放）。
    assert _geojson_features(first) == _geojson_features(second)


def test_known_answer_matrix(service, synthetic_raster):
    """点/框/多边形在已知合成几何上的掩膜脚印（手导期望）。"""
    from app.lib.modelops.promptable import PromptSpec

    # 框提示在背景相似区（窗内相似语义 → 框∩相似 ≈ 框；亮块与全窗中值
    # 不相似是 provider 语义，known-answer 选背景区才是该语义的真值路径）。
    import rasterio

    box_prompt = PromptSpec(boxes=((2.0, 40.0, 10.0, 8.0),))
    result = service.run_inference(_req(
        synthetic_raster, box_prompt, session="ka-box"
    ))
    with rasterio.open(result.outputs["prompt_mask"]["path"]) as r:
        canvas = r.read(1)
    ys, xs = np.nonzero(canvas == 1)
    assert len(xs) > 0
    # 命中像元全部落在框内（x∈[2,12), y∈[40,48)）。
    assert xs.min() >= 2 and xs.max() < 12
    assert ys.min() >= 40 and ys.max() < 48

    # 点提示在亮块中心：种子不相似 → 诚实单点目标（退化语义已知）。
    pt_prompt = PromptSpec(points=((30.0, 20.0),))
    result2 = service.run_inference(_req(
        synthetic_raster, pt_prompt, session="ka-point"
    ))
    with rasterio.open(result2.outputs["prompt_mask"]["path"]) as r:
        canvas2 = r.read(1)
    ys2, xs2 = np.nonzero(canvas2 == 1)
    assert len(xs2) == 1
    assert (ys2[0], xs2[0]) == (20, 30)


def test_reference_layer_known_answer_with_nodata(service, synthetic_raster, tmp_path):
    """参考层 × nodata：先验=参考非零层；nodata 像元永不入掩膜。"""
    import rasterio

    ref = tmp_path / "ref_ka.tif"
    layer = np.zeros((100, 130), dtype=np.uint8)
    layer[40:56, 2:24] = 5  # 背景相似区子块（先验∩相似 ≈ 先验）
    with rasterio.open(
        ref, "w", driver="GTiff", width=130, height=100, count=1, dtype="uint8",
    ) as dst:
        dst.write(layer, 1)
    payload = {"reference_layer": {"uri": str(ref), "band": 1, "strategy": "nonzero"}}
    compiled = service.compile_geo_prompt(payload, str(synthetic_raster))
    result = service.run_inference(_req(
        synthetic_raster, compiled["prompt"], session="ka-ref",
        prompt_artifact_id=compiled["artifact_id"], prompt_audit=compiled["audit"],
    ))
    with rasterio.open(result.outputs["prompt_mask"]["path"]) as r:
        canvas = r.read(1)
    # 掩膜 ⊆ 参考层非零区（先验∩相似），且非空。
    inside = canvas[40:56, 2:24]
    assert 0 < int(inside.sum()) <= 16 * 22
    assert not np.any(canvas & (layer == 0))


def test_text_prompt_refused_without_provider_declaration(service, synthetic_raster):
    from app.lib.modelops.promptable import PromptSpec

    with pytest.raises(CompatibilityError, match="prompt modes"):
        service.run_inference(_req(
            synthetic_raster,
            PromptSpec(text="water bodies in the north"),
            session="absence-text",
        ))


def test_artifact_replay_via_tool_composes(service, synthetic_raster, monkeypatch):
    """artifact → inspect → run → refine 的完整链（服务面组合 E2E）。"""
    artifact = {
        "crs": "EPSG:4326",
        "polygons": [[[118.0, -8.0], [123.0, -8.0], [123.0, -13.0], [118.0, -13.0]]],
    }
    compiled = service.compile_geo_prompt(artifact, str(synthetic_raster))
    first = service.run_inference(_req(
        synthetic_raster, compiled["prompt"], session="e2e-compose",
        prompt_artifact_id=compiled["artifact_id"], prompt_audit=compiled["audit"],
        return_candidates=True,
    ))
    assert "prompt_candidates" in first.outputs
    result, meta = service.run_prompt_refine(
        "tiny-promptable-seg", str(synthetic_raster),
        first.outputs["prompt_candidates"]["path"], 0,
        session_id="e2e-compose",
    )
    assert result.status == "completed"
    assert meta["prior_pixels"] > 0
    assert result.manifest["prompt_audit"]["derived_mask_source"] == "mask_sidecar"
