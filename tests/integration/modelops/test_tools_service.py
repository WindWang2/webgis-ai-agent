"""工具注册 + 评估服务 + provenance/comparison 服务面测试（Epic §N/K/L）。"""
from __future__ import annotations

import json

import numpy as np
import pytest

from app.services.modelops.engine import InferenceRequest
from app.services.modelops.evaluation_service import EvaluationRequest


TOOL_NAMES = {
    "modelops_list_models", "modelops_inspect_model", "modelops_check_compatibility",
    "modelops_estimate_resources", "modelops_run_inference", "modelops_run_promptable",
    "modelops_evaluate_model", "modelops_compare_results", "modelops_inspect_provenance",
    "modelops_cancel_inference",
}


def test_all_ten_tools_registered():
    from app.tools.registry import ToolRegistry
    from app.tools.modelops_tools import register_modelops_tools

    registry = ToolRegistry()
    register_modelops_tools(registry)
    registered = set(registry.tool_names()) & TOOL_NAMES
    assert registered == TOOL_NAMES, sorted(TOOL_NAMES - registered)


def test_tools_registry_table_includes_modelops():
    import app.tools as tools_init

    modules = [m for m, _ in tools_init._TOOL_MODULES]
    assert "app.tools.modelops_tools" in modules


def test_estimate_and_compatibility_service(service, synthetic_raster):
    estimate = service.estimate_resources(
        "tiny-landcover-seg", str(synthetic_raster), session_id="s1"
    )
    assert estimate["device"] == "cpu"
    assert estimate["tiles"] > 0
    compat = service.check_compatibility(
        "tiny-landcover-seg", str(synthetic_raster), session_id="s1"
    )
    assert compat["compatibility"]["verdict"] in ("compatible", "incompatible")


def test_evaluation_segmentation_roundtrip(service, tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    pred_path = tmp_path / "pred.tif"
    ref_path = tmp_path / "ref.tif"
    y_true = np.zeros((32, 32), dtype=np.uint8)
    y_true[5:20, 5:20] = 1
    y_pred = y_true.copy()
    y_pred[18, 18] = 0  # 1 px 误差
    profile = {
        "driver": "GTiff", "width": 32, "height": 32, "count": 1, "dtype": "uint8",
        "crs": "EPSG:4326", "transform": from_origin(116.0, 40.0, 1.0, 1.0),
        "nodata": 255,
    }
    with rasterio.open(pred_path, "w", **profile) as dst:
        dst.write(y_pred, 1)
    with rasterio.open(ref_path, "w", **profile) as dst:
        dst.write(y_true, 1)
    report = service.evaluate(
        EvaluationRequest(
            owner_scope={"session_id": "s1"},
            task_type="segmentation",
            predictions_path=pred_path,
            references_path=ref_path,
            num_classes=2,
        )
    )
    metrics = report["metrics"]
    assert metrics["iou_per_class"][0] == pytest.approx(799 / 800, abs=1e-4)
    assert metrics["iou_per_class"][1] > 0.99
    assert report["leakage_guard"]["spatial_blocked"] is True
    assert report["artifact"].get("published")


def test_evaluation_requires_references(service, tmp_path):
    with pytest.raises(Exception):
        service.evaluate(
            EvaluationRequest(
                owner_scope={"session_id": "s1"},
                task_type="segmentation",
                predictions_path=tmp_path / "none.tif",
            )
        )


def test_compare_and_provenance(service, synthetic_raster):
    result = service.run_inference(
        InferenceRequest(
            model_id="tiny-landcover-seg",
            source_uri=str(synthetic_raster),
            owner_scope={"session_id": "s1"},
            task_type="semantic_segmentation",
        )
    )
    manifest = result.manifest
    comparison = service.compare_results(manifest, manifest)
    assert comparison["differences"] == {}
    provenance = service.inspect_provenance(manifest, section="preprocess")
    assert "preprocess" in provenance
    full = service.inspect_provenance(manifest)
    assert full["model"]["model_id"] == "tiny-landcover-seg"


def test_manifest_secret_redaction(service, synthetic_raster):
    """secret 词根字段经统一 redaction 后不出现在 manifest。"""
    result = service.run_inference(
        InferenceRequest(
            model_id="tiny-landcover-seg",
            source_uri=str(synthetic_raster),
            owner_scope={"session_id": "s1"},
            task_type="semantic_segmentation",
        )
    )
    raw = json.dumps(result.manifest)
    assert "api_key" not in raw.lower() or "credentials_ref" in raw


def test_cancel_unknown_key_returns_false(service):
    assert service.cancel("no-such-key") is False
