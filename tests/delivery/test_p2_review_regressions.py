"""G18 全量逐行审查 P2 修复回归（data_delivery）。

每条测试在未修复代码上必须失败：
- GeoJSON GeometryCollection 不得补 coordinates:null（RFC 7946）；
- PyogrioWriter 零批次 close 不得抛裸 FileNotFoundError/产空 ZIP；
- 反读门禁接受 WGS84/OGC:CRS84 目标别名并拒绝非 4326；
- table_rows 源在执行前必须复核内容指纹；
- durable 发布成功必须返回 state=published；
- 原子发布后的收尾异常不得把已发布交付标记 failed。
"""
from __future__ import annotations

import json

import pytest

from app.schemas.data_delivery_schema import (
    DeliverySourceRef,
    DeliverySpec,
)
from tests.delivery.conftest import run_job_to_completion


def _write_point_geojson(out_dir):
    from app.services.data_delivery.writers import GeoJSONWriter, WriterContext

    writer = GeoJSONWriter(WriterContext(
        fmt="geojson", out_dir=out_dir, base_filename="verify", fields=["n"]))
    writer.write_batch([{
        "type": "Feature", "id": "p1",
        "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
        "properties": {"n": 1},
    }])
    writer.close()


def test_geojson_geometrycollection_has_no_coordinates_member(tmp_path):
    """RFC 7946：GeometryCollection 只有 geometries，禁止补 coordinates。"""
    from app.services.data_delivery.writers import GeoJSONWriter, WriterContext

    writer = GeoJSONWriter(WriterContext(
        fmt="geojson", out_dir=tmp_path, base_filename="gc",
        fields=["n"], coordinate_precision=6))
    writer.write_batch([{
        "type": "Feature", "id": "g1",
        "geometry": {"type": "GeometryCollection", "geometries": [
            {"type": "Point", "coordinates": [116.123456789, 39.987654321]},
        ]},
        "properties": {"n": 1},
    }])
    result = writer.close()
    assert result.rows == 1

    fc = json.loads((tmp_path / "gc.geojson").read_text(encoding="utf-8"))
    geom = fc["features"][0]["geometry"]
    assert geom["type"] == "GeometryCollection"
    assert "coordinates" not in geom, "GeometryCollection 不得携带 coordinates"
    assert geom["geometries"][0]["coordinates"] == [116.123457, 39.987654]


@pytest.mark.parametrize("driver,ext", [
    ("GPKG", ".gpkg"),
    ("FlatGeobuf", ".fgb"),
    ("ESRI Shapefile", ".zip"),
])
def test_pyogrio_writer_zero_rows_is_typed_and_writes_nothing(
        tmp_path, driver, ext):
    """零批次 close：typed WRITE_FAILED，既不抛裸 FileNotFoundError 也不产空包。"""
    from app.services.data_delivery.writers import (
        PyogrioWriter,
        WriterContext,
        WriterError,
    )

    fmt = {"GPKG": "gpkg", "FlatGeobuf": "flatgeobuf",
           "ESRI Shapefile": "shapefile_zip"}[driver]
    ctx = WriterContext(
        fmt=fmt, out_dir=tmp_path, base_filename="empty", fields=["a"],
        crs_out="EPSG:4326")
    writer = PyogrioWriter(
        ctx, driver=driver, layer_name="empty", geometry_type="Point")
    with pytest.raises(WriterError) as ei:
        writer.close()
    assert ei.value.error_code == "WRITE_FAILED"
    assert not (tmp_path / f"empty{ext}").exists(), "不得留下空数据成员"


@pytest.mark.parametrize("target", ["WGS84", "OGC:CRS84", "epsg:4326"])
def test_verify_output_accepts_wgs84_alias_targets(tmp_path, target):
    """loss.py 允许的 WGS84/OGC:CRS84 目标别名不得被反读门禁误杀。"""
    from app.services.data_delivery.service import _verify_output

    _write_point_geojson(tmp_path)
    evidence = _verify_output(
        tmp_path, "geojson", 1, {"transformed": True, "target": target})
    assert evidence["rows_read"] == 1


