"""Lakehouse V8 — cube v3（model × scenario 维度 + 逐变量 nodata）。

覆盖面（ADR-0130 §3，验收：cube 可表达 SAR polarization + optical band
+ time，且扩展 model/scenario）：
- 契约版本按需升级：六维投影恒 v2（既有发布路径字节级不变）；
  出现 model/scenario → v3；
- 逐变量 nodata：校验（未知变量拒绝）、投影确定性、store attrs 往返；
- v3 cube 写读往返（(time, band, model, scenario, y, x) 布局）；
- model/scenario 标签选择（plan_selection + read_labeled_window 一致）；
- 未知标签 typed 拒绝（绝不静默钳制）。
"""
from __future__ import annotations

import numpy as np
import pytest

zarr_mod = pytest.importorskip("zarr")

from app.services.lakehouse.cube_schema import (
    CUBE_SCHEMA_VERSION_V2,
    CUBE_SCHEMA_VERSION_V3,
    CubeSchemaError,
    resolve_cube_schema_version,
    validate_labeled_schema,
)
from app.services.lakehouse.cube_store import (
    read_labeled_window,
    write_labeled_cube,
)
from app.services.lakehouse.labeled_selection import plan_selection

YS = [3.5, 2.5, 1.5, 0.5]
XS = [0.5, 1.5, 2.5, 3.5]
TIMES = ["2025-01-01T00:00:00Z", "2025-02-01T00:00:00Z"]
BANDS = ["B02", "B03"]
MODELS = ["unet-v3", "swin-v1"]
SCENARIOS = ["baseline", "rcp45"]


def _coords(**extra):
    coords = {
        "time": TIMES,
        "band": BANDS,
        "y": YS,
        "x": XS,
    }
    coords.update(extra)
    return coords


# ── 纯 schema（无 IO）────────────────────────────────────────────────────


def test_schema_version_resolution():
    six = ["time", "band", "polarization", "vertical", "y", "x"]
    assert resolve_cube_schema_version(six) == CUBE_SCHEMA_VERSION_V2
    assert resolve_cube_schema_version(["time", "y", "x"]) == (
        CUBE_SCHEMA_VERSION_V2
    )
    assert resolve_cube_schema_version(["time", "model", "y", "x"]) == (
        CUBE_SCHEMA_VERSION_V3
    )
    assert resolve_cube_schema_version(["scenario", "y", "x"]) == (
        CUBE_SCHEMA_VERSION_V3
    )


def test_v2_projection_unchanged_by_v8():
    """六维投影的 cube_schema_version 恒 2 —— 既有 manifest 身份零漂移。"""
    proj = validate_labeled_schema(
        dims=["time", "band", "y", "x"],
        shape=[2, 2, 4, 4],
        coordinates=_coords(),
        crs="EPSG:4326",
        dtype="float32",
    )
    assert proj["cube_schema_version"] == CUBE_SCHEMA_VERSION_V2
    assert "nodata_per_variable" not in proj


def test_v3_projection_and_nodata_per_variable():
    proj = validate_labeled_schema(
        dims=["time", "model", "scenario", "y", "x"],
        shape=[2, 2, 2, 4, 4],
        coordinates=_coords(model=MODELS, scenario=SCENARIOS),
        crs="EPSG:4326",
        dtype="float32",
        variables={
            "probability": {"dims": ["time", "model", "scenario", "y", "x"],
                            "dtype": "float32"},
            "valid_mask": {"dims": ["time", "y", "x"], "dtype": "uint8"},
        },
        nodata_per_variable={"probability": -9999.0, "valid_mask": 255},
    )
    assert proj["cube_schema_version"] == CUBE_SCHEMA_VERSION_V3
    # 键序规范化（sorted）—— 投影确定性。
    assert proj["nodata_per_variable"] == {
        "probability": -9999.0, "valid_mask": 255.0,
    }
    again = validate_labeled_schema(
        dims=["time", "model", "scenario", "y", "x"],
        shape=[2, 2, 2, 4, 4],
        coordinates=_coords(model=MODELS, scenario=SCENARIOS),
        crs="EPSG:4326",
        dtype="float32",
        variables={
            "probability": {"dims": ["time", "model", "scenario", "y", "x"],
                            "dtype": "float32"},
            "valid_mask": {"dims": ["time", "y", "x"], "dtype": "uint8"},
        },
        nodata_per_variable={"valid_mask": 255, "probability": -9999.0},
    )
    assert again == proj


def test_nodata_per_variable_bumps_schema_version():
    """v2 维度 + 逐变量 nodata → 投影升 v3（版本字段判别投影形状）。"""
    proj = validate_labeled_schema(
        dims=["time", "band", "y", "x"],
        shape=[2, 2, 4, 4],
        coordinates=_coords(),
        crs="EPSG:4326",
        dtype="float32",
        nodata_per_variable={"data": -1.0},
    )
    assert proj["cube_schema_version"] == CUBE_SCHEMA_VERSION_V3
    assert proj["nodata_per_variable"] == {"data": -1.0}


