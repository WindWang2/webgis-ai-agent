"""SEC-06 regression: GeoAI service-level source/artifact path gate.

Deep-review swarm 2026-09-19 (SEC-06): the LLM tools called
``ModelOpsService.compile_geo_prompt`` / ``run_inference_async`` without the
DATA_DIR source gate the HTTP route applies, so a model-controlled absolute
path could read arbitrary local rasters. The gate now lives in the service
(compile defaults to DATA_DIR + registry; inference defaults to DATA_DIR).
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.modelops.errors import ModelOpsError, PromptArtifactError
from app.services.modelops.config import ModelOpsSettings
from app.services.modelops.service import ModelOpsService


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    from app.core.config import settings

    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(settings, "DATA_DIR", str(root))
    return root


@pytest.fixture()
def service(tmp_path):
    return ModelOpsService(ModelOpsSettings(registry_dir=tmp_path / "modelops"))


def _raster(path) -> str:
    import rasterio
    from rasterio.transform import from_origin

    data = np.full((3, 64, 64), 0.5, dtype="float32")
    with rasterio.open(
        path, "w", driver="GTiff", width=64, height=64, count=3,
        dtype="float32", crs="EPSG:4326",
        transform=from_origin(116.0, 40.0, 1.0, 1.0),
    ) as dst:
        dst.write(data)
    return str(path)


def test_compile_rejects_source_outside_data_dir(service, data_dir, tmp_path):
    outside = _raster(tmp_path / "outside.tif")
    with pytest.raises(PromptArtifactError, match="data directory"):
        service.compile_geo_prompt({"points": [[1, 1]]}, outside)


def test_compile_accepts_source_inside_data_dir(service, data_dir):
    inside = _raster(data_dir / "inside.tif")
    compiled = service.compile_geo_prompt({"points": [[10, 10]]}, inside)
    assert compiled["artifact_id"]


def test_compile_rejects_reference_layer_outside_data_dir(service, data_dir, tmp_path):
    inside = _raster(data_dir / "inside.tif")
    outside = _raster(tmp_path / "ref.tif")
    with pytest.raises(PromptArtifactError, match="data directory"):
        service.compile_geo_prompt(
            {"reference_layer": {"uri": outside, "band": 1, "strategy": "nonzero"}},
            inside,
        )


async def test_run_inference_async_rejects_source_outside_data_dir(
    service, data_dir, tmp_path
):
    from app.lib.modelops.promptable import PromptSpec
    from app.services.modelops.engine import InferenceRequest

    outside = _raster(tmp_path / "outside.tif")
    with pytest.raises(ModelOpsError, match="data directory"):
        await service.run_inference_async(InferenceRequest(
            model_id="tiny-promptable-seg",
            source_uri=outside,
            owner_scope={"session_id": "sec06"},
            prompt=PromptSpec(points=((10.0, 10.0),)),
        ))


async def test_run_inference_async_accepts_source_inside_data_dir(service, data_dir):
    from app.lib.modelops.promptable import PromptSpec
    from app.services.modelops.engine import InferenceRequest

    inside = _raster(data_dir / "inside.tif")
    result = await service.run_inference_async(InferenceRequest(
        model_id="tiny-promptable-seg",
        source_uri=inside,
        owner_scope={"session_id": "sec06-ok"},
        prompt=PromptSpec(points=((10.0, 10.0),)),
    ))
    assert result.status == "completed"
