"""G39 服务旅程 — 计划/预览/执行/成果/幂等/恢复（S01-S03, S19-S34 核心）。"""
from __future__ import annotations

import asyncio
import os

import numpy as np
import pytest

from app.services.raster_sampling import service as rs_service
from app.services.raster_sampling import store as rs_store
from app.services.raster_sampling.contracts import RasterSamplingError

SESS = "sess-g39-a"
SESS_OTHER = "sess-g39-other"


def _run(coro):
    return asyncio.run(coro)


async def _seed_constant_raster(raster_factory, session_id, name="const", value=42.0,
                                bands=1, band_tags=None, width=8, height=8,
                                transform=None):
    values = np.full((bands, height, width), value, dtype="float32")
    return await raster_factory(session_id, name=name, bands=bands, width=width,
                         height=height, values=values, band_tags=band_tags,
                         transform=transform)


async def _make_point_plan(rs_env, raster_factory, seed_points_ref, *, session_id=SESS,
                           method="source_pixel", sources=None,
                           band_semantics=None, points=None):
    if points is None:
        points = [
            {"id": "st-alpha", "x": 10.02, "y": 19.97,
             "properties": {"name": "监测站A"}},
            {"id": "st-beta", "x": 10.04, "y": 19.95},
            {"id": "st-gamma", "x": 10.06, "y": 19.93},
        ]
    points_ref = await seed_points_ref(session_id, points)
    if sources is None:
        ref, _path = await _seed_constant_raster(raster_factory, session_id)
        sources = [{"ref_id": ref, "bands": [1],
                    "band_semantics": band_semantics or {}}]
    out = await rs_service.create_plan(
        session_id=session_id, owner_token=None, name="旅程",
        mode="source_points", points_ref=points_ref,
        station_crs="EPSG:4326", method=method, sources=sources,
        trusted_dispatch=True)
    return out


# ── S01：常量栅格 × 真实监测点 ref ──────────────────────────────────

async def test_s01_point_journey_full_products(rs_env, raster_factory, seed_points_ref):
    out = await _make_point_plan(rs_env, raster_factory, seed_points_ref)
    plan = out["plan"]
    assert out["stations"]["count"] == 3
    assert out["stations"]["first_sample_ids"][0].startswith("sp_")

    preview = await rs_service.preview(
        session_id=SESS, plan_id=plan["plan_id"], owner_token=None,
        expected_revision=plan["revision"], limit=8, trusted_dispatch=True)
    assert preview["preview"] is True
    assert preview["rows"][0]["raw_value"] == 42.0
    assert preview["rows"][0]["parent_locator"] == "st-alpha"

    exec_out = await rs_service.execute(
        session_id=SESS, plan_id=plan["plan_id"], owner_token=None,
        expected_revision=plan["revision"], background=False,
        trusted_dispatch=True)
    receipt = exec_out["receipt"]
    assert receipt.coverage.valid_cells == 3
    assert receipt.coverage.requested_cells == 3
    assert receipt.station_count == 3
    assert receipt.outputs["sample_table_ref"].startswith("ref:fabric-parquet/")
    assert receipt.outputs["station_layer_ref"].startswith("ref:raster-sampling-")

    results = await rs_service.results(
        session_id=SESS, plan_id=plan["plan_id"], owner_token=None,
        page=1, per_page=50)
    assert len(results["rows"]) == 3
    by_parent = {r["parent_locator"]: r for r in results["rows"]}
    assert set(by_parent) == {"st-alpha", "st-beta", "st-gamma"}
    assert all(r["raw_value"] == 42.0 for r in results["rows"])
    # 站位层：一站一要素（父 ID 可追溯）
    from app.services.session_data import session_data_manager
    layer = await session_data_manager.get(
        SESS, receipt.outputs["station_layer_ref"])
    assert layer["type"] == "FeatureCollection"
    assert [f["id"] for f in layer["features"]] == sorted(
        f["id"] for f in layer["features"])
    assert len(layer["features"]) == 3
    assert layer["features"][0]["properties"]["parent_locator"]


# ── S02：单源多波段（值/单位不同） ──────────────────────────────────

