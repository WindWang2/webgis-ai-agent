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
        "tiny-landcover-seg", str(synthetic_raster), session_id="s1", allowed_roots=[synthetic_raster.parent]
    )
    assert estimate["device"] == "cpu"
    assert estimate["tiles"] > 0
    compat = service.check_compatibility(
        "tiny-landcover-seg", str(synthetic_raster), session_id="s1", allowed_roots=[synthetic_raster.parent]
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
        ), allowed_roots=[tmp_path]
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


def test_evaluate_async_gates_paths_outside_data_dir(service, tmp_path, monkeypatch):
    """#1708（SEC-06）：评估族输入栅格与推理族同一道数据目录门。

    默认门钉在 ``settings.DATA_DIR``（工具面/HTTP 面口径）；受信进程内
    调用者经 ``allowed_roots`` 显式豁免（``[]`` = 关闭门，历史语义）。
    """
    import asyncio

    import rasterio
    from rasterio.transform import from_origin

    from app.core.config import settings
    from app.lib.modelops.errors import ModelOpsError

    def _write(path, value):
        profile = {
            "driver": "GTiff", "width": 16, "height": 16, "count": 1,
            "dtype": "uint8", "crs": "EPSG:4326",
            "transform": from_origin(116.0, 40.0, 1.0, 1.0), "nodata": 255,
        }
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(value, 1)
        return path

    y = np.zeros((16, 16), dtype=np.uint8)
    y[4:12, 4:12] = 1
    pred_path = _write(tmp_path / "pred.tif", y.copy())
    ref_path = _write(tmp_path / "ref.tif", y.copy())

    # DATA_DIR 指向另一目录 → 两个栅格都在门外（模型控制的路径）。
    data_root = tmp_path / "elsewhere"
    data_root.mkdir()
    monkeypatch.setattr(settings, "DATA_DIR", str(data_root), raising=False)

    request = EvaluationRequest(
        owner_scope={"session_id": "s1"},
        task_type="segmentation",
        predictions_path=pred_path,
        references_path=ref_path,
        num_classes=2,
    )
    with pytest.raises(ModelOpsError, match="data directory"):
        asyncio.run(service.evaluate_async(request))

    # 受信调用者显式豁免：同一请求改走显式 roots 即可真实评估。
    report = asyncio.run(service.evaluate_async(request, allowed_roots=[tmp_path]))
    assert report["metrics"]["iou_per_class"][1] > 0.99


def test_evaluate_async_gates_confidence_and_regions_paths(service, tmp_path, monkeypatch):
    """#1708（SEC-06）：可选 confidence/regions 栅格同门（同一读取链）。"""
    import asyncio

    import rasterio
    from rasterio.transform import from_origin

    from app.core.config import settings
    from app.lib.modelops.errors import ModelOpsError

    def _write(path, value):
        profile = {
            "driver": "GTiff", "width": 16, "height": 16, "count": 1,
            "dtype": "uint8", "crs": "EPSG:4326",
            "transform": from_origin(116.0, 40.0, 1.0, 1.0), "nodata": 255,
        }
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(value, 1)
        return path

    y = np.zeros((16, 16), dtype=np.uint8)
    y[4:12, 4:12] = 1
    pred_path = _write(tmp_path / "pred.tif", y.copy())
    ref_path = _write(tmp_path / "ref.tif", y.copy())
    conf_path = _write(tmp_path / "conf.tif", np.full((16, 16), 200, dtype=np.uint8))
    regions_path = _write(tmp_path / "regions.tif", np.ones((16, 16), dtype=np.uint8))

    request = EvaluationRequest(
        owner_scope={"session_id": "s1"},
        task_type="segmentation",
        predictions_path=pred_path,
        references_path=ref_path,
        confidence_path=conf_path,
        regions_path=regions_path,
        num_classes=2,
    )
    # conftest 的 DATA_DIR 夹具已把数据根钉到 tmp_path → 门放行。
    report = asyncio.run(service.evaluate_async(request, allowed_roots=[tmp_path]))
    assert report["metrics"]["iou_per_class"][1] > 0.99
    assert report.get("ece") is not None

    # 把数据根挪走 → confidence/regions 也在门外，同样 typed 拒绝。
    data_root = tmp_path / "elsewhere"
    data_root.mkdir()
    monkeypatch.setattr(settings, "DATA_DIR", str(data_root), raising=False)
    with pytest.raises(ModelOpsError, match="data directory"):
        asyncio.run(service.evaluate_async(request))


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


