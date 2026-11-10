"""G19 独立评审修复回归（review → fix → rereview 证据）。

每一条对应 REVIEW.md 中的 P1 / 影响验收的 P2：
P1-1 OpenAPI 短名保持、P1-2 项目保存失败降级、P1-3 收据落盘闸门、
P1-4 datetime_part 时区、P2-5 幂等升级项目、P2-6 computed_rows、
P2-7 abort 下几何阻断豁免、P2-8 服务端取消端点、P2-10 owner 元数据、
P2-11 ring/manifest 不截断、P2-13 弱引用锁。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from app.services.data_preparation.field_compute import receipt as rc
from app.services.data_preparation.field_compute import service
from app.services.data_preparation.field_compute.contracts import (
    ComputeReceipt, ExecError,
)
from app.services.data_preparation.field_compute.interpreter import (
    EvalConfig, evaluate_rows,
)
from app.services.data_preparation.field_compute.parser import (
    CompileContext, compile_columns,
)
from app.services.dataset_semantics import reset_dataset_semantic_store
from app.services.session_data import session_data_manager as sdm


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path)
    reset_dataset_semantic_store()
    yield
    reset_dataset_semantic_store()


def _fc():
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.01, 30.0]},
         "properties": {"人口": 1000 + i * 100, "面积 平方千米": 10 + i,
                        "基期": 100, "现期": 120, "代码": "00123",
                        "时间": "2023-12-31 16:30:00", "名称": f"区{i}"}}
        for i in range(4)]}


def _payload(crs="EPSG:4326", crs_source="declared"):
    return {"geojson": _fc(), "crs": crs, "crs_source": crs_source}


async def _seed(sid, payload=None):
    return await sdm.store(sid, payload or _payload(), prefix="test")


async def _plan(sid, ref, columns, **kw):
    req = {"session_id": sid, "source_ref": ref, "columns": columns}
    req.update(kw)
    return (await service.create_plan(req))["plan"]


# ── P1-1 / P2-12：OpenAPI 既有短名与 $ref 零变化 ──────────────────────────

class TestOpenApiCompatibility:
    def test_existing_short_schema_names_preserved(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        d = json.loads((root / "tests/quality/snapshots/openapi.json").read_text(
            encoding="utf-8"))
        schemas = d["components"]["schemas"]
        for name in ("PlanOut", "PlanResponse", "PlanListResponse",
                     "PlanRequest", "PreviewResponse"):
            assert name in schemas, f"既有短名 {name} 被消歧改写"
        assert not any(k.startswith("app__schemas__data_preparation_schema__")
                       for k in schemas)
        for path in ("/api/v1/data-preparation/plan",
                     "/api/v1/data-preparation/plans"):
            op = (d["paths"][path].get("post") or d["paths"][path].get("get"))
            ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
            assert ref.startswith("#/components/schemas/Plan")
            assert "app__schemas__" not in ref
        assert all(k.startswith("FieldCompute") for k in schemas
                   if k.startswith("FieldCompute"))
        assert "FieldComputePlanOut" in schemas


# ── P1-4：datetime_part 使用表达式时区 ────────────────────────────────────

class TestTimezone:
    def test_plan_utc_but_formula_plus8_crosses_day(self):
        ctx = CompileContext(
            available_fields=("时间",), timezone="UTC",
            date_format="%Y-%m-%d %H:%M:%S")
        cols, _ = compile_columns([{
            "name": "月份",
            "expression": "datetime_part(parse_datetime({时间}, '%Y-%m-%d %H:%M:%S', '+08:00'), 'month')",
        }], ctx)
        cfg = EvalConfig(timezone="UTC", date_format="%Y-%m-%d %H:%M:%S")
        rows = [({"时间": "2023-12-31 16:30:00"}, None),
                ({"时间": "2024-01-01 00:30:00"}, None)]
        out = evaluate_rows(rows, cols, None, cfg)
        # 计划级 UTC 不得覆盖公式内 +08:00；跨 UTC 日期边界仍取 12 月 / 1 月。
        assert [r["月份"] for r in out.rows] == [12, 1]


# ── P1-2 / P1-3 / P2-5 / P2-6：发布顺序、持久化与统计 ─────────────────────

class TestPublishSequence:
    async def test_project_save_failure_downgrades_persistence(self, monkeypatch):
        sid = "sess-fc-rev-savefail"
        ref = await _seed(sid)
        plan = await _plan(sid, ref,
                           [{"name": "密度", "expression": "{人口} / {面积 平方千米}"}],
                           owner="alice")

        async def _fail(*_a, **_kw):
            return False
        monkeypatch.setattr(service, "save_to_project", _fail)
        receipt = await service.execute(plan.plan_id, sid, project_id="proj-fail")
        assert receipt.persistence == "session_ref"
        assert "PROJECT_SAVE_FAILED" in receipt.degraded
        assert await service.list_project_results(sid, "proj-fail") == []

    async def test_receipt_gate_failure_writes_nothing(self, monkeypatch):
        sid = "sess-fc-rev-gate"
        ref = await _seed(sid)
        plan = await _plan(sid, ref,
                           [{"name": "密度", "expression": "{人口} / {面积 平方千米}"}])
        fc_writes: list = []
        real_store = sdm.store

        async def _rec(*a, **kw):
            if kw.get("prefix") == "fc":
                fc_writes.append(a)
            return await real_store(*a, **kw)

        async def _no_receipt(*_a, **_kw):
            return False

        monkeypatch.setattr(sdm, "store", _rec)
        monkeypatch.setattr(rc, "append_receipt", _no_receipt)
        with pytest.raises(ExecError) as ei:
            await service.execute(plan.plan_id, sid, project_id="proj-gate")
        assert ei.value.code == "WRITE_FAILED"
        assert fc_writes == []                    # store 从未调用 → 无孤儿 ref
        assert await service.list_project_results(sid, "proj-gate") == []
        assert await rc.find_receipt(sid, plan.compute_fingerprint, ref,
                                     plan.anchor.content_hash) is None

    async def test_publish_probe_failure_writes_nothing(self, monkeypatch):
        sid = "sess-fc-rev-probe"
        ref = await _seed(sid)
        plan = await _plan(sid, ref,
                           [{"name": "密度", "expression": "{人口} / 1"}])
        fc_writes: list = []
        real_store = sdm.store

        async def _rec(*a, **kw):
            if kw.get("prefix") == "fc":
                fc_writes.append(a)
            return await real_store(*a, **kw)

        async def _not_writable(_sid):
            return False

        monkeypatch.setattr(sdm, "store", _rec)
        monkeypatch.setattr(rc, "probe_publish", _not_writable)
        with pytest.raises(ExecError) as ei:
            await service.execute(plan.plan_id, sid, project_id="proj-probe")
        assert ei.value.code == "WRITE_FAILED"
        assert fc_writes == []
        assert await service.list_project_results(sid, "proj-probe") == []

    async def test_final_receipt_failure_rolls_back_visible_result(
            self, monkeypatch):
        """复审 P1：最终已发布收据写入失败时不得留下可见半成品。"""
        sid = "sess-fc-rev-finalfail"
        ref = await _seed(sid)
        plan = await _plan(sid, ref,
                           [{"name": "密度", "expression": "{人口} / 1"}])
        written: list = []
        real_store = sdm.store

        async def _rec(*a, **kw):
            out = await real_store(*a, **kw)
            if kw.get("prefix") == "fc":
                written.append(out)
            return out

        real_append = rc.append_receipt

        async def _fail_published(session_id, receipt):
            if getattr(receipt, "published", False):
                return False
            return await real_append(session_id, receipt)

        monkeypatch.setattr(sdm, "store", _rec)
        monkeypatch.setattr(rc, "append_receipt", _fail_published)
        with pytest.raises(ExecError) as ei:
            await service.execute(plan.plan_id, sid, project_id="proj-finalfail")
        assert ei.value.code == "WRITE_FAILED"
        assert "回滚" in ei.value.detail
        # ① session ref 已撤销（不留下能被 get_result 读到的孤儿成果）
        assert len(written) == 1
        assert await sdm.get(sid, written[0]) is None
        # ② 项目清单已撤销（重开项目看不到）
        assert await service.list_project_results(sid, "proj-finalfail") == []
        # ③ 幂等查找不得命中「未发布」成果
        assert await rc.find_receipt(sid, plan.compute_fingerprint, ref,
                                     plan.anchor.content_hash) is None

    async def test_stable_key_write_failure_never_publishes_or_poisons(
            self, monkeypatch):
        """P1 根因：稳定键写失败时 ring 不得留下 published=True 的 final。

        复现复审描述的组合：草稿闸门通过 → store/登记/项目清单已写 →
        最终收据的稳定键写失败。此时必须回滚可见面，且 `find_receipt`
        不能命中任何「未发布」成果（否则下次幂等会返回已删除的 output_ref）。
        """
        sid = "sess-fc-rev-keyfail"
        ref = await _seed(sid)
        plan = await _plan(sid, ref,
                           [{"name": "密度", "expression": "{人口} / 1"}])
        real_write = rc._atomic_write_json

        def _fail(path, payload):
            if "by_key" in str(path):
                raise OSError("simulated disk full")
            return real_write(path, payload)

        monkeypatch.setattr(rc, "_atomic_write_json", _fail)
        with pytest.raises(ExecError) as ei:
            await service.execute(plan.plan_id, sid, project_id="proj-keyfail")
        assert ei.value.code == "WRITE_FAILED"
        assert "回滚" in ei.value.detail
        # 恢复真实写入后复查：不得留下已发布收据、项目清单条目
        monkeypatch.setattr(rc, "_atomic_write_json", real_write)
        assert await rc.find_receipt(sid, plan.compute_fingerprint, ref,
                                     plan.anchor.content_hash) is None
        assert await service.list_project_results(sid, "proj-keyfail") == []
        # 重试可正常产出新成果（回滚不留陈旧状态）
        again = await service.execute(plan.plan_id, sid)
        assert again.output_ref.startswith("ref:fc-")

    async def test_upgrade_clears_stale_project_save_failure(self, monkeypatch):
        """复审 P2：项目升级成功后不得残留 PROJECT_SAVE_FAILED。"""
        sid = "sess-fc-rev-upgrade-clean"
        ref = await _seed(sid)
        plan = await _plan(sid, ref,
                           [{"name": "密度", "expression": "{人口} / 1"}])
        real_save = service.save_to_project

        async def _fail(*_a, **_kw):
            return False
        monkeypatch.setattr(service, "save_to_project", _fail)
        first = await service.execute(plan.plan_id, sid, project_id="proj-up2")
        assert first.persistence == "session_ref"
        assert "PROJECT_SAVE_FAILED" in first.degraded

        monkeypatch.setattr(service, "save_to_project", real_save)
        second = await service.execute(plan.plan_id, sid, project_id="proj-up2")
        assert second.output_ref == first.output_ref
        assert second.persistence == "project_artifact"
        assert "PROJECT_SAVE_FAILED" not in second.degraded
        assert len(await service.list_project_results(sid, "proj-up2")) == 1

    async def test_ring_write_failure_removes_stable_key(self, monkeypatch):
        """ring 写失败时稳定键必须被删除（否则 fallback 幂等会命中未发布成果）。"""
        receipt = ComputeReceipt(
            plan_id="p", compute_fingerprint="fp-ring",
            input_ref="ref:in", output_ref="ref:out",
            input_content_hash="h-ring", output_content_hash="o",
            published=True)

        def _boom(_path, _records):
            raise OSError("simulated ring write failure")

        key = rc.stable_key("fp-ring", "ref:in", "h-ring")
        key_file = rc._stable_receipt_path("sess-fc-rev-ringfail", key)

        def _boom(_path, _records):
            # ring 失败前稳定键必须已写入 —— 证明「先写键、再写 ring」的顺序，
            # 否则本测试可能因为实现根本不写键而假通过。
            assert key_file.exists(), "ring 失败前应已写入稳定键"
            raise OSError("simulated ring write failure")

        monkeypatch.setattr(rc, "_append_sync", _boom)
        assert await rc.append_receipt("sess-fc-rev-ringfail", receipt) is False
        assert not key_file.exists()
        assert await rc.find_receipt(
            "sess-fc-rev-ringfail", "fp-ring", "ref:in", "h-ring") is None

    async def test_drop_published_removes_existing_stable_key(self):
        """drop_published 的成功删除路径（不是只测 mock 失败分支）。"""
        receipt = ComputeReceipt(
            plan_id="p", compute_fingerprint="fp-drop",
            input_ref="ref:in", output_ref="ref:out",
            input_content_hash="h-drop", output_content_hash="o",
            published=True)
        sid = "sess-fc-rev-drop"
        assert await rc.append_receipt(sid, receipt)
        path = rc._stable_receipt_path(
            sid, rc.stable_key("fp-drop", "ref:in", "h-drop"))
        assert path.exists()
        assert await rc.drop_published(sid, receipt) is True
        assert not path.exists()
        assert await rc.find_receipt(sid, "fp-drop", "ref:in", "h-drop") is None

    async def test_final_failure_reports_stable_key_residual(self, monkeypatch):
        """稳定键回滚失败时必须在错误 detail 如实列出残留码。"""
        sid = "sess-fc-rev-residual"
        ref = await _seed(sid)
        plan = await _plan(sid, ref,
                           [{"name": "密度", "expression": "{人口} / 1"}])

        async def _fail_published(_session_id, receipt):
            return not getattr(receipt, "published", False)

        async def _drop_fail(_session_id, _receipt):
            return False

        monkeypatch.setattr(rc, "append_receipt", _fail_published)
        monkeypatch.setattr(rc, "drop_published", _drop_fail)
        with pytest.raises(ExecError) as ei:
            await service.execute(plan.plan_id, sid)
        assert ei.value.code == "WRITE_FAILED"
        assert "STABLE_KEY_ROLLBACK_FAILED" in ei.value.detail

    async def test_upgrade_failure_marks_degraded(self, monkeypatch):
        """幂等命中后项目保存仍失败：必须保留 session_ref 并标 PROJECT_SAVE_FAILED。"""
        sid = "sess-fc-rev-upgrade-fail"
        ref = await _seed(sid)
        plan = await _plan(sid, ref,
                           [{"name": "密度", "expression": "{人口} / 1"}])
        first = await service.execute(plan.plan_id, sid)
        assert first.persistence == "session_ref"

        async def _fail(*_a, **_kw):
            return False

        monkeypatch.setattr(service, "save_to_project", _fail)
        second = await service.execute(plan.plan_id, sid, project_id="proj-up3")
        assert second.output_ref == first.output_ref
        assert second.persistence == "session_ref"
        assert "PROJECT_SAVE_FAILED" in second.degraded
        assert await service.list_project_results(sid, "proj-up3") == []
        # 降级必须回写台账：之后查询/重开也看到真实状态
        persisted = await rc.find_receipt(
            sid, plan.compute_fingerprint, ref, plan.anchor.content_hash)
        assert persisted is not None
        assert "PROJECT_SAVE_FAILED" in persisted.degraded

    async def test_idempotent_upgrade_from_session_to_project(self):
        sid = "sess-fc-rev-upgrade"
        ref = await _seed(sid)
        plan = await _plan(sid, ref,
                           [{"name": "密度", "expression": "{人口} / {面积 平方千米}"}])
        first = await service.execute(plan.plan_id, sid)
        assert first.persistence == "session_ref"
        second = await service.execute(plan.plan_id, sid, project_id="proj-up")
        assert second.output_ref == first.output_ref
        assert second.persistence == "project_artifact"
        items = await service.list_project_results(sid, "proj-up")
        assert len(items) == 1 and items[0]["output_ref"] == first.output_ref

    async def test_computed_rows_excludes_error_rows(self):
        sid = "sess-fc-rev-computed"
        payload = _payload()
        payload["geojson"]["features"][0]["properties"]["面积 平方千米"] = 0
        ref = await _seed(sid, payload)
        plan = await _plan(sid, ref,
                           [{"name": "密度", "expression": "{人口} / {面积 平方千米}"}])
        receipt = await service.execute(plan.plan_id, sid)
        assert receipt.row_count == 4
        assert receipt.error_count >= 1
        assert receipt.computed_rows == 3
        assert receipt.computed_rows < receipt.row_count


# ── P2-7：abort 下几何阻断不终止其他列 ────────────────────────────────────

class TestAbortGeometry:
    async def test_abort_with_blocked_geometry_still_computes_text(self):
        sid = "sess-fc-rev-abortgeo"
        ref = await _seed(sid, _payload(crs="", crs_source="assumed"))
        plan = await _plan(
            sid, ref,
            [{"name": "规范代码", "expression": "pad_left({代码}, 6, '0')"},
             {"name": "面积", "expression": "area_km2()"}],
            error_policy="abort")
        receipt = await service.execute(plan.plan_id, sid)
        got = await service.get_result(sid, receipt.output_ref)
        assert got["sample_rows"][0]["规范代码"] == "000123"
        assert got["sample_rows"][0]["面积"] is None
        assert any(c.name == "面积" and c.errors > 0 for c in receipt.columns)


# ── P2-8 / P2-10 / P2-13：取消端点、owner 元数据、锁有界 ──────────────────

class TestCancelOwnerLocks:
    async def test_cancel_during_execution_stops_it(self, monkeypatch):
        sid = "sess-fc-rev-cancel"
        ref = await _seed(sid)
        plan = await _plan(sid, ref,
                           [{"name": "密度", "expression": "{人口} / {面积 平方千米}"}])
        # 4 行 → 4 批，批间检查取消；只有「执行中」才接受取消请求。
        monkeypatch.setattr(service, "MAX_BATCH_ROWS", 1)
        entered = threading.Event()
        release = threading.Event()
        real_eval = service.evaluate_rows

        def _slow(rows, cols, mask, cfg, **kw):
            entered.set()
            release.wait(10)
            return real_eval(rows, cols, mask, cfg, **kw)

        monkeypatch.setattr(service, "evaluate_rows", _slow)
        task = asyncio.create_task(service.execute(plan.plan_id, sid))
        assert await asyncio.to_thread(entered.wait, 10)
        assert service.is_executing(sid, plan.plan_id) is True
        assert service.request_cancel(sid, plan.plan_id) is True
        release.set()
        with pytest.raises(ExecError) as ei:
            await task
        assert ei.value.code == "CANCELLED"
        # 取消请求与活跃登记都在 finally 清理，不污染后续重提
        assert service._CANCEL_REQUESTS == {}
        assert service._ACTIVE_EXEC == {}
        # 取消后重提原计划可正常执行（可恢复态）
        receipt = await service.execute(plan.plan_id, sid)
        assert receipt.output_ref.startswith("ref:fc-")

    async def test_queued_execution_cancel_is_not_lost(self, monkeypatch):
        """同一计划排队执行：取消请求不得被先完成的调用静默清掉。"""
        sid = "sess-fc-rev-cancel-queue"
        ref = await _seed(sid)
        plan = await _plan(sid, ref,
                           [{"name": "密度", "expression": "{人口} / {面积 平方千米}"}])
        monkeypatch.setattr(service, "MAX_BATCH_ROWS", 1)
        entered = threading.Event()
        release = threading.Event()
        real_eval = service.evaluate_rows

        def _slow(rows, cols, mask, cfg, **kw):
            entered.set()
            release.wait(10)
            return real_eval(rows, cols, mask, cfg, **kw)

        monkeypatch.setattr(service, "evaluate_rows", _slow)
        first = asyncio.create_task(service.execute(plan.plan_id, sid))
        assert await asyncio.to_thread(entered.wait, 10)
        second = asyncio.create_task(service.execute(plan.plan_id, sid))
        # 轮询等待第二个执行完成活跃登记（单次 sleep(0) 在慢机上可能还没跑到）
        key = service._cancel_key(sid, plan.plan_id)
        for _ in range(200):
            if service._ACTIVE_EXEC.get(key, 0) >= 2:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("第二个执行未完成活跃登记")
        assert service.request_cancel(sid, plan.plan_id) is True
        release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        codes = [getattr(r, "code", "") for r in results]
        assert codes == ["CANCELLED", "CANCELLED"], codes
        assert service._CANCEL_REQUESTS == {}
        assert service._ACTIVE_EXEC == {}

    async def test_cancel_on_idle_or_finished_plan_is_noop(self):
        sid = "sess-fc-rev-cancel-idle"
        ref = await _seed(sid)
        plan = await _plan(sid, ref,
                           [{"name": "密度", "expression": "{人口} / 1"}])
        # 未开始：拒绝登记（旧实现无条件 add，陈旧键会让重提第一批就 CANCELLED）
        assert service.request_cancel(sid, plan.plan_id) is False
        receipt = await service.execute(plan.plan_id, sid)
        assert receipt.output_ref.startswith("ref:fc-")
        # 已结束：同样拒绝，且不残留任何键
        assert service.request_cancel(sid, plan.plan_id) is False
        assert service._CANCEL_REQUESTS == {}
        again = await service.execute(plan.plan_id, sid)
        assert again.output_ref == receipt.output_ref

    async def test_idempotent_hit_respects_cancel(self):
        """幂等短路前必须尊重取消意图（排队期间已取消不得判成功）。"""
        sid = "sess-fc-rev-cancel-idem"
        ref = await _seed(sid)
        plan = await _plan(sid, ref,
                           [{"name": "密度", "expression": "{人口} / 1"}])
        first = await service.execute(plan.plan_id, sid)
        assert first.output_ref.startswith("ref:fc-")
        key = service._cancel_key(sid, plan.plan_id)
        service._mark_active(key)             # 模拟排队中被用户取消
        service._CANCEL_REQUESTS[key] = time.monotonic()
        try:
            with pytest.raises(ExecError) as ei:
                await service.execute(plan.plan_id, sid)
            assert ei.value.code == "CANCELLED"
        finally:
            service._CANCEL_REQUESTS.pop(key, None)
            service._unmark_active(key)

    async def test_preview_allowed_after_cancel(self):
        sid = "sess-fc-rev-cancel-preview"
        ref = await _seed(sid)
        plan = await _plan(sid, ref,
                           [{"name": "密度", "expression": "{人口} / 1"}])
        plan.state = "cancelled"
        assert await rc.save_plan(plan)
        preview = await service.preview(plan.plan_id, sid)
        assert preview["kind"] == "sample"

    async def test_owner_is_metadata_not_a_gate(self):
        sid = "sess-fc-rev-owner"
        ref = await _seed(sid)
        plan = await _plan(sid, ref,
                           [{"name": "密度", "expression": "{人口} / 1"}],
                           owner="alice")
        # 跨入口（UI user_id / Pi agent）复用同一 plan 不得被 owner 误拒；
        # 数据隔离由 session 归属与 ref 命名空间保证。
        receipt = await service.execute(plan.plan_id, sid, owner="bob")
        assert receipt.output_ref.startswith("ref:fc-")

    def test_active_registration_is_reference_counted(self):
        """同一计划可能排队执行：先完成者不得摘掉排队者的活跃登记。"""
        key = service._cancel_key("sess-refcount", "plan-refcount")
        service._mark_active(key)
        service._mark_active(key)
        assert service.is_executing("sess-refcount", "plan-refcount") is True
        service._unmark_active(key)
        assert service.is_executing("sess-refcount", "plan-refcount") is True
        service._unmark_active(key)
        assert service.is_executing("sess-refcount", "plan-refcount") is False

    def test_exec_lock_table_is_weak(self):
        import weakref
        assert isinstance(service._EXEC_LOCKS, weakref.WeakValueDictionary)


# ── P2-11：ring 溢出后幂等仍成立；manifest 不截断 ─────────────────────────

class TestStableKeys:
    async def test_ring_overflow_keeps_idempotency(self):
        sid = "sess-fc-rev-ring"
        ref = await _seed(sid)
        plan = await _plan(sid, ref,
                           [{"name": "密度", "expression": "{人口} / {面积 平方千米}"}])
        first = await service.execute(plan.plan_id, sid)
        # 制造 70 条不同收据把 64 条 ring 挤出，稳定键文件必须仍保住幂等。
        for i in range(70):
            dummy = first.model_copy(update={
                "compute_fingerprint": f"dummy-{i:03d}",
                "output_ref": f"ref:fc-dummy-{i}",
            })
            assert await rc.append_receipt(sid, dummy)
        again = await service.execute(plan.plan_id, sid)
        assert again.output_ref == first.output_ref
        assert again.created_at == first.created_at

    async def test_manifest_does_not_silently_drop_history(self):
        sid = "sess-fc-rev-manifest"
        ref = await _seed(sid)
        plan = await _plan(sid, ref,
                           [{"name": "密度", "expression": "{人口} / 1"}])
        for i in range(130):
            assert await service.save_to_project(
                sid, f"ref:fc-x-{i}", "proj-many", plan)
        items = await service.list_project_results(sid, "proj-many")
        assert len(items) == 130
