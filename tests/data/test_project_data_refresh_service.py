"""G10 服务层集成测试（DI：真实 SQLite DB + 合成 payload + 真实编排/CAS 代码）。

模式说明：payload 用本地合成数据（契约允许「缺少合法凭证时使用本地合成
数据和真实生产代码路径」），但差异判定、影响预览、三方合并、漂移校验、
lease、CAS 采纳全部走**生产代码**，不是 mock 返回值。
"""
from __future__ import annotations

import copy

import pytest

from app.lib.data.fingerprints import canonical_fingerprint
from app.services.project_runtime.refresh.contracts import ChangeEvidence
from app.services.project_runtime.refresh.execution import InMemoryMapSpecPort
from app.services.project_runtime.refresh.service import (
    ProjectDataRefreshService,
    RefreshError,
)
from app.services.project_runtime.refresh.sources import StaticPayloadProvider
from app.services.project_runtime.refresh.store import (
    RefreshStore,
    reset_refresh_store,
)

PROJECT_ID = "proj-g10"
SESSION_ID = "sess-g10"
USER_ID = "u-g10"
DATASET_ID = "ds-g10"
SOURCE_REF = "ref-g10"
LOGICAL_ID = f"dataset:{DATASET_ID}"


def _feature(fid: str, name: str, students: int, geom: str = "POINT(1 1)") -> dict:
    return {"type": "Feature", "id": fid,
            "properties": {"name": name, "students": students},
            "geometry": geom}


def _payload(version: int, features: list[dict], *, students_unit: str = "person") -> dict:
    return {
        "descriptor": {
            "content_hash": f"hash-v{version}",
            "content_revision": version,
            "crs": "EPSG:4326",
            "bbox": [103.9, 30.5, 104.2, 30.8],
            "field_schema": {
                "name": {"type": "str"},
                "students": {"type": "int", "unit": students_unit},
            },
            "field_schema_complete": True,
            "estimated_bytes": 2048,
            "feature_count": len(features),
        },
        "payload": {"type": "FeatureCollection", "features": features},
    }


def _mapspec() -> dict:
    return {
        "title": "学校分布 2024",
        "legend": {"position": "bottom-left", "visible": True},
        "layers": [
            {"id": "L1", "source": "ref://schools-v1", "type": "circle",
             "paint": {"circle-color": "#3388ff", "circle-opacity": 0.8},
             "visible": True},
            {"id": "L2", "source": "ref://heat-v1", "type": "heatmap",
             "visible": True},
        ],
        "components": [{"id": "C1", "type": "text", "text": "遂宁校区",
                        "position": {"x": 10, "y": 20}}],
        "workbench": {"lockedLayerIds": [], "lockedComponentIds": []},
    }


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
        s.query(ProjectDataset).filter(ProjectDataset.id == DATASET_ID).delete()
        s.query(Project).filter(Project.id == PROJECT_ID).delete()
        s.add(Project(id=PROJECT_ID, name="g10-corpus", owner_id=USER_ID,
                      status="active"))
        s.add(ProjectDataset(
            id=DATASET_ID, project_id=PROJECT_ID, name="schools",
            source_type="upload", source_ref=SOURCE_REF, crs="EPSG:4326",
            version_fingerprint="fp-v1",
        ))
        s.commit()
        yield s


def _service(provider: StaticPayloadProvider, doc: dict, revision: int = 3,
             store: RefreshStore | None = None) -> ProjectDataRefreshService:
    return ProjectDataRefreshService(
        store=store or RefreshStore(),
        port=InMemoryMapSpecPort(copy.deepcopy(doc), revision),
        payload_provider=provider,
    )


BASE_FEATURES = [
    _feature("s1", "A", 100), _feature("s2", "B", 200),
    _feature("s3", "C", 300), _feature("s4", "D", 400),
    _feature("s5", "E", 500),
]


