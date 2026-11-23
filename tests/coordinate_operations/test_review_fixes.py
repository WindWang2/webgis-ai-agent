"""G45 评审修复回归（Round-1 独立评审 P1/P2/P3 对应项）。

- P1-1：幂等世代复用（历史失败世代后的 completed run 仍可复用）
- P2-3：z_policy=reject 真实拒绝；out_of_area_policy=fail 词表摘除
- P3-6：strict_publish_gate 对 worker/attempt 缺失 fail-closed
- P3-7：预览 findings 区分输入/输出非有限
- P1-2：线程内进度上报真实落库（async handle）
- P1（波次三）：verify_frozen 按 carrier 复核 json 冻结件（无 pyarrow 环境）
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.coordinate_operations import service
from app.services.coordinate_operations.contracts import CoordinateOperationError
from tests.coordinate_operations.conftest import make_point_features

SESSION = "sess-g45-review"
ACTOR = {"user_id": "u-g45", "org_id": None, "owner_token": None,
         "trusted_session_id": SESSION}


def _run(coro):
    return asyncio.run(coro)


def _plan(src, target="EPSG:32650", **over):
    plan = {
        "title": "评审回归",
        "source": {"kind": "session_ref", "ref": src["ref"]},
        "target_crs": target,
        "z_policy": "preserve_unconverted",
        "empty_geometry_policy": "keep",
        "out_of_area_policy": "report",
    }
    plan.update(over)
    return plan


def _lock(src, plan_id, revision):
    listing = _run(service.list_operations(
        SESSION, {"kind": "session_ref", "ref": src["ref"]}, "EPSG:32650",
        None, ACTOR))
    cid = next(c["candidate_id"] for c in listing["listing"]["candidates"]
               if c["availability"] == "available")
    return _run(service.lock_plan(
        SESSION, plan_id, cid, expected_revision=revision, actor=ACTOR))


async def _lock_async(src, plan_id, revision):
    listing = await service.list_operations(
        SESSION, {"kind": "session_ref", "ref": src["ref"]}, "EPSG:32650",
        None, ACTOR)
    cid = next(c["candidate_id"] for c in listing["listing"]["candidates"]
               if c["availability"] == "available")
    return await service.lock_plan(
        SESSION, plan_id, cid, expected_revision=revision, actor=ACTOR)


class TestP1IdempotencyGenerationReuse:
    def test_completed_in_later_generation_is_reused(self, co_env, seed_fc_ref):
        """P1-1：首代失败 → 次代成功 → 第三次同输入必须幂等复用（非全量重跑）。"""
        src = seed_fc_ref(SESSION, make_point_features(12), crs="EPSG:4326")
        plan = _run(service.create_plan(SESSION, _plan(src), actor=ACTOR))
        view = _lock(src, plan["plan_id"], plan["revision"])

        # exec1：注入失败（模拟 publish_blocked 类瞬时失败）
        from app.services.coordinate_operations import outputs as out_mod

        orig_gate = out_mod.strict_publish_gate

        def failing_gate(handle, job_id):
            raise CoordinateOperationError("publish_blocked", "注入瞬时失败")

        out_mod.strict_publish_gate = failing_gate
        try:
            with pytest.raises(CoordinateOperationError):
                _run(service.execute_plan(
                    SESSION, plan["plan_id"], expected_revision=view["revision"],
                    actor=ACTOR, background=False))
        finally:
            out_mod.strict_publish_gate = orig_gate

        # exec2：成功（世代键 base:g2）
        r2 = _run(service.execute_plan(
            SESSION, plan["plan_id"], expected_revision=view["revision"],
            actor=ACTOR, background=False))
        assert r2["status"] == "completed"

        # exec3：同输入 → 复用 exec2 成果（不再新 run）
        r3 = _run(service.execute_plan(
            SESSION, plan["plan_id"], expected_revision=view["revision"],
            actor=ACTOR, background=False))
        assert r3.get("idempotent_reuse") is True
        assert r3["run_id"] == r2["run_id"]

    def test_active_generation_is_convergence_anchor(self, co_env, seed_fc_ref):
        """P1-1：任一世代的活跃 run 收敛（不并发起新执行）。

        同一事件循环内提交两次（background 任务附着于该循环；跨 asyncio.run
        会把首个 background 执行体取消成 failed —— 那是另一条正确路径）。
        """

        src = seed_fc_ref(SESSION, make_point_features(8), crs="EPSG:4326")

        async def scenario():
            plan = await service.create_plan(SESSION, _plan(src), actor=ACTOR)
            view = await _lock_async(src, plan["plan_id"], plan["revision"])
            r1 = await service.execute_plan(
                SESSION, plan["plan_id"], expected_revision=view["revision"],
                actor=ACTOR, background=True)
            assert r1["status"] in ("pending", "running")
            r2 = await service.execute_plan(
                SESSION, plan["plan_id"], expected_revision=view["revision"],
                actor=ACTOR, background=True)
            assert r2.get("idempotent_reuse") is True
            assert r2["run_id"] == r1["run_id"]
            await service.cancel_run(SESSION, r1["run_id"])

        asyncio.run(scenario())


class TestP2PolicyHonesty:
    def test_z_policy_reject_blocks_z_source(self, co_env, seed_fc_ref):
        """P2-3：z_policy=reject 在预览/执行前按源 Z 证据 typed 拒绝。"""
        src = seed_fc_ref(
            SESSION, make_point_features(6, crs_z=True), crs="EPSG:4326")
        plan = _run(service.create_plan(
            SESSION, _plan(src, z_policy="reject"), actor=ACTOR))
        view = _lock(src, plan["plan_id"], plan["revision"])
        with pytest.raises(CoordinateOperationError) as ei:
            _run(service.preview_plan(
                SESSION, plan["plan_id"], expected_revision=view["revision"],
                actor=ACTOR))
        assert ei.value.error_code == "unsupported_dimension"
        assert "z_policy=reject" in ei.value.reason

    def test_out_of_area_fail_removed_from_contract(self, co_env, seed_fc_ref):
        """P2-3：fail 政策未实现 —— 词表摘除（pydantic 拒绝）。"""
        src = seed_fc_ref(SESSION, make_point_features(5), crs="EPSG:4326")
        with pytest.raises(CoordinateOperationError) as ei:
            _run(service.create_plan(
                SESSION, _plan(src, out_of_area_policy="fail"), actor=ACTOR))
        assert ei.value.error_code == "plan_invalid"


class TestP3GateAndFindings:
    def test_gate_missing_worker_attempt_blocks(self, co_env, seed_fc_ref):
        """P3-6：job worker/attempt 字段缺失 = 无法确认 → 阻断。"""
        from app.services.coordinate_operations import outputs as out_mod

        src = seed_fc_ref(SESSION, make_point_features(6), crs="EPSG:4326")
        plan = _run(service.create_plan(SESSION, _plan(src), actor=ACTOR))
        view = _lock(src, plan["plan_id"], plan["revision"])

        orig_gate = out_mod.strict_publish_gate

        def gate_nulling_fields(handle, job_id):
            from app.core.database import SessionLocal
            from app.models.db_model import AnalysisTask
            from sqlalchemy import select

            with SessionLocal() as db:
                row = db.execute(select(AnalysisTask).where(
                    AnalysisTask.id == job_id)).scalar_one()
                row.worker_id = None
                row.attempt = None
                db.commit()
            return orig_gate(handle, job_id)

        out_mod.strict_publish_gate = gate_nulling_fields
        try:
            with pytest.raises(CoordinateOperationError) as ei:
                _run(service.execute_plan(
                    SESSION, plan["plan_id"], expected_revision=view["revision"],
                    actor=ACTOR, background=False))
        finally:
            out_mod.strict_publish_gate = orig_gate
        assert ei.value.error_code == "publish_blocked"
        assert "无法确认" in ei.value.reason or "缺失" in ei.value.reason

    def test_preview_distinguishes_non_finite_phase(self, co_env, seed_fc_ref):
        """P3-7：非有限输出（域外变换结果）计入 non_finite_output 而非 input。"""
        # UTM 50N 域外点（经度 170 远超 [114,120) 带域）：变换输出非有限/超域
        feats = make_point_features(4)
        feats[2]["geometry"]["coordinates"] = [170.0, 39.9]
        src = seed_fc_ref(SESSION, feats, crs="EPSG:4326")
        plan = _run(service.create_plan(SESSION, _plan(src), actor=ACTOR))
        view = _lock(src, plan["plan_id"], plan["revision"])
        pv = _run(service.preview_plan(
            SESSION, plan["plan_id"], expected_revision=view["revision"],
            actor=ACTOR))
        f = pv["findings"]
        if (f.get("non_finite_input") or 0) + (f.get("non_finite_output") or 0):
            # 域外点若被判非有限，phase 必须归 output（输入本身有限）
            assert (f.get("non_finite_output") or 0) >= 1
            assert (f.get("non_finite_input") or 0) == 0


class TestP1ProgressReporting:
    def test_thread_progress_reaches_async_handle(self, co_env):
        """P1-2：worker 线程内 progress 协程经 run_coroutine_threadsafe 真实执行。"""
        import concurrent.futures

        from app.services.coordinate_operations.execute import (
            _report_progress,
        )

        awaited = asyncio.Event()
        calls: list[dict] = []

        class AsyncHandle:
            worker_id = "w"
            attempt = 1

            async def progress(self, **fields):
                calls.append(fields)
                awaited.set()

        async def scenario():
            handle = AsyncHandle()
            loop = asyncio.get_running_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                await loop.run_in_executor(
                    pool, lambda: _report_progress(
                        handle, value=7, message="已变换 7/10 行", loop=loop))
            assert await asyncio.wait_for(awaited.wait(), 5.0), \
                "progress 协程未被 await"
            assert calls and calls[0]["value"] == 7

        asyncio.run(scenario())


class TestP25StartupRecovery:
    def test_recover_all_converges_orphans(self, co_env, seed_fc_ref):
        """P2-5：全局恢复（lifespan 接线入口）收敛崩溃窗口孤儿。"""
        src = seed_fc_ref(SESSION, make_point_features(6), crs="EPSG:4326")
        plan = _run(service.create_plan(SESSION, _plan(src), actor=ACTOR))
        view = _lock(src, plan["plan_id"], plan["revision"])
        run = _run(service.execute_plan(
            SESSION, plan["plan_id"], expected_revision=view["revision"],
            actor=ACTOR, background=False))
        assert run["status"] == "completed"
        service._mark_run(SESSION, run["run_id"], "running")
        out = _run(service.recover_all())
        actions = {c["run_id"]: c["action"] for c in out["converged"]}
        assert actions.get(run["run_id"]) == "receipt_finalized"


class TestP1JsonCarrierVerifyFrozen:
    """P1（波次三）：verify_frozen 必须按载体复核冻结件。

    无 pyarrow 时 ``freeze_source`` 对 payload 源产出 carrier="json" 的
    ``source-frozen.json``（执行体 ``_iter_frozen_features`` 双载体可读）；
    verify_frozen 只认 parquet 会把它误报"冻结源缺失" —— session_ref 源的
    全部坐标操作在该环境不可执行。
    """

    @staticmethod
    def _snapshot(features):
        import types

        return types.SimpleNamespace(
            parquet_path=None,
            payload={"type": "FeatureCollection", "features": features,
                     "properties": {"schema_info": {"crs": "EPSG:4326"}}},
            row_count=len(features),
            crs="EPSG:4326",
            content_fingerprint="a" * 64,
            kind="session_ref",
            geometry={"types": ["Point"]},
        )

    def _freeze_json(self, tmp_path, monkeypatch, features):
        import sys

        from app.services.coordinate_operations import freeze

        run_dir = tmp_path / "run"
        # 无 pyarrow 环境：block 掉 pyarrow 使 freeze_source 走 json 载体
        monkeypatch.setitem(sys.modules, "pyarrow", None)
        return run_dir, freeze, freeze.freeze_source(
            self._snapshot(features), run_dir)

    def test_json_carrier_frozen_passes_verify(self, tmp_path, monkeypatch):
        """carrier="json" 的合法冻结件必须通过执行前复核（parquet 仍按原名找）。"""
        run_dir, freeze, evidence = self._freeze_json(
            tmp_path, monkeypatch, make_point_features(3))
        assert evidence["carrier"] == "json"
        assert (run_dir / "source-frozen.json").is_file()
        path = freeze.verify_frozen(run_dir, evidence)
        assert path == run_dir / "source-frozen.json"
        assert path.is_file()
        # parquet 载体仍解析到 .parquet（同目录无该文件 → 缺失报错不变）
        with pytest.raises(CoordinateOperationError) as ei:
            freeze.verify_frozen(run_dir, dict(evidence, carrier="parquet"))
        assert ei.value.error_code == "internal_error"
        assert "冻结源缺失" in ei.value.reason

    def test_json_carrier_drift_still_blocked(self, tmp_path, monkeypatch):
        """json 载体的 sha256 漂移 / 行数漂移 / 未知载体仍被 typed 拒绝。"""
        run_dir, freeze, evidence = self._freeze_json(
            tmp_path, monkeypatch, make_point_features(3))
        frozen = run_dir / "source-frozen.json"

        # sha256 漂移（staging 被外部改动）
        frozen.write_text('{"type":"FeatureCollection","features":[]}')
        with pytest.raises(CoordinateOperationError) as ei:
            freeze.verify_frozen(run_dir, evidence)
        assert ei.value.error_code == "internal_error"
        assert "sha256" in ei.value.reason

        # 行数漂移（sha 对齐但行数与证据不符）
        drifted = dict(evidence, frozen_sha256=freeze._file_sha256(frozen))
        with pytest.raises(CoordinateOperationError) as ei:
            freeze.verify_frozen(run_dir, drifted)
        assert ei.value.error_code == "internal_error"
        assert "行数" in ei.value.reason

        # 未知载体：拒绝而非静默按 parquet 猜
        with pytest.raises(CoordinateOperationError) as ei:
            freeze.verify_frozen(run_dir, dict(evidence, carrier="avro"))
        assert ei.value.error_code == "internal_error"
        assert "载体" in ei.value.reason


class TestDisplayFabricCleanup:
    """P2：显示副本 fabric parquet 的临时文件/孤儿 blob 清理。"""

    @staticmethod
    def _fc() -> dict:
        return {"type": "FeatureCollection", "features": [{
            "type": "Feature", "properties": {"name": "显示"},
            "geometry": {"type": "Point", "coordinates": [116.0, 40.0]}}]}

    def _patch_common(self, monkeypatch):
        from app.services.coordinate_operations import outputs as out_mod

        monkeypatch.setattr(out_mod, "load_output_fc", lambda rid: self._fc())

        async def fake_mint(session_id, fc, *, label):
            return {"display_ref": "ref:session-data/display-x",
                    "rows": len(fc["features"]), "label": label}

        monkeypatch.setattr(out_mod, "mint_display_ref", fake_mint)
        return out_mod

    def test_temp_parquet_unlinked_when_fabric_register_fails(
            self, co_env, monkeypatch):
        out_mod = self._patch_common(monkeypatch)
        captured = {}

        def boom(session_id, parquet_path, **kw):  # noqa: ARG001
            captured["tmp"] = str(parquet_path)
            assert parquet_path.is_file()  # write_table 已完成
            raise RuntimeError("注入：fabric 登记失败")

        monkeypatch.setattr(out_mod, "register_fabric_parquet", boom)
        result = _run(service._mint_display_copy(
            service._NullHandle(), SESSION, "run-x", "EPSG:32650",
            {"title": "显示"}))
        assert result["state"] == "failed"
        from pathlib import Path

        assert not Path(captured["tmp"]).exists()

    def test_copied_blob_removed_when_artifact_registration_fails(
            self, co_env, monkeypatch, tmp_path):
        out_mod = self._patch_common(monkeypatch)
        blob = tmp_path / "fabric-copy.parquet"
        blob.write_bytes(b"parquet")

        def fake_register(session_id, parquet_path, **kw):  # noqa: ARG001
            return {"artifact_ref": "ref:fabric-parquet/deadbeef",
                    "path": str(blob), "title": kw.get("title", ""),
                    "crs": kw.get("crs"), "rows": kw.get("rows"),
                    "content_sha256": "0" * 64, "run_id": kw.get("run_id")}

        monkeypatch.setattr(out_mod, "register_fabric_parquet", fake_register)

        async def boom(*a, **k):  # noqa: ARG001
            raise RuntimeError("注入：台账登记失败")

        import app.services.artifact_registry as ar_mod

        monkeypatch.setattr(ar_mod, "register_artifact", boom)
        result = _run(service._mint_display_copy(
            service._NullHandle(), SESSION, "run-y", "EPSG:32650",
            {"title": "显示"}))
        assert result["state"] == "failed"
        assert not blob.exists()


class TestRecoverAllStagingSweep:
    """P2：>1h staging 清扫不得删除在飞 run 的目录。"""

    def test_active_run_staging_not_swept(self, co_env, seed_fc_ref):
        import os
        import time

        from app.core.database import SessionLocal
        from app.models.coordinate_operations import CoordinateOperationRun
        from app.services.coordinate_operations import outputs as out_mod

        src = seed_fc_ref(SESSION, make_point_features(2), crs="EPSG:4326")
        plan = _run(service.create_plan(SESSION, _plan(src), actor=ACTOR))
        run_id = "run-active-staging"
        with SessionLocal() as db:
            db.add(CoordinateOperationRun(
                id=run_id, plan_id=plan["plan_id"], session_id=SESSION,
                plan_revision=plan["revision"], plan_digest="0" * 64,
                status="running"))
            db.commit()
        staging = out_mod.staging_dir(run_id)
        (staging / "output.geojson").write_text(
            '{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        old = time.time() - 2 * 3600
        os.utime(staging, (old, old))  # 目录 mtime 不随 append 更新
        _run(service.recover_all())
        assert staging.is_dir()