async def test_s02_multiband_values_and_units(rs_env, raster_factory, seed_points_ref):
    import rasterio.transform as rt
    rows, cols = np.mgrid[0:8, 0:8]
    # band1 = NDVI 风格小数值；band2 = 温度（scale/offset）；band3 = 类别
    values = np.stack([
        0.3 + 0.001 * cols,
        100.0 + rows,            # raw，声明 scale=0.01/offset=-273.15 → K
        np.where((rows + cols) % 2 == 0, 1.0, 2.0),
    ]).astype("float32")
    ref, _ = await raster_factory(
        SESS, name="multi3", bands=3, width=8, height=8, values=values,
        band_tags={2: {"units": "degC", "scale": "0.01", "offset": "-273.15"},
                   1: {"units": ""}},
        transform=rt.from_origin(10.0, 20.0, 0.01, 0.01))
    out = await _make_point_plan(
        rs_env, raster_factory, seed_points_ref,
        sources=[{"ref_id": ref, "bands": [1, 2, 3]}],
        method="source_pixel")
    plan = out["plan"]
    await rs_service.execute(
        session_id=SESS, plan_id=plan["plan_id"], owner_token=None,
        background=False, trusted_dispatch=True)
    results = await rs_service.results(
        session_id=SESS, plan_id=plan["plan_id"], owner_token=None, page=1,
        per_page=50)
    rows_out = {(r["parent_locator"], r["band"]): r for r in results["rows"]}
    # 波段 2：physical = raw*0.01 + (-273.15)（恰一次）；
    # raw = 100 + row(st-alpha) —— row 由站点坐标独立推导
    r2 = rows_out[("st-alpha", 2)]
    st_alpha_row = int((20.0 - 19.97) / 0.01)
    assert r2["raw_value"] == pytest.approx(100.0 + st_alpha_row, abs=1e-6)
    assert r2["physical_value"] == pytest.approx(
        (100.0 + st_alpha_row) * 0.01 - 273.15, abs=1e-6)
    assert r2["value_domain"] == "physical"
    assert r2["unit"] == "degC"
    # 波段 1：无声明换算 → raw 域
    r1 = rows_out[("st-alpha", 1)]
    assert r1["value_domain"] == "raw"
    assert r1["unit_status"] == "unknown"
    # 波段分离：三行不同 band 标签
    assert {b for (_, b) in rows_out} == {1, 2, 3}


# ── S03：双源不同分辨率（各自网格） ────────────────────────────────

async def test_s03_two_sources_different_resolution(rs_env, raster_factory, seed_points_ref):
    import rasterio.transform as rt
    ref_c, _ = await _seed_constant_raster(
        raster_factory, SESS, name="coarse", value=111.0, width=8, height=8)
    ref_f, _ = await _seed_constant_raster(
        raster_factory, SESS, name="fine", value=222.0, width=80, height=80,
        transform=rt.from_origin(10.0, 20.0, 0.001, 0.001))
    out = await _make_point_plan(
        rs_env, raster_factory, seed_points_ref,
        sources=[{"ref_id": ref_c, "bands": [1]},
                 {"ref_id": ref_f, "bands": [1]}])
    plan = out["plan"]
    await rs_service.execute(
        session_id=SESS, plan_id=plan["plan_id"], owner_token=None,
        background=False, trusted_dispatch=True)
    results = await rs_service.results(
        session_id=SESS, plan_id=plan["plan_id"], owner_token=None, page=1,
        per_page=50)
    by_src = {}
    for r in results["rows"]:
        by_src.setdefault(r["source_ref"], []).append(r)
    assert len(by_src) == 2
    values = {tuple(sorted({r["raw_value"] for r in v})) for v in by_src.values()}
    assert values == {(111.0,), (222.0,)}  # 各自网格、无暗中重采样


# ── S20：预览后源内容变化 → STALE/BINDING typed 失败 ────────────────

