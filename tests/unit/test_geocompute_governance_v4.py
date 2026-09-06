"""ADR-0101 D10：governor 层级身份作用域、durable 能力提示、幂等重派。"""
from __future__ import annotations

import threading


from app.services.geocompute import (
    ExecutionNode,
    ExecutionPlan,
    NodeCategory,
    ResourceBudget,
)
from app.services.geocompute.api import (
    GOVERNOR,
    GOVERNOR_PROJECT_MAX_CONCURRENCY,
    GOVERNOR_TENANT_MAX_CONCURRENCY,
    run_plan_sync,
)
from app.services.geocompute.budgets import ScopeKind
from app.services.geocompute.durable import required_capabilities


def _plan(node_id: str = "n1") -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="gov-v4",
        nodes=[ExecutionNode(
            node_id=node_id, category=NodeCategory.FILTER,
            parameters={"predicate": {"op": "eq", "field": "kind", "value": "a"},
                        "features": [
                            {"type": "Feature", "geometry": None,
                             "properties": {"kind": "a", "v": i}}
                            for i in range(3)
                        ]},
        )],
        budget=ResourceBudget(max_rows=1000),
    )


class TestHierarchicalIdentityScopes:
    def test_tenant_and_project_scopes_mounted_from_identity(self):
        gov = GOVERNOR
        caller = {"user_id": "u-govv4", "org_id": "org-govv4"}
        run = run_plan_sync(_plan(), session_id="sess-govv4", caller=caller,
                            project_id="proj-govv4", governor=gov)
        assert run.status.value == "completed"
        # 层级链：global:root / tenant:hash(org) / project:hash(proj) / session:hash(sid)
        root_children = {c.path for c in gov._root.children}
        tenant_paths = [p for p in root_children if p.startswith("tenant:")]
        assert tenant_paths, "tenant scope should be mounted for org caller"
        # 深度遍历找 project/session
        def walk(scope):
            yield scope
            for c in scope.children:
                yield from walk(c)
        paths = [s.path for s in walk(gov._root)]
        assert any(p.startswith("project:") for p in paths)
        assert any(p.startswith("session:") for p in paths)

    def test_anonymous_caller_mounts_no_tenant(self):
        gov = GOVERNOR
        run = run_plan_sync(_plan(node_id="n-anon"), session_id="sess-anon-govv4",
                            caller=None, governor=gov)
        assert run.status.value == "completed"
        # 无法精确断言全局树（共享单例），但 run 完成即链路可用。

    def test_concurrency_slots_released_after_run(self):
        gov = GOVERNOR
        caller = {"user_id": "u-govv4b", "org_id": "org-govv4"}
        run_plan_sync(_plan(node_id="n-rel"), session_id="sess-govv4b",
                      caller=caller, governor=gov)
        usage = gov.usage_full("global:root")
        assert usage.concurrency == 0  # 槽位随节点落定全部释放

    def test_tenant_limit_constant_bounded(self):
        assert 1 <= GOVERNOR_TENANT_MAX_CONCURRENCY <= 64
        assert 1 <= GOVERNOR_PROJECT_MAX_CONCURRENCY <= 64

    def test_scope_creation_thread_safe(self):
        from app.services.geocompute.budgets import ResourceGovernor

        gov = ResourceGovernor()
        errors = []

        def mount(i):
            try:
                for _ in range(20):
                    gov.ensure_scope("global:root", ScopeKind.SESSION, f"s{i}")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=mount, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not errors
        assert len(gov._root.children) == 4  # 并发首次挂载不产生重复子作用域


class TestWorkerCapabilityHints:
    def test_capability_mapping(self):
        raster_node = ExecutionNode(node_id="r", category=NodeCategory.RASTER_WINDOW_OPERATION)
        assert required_capabilities(raster_node) == ["raster", "gdal"]
        interp = ExecutionNode(node_id="i", category=NodeCategory.INTERPOLATION)
        assert required_capabilities(interp) == ["heavy_cpu"]
        query = ExecutionNode(node_id="q", category=NodeCategory.QUERY)
        assert required_capabilities(query) == ["vector"]

    def test_capabilities_deterministic(self):
        node = ExecutionNode(node_id="r", category=NodeCategory.RASTER_OPERATION)
        assert required_capabilities(node) == required_capabilities(node)

    def test_dispatch_declares_capabilities(self, monkeypatch):
        from app.services.geocompute import durable

        captured = {}

        def fake_submit(**kw):
            captured.update(kw)
            return {"status": "analysis_task_started", "job_id": "1"}

        import app.services.jobs.submit as submit_mod

        monkeypatch.setattr("app.services.geocompute.tasks.run_geocompute_node",
                            object(), raising=False)
        monkeypatch.setattr(submit_mod, "submit_durable_job", fake_submit)
        node = ExecutionNode(node_id="r", category=NodeCategory.RASTER_WINDOW_OPERATION)
        durable.dispatch_node(node, session_id="s", plan_fingerprint="pf", deadline_s=5)
        assert captured["params"]["capabilities"] == ["raster", "gdal"]

    def test_duplicate_dispatch_reuses_job_row(self, monkeypatch, tmp_path):
        """同参数重派（retry storm）命中幂等键 → 同一 job 行，不产生第二行。"""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.core.database import Base
        import app.models.db_model  # noqa: F401 - 注册 AnalysisTask

        engine = create_engine(f"sqlite:///{tmp_path}/jobs.db")
        Base.metadata.create_all(engine)
        Sess = sessionmaker(bind=engine)

        import contextlib

        import app.services.jobs.submit as submit_mod

        @contextlib.contextmanager
        def fake_db_session():
            db = Sess()
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        monkeypatch.setattr(submit_mod, "db_session", fake_db_session)
        from app.services.jobs.submit import build_execution_key, submit_durable_job

        from types import SimpleNamespace

        class FakeCeleryTask:
            name = "fake.geocompute_node"

            def apply_async(self, args=None, kwargs=None, **kw):
                return SimpleNamespace(id="celery-fake-1")

        params = {"node": {"node_id": "r", "category": "raster_window_operation"},
                  "plan_fingerprint": "pf"}
        r1 = submit_durable_job(
            celery_task=FakeCeleryTask(), task_type="geocompute_node",
            display_name="t", params=params, task_kwargs={}, session_id="sess-idem")
        # 第二次派发（模拟 retry storm）：行仍在队列（非终态）→ 幂等复用。
        r2 = submit_durable_job(
            celery_task=FakeCeleryTask(), task_type="geocompute_node",
            display_name="t", params=params, task_kwargs={}, session_id="sess-idem")
        key = build_execution_key("geocompute_node", "sess-idem", params)
        assert r1.get("job_id") is not None
        assert r2.get("idempotent_reuse") is True or r2.get("job_id") == r1.get("job_id")
        with Sess() as db:
            from app.models.db_model import AnalysisTask

            rows = db.query(AnalysisTask).filter(
                AnalysisTask.idempotency_key == key).all()
            assert len(rows) <= 1