def test_compare_results_tolerates_null_checksum(service):
    """外部/旧 manifest 的 checksum=null：不得 None[:12] 裸 TypeError。"""
    manifest = {
        "model": {"model_id": "m", "model_version": "1", "checksum": None},
        "provider": {},
        "device_plan": {},
        "performance": {},
    }
    comparison = service.compare_results(manifest, manifest)
    assert comparison["a"]["checksum"] == ""
    assert comparison["differences"] == {}


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


def test_run_inference_rejects_invalid_postgis_table_at_tool_boundary(monkeypatch):
    """P2 security：LLM 提供的 postgis_table 必须在工具面白名单校验。

    未修复时非法表名直传引擎；修复后 typed ModelOpsError，引擎不被触达。
    """
    import asyncio

    from app.lib.modelops.errors import ModelOpsError
    from app.services.modelops import service as service_mod
    from app.tools.modelops_tools import register_modelops_tools
    from app.tools.registry import ToolRegistry

    calls = []

    class _ExplodingService:
        async def run_inference_async(self, request):
            calls.append(request)
            raise AssertionError("engine reached with invalid postgis_table")

    monkeypatch.setattr(
        service_mod, "get_modelops_service", lambda: _ExplodingService()
    )
    registry = ToolRegistry()
    register_modelops_tools(registry)
    run_inference = registry.get("modelops_run_inference")
    with pytest.raises(ModelOpsError, match="table name"):
        asyncio.run(run_inference(
            model_id="m", source_uri="/nonexistent.tif", session_id="s1",
            postgis_table="class_polygons; DROP TABLE layers",
        ))
    assert calls == []

    # 合法表名照常放行到引擎（校验不是一刀切拒绝）。
    class _Sentinel(Exception):
        pass

    captured = []

    class _CaptureService:
        async def run_inference_async(self, request):
            captured.append(request)
            raise _Sentinel()

    monkeypatch.setattr(
        service_mod, "get_modelops_service", lambda: _CaptureService()
    )
    with pytest.raises(_Sentinel):
        asyncio.run(run_inference(
            model_id="m", source_uri="/nonexistent.tif", session_id="s1",
            postgis_table="class_polygons",
        ))
    assert captured[0].postgis_table == "class_polygons"


def test_run_inference_rejects_roi_truncated_to_zero_width(monkeypatch):
    """P3 correctness：roi_bbox 先按 float 校验再 int() 截断，10.0/10.9 会被
    截成 x0==x1 的零宽窗口直送引擎；修复后按截断值校验，引擎不被触达。"""
    import asyncio

    from app.lib.modelops.errors import ModelOpsError
    from app.services.modelops import service as service_mod
    from app.tools.modelops_tools import register_modelops_tools
    from app.tools.registry import ToolRegistry

    captured = []

    class _CaptureService:
        async def run_inference_async(self, request):
            captured.append(request)
            raise AssertionError("engine reached with degenerate roi window")

    monkeypatch.setattr(
        service_mod, "get_modelops_service", lambda: _CaptureService()
    )
    registry = ToolRegistry()
    register_modelops_tools(registry)
    run_inference = registry.get("modelops_run_inference")
    with pytest.raises(ModelOpsError, match="invalid roi_bbox"):
        asyncio.run(run_inference(
            model_id="m", source_uri="/nonexistent.tif", session_id="s1",
            roi_bbox=[10.0, 0.0, 10.9, 50.0],
        ))
    assert captured == []

    # 整数 ROI 照常放行（校验口径与截断值一致，不是一刀切拒绝）。
    with pytest.raises(AssertionError, match="degenerate roi window"):
        asyncio.run(run_inference(
            model_id="m", source_uri="/nonexistent.tif", session_id="s1",
            roi_bbox=[10, 0, 11, 50],
        ))
    assert captured[0].roi_bbox == (10, 0, 11, 50)
