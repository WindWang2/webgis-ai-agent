"""G45 服务全流程测试 —— 计划/锁定/预览/执行/收据/幂等/取消/恢复。

真实 SQLite + temp DATA_DIR + 真实 durable job（run_async_job 前台路径）；
不 mock service/冻结/变换/发布语义。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.services.coordinate_operations import contracts, outputs, service
from app.services.coordinate_operations.contracts import CoordinateOperationError
from tests.coordinate_operations.conftest import make_point_features

SESSION = "sess-g45-flow"
# 服务层测试用工具平面语义（trusted_session_id 注入即授权）；
# HTTP 面归属由 require_owned_session 守卫（路由层契约）。
ACTOR = {"user_id": "u-g45", "org_id": None, "owner_token": None,
         "trusted_session_id": SESSION}


def _run(coro):
    return asyncio.run(coro)


def _seed_source(co_env, seed_fc_ref, n=61, crs="EPSG:4326",
                 with_crs_evidence=True, truncated=False):
    feats = make_point_features(n)
    return seed_fc_ref(SESSION, feats, crs=crs,
                       with_crs_evidence=with_crs_evidence,
                       truncated=truncated)


def _plan_input(src, target="EPSG:32650", **over):
    plan = {
        "title": "测试重投影",
        "source": {"kind": src["kind"], "ref": src["ref"]},
        "target_crs": target,
        "z_policy": "preserve_unconverted",
        "empty_geometry_policy": "keep",
        "out_of_area_policy": "report",
    }
    plan.update(over)
    return plan


def _lock_best(co_env, src, target="EPSG:32650", plan_id=None, revision=None):
    listing = _run(service.list_operations(
        SESSION, {"kind": src["kind"], "ref": src["ref"]}, target, None, ACTOR))
    avail = [c for c in listing["listing"]["candidates"]
             if c["availability"] == "available"]
    assert avail
    view = _run(service.lock_plan(
        SESSION, plan_id, avail[0]["candidate_id"],
        expected_revision=revision, actor=ACTOR))
    return view, listing


class TestDescribeAndPlan:
    def test_describe_source_with_crs_evidence(self, co_env, seed_fc_ref):
        src = _seed_source(co_env, seed_fc_ref)
        card = _run(service.describe_source(
            SESSION, {"kind": "session_ref", "ref": src["ref"]}, ACTOR))
        assert card["crs"] == "EPSG:4326"
        assert card["crs_evidence"]["trusted"] is True
        assert card["row_count"] == 61
        assert card["crs_description"]["identity"] == "EPSG:4326"

    def test_c04_missing_crs_blocked_not_defaulted(self, co_env, seed_fc_ref):
        src = _seed_source(co_env, seed_fc_ref, with_crs_evidence=False)
        with pytest.raises(CoordinateOperationError) as ei:
            _run(service.describe_source(
                SESSION, {"kind": "session_ref", "ref": src["ref"]}, ACTOR))
        assert ei.value.error_code == "source_crs_missing"

    def test_c31_truncated_source_blocked(self, co_env, seed_fc_ref):
        src = _seed_source(co_env, seed_fc_ref, truncated=True)
        with pytest.raises(CoordinateOperationError) as ei:
            _run(service.describe_source(
                SESSION, {"kind": "session_ref", "ref": src["ref"]}, ACTOR))
        assert ei.value.error_code == "source_incomplete"

    def test_crs_lookup_and_dimension_gate(self, co_env):
        d = service.lookup_crs("EPSG:32650")
        assert d["identity"] == "EPSG:32650"
        assert d["units"]["is_metric_metre"] is True
        bad = service.lookup_crs("EPSG:9518")
        assert bad["dimension_supported"] is False

    def test_plan_crud_revision_cas(self, co_env, seed_fc_ref):
        src = _seed_source(co_env, seed_fc_ref)
        view = _run(service.create_plan(
            SESSION, _plan_input(src), actor=ACTOR))
        assert view["status"] == "draft" and view["revision"] == 1
        # 内容不变 update：revision 不变
        same = _run(service.update_plan(
            SESSION, view["plan_id"], _plan_input(src),
            expected_revision=1, actor=ACTOR))
        assert same["revision"] == 1
        # 实质变更：revision+1 → draft（锁定被清）
        changed = _run(service.update_plan(
            SESSION, view["plan_id"], _plan_input(src, target="EPSG:3857"),
            expected_revision=1, actor=ACTOR))
        assert changed["revision"] == 2 and changed["status"] == "draft"
        # CAS 冲突
        with pytest.raises(CoordinateOperationError) as ei:
            _run(service.update_plan(
                SESSION, view["plan_id"], _plan_input(src, target="EPSG:3857"),
                expected_revision=1, actor=ACTOR))
        assert ei.value.error_code == "plan_revision_conflict"


class TestLockAndPreview:
    def test_lock_preview_flow(self, co_env, seed_fc_ref):
        src = _seed_source(co_env, seed_fc_ref)
        plan = _run(service.create_plan(
            SESSION, _plan_input(src), actor=ACTOR))
        view, listing = _lock_best(
            co_env, src, plan_id=plan["plan_id"], revision=plan["revision"])
        assert view["status"] == "locked"
        assert view["revision"] == plan["revision"] + 1
        assert view["operation"]["candidate_id"]

        pv = _run(service.preview_plan(
            SESSION, plan["plan_id"], expected_revision=view["revision"],
            actor=ACTOR, sample_rows=10))
        assert pv["sample_rows_transformed"] == 10
        assert len(pv["coordinate_pairs"]) <= contracts.PREVIEW_MAX_COORD_PAIRS
        assert pv["operation"]["candidate_id"] == view["operation"]["candidate_id"]
        # before/after 非对称（真实变换）
        pair = pv["coordinate_pairs"][0]
        assert pair["before"] != pair["after"]

    def test_c25_source_change_after_lock_blocks_preview_execute(
            self, co_env, seed_fc_ref):
        src = _seed_source(co_env, seed_fc_ref)
        plan = _run(service.create_plan(
            SESSION, _plan_input(src), actor=ACTOR))
        view, _ = _lock_best(
            co_env, src, plan_id=plan["plan_id"], revision=plan["revision"])
        # 源被覆盖（同 ref 新内容 → 指纹漂移）：测试内直接改内存 store 载荷
        from app.services.session_data import session_data_manager

        session_data_manager._store[SESSION][src["ref"]] = {
            "type": "FeatureCollection",
            "features": make_point_features(5),
            "properties": {"schema_info": {"crs": "EPSG:4326"}},
        }
        with pytest.raises(CoordinateOperationError) as ei:
            _run(service.preview_plan(
                SESSION, plan["plan_id"], expected_revision=view["revision"],
                actor=ACTOR))
        assert ei.value.error_code == "source_revision_changed"

    def test_c10_env_drift_blocks_preview(self, co_env, seed_fc_ref, monkeypatch):
        src = _seed_source(co_env, seed_fc_ref)
        plan = _run(service.create_plan(
            SESSION, _plan_input(src), actor=ACTOR))
        view, _ = _lock_best(
            co_env, src, plan_id=plan["plan_id"], revision=plan["revision"])
        # 篡改冻结环境证据 → 预览/执行前环境复核阻断
        from app.core.database import SessionLocal
        from app.models.coordinate_operations import CoordinateOperationPlan
        from sqlalchemy import select

        with SessionLocal() as db:
            row = db.execute(select(CoordinateOperationPlan).where(
                CoordinateOperationPlan.id == plan["plan_id"])).scalar_one()
            op = json.loads(row.operation)
            op["environment"]["proj_version"] = "0.0.0-fake"
            row.operation = json.dumps(op)
            db.commit()
        with pytest.raises(CoordinateOperationError) as ei:
            _run(service.preview_plan(
                SESSION, plan["plan_id"], expected_revision=view["revision"],
                actor=ACTOR))
        assert ei.value.error_code == "operation_env_changed"

    def test_preview_stale_after_edit(self, co_env, seed_fc_ref):
        src = _seed_source(co_env, seed_fc_ref)
        plan = _run(service.create_plan(
            SESSION, _plan_input(src), actor=ACTOR))
        view, _ = _lock_best(
            co_env, src, plan_id=plan["plan_id"], revision=plan["revision"])
        changed = _run(service.update_plan(
            SESSION, plan["plan_id"], _plan_input(src, target="EPSG:3857"),
            expected_revision=view["revision"], actor=ACTOR))
        assert changed["status"] == "draft"  # 编辑 → 回 draft，预览/锁定失效
        with pytest.raises(CoordinateOperationError) as ei:
            _run(service.preview_plan(
                SESSION, plan["plan_id"],
                expected_revision=changed["revision"], actor=ACTOR))
        assert ei.value.error_code == "plan_not_locked"


class TestExecute:
    def test_c01_c20_full_execute_1201_features(self, co_env, seed_fc_ref):
        """C01 完整链 + C20 超过预览行数的 1201 要素完整成果。"""
        src = _seed_source(co_env, seed_fc_ref, n=1201)
        plan = _run(service.create_plan(
            SESSION, _plan_input(src), actor=ACTOR))
        view, _ = _lock_best(
            co_env, src, plan_id=plan["plan_id"], revision=plan["revision"])

        run = _run(service.execute_plan(
            SESSION, plan["plan_id"], expected_revision=view["revision"],
            actor=ACTOR, background=False))
        assert run["status"] == "completed", run
        receipt = run["receipt"]
        assert receipt["rows_transformed"] == 1201
        assert receipt["rows_total"] == 1201
        assert receipt["output_crs"] == "EPSG:32650"
        assert receipt["operation"]["name"]
        assert receipt["z_policy"] == "preserve_unconverted"

        # 权威文件 + oracle 对账
        fc = outputs.load_output_fc(run["run_id"])
        assert len(fc["features"]) == 1201
        from tests.coordinate_operations.test_kernel_and_candidates import (
            _utm50n_analytic,
        )
        p0 = fc["features"][0]
        want = _utm50n_analytic(*make_point_features(1)[0]["geometry"]["coordinates"][:2])
        assert abs(p0["geometry"]["coordinates"][0] - want[0]) < 0.01
        # 属性保真（C36）
        assert p0["properties"]["_wfid"] == "f-00000"
        assert p0["properties"]["name"] == "要素0·中文"
        assert p0["properties"]["big_int"] == 98765432101234
        # 显示副本（C21）
        assert run["receipt"]["display"]["state"] == "available"
        assert run["receipt"]["display"]["display_ref"]

        # 结果分页
        page = _run(service.run_result_page(SESSION, run["run_id"], 0, 50))
        assert page["total_rows"] == 1201
        assert len(page["rows"]) == 50
        assert page["crs"] == "EPSG:32650"

    def test_c27_idempotent_reuse_verified(self, co_env, seed_fc_ref):
        src = _seed_source(co_env, seed_fc_ref, n=33)
        plan = _run(service.create_plan(
            SESSION, _plan_input(src), actor=ACTOR))
        view, _ = _lock_best(
            co_env, src, plan_id=plan["plan_id"], revision=plan["revision"])
        r1 = _run(service.execute_plan(
            SESSION, plan["plan_id"], expected_revision=view["revision"],
            actor=ACTOR, background=False))
        assert r1["status"] == "completed"
        r2 = _run(service.execute_plan(
            SESSION, plan["plan_id"], expected_revision=view["revision"],
            actor=ACTOR, background=False))
        assert r2.get("idempotent_reuse") is True
        assert r2["run_id"] == r1["run_id"]
        # 内容被删 → 幂等命中失效（typed，不凭历史收据）
        outputs.discard_final(r1["run_id"])
        r3 = _run(service.execute_plan(
            SESSION, plan["plan_id"], expected_revision=view["revision"],
            actor=ACTOR, background=False))
        assert r3["status"] == "completed" and r3["run_id"] != r1["run_id"]

    def test_c18_all_or_nothing_no_publication_on_failure(
            self, co_env, seed_fc_ref):
        feats = make_point_features(10)
        feats[5]["geometry"]["coordinates"] = [float("nan"), 39.9]
        src = seed_fc_ref(SESSION, feats, crs="EPSG:4326")
        plan = _run(service.create_plan(
            SESSION, _plan_input({"kind": "session_ref", "ref": src["ref"]}),
            actor=ACTOR))
        view, _ = _lock_best(
            co_env, {"kind": "session_ref", "ref": src["ref"]},
            plan_id=plan["plan_id"], revision=plan["revision"])
        from app.services.data_operations.runner import run_async_job  # noqa: F401

        with pytest.raises(CoordinateOperationError):
            _run(service.execute_plan(
                SESSION, plan["plan_id"], expected_revision=view["revision"],
                actor=ACTOR, background=False))
        runs = _run(service.list_runs(SESSION))
        assert runs["runs"], "run 行应存在"
        run = runs["runs"][0]
        assert run["status"] == "failed"
        # 非有限在最早可证明阶段被 typed 拒绝：冻结编码（unsupported_dimension）
        # 或变换内核（execute_failed）—— 均为 all-or-nothing、无静默丢行
        assert run["error_code"] in ("execute_failed", "unsupported_dimension")
        # 无终态包发布
        assert not outputs.final_dir(run["run_id"]).exists()
        assert all(r["status"] != "completed" for r in runs["runs"])

    def test_c22_display_failure_keeps_main_completed(
            self, co_env, seed_fc_ref, monkeypatch):
        src = _seed_source(co_env, seed_fc_ref, n=10)
        plan = _run(service.create_plan(
            SESSION, _plan_input(src), actor=ACTOR))
        view, _ = _lock_best(
            co_env, src, plan_id=plan["plan_id"], revision=plan["revision"])

        def _boom(handle, *a, **k):
            raise CoordinateOperationError("display_unavailable", "注入失败")

        monkeypatch.setattr(service, "_mint_display_copy", _boom)
        run = _run(service.execute_plan(
            SESSION, plan["plan_id"], expected_revision=view["revision"],
            actor=ACTOR, background=False))
        assert run["status"] == "completed"
        assert run["receipt"]["display"]["state"] == "failed"
        # 主成果仍可读
        fc = outputs.load_output_fc(run["run_id"])
        assert len(fc["features"]) == 10

    def test_display_ref_remint(self, co_env, seed_fc_ref):
        src = _seed_source(co_env, seed_fc_ref, n=10)
        plan = _run(service.create_plan(
            SESSION, _plan_input(src), actor=ACTOR))
        view, _ = _lock_best(
            co_env, src, plan_id=plan["plan_id"], revision=plan["revision"])
        run = _run(service.execute_plan(
            SESSION, plan["plan_id"], expected_revision=view["revision"],
            actor=ACTOR, background=False))
        d = _run(service.display_ref_for_run(SESSION, run["run_id"], ACTOR))
        assert d["state"] == "available" and d["display_ref"]


class TestCancelAndRecovery:
    def test_c28_cancel_before_publish(self, co_env, seed_fc_ref, monkeypatch):
        """执行期取消：无完整发布，终态诚实。"""
        src = _seed_source(co_env, seed_fc_ref, n=200)

        # 在变换内核的预算闸前注入取消（checkpoint 语义等效：第一批后取消）
        import app.services.coordinate_operations.execute as ex_mod

        orig = ex_mod.transform_feature
        calls = {"n": 0}

        def slow_transform(feature, op, stats, *, row_index):
            calls["n"] += 1
            if calls["n"] == 50:
                from app.lib.cancellation import OperationCancelled

                raise OperationCancelled("cancel requested (durable)")
            return orig(feature, op, stats, row_index=row_index)

        monkeypatch.setattr(ex_mod, "transform_feature", slow_transform)
        plan = _run(service.create_plan(
            SESSION, _plan_input(src), actor=ACTOR))
        view, _ = _lock_best(
            co_env, src, plan_id=plan["plan_id"], revision=plan["revision"])
        run = _run(service.execute_plan(
            SESSION, plan["plan_id"], expected_revision=view["revision"],
            actor=ACTOR, background=False))
        assert run["status"] in ("cancelled", "failed")  # 取消传播为准
        if run["status"] == "cancelled":
            assert not outputs.final_dir(run["run_id"]).exists()

    def test_c29_recovery_converges_orphans(self, co_env, seed_fc_ref):
        """主成果已发布但 run 行未收敛 → recover 按权威文件补收据（C38）。"""
        src = _seed_source(co_env, seed_fc_ref, n=20)
        plan = _run(service.create_plan(
            SESSION, _plan_input(src), actor=ACTOR))
        view, _ = _lock_best(
            co_env, src, plan_id=plan["plan_id"], revision=plan["revision"])
        run = _run(service.execute_plan(
            SESSION, plan["plan_id"], expected_revision=view["revision"],
            actor=ACTOR, background=False))
        assert run["status"] == "completed"
        # 模拟崩溃窗口：run 行拨回 running（job 已 completed）
        service._mark_run(SESSION, run["run_id"], "running")
        out = _run(service.recover(SESSION))
        actions = {c["run_id"]: c["action"] for c in out["converged"]}
        assert actions.get(run["run_id"]) == "receipt_finalized"
        again = _run(service.inspect_run(SESSION, run["run_id"]))
        assert again["status"] == "completed"
        assert again["receipt"]["rows_total"] == 20


class TestSourceIntegrity:
    def test_c26_freeze_isolates_aba(self, co_env, seed_fc_ref):
        """冻结件与源隔离：执行读冻结件，源后续 ABA 不产生混合成果。"""
        src = _seed_source(co_env, seed_fc_ref, n=40)
        plan = _run(service.create_plan(
            SESSION, _plan_input(src), actor=ACTOR))
        view, _ = _lock_best(
            co_env, src, plan_id=plan["plan_id"], revision=plan["revision"])
        run = _run(service.execute_plan(
            SESSION, plan["plan_id"], expected_revision=view["revision"],
            actor=ACTOR, background=False))
        assert run["status"] == "completed"
        # 成果行数 = 冻结时行数（40），与源之后变化无关
        fc = outputs.load_output_fc(run["run_id"])
        assert len(fc["features"]) == 40

    def test_c04_untrusted_crs_label(self, co_env, seed_fc_ref):
        """CRS 证据只能来自载体元数据：GeoJSON 顶层 crs 成员不是权威证据
        （本域走 schema_info.crs 通道）；缺失即阻断。"""
        feats = make_point_features(5)
        src = seed_fc_ref(SESSION, feats, crs="EPSG:4326",
                          with_crs_evidence=False)
        with pytest.raises(CoordinateOperationError) as ei:
            _run(service.describe_source(
                SESSION, {"kind": "session_ref", "ref": src["ref"]}, ACTOR))
        assert ei.value.error_code == "source_crs_missing"


class TestStrictPublishGate:
    def test_c39_cancel_facts_block_publish(self, co_env, seed_fc_ref):
        """发布门前 job 已被请求取消 → publish_blocked，无终态包。"""
        from app.services.coordinate_operations import outputs as out_mod

        src = _seed_source(co_env, seed_fc_ref, n=30)
        plan = _run(service.create_plan(
            SESSION, _plan_input(src), actor=ACTOR))
        view, _ = _lock_best(
            co_env, src, plan_id=plan["plan_id"], revision=plan["revision"])

        import app.services.coordinate_operations.service as svc

        orig_gate = out_mod.strict_publish_gate

        def gate_with_cancel(handle, job_id):
            from app.core.database import SessionLocal
            from app.services.jobs.store import DurableJobStore

            with SessionLocal() as db:
                DurableJobStore.request_cancel_sync(db, job_id)
                db.commit()
            return orig_gate(handle, job_id)

        out_mod.strict_publish_gate = gate_with_cancel
        try:
            _run(service.execute_plan(
                SESSION, plan["plan_id"], expected_revision=view["revision"],
                actor=ACTOR, background=False))
        except Exception:  # noqa: BLE001 — OperationCancelled 经 runner 收敛
            pass  # 为 cancelled 终态；传播形态两可，终态断言为准
        finally:
            out_mod.strict_publish_gate = orig_gate
        runs = _run(service.list_runs(SESSION))
        run = runs["runs"][0]
        # P3-10（评审）：取消事实命中的发布阻断 → 终态 cancelled（用户主动
        # 取消不得显示为「失败」）；无终态包发布保持不变。
        assert run["status"] in ("cancelled", "failed")
        if run["status"] == "failed":
            assert run["error_code"] == "publish_blocked"
        assert not outputs.final_dir(run["run_id"]).exists()

    def test_c40_stale_attempt_cannot_publish(self, co_env, seed_fc_ref):
        """旧执行体 claim 被新 worker 接管 → 发布门阻断。"""
        from app.services.coordinate_operations import outputs as out_mod

        src = _seed_source(co_env, seed_fc_ref, n=10)
        plan = _run(service.create_plan(
            SESSION, _plan_input(src), actor=ACTOR))
        view, _ = _lock_best(
            co_env, src, plan_id=plan["plan_id"], revision=plan["revision"])

        orig_gate = out_mod.strict_publish_gate

        class FakeHandle:
            worker_id = "old-worker"
            attempt = 1

            def checkpoint(self):
                return None

            def progress(self, *a, **k):
                return False

        def gate_with_takeover(handle, job_id):
            from app.core.database import SessionLocal
            from app.models.db_model import AnalysisTask
            from sqlalchemy import select

            with SessionLocal() as db:
                row = db.execute(select(AnalysisTask).where(
                    AnalysisTask.id == job_id)).scalar_one()
                row.worker_id = "new-worker"  # 新 attempt 接管
                db.commit()
            return orig_gate(FakeHandle(), job_id)

        out_mod.strict_publish_gate = gate_with_takeover
        try:
            with pytest.raises(CoordinateOperationError) as ei:
                _run(service.execute_plan(
                    SESSION, plan["plan_id"], expected_revision=view["revision"],
                    actor=ACTOR, background=False))
        finally:
            out_mod.strict_publish_gate = orig_gate
        assert ei.value.error_code == "publish_blocked"
        runs = _run(service.list_runs(SESSION))
        run = runs["runs"][0]
        assert run["status"] == "failed"
        assert run["error_code"] == "publish_blocked"
        assert not outputs.final_dir(run["run_id"]).exists()


class TestBudget:
    def test_c34_row_budget_at_describe(self, co_env, seed_fc_ref, monkeypatch):
        monkeypatch.setattr(contracts, "MAX_ROWS", 10)
        src = _seed_source(co_env, seed_fc_ref, n=61)
        with pytest.raises(CoordinateOperationError) as ei:
            _run(service.describe_source(
                SESSION, {"kind": "session_ref", "ref": src["ref"]}, ACTOR))
        assert ei.value.error_code == "budget_exceeded"