def _dag() -> dict:
    return {
        "nodes": [
            {"node_id": "data:schools", "parameters": []},
            {"node_id": "data:population", "parameters": []},
            {"node_id": "analysis:heat", "parameters": []},
            {"node_id": "analysis:normalized", "parameters": []},
            {"node_id": "output:legend", "parameters": []},
            {"node_id": "output:map", "parameters": []},
        ],
        "edges": [
            {"from": "data:schools", "to": "analysis:heat"},
            {"from": "data:population", "to": "analysis:normalized"},
            {"from": "analysis:heat", "to": "output:map"},
            {"from": "analysis:normalized", "to": "output:map"},
        ],
    }


# ── O01 / J1：首次建立基线 + 后续检查变化 ──────────────────────────

@pytest.mark.asyncio
async def test_o01_first_check_establishes_baseline_without_change_claim(db):
    svc = _service(StaticPayloadProvider({SOURCE_REF: _payload(1, BASE_FEATURES)}),
                   _mapspec())
    receipt = await svc.check_updates(
        db, project_id=PROJECT_ID, session_id=SESSION_ID, user_id=USER_ID,
    )
    assert receipt.sources_total == 1
    assert receipt.changed == 0
    assert receipt.stale is False
    assert any("baseline" in w.lower() for w in receipt.warnings)
    assert svc.store.load_baselines(PROJECT_ID) is not None
    assert receipt.inventory.items[0].logical_id == LOGICAL_ID
    assert receipt.inventory.items[0].evidence.value == "content_exact"


@pytest.mark.asyncio
async def test_o01_change_detected_with_stable_logical_identity(db):
    svc = _service(StaticPayloadProvider({SOURCE_REF: _payload(1, BASE_FEATURES)}),
                   _mapspec())
    await svc.check_updates(db, project_id=PROJECT_ID, session_id=SESSION_ID,
                            user_id=USER_ID)

    v2 = BASE_FEATURES[:-1] + [
        _feature("s6", "F", 600), _feature("s7", "G", 700),
    ]
    svc.payload_provider = StaticPayloadProvider({SOURCE_REF: _payload(2, v2)})
    receipt = await svc.check_updates(db, project_id=PROJECT_ID,
                                      session_id=SESSION_ID, user_id=USER_ID,
                                      establish_baseline=False)
    assert receipt.changed == 1
    assert receipt.stale is True
    assert receipt.inventory.items[0].logical_id == LOGICAL_ID  # 身份稳定
    assert receipt.inventory.items[0].baseline_version == "hash-v1"
    assert receipt.inventory.items[0].current_version == "hash-v2"


# ── O02 / O05 / J3：预览精确差异且零写入 ────────────────────────────

@pytest.mark.asyncio
async def test_o05a_preview_exact_diff_and_zero_write(db):
    svc = _service(StaticPayloadProvider({SOURCE_REF: _payload(1, BASE_FEATURES)}),
                   _mapspec())
    await svc.check_updates(db, project_id=PROJECT_ID, session_id=SESSION_ID,
                            user_id=USER_ID)
    v2 = [
        _feature("s1", "A", 100), _feature("s2", "B", 200),
        _feature("s3", "C", 999),
        _feature("s4", "D", 400, "POINT(2 2)"),
        _feature("s6", "F", 600), _feature("s7", "G", 700),
    ]
    svc.payload_provider = StaticPayloadProvider({SOURCE_REF: _payload(2, v2)})

    before_doc, before_rev = await svc.port.read(SESSION_ID)
    before_fp = canonical_fingerprint(before_doc)
    before_baselines = svc.store.load_baselines(PROJECT_ID)

    proposal = await svc.preview(db, project_id=PROJECT_ID, session_id=SESSION_ID,
                                 user_id=USER_ID, dag=_dag())
    assert proposal.diffs[0].evidence is ChangeEvidence.EXACT
    assert proposal.diffs[0].exact_counts.added == 2
    assert proposal.diffs[0].exact_counts.removed == 1
    assert proposal.diffs[0].exact_counts.attribute_changed == 1
    assert proposal.diffs[0].exact_counts.geometry_changed == 1
    assert "data:schools" in proposal.plan.recompute
    assert "analysis:normalized" not in proposal.plan.recompute
    assert proposal.plan.conservative is False

    after_doc, after_rev = await svc.port.read(SESSION_ID)
    assert canonical_fingerprint(after_doc) == before_fp
    assert after_rev == before_rev == 3
    assert svc.store.load_baselines(PROJECT_ID) == before_baselines
    assert proposal.proposal_fingerprint.startswith("g10:")