async def test_s20_source_change_typed_rejection(rs_env, raster_factory, seed_points_ref):
    out = await _make_point_plan(rs_env, raster_factory, seed_points_ref)
    plan = out["plan"]
    await rs_service.preview(session_id=SESS, plan_id=plan["plan_id"],
                             owner_token=None, trusted_dispatch=True)
    # 覆写源文件内容（同 ref 同路径，mtime/size 改变 → 新 data_revision）
    path = rs_env["raster_root"] / "const.tif"
    import rasterio
    from rasterio.transform import from_origin
    with rasterio.open(path, "w", driver="GTiff", width=8, height=8,
                       count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(10.0, 20.0, 0.01, 0.01)) as dst:
        dst.write(np.full((1, 8, 8), 99.0, dtype="float32"))
    from app.services.raster_authoring.source import (
        _REVISION_CACHE, _SOURCE_CACHE, invalidate_source_cache,
    )
    _SOURCE_CACHE.clear()
    _REVISION_CACHE.clear()
    invalidate_source_cache(SESS, plan["sources"][0]["ref_id"])
    with pytest.raises(RasterSamplingError) as ei:
        await rs_service.execute(session_id=SESS, plan_id=plan["plan_id"],
                                 owner_token=None, background=False,
                                 trusted_dispatch=True)
    assert ei.value.code in ("SOURCE_BINDING_CHANGED", "STALE_BINDING")


# ── S21：仅样式变化不影响采样（幂等短路复用） ───────────────────────

async def test_s21_style_change_no_recompute(rs_env, raster_factory, seed_points_ref):
    out = await _make_point_plan(rs_env, raster_factory, seed_points_ref)
    plan = out["plan"]
    first = await rs_service.execute(
        session_id=SESS, plan_id=plan["plan_id"], owner_token=None,
        background=False, trusted_dispatch=True)
    # 「样式变化」= ref payload 增加 colormap 字段（不影响数据修订绑定：
    # resolve_source 的修订 key 不含样式键）
    from app.services.session_data import session_data_manager
    payload = await session_data_manager.get(
        SESS, plan["sources"][0]["ref_id"])
    payload["colormap"] = {"1": "#ff0000"}
    await session_data_manager.overwrite(
        SESS, plan["sources"][0]["ref_id"], payload)
    _SOURCE_CACHE_KEY = None
    from app.services.raster_authoring.source import _SOURCE_CACHE
    _SOURCE_CACHE.clear()
    second = await rs_service.execute(
        session_id=SESS, plan_id=plan["plan_id"], owner_token=None,
        background=False, trusted_dispatch=True)
    assert second.get("idempotent_reuse") is True
    assert (second["receipt"].execution_id
            == first["receipt"].execution_id)


# ── S23：同计划两副本并发 → 幂等收敛单发布 ─────────────────────────

async def test_s23_concurrent_execute_single_publication(rs_env, raster_factory,
                                                         seed_points_ref):
    out = await _make_point_plan(rs_env, raster_factory, seed_points_ref)
    plan_id = out["plan"]["plan_id"]
    results = await asyncio.gather(
        rs_service.execute(session_id=SESS, plan_id=plan_id, owner_token=None,
                           background=False, trusted_dispatch=True),
        rs_service.execute(session_id=SESS, plan_id=plan_id, owner_token=None,
                           background=False, trusted_dispatch=True),
    )
    exec_ids = set()
    for r in results:
        if r.get("receipt"):
            exec_ids.add(r["receipt"].execution_id)
        else:
            assert r.get("job_id"), r
    receipts = [x for x in await rs_store.list_receipts(SESS)
                if x.plan_id == plan_id]
    assert len(receipts) == 1
    assert len(exec_ids) <= 1


# ── S26：跨会话/伪造 ref → 权限闸 ──────────────────────────────────

async def test_s26_foreign_session_rejected(rs_env, raster_factory, seed_points_ref):
    out = await _make_point_plan(rs_env, raster_factory, seed_points_ref)
    plan = out["plan"]
    # 他人会话猜 plan id → PLAN_NOT_FOUND（不泄露存在性）
    with pytest.raises(RasterSamplingError) as ei:
        await rs_service.execute(session_id=SESS_OTHER, plan_id=plan["plan_id"],
                                 owner_token=None, background=False,
                                 trusted_dispatch=True)
    assert ei.value.code == "PLAN_NOT_FOUND"
    # 他人会话猜 raster ref → 计划创建即被源解析权限闸拒绝（fail-early）
    with pytest.raises(RasterSamplingError) as ei2:
        await _make_point_plan(
            rs_env, raster_factory, seed_points_ref, session_id=SESS_OTHER,
            sources=[{"ref_id": plan["sources"][0]["ref_id"], "bands": [1]}])
    assert ei2.value.code == "SOURCE_UNAVAILABLE"


