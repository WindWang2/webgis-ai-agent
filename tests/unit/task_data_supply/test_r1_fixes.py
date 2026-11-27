"""R1 评审修复回归测试（GOAL-20261126-02 WP6 修复批次）。

覆盖三方评审的 P0/P1/P2 关键修复：
- A-P0-1：geometry 声明的跨线任务（±180 拆分约定）逐顶点归一，
  手算真值比 1.0（修复前整体平移 → 340° 宽错侧 → 伪 partial 0.5）；
- A-P1-1：期次不匹配（temporal none）→ 计划 requires_confirmation；
- A-P2-2：display_only×numeric 的 FULL 候选也需确认（upload 替代）；
- A-P2-3/C-P2-2：search_more 进入可选决策词表；
- A-P2-4：time_range 混用 date/datetime、aware/naive → 类型化拒绝；
- A-P2-5：requirement_id 对集合型字段顺序不敏感；
- A-P2-6：覆盖证据变化（footprint 修正）→ plan_id 换键；
- A-P2-8：accessibility/geometry_skeleton 对 display_only/unknown 不可用；
- A-P3-8：bbox 纬度越界类型化拒绝；
- C-P1-2：裸 switch（无候选目标）→ SUPPLY_CHOICE_SWITCH_TARGET_REQUIRED；
- B-P1-4：重复执行幂等（已完成步骤跳过不重复物化）；
- B-P1-5：计划存储按会话分桶（跨会话不可互相驱逐）；
- B-P1-2：执行面租户校验（跨租户条目 → 类型化失败，不泄漏存在性）；
- A-P1-2/C-P1-1：执行投影 QuerySpec（bbox/columns/datetime_range/limit）。
"""
import pytest

from app.services.task_data_supply.coverage import (
    CoverageCandidate,
    evaluate_task_coverage,
)
from app.services.task_data_supply.discovery import (
    TaskSupplyStore,
)
from app.services.task_data_supply.requirement import (
    TaskScope,
    task_requirement,
)
from app.services.task_data_supply.supply_plan import (
    build_supply_plan,
)