@pytest.mark.asyncio
async def test_o05b_version_drift_after_preview_is_rejected(db):
    svc = _service(StaticPayloadProvider({SOURCE_REF: _payload(1, BASE_FEATURES)}),
                   _mapspec())
    await svc.check_updates(db, project_id=PROJECT_ID, session_id=SESSION_ID,
                            user_id=USER_ID)
    svc.payload_provider = StaticPayloadProvider({SOURCE_REF: _payload(2, BASE_FEATURES)})
    proposal = await svc.preview(db, project_id=PROJECT_ID, session_id=SESSION_ID,
                                 user_id=USER_ID, dag=_dag())

    # 预览后来源又更新一代
    svc.payload_provider = StaticPayloadProvider({SOURCE_REF: _payload(3, BASE_FEATURES)})
    with pytest.raises(RefreshError) as exc:
        await svc.execute(db, project_id=PROJECT_ID,
                          proposal_id=proposal.proposal_id, user_id=USER_ID)
    assert exc.value.code == "proposal_drift"
    assert "source_version_set_changed" in exc.value.detail
    stored = svc.store.load_proposal(PROJECT_ID, proposal.proposal_id)
    assert stored.status.value == "drifted"


@pytest.mark.asyncio
async def test_o05c_mapspec_revision_drift_is_rejected(db):
    svc = _service(StaticPayloadProvider({SOURCE_REF: _payload(1, BASE_FEATURES)}),
                   _mapspec())
    await svc.check_updates(db, project_id=PROJECT_ID, session_id=SESSION_ID,
                            user_id=USER_ID)
    svc.payload_provider = StaticPayloadProvider({SOURCE_REF: _payload(2, BASE_FEATURES)})
    proposal = await svc.preview(db, project_id=PROJECT_ID, session_id=SESSION_ID,
                                 user_id=USER_ID, dag=_dag())
    # 预览后用户手工编辑推进 revision
    await svc.port.commit(SESSION_ID, {**_mapspec(), "title": "人工改标题"},
                          expected_revision=3, mutation_id="user-edit")
    with pytest.raises(RefreshError) as exc:
        await svc.execute(db, project_id=PROJECT_ID,
                          proposal_id=proposal.proposal_id, user_id=USER_ID)
    assert "mapspec" in exc.value.detail


# ── O06 / O07 / O08：候选、user-wins、幂等、采纳、回退 ──────────────

async def _prepared(svc, db, *, v2_features=None):
    await svc.check_updates(db, project_id=PROJECT_ID, session_id=SESSION_ID,
                            user_id=USER_ID)
    features = v2_features or (BASE_FEATURES[:-1] + [_feature("s6", "F", 600)])
    svc.payload_provider = StaticPayloadProvider({SOURCE_REF: _payload(2, features)})
    return await svc.preview(db, project_id=PROJECT_ID, session_id=SESSION_ID,
                             user_id=USER_ID, dag=_dag())


