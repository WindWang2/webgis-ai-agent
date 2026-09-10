"""Slice B/D/E：promptable / detection / instance / embedding / temporal 路径。"""
from __future__ import annotations

import json

import numpy as np
import pytest

from app.lib.geo_raster.reader import RasterReader
from app.services.modelops.engine import InferenceRequest
from app.lib.modelops.errors import ModelOpsError
from app.lib.modelops.promptable import PromptSpec
from app.lib.modelops.temporal import TemporalStackSpec


def test_slice_b_promptable_point(service, synthetic_raster):
    prompt = PromptSpec(points=((70.0, 50.0),))
    result = service.run_inference(
        InferenceRequest(
            model_id="tiny-promptable-seg",
            source_uri=str(synthetic_raster),
            owner_scope={"session_id": "s1"},
            prompt=prompt,
        )
    )
    assert result.status == "completed"
    assert result.task_type == "promptable_segmentation"
    assert result.outputs["prompt_mask"].get("data_object_id")
    assert result.manifest["prompt"]["points"] == [[70.0, 50.0]]
    with RasterReader.open(result.outputs["prompt_mask"]["path"]) as reader:
        mask = reader.read_full(budget_ok=True)
    assert set(np.unique(mask)) <= {0, 1}


def test_slice_b_prompt_requires_prompt(service, synthetic_raster):
    with pytest.raises(ModelOpsError):
        service.run_inference(
            InferenceRequest(
                model_id="tiny-promptable-seg",
                source_uri=str(synthetic_raster),
                owner_scope={"session_id": "s1"},
            )
        )


def test_detection_global_coords_artifact(service, sar_raster):
    result = service.run_inference(
        InferenceRequest(
            model_id="tiny-sar-detector",
            source_uri=str(sar_raster),
            owner_scope={"session_id": "s1"},
            task_type="object_detection",
            score_threshold=0.7,
        )
    )
    assert result.task_type == "object_detection"
    detections = json.loads(
        (result.outputs["detections"]["path"]).read_text(encoding="utf-8")
        if hasattr(result.outputs["detections"]["path"], "read_text")
        else open(result.outputs["detections"]["path"], encoding="utf-8").read()
    )
    assert detections["type"] == "FeatureCollection"
    assert len(detections["features"]) >= 1
    feature = detections["features"][0]
    # 全局地理坐标（源 CRS EPSG:32650，transform 500000/4000000）。
    ring = feature["geometry"]["coordinates"][0]
    xs = [pt[0] for pt in ring]
    assert min(xs) >= 500000.0 - 1.0
    assert feature["properties"]["score"] >= 0.7


def test_embedding_collection(service, synthetic_raster):
    result = service.run_inference(
        InferenceRequest(
            model_id="tiny-chip-embedder",
            source_uri=str(synthetic_raster),
            owner_scope={"session_id": "s1"},
            task_type="embedding",
        )
    )
    payload = json.loads(
        open(result.outputs["embeddings"]["path"], encoding="utf-8").read()
    )
    assert len(payload) >= 1
    assert "core_window" in payload[0] and "vector" in payload[0]


def test_temporal_forecast(service, temporal_raster):
    spec = TemporalStackSpec(
        times=("2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"),
        max_length=8,
        missing_policy="flag",
    )
    result = service.run_inference(
        InferenceRequest(
            model_id="tiny-temporal-forecast",
            source_uri=str(temporal_raster),
            owner_scope={"session_id": "s1"},
            temporal=spec,
        )
    )
    assert result.task_type == "temporal_forecast"
    with RasterReader.open(result.outputs["temporal_forecast"]["path"]) as reader:
        meta = reader.metadata()
        arr = reader.read_full(budget_ok=True)
    assert meta.count == 2  # C=2 输出栈
    # 线性趋势外推：预测 ≥ 最后观测（0.7/0.35 附近）。
    assert float(arr[0].mean()) > 0.6
    assert float(arr[1].mean()) > 0.3


def test_temporal_length_overflow_rejected(service, temporal_raster):
    with pytest.raises(Exception):
        spec = TemporalStackSpec(
            times=tuple(f"2026-0{m}-01" for m in range(1, 10)),
            max_length=8,
            missing_policy="error",
        )
        service.run_inference(
            InferenceRequest(
                model_id="tiny-temporal-forecast",
                source_uri=str(temporal_raster),
                owner_scope={"session_id": "s1"},
                temporal=spec,
            )
        )
