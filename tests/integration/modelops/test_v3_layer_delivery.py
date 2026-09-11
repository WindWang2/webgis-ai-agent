"""V3 §I 集成：推理产物 → 地图图层交付（样式建议 + provenance 链）。"""
from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin


@pytest.fixture()
def seg_raster(tmp_path):
    path = tmp_path / "seg_in.tif"
    data = np.full((3, 64, 64), 0.2, dtype=np.float32)
    data[:, 10:30, 20:45] = 0.9
    with rasterio.open(
        path, "w", driver="GTiff", width=64, height=64, count=3,
        dtype="float32", crs="EPSG:4326",
        transform=from_origin(116.0, 40.0, 1.0, 1.0), nodata=-9999.0,
    ) as dst:
        dst.write(data)
    return path


def _run_segmentation(service, seg_raster):
    from app.services.modelops.engine import InferenceRequest

    return service.run_inference(
        InferenceRequest(
            model_id="tiny-landcover-seg",
            source_uri=str(seg_raster),
            owner_scope={"session_id": "s-layer"},
        )
    )


def test_style_recommendation_deterministic_and_role_aware():
    from app.services.modelops.layer_delivery import style_recommendation

    classes = style_recommendation("classes", class_names=("background", "bright", "mid"))
    assert classes["kind"] == "raster_categorical"
    assert classes["palette"][0] == "#4C78A8"
    change = style_recommendation("classes", class_names=("no_change", "change"))
    assert change["palette"] == ["#D9D9D9", "#C44E52"]
    confidence = style_recommendation("confidence")
    assert confidence["kind"] == "raster_continuous"
    det = style_recommendation("detections")
    assert det["kind"] == "vector"
    # 确定性（同输入同输出）。
    assert style_recommendation("classes", class_names=("background", "bright", "mid")) == classes


def test_inference_outputs_become_session_layers(service, seg_raster, monkeypatch):
    """端到端：run → publish_layers → 会话 ref 可解析（含 provenance）。"""
    import asyncio

    from app.services.session_data import session_data_manager as sdm

    result = _run_segmentation(service, seg_raster)
    from app.tools.modelops_tools import register_modelops_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_modelops_tools(registry)
    result_payload = {
        "outputs": {
            role: {"path": out.get("path"), "data_object_id": out.get("data_object_id")}
            for role, out in result.outputs.items()
        },
        "manifest": result.manifest,
    }
    published = asyncio.run(
        registry.dispatch(
            "modelops_publish_layers",
            {"outputs": result_payload["outputs"], "manifest": result.manifest,
             "session_id": "s-layer"},
            session_id="s-layer",
        )
    )
    assert published["registered_count"] >= 1, published
    layers = published["layers"]
    classes_layer = next(l for l in layers if l["role"] == "classes")
    assert classes_layer["layer_kind"] == "raster"
    assert classes_layer["registered"] is True
    assert classes_layer["ref_id"]
    # provenance 链完整（模型 → run 可追溯）。
    prov = classes_layer["provenance"]
    assert prov["model_id"] == "tiny-landcover-seg"
    assert prov["model_version"] == "1.0.0"
    assert prov["checksum"]
    assert prov["run_id"] == result.run_id
    # 会话 ref 真实可解析（图层路由的数据源）。
    stored = asyncio.run(sdm.get("s-layer", classes_layer["ref_id"]))
    assert stored is not None and stored.get("path")
    # ref payload 带 provenance 字段（前端可见的模型溯源）。
    assert stored["_modelops"]["model_id"] == "tiny-landcover-seg"
    # style recommendation 附带（Cartography/Workbench 消费面）。
    assert classes_layer["style"]["kind"] == "raster_categorical"
    # finalize 建议（show_refs 可直接喂 display_layer/finalize_display）。
    assert classes_layer["ref_id"] in published["suggested_show_refs"]


def test_vector_layer_package_requires_feature_collection(service, tmp_path):
    """矢量产物无 FeatureCollection 时 honest 不注册（不虚报）。"""
    from app.services.modelops.layer_delivery import build_layer_packages

    packages = build_layer_packages(
        {"detections": {"path": "/tmp/x.geojson"}},
        {"task_type": "object_detection", "model": {"model_id": "m", "model_version": "1"}},
    )
    assert packages[0]["layer_kind"] == "vector"
    assert packages[0]["data"] is None  # 由 tool 层注入 FeatureCollection