@pytest.mark.asyncio
async def test_o06_o07_o08_candidate_preserves_edits_is_idempotent_and_atomic(db):
    user_doc = _mapspec()
    user_doc["layers"][0]["paint"]["circle-color"] = "#e11d48"
    user_doc["layers"][1]["visible"] = False
    user_doc["legend"]["position"] = "top-right"
    user_doc["workbench"]["lockedLayerIds"] = ["L1"]
    svc = _service(StaticPayloadProvider({SOURCE_REF: _payload(1, BASE_FEATURES)}),
                   user_doc)

    proposal = await _prepared(svc, db)
    visible_before, rev_before = await svc.port.read(SESSION_ID)
    fp_before = canonical_fingerprint(visible_before)

    run1 = await svc.execute(db, project_id=PROJECT_ID,
                             proposal_id=proposal.proposal_id, user_id=USER_ID)
    assert run1["replayed"] is False
    assert run1["receipt"]["version_adoption"] == "unchanged"
    assert run1["receipt"]["map_observation"] == "unverified"
    # 候选执行不污染当前可见地图
    fp_after_exec, rev_after_exec = await svc.port.read(SESSION_ID)
    assert canonical_fingerprint(fp_after_exec) == fp_before
    assert rev_after_exec == rev_before == 3

    run2 = await svc.execute(db, project_id=PROJECT_ID,
                             proposal_id=proposal.proposal_id, user_id=USER_ID)
    assert run2["replayed"] is True
    assert run2["candidate"].attempts == 1      # 未重复昂贵步骤

    candidate_doc = svc.store.load_candidate_doc(
        PROJECT_ID, f"g10c-{proposal.proposal_id}")
    assert candidate_doc is not None
    layers = {x["id"]: x for x in candidate_doc["layers"]}
    assert layers["L1"]["paint"]["circle-color"] == "#e11d48"   # 颜色保留
    assert layers["L2"]["visible"] is False                       # 隐藏保留
    assert candidate_doc["legend"]["position"] == "top-right"     # 组件位置保留
    assert "L1" in candidate_doc["workbench"]["lockedLayerIds"]   # 族锁保留
    assert layers["L1"].get("data_binding", {}).get("version_token") == "hash-v2"

    receipt = await svc.adopt(db, project_id=PROJECT_ID,
                              proposal_id=proposal.proposal_id, user_id=USER_ID)
    assert receipt.status == "adopted"
    assert receipt.version_adoption == "adopted"
    assert receipt.revision_after == 4 and receipt.revision_before == 3
    assert receipt.map_observation == "unverified"   # 不伪造 renderer ACK
    visible, rev = await svc.port.read(SESSION_ID)
    assert rev == 4
    assert canonical_fingerprint(visible) == receipt.mapspec_fingerprint

    replay = await svc.adopt(db, project_id=PROJECT_ID,
                             proposal_id=proposal.proposal_id, user_id=USER_ID)
    assert replay.replayed is True and replay.status == "adopted"
    assert (await svc.port.read(SESSION_ID))[1] == 4   # 幂等，revision 不重复 +1

    rollback = await svc.rollback(project_id=PROJECT_ID,
                                  proposal_id=proposal.proposal_id,
                                  user_id=USER_ID)
    assert rollback.status == "rolled_back"
    assert rollback.revision_after == 5             # 前向回退 = 新 revision
    assert rollback.map_observation == "unverified"
    restored, final_rev = await svc.port.read(SESSION_ID)
    assert canonical_fingerprint(restored) == fp_before
    assert final_rev == 5


@pytest.mark.asyncio
async def test_o07_cancel_and_failure_leave_current_intact(db):
    svc = _service(StaticPayloadProvider({SOURCE_REF: _payload(1, BASE_FEATURES)}),
                   _mapspec())
    proposal = await _prepared(svc, db)
    before, rev = await svc.port.read(SESSION_ID)
    fp = canonical_fingerprint(before)

    cancelled = await svc.execute(db, project_id=PROJECT_ID,
                                  proposal_id=proposal.proposal_id,
                                  user_id=USER_ID, cancel=True)
    assert cancelled["receipt"]["task_execution"] == "failed"
    after, after_rev = await svc.port.read(SESSION_ID)
    assert canonical_fingerprint(after) == fp and after_rev == rev

    # 取消后仍可重新执行（不留下半新半旧状态）
    assert await svc.execute(db, project_id=PROJECT_ID,
                             proposal_id=proposal.proposal_id,
                             user_id=USER_ID) is not None