# ── S28：成果恢复（重读收据 + 分页） ────────────────────────────────

async def test_s28_receipt_recovery_and_pagination(rs_env, raster_factory, seed_points_ref):
    out = await _make_point_plan(rs_env, raster_factory, seed_points_ref)
    plan_id = out["plan"]["plan_id"]
    await rs_service.execute(session_id=SESS, plan_id=plan_id,
                             owner_token=None, background=False,
                             trusted_dispatch=True)
    # 新会话状态（模拟重载）：收据/产物经 status/results 可恢复
    status = await rs_service.status(session_id=SESS, plan_id=plan_id)
    assert status["latest_receipt"] is not None
    assert status["outputs_alive"]["sample_table"] is True
    page1 = await rs_service.results(session_id=SESS, plan_id=plan_id,
                                     owner_token=None, page=1, per_page=2)
    assert len(page1["rows"]) == 2
    assert page1["pagination"]["filtered_rows"] == 3
    page2 = await rs_service.results(session_id=SESS, plan_id=plan_id,
                                     owner_token=None, page=2, per_page=2)
    assert len(page2["rows"]) == 1
    assert page1["rows"][0]["sample_id"] != page2["rows"][0]["sample_id"]


# ── S32：历史成果内容被清理 → typed 失效 ────────────────────────────

async def test_s32_results_expired_typed(rs_env, raster_factory, seed_points_ref):
    out = await _make_point_plan(rs_env, raster_factory, seed_points_ref)
    plan_id = out["plan"]["plan_id"]
    exec_out = await rs_service.execute(
        session_id=SESS, plan_id=plan_id, owner_token=None,
        background=False, trusted_dispatch=True)
    ref = exec_out["receipt"].outputs["sample_table_ref"]
    from app.services.artifact_registry import fabric_parquet_path
    path = fabric_parquet_path(SESS, ref)
    os.unlink(path)
    with pytest.raises(RasterSamplingError) as ei:
        await rs_service.results(session_id=SESS, plan_id=plan_id,
                                 owner_token=None)
    assert ei.value.code == "RESULTS_EXPIRED"
    # 历史收据仍可读（标旧版事实保留）
    status = await rs_service.status(session_id=SESS, plan_id=plan_id)
    assert status["latest_receipt"] is not None
    assert status["outputs_alive"]["sample_table"] is False


# ── S06/S07：分类声明拒绝 bilinear（服务面） ────────────────────────

async def test_s06_categorical_rejects_bilinear_at_plan(rs_env, raster_factory,
                                                        seed_points_ref):
    ref, _ = await _seed_constant_raster(raster_factory, SESS, name="cat")
    with pytest.raises(RasterSamplingError) as ei:
        await _make_point_plan(
            rs_env, raster_factory, seed_points_ref, method="bilinear",
            sources=[{"ref_id": ref, "bands": [1],
                      "band_semantics": {"1": "categorical"}}])
    assert ei.value.code == "METHOD_INCOMPATIBLE"
    # 未声明语义 → 同样拒绝插值（要求显式来源）
    with pytest.raises(RasterSamplingError) as ei2:
        await _make_point_plan(
            rs_env, raster_factory, seed_points_ref, method="bilinear",
            sources=[{"ref_id": ref, "bands": [1]}])
    assert ei2.value.code == "METHOD_INCOMPATIBLE"


# ── S04（服务面）：bilinear 声明 continuous 后可执行 ────────────────

async def test_s04_bilinear_journey_with_declaration(rs_env, raster_factory,
                                                     seed_points_ref):
    import rasterio.transform as rt
    rows, cols = np.mgrid[0:8, 0:8]
    values = (0.5 * cols + 2.0 * rows).astype("float32")[None, ...]
    ref, _ = await raster_factory(
        SESS, name="linear", bands=1, width=8, height=8, values=values,
        transform=rt.from_origin(10.0, 20.0, 0.01, 0.01))
    out = await _make_point_plan(
        rs_env, raster_factory, seed_points_ref, method="bilinear",
        sources=[{"ref_id": ref, "bands": [1],
                  "band_semantics": {"1": "continuous"}}])
    await rs_service.execute(
        session_id=SESS, plan_id=out["plan"]["plan_id"], owner_token=None,
        background=False, trusted_dispatch=True)
    results = await rs_service.results(
        session_id=SESS, plan_id=out["plan"]["plan_id"], owner_token=None,
        page=1, per_page=50)
    r = results["rows"][0]
    assert r["value_domain"] == "interpolated"
    assert r["interpolated_value"] is not None
    assert r["method"] == "bilinear"


