"""Artifact Lifecycle Service —— 传播 / 赋值 / 状态机 / 版本史测试。"""
import pytest

from app.lib.data import vocabulary as vocab
from app.lib.data.fingerprints import ChangeClass
from app.services.artifact_registry import (
    get_artifact,
    list_artifacts,
    register_artifact,
)
from app.services.data_lifecycle.service import (
    get_lifecycle_service,
    reset_lifecycle_service,
)
from app.services.session_data import session_data_manager


@pytest.fixture(autouse=True)
def _clean_session():
    # 每 e 用独立 session id 由测试自管；这里只做服务单例复位。
    reset_lifecycle_service()
    yield
    reset_lifecycle_service()


async def _chain(session_id: str):
    """src → d1 → d2 的血缘链（dispatch seam 形状）。"""
    await register_artifact(session_id, artifact_id="ref:geojson-src", producer_tool="tool_a")
    await register_artifact(
        session_id, artifact_id="ref:geojson-d1", producer_tool="tool_b",
        inputs=["ref:geojson-src"],
    )
    await register_artifact(
        session_id, artifact_id="ref:geojson-d2", producer_tool="tool_c",
        inputs=["ref:geojson-d1"],
    )


class TestRoleAndPersistence:
    async def test_assign_role_overrides_default(self):
        sid = "lc-role"
        await register_artifact(sid, artifact_id="ref:geojson-a", producer_tool="t")
        svc = get_lifecycle_service()
        assert (await svc.assign_role(sid, "ref:geojson-a", "boundary")).ok
        contract = await svc.get_contract(sid, "ref:geojson-a")
        assert contract.logical_role is vocab.LogicalRole.BOUNDARY

    async def test_assign_unknown_role_rejected(self):
        sid = "lc-role-bad"
        await register_artifact(sid, artifact_id="ref:geojson-a", producer_tool="t")
        result = await get_lifecycle_service().assign_role(sid, "ref:geojson-a", "not-a-role")
        assert not result.ok
        assert "unknown logical role" in result.reason

    async def test_set_persistence_policy_maps_tier(self):
        sid = "lc-persist"
        await register_artifact(sid, artifact_id="ref:geojson-a", producer_tool="t")
        svc = get_lifecycle_service()
        assert (await svc.set_persistence(sid, "ref:geojson-a", "workspace_persistent")).ok
        records = {r.artifact_id: r for r in await list_artifacts(sid)}
        md = records["ref:geojson-a"].metadata
        assert md["persistence_tier"] == "workspace"
        assert md["materialization_policy"] == "workspace_persistent"

    async def test_missing_record_fails_gracefully(self):
        result = await get_lifecycle_service().assign_role(
            "lc-none", "ref:geojson-ghost", "mask"
        )
        assert not result.ok


class TestApplyState:
    async def test_legal_transition_valid_to_stale(self):
        sid = "lc-state"
        await register_artifact(sid, artifact_id="ref:geojson-a", producer_tool="t")
        result = await get_lifecycle_service().apply_state(sid, "ref:geojson-a", "stale")
        assert result.ok
        rec = await get_artifact(sid, "ref:geojson-a")
        assert rec.status == "stale"

    async def test_illegal_transition_rejected(self):
        sid = "lc-state-bad"
        await register_artifact(sid, artifact_id="ref:geojson-a", producer_tool="t")
        # valid → superseded 合法（有账本投影）；superseded → ready 非法
        # （被替换产物不可复活 —— 与 vocabulary 迁移表一致）
        first = await get_lifecycle_service().apply_state(sid, "ref:geojson-a", "superseded")
        assert first.ok
        result = await get_lifecycle_service().apply_state(sid, "ref:geojson-a", "ready")
        assert not result.ok
        assert "illegal transition" in result.reason

    async def test_transient_states_have_no_ledger_projection(self):
        sid = "lc-transient"
        await register_artifact(sid, artifact_id="ref:geojson-a", producer_tool="t")
        # ready → materializing 是合法 V3 迁移，但会话账本没有该态投影
        result = await get_lifecycle_service().apply_state(sid, "ref:geojson-a", "materializing")
        assert not result.ok
        assert "no session-ledger projection" in result.reason

    async def test_unknown_state_rejected(self):
        sid = "lc-state-unk"
        await register_artifact(sid, artifact_id="ref:geojson-a", producer_tool="t")
        assert not (await get_lifecycle_service().apply_state(sid, "ref:geojson-a", "bogus")).ok


