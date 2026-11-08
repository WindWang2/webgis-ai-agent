"""独立审查 C（R1）缺陷的针对性复现与回归测试。

每条对应审查报告里的一个问题；修复后必须转绿，且不得靠改阈值/跳过用例。
"""
from __future__ import annotations

import copy

import pytest

from app.services.project_runtime.refresh.contracts import (
    ChangeEvidence,
    EvidenceLevel,
    FeatureIdKind,
    MAX_SNAPSHOT_FEATURES,
    SourceSnapshot,
)
from app.services.project_runtime.refresh.diff import compute_source_diff
from app.services.project_runtime.refresh.execution import (
    InMemoryMapSpecPort,
    verify_no_drift,
)
from app.services.project_runtime.refresh.impact import build_refresh_plan
from app.services.project_runtime.refresh.merge import three_way_merge
from app.services.project_runtime.refresh.records import RefreshProposal
from app.services.project_runtime.refresh.service import ProjectDataRefreshService
from app.services.project_runtime.refresh.sources import StaticPayloadProvider
from app.services.project_runtime.refresh.store import (
    RefreshStore,
    reset_refresh_store,
)

DAG = {
    "nodes": [{"node_id": n, "parameters": []} for n in (
        "data:schools", "data:population", "analysis:heat",
        "analysis:normalized", "output:legend", "output:map")],
    "edges": [
        {"from": "data:schools", "to": "analysis:heat"},
        {"from": "data:population", "to": "analysis:normalized"},
        {"from": "analysis:heat", "to": "output:map"},
        {"from": "analysis:normalized", "to": "output:map"},
    ],
}


def _snap(n: int, *, version: str = "c1", crs: str = "EPSG:4326",
          unit: str = "person") -> SourceSnapshot:
    return SourceSnapshot(
        logical_id="ds://schools", version_token=version,
        evidence=EvidenceLevel.CONTENT_EXACT,
        fingerprints={"content": version, "schema": f"schema:{unit}",
                      "metadata": "m0", "crs": crs},
        schema_fields={"students": f"int|{unit}"}, crs=crs,
        bbox=[103.9, 30.5, 104.2, 30.8],
        features={f"s{i}": {"properties": {"students": i},
                            "geometry": "POINT(1 1)"} for i in range(n)},
        feature_id_kind=FeatureIdKind.STABLE,
        analysis_roles=["data:schools"],
    )


# ── R1-P1-1：静默截断不得产生假精确 / silently unchanged ───────────

def test_r1_p1_1_truncation_is_flagged_and_never_fakes_exact_counts():
    base = _snap(MAX_SNAPSHOT_FEATURES + 44, version="c1")
    cur = _snap(MAX_SNAPSHOT_FEATURES + 44, version="c2")
    # Pydantic 有界截断必须自动置位
    assert len(base.features) == MAX_SNAPSHOT_FEATURES
    assert base.truncated is True and base.sampled is True
    assert len(cur.features) == MAX_SNAPSHOT_FEATURES
    assert cur.truncated is True

    diff = compute_source_diff(baseline=base, current=cur)
    assert diff.evidence is not ChangeEvidence.EXACT
    assert diff.exact_counts is None
    assert diff.truncated is True
    assert diff.seed_targets, "must never produce an empty seed set for a real change"

    plan = build_refresh_plan(diffs=[diff], dag=DAG)
    assert plan.conservative is True
    assert len(plan.recompute) == len(DAG["nodes"])
    assert plan.reuse == []


def test_r1_p1_1_small_exact_case_still_precise():
    base, cur = _snap(5, version="c1"), _snap(5, version="c2")
    cur.features["s5"] = {"properties": {"students": 5}, "geometry": "POINT(1 1)"}
    diff = compute_source_diff(baseline=base, current=cur)
    assert diff.evidence is ChangeEvidence.EXACT
    assert diff.exact_counts.added == 1
    assert diff.truncated is False


# ── R1-P1-2：客户端 style_only 不得跳过真实差异 ─────────────────────

