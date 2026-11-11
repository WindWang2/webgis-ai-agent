"""G18 — 独立评审修复回归（P1/P2 修复的证据锁）。

- P1-1：ids 范围在 parquet 源上不得整表物化（有界样本读取）；
- P1-2：回执 losses_applied 与预览声明一一对应（含已知 CRS 的嵌套值）；
- P1-3：交付 job 不落 dispatch_spec（无 Celery task 可重试入队）；
- P2：输出字节硬帽真实生效；
- P3：durable 三态可区分（published/oversized/failed）。
"""
from __future__ import annotations

import json

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
    preview_delivery,
    submit_delivery,
)
from tests.delivery.conftest import run_job_to_completion


async def _deliver(spec, actor):
    preview = await preview_delivery(spec, actor)
    assert not preview.blocking_issues, [b.message for b in preview.blocking_issues]
    sub = await submit_delivery(spec, actor,
                                acknowledged_losses=preview.required_acknowledgments)
    await run_job_to_completion(sub["job_id"])
    return await get_delivery_job(sub["job_id"], actor)


async def _parquet_ref(session_id: str, feats, title: str = "T") -> str:
    from app.services.data_fabric.materialization_service import (
        materialization_service,
    )
    from app.services.data_fabric.vector_carrier import features_to_arrow

    table = features_to_arrow(feats, crs="EPSG:4326")
    written = await materialization_service.materialize_geoparquet(session_id, table, title)
    return written["ref"]


def _feats(n: int = 10):
    # _wfid：parquet 载体不回传顶层 feature.id（arrow 列式），稳定 ID 以
    # 属性字段承载（id_field 自动探测路径）。
    return [{"type": "Feature", "id": f"i{k}",
             "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
             "properties": {"n": k, "_wfid": f"i{k}"}} for k in range(n)]


async def test_ids_scope_parquet_bounded_read(delivery_env, actor, monkeypatch):
    """P1-1：ids 范围只读有界样本 —— features() 整表读取被禁用后仍可交付。"""
    from app.services.data_delivery.source_resolver import SourceSnapshot

    def _forbidden(self):
        raise AssertionError("ids 范围不得整表物化（评审 P1-1）")

    monkeypatch.setattr(SourceSnapshot, "features", _forbidden)

    ref = await _parquet_ref("sess-1", _feats())
    spec = DeliverySpec(
        source=DeliverySourceRef(kind="fabric_parquet", session_id="sess-1",
                                 ref=ref),
        scope=DeliveryScope(type="ids", ids=["i1", "i2", "i3"]),
        format="geojson", filename="bounded")
    view = await _deliver(spec, actor)
    assert view.status == "completed", (view.error_code, view.error)
    assert view.receipt.row_count == 3


async def test_receipt_reports_declared_losses_with_known_crs(delivery_env, actor):
    """P1-2：已知 CRS + 嵌套值 CSV —— 预览声明与回执应用一一对应。"""
    from app.services.session_data import session_data_manager

    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "id": "x",
                        "geometry": {"type": "Point",
                                     "coordinates": [116.4, 39.9]},
                        "properties": {"name": "北京", "nested": {"k": 1}}}],
          "properties": {"schema_info": {"crs": "EPSG:4326"}}}
    ref = await session_data_manager.store("sess-1", fc, prefix="test")
    spec = DeliverySpec(
        source=DeliverySourceRef(kind="session_ref", session_id="sess-1", ref=ref),
        format="csv", filename="loss-correspondence")
    view = await _deliver(spec, actor)
    assert view.status == "completed"
    assert "nested_value_json_encoded" in view.receipt.losses_applied, \
        view.receipt.losses_applied
    # 反读证实：嵌套值确实被 JSON 编码
    from app.services.data_delivery import resolve_download

    path, _, _, _ = await resolve_download(view.job_id, None, actor)
    assert '""k"": 1' in path.read_text(encoding="utf-8"), "嵌套值 JSON 编码（CSV 引号转义）"


async def test_receipt_reports_shapefile_type_losses(delivery_env, actor):
    """P1-2：Shapefile bool/大整数降级如实进回执。"""
    from app.services.session_data import session_data_manager

    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "id": "x",
                        "geometry": {"type": "Point",
                                     "coordinates": [116.4, 39.9]},
                        "properties": {"ok": True, "big": 9876543210}}],
          "properties": {"schema_info": {"crs": "EPSG:4326"}}}
    ref = await session_data_manager.store("sess-1", fc, prefix="test")
    spec = DeliverySpec(
        source=DeliverySourceRef(kind="session_ref", session_id="sess-1", ref=ref),
        format="shapefile_zip", filename="shp-losses")
    view = await _deliver(spec, actor)
    assert view.status == "completed"
    codes = view.receipt.losses_applied
    assert "bool_to_string" in codes, codes
    assert "int64_downgrade" in codes, codes


async def test_delivery_job_has_no_dispatch_spec(delivery_env, seeded_session, actor):
    """P1-3：job 行不落 dispatch_spec（进程内执行体，无 Celery task）。"""
    from app.core.database import SessionLocal
    from app.services.jobs.store import DurableJobStore

    from app.services.data_delivery import submit_delivery as submit

    sub = await submit(_spec_for(seeded_session["ref"]), actor)
    with SessionLocal() as db:
        record = DurableJobStore.get_sync(db, sub["job_id"])
        assert record.dispatch_spec is None
        assert record.max_retries == 0


