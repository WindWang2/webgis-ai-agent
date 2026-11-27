"""G18 — P3 评审回归：data_delivery 域错误边界/契约/授权回归锁。

覆盖评审清单 P3-app_services_data_delivery-00 中可复现的缺陷：

- loss.py:85   显式 field_map 输出名与未映射字段同名 → 预览即阻断；
- service.py:1042 owner_token 用常量时间 + digest 语义比较（owner_token_matches）；
- service.py:1207 列表不再每行重开会话/重跑授权（N+1）；
- source_resolver.py:120/129 非 dict feature/row → typed 拒绝而非 AttributeError；
- source_resolver.py:550 parquet 快照 fields.sample_values 统一为 []；
- store.py:76  atomic_publish 目标已存在 → typed PUBLISH_FAILED；
- writers.py:109 写失败路径关闭 writer 文件句柄；
- writers.py:194 _to_frame 异常（如非法 CRS）→ typed WriterError。
"""
from __future__ import annotations

import pytest

from app.schemas.data_delivery_schema import (
    DeliveryCrsSpec,
    DeliveryFieldSelection,
    DeliveryScope,
    DeliverySourceRef,
    DeliverySpec,
)
from app.services.data_delivery import (
    get_delivery_job,
    submit_delivery,
)
from tests.delivery.conftest import run_job_to_completion


def _snapshot(fields, crs="EPSG:4326"):
    from app.services.data_delivery.source_resolver import SourceSnapshot

    return SourceSnapshot(
        kind="session_ref", label="t", is_table=False, row_count=1,
        content_fingerprint="fp", crs=crs, fields=fields,
        geometry={"types": ["Point"], "has_z": False,
                  "null_or_empty_count": 0, "sampled_rows": 1},
    )


# ── loss.py:85 ───────────────────────────────────────────────────────────

def test_field_map_target_colliding_with_unmapped_field_blocks_preview():
    """显式映射目标撞未映射字段名 → 预览阻断（此前提交期才 INVALID_SPEC）。"""
    from app.services.data_delivery.loss import LossError, analyze_losses

    snap = _snapshot([
        {"name": "population_total", "inferred_type": "int", "sample_values": []},
        {"name": "b", "inferred_type": "int", "sample_values": []},
    ])
    with pytest.raises(LossError) as exc:
        analyze_losses(
            snap, "shapefile_zip",
            DeliveryFieldSelection(
                fields=["population_total", "b"],
                field_map={"population_total": "b"}),
            DeliveryCrsSpec(), DeliveryScope())
    assert exc.value.error_code == "LOSS_ACK_REQUIRED"
    assert "未映射字段同名" in exc.value.reason


# ── service.py:1042 ──────────────────────────────────────────────────────

def test_job_authorize_matches_owner_token_digest_constant_time():
    """owner_token 行存 digest、调用方持原文 —— 经常量时间比较应放行。"""
    from app.core.owner_tokens import OwnerTokenDigest, owner_token_digest
    from app.services.data_delivery.service import DeliveryError, _job_authorize
    from app.services.data_delivery.source_resolver import DeliveryActor

    class _Record:
        creator_id = None
        session_id = None
        project_id = None
        owner_token = OwnerTokenDigest(owner_token_digest("secret-token"))

    _job_authorize(_Record(), DeliveryActor(owner_token="secret-token"))
    with pytest.raises(DeliveryError):
        _job_authorize(_Record(), DeliveryActor(owner_token="wrong-token"))


# ── service.py:1207 ──────────────────────────────────────────────────────

def test_list_jobs_does_not_open_per_row_sessions(delivery_env, monkeypatch):
    """列表复用已加载行：N 行只开 1 次会话（此前 1+N 次 + 重复授权）。"""
    import app.core.database as db_mod
    from app.models.db_model import AnalysisTask
    from app.services.data_delivery.service import _list_delivery_jobs_sync
    from app.services.data_delivery.source_resolver import DeliveryActor

    factory = delivery_env["sync_factory"]
    with factory() as db:
        db.add_all([
            AnalysisTask(task_type="data_delivery", parameters={},
                         creator_id="user-1"),
            AnalysisTask(task_type="data_delivery", parameters={},
                         creator_id="user-1"),
        ])
        db.commit()

    calls = []

    def counting_factory(*args, **kwargs):
        calls.append(1)
        return factory(*args, **kwargs)

    monkeypatch.setattr(db_mod, "SessionLocal", counting_factory)
    views = _list_delivery_jobs_sync(
        DeliveryActor(user_id="user-1"), None, 50)
    assert len(views) == 2
    assert len(calls) == 1, f"列表每行重开会话（N+1）: {len(calls)} 次"


# ── source_resolver.py:120/129 ───────────────────────────────────────────