def test_r1_p1_2_style_only_cannot_suppress_real_data_changes():
    base, cur = _snap(5, version="c1"), _snap(5, version="c2")
    cur.features["s5"] = {"properties": {"students": 5}, "geometry": "POINT(1 1)"}
    diff = compute_source_diff(baseline=base, current=cur)
    plan = build_refresh_plan(diffs=[diff], dag=DAG, style_only=True)
    assert plan.style_only is False
    assert plan.recompute, "real data change must still recompute"
    assert any("style_only request ignored" in w for w in plan.warnings)


def test_r1_p1_2_genuine_style_only_still_zero_recompute():
    plan = build_refresh_plan(diffs=[], dag=DAG, style_only=True)
    assert plan.style_only is True and plan.recompute == []
    assert plan.proposal_fingerprint.startswith("g10:")


# ── R1-P2：漂移检查空值 fail-closed ────────────────────────────────

def test_r1_p2_empty_owner_and_missing_fingerprint_fail_closed():
    prop = RefreshProposal(
        proposal_id="g10p-x", project_id="proj-x", owner_id="",
        mapspec_fingerprint="", mapspec_revision=3,
        proposal_fingerprint="g10:abc",
    )
    drift = verify_no_drift(
        proposal=prop, current_versions={}, current_revision=3,
        current_mapspec_fingerprint="fp", owner_id="intruder",
    )
    assert "owner_permission_changed" in drift
    assert any("mapspec" in d for d in drift)

    prop.owner_id = "owner"
    prop.mapspec_fingerprint = "fp"
    assert verify_no_drift(
        proposal=prop, current_versions={}, current_revision=3,
        current_mapspec_fingerprint="fp", owner_id="owner",
    ) == []


# ── R1-P2：候选正常删除图层不应一律阻断采纳 ────────────────────────

def test_r1_p2_normal_layer_removal_is_not_a_conflict():
    base = {"layers": [{"id": "L1", "paint": {"c": 1}},
                       {"id": "L2", "paint": {"c": 2}}],
            "components": [], "workbench": {}}
    current = copy.deepcopy(base)
    candidate = {"layers": [{"id": "L1", "paint": {"c": 1}}],
                 "components": [], "workbench": {}}
    result = three_way_merge(baseline=base, current=current, candidate=candidate)
    assert result.conflicts == []
    assert result.adoptable is True

    # 但用户给被删层挂了标注 → 必须显式冲突
    result2 = three_way_merge(baseline=base, current=current, candidate=candidate,
                              annotation_targets={"L2": "name"})
    assert any(c.kind == "deleted_annotation_target" for c in result2.conflicts)
    assert result2.adoptable is False


# ── R1-P1-3 / P2：硬语义错误、回退不覆盖用户后续编辑 ───────────────

@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    reset_refresh_store()
    yield
    reset_refresh_store()


@pytest.fixture
def db():
    from app.core.database import Base, Engine, SessionLocal
    from app.models.project import Project, ProjectDataset

    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as s:
        s.query(ProjectDataset).filter(ProjectDataset.id == "ds-r1").delete()
        s.query(Project).filter(Project.id == "proj-r1").delete()
        s.add(Project(id="proj-r1", name="r1", owner_id="u-r1", status="active"))
        s.add(ProjectDataset(id="ds-r1", project_id="proj-r1", name="schools",
                             source_type="upload", source_ref="ref-r1",
                             version_fingerprint="fp-v1", crs="EPSG:4326"))
        s.commit()
        yield s


def _payload(version: int, *, crs: str = "EPSG:4326", unit: str = "person") -> dict:
    return {"descriptor": {
        "content_hash": f"h{version}", "content_revision": version,
        "crs": crs, "bbox": [103.9, 30.5, 104.2, 30.8],
        "field_schema": {"name": {"type": "str"},
                         "students": {"type": "int", "unit": unit}},
        "field_schema_complete": True, "estimated_bytes": 1024,
        "feature_count": 3},
        "payload": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": f"s{i}",
             "properties": {"name": f"s{i}", "students": i},
             "geometry": "POINT(1 1)"} for i in range(3)]}}


def _svc(payload: dict) -> ProjectDataRefreshService:
    return ProjectDataRefreshService(
        store=RefreshStore(),
        port=InMemoryMapSpecPort(
            {"title": "t", "layers": [{"id": "L1", "source": "ref://schools"}],
             "components": [], "workbench": {}}, revision=3),
        payload_provider=StaticPayloadProvider({"ref-r1": payload}),
    )