# ── S31：弱指纹不当作不可变（中途改写被批间复核拦截） ──────────────

async def test_s31_weak_fingerprint_not_immutability(rs_env, raster_factory,
                                                     seed_points_ref):
    """源文件中部改写：即使 corner 采样/mtime 证据不足，批间代次复核
    也要拦截（本测试用直改 + 缓存失效模拟执行期漂移路径）。"""
    out = await _make_point_plan(rs_env, raster_factory, seed_points_ref)
    plan_id = out["plan"]["plan_id"]
    first = await rs_service.execute(
        session_id=SESS, plan_id=plan_id, owner_token=None,
        background=False, trusted_dispatch=True)
    assert first["receipt"].coverage.valid_cells == 3
    # 中部改写同尺寸（corner 证据不足场景）→ 下次执行绑定复核拒绝
    path = rs_env["raster_root"] / "const.tif"
    import rasterio
    from rasterio.transform import from_origin
    with rasterio.open(path, "w", driver="GTiff", width=8, height=8,
                       count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(10.0, 20.0, 0.01, 0.01)) as dst:
        dst.write(np.full((1, 8, 8), 7.0, dtype="float32"))
    from app.services.raster_authoring.source import (
        _REVISION_CACHE, _SOURCE_CACHE, invalidate_source_cache,
    )
    _SOURCE_CACHE.clear()
    _REVISION_CACHE.clear()
    invalidate_source_cache(SESS, out["plan"]["sources"][0]["ref_id"])
    with pytest.raises(RasterSamplingError) as ei:
        await rs_service.execute(session_id=SESS, plan_id=plan_id,
                                 owner_token=None, background=False,
                                 trusted_dispatch=True)
    assert ei.value.code == "SOURCE_BINDING_CHANGED"


async def test_invalid_reason_taxonomy_end_to_end(rs_env, raster_factory,
                                                  seed_points_ref):
    """S08（评审 P2 回归）：nodata / 非有限元在真实读取路径分型为
    nodata / non_finite，不折叠成 masked。"""
    import rasterio.transform as rt
    rows, cols = np.mgrid[0:8, 0:8]
    values = (100.0 + rows + cols).astype("float32")[None, ...].copy()
    values[0, 0, 4] = -9999.0                  # nodata 像元（row 0, col 4）
    values[0, 0, 6] = float("nan")             # 非有限元（row 0, col 6）
    ref, _ = await raster_factory(
        SESS, name="taxonomy", width=8, height=8, values=values,
        nodata=-9999.0, transform=rt.from_origin(10.0, 20.0, 0.01, 0.01))
    points = [
        {"id": "ok", "x": 10.015, "y": 19.995},    # col 1 有效
        {"id": "nod", "x": 10.045, "y": 19.995},   # col 4 → nodata
        {"id": "nan", "x": 10.065, "y": 19.995},   # col 6 → non_finite
    ]
    points_ref = await seed_points_ref(SESS, points)
    out = await rs_service.create_plan(
        session_id=SESS, owner_token=None, name="taxonomy",
        mode="source_points", points_ref=points_ref, method="source_pixel",
        sources=[{"ref_id": ref, "bands": [1]}], trusted_dispatch=True)
    exec_out = await rs_service.execute(
        session_id=SESS, plan_id=out["plan"]["plan_id"], owner_token=None,
        background=False, trusted_dispatch=True)
    cov = exec_out["receipt"].coverage
    assert cov.invalid_by_reason.get("nodata") == 1
    assert cov.invalid_by_reason.get("non_finite") == 1
    assert "masked" not in cov.invalid_by_reason
    results = await rs_service.results(
        session_id=SESS, plan_id=out["plan"]["plan_id"], owner_token=None,
        page=1, per_page=50)
    reasons = {r["parent_locator"]: r["invalid_reason"] for r in results["rows"]}
    assert reasons["ok"] is None
    assert reasons["nod"] == "nodata"
    assert reasons["nan"] == "non_finite"


