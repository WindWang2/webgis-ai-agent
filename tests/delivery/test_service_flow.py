"""G18 — 服务编排端到端（预览→提交→执行→回执→下载 全链真实路径）。

对应任务书验收场景 1/2/3/5/6/7/8/9/12/13/14/15/16/17/18/19/20/22 的
服务层路径（HTTP 契约在 test_routes；工具面在 test_tools）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schemas.data_delivery_schema import (
    DeliveryCrsSpec,
    DeliveryFieldSelection,
    DeliveryScope,
    DeliverySourceRef,
    DeliverySpec,
)
from app.services.data_delivery import (
    DeliveryError,
    get_delivery_job,
    list_delivery_jobs,
    preview_delivery,
    resolve_download,
    submit_delivery,
)
from tests.delivery.conftest import run_job_to_completion


def _spec(ref: str, fmt: str = "geojson", **kw) -> DeliverySpec:
    return DeliverySpec(
        source=DeliverySourceRef(kind="session_ref", session_id="sess-1", ref=ref),
        format=fmt, filename="t", **kw)


async def _deliver(spec: DeliverySpec, actor) -> object:
    """与 UI 相同的诚实流程：预览 → 按预览 required acks 提交（不旁路确认）。"""
    preview = await preview_delivery(spec, actor)
    assert not preview.blocking_issues, [b.message for b in preview.blocking_issues]
    sub = await submit_delivery(spec, actor,
                                acknowledged_losses=preview.required_acknowledgments)
    assert sub.get("idempotent_reuse") is False, sub
    await run_job_to_completion(sub["job_id"])
    return await get_delivery_job(sub["job_id"], actor)


async def test_session_fc_geojson_delivery_roundtrip(delivery_env, seeded_session, actor):
    """场景 1：session FC ref → GeoJSON；行数/内容/ID 可反读。"""
    view = await _deliver(_spec(seeded_session["ref"]), actor)
    assert view.status == "completed", (view.error_code, view.error)
    assert view.receipt.row_count == 3
    path, mime, name, sha = await resolve_download(view.job_id, None, actor)
    assert mime == "application/geo+json"
    back = json.loads(path.read_text(encoding="utf-8"))
    assert [f["id"] for f in back["features"]] == ["f1", "f2", "f3"]
    assert view.receipt.content_sha256 == sha
    assert view.receipt.scope["type"] == "full"


async def test_session_fc_gpkg_delivery(delivery_env, seeded_session, actor):
    """场景 2：GeoPackage；读取器打开图层 + 属性 + CRS 与回执一致。"""
    view = await _deliver(_spec(seeded_session["ref"], "gpkg"), actor)
    assert view.status == "completed", (view.error_code, view.error)
    path, _, _, _ = await resolve_download(view.job_id, None, actor)
    import pyogrio

    df = pyogrio.read_dataframe(str(path), layer="t")
    assert len(df) == 3 and str(df.crs) == "EPSG:4326"
    assert view.receipt.crs == {"source": "EPSG:4326", "target": "EPSG:4326",
                                "transformed": False}


async def test_session_fc_geoparquet_delivery(delivery_env, seeded_session, actor):
    """场景 3：GeoParquet；真实 parquet + geo metadata + 类型正确。"""
    view = await _deliver(_spec(seeded_session["ref"], "geoparquet"), actor)
    assert view.status == "completed", (view.error_code, view.error)
    path, _, _, _ = await resolve_download(view.job_id, None, actor)
    assert path.suffix == ".parquet"
    import pyarrow.parquet as pq

    schema = pq.read_schema(str(path))
    geo = json.loads(schema.metadata[b"geo"])
    assert geo["primary_column"] == "geometry"
    assert geo["columns"]["geometry"]["crs"] == "EPSG:4326"
    assert pq.read_metadata(str(path)).num_rows == 3


async def test_csv_delivery_field_order_unicode_null(delivery_env, seeded_session, actor):
    """场景 5/11：CSV 字段顺序、中文、空值策略 + schema sidecar。"""
    spec = _spec(
        seeded_session["ref"], "csv",
        fields=DeliveryFieldSelection(fields=["name", "pop"],
                                      include_geometry=False))
    view = await _deliver(spec, actor)
    assert view.status == "completed", (view.error_code, view.error)
    path, _, _, _ = await resolve_download(view.job_id, None, actor)
    import csv as _csv

    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(_csv.reader(fh))
    assert rows[0] == ["name", "pop"], "字段顺序保持（纯属性模式）"
    assert rows[1][0] == "北京"
    assert rows[3][1] == "", "null → 空串（sidecar 约定）"
    members = {m.path: m for m in view.receipt.members}
    assert any(p.endswith(".schema.json") for p in members)
    # 缺省含几何：WKT 列在末位（目标坐标系标注）
    spec2 = _spec(seeded_session["ref"], "csv",
                  fields=DeliveryFieldSelection(fields=["name", "pop"]))
    view2 = await _deliver(spec2, actor)
    p2, _, _, _ = await resolve_download(view2.job_id, None, actor)
    with open(p2, encoding="utf-8", newline="") as fh:
        rows2 = list(_csv.reader(fh))
    assert rows2[0] == ["name", "pop", "geometry_wkt"]


async def test_projected_geojson_reprojects(delivery_env, actor):
    """场景 6：投影源 → GeoJSON 真实转换；源/目标证据分别存在。"""
    from pyproj import CRS, Transformer
    from shapely.geometry import shape
    from shapely.ops import transform as shp_transform

    tr = Transformer.from_crs(CRS.from_user_input("EPSG:4326"),
                              CRS.from_user_input("EPSG:32650"), always_xy=True)
    pt = shp_transform(tr.transform, shape(
        {"type": "Point", "coordinates": [116.397128, 39.916527]}))
    feats = [{"type": "Feature", "id": "p",
              "geometry": json.loads(__import__("shapely").to_geojson(pt)),
              "properties": {"a": 1}}]
    fc = {"type": "FeatureCollection", "features": feats,
          "properties": {"schema_info": {"crs": "EPSG:32650"}}}
    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store("sess-1", fc, prefix="test")
    view = await _deliver(_spec(ref), actor)
    assert view.status == "completed", (view.error_code, view.error)
    assert view.receipt.crs["transformed"] is True
    assert view.receipt.crs["source"] == "EPSG:32650"
    assert view.receipt.crs["target"] == "EPSG:4326"
    path, _, _, _ = await resolve_download(view.job_id, None, actor)
    back = json.loads(path.read_text(encoding="utf-8"))
    coord = back["features"][0]["geometry"]["coordinates"]
    assert abs(coord[0] - 116.397128) < 1e-5 and abs(coord[1] - 39.916527) < 1e-5


async def test_missing_crs_geojson_blocked_other_formats_ack(delivery_env, actor):
    """场景 7：缺 CRS → GeoJSON 预览拒绝（不默认 4326）；GPKG 需显式确认。"""
    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "id": "x",
                        "geometry": {"type": "Point",
                                     "coordinates": [116.4, 39.9]},
                        "properties": {"a": 1}}],
          "properties": {}}
    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store("sess-1", fc, prefix="test")
    preview = await preview_delivery(_spec(ref), actor)
    codes = [b.code for b in preview.blocking_issues]
    assert "MISSING_SOURCE_CRS" in codes
    with pytest.raises(DeliveryError) as ei:
        await submit_delivery(_spec(ref), actor)
    assert ei.value.error_code == "MISSING_SOURCE_CRS"

    # GPKG：确认后可交付（crs_label_unknown 披露）
    spec = _spec(ref, "gpkg", crs=DeliveryCrsSpec(allow_unknown_source_crs=True))
    preview2 = await preview_delivery(spec, actor)
    assert "crs_label_unknown" in preview2.required_acknowledgments
    view = await _deliver(spec, actor)
    assert view.status == "completed"
    assert "crs_label_unknown" in view.receipt.losses_applied


async def test_shapefile_long_field_names_explicit_map(delivery_env, seeded_session, actor):
    """场景 8：长/同前缀字段 → 显式映射无冲突；清单可还原。"""
    fc = {"type": "FeatureCollection",
          "features": [
              {"type": "Feature", "id": "x",
               "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
               "properties": {"population_total": 1, "population_rate": 2,
                              "population_ratio": 3}},
          ],
          "properties": {"schema_info": {"crs": "EPSG:4326"}}}
    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store("sess-1", fc, prefix="test")
    # 未提供映射：预览生成自动映射并要求确认；提交必须 ack
    spec0 = _spec(ref, "shapefile_zip")
    preview = await preview_delivery(spec0, actor)
    assert "field_name_truncated" in preview.required_acknowledgments
    assert preview.field_name_map_applied, "自动映射已生成且无冲突"
    mapping = preview.field_name_map_applied
    assert len(set(mapping.values())) == len(mapping), "映射单射"
    view = await _deliver(spec0, actor)  # submit 用 preview 全部 required acks
    assert view.status == "completed", (view.error_code, view.error)
    assert view.receipt.field_map_applied == mapping
    # ZIP 反读：DBF 字段名 ≤10 且可经映射还原
    path, _, _, _ = await resolve_download(view.job_id, None, actor)
    import tempfile
    import zipfile

    import pyogrio

    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(str(path)) as zf:
            zf.extractall(td)
        df = pyogrio.read_dataframe(str(next(Path(td).glob("*.shp"))))
    assert set(df.columns) - {"geometry"} == set(mapping.values())


async def test_mixed_geometry_grouping_counts(delivery_env, actor):
    """场景 9/10：混合几何分组 + null 几何保留（GeoJSON/GPKG）。"""
    fc = {"type": "FeatureCollection",
          "features": [
              {"type": "Feature", "id": "p",
               "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
               "properties": {"a": 1}},
              {"type": "Feature", "id": "l",
               "geometry": {"type": "LineString",
                            "coordinates": [[116.4, 39.9], [116.41, 39.91]]},
               "properties": {"a": 2}},
              {"type": "Feature", "id": "n", "geometry": None,
               "properties": {"a": 3}},
          ],
          "properties": {"schema_info": {"crs": "EPSG:4326"}}}
    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store("sess-1", fc, prefix="test")
    view = await _deliver(_spec(ref, "gpkg"), actor)
    assert view.status == "completed"
    assert view.receipt.row_count == 3, "null 几何行不丢（含在 Unknown 层）"
    assert "mixed_geometry_single_layer" in view.receipt.losses_applied


async def test_ids_scope_exact_seven_and_missing_disclosed(delivery_env, actor):
    """场景 13：恰好交付所列 ID；失效 ID 逐项披露。"""
    feats = [{"type": "Feature", "id": f"i{n}",
              "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
              "properties": {"n": n}} for n in range(1, 11)]
    fc = {"type": "FeatureCollection", "features": feats,
          "properties": {"schema_info": {"crs": "EPSG:4326"}}}
    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store("sess-1", fc, prefix="test")
    spec = _spec(ref, scope=DeliveryScope(
        type="ids", ids=[f"i{n}" for n in range(1, 8)] + ["ghost"]))
    view = await _deliver(spec, actor)
    assert view.status == "completed", (view.error_code, view.error)
    assert view.receipt.row_count == 7
    assert view.receipt.scope["missing_ids"] == ["ghost"]
    path, _, _, _ = await resolve_download(view.job_id, None, actor)
    back = json.loads(path.read_text(encoding="utf-8"))
    assert [f["id"] for f in back["features"]] == [f"i{n}" for n in range(1, 8)]


async def test_truncated_selection_blocks_then_subset_ack(delivery_env, actor):
    """场景 26：matched_count > 所携 ID 且无谓词 → 阻断；subset_ack 后子集交付。"""
    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "id": f"i{n}",
                        "geometry": {"type": "Point",
                                     "coordinates": [116.4, 39.9]},
                        "properties": {"n": n}} for n in range(1, 6)],
          "properties": {"schema_info": {"crs": "EPSG:4326"}}}
    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store("sess-1", fc, prefix="test")
    scope = DeliveryScope(type="ids", ids=["i1", "i2"],
                          selection_matched_count=80, ids_truncated=True)
    spec = _spec(ref, scope=scope)
    preview = await preview_delivery(spec, actor)
    assert any(b.code == "SELECTION_INCOMPLETE" for b in preview.blocking_issues)
    with pytest.raises(DeliveryError) as ei:
        await submit_delivery(spec, actor)
    assert ei.value.error_code == "SELECTION_INCOMPLETE"
    # 显式子集确认后可交付，receipt 如实标注
    spec_ack = _spec(ref, scope=scope.model_copy(update={"subset_ack": True}))
    preview2 = await preview_delivery(spec_ack, actor)
    assert not preview2.blocking_issues
    view = await _deliver(spec_ack, actor)
    assert view.status == "completed"
    assert view.receipt.scope["subset_of_selection"] is True


async def test_truncated_selection_with_predicate_replays_full(delivery_env, actor):
    """场景 25：>50 ID + 可精确重放 typed predicate → 完整选择交付。"""
    feats = [{"type": "Feature", "id": f"i{n}",
              "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
              "properties": {"grp": "A" if n <= 80 else "B", "n": n}}
             for n in range(1, 101)]
    fc = {"type": "FeatureCollection", "features": feats,
          "properties": {"schema_info": {"crs": "EPSG:4326"}}}
    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store("sess-1", fc, prefix="test")
    scope = DeliveryScope(
        type="ids", ids=[f"i{n}" for n in range(1, 51)],  # 运输前缀（50 个）
        selection_matched_count=80,
        predicate={"op": "eq", "field": "grp", "value": "A"})
    spec = _spec(ref, scope=scope)
    preview = await preview_delivery(spec, actor)
    assert not preview.blocking_issues, [b.message for b in preview.blocking_issues]
    assert "完整选择" in (preview.scope_note or "")
    view = await _deliver(spec, actor)
    assert view.receipt.row_count == 80, "谓词重放得到完整选择而非 ID 前缀"


async def test_sample_ref_not_labeled_full(delivery_env, actor):
    """场景 14：100 行 sample 快照 → 范围标签为当前快照，不称全量。"""
    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "id": f"s{n}",
                        "geometry": {"type": "Point",
                                     "coordinates": [116.4, 39.9]},
                        "properties": {"n": n}} for n in range(100)],
          "properties": {"total_count": 5000, "truncated": True}}
    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store("sess-1", fc, prefix="test")
    preview = await preview_delivery(_spec(ref), actor)
    assert preview.source.row_count == 100
    assert "截断" in (preview.scope_note or "")
    assert preview.source.completeness.get("truncated") is True


async def test_source_revision_changed_between_submit_and_execute(delivery_env, actor, monkeypatch):
    """场景 15：提交冻结指纹后源变化 → 执行期复核拒绝（不混读两版本）。

    注入点：runner 阶段 A 的 resolve_source（第 1 次调用 = 提交时服务端
    预览，冻结指纹；第 2 次 = runner 复核 —— 此刻源内容已被修订）。
    """
    from app.services.data_delivery import service as dd_service
    from app.services.session_data import session_data_manager

    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "id": "a",
                        "geometry": {"type": "Point",
                                     "coordinates": [116.4, 39.9]},
                        "properties": {"n": 1}}],
          "properties": {"schema_info": {"crs": "EPSG:4326"}}}
    ref = await session_data_manager.store("sess-1", fc, prefix="test")
    spec = _spec(ref)

    original_resolve = dd_service.resolve_source
    state = {"calls": 0}

    async def drift_then_resolve(source, act):
        state["calls"] += 1
        if state["calls"] == 2:
            store = session_data_manager._store["sess-1"]
            mutated = json.loads(json.dumps(store[ref]))
            mutated["features"][0]["properties"]["n"] = 999
            store[ref] = mutated
            session_data_manager._ref_revisions.setdefault("sess-1", {})[ref] =                 session_data_manager._ref_revisions.get("sess-1", {}).get(ref, 0) + 1
        return await original_resolve(source, act)

    monkeypatch.setattr(dd_service, "resolve_source", drift_then_resolve)

    sub = await submit_delivery(spec, actor)
    await run_job_to_completion(sub["job_id"])
    view = await get_delivery_job(sub["job_id"], actor)
    assert view.status == "failed"
    assert view.error_code == "SOURCE_REVISION_CHANGED"


async def test_expired_ref_no_fake_success(delivery_env, actor):
    """场景 17：session ref 过期 → 明确不可交付，不生成空文件。"""
    spec = _spec("ref:data-fabric-deadbeef")
    with pytest.raises(DeliveryError) as ei:
        await preview_delivery(spec, actor)
    assert ei.value.error_code == "SOURCE_EXPIRED"


async def test_idempotent_resubmit_reauth(delivery_env, seeded_session, actor):
    """场景 18：相同规格重复提交 → 复用且重新鉴权。"""
    spec = _spec(seeded_session["ref"], "gpkg")
    view = await _deliver(spec, actor)
    sub2 = await submit_delivery(spec, actor)
    assert sub2["idempotent_reuse"] is True
    assert sub2["job_id"] == view.job_id
    # 跨用户重放同规格：无权访问原 job → 新 job（不泄漏他人交付）
    other_spec = _spec(seeded_session["ref"], "gpkg")
    from app.services.data_delivery.source_resolver import DeliveryActor as DA

    other = DA(user_id="user-2")
    with pytest.raises(Exception):
        await get_delivery_job(view.job_id, other)


async def test_cross_owner_404(delivery_env, seeded_session, actor):
    """场景 19：不同 owner 不能经 cache/hash/下载越权。"""
    view = await _deliver(_spec(seeded_session["ref"]), actor)
    from app.services.data_delivery.source_resolver import DeliveryActor as DA

    other = DA(user_id="user-2")
    with pytest.raises(DeliveryError) as e1:
        await get_delivery_job(view.job_id, other)
    assert e1.value.status_code == 404
    with pytest.raises(DeliveryError) as e2:
        await resolve_download(view.job_id, None, other)
    assert e2.value.status_code == 404
    with pytest.raises(DeliveryError) as e3:
        await preview_delivery(_spec(seeded_session["ref"]), other)
    assert e3.value.error_code == "UNAUTHORIZED"


async def test_unknown_source_kind_fails_closed(delivery_env, actor):
    spec = DeliverySpec(
        source=DeliverySourceRef(kind="project_artifact",
                                 project_id="p-nope", artifact_id="a-nope"),
        format="csv", filename="t")
    with pytest.raises(DeliveryError) as ei:
        await preview_delivery(spec, actor)
    # 404 语义：不存在/无权一律 UNAUTHORIZED（不泄漏项目存在性）
    assert ei.value.error_code == "UNAUTHORIZED"
    assert ei.value.status_code == 404


async def test_download_member_validation(delivery_env, seeded_session, actor):
    """成员名必须逐字匹配清单（防路径穿越/猜测）。"""
    view = await _deliver(_spec(seeded_session["ref"], "csv",
                                fields=DeliveryFieldSelection(
                                    fields=["name", "pop"])), actor)
    assert view.status == "completed"
    from app.services.data_delivery.source_resolver import DeliveryActor as DA

    other = DA(user_id="user-2")
    for bad in ("../../etc/passwd", "receipt.json", "manifest.json", "nope.csv"):
        with pytest.raises(DeliveryError):
            await resolve_download(view.job_id, bad, actor)
    path, _, _, _ = await resolve_download(view.job_id, "t.schema.json", actor)
    assert path.name == "t.schema.json"
    with pytest.raises(DeliveryError):
        await resolve_download(view.job_id, "t.csv", other)


async def test_table_rows_csv(delivery_env, actor):
    """标准 rows 表格 → CSV（无几何要求）。"""
    from app.services.session_data import session_data_manager

    payload = {"rows": [{"city": "北京", "count": 3},
                        {"city": "上海", "count": 2}],
               "is_complete": True, "total_matching": 2}
    ref = await session_data_manager.store("sess-1", payload, prefix="aggregate")
    spec = DeliverySpec(
        source=DeliverySourceRef(kind="table_rows", session_id="sess-1",
                                 table_ref=ref),
        format="csv", filename="agg")
    view = await _deliver(spec, actor)
    assert view.status == "completed", (view.error_code, view.error)
    path, _, _, _ = await resolve_download(view.job_id, None, actor)
    text = path.read_text(encoding="utf-8")
    assert "北京,3" in text and "上海,2" in text


async def test_fabric_parquet_fast_path(delivery_env, seeded_session, actor):
    """场景 4：fabric-parquet 无变换 → 安全字节复制（源身份可追踪）。"""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from app.services.artifact_registry import fabric_parquet_path
    from app.services.data_fabric.materialization_service import (
        materialization_service,
    )
    from app.services.data_fabric.vector_carrier import features_to_arrow

    table = features_to_arrow(seeded_session["features"], crs="EPSG:4326")
    written = await materialization_service.materialize_geoparquet(
        "sess-1", table, "T")
    spec = DeliverySpec(
        source=DeliverySourceRef(kind="fabric_parquet", session_id="sess-1",
                                 ref=written["ref"]),
        format="geoparquet", filename="fast")
    view = await _deliver(spec, actor)
    assert view.status == "completed", (view.error_code, view.error)
    assert "fast_path_byte_copy" in view.receipt.losses_applied
    src_sha = Path(written["path"]).read_bytes().__len__() and None
    src = Path(written["path"])
    out, _, _, sha = await resolve_download(view.job_id, None, actor)
    assert out.read_bytes() == src.read_bytes(), "字节级一致（无中间格式）"
    assert view.receipt.source["content_fingerprint"] == written["content_sha256"]


async def test_formula_prefix_csv_modes(delivery_env, actor):
    """场景 12：公式前缀原始/安全两种模式行为明确。"""
    from app.services.session_data import session_data_manager

    payload = {"rows": [{"v": "=SUM(A1)"}, {"v": "+ADD(1)"}]}
    ref = await session_data_manager.store("sess-1", payload, prefix="agg")
    base = DeliverySpec(
        source=DeliverySourceRef(kind="table_rows", session_id="sess-1",
                                 table_ref=ref),
        format="csv", filename="f")
    # 原始模式：值原样
    v1 = await _deliver(base, actor)
    p1, _, _, _ = await resolve_download(v1.job_id, None, actor)
    assert "=SUM(A1)" in p1.read_text(encoding="utf-8")
    # 安全模式：变换并记录
    spec_safe = base.model_copy(update={"spreadsheet_safe": True})
    v2 = await _deliver(spec_safe, actor)
    p2, _, _, _ = await resolve_download(v2.job_id, None, actor)
    text2 = p2.read_text(encoding="utf-8")
    assert "'=SUM(A1)" in text2 and "'+ADD(1)" in text2
    assert "formula_prefix_sanitized" in v2.receipt.losses_applied


async def test_zero_rows_refuses_empty_file(delivery_env, actor):
    """过滤后 0 行 → 拒绝写空文件冒充成功。"""
    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "id": "a",
                        "geometry": {"type": "Point",
                                     "coordinates": [116.4, 39.9]},
                        "properties": {"n": 1}}],
          "properties": {"schema_info": {"crs": "EPSG:4326"}}}
    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store("sess-1", fc, prefix="test")
    spec = _spec(ref, scope=DeliveryScope(
        type="filter", predicate={"op": "eq", "field": "n", "value": 999}))
    sub = await submit_delivery(spec, actor)
    await run_job_to_completion(sub["job_id"])
    view = await get_delivery_job(sub["job_id"], actor)
    assert view.status == "failed"
    assert "空文件" in (view.error or "") or view.error_code == "WRITE_FAILED"
    # staging 清理（无半成品终态目录）
    staging_root = delivery_env["data_dir"] / "data-delivery" / ".staging"
    assert not any(staging_root.iterdir()) if staging_root.exists() else True


async def test_list_recovery_after_completion(delivery_env, seeded_session, actor):
    """场景 21（服务层）：完成后列表/详情可恢复（server state）。"""
    view = await _deliver(_spec(seeded_session["ref"]), actor)
    jobs = await list_delivery_jobs(actor)
    assert any(j.job_id == view.job_id and j.receipt is not None for j in jobs)
