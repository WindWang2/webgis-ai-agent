"""G18 WP12 — 取消/故障/恢复终态与故障反验（fault-injection oracle）。

故障注入只存在于测试设施（monkeypatch writer/路由/registry），证明真实
产品路径被验证而非自洽 mock。
"""
from __future__ import annotations

import json

import pytest

from app.schemas.data_delivery_schema import DeliveryScope, DeliverySourceRef, DeliverySpec
from app.services.data_delivery import (
    DeliveryError,
    get_delivery_job,
    resolve_download,
    submit_delivery,
)
from tests.delivery.conftest import run_job_to_completion


def _spec(ref: str, fmt: str = "geojson", **kw) -> DeliverySpec:
    return DeliverySpec(
        source=DeliverySourceRef(kind="session_ref", session_id="sess-1", ref=ref),
        format=fmt, filename="t", **kw)


async def _deliver(spec, actor):
    from app.services.data_delivery import preview_delivery

    preview = await preview_delivery(spec, actor)
    sub = await submit_delivery(spec, actor,
                                acknowledged_losses=preview.required_acknowledgments)
    await run_job_to_completion(sub["job_id"])
    return await get_delivery_job(sub["job_id"], actor)


async def test_cancel_mid_run_no_half_file(delivery_env, actor, monkeypatch):
    """场景 20：执行中取消 → 无 completed、无半成品可下载、staging 清理。"""
    from app.services.session_data import session_data_manager

    feats = [{"type": "Feature", "id": f"i{n}",
              "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
              "properties": {"n": n}} for n in range(50)]
    fc = {"type": "FeatureCollection", "features": feats,
          "properties": {"schema_info": {"crs": "EPSG:4326"}}}
    ref = await session_data_manager.store("sess-1", fc, prefix="test")

    # 取消钩子：第一批写入后立即取消（真实协作取消路径）
    from app.services.data_delivery import writers as writers_mod

    original = writers_mod.GeoJSONWriter.write_batch

    def cancelling_write_batch(self, features):
        out = original(self, features)
        from app.lib.cancellation import OperationCancelled
        from app.services.jobs import registry as cancellation_registry

        runners = [k for k in cancellation_registry._tokens]
        for key in runners:
            cancellation_registry.cancel(key, "test cancel")
        raise OperationCancelled("cancelled in test after first batch")

    monkeypatch.setattr(writers_mod.GeoJSONWriter, "write_batch",
                        cancelling_write_batch)

    sub = await submit_delivery(_spec(ref), actor)
    await run_job_to_completion(sub["job_id"])
    view = await get_delivery_job(sub["job_id"], actor)
    assert view.status == "cancelled", view.status
    assert view.receipt is None
    with pytest.raises(DeliveryError):
        await resolve_download(sub["job_id"], None, actor)
    staging = delivery_env["data_dir"] / "data-delivery" / ".staging"
    finals = delivery_env["data_dir"] / "data-delivery"
    leftovers = [p for p in finals.iterdir() if p.name != ".staging"] \
        if finals.exists() else []
    assert not leftovers, f"半成品终态目录: {leftovers}"


async def test_writer_crash_fails_clean(delivery_env, seeded_session, actor, monkeypatch):
    """writer 崩溃 → failed + 清理，不误报成功。"""
    from app.services.data_delivery import writers as writers_mod

    def boom(self, features):
        raise RuntimeError("simulated disk failure")

    monkeypatch.setattr(writers_mod.GeoJSONWriter, "write_batch", boom)
    view = await _deliver(_spec(seeded_session["ref"]), actor)
    assert view.status == "failed"
    assert view.error_code == "WRITE_FAILED"
    with pytest.raises(DeliveryError):
        await resolve_download(view.job_id, None, actor)


async def test_verify_mismatch_detected(delivery_env, seeded_session, actor, monkeypatch):
    """反读 oracle：writer 少写一半（真实半成品）→ VERIFY_FAILED。

    注入点：writer close 后截断输出文件（模拟半写盘），独立读取路径
    反读行数与 receipt 不符 —— 产品路径的反读校验必须抓住它。
    """
    from app.services.data_delivery import writers as writers_mod

    original_close = writers_mod.GeoJSONWriter.close

    def truncating_close(self):
        result = original_close(self)
        text = self.path.read_text(encoding="utf-8")
        # 去掉最后一个 feature（少写一半的真实故障）
        last = text.rfind('{"type":"Feature"')
        if last > 0:
            self.path.write_text(text[:last].rstrip(",") + "]}", encoding="utf-8")
        return result

    monkeypatch.setattr(writers_mod.GeoJSONWriter, "close", truncating_close)
    view = await _deliver(_spec(seeded_session["ref"]), actor)
    assert view.status == "failed"
    assert view.error_code == "VERIFY_FAILED"


