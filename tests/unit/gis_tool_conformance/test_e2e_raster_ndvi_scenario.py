"""G03 WP-D.13 离线 E2E 场景 B —— 上传栅格 → NDVI 分析 → 分级/图层证据。

经**真实 ToolDispatchService.dispatch** 走完整调度链（同场景 A 的沙箱纪律：
MemorySessionStore 注入 + 全消费面替换 + USE_REDIS 关闭，零网络零 Redis）。

离线事实（工具勘察结论，如实入测）：

- ``compute_ndvi``（bbox+日期，Sentinel-2/STAC）与 ``fetch_dem`` 声明
  ``network=True``，离线不可跑——不入链。
- ``analyze_vegetation_index``（上传 TIFF 的 Celery 异步入口）经
  ``submit_durable_job`` 写 DB + 入队 broker，离线单测不可确定性执行——
  不入链（诚实边界，见 app/tools/nature_resources.py:79）。
- 离线可跑的 NDVI 类工具（本场景采用）：
  * ``raster_calculator``（app/tools/advanced_spatial.py:2760，
    ``network=False, deterministic=True``）——对本地 TIFF 做像元级
    ``(B-A)/(B+A)`` 即 NDVI 公式，产出真实 GeoTIFF 工件；
  * ``compute_spectral_index``（app/tools/remote_sensing.py:230，
    ``network=False, deterministic=True``）——类型化光谱指数（经
    ParameterContract ``spectral_index_analysis`` 真实契约校验）；
  * ``raster_reclassify``——NDVI 结果分级（植被覆盖类别面）。

栅格 fixture：rasterio+numpy 生成 8×8 像素 red/nir 双 TIFF（固定种子，
反射率量级），写入 tmp_path 下的 ``data/`` 目录并 chdir——
``validate_data_path`` 的 ``./data`` 基准目录随之解析到沙箱（路径闸真实生效，
拒绝 data_dir 之外的逃逸路径也有覆盖）。
"""
from __future__ import annotations

import importlib
import uuid
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.services.session_data import MemorySessionStore
from app.services.tool_dispatch_service import ToolDispatchService
from app.tools.advanced_spatial import register_advanced_spatial_tools
from app.tools.catalog_discovery_tools import register_catalog_discovery_tools
from app.tools.registry import ToolRegistry
from app.tools.remote_sensing import register_rs_tools

_VERDICTS = {"pass", "warning", "fail", "not_evaluated"}
_FACES = {
    "registration", "search", "planner", "pi_native",
    "proxy", "dispatch", "result", "science",
}

_SESSION_DATA_CONSUMERS = (
    "app.services.session_data",
    "app.tools.registry",
    "app.services.tool_dispatch_service",
    "app.services.mapspec.lifecycle_engine",
    "app.services.mapspec.store",
    "app.services.mapspec_store",
    "app.services.mapspec.mutation_handlers",
    "app.services.mapspec.mutation_primitives",
    "app.services.artifact_registry",
    "app.services.render_work_supply",
    "app.services.session_plan",
    "app.lib.gis.analysis_reuse",
    "app.services.cartography_runtime",
)


@pytest.fixture(scope="module")
def registry() -> ToolRegistry:
    r = ToolRegistry()
    register_advanced_spatial_tools(r)
    register_rs_tools(r)
    register_catalog_discovery_tools(r)
    return r