def _spec_for(ref: str):
    from app.schemas.data_delivery_schema import (
        DeliveryFieldSelection,
        DeliverySourceRef,
        DeliverySpec,
    )

    return DeliverySpec(
        source=DeliverySourceRef(kind="session_ref", session_id="sess-1", ref=ref),
        format="geojson", filename="t")


async def test_output_byte_budget_enforced(delivery_env, actor, monkeypatch):
    """P2：输出字节硬帽真实生效（preview 阻断 + 执行拒绝）。"""
    from app.services.data_delivery import contracts as dl_contracts
    from app.services.session_data import session_data_manager

    monkeypatch.setattr(dl_contracts, "MAX_OUTPUT_BYTES", 512)
    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "id": f"i{k}",
                        "geometry": {"type": "Point",
                                     "coordinates": [116.4, 39.9]},
                        "properties": {"n": k}} for k in range(20)],
          "properties": {"schema_info": {"crs": "EPSG:4326"}}}
    ref = await session_data_manager.store("sess-1", fc, prefix="test")
    spec = DeliverySpec(
        source=DeliverySourceRef(kind="session_ref", session_id="sess-1", ref=ref),
        format="geojson", filename="big")
    # 执行期：writer 主输出超过硬帽 → typed 失败
    sub = await submit_delivery(spec, actor)
    await run_job_to_completion(sub["job_id"])
    view = await get_delivery_job(sub["job_id"], actor)
    assert view.status == "failed"
    assert view.error_code == "OUTPUT_BUDGET_EXCEEDED"


async def test_durable_failure_state_distinct(delivery_env, seeded_session, actor,
                                              monkeypatch):
    """P3：DataObject 发布异常 → durable='failed'（不与 oversized 混淆）。"""
    from app.services.lakehouse import data_object as data_object_mod

    def boom(**kwargs):
        raise RuntimeError("simulated blob store failure")

    monkeypatch.setattr(data_object_mod, "publish_data_object", boom)
    spec = DeliverySpec(
        source=DeliverySourceRef(kind="session_ref", session_id="sess-1",
                                 ref=seeded_session["ref"]),
        format="geojson", filename="durable-fail")
    view = await _deliver(spec, actor)
    assert view.status == "completed", (view.error_code, view.error)
    assert view.receipt.durable == "failed"
    # 交付本体不受 durable 失败影响（可下载）
    from app.services.data_delivery import resolve_download

    path, _, _, _ = await resolve_download(view.job_id, None, actor)
    assert path.is_file()


async def test_fast_path_copy_mode_not_loss(delivery_env, actor):
    """P3：fast_path 字节复制标记进 copy_mode，不污染损失词汇表。"""
    ref = await _parquet_ref("sess-1", _feats(3))
    spec = DeliverySpec(
        source=DeliverySourceRef(kind="fabric_parquet", session_id="sess-1",
                                 ref=ref),
        format="geoparquet", filename="fast")
    view = await _deliver(spec, actor)
    assert view.status == "completed"
    assert "fast_path_byte_copy" not in view.receipt.losses_applied
    assert view.receipt.timings.get("fast_path") is not None or \
        view.receipt.copy_mode == "byte_copy"


async def test_field_name_codes_no_duplicate_or_mislabel(delivery_env, actor):
    """NEW-1：字段名损失码单点来源 —— 无重复、不把自动截断误标为显式映射。"""
    from app.services.session_data import session_data_manager

    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "id": "x",
                        "geometry": {"type": "Point",
                                     "coordinates": [116.4, 39.9]},
                        "properties": {"population_total": 1,
                                       "population_rate": 2}}],
          "properties": {"schema_info": {"crs": "EPSG:4326"}}}
    ref = await session_data_manager.store("sess-1", fc, prefix="test")

    # 自动映射（无 field_map）：仅 field_name_truncated，不得出现 mapped
    view_auto = await _deliver(DeliverySpec(
        source=DeliverySourceRef(kind="session_ref", session_id="sess-1",
                                 ref=ref),
        format="shapefile_zip", filename="auto-map"), actor)
    codes = view_auto.receipt.losses_applied
    assert codes.count("field_name_truncated") == 1, codes
    assert "field_name_mapped" not in codes, codes
    assert len(codes) == len(set(codes)), f"回执损失码重复: {codes}"

    # 显式映射：仅 field_name_mapped
    view_explicit = await _deliver(DeliverySpec(
        source=DeliverySourceRef(kind="session_ref", session_id="sess-1",
                                 ref=ref),
        fields=DeliveryFieldSelection(
            field_map={"population_total": "pop_total",
                       "population_rate": "pop_rate"}),
        format="shapefile_zip", filename="explicit-map"), actor)
    codes2 = view_explicit.receipt.losses_applied
    assert codes2.count("field_name_mapped") == 1, codes2
    assert "field_name_truncated" not in codes2, codes2


async def test_coordinate_precision_blocked_for_binary_formats(delivery_env,
                                                              seeded_session, actor):
    """NEW-4：精度选项仅 GeoJSON/CSV 兑现 —— 其它格式预览阻断（不静默忽略）。"""
    from app.services.data_delivery import preview_delivery

    spec = DeliverySpec(
        source=DeliverySourceRef(kind="session_ref", session_id="sess-1",
                                 ref=seeded_session["ref"]),
        crs=DeliveryCrsSpec(coordinate_precision=6),
        format="geoparquet", filename="prec-gpq")
    preview = await preview_delivery(spec, actor)
    assert any(b.code == "INVALID_SPEC" for b in preview.blocking_issues)