@pytest.mark.asyncio
async def test_r1_p1_3_single_unit_change_blocks_adoption(db):
    svc = _svc(_payload(1))
    await svc.check_updates(db, project_id="proj-r1", session_id="sess-r1",
                            user_id="u-r1")
    # 仅单位变化（CRS 不变）
    svc.payload_provider = StaticPayloadProvider(
        {"ref-r1": _payload(2, unit="thousand_person")})
    proposal = await svc.preview(db, project_id="proj-r1", session_id="sess-r1",
                                 user_id="u-r1", dag=DAG)
    result = await svc.execute(db, project_id="proj-r1",
                               proposal_id=proposal.proposal_id, user_id="u-r1")
    candidate = result["candidate"]
    assert candidate.hard_semantic_errors, "single unit change must be a hard error"
    assert candidate.adoptable is False


@pytest.mark.asyncio
async def test_r1_p2_rollback_refuses_to_overwrite_post_adoption_user_edits(db):
    svc = _svc(_payload(1))
    await svc.check_updates(db, project_id="proj-r1", session_id="sess-r1",
                            user_id="u-r1")
    p2 = _payload(2)
    p2["payload"]["features"].append(
        {"type": "Feature", "id": "s3", "properties": {"name": "s3", "students": 3},
         "geometry": "POINT(1 1)"})
    svc.payload_provider = StaticPayloadProvider({"ref-r1": p2})
    proposal = await svc.preview(db, project_id="proj-r1", session_id="sess-r1",
                                 user_id="u-r1", dag=DAG)
    await svc.execute(db, project_id="proj-r1",
                      proposal_id=proposal.proposal_id, user_id="u-r1")
    adopted = await svc.adopt(db, project_id="proj-r1",
                              proposal_id=proposal.proposal_id, user_id="u-r1")
    assert adopted.status == "adopted"

    # 采纳之后用户又编辑了 MapSpec
    await svc.port.commit("sess-r1", {"title": "user edit after adoption",
                                      "layers": [], "components": [],
                                      "workbench": {}},
                          expected_revision=adopted.revision_after,
                          mutation_id="user-edit")
    rollback = await svc.rollback(project_id="proj-r1",
                                  proposal_id=proposal.proposal_id, user_id="u-r1")
    assert rollback.status == "blocked"
    assert any("user edits after adoption" in w for w in rollback.warnings)
    assert rollback.version_adoption == "unchanged"


# ── R1-P2：路径白名单 / 生产 MapSpec CAS 真实覆盖 ──────────────────

def test_r1_p2_store_rejects_path_traversal_ids():
    store = RefreshStore()
    assert store.load_proposal("proj-r1", "../../etc/passwd") is None
    assert store.load_candidate_doc("proj-r1", "../x") is None
    assert store.load_doc("proj-r1", "a/b") is None
    ok, holder = store.acquire_lease("proj-r1", "../../escape")
    assert ok is False and "invalid lease key" in str(holder)


@pytest.mark.asyncio
async def test_r1_p2_production_mapspec_port_cas_and_revision_monotonic():
    """真实 SessionMapSpecPort + MapSpecStore.save_mapspec（非内存端口）。"""
    from app.services.mapspec.store import mapspec_store_instance
    from app.services.project_runtime.refresh.execution import SessionMapSpecPort

    sid = "sess-g10-prod-cas"
    await mapspec_store_instance.save_mapspec(
        sid, {"title": "base", "layers": []}, mutation_revision=1)
    port = SessionMapSpecPort()
    _doc, rev = await port.read(sid)
    assert rev == 1

    first = await port.commit(
        sid, {"title": "candidate", "layers": [{"id": "L1"}]},
        expected_revision=1, mutation_id="g10-cas-1")
    assert first["status"] == "committed", first
    assert first["revision_after"] == 2
    assert first["revision_before"] == 1

    stale = await port.commit(
        sid, {"title": "stale", "layers": []},
        expected_revision=1, mutation_id="g10-cas-2")
    assert stale["status"] == "superseded"
    doc, rev = await port.read(sid)
    assert rev == 2 and doc.get("title") == "candidate"