def _square(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[
        [x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


# ── A-P0-1：geometry 声明的跨线任务（手算 oracle） ───────────────────────

class TestDatelineGeometryTask:
    def test_split_convention_geometry_full_coverage(self):
        # 任务：170°E..170°W × ±10（拆分约定顶点 170 与 -170）
        ring = [[170.0, -10.0], [-170.0, -10.0], [-170.0, 10.0],
                [170.0, 10.0], [170.0, -10.0]]
        req = task_requirement(
            TaskScope(geometry={"type": "Polygon", "coordinates": [ring]}),
            [{"role": "subject", "consumption_methods": ["numeric_stats"]}],
            provenance=[{"source": "oracle"}])
        # 候选 footprint 覆盖整带：169.5°E..169.5°W × ±10.5
        #（归一帧 169.5..190.5 ⊇ 任务 170..190，手算真值 1.0）
        fp = {"type": "Polygon", "coordinates": [[
            [169.5, -10.5], [-169.5, -10.5], [-169.5, 10.5], [169.5, 10.5],
            [169.5, -10.5]]]}
        cand = CoverageCandidate(
            candidate_id="dl/full", channel="local",
            serves_roles=("subject",),
            declared_bbox=[169.5, -10.5, -169.5, 10.5],
            footprint_geometry=fp)
        matrix = evaluate_task_coverage(req, [cand])
        ev = matrix.roles[0].evaluations[0]
        assert ev.verdict == "full", (ev.verdict, ev.spatial_ratio)
        assert ev.spatial_ratio is not None and ev.spatial_ratio > 0.99

    def test_split_convention_geometry_half_ratio(self):
        # 任务真带 170..190（宽 20°）；footprint 覆盖 175..185（真值 0.25）
        ring = [[170.0, -10.0], [-170.0, -10.0], [-170.0, 10.0],
                [170.0, 10.0], [170.0, -10.0]]
        req = task_requirement(
            TaskScope(geometry={"type": "Polygon", "coordinates": [ring]}),
            [{"role": "subject", "consumption_methods": ["numeric_stats"]}],
            provenance=[{"source": "oracle"}])
        fp = {"type": "Polygon", "coordinates": [[
            [175.0, -5.0], [-175.0, -5.0], [-175.0, 5.0], [175.0, 5.0],
            [175.0, -5.0]]]}
        cand = CoverageCandidate(
            candidate_id="dl/half", channel="local",
            serves_roles=("subject",), declared_bbox=[175.0, -10.0, -175.0, 10.0],
            footprint_geometry=fp)
        matrix = evaluate_task_coverage(req, [cand])
        ev = matrix.roles[0].evaluations[0]
        assert ev.verdict == "partial"
        assert abs(ev.spatial_ratio - 0.25) < 0.01


# ── A-P1-1 / A-P2-2：确认门补漏 ─────────────────────────────────────────

class TestConfirmationGates:
    def _req(self, **kw):
        return task_requirement(
            TaskScope(bbox=[104.0, 30.0, 105.0, 31.0],
                      time_range=["2024-01-01", "2024-12-31"]),
            [{"role": "subject", "consumption_methods": ["numeric_stats"],
              **kw}],
            provenance=[{"source": "gate"}])

    def test_temporal_mismatch_requires_confirmation(self):
        req = self._req()
        cand = CoverageCandidate(
            candidate_id="old/2010", channel="local",
            serves_roles=("subject",),
            declared_bbox=[104.0, 30.0, 105.0, 31.0],
            temporal_coverage=["2010-01-01", "2015-12-31"],
            footprint_geometry=_square(103.9, 29.9, 105.1, 31.1))
        plan = build_supply_plan(req, evaluate_task_coverage(req, [cand]),
                                 [cand])
        assert plan.state == "requires_confirmation"
        step = plan.step("subject")
        assert any("期次" in d or "时间" in d for d in step.disclosures)
        kinds = {a["kind"] for a in step.alternatives}
        assert {"keep_gap", "shrink_scope", "search_more", "upload"} <= kinds
        # 用户选择 keep_gap（期次缺口接受）→ ready
        plan2 = build_supply_plan(req, evaluate_task_coverage(req, [cand]),
                                  [cand], choices={"subject": "keep_gap"})
        assert plan2.state == "ready"

    def test_display_only_full_numeric_requires_confirmation(self):
        req = self._req()
        cand = CoverageCandidate(
            candidate_id="wms/full", channel="local",
            serves_roles=("subject",),
            declared_bbox=[104.0, 30.0, 105.0, 31.0],
            role_class="display_only",
            footprint_geometry=_square(103.9, 29.9, 105.1, 31.1))
        plan = build_supply_plan(req, evaluate_task_coverage(req, [cand]),
                                 [cand])
        assert plan.state == "requires_confirmation"

    def test_search_more_choice_accepted(self):
        req = self._req()
        plan = build_supply_plan(req, evaluate_task_coverage(req, []),
                                 [],
                                 choices={"subject": "search_more"})
        # clarify 步骤已决策 → 门通过（执行时该步骤类型化拒绝并给指引）
        assert plan.state == "ready"

    def test_bare_switch_rejected(self):
        req = self._req()
        with pytest.raises(ValueError,
                           match="SUPPLY_CHOICE_SWITCH_TARGET_REQUIRED"):
            build_supply_plan(req, evaluate_task_coverage(req, []), [],
                              choices={"subject": "switch"})


# ── A-P2-4/5/8、A-P3-8、A-P2-6 ──────────────────────────────────────────

class TestRequirementAndFitness:
    def test_mixed_date_datetime_no_typeerror(self):
        # date 与 datetime 混用不再 TypeError（归一可比；原缺陷是未捕获
        # TypeError，修复为归一 UTC 比较——合法输入不再炸）
        scope = TaskScope(time_range=["2024-01-01", "2024-06-30T00:00:00"])
        assert scope.time_range == ["2024-01-01", "2024-06-30T00:00:00"]
        with pytest.raises(ValueError, match="REQUIREMENT_TIME_INVALID"):
            TaskScope(time_range=["2024-06-30T00:00:00", "2024-01-01"])

    def test_aware_naive_mix_handled(self):
        # aware 与 naive 混用不再 TypeError（归一 UTC 比较）
        TaskScope(time_range=["2024-01-01T00:00:00+00:00",
                              "2024-06-30T12:00:00"])

    def test_requirement_id_order_insensitive_for_collections(self):
        r1 = task_requirement(
            TaskScope(place="成都"),
            [{"role": "subject", "required_fields": ("name", "grade"),
              "consumption_methods": ("numeric_stats", "density")}],
            provenance=[{"source": "x"}])
        r2 = task_requirement(
            TaskScope(place="成都"),
            [{"role": "subject", "required_fields": ("grade", "name"),
              "consumption_methods": ("density", "numeric_stats")}],
            provenance=[{"source": "x"}])
        assert r1.requirement_id == r2.requirement_id

    def test_latitude_out_of_range_rejected(self):
        with pytest.raises(ValueError, match="REQUIREMENT_SCOPE_INVALID"):
            TaskScope(bbox=[104.0, 95.0, 105.0, 99.0])

    def test_accessibility_gate_on_display_only(self):
        req = task_requirement(
            TaskScope(place="成都"),
            [{"role": "subject", "consumption_methods": (
                "accessibility", "geometry_skeleton", "map_display")}],
            provenance=[{"source": "g"}])
        cand = CoverageCandidate(
            candidate_id="wms/x", channel="local", serves_roles=("subject",),
            declared_bbox=[103.9, 30.5, 104.6, 31.0], role_class="display_only")
        ev = evaluate_task_coverage(req, [cand]).roles[0].evaluations[0]
        assert ev.fit_for_methods["accessibility"] is False
        assert ev.fit_for_methods["geometry_skeleton"] is False
        assert ev.fit_for_methods["map_display"] is True

    def test_evidence_change_changes_plan_id(self):
        req = task_requirement(
            TaskScope(bbox=[104.0, 30.0, 105.0, 31.0]),
            [{"role": "subject", "consumption_methods": ["numeric_stats"]}],
            provenance=[{"source": "g"}])
        full = CoverageCandidate(
            candidate_id="src/a", channel="local", serves_roles=("subject",),
            declared_bbox=[104.0, 30.0, 105.0, 31.0],
            footprint_geometry=_square(103.9, 29.9, 105.1, 31.1))
        partial = CoverageCandidate(
            candidate_id="src/a", channel="local", serves_roles=("subject",),
            declared_bbox=[104.0, 30.0, 105.0, 31.0],
            footprint_geometry=_square(104.0, 30.0, 104.5, 31.0))
        p1 = build_supply_plan(req, evaluate_task_coverage(req, [full]),
                               [full])
        p2 = build_supply_plan(req, evaluate_task_coverage(req, [partial]),
                               [partial])
        assert p1.plan_id != p2.plan_id


# ── B-P1-4 / B-P1-5：幂等与分桶存储 ──────────────────────────────────────

class TestIdempotencyAndStore:
    def test_reexecute_skips_completed_steps(self):
        from app.services.task_data_supply.supply_plan import SupplyPlanner

        req = task_requirement(
            TaskScope(bbox=[104.0, 30.0, 105.0, 31.0]),
            [{"role": "subject", "consumption_methods": ["numeric_stats"]}],
            provenance=[{"source": "g"}])
        cand = CoverageCandidate(
            candidate_id="ref:local", channel="local",
            serves_roles=("subject",), role_class="vector_features",
            declared_bbox=[104.0, 30.0, 105.0, 31.0],
            footprint_geometry=_square(103.9, 29.9, 105.1, 31.1))
        plan = build_supply_plan(req, evaluate_task_coverage(req, [cand]),
                                 [cand])
        assert plan.state == "ready"
        planner = SupplyPlanner()
        r1, _ = await_execute(plan, roles=None, planner=planner)
        assert r1[0]["outcome"] == "completed"
        r2, skipped = await_execute(plan, roles=None, planner=planner)
        assert r2 == []
        assert skipped and skipped[0]["role"] == "subject"
        assert skipped[0]["outcome"] == "completed"

    def test_store_per_session_eviction_isolation(self):
        store = TaskSupplyStore()

        def _plan_for(i):
            req = task_requirement(
                TaskScope(place=f"成都{i}"),
                [{"role": "subject", "consumption_methods": ["numeric_stats"]}],
                provenance=[{"source": "g"}])
            return build_supply_plan(req, evaluate_task_coverage(req, []), [])

        victim = _plan_for(0)
        store.save("sess-victim", victim)
        # 另一会话连建 15 个计划（超过全局旧上限 20 的一半）
        for i in range(15):
            store.save("sess-attacker", _plan_for(i))
        # 受害会话计划仍在（分桶隔离，不被驱逐）
        assert store.get("sess-victim", victim.plan_id) is not None
        # 攻击者自己只保留最近 10 个
        assert len(store.list_ids("sess-attacker")) == 10


def await_execute(plan, *, roles, planner):
    import asyncio

    from app.services.task_data_supply.execution import execute_plan_steps

    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        execute_plan_steps("sess-x", plan, roles=roles, planner=planner))


# ── B-P1-1/B-P1-2：fabric 执行解析与租户校验（sqlite 种子） ──────────────

class TestFabricExecution:
    @pytest.fixture()
    def fabric_env(self):
        from app.core.database import Base, Engine, SessionLocal
        from app.models.data_fabric import CatalogItemModel, DataSourceModel

        Base.metadata.create_all(bind=Engine)
        with SessionLocal() as db:
            db.query(CatalogItemModel).delete()
            db.query(DataSourceModel).delete()
            src = DataSourceModel(
                id="ds-tenant-a", org_id=None, owner_id="user-a",
                name="tenant a source", source_type="postgis",
                endpoint_url="postgresql://example/db",
                connection_profile={}, capabilities_json=[])
            db.add(src)
            db.add(CatalogItemModel(
                id="cat_ds-tenant-a_parcels", source_id="ds-tenant-a",
                name="parcels", title="parcels", geometry_type="Polygon",
                feature_type="vector", crs="EPSG:4326", bbox_json=None,
                tags_json=[], descriptor_json={}, meta_profile_json={},
                fingerprint="fp-x", availability="available"))
            db.commit()
        yield
        with SessionLocal() as db:
            db.query(CatalogItemModel).delete()
            db.query(DataSourceModel).delete()
            db.commit()

    def test_resolve_and_tenant_deny(self, fabric_env):
        from app.core.database import SessionLocal
        from app.services.task_data_supply.execution import (
            _resolve_fabric_catalog_item, _tenant_owns_source)
        from app.models.data_fabric import DataSourceModel

        with SessionLocal() as db:
            item = _resolve_fabric_catalog_item(
                db, "fabric:parcels", "ds-tenant-a")
            assert item is not None, "sync id 约定解析失败"
            src = db.query(DataSourceModel).filter(
                DataSourceModel.id == item.source_id).first()
            # 他人 → 拒绝；本人/同 org/全局 → 允许（与 _require_tenant_owned 同语义）
            assert _tenant_owns_source(src, "user-b", None) is False
            assert _tenant_owns_source(src, "user-a", None) is True
            assert _tenant_owns_source(src, "user-b", "org-a") is False
            assert _tenant_owns_source(src, None, None) is False

    def test_query_spec_projection(self):
        from app.services.task_data_supply.execution import (
            _query_spec_from_ads)

        spec = _query_spec_from_ads({
            "bbox": [104.0, 30.0, 105.0, 31.0],
            "columns": ["name", "grade"],
            "time_range": ["2024-01-01", "2024-12-31"],
            "sample_rate": 1.0,
        })
        assert spec.bbox == [104.0, 30.0, 105.0, 31.0]
        assert spec.columns == ["name", "grade"]
        assert spec.datetime_range == ["2024-01-01", "2024-12-31"]
        assert spec.limit >= 1000  # 有界执行帽，不静默 500

    def test_dateline_bbox_not_pushed(self):
        from app.services.task_data_supply.execution import (
            _query_spec_from_ads)

        spec = _query_spec_from_ads({"bbox": [170.0, -10.0, -170.0, 10.0]})
        assert spec.bbox is None  # 跨线形状不投给单 bbox 下推（已披露）


class TestSearchMoreOnAcquire:
    """R2 评审 A 新 P1 回归：acquire 步骤上 search_more 必须转 clarify，
    执行面类型化拒绝——绝不静默执行用户明确不取的候选。"""

    def _plan(self, choices):
        from app.services.task_data_supply.coverage import (
            CoverageCandidate, evaluate_task_coverage)

        req = task_requirement(
            TaskScope(bbox=[104.0, 30.0, 105.0, 31.0],
                      time_range=["2024-01-01", "2024-12-31"]),
            [{"role": "subject", "consumption_methods": ["numeric_stats"]}],
            provenance=[{"source": "r2"}])
        cand = CoverageCandidate(
            candidate_id="old/2010", channel="local",
            serves_roles=("subject",),
            declared_bbox=[104.0, 30.0, 105.0, 31.0],
            temporal_coverage=["2010-01-01", "2015-12-31"],
            footprint_geometry=_square(103.9, 29.9, 105.1, 31.1))
        return build_supply_plan(req, evaluate_task_coverage(req, [cand]),
                                 [cand], choices=choices)

    def test_search_more_converts_acquire_to_clarify(self):
        plan = self._plan({"subject": "search_more"})
        assert plan.state == "ready"
        step = plan.step("subject")
        assert step.action == "clarify"
        assert any("不获取" in d for d in step.disclosures)

    def test_execution_refuses_search_more_step(self):
        import asyncio

        from app.services.task_data_supply.coverage import (
            CoverageCandidate, evaluate_task_coverage)
        from app.services.task_data_supply.execution import execute_plan_steps

        req = task_requirement(
            TaskScope(bbox=[104.0, 30.0, 105.0, 31.0],
                      time_range=["2024-01-01", "2024-12-31"]),
            [{"role": "subject", "consumption_methods": ["numeric_stats"]}],
            provenance=[{"source": "r2"}])
        cand = CoverageCandidate(
            candidate_id="old/2010", channel="local",
            serves_roles=("subject",),
            declared_bbox=[104.0, 30.0, 105.0, 31.0],
            temporal_coverage=["2010-01-01", "2015-12-31"],
            footprint_geometry=_square(103.9, 29.9, 105.1, 31.1))
        plan = build_supply_plan(req, evaluate_task_coverage(req, [cand]),
                                 [cand], choices={"subject": "search_more"})
        loop = asyncio.get_event_loop_policy().new_event_loop()
        try:
            results, _skipped = loop.run_until_complete(
                execute_plan_steps("sess-r2", plan))
        finally:
            loop.close()
        assert results[0]["outcome"] == "failed"
        assert results[0]["code"] == "TASK_SUPPLY_STEP_NOT_EXECUTABLE"
        # 候选未被绑定/物化
        assert plan.step("subject").receipt_ref == ""


class TestR2Fixes:
    """R2 评审 B/C 修复回归：executing 楔死、REST 身份、fabric 端到端。"""

    def _fabric_plan(self):
        req = task_requirement(
            TaskScope(bbox=[104.0, 30.0, 105.0, 31.0]),
            [{"role": "subject", "consumption_methods": ["numeric_stats"]}],
            provenance=[{"source": "r2b"}])
        cand = CoverageCandidate(
            candidate_id="fabric:parcels", channel="data_fabric_catalog",
            serves_roles=("subject",), role_class="vector_features",
            declared_bbox=[104.0, 30.0, 105.0, 31.0],
            source="ds-tenant-a",
            footprint_geometry=_square(103.9, 29.9, 105.1, 31.1))
        return build_supply_plan(req, evaluate_task_coverage(req, [cand]),
                                 [cand])

    def _fabric_env(self):
        from app.core.database import Base, Engine, SessionLocal
        from app.models.data_fabric import (
            CatalogItemModel, DataSourceModel)

        Base.metadata.create_all(bind=Engine)
        with SessionLocal() as db:
            db.query(CatalogItemModel).delete()
            db.query(DataSourceModel).delete()
            db.add(DataSourceModel(
                id="ds-tenant-a", org_id=None, owner_id="user-a",
                name="tenant a source", source_type="postgis",
                endpoint_url="postgresql://example/db",
                connection_profile={}, capabilities_json=[]))
            db.add(CatalogItemModel(
                id="cat_ds-tenant-a_parcels", source_id="ds-tenant-a",
                name="parcels", title="parcels", geometry_type="Polygon",
                feature_type="vector", crs="EPSG:4326", bbox_json=None,
                tags_json=[], descriptor_json={}, meta_profile_json={},
                fingerprint="fp-x", availability="available"))
            db.commit()

    def _cleanup_env(self):
        from app.core.database import SessionLocal
        from app.models.data_fabric import (
            CatalogItemModel, DataSourceModel)

        with SessionLocal() as db:
            db.query(CatalogItemModel).delete()
            db.query(DataSourceModel).delete()
            db.commit()

    def test_fabric_end_to_end_resolve_tenant_materialize(self):
        """R2-B：解析→租户→物化三段接线（monkeypatch 物化，断言 item.id
        与 QuerySpec 投影真实传递）。"""
        import asyncio

        from app.services.data_fabric.manager import DataFabricManager
        from app.services.task_data_supply.execution import execute_plan_steps

        self._fabric_env()
        try:
            plan = self._fabric_plan()
            assert plan.state == "ready"
            captured = {}

            async def _fake_materialize(cls, db, session_id, *, item_id,
                                        query_spec=None, **kw):
                captured["item_id"] = item_id
                captured["spec"] = query_spec
                return {"ref_id": "ref:fabric-parquet/e2e",
                        "feature_count": 42}

            original = DataFabricManager.materialize_catalog_item
            DataFabricManager.materialize_catalog_item = classmethod(
                _fake_materialize)
            loop = asyncio.get_event_loop_policy().new_event_loop()
            try:
                results, _skipped = loop.run_until_complete(
                    execute_plan_steps(
                        "sess-fab", plan,
                        caller_identity=("user-a", None)))
            finally:
                loop.close()
                DataFabricManager.materialize_catalog_item = original
            assert results[0]["outcome"] == "completed", results
            assert results[0]["receipt_ref"] == "ref:fabric-parquet/e2e"
            assert results[0]["rows"] == 42  # feature_count 键读取（R2-B P3）
            assert captured["item_id"] == "cat_ds-tenant-a_parcels"
            assert captured["spec"].bbox == [104.0, 30.0, 105.0, 31.0]
            assert captured["spec"].limit >= 1000
            assert plan.step("subject").outcome == "completed"
        finally:
            self._cleanup_env()

    def test_fabric_tenant_deny_is_typed_failure(self):
        """他人身份 → 类型化失败，不泄漏跨租户条目存在性。"""
        import asyncio

        from app.services.data_fabric.manager import DataFabricManager
        from app.services.task_data_supply.execution import execute_plan_steps

        self._fabric_env()
        try:
            plan = self._fabric_plan()
            called = {"n": 0}

            async def _fail_materialize(*a, **kw):
                called["n"] += 1
                raise AssertionError("物化不应被调用（租户拒绝必须在前）")

            original = DataFabricManager.materialize_catalog_item
            DataFabricManager.materialize_catalog_item = classmethod(
                _fail_materialize)
            loop = asyncio.get_event_loop_policy().new_event_loop()
            try:
                results, _ = loop.run_until_complete(
                    execute_plan_steps(
                        "sess-fab", plan,
                        caller_identity=("user-b", None)))
            finally:
                loop.close()
                DataFabricManager.materialize_catalog_item = original
            assert called["n"] == 0
            assert results[0]["outcome"] == "failed"
            # 拒绝原因落在步骤披露（不泄漏跨租户条目存在性）
            step = plan.step("subject")
            assert any("不可见" in d for d in step.disclosures)
            assert step.receipt_ref == ""
        finally:
            self._cleanup_env()

    def test_roles_filtered_execution_does_not_wedge(self):
        """R2-B P1 回归：roles 过滤执行后计划保持可继续（不再楔死在
        executing，后续角色可执行）。"""
        import asyncio

        from app.services.task_data_supply.execution import execute_plan_steps
        from app.services.task_data_supply.supply_plan import SupplyPlanner

        async def _run():
            planner = SupplyPlanner()
            plan = self._two_role_plan()
            r1, _ = await execute_plan_steps(
                "s", plan, roles={"subject"},
                planner=planner, caller_identity=("u", None))
            assert r1[0]["outcome"] == "completed"
            r2, _ = await execute_plan_steps(
                "s", plan, roles={"boundary"},
                planner=planner, caller_identity=("u", None))
            assert r2[0]["outcome"] == "completed", (r2, plan.state)
            return plan

        plan = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_run())
        assert plan.state == "completed"

    def _two_role_plan(self):
        from app.services.task_data_supply.coverage import (
            CoverageCandidate, evaluate_task_coverage)

        req = task_requirement(
            TaskScope(bbox=[104.0, 30.0, 105.0, 31.0]),
            [{"role": "subject", "consumption_methods": ["numeric_stats"]},
             {"role": "boundary", "consumption_methods": [
                 "geometry_skeleton"]}],
            provenance=[{"source": "r2b"}])
        cands = [
            CoverageCandidate(
                candidate_id="ref:a", channel="local",
                serves_roles=("subject",), role_class="vector_features",
                declared_bbox=[104.0, 30.0, 105.0, 31.0],
                footprint_geometry=_square(103.9, 29.9, 105.1, 31.1)),
            CoverageCandidate(
                candidate_id="ref:b", channel="local",
                serves_roles=("boundary",), role_class="vector_features",
                declared_bbox=[104.0, 30.0, 105.0, 31.0],
                footprint_geometry=_square(103.9, 29.9, 105.1, 31.1)),
        ]
        return build_supply_plan(req, evaluate_task_coverage(req, cands),
                                 cands)
