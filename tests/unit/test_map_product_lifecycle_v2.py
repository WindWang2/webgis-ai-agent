"""Map Product lifecycle V2 (ADR-0099) — open / fork / merge / auto-record /
style-only restore semantics.

版本行不可变：所有生命周期操作都是新增行（append-only 证据）+ lineage
边。本套件锁定：
1. open 只读检视 + 恢复模式诚实降级（无快照 → style_only 不可用）；
2. fork 复制 provenance 且 parent/lineage 正确；
3. 受限合并：style-only × analysis-only 可合，同维冲突拒绝；
4. auto-record 幂等（同 run+指纹不双记）；
5. style-only restore 的五维 diff 机器证明（无分析重算）；
6. 生命周期 REST 合同（open/fork/merge/rerun 路由）。
"""
import uuid

import pytest

from app.core.database import Base, Engine, SessionLocal
from app.services.map_product_service import MapProductService


@pytest.fixture()
def lifecycle_project():
    from tests.unit.test_reproducible_gis_runtime import _PROJECT_DOMAIN_TABLES

    import app.models.db_model  # noqa: F401 — register metadata
    import app.models.project  # noqa: F401

    Base.metadata.create_all(bind=Engine, checkfirst=True)
    domain = [t for t in Base.metadata.sorted_tables if t.name in _PROJECT_DOMAIN_TABLES]
    for tbl in reversed(domain):
        tbl.drop(bind=Engine, checkfirst=True)
    for tbl in domain:
        tbl.create(bind=Engine, checkfirst=True)

    from app.models.db_model import User
    from app.models.project import Project

    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    base_manifest = {
        "steps": [
            {"step_id": "s1", "tool_name": "poi_query", "algorithm": "poi.query",
             "args": {"q": "小学"}},
            {"step_id": "s2", "tool_name": "admin_aggregate", "algorithm": "admin.aggregate",
             "args": {"by": "district"}},
        ],
        "artifacts": [{"id": "a1", "content_fingerprint": "out1"}],
    }
    with SessionLocal() as s:
        s.merge(User(id="u_lc", username="lc", email="lc@example.com",
                     password_hash="x", role="viewer", is_active=True))
        s.add(Project(id=project_id, name="lifecycle", owner_id="u_lc"))
        s.commit()
        # V1: base with snapshot
        v1 = MapProductService.record_version(
            s, project_id, mapspec_fingerprint="carto-v1", recipe_id="poi_distribution_overview",
            input_dataset_fingerprints={"ds1": "fpA"}, run_manifest=base_manifest,
            mapspec_snapshot={"version": "1.0", "layers": [{"id": "l1", "paint": {"color": "red"}}],
                              "layout": {"components": []}, "view": {"zoom": 9}},
            label="base",
        )
        # V2: style-only（同 compute + inputs，新 MapSpec 指纹 + 快照）
        v2 = MapProductService.record_version(
            s, project_id, mapspec_fingerprint="carto-v2",
            input_dataset_fingerprints={"ds1": "fpA"}, run_manifest=base_manifest,
            mapspec_snapshot={"version": "1.0", "layers": [{"id": "l1", "paint": {"color": "blue"}}],
                              "layout": {"components": []}, "view": {"zoom": 10}},
        )
        # V3: analysis-only（algorithm+parameter 变化，style 不动）
        v3 = MapProductService.record_version(
            s, project_id, mapspec_fingerprint="carto-v2",
            input_dataset_fingerprints={"ds1": "fpA"},
            run_manifest={
                "steps": [
                    {"step_id": "s1", "tool_name": "poi_query", "algorithm": "poi.query",
                     "args": {"q": "小学"}},
                    {"step_id": "s2", "tool_name": "admin_aggregate",
                     "algorithm": "admin.aggregate_v2", "args": {"by": "street"}},
                ],
                "artifacts": [{"id": "a1", "content_fingerprint": "out1"}],
            },
        )
        versions = (int(v1.version_no), int(v2.version_no), int(v3.version_no))
    return project_id, versions


# ── open ────────────────────────────────────────────────────────────────────


def test_open_reports_honest_restore_modes(lifecycle_project):
    project_id, (v1, _, _) = lifecycle_project
    with SessionLocal() as s:
        view = MapProductService.open_version(s, project_id, v1)
    assert view["version_no"] == v1
    assert view["snapshot_available"] is True
    modes = {m["mode"]: m for m in view["restore_modes"]}
    assert modes["style_only"]["available"] is True
    assert modes["full"]["available"] is False  # 无绑定 run → 诚实降级
    assert view["provenance"]["plan_steps"] == 2


def test_open_without_snapshot_degrades_style_restore(lifecycle_project):
    project_id, _ = lifecycle_project
    with SessionLocal() as s:
        v_no_snapshot = MapProductService.record_version(
            s, project_id, mapspec_fingerprint="carto-x",
            input_dataset_fingerprints={"ds1": "fpA"},
            run_manifest={"steps": [], "artifacts": []},
        ).version_no
        view = MapProductService.open_version(s, project_id, v_no_snapshot)
    assert view["snapshot_available"] is False
    modes = {m["mode"]: m for m in view["restore_modes"]}
    assert modes["style_only"]["available"] is False