async def test_download_owner_check_injection_fails(delivery_env, seeded_session, actor, monkeypatch):
    """故障反验：跳过 owner 检查的下载应使越权测试失败（注入后确实能检出）。"""
    view = await _deliver(_spec(seeded_session["ref"]), actor)
    assert view.status == "completed"
    from app.services.data_delivery.source_resolver import DeliveryActor

    other = DeliveryActor(user_id="user-2")
    # 基线：越权被拒
    with pytest.raises(DeliveryError):
        await resolve_download(view.job_id, None, other)
    # 注入：绕过授权 —— 证明真实路径上存在该检查（移除它将允许越权）
    from app.services.data_delivery import service as service_mod

    monkeypatch.setattr(service_mod, "_job_authorize", lambda record, a: None)
    path, _, _, _ = await resolve_download(view.job_id, None, other)
    assert path.is_file(), "注入绕过后可读 —— 证明产品路径依赖 _job_authorize"


async def test_completed_survives_simulated_restart(delivery_env, seeded_session, actor):
    """场景 21：完成后清空进程内状态（模拟重启）→ 状态/下载仍可恢复。"""
    view = await _deliver(_spec(seeded_session["ref"], "gpkg"), actor)
    assert view.status == "completed"
    from app.services.data_delivery import service as service_mod

    service_mod._ACTIVE_RUNNERS.clear()
    from app.services.jobs import registry as cancellation_registry

    cancellation_registry.clear()
    view2 = await get_delivery_job(view.job_id, actor)
    assert view2.status == "completed"
    assert view2.receipt is not None and view2.receipt.row_count == 3
    path, mime, name, sha = await resolve_download(view.job_id, None, actor)
    assert path.is_file() and sha


async def test_row_cap_enforced(delivery_env, actor, monkeypatch):
    """行数硬帽：超限拒绝（不写一半成功）。"""
    from app.services.data_delivery import contracts as dl_contracts

    monkeypatch.setattr(dl_contracts, "MAX_ROWS_PER_DELIVERY", 5)
    from app.services.session_data import session_data_manager

    feats = [{"type": "Feature", "id": f"i{n}",
              "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
              "properties": {"n": n}} for n in range(20)]
    fc = {"type": "FeatureCollection", "features": feats,
          "properties": {"schema_info": {"crs": "EPSG:4326"}}}
    ref = await session_data_manager.store("sess-1", fc, prefix="test")
    from app.services.data_delivery import preview_delivery

    preview = await preview_delivery(_spec(ref), actor)
    assert any(b.code == "ROW_LIMIT_EXCEEDED" for b in preview.blocking_issues)
    with pytest.raises(DeliveryError) as ei:
        await submit_delivery(_spec(ref), actor)
    assert ei.value.error_code == "ROW_LIMIT_EXCEEDED"


async def test_selection_epoch_change_blocked(delivery_env, actor, monkeypatch):
    """场景 27：提交冻结后源/选择内容变化 → 复核拒绝（不悄悄用新数据）。

    注入点：runner 阶段 A 的 resolve_source 第 2 次调用时修订源 payload
    （提交时服务端预览已完成冻结）。
    """
    from app.services.data_delivery import service as dd_service
    from app.services.session_data import session_data_manager

    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "id": f"i{n}",
                        "geometry": {"type": "Point",
                                     "coordinates": [116.4, 39.9]},
                        "properties": {"grp": "A", "n": n}}
                       for n in range(5)],
          "properties": {"schema_info": {"crs": "EPSG:4326"}}}
    ref = await session_data_manager.store("sess-1", fc, prefix="test")
    spec = _spec(ref, scope=DeliveryScope(
        type="filter", predicate={"op": "eq", "field": "grp", "value": "A"}))

    original_resolve = dd_service.resolve_source
    state = {"calls": 0}

    async def drift_then_resolve(source, act):
        state["calls"] += 1
        if state["calls"] == 2:
            store = session_data_manager._store["sess-1"]
            mutated = json.loads(json.dumps(store[ref]))
            for f in mutated["features"]:
                f["properties"]["grp"] = "B"
            store[ref] = mutated
            session_data_manager._ref_revisions.setdefault("sess-1", {})[ref] = 1
        return await original_resolve(source, act)

    monkeypatch.setattr(dd_service, "resolve_source", drift_then_resolve)

    sub = await submit_delivery(spec, actor)
    await run_job_to_completion(sub["job_id"])
    view = await get_delivery_job(sub["job_id"], actor)
    assert view.status == "failed"
    assert view.error_code == "SOURCE_REVISION_CHANGED"