def test_fc_payload_non_dict_feature_typed_rejection():
    from app.services.data_delivery.source_resolver import (
        SourceResolutionError,
        _snapshot_from_payload,
    )

    payload = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": None, "properties": {}},
        "not-a-feature",
    ]}
    with pytest.raises(SourceResolutionError) as exc:
        _snapshot_from_payload("t", payload, "ref")
    assert exc.value.error_code == "SOURCE_SHAPE_UNSUPPORTED"


def test_rows_payload_non_dict_beyond_eighth_row_typed_rejection():
    from app.services.data_delivery.source_resolver import (
        SourceResolutionError,
        _snapshot_from_payload,
    )

    payload = {"rows": [{"n": i} for i in range(8)] + ["not-a-row"]}
    with pytest.raises(SourceResolutionError) as exc:
        _snapshot_from_payload("t", payload, "ref")
    assert exc.value.error_code == "SOURCE_SHAPE_UNSUPPORTED"


# ── source_resolver.py:550 ───────────────────────────────────────────────

def test_parquet_snapshot_fields_sample_values_is_list(tmp_path):
    """同一 Snapshot 契约：parquet 字段 sample_values 与 payload 路径一致为 []。"""
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    path = tmp_path / "t.parquet"
    pq.write_table(pa.table({"n": [1, 2], "s": ["a", "b"]}), str(path))

    from app.services.data_delivery.source_resolver import _snapshot_from_parquet

    snap = _snapshot_from_parquet("ref", path)
    assert snap.fields
    assert all(f["sample_values"] == [] for f in snap.fields)


# ── store.py:76 ──────────────────────────────────────────────────────────

def test_atomic_publish_existing_target_is_typed_error(tmp_path, monkeypatch):
    """终态目录已存在且非空 → typed PUBLISH_FAILED（此前裸 OSError）。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    from app.services.data_delivery import store
    from app.services.data_delivery.service import DeliveryError

    delivery_id = store.new_delivery_id()
    first = store.staging_dir(delivery_id)
    (first / "a.txt").write_text("x", encoding="utf-8")
    assert store.atomic_publish(delivery_id).is_dir()

    second = store.staging_dir(delivery_id)
    (second / "b.txt").write_text("y", encoding="utf-8")
    with pytest.raises(DeliveryError) as exc:
        store.atomic_publish(delivery_id)
    assert exc.value.error_code == "PUBLISH_FAILED"


# ── writers.py:109（经 service 执行路径）─────────────────────────────────

async def test_write_failure_closes_writer_file_handle(delivery_env, actor,
                                                       monkeypatch):
    """write_batch 抛错时 writer 句柄立即关闭（不靠 GC / 不泄漏 fd）。"""
    from app.services.data_delivery import service as dd_service
    from app.services.data_delivery.writers import CSVWriter
    from app.services.session_data import session_data_manager

    instances = []

    class ExplodingCSVWriter(CSVWriter):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            instances.append(self)

        def write_batch(self, rows):
            raise RuntimeError("simulated write failure")

    monkeypatch.setattr(dd_service, "CSVWriter", ExplodingCSVWriter)
    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "id": "x",
                        "geometry": {"type": "Point",
                                     "coordinates": [116.4, 39.9]},
                        "properties": {"n": 1}}],
          "properties": {"schema_info": {"crs": "EPSG:4326"}}}
    ref = await session_data_manager.store("sess-1", fc, prefix="test")
    spec = DeliverySpec(
        source=DeliverySourceRef(kind="session_ref", session_id="sess-1",
                                 ref=ref),
        format="csv", filename="fd-leak")
    sub = await submit_delivery(spec, actor)
    await run_job_to_completion(sub["job_id"])
    view = await get_delivery_job(sub["job_id"], actor)
    assert view.status == "failed"
    assert instances, "CSV writer 未被使用"
    assert instances[0]._fh.closed, "失败路径 writer 句柄未关闭"


# ── writers.py:194 ───────────────────────────────────────────────────────

def test_pyogrio_conversion_error_is_typed_writer_error(tmp_path):
    """_to_frame 的转换异常（如非法 CRS）→ typed WriterError，而非裸 CRSError。"""
    pytest.importorskip("pyogrio")
    from pathlib import Path

    from app.services.data_delivery.writers import (
        PyogrioWriter,
        WriterContext,
        WriterError,
    )

    ctx = WriterContext(fmt="gpkg", out_dir=Path(tmp_path),
                        base_filename="t", fields=["n"],
                        crs_out="not-a-crs")
    writer = PyogrioWriter(ctx, driver="GPKG", layer_name="t")
    with pytest.raises(WriterError):
        writer.write_batch([{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
            "properties": {"n": 1},
        }])
