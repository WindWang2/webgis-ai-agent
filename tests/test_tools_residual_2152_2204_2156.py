"""残余工具/数据交付修复回归（#2152 Overpass relation、#2204 数据交付
sync-in-async、#2156 P3 未完成子项：报告格式/上传会话 fail-closed/
materialize 会话与 offload/COG out_dir/资产删除顺序/terrain nodata）。

先回归后修复；安全项同时验证攻击者与正常用户。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ── #2152 Overpass relation：out body geom 必须组装 (Multi)Polygon ─────────

def _relation_element():
    """一个 2 outer way（各两段）+ 1 inner hole 的 relation，模拟
    Overpass ``out body geom`` 的 member 折线形态（way 被打断成多段）。"""
    return {
        "type": "relation", "id": 100,
        "tags": {"name": "测试区", "admin_level": "8", "type": "boundary"},
        "members": [
            {"type": "way", "ref": 1, "role": "outer",
             "geometry": [{"lat": 0.0, "lon": 0.0}, {"lat": 0.0, "lon": 1.0}]},
            {"type": "way", "ref": 2, "role": "outer",
             "geometry": [{"lat": 0.0, "lon": 1.0}, {"lat": 1.0, "lon": 1.0}]},
            {"type": "way", "ref": 3, "role": "outer",
             "geometry": [{"lat": 1.0, "lon": 1.0}, {"lat": 1.0, "lon": 0.0}]},
            {"type": "way", "ref": 4, "role": "outer",
             "geometry": [{"lat": 1.0, "lon": 0.0}, {"lat": 0.0, "lon": 0.0}]},
            {"type": "way", "ref": 5, "role": "inner",
             "geometry": [{"lat": 0.4, "lon": 0.4}, {"lat": 0.4, "lon": 0.6},
                          {"lat": 0.6, "lon": 0.6}, {"lat": 0.6, "lon": 0.4},
                          {"lat": 0.4, "lon": 0.4}]},
        ],
    }


def test_2152_relation_geom_assembled_from_members():
    from app.tools.osm import _overpass_to_geojson

    data = {"elements": [_relation_element()]}
    fc = _overpass_to_geojson(json.dumps(data))
    assert len(fc["features"]) == 1, "relation 被静默丢弃（#2152）"
    geom = fc["features"][0]["geometry"]
    assert geom["type"] == "Polygon"
    # 单 outer + 1 hole
    assert len(geom["coordinates"]) == 2
    outer = geom["coordinates"][0]
    assert len(outer) == 5 and outer[0] == outer[-1] == [0.0, 0.0]


def test_2152_multi_outer_relation_is_multipolygon():
    from app.tools.osm import _overpass_to_geojson

    el = _relation_element()
    # 第二个独立 outer 环（不与第一环共享端点）
    el["members"].extend([
        {"type": "way", "ref": 6, "role": "outer",
         "geometry": [{"lat": 2.0, "lon": 2.0}, {"lat": 2.0, "lon": 3.0}]},
        {"type": "way", "ref": 7, "role": "outer",
         "geometry": [{"lat": 2.0, "lon": 3.0}, {"lat": 3.0, "lon": 3.0}]},
        {"type": "way", "ref": 8, "role": "outer",
         "geometry": [{"lat": 3.0, "lon": 3.0}, {"lat": 3.0, "lon": 2.0}]},
        {"type": "way", "ref": 9, "role": "outer",
         "geometry": [{"lat": 3.0, "lon": 2.0}, {"lat": 2.0, "lon": 2.0}]},
    ])
    fc = _overpass_to_geojson({"elements": [el]})
    geom = fc["features"][0]["geometry"]
    assert geom["type"] == "MultiPolygon"
    assert len(geom["coordinates"]) == 2


def test_2152_out_center_relation_still_point():
    """out center 语义（有 center 无 member 几何）保持点要素，不误伤。"""
    from app.tools.osm import _overpass_to_geojson

    fc = _overpass_to_geojson({"elements": [
        {"type": "relation", "id": 7, "tags": {"name": "x"},
         "center": {"lat": 30.0, "lon": 104.0}},
    ]})
    assert fc["features"][0]["geometry"] == {
        "type": "Point", "coordinates": [104.0, 30.0]}


@pytest.mark.asyncio
async def test_2152_boundary_overpass_hit_no_fallback_note():
    """Overpass relation 命中时：不再降级 Nominatim，source=overpass。"""
    from app.tools.osm import register_osm_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_osm_tools(reg)

    async def fake_tracked(name, url, params=None, **kwargs):
        if name == "overpass":
            return {"elements": [_relation_element()]}
        return []  # nominatim 不应被调用

    with patch("app.tools.osm.tracked_provider_get", side_effect=fake_tracked):
        result = await reg.dispatch(
            "query_osm_boundary", {"name": "测试区", "admin_level": 8})
    assert result["count"] == 1, f"relation 被丢弃导致降级/空结果: {result}"
    assert result["source"] == "overpass"
    assert "fallback_note" not in result


@pytest.mark.asyncio
async def test_2152_boundary_both_miss_reports_error():
    """双源皆未命中 → 显式 error，而不是 count=0 假成功。"""
    from app.tools.osm import register_osm_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_osm_tools(reg)

    async def fake_tracked(name, url, params=None, **kwargs):
        return {"elements": []} if name == "overpass" else []

    with patch("app.tools.osm.tracked_provider_get", side_effect=fake_tracked):
        result = await reg.dispatch(
            "query_osm_boundary", {"name": "不存在的地方xyz", "admin_level": 8})
    assert result["count"] == 0
    assert result.get("error"), "双源未命中应给出显式错误（#2152）"


# ── #2204 data_delivery：Parquet/SHA256 同步 IO 移出事件循环 ──────────────

@pytest.mark.asyncio
async def test_2204_verify_unchanged_sha256_offloaded(tmp_path, monkeypatch):
    import asyncio
    from app.services.data_delivery import source_resolver as sr

    # 线程标识探针：若 SHA256 在工作线程执行，此事件被 set
    ran_in_thread = asyncio.Event()

    real_sha = sr._file_sha256

    def probe(path):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            ran_in_thread.set()
        return real_sha(path)

    monkeypatch.setattr(sr, "_file_sha256", probe)
    # 源码内按模块全局名解析 _file_sha256 —— 但 verify_unchanged 经
    # self 方法引用模块函数，patch 模块属性即可。

    payload_file = tmp_path / "src.parquet"
    payload_file.write_bytes(b"parquet-bytes")
    fingerprint = real_sha(payload_file)

    snap = sr.SourceSnapshot(
        kind="fabric_parquet", label="ref", is_table=False,
        row_count=1, content_fingerprint=fingerprint,
        fields=[], parquet_path=payload_file,
    )
    await snap.verify_unchanged(None)
    assert ran_in_thread.is_set(), "SHA256 复核仍在事件循环线程执行（#2204）"


@pytest.mark.asyncio
async def test_2204_resolve_parquet_snapshot_offloaded(tmp_path, monkeypatch):
    import asyncio
    from app.services.data_delivery import source_resolver as sr

    ran_in_thread = asyncio.Event()
    real_fn = sr._snapshot_from_parquet

    def probe(ref, path):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            ran_in_thread.set()
        return real_fn(ref, path)

    monkeypatch.setattr(sr, "_snapshot_from_parquet", probe)

    schema = pytest.importorskip("pyarrow").schema(
        [("name", pytest.importorskip("pyarrow").string())])
    import pyarrow.parquet as pq
    pq.write_table(
        pytest.importorskip("pyarrow").table({"name": ["a"]}),
        tmp_path / "t.parquet")

    from app.schemas.data_delivery_schema import DeliverySourceRef
    actor = sr.DeliveryActor(user_id="u1")

    monkeypatch.setattr(sr, "verify_session_access", lambda a, s: asyncio.sleep(0))
    monkeypatch.setattr("app.services.artifact_registry.is_fabric_parquet_ref",
                        lambda ref: True)
    monkeypatch.setattr("app.services.artifact_registry.fabric_parquet_path",
                        lambda sid, ref: tmp_path / "t.parquet")

    await sr.resolve_source(
        DeliverySourceRef(kind="fabric_parquet", session_id="s1", ref="ref:fabric-parquet/x"),
        actor,
    )
    assert ran_in_thread.is_set(), "parquet 快照解析仍在事件循环执行（#2204）"


# ── #2156(P3) generate_analysis_report：format 白名单 ─────────────────────

@pytest.mark.asyncio
async def test_2156_report_format_path_traversal_rejected():
    from app.tools.report import register_report_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_report_tools(reg)
    fn = reg._tools["generate_analysis_report"]
    out = await fn(format="x/../../../../tmp/evil", session_id="s-1")
    assert "error" in out
    assert "不支持的报告格式" in out["error"]


@pytest.mark.asyncio
async def test_2156_report_valid_formats_accepted():
    from app.tools.report import register_report_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_report_tools(reg)
    fn = reg._tools["generate_analysis_report"]
    # 正常用户的合法格式不被误伤（会话不存在 → 另一 typed 错误，不是格式拒绝）
    out = await fn(format="markdown", session_id="no-such-session-xyz")
    assert "不支持的报告格式" not in str(out.get("error", ""))


# ── #2156(P3) _resolve_session_id fail closed：攻击者与正常用户 ────────────

def test_2156_upload_session_fail_closed_attacker_vs_user():
    from app.lib.runtime.context import bind_runtime_context
    from app.tools.upload_tools import _resolve_session_id

    # 攻击者：注入他人 session_id，无 runtime context（Celery/子代理/harness）
    assert _resolve_session_id("victim-session") is None
    # 攻击者：上下文绑定自己但参数点名他人 → 拒绝
    with bind_runtime_context(session_id="own-session"):
        assert _resolve_session_id("victim-session") is None
    # 正常用户：上下文绑定 + 参数一致 / 缺省 → 放行
    with bind_runtime_context(session_id="own-session"):
        assert _resolve_session_id("own-session") == "own-session"
        assert _resolve_session_id(None) == "own-session"


# ── #2156(P3) materialize_dataset：会话 fail closed + 解析 offloaded ───────

@pytest.mark.asyncio
async def test_2156_materialize_requires_session():
    from app.tools.data_fabric_tools import register_data_fabric_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_data_fabric_tools(reg)
    fn = reg._tools["materialize_dataset"]
    out = await fn(dataset_id="ds1")  # 无 session_id
    assert out["status"] == "error"
    assert out["error_type"] == "SESSION_REQUIRED"


@pytest.mark.asyncio
async def test_2156_materialize_resolution_offloaded(monkeypatch):
    import asyncio
    from app.tools.data_fabric_tools import register_data_fabric_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_data_fabric_tools(reg)
    fn = reg._tools["materialize_dataset"]

    ran_in_thread = asyncio.Event()

    def probe(pid, sid):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            ran_in_thread.set()
        return None  # 无适配器 → typed 错误（不触碰网络/DB）

    monkeypatch.setattr(
        "app.tools.data_fabric_tools._resolve_source_adapter", probe)
    monkeypatch.setattr(
        "app.tools.data_fabric_tools.spatial_catalog_service.get_profile_id",
        lambda ds, owner=None: "pid-1")

    out = await fn(dataset_id="ds1", session_id="sess-1", profile_id="pid-1")
    assert out["status"] == "error"  # 无适配器 typed 错误
    assert ran_in_thread.is_set(), "适配器解析仍在事件循环执行（#2156）"


# ── #2156(P3) convert_raster_to_cog：out_dir 双前缀 ───────────────────────

@pytest.mark.asyncio
async def test_2156_cog_out_dir_no_double_data_prefix(tmp_path, monkeypatch):
    import os
    from app.tools.raster_tools_cog import register_raster_cog_tools
    from app.tools.registry import ToolRegistry

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    reg = ToolRegistry()
    register_raster_cog_tools(reg)
    fn = reg._tools["convert_raster_to_cog"]

    captured: dict = {}

    def fake_ensure_cog(src, dst, session_id=""):
        captured["dst"] = dst
        return dst

    import app.tools.raster_tools_cog as cog_mod

    async def _inline(f, *a, **k):
        return f(*a, **k)

    monkeypatch.setattr(cog_mod.asyncio, "to_thread", _inline)
    monkeypatch.setattr("app.lib.geo_raster.cog.ensure_cog", fake_ensure_cog)
    monkeypatch.setattr("app.lib.geo_raster.cog.validate_cog",
                        lambda p: {"ok": True, "driver": "GTiff"})

    src = tmp_path / "data" / "in.tif"
    src.write_bytes(b"x")
    # LLM 按描述传 "./data/..." 形态 —— 旧实现解析到 ./data/data/cog
    out = await fn(raster_path="in.tif", out_dir="./data/cog", session_id="")
    assert out.get("success") is True, out
    dst = captured["dst"]
    norm = os.path.normpath(dst)
    assert "data/data" not in norm, f"双前缀未消除: {dst}"
    assert norm.endswith(os.path.join("data", "cog")), norm


# ── #2156(P3) manage_analysis_asset 删除顺序：先提交后删文件 ──────────────

def test_2156_asset_delete_commits_before_unlink():
    import os as _os
    import app.tools.nature_resources as nr
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    nr.register_nature_resource_tools(reg)
    manage = reg._tools["manage_analysis_asset"]

    order: list = []
    record = MagicMock()
    record.id = 1
    record.session_id = "sess-1"
    record.original_name = "ndvi.tif"
    record.filename = "data/uploads/ndvi.tif"

    class FakeDB:
        def query(self, *a, **k):
            m = MagicMock()
            m.filter.return_value.first.return_value = record
            return m

        def delete(self, r):
            order.append("db.delete")

        def commit(self):
            order.append("db.commit")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    removed: list = []
    with patch.object(nr, "db_session", lambda: FakeDB()), \
            patch.object(nr, "_resolve_session_id", lambda s: "sess-1"), \
            patch("app.utils.path.validate_data_path", lambda p, d=None: p), \
            patch.object(_os.path, "exists", lambda p: True), \
            patch.object(_os, "remove", lambda p: removed.append(p)):
        out = manage(asset_id=1, action="delete", session_id="sess-1")
    assert out.get("success") is True
    assert order == ["db.delete", "db.commit"], order
    assert removed, "提交成功后应删除物理文件"


# ── #2156(P3) terrain 产物：NaN 单元重写成声明的 nodata ───────────────────

def test_2156_persist_raster_product_nan_to_declared_nodata():
    from app.tools._terrain_support import _nan_to_declared_nodata

    arr = np.array([[1.0, np.nan], [3.0, 4.0]])
    out = _nan_to_declared_nodata(arr, -9999.0)
    assert out[0, 1] == -9999.0
    assert out[0, 0] == 1.0
    # 无 NaN 原样返回（不复制语义不变）
    clean = np.ones((2, 2))
    assert _nan_to_declared_nodata(clean, -9999.0) is clean
