"""GeoPrompt artifact 平台集成（Platform 11 / WP-A engine+service 面）。

oracle 独立性：期望直接基于 conftest 合成栅格的已知亮度块布局
（rows/cols 区间是构造参数，不是被测输出）与手导仿射（EPSG:4326、
1°/px、左上 (116, 40)）。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.lib.modelops.errors import PlanningError, PromptArtifactError


def _point_request(service_uri, prompt, *, session="geoai", audit=None, artifact_id=None):
    from app.services.modelops.engine import InferenceRequest

    return InferenceRequest(
        model_id="tiny-promptable-seg",
        source_uri=str(service_uri),
        owner_scope={"session_id": session},
        prompt=prompt,
        prompt_artifact_id=artifact_id,
        prompt_audit=audit,
    )


def test_geo_prompt_polygon_map_crs_end_to_end(service, synthetic_raster):
    from shapely.geometry import shape
    from shapely.ops import unary_union

    from app.lib.modelops.geo_prompt import GeoPromptArtifact, GeoPromptTarget

    # 地图坐标多边形罩住纯背景区（rows 48:52 / cols 2:6 ⇔ map x∈[118,123]、
    # y∈[-13,-8]；EPSG:4326、1°/px、原点 (116,40) 手导换算）。参考模型
    # 语义 = prior ∩ 窗内亮度相似：背景与全窗中值相似 ⇒ 掩膜脚印 ≈ 先验。
    artifact = GeoPromptArtifact(
        crs="EPSG:4326",
        polygons=(((118.0, -8.0), (123.0, -8.0), (123.0, -13.0), (118.0, -13.0)),),
        target=GeoPromptTarget(model_id="tiny-promptable-seg"),
        created_by="integration-test",
    )
    compiled = service.compile_geo_prompt(
        artifact.to_payload(), str(synthetic_raster)
    )
    audit = compiled["audit"]
    assert audit["coordinate_space"] == "map→pixel"
    assert audit["derived_mask_source"] == "polygon/polyline rasterized"
    assert audit["roundtrip_max_error_px"] <= 1e-6
    assert audit["target"]["model_id"] == "tiny-promptable-seg"
    assert audit["artifact_id"] == artifact.artifact_id == compiled["artifact_id"]
    # 编译审计的 anchor（半开像素）：cols 2..7 / rows 48..53（as_dict → list）。
    assert audit["anchor_box"] == [2, 48, 7, 53]

    result = service.run_inference(
        _point_request(
            synthetic_raster, compiled["prompt"],
            session="geoai-polygon-e2e",
            audit=audit, artifact_id=compiled["artifact_id"],
        )
    )
    assert result.status == "completed"
    manifest = result.manifest
    # provenance：artifact 身份 + 审计进 manifest；指纹身份进 postprocess。
    assert manifest["postprocess"]["prompt_artifact_id"] == artifact.artifact_id
    assert manifest["postprocess"]["prompt_prior_digest"]
    assert manifest["prompt_audit"]["artifact_id"] == artifact.artifact_id
    geojson = json.loads(
        Path(result.outputs["prompt_mask_geojson"]["path"]).read_text(encoding="utf-8")
    )
    assert geojson["features"], "polygon prompt must yield a non-empty mask"
    footprint = unary_union(
        [shape(f["geometry"]) for f in geojson["features"]]
    )
    minx, miny, maxx, maxy = footprint.bounds
    # 掩膜脚印 ≈ 多边形先验（栅格化粒度 ±1px = 1°）。
    assert 117.0 <= minx <= 118.6 and 122.4 <= maxx <= 124.0
    assert -13.6 <= miny <= -12.4 and -8.6 <= maxy <= -7.4


def test_prior_mask_content_digest_invalidates_reuse(service, synthetic_raster):
    """同数量不同内容的先验掩膜不得命中同一 reuse key（count-only 旧洞）。"""
    from app.lib.modelops.promptable import PromptSpec

    mask_block2 = np.zeros((100, 130), dtype=bool)
    mask_block2[60:80, 90:120] = True
    mask_block1 = np.zeros((100, 130), dtype=bool)
    mask_block1[10:30, 20:45] = True
    # 两次运行使用同一 anchor（窗口放置一致）——唯一差异 = mask 内容。
    anchor = (20.0, 10.0, 25.0, 20.0)

    def run(mask, session):
        return service.run_inference(
            _point_request(
                synthetic_raster,
                PromptSpec(prior_masks=(mask,), anchor_box=anchor),
                session=session,
            )
        )

    first = run(mask_block1, "geoai-digest")
    assert first.status == "completed"
    second = run(mask_block1, "geoai-digest")
    assert second.reused, "identical prior content must reuse"
    third = run(mask_block2, "geoai-digest")
    assert not third.reused, "different prior content (same count) must miss"


def test_prompt_target_model_binding_typed_refusal(service, synthetic_raster):
    from app.lib.modelops.promptable import PromptSpec

    request = _point_request(
        synthetic_raster,
        PromptSpec(points=((30.0, 20.0),)),
        session="geoai-binding",
        audit={"target": {"model_id": "tiny-sar-detector"}},
    )
    with pytest.raises(PlanningError, match="bound to model"):
        service.run_inference(request)


def test_mask_sidecar_digest_fail_closed(service, synthetic_raster, tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    from app.lib.data.fingerprints import sha256_of_file

    sidecar = tmp_path / "prior.tif"
    mask = np.zeros((100, 130), dtype=np.uint8)
    mask[10:30, 20:45] = 1
    with rasterio.open(
        sidecar, "w", driver="GTiff", width=130, height=100, count=1,
        dtype="uint8", transform=from_origin(116.0, 40.0, 1.0, 1.0),
    ) as dst:
        dst.write(mask, 1)

    payload = {
        "mask_ref": {
            "path": str(sidecar), "sha256": sha256_of_file(str(sidecar)), "band": 1,
        }
    }
    compiled = service.compile_geo_prompt(payload, str(synthetic_raster))
    assert compiled["audit"]["derived_mask_source"] == "mask_sidecar"
    assert compiled["prompt"].prior_masks[0][10:30, 20:45].all()

    # 篡改 sidecar 内容 → digest 失配 → typed 拒绝。
    mask[15, 25] = 0
    with rasterio.open(sidecar, "r+") as dst:
        dst.write(mask, 1)
    with pytest.raises(PromptArtifactError, match="digest mismatch"):
        service.compile_geo_prompt(payload, str(synthetic_raster))


def test_relative_mask_path_requires_root(service, synthetic_raster):
    payload = {
        "mask_ref": {"path": "relative/prior.tif", "sha256": "a" * 64, "band": 1}
    }
    with pytest.raises(PromptArtifactError, match="mask_root"):
        service.compile_geo_prompt(payload, str(synthetic_raster))


def test_reference_layer_prompt_end_to_end(service, synthetic_raster, tmp_path):
    """参考图层 prompt：以块 2 为参考非零层 → 先验即块 2 → 掩膜非空。"""
    import rasterio

    ref = tmp_path / "ref.tif"
    layer = np.zeros((100, 130), dtype=np.uint8)
    layer[60:80, 90:120] = 7  # 任意非零值
    with rasterio.open(
        ref, "w", driver="GTiff", width=130, height=100, count=1, dtype="uint8",
    ) as dst:
        dst.write(layer, 1)
    payload = {"reference_layer": {"uri": str(ref), "band": 1, "strategy": "nonzero"}}
    compiled = service.compile_geo_prompt(payload, str(synthetic_raster))
    assert compiled["audit"]["derived_mask_source"] == "reference_layer"
    prior = compiled["prompt"].prior_masks[0]
    assert prior[60:80, 90:120].all() and not prior[:60].any()
    result = service.run_inference(
        _point_request(
            synthetic_raster, compiled["prompt"],
            session="geoai-reflayer", audit=compiled["audit"],
            artifact_id=compiled["artifact_id"],
        )
    )
    assert result.status == "completed"
    assert result.manifest["prompt_audit"]["derived_mask_source"] == "reference_layer"