def test_open_missing_version_raises(lifecycle_project):
    project_id, _ = lifecycle_project
    with SessionLocal() as s:
        with pytest.raises(ValueError):
            MapProductService.open_version(s, project_id, 999)


# ── fork ────────────────────────────────────────────────────────────────────


def test_fork_copies_provenance_and_records_lineage(lifecycle_project):
    project_id, (v1, _, _) = lifecycle_project
    with SessionLocal() as s:
        fork = MapProductService.fork_version(
            s, project_id, v1, label="branch-report")
        assert fork.parent_version_no == v1
        assert fork.lineage_kind == "fork"
        assert fork.label == "branch-report"
        assert fork.mapspec_fingerprint == "carto-v1"
        assert fork.mapspec_snapshot  # 快照随行
        # fork 行不改写历史
        src = MapProductService.get_version(s, project_id, v1)
        assert src.lineage_kind is None  # 原行未被触碰


# ── constrained merge ───────────────────────────────────────────────────────


def test_merge_style_with_analysis_succeeds(lifecycle_project):
    """V2(style-only vs V1) × V3(analysis-only vs V2) → 合法合并。"""
    project_id, (v1, v2, v3) = lifecycle_project
    with SessionLocal() as s:
        merged = MapProductService.merge_dimensions(s, project_id, v2, v3)
        assert merged.lineage_kind == "merge"
        # 计算身份来自分析侧（V3），表达指纹来自样式侧（V2）
        assert merged.mapspec_fingerprint == "carto-v2"
        plan_algos = {st.get("step_id"): st.get("algorithm") for st in merged.compute_plan}
        assert plan_algos["s2"] == "admin.aggregate_v2"
        assert merged.mapspec_snapshot  # 样式侧快照随行


def test_merge_conflicting_dimensions_refused(lifecycle_project):
    """V1 vs V3：style 与 analysis 同时变化 → 结构性冲突拒绝。"""
    project_id, (v1, _, v3) = lifecycle_project
    with SessionLocal() as s:
        with pytest.raises(ValueError, match="conflict|refused"):
            MapProductService.merge_dimensions(s, project_id, v1, v3)


def test_merge_identical_versions_refused(lifecycle_project):
    project_id, (v1, v2, v3) = lifecycle_project
    with SessionLocal() as s:
        # 记录一个与 V2 全同指纹的版本（时间线证据行），二者五维全同
        v4 = MapProductService.record_version(
            s, project_id, mapspec_fingerprint="carto-v2",
            input_dataset_fingerprints={"ds1": "fpA"},
            run_manifest={"steps": [
                {"step_id": "s1", "tool_name": "poi_query", "algorithm": "poi.query",
                 "args": {"q": "小学"}},
                {"step_id": "s2", "tool_name": "admin_aggregate",
                 "algorithm": "admin.aggregate_v2", "args": {"by": "street"}},
            ], "artifacts": [{"id": "a1", "content_fingerprint": "out1"}]},
        ).version_no
        with pytest.raises(ValueError, match="refused|identical"):
            MapProductService.merge_dimensions(s, project_id, v3, v4)


# ── auto-record idempotency ─────────────────────────────────────────────────


def _real_run(db, project_id, run_id=None):
    """真实 WorkflowRun 行（production 路径 run 一定落库）。"""
    from app.models.project import Workflow, WorkflowRun

    wf = db.execute(
        __import__("sqlalchemy").select(Workflow).where(
            Workflow.project_id == project_id)
    ).scalars().first()
    if wf is None:
        wf = Workflow(id=f"wf_{uuid.uuid4().hex[:8]}", project_id=project_id,
                      name="lc", version=1, graph_spec={"steps": []})
        db.add(wf)
        db.commit()
    run = WorkflowRun(
        id=run_id or f"run_{uuid.uuid4().hex[:8]}",
        workflow_id=wf.id, project_id=project_id, workflow_version=1,
        status="completed",
        run_manifest={
            "steps": [{"step_id": "s1", "capability": "poi_query",
                       "algorithm": "poi.query", "tool_name": "query_local_poi",
                       "args": {"q": "小学"}}],
            "artifacts": [{"id": "a1", "content_fingerprint": "out1"}],
            "outcome": {"mapspec_fingerprint": "carto-auto"},
        },
        input_dataset_fingerprints={"ds1": "fpA"},
    )
    db.add(run)
    db.commit()
    return run


def test_auto_record_idempotent(lifecycle_project):
    project_id, _ = lifecycle_project
    with SessionLocal() as s:
        run = _real_run(s, project_id)
        first = MapProductService.maybe_auto_record_version(s, run)
        assert first is not None and first.lineage_kind == "auto"
        # 幂等：同 run+指纹不双记（返回既有行）
        s.expire_all()
        again = MapProductService.maybe_auto_record_version(s, run)
        assert again.id == first.id
        rows, total = MapProductService.list_versions_paginated(s, project_id)
        autos = [r for r in rows if r.lineage_kind == "auto"]
        assert len(autos) == 1