def test_nodata_per_variable_non_numeric_rejected():
    with pytest.raises(CubeSchemaError):
        validate_labeled_schema(
            dims=["time", "band", "y", "x"],
            shape=[2, 2, 4, 4],
            coordinates=_coords(),
            crs="EPSG:4326",
            dtype="float32",
            nodata_per_variable={"data": "not-a-number"},
        )


def test_nodata_per_variable_unknown_variable_rejected():
    with pytest.raises(CubeSchemaError):
        validate_labeled_schema(
            dims=["time", "model", "y", "x"],
            shape=[2, 2, 4, 4],
            coordinates=_coords(model=MODELS),
            crs="EPSG:4326",
            dtype="float32",
            nodata_per_variable={"nope": -1.0},
        )


# ── v3 store 写读往返 ─────────────────────────────────────────────────────


def _write_v3_cube(tmp_path, **kw):
    rng = np.random.default_rng(7)
    prob = rng.random((2, 2, 2, 4, 4)).astype("float32")
    mask = (rng.random((2, 4, 4)) > 0.2).astype("uint8")
    return write_labeled_cube(
        {
            "probability": (["time", "model", "scenario", "y", "x"], prob),
            "valid_mask": (["time", "y", "x"], mask),
        },
        _coords(model=MODELS, scenario=SCENARIOS),
        tmp_path / "cube_v3",
        crs="EPSG:4326",
        nodata_per_variable={"probability": -9999.0, "valid_mask": 255},
        chunks={"time": 1, "model": 1, "scenario": 1, "y": 2, "x": 2},
        **kw,
    )


def test_v3_cube_write_read_roundtrip(tmp_path):
    proj = _write_v3_cube(tmp_path)
    assert proj["cube_schema_version"] == CUBE_SCHEMA_VERSION_V3
    result = read_labeled_window(
        tmp_path / "cube_v3",
        index_slices={"time": slice(0, 1), "model": slice(0, 1),
                      "scenario": slice(0, 1), "y": slice(0, 2),
                      "x": slice(0, 2)},
    )
    assert result["variables"]["probability"].shape == (1, 1, 1, 2, 2)
    assert result["variables"]["valid_mask"].shape == (1, 2, 2)
    assert result["attrs"]["cube_schema_version"] == (
        CUBE_SCHEMA_VERSION_V3
    )
    assert result["attrs"]["nodata_per_variable"] == {
        "probability": -9999.0, "valid_mask": 255.0,
    }
    assert result["coords"]["model"].tolist() == MODELS
    assert result["coords"]["scenario"].tolist() == SCENARIOS


def test_v3_label_selection_matches_window(tmp_path):
    _write_v3_cube(tmp_path)
    coordinates = {
        "time": TIMES, "model": MODELS, "scenario": SCENARIOS,
        "y": YS, "x": XS,
    }
    selection = {"model": "swin-v1", "scenario": ["baseline"],
                 "time": TIMES[1], "bbox": [1.0, 1.0, 3.0, 3.0]}
    plan = plan_selection(
        projection={
            "dims": ["time", "model", "scenario", "y", "x"],
            "shape": [2, 2, 2, 4, 4],
            "variables": {
                "probability": ["time", "model", "scenario", "y", "x"],
            },
            "chunks": [1, 1, 1, 2, 2],
            "dtype": "float32",
        },
        coordinates=coordinates,
        selection=selection,
    )
    slices = {d: slice(a, b) for d, (a, b) in plan["slices"].items()}
    result = read_labeled_window(tmp_path / "cube_v3", index_slices=slices)
    # 切片作用于变量（coords 恒回全轴 —— 既有读取契约）；
    # 标签解析正确性从 plan["slices"] 断言。
    assert plan["slices"]["model"] == [1, 2]
    assert plan["slices"]["scenario"] == [0, 1]
    assert plan["slices"]["time"] == [1, 2]
    assert result["variables"]["probability"].shape == (1, 1, 1, 2, 2)
    # bbox [1,1,3,3] 与中心坐标的相交像元 = y∈{1.5,2.5}, x∈{1.5,2.5}。
    assert plan["slices"]["y"] == [1, 3]
    assert plan["slices"]["x"] == [1, 3]


def test_v3_unknown_model_label_rejected(tmp_path):
    _write_v3_cube(tmp_path)
    coordinates = {
        "time": TIMES, "model": MODELS, "scenario": SCENARIOS,
        "y": YS, "x": XS,
    }
    from app.services.lakehouse.labeled_selection import resolve_selection

    with pytest.raises(CubeSchemaError):
        resolve_selection(
            coordinates=coordinates,
            selection={"model": "no-such-model"},
        )
    # v2 cube 上选择 model 轴 = typed 拒绝（无此维度）。
    with pytest.raises(CubeSchemaError):
        resolve_selection(
            coordinates={"time": TIMES, "band": BANDS, "y": YS, "x": XS},
            selection={"model": "unet-v3"},
        )