# ── P1（jobs.py:110）：产物被 GC 后不得复用 completed 幂等行空转 ───────

async def test_gc_outputs_completed_job_row_reruns_and_publishes(
        rs_env, raster_factory, seed_points_ref):
    """产物被清掉（收据短路不命中）而幂等 job 行仍在 TTL 内 completed：
    再次执行必须真的重跑并发布新收据，不能复用终态行空转（内联路径）。"""
    from app.services.artifact_registry import (
        fabric_parquet_path,
        fabric_parquet_ref_exists,
    )

    out = await _make_point_plan(rs_env, raster_factory, seed_points_ref)
    plan_id = out["plan"]["plan_id"]
    first = await rs_service.execute(
        session_id=SESS, plan_id=plan_id, owner_token=None,
        background=False, trusted_dispatch=True)
    first_ref = first["receipt"].outputs["sample_table_ref"]
    assert fabric_parquet_ref_exists(SESS, first_ref)
    first_job = (await rs_service.status(
        session_id=SESS, plan_id=plan_id))["job"]
    assert first_job["status"] == "completed"

    # GC：产物 parquet 被清掉（收据与 completed job 行都还在复用 TTL 内）
    os.unlink(fabric_parquet_path(SESS, first_ref))
    assert not fabric_parquet_ref_exists(SESS, first_ref)

    second = await rs_service.execute(
        session_id=SESS, plan_id=plan_id, owner_token=None,
        background=False, trusted_dispatch=True)
    receipt = second.get("receipt")
    assert receipt is not None, second
    # 重算发布的新收据，且产物重新存活（而非复用死收据）
    assert receipt.execution_id != first["receipt"].execution_id
    assert fabric_parquet_ref_exists(SESS, receipt.outputs["sample_table_ref"])
    # 新 job 行接管幂等键，旧的 completed 行不再被复用
    second_job = (await rs_service.status(
        session_id=SESS, plan_id=plan_id))["job"]
    assert second_job["job_id"] != first_job["job_id"]
    assert second_job["status"] == "completed"
    results = await rs_service.results(
        session_id=SESS, plan_id=plan_id, owner_token=None, page=1, per_page=50)
    assert len(results["rows"]) == 3


async def test_gc_outputs_background_submit_does_not_spin(
        rs_env, raster_factory, seed_points_ref):
    """同一 GC 场景的 background=True：不得只回一个永不执行的 running。"""
    from app.services.artifact_registry import (
        fabric_parquet_path,
        fabric_parquet_ref_exists,
    )

    out = await _make_point_plan(rs_env, raster_factory, seed_points_ref)
    plan_id = out["plan"]["plan_id"]
    first = await rs_service.execute(
        session_id=SESS, plan_id=plan_id, owner_token=None,
        background=False, trusted_dispatch=True)
    os.unlink(fabric_parquet_path(SESS, first["receipt"].outputs["sample_table_ref"]))
    assert not fabric_parquet_ref_exists(
        SESS, first["receipt"].outputs["sample_table_ref"])

    submitted = await rs_service.execute(
        session_id=SESS, plan_id=plan_id, owner_token=None,
        background=True, trusted_dispatch=True)
    assert submitted.get("job_id")

    # 轮询到终态：复用的旧 completed 行会永远停在同一行上（空转）
    deadline = asyncio.get_event_loop().time() + 30.0
    while True:
        state = await rs_service.status(session_id=SESS, plan_id=plan_id)
        if state["job"]["status"] not in ("pending", "queued", "running",
                                          "cancelling"):
            break
        assert asyncio.get_event_loop().time() < deadline, "background job 空转"
        await asyncio.sleep(0.05)
    assert state["job"]["job_id"] == submitted["job_id"]
    assert state["job"]["status"] == "completed"
    assert state["latest_receipt"] is not None
    assert state["outputs_alive"]["sample_table"] is True


# ── P2 perf：status 的同步 DB 查询不得阻塞事件循环 ──────────────────