def test_verify_output_still_rejects_non_wgs84_target(tmp_path):
    from app.services.data_delivery import DeliveryError
    from app.services.data_delivery.service import _verify_output

    _write_point_geojson(tmp_path)
    with pytest.raises(DeliveryError) as ei:
        _verify_output(
            tmp_path, "geojson", 1,
            {"transformed": True, "target": "EPSG:3857"})
    assert ei.value.error_code == "VERIFY_FAILED"


async def test_table_rows_verify_unchanged_detects_replacement(delivery_env, actor):
    """table_rows 源在预览后 rows 被替换 → SOURCE_REVISION_CHANGED。"""
    from app.services.data_delivery.source_resolver import (
        SourceResolutionError,
        resolve_source,
    )
    from app.services.session_data import session_data_manager

    payload = {"rows": [{"n": 1}, {"n": 2}],
               "is_complete": True, "truncated": False}
    ref = await session_data_manager.store("sess-1", payload, prefix="table")
    snap = await resolve_source(DeliverySourceRef(
        kind="table_rows", session_id="sess-1", table_ref=ref), actor)

    # 基线：未变化时通过
    await snap.verify_unchanged("sess-1")

    session_data_manager._store["sess-1"][ref]["rows"][0]["n"] = 99
    with pytest.raises(SourceResolutionError) as ei:
        await snap.verify_unchanged("sess-1")
    assert ei.value.error_code == "SOURCE_REVISION_CHANGED"


def test_publish_data_object_success_reports_published_state(
        delivery_env, monkeypatch):
    """成功分支必须返回 docstring 承诺的 state=published。"""
    from app.services.data_delivery import store as dl_store
    from app.services.lakehouse import data_object as data_object_mod

    delivery_id = "d" * 16
    final = dl_store.final_dir(delivery_id)
    final.mkdir(parents=True)
    (final / "data.geojson").write_text("{}", encoding="utf-8")

    class _Identity:
        data_object_id = "a" * 64
        manifest_location = "cas/manifest.json"
        content_sha256 = "b" * 64
        byte_size = 2
        deduped = False

    monkeypatch.setattr(data_object_mod, "publish_data_object",
                        lambda *args, **kwargs: _Identity())
    result = dl_store.publish_data_object_for_delivery(
        delivery_id, owner_scope={"session_id": "sess-1"})
    assert result["state"] == "published"
    assert result["data_object_id"] == "a" * 64


async def test_finalize_failure_after_publish_keeps_delivery_completed(
        delivery_env, seeded_session, actor, monkeypatch):
    """atomic_publish 后的收尾异常不得把已发布交付标记 failed/下架。"""
    from app.services.data_delivery import (
        get_delivery_job,
        preview_delivery,
        resolve_download,
        submit_delivery,
    )
    from app.services.data_delivery import service as dd_service

    async def boom(*args, **kwargs):
        raise RuntimeError("simulated receipt write / DB failure")

    monkeypatch.setattr(dd_service, "_finalize_delivery", boom)
    spec = DeliverySpec(
        source=DeliverySourceRef(
            kind="session_ref", session_id="sess-1",
            ref=seeded_session["ref"]),
        format="geojson", filename="finalize-fault")
    preview = await preview_delivery(spec, actor)
    assert not preview.blocking_issues
    sub = await submit_delivery(
        spec, actor,
        acknowledged_losses=preview.required_acknowledgments)
    await run_job_to_completion(sub["job_id"])

    view = await get_delivery_job(sub["job_id"], actor)
    assert view.status == "completed", (view.status, view.error)
    path, _, _, _ = await resolve_download(view.job_id, None, actor)
    assert path.is_file()