@pytest.fixture
def sandbox(registry, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.USE_REDIS", False)
    store = MemorySessionStore()
    for module_name in _SESSION_DATA_CONSUMERS:
        module = importlib.import_module(module_name)
        if hasattr(module, "session_data_manager"):
            monkeypatch.setattr(module, "session_data_manager", store)
    import app.agent_pi_bridge as bridge

    monkeypatch.setattr(bridge, "try_get_tool_registry", lambda: registry)
    service = ToolDispatchService(registry=registry, session_data=store)
    return SimpleNamespace(store=store, service=service, registry=registry)


@pytest.fixture
def rasters(tmp_path, monkeypatch):
    """模拟上传：8×8 red/nir 反射率 TIFF 写入沙箱 data_dir（固定种子确定性）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.chdir(tmp_path)  # validate_data_path("./data") → tmp_path/data

    rng = np.random.default_rng(20261021)
    red = (0.05 + 0.20 * rng.random((8, 8))).astype("float32")
    nir = (0.25 + 0.50 * rng.random((8, 8))).astype("float32")
    transform = from_origin(104.05, 30.70, 0.0001, 0.0001)

    def _write(name: str, arr: np.ndarray) -> str:
        path = data_dir / name
        with rasterio.open(
            path, "w", driver="GTiff", height=8, width=8, count=1,
            dtype="float32", crs="EPSG:4326", transform=transform,
        ) as dst:
            dst.write(arr, 1)
        return name  # data_dir 相对名（validate_data_path 的契约形态）

    return SimpleNamespace(
        red_name=_write("red.tif", red), nir_name=_write("nir.tif", nir),
        red=red, nir=nir, data_dir=data_dir)


def _tc(name: str, args: dict) -> dict:
    return {
        "id": "call_" + uuid.uuid4().hex[:8],
        "function": {"name": name, "arguments": args},
    }


def _expected_ndvi(rasters) -> np.ndarray:
    return (rasters.nir - rasters.red) / (rasters.nir + rasters.red)


# ── 1. 上传栅格 → 像元级 NDVI 工件 ───────────────────────────────────


async def test_raster_ndvi_artifact_produced_via_dispatch(sandbox, rasters):
    """raster_calculator 以 (B-A)/(B+A) 真算 NDVI：dispatch ok + 真实
    GeoTIFF 工件落地 + 数值与闭式公式逐像素一致。"""
    result = await sandbox.service.dispatch(
        _tc("raster_calculator", {
            "raster_a": rasters.red_name, "raster_b": rasters.nir_name,
            "expression": "(B - A) / (B + A)",
        }), "sess-g03-ndvi-prod", set())

    assert result.status == "ok", result.error_msg
    raw = result.raw_result
    assert raw.get("success") is True
    data = raw.get("data") or {}
    out_path = data.get("output_path")
    assert out_path, "NDVI 工件必须返回 output_path（产物 ref 语义）"
    produced_path = rasters.data_dir / "red_calc.tif"
    assert produced_path.exists(), f"NDVI 工件必须真实落地: {out_path}"

    with rasterio.open(produced_path) as ds:
        produced = ds.read(1)
        assert ds.crs.to_string() == "EPSG:4326"
        assert (ds.height, ds.width) == (8, 8)
    expected = _expected_ndvi(rasters)
    assert np.allclose(produced, expected, atol=1e-5), "NDVI 像元值必须与公式一致"
    assert float(produced.min()) >= -1.0 and float(produced.max()) <= 1.0


# ── 2. 类型化 NDVI 统计（真实 ParameterContract） ────────────────────


async def test_typed_spectral_index_ndvi_stats(sandbox, rasters):
    """compute_spectral_index：契约校验 + 公式披露 + 统计与 numpy 基准一致。"""
    result = await sandbox.service.dispatch(
        _tc("compute_spectral_index", {
            "index_id": "ndvi",
            "bands": {
                "red": rasters.red.tolist(),
                "nir": rasters.nir.tolist(),
            },
        }), "sess-g03-ndvi-spectral", set())

    assert result.status == "ok", result.error_msg
    raw = result.raw_result
    assert raw.get("success") is True
    assert raw.get("index_id") == "ndvi"
    assert "NIR" in (raw.get("formula") or "") and "Red" in (raw.get("formula") or "")
    assert raw.get("reference"), "科学语义面必须披露公式出处"
    stats = raw.get("stats") or {}
    expected = _expected_ndvi(rasters)
    assert stats.get("valid_pixels") == 64 and stats.get("total_pixels") == 64
    assert abs(stats["mean"] - float(np.mean(expected))) < 1e-4
    assert abs(stats["max"] - float(np.max(expected))) < 1e-4


async def test_typed_spectral_index_unknown_index_rejected(sandbox, rasters):
    """非法 index_id 被 ParameterContract 类型化拒绝（不得静默降级 ndvi）。"""
    result = await sandbox.service.dispatch(
        _tc("compute_spectral_index", {
            "index_id": "definitely_not_an_index",
            "bands": {"red": rasters.red.tolist(), "nir": rasters.nir.tolist()},
        }), "sess-g03-ndvi-badidx", set())
    assert result.status == "error"
    assert result.raw_result.get("code") in {"VALIDATION_ERROR", "TOOL_ERROR"}
    assert result.raw_result.get("success") is False


# ── 3. NDVI 分级（图层语义的类别面） ─────────────────────────────────


async def test_ndvi_reclassify_vegetation_classes(sandbox, rasters):
    """NDVI 工件 → 三级植被覆盖类别：标签回带 + 类别值封闭。"""
    ndvi = await sandbox.service.dispatch(
        _tc("raster_calculator", {
            "raster_a": rasters.red_name, "raster_b": rasters.nir_name,
            "expression": "(B - A) / (B + A)",
        }), "sess-g03-ndvi-reclass", set())
    assert ndvi.status == "ok"
    ndvi_out = (ndvi.raw_result.get("data") or {}).get("output_path")

    result = await sandbox.service.dispatch(
        _tc("raster_reclassify", {
            "raster_path": ndvi_out,
            "scheme": [
                {"min": -1.0, "max": 0.2, "value": 1, "label": "bare"},
                {"min": 0.2, "max": 0.4, "value": 2, "label": "sparse"},
                {"min": 0.4, "max": 1.0, "value": 3, "label": "dense"},
            ],
        }), "sess-g03-ndvi-reclass", set())

    assert result.status == "ok", result.error_msg
    data = result.raw_result.get("data") or {}
    labels = data.get("labels") or {}
    assert set(labels.values()) <= {"bare", "sparse", "dense"}
    assert set(data.get("unique_values") or []) <= {1, 2, 3}
    # 分级阈值与 NDVI 分布一致：随机反射率下三级都应出现（固定种子确定性）
    assert len(labels) == 3


# ── 4. 图层证据（诚实断言） ──────────────────────────────────────────


async def test_raster_result_not_acknowledged_as_vector_layer(sandbox, rasters):
    """栅格产物不走矢量挂载：不得铸造 geojson_ref 空转 MapSpec 矢量层
    （空 FeatureCollection ACK 栅格 = 谎报已显示，dispatch 契约明令禁止）。
    图层证据若存在（map_actions/mapspec 指纹）则必须结构完整。"""
    result = await sandbox.service.dispatch(
        _tc("raster_calculator", {
            "raster_a": rasters.red_name, "raster_b": rasters.nir_name,
            "expression": "(B - A) / (B + A)",
        }), "sess-g03-ndvi-layer", set())

    assert result.status == "ok"
    assert result.geojson_ref is None, "栅格结果不得被 ACK 为矢量图层 ref"
    for action in result.map_actions:
        assert action.get("action_id") and action.get("command")
    raw = result.raw_result
    if raw.get("mapspec_fingerprint"):
        assert raw["mapspec_fingerprint"].startswith("carto-sha256:")


# ── 5. 错误吞噬检查：结构化失败绝不静默成功 ──────────────────────────


async def test_path_escape_rejected_structured(sandbox, rasters):
    """data_dir 之外的路径 → 路径闸结构化拒绝（机器码 + 失败信息）。"""
    result = await sandbox.service.dispatch(
        _tc("raster_calculator", {
            "raster_a": "/etc/passwd.tif", "expression": "A",
        }), "sess-g03-ndvi-escape", set())
    assert result.status == "error"
    raw = result.raw_result
    assert raw.get("success") is False
    assert raw.get("code"), "路径闸拒绝必须携带机器码"
    assert "data_dir" in (raw.get("message") or "")


async def test_missing_raster_structured_failure(sandbox, rasters):
    """data_dir 内不存在的文件 → 结构化失败；同参重试槽位已释放。"""
    executed: set = set()
    result = await sandbox.service.dispatch(
        _tc("raster_calculator", {
            "raster_a": "ghost_upload.tif", "expression": "A",
        }), "sess-g03-ndvi-missing", executed)
    assert result.status == "error"
    assert result.raw_result.get("success") is False
    assert result.raw_result.get("code")
    assert not executed, "失败调用必须释放 dedup 占位"


# ── 6. 链路中经 dispatch 调 catalog_conformance 解释工具选择 ─────────


async def test_catalog_conformance_in_chain_explains_tool_choice(sandbox, rasters):
    result = await sandbox.service.dispatch(
        _tc("catalog_conformance", {"tool": "raster_calculator"}),
        "sess-g03-ndvi-catalog", set())
    assert result.status == "ok"
    raw = result.raw_result
    assert raw.get("tool") == "raster_calculator"
    assert raw.get("overall") in _VERDICTS
    faces = {f["face"]: f for f in raw.get("faces", [])}
    assert set(faces) == _FACES
    # offline 声明与离线现实一致：network=False 的确定性计算工具不应有 fail 面
    assert raw["overall"] != "fail"
    for verdict in faces.values():
        assert verdict["status"] in _VERDICTS
    # 网络依赖工具的对账披露：compute_ndvi 声明 network=True 属实（proxy 面
    # 仍可达——网络可用性是运行时资格，不是静态契约破损）
    online = await sandbox.service.dispatch(
        _tc("catalog_conformance", {"tool": "compute_ndvi"}),
        "sess-g03-ndvi-catalog", set())
    assert online.status == "ok"
    online_faces = {f["face"]: f for f in online.raw_result.get("faces", [])}
    assert online_faces["proxy"]["status"] in {"pass", "warning"}