class TestStalenessPropagation:
    async def test_downstream_closure_marked_stale(self):
        sid = "lc-prop"
        await _chain(sid)
        svc = get_lifecycle_service()
        report = await svc.propagate_staleness(sid, "ref:geojson-src", reason="unit")
        assert report.marked == ["ref:geojson-d1", "ref:geojson-d2"]
        for dep in ("ref:geojson-d1", "ref:geojson-d2"):
            rec = await get_artifact(sid, dep)
            assert rec.status == "stale"
            assert rec.metadata["stale_source"] == "ref:geojson-src"
            assert rec.metadata["stale_verdict"] == "recompute"
            assert rec.metadata["stale_reason"] == "unit"

    async def test_idempotent_second_run(self):
        sid = "lc-prop-idem"
        await _chain(sid)
        svc = get_lifecycle_service()
        first = await svc.propagate_staleness(sid, "ref:geojson-src")
        second = await svc.propagate_staleness(sid, "ref:geojson-src")
        assert first.marked and not second.marked
        assert set(second.already_stale) == set(first.marked)

    async def test_metadata_only_change_marks_without_status_flip(self):
        sid = "lc-prop-meta"
        await register_artifact(sid, artifact_id="ref:geojson-src", producer_tool="t")
        await register_artifact(
            sid, artifact_id="ref:geojson-d1", producer_tool="t", inputs=["ref:geojson-src"]
        )
        report = await get_lifecycle_service().propagate_staleness(
            sid, "ref:geojson-src", change=ChangeClass.METADATA_ONLY, reason="meta"
        )
        # metadata-only → stale verdict（提示但仍可用）：会话层同样标 stale
        assert report.verdict == "stale"
        assert report.marked == ["ref:geojson-d1"]

    async def test_missing_source_reported_honestly(self):
        report = await get_lifecycle_service().propagate_staleness(
            "lc-prop-miss", "ref:geojson-ghost"
        )
        assert report.not_in_ledger == ["ref:geojson-ghost"]
        assert not report.marked


class TestImpactAndHistory:
    async def test_impact_analysis_readonly(self):
        sid = "lc-impact"
        await _chain(sid)
        before = {r.artifact_id: r.status for r in await list_artifacts(sid)}
        impact = await get_lifecycle_service().impact_analysis(
            sid, "ref:geojson-src", change=ChangeClass.CONTENT
        )
        after = {r.artifact_id: r.status for r in await list_artifacts(sid)}
        assert before == after  # 只读：不改状态
        assert impact["affected_count"] == 2
        assert impact["verdict"] == "recompute"

    async def test_version_history_with_replaces(self):
        sid = "lc-history"
        await register_artifact(sid, artifact_id="ref:geojson-v1", producer_tool="t")
        await register_artifact(
            sid, artifact_id="ref:geojson-v2", producer_tool="t", metadata={"replaces_hint": 1}
        )
        from app.services.artifact_registry import update_record_metadata

        # 手工补 replaces 边（register 的自动 supersede 仅同 capability 场景）
        await update_record_metadata(sid, "ref:geojson-v2", metadata={"replaces": "ref:geojson-v1"})
        # rebuild：replaces 是 record 字段不是 metadata 键 —— 走 metadata 投影
        # 不可行，这里直接断言 metadata 注入不 crash 且 version_chain 反映账本。
        from app.services.artifact_registry import build_artifact_graph

        records = {r.artifact_id: r for r in await list_artifacts(sid)}
        graph = build_artifact_graph(records)
        assert graph.dependents("ref:geojson-v1") == []  # replaces 非 inputs 边

        # 真正的 replaces 链：用 register 同 capability supersede 机制产生
        sid2 = "lc-history2"
        await register_artifact(
            sid2, artifact_id="ref:geojson-a1", producer_capability="kde", producer_tool="kde"
        )
        await register_artifact(
            sid2, artifact_id="ref:geojson-a2", producer_capability="kde", producer_tool="kde"
        )
        history = await get_lifecycle_service().version_history(sid2, "ref:geojson-a2")
        assert [v.artifact_id for v in history.chain] == ["ref:geojson-a1", "ref:geojson-a2"]
        rec1 = await get_artifact(sid2, "ref:geojson-a1")
        assert rec1.status == "superseded"


class TestContractEnrichment:
    async def test_contract_carries_explicit_metadata(self):
        sid = "lc-contract"
        await register_artifact(
            sid, artifact_id="ref:geojson-a", producer_tool="t",
            metadata={"content_fingerprint": "cf123"},
        )
        svc = get_lifecycle_service()
        await svc.assign_role(sid, "ref:geojson-a", "training")
        await svc.set_persistence(sid, "ref:geojson-a", "user_persistent")
        contract = await svc.get_contract(sid, "ref:geojson-a")
        assert contract.logical_role is vocab.LogicalRole.TRAINING
        assert contract.persistence is vocab.PersistenceTier.PERSISTENT
        assert contract.fingerprint.content == "cf123"
        assert contract.stable_identity == "cf123"

    async def test_get_contract_missing_returns_none(self):
        assert await get_lifecycle_service().get_contract("lc-none", "ref:ghost") is None


async def test_detector_token_comparison():
    sid = "lc-detect"
    fc = {"type": "FeatureCollection", "features": []}
    ref = await session_data_manager.store(sid, fc, prefix="geojson")
    await register_artifact(sid, artifact_id=ref, producer_tool="t")
    svc = get_lifecycle_service()
    # 记录当前修订快照
    current = await svc.current_source_revision(sid, ref)
    change = await svc.detect_source_change(sid, ref, recorded=current)
    assert change is ChangeClass.NONE
    # 覆写为新对象（内容变化）→ revision bump → CONTENT
    # （同一对象覆写走 store 热路径守卫：无变化 = 无 bump，正确语义）
    await session_data_manager.overwrite(
        sid, ref, {"type": "FeatureCollection", "features": []}
    )
    change2 = await svc.detect_source_change(sid, ref, recorded=current)
    assert change2 is ChangeClass.CONTENT