@pytest.mark.asyncio
async def test_o09_discard_releases_candidate_pin(db):
    svc = _service(StaticPayloadProvider({SOURCE_REF: _payload(1, BASE_FEATURES)}),
                   _mapspec())
    proposal = await _prepared(svc, db)
    cid = f"g10c-{proposal.proposal_id}"
    await svc.execute(db, project_id=PROJECT_ID,
                      proposal_id=proposal.proposal_id, user_id=USER_ID)
    assert svc.store.load_candidate_doc(PROJECT_ID, cid) is not None

    receipt = await svc.discard(project_id=PROJECT_ID,
                                proposal_id=proposal.proposal_id, user_id=USER_ID)
    assert receipt.status == "discarded"
    assert svc.store.load_candidate_doc(PROJECT_ID, cid) is None
    assert any("GC pin" in w for w in receipt.warnings)


@pytest.mark.asyncio
async def test_o01_cross_owner_proposal_access_is_refused(db):
    svc = _service(StaticPayloadProvider({SOURCE_REF: _payload(1, BASE_FEATURES)}),
                   _mapspec())
    proposal = await _prepared(svc, db)
    with pytest.raises(RefreshError) as exc:
        await svc.get_proposal(project_id=PROJECT_ID,
                               proposal_id=proposal.proposal_id,
                               user_id="another-owner")
    assert exc.value.code == "forbidden"
    assert exc.value.status == 404


# ── O12 三场景（增量计划正确性） ────────────────────────────────────

@pytest.mark.asyncio
async def test_o12_scenario_a_school_points_only_heat_subgraph(db):
    svc = _service(StaticPayloadProvider({SOURCE_REF: _payload(1, BASE_FEATURES)}),
                   _mapspec())
    proposal = await _prepared(svc, db, v2_features=BASE_FEATURES + [
        _feature("s6", "F", 600)])
    assert proposal.plan.conservative is False
    assert "data:schools" in proposal.plan.recompute
    assert "analysis:heat" in proposal.plan.recompute
    assert "data:population" not in proposal.plan.recompute
    assert "analysis:normalized" not in proposal.plan.recompute


@pytest.mark.asyncio
async def test_o12_scenario_b_population_table_year_change_only_normalization(db):
    svc = _service(StaticPayloadProvider({SOURCE_REF: _payload(1, BASE_FEATURES)}),
                   _mapspec())
    await svc.check_updates(db, project_id=PROJECT_ID, session_id=SESSION_ID,
                            user_id=USER_ID)
    # 相同要素、相同边界，仅字段单位/年度变化 → 归一化统计节点
    svc.payload_provider = StaticPayloadProvider(
        {SOURCE_REF: _payload(2, BASE_FEATURES, students_unit="thousand_person")})
    proposal = await svc.preview(db, project_id=PROJECT_ID, session_id=SESSION_ID,
                                 user_id=USER_ID, dag=_dag())
    assert proposal.diffs[0].fields_unit_changed == ["students"]
    assert "analysis:normalized" in proposal.plan.recompute
    assert "data:population" not in proposal.plan.recompute


@pytest.mark.asyncio
async def test_o12_scenario_c_nodata_or_global_dependency_forces_conservative(db):
    svc = _service(StaticPayloadProvider({SOURCE_REF: _payload(1, BASE_FEATURES)}),
                   _mapspec())
    await svc.check_updates(db, project_id=PROJECT_ID, session_id=SESSION_ID,
                            user_id=USER_ID)
    payload = _payload(2, BASE_FEATURES)
    payload["descriptor"]["tiles"] = {"t0": "h1", "t1": "h0"}
    # 全局分类断点 / 核密度带宽 → 扩大依赖域
    payload["descriptor"]["global_method_dependencies"] = ["global_stats", "stencil"]
    svc.payload_provider = StaticPayloadProvider({SOURCE_REF: payload})
    proposal = await svc.preview(db, project_id=PROJECT_ID, session_id=SESSION_ID,
                                 user_id=USER_ID, dag=_dag())
    # 证据缺失/未对齐时保守，不得 silently unchanged
    assert proposal.diffs[0].change_class != "none"
    assert proposal.plan.recompute or proposal.plan.conservative