def test_auto_record_skips_incomplete_runs(lifecycle_project):
    project_id, _ = lifecycle_project
    with SessionLocal() as s:
        run = _real_run(s, project_id)
        run.status = "failed"
        db_run = s.merge(run)
        assert MapProductService.maybe_auto_record_version(s, db_run) is None


# ── style-only restore（会话引擎走真实 lifecycle engine）────────────────────


def test_style_restore_records_style_only_proof(lifecycle_project):
    """style-only restore 落 restore 行，且其 diff 是 style-only 机器证明。

    会话态走内存 store（无 Redis 依赖）：先 init + upsert 一个带 paint 的
    层，再从 V2 快照恢复（paint 颜色变化）—— diff_summary 的
    analysis_recomputation_expected 必须为 False。
    """
    import asyncio

    project_id, (v1, v2, _) = lifecycle_project
    session_id = f"sess_{uuid.uuid4().hex[:10]}"

    async def _drive():
        from app.services.mapspec.lifecycle_engine import (
            InitProjectIntent,
            MapSpecLifecycleEngine,
            UpsertLayerIntent,
        )

        engine = MapSpecLifecycleEngine()
        await engine.apply_mutation(session_id, InitProjectIntent(view={"center": [104, 30], "zoom": 9}))
        await engine.apply_mutation(
            session_id,
            UpsertLayerIntent(
                layer={"id": "l1", "type": "circle", "source": "src1",
                       "paint": {"color": "red"}},
                source_data={"type": "geojson", "inlineData": {
                    "type": "FeatureCollection", "features": [
                        {"type": "Feature", "geometry": {"type": "Point",
                         "coordinates": [104, 30]}, "properties": {}}]}},
            ),
        )
        with SessionLocal() as s:
            result = await MapProductService.restore_style_to_session(
                s, project_id, v2, session_id=session_id, actor="u_lc")
        return result

    result = asyncio.run(_drive())
    assert result["mode"] == "style_only"
    assert result["source_version_no"] == v2
    # 机器证明：restore 行与来源版本计算身份逐位相同、零分析执行
    assert result["style_only_proof"]["compute_identity_preserved"] is True
    assert result["style_only_proof"]["analysis_executed"] is False
    with SessionLocal() as s:
        row = MapProductService.get_version(s, project_id, result["restored_version_no"])
        assert row.lineage_kind == "restore"
        assert row.parent_version_no == v2
        # restore 行的计算计划与来源版本一致（排序比较）
        from app.services.map_product_service import _project_steps
        assert (
            _project_steps({"steps": row.compute_plan or []}, sort=True)
            == _project_steps({"steps": MapProductService.get_version(
                s, project_id, v2).compute_plan or []}, sort=True)
        )


def test_style_restore_without_snapshot_refused(lifecycle_project):
    project_id, _ = lifecycle_project
    with SessionLocal() as s:
        v_no = MapProductService.record_version(
            s, project_id, mapspec_fingerprint="carto-nosnap",
            run_manifest={"steps": [], "artifacts": []}).version_no
    import asyncio

    with pytest.raises(ValueError, match="snapshot"):
        asyncio.run(MapProductService.restore_style_to_session(
            SessionLocal(), project_id, v_no, session_id="sess-x"))


# ── REST contract ───────────────────────────────────────────────────────────


@pytest.fixture()
def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def _auth(user_id="u_lc"):
    from app.core.auth import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        {"sub": user_id, "username": user_id, "role": "viewer"})}


def test_rest_open_and_fork(lifecycle_project, _client):
    project_id, (v1, _, _) = lifecycle_project
    r = _client.get(f"/api/v1/projects/{project_id}/map-products/{v1}/open",
                    headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["snapshot_available"] is True

    r2 = _client.post(f"/api/v1/projects/{project_id}/map-products/{v1}/fork",
                      json={"label": "report-branch"}, headers=_auth())
    assert r2.status_code == 201
    assert r2.json()["lineage_kind"] == "fork"


def test_rest_merge_conflict_maps_to_409(lifecycle_project, _client):
    project_id, (v1, _, v3) = lifecycle_project
    r = _client.post(
        f"/api/v1/projects/{project_id}/map-products/merge",
        json={"from_version_no": v1, "to_version_no": v3}, headers=_auth())
    assert r.status_code == 409
    assert "conflict" in r.json()["detail"] or "refused" in r.json()["detail"]


def test_rest_rerun_requires_bound_run(lifecycle_project, _client):
    project_id, (v1, _, _) = lifecycle_project
    r = _client.post(
        f"/api/v1/projects/{project_id}/map-products/{v1}/rerun", headers=_auth())
    assert r.status_code == 409  # 无绑定 run → 诚实拒绝