async def test_latest_job_sync_query_runs_off_event_loop(monkeypatch):
    import threading

    from app.services.data_operations import runner

    seen = {}

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, *args, **kwargs):
            seen["thread"] = threading.get_ident()
            return _Result()

        def commit(self):
            pass

    monkeypatch.setattr(runner, "_sync_session", lambda: _FakeSession())
    out = await rs_service._latest_job("sess-thread", "plan-thread")
    assert out is None
    assert seen["thread"] != threading.get_ident()  # 不在事件循环线程同步查库


# ── P2 api-contract：status/results 读取复核 owner_token ────────────

async def test_status_results_recheck_owner_token(rs_env, raster_factory,
                                                  seed_points_ref):
    from app.models.db_model import Conversation

    out = await _make_point_plan(rs_env, raster_factory, seed_points_ref)
    plan_id = out["plan"]["plan_id"]
    await rs_service.execute(
        session_id=SESS, plan_id=plan_id, owner_token=None,
        background=False, trusted_dispatch=True)
    with rs_env["sync_factory"]() as db:
        db.add(Conversation(id=SESS, owner_token="owner-secret"))
        db.commit()

    with pytest.raises(RasterSamplingError) as ei:
        await rs_service.status(session_id=SESS, plan_id=plan_id,
                                owner_token="wrong")
    assert ei.value.code == "UNAUTHORIZED"
    assert ei.value.status_code == 404
    with pytest.raises(RasterSamplingError) as ei2:
        await rs_service.results(session_id=SESS, plan_id=plan_id,
                                 owner_token="wrong")
    assert ei2.value.code == "UNAUTHORIZED"
    ok_status = await rs_service.status(session_id=SESS, plan_id=plan_id,
                                        owner_token="owner-secret")
    assert ok_status["success"] is True
    ok_results = await rs_service.results(
        session_id=SESS, plan_id=plan_id, owner_token="owner-secret",
        include_curves=False)
    assert ok_results["success"] is True


# ── P2 perf：results(include_curves) 只读样本表一次 ────────────────

async def test_results_include_curves_reads_table_once(
        rs_env, raster_factory, seed_points_ref, monkeypatch):
    out = await _make_point_plan(rs_env, raster_factory, seed_points_ref)
    plan_id = out["plan"]["plan_id"]
    await rs_service.execute(
        session_id=SESS, plan_id=plan_id, owner_token=None,
        background=False, trusted_dispatch=True)

    import pyarrow.parquet as pq

    reads = {"n": 0}
    orig_read_table = pq.read_table
    orig_parquet_file = pq.ParquetFile

    def _counting_read_table(*args, **kwargs):
        reads["n"] += 1
        return orig_read_table(*args, **kwargs)

    def _counting_parquet_file(*args, **kwargs):
        reads["n"] += 1
        return orig_parquet_file(*args, **kwargs)

    monkeypatch.setattr(pq, "read_table", _counting_read_table)
    monkeypatch.setattr(pq, "ParquetFile", _counting_parquet_file)
    res = await rs_service.results(
        session_id=SESS, plan_id=plan_id, owner_token=None,
        page=1, per_page=50, include_curves=True)
    assert res["curves"]["bands"]
    assert res["pagination"]["total_rows"] == 3
    assert reads["n"] == 1


async def test_results_streamed_filters_preserve_facts(
        rs_env, raster_factory, seed_points_ref):
    """分页/过滤改为 Arrow 批流后，过滤计数与页切片语义保持不变。"""
    out = await _make_point_plan(rs_env, raster_factory, seed_points_ref)
    plan_id = out["plan"]["plan_id"]
    ref_id = out["plan"]["sources"][0]["ref_id"]
    await rs_service.execute(
        session_id=SESS, plan_id=plan_id, owner_token=None,
        background=False, trusted_dispatch=True)

    page = await rs_service.results(
        session_id=SESS, plan_id=plan_id, owner_token=None,
        page=2, per_page=2, include_curves=False,
        source_ref=ref_id, band=1, valid=True)
    assert page["pagination"]["total_rows"] == 3
    assert page["pagination"]["filtered_rows"] == 3
    assert len(page["rows"]) == 1
    empty = await rs_service.results(
        session_id=SESS, plan_id=plan_id, owner_token=None,
        include_curves=False, band=999)
    assert empty["pagination"]["filtered_rows"] == 0
    assert empty["rows"] == []
