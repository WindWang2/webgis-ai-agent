"""Durable Project Cartographic Runtime V1 —— 跨 session 场景语料（ADR-0226）。

端到端恢复主场景 + 漂移/降级/安全/并发边界：

1. session A 建数据 + 账本 + MapSpec + plan → freeze；
2. 模拟 process restart / session 死亡（clear_session 全量 purge）；
3. session B resume：durable source 可用、stale observation 不复用、
   lineage 连续、Agent 可继续（spec 在新 session 可读可改）；
4. 漂移面：dataset version changed / detached、capability unavailable、
   blob digest 损坏、unsupported schema、外部 URL 源、oversized 预算；
5. 安全面：跨用户删除拒绝、owner 守卫；
6. 并发面：resume lease fail-fast、重复 freeze 各自成快照（cap 有界）。
"""
from __future__ import annotations

import json
import time

import pytest

from app.services.artifact_registry import list_artifacts, register_artifact
from app.services.session_data import session_data_manager
from app.services.workspace.snapshot import reset_workspace_snapshot_service


# ── fixture：DATA_DIR 隔离（tests/data/conftest 的 blob 根重置自动生效）──


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    from app.services.project_runtime.store import reset_project_runtime_store

    reset_project_runtime_store()
    reset_workspace_snapshot_service()
    yield
    reset_project_runtime_store()
    reset_workspace_snapshot_service()


@pytest.fixture()
def db():
    """项目域表（Project/ProjectDataset/Conversation 等按需补建）+ 种子行。"""
    from app.core.database import Base, Engine, SessionLocal
    from app.models.project import Project, ProjectDataset

    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as s:
        # 幂等重置（上一次 run 的 detached/archived/fp-v2 残留不得串场）
        s.query(ProjectDataset).filter(
            ProjectDataset.id == "ds-rt1"
        ).update({"version_fingerprint": "fp-v1", "detached_at": None})
        s.query(Project).filter(Project.id == "proj-rt1").update(
            {"status": "active", "owner_id": "u_rt"})
        s.merge(Project(id="proj-rt1", name="runtime-corpus",
                        owner_id="u_rt", status="active"))
        s.merge(ProjectDataset(
            id="ds-rt1", project_id="proj-rt1", name="haidian_poi",
            source_type="upload", source_ref="upload_1",
            version_fingerprint="fp-v1",
        ))
        s.commit()
        yield s


# ── 种子：一个真实可冻结的制图 session ─────────────────────────────────


async def _seed_session(
    sid: str, *, with_plan: bool = True,
    with_capabilities: bool = False,
):
    """最小而真实的制图 session：2 个 ref 源 + 1 个外部 URL 源 + 组件绑定。

    - ``ref_a``：活载荷（store 内）+ 账本行；
    - ``ref:geojson-ghost-*``：账本行有血缘但载荷从未落 store（recomputable）；
    - ``tiles``：外部 URL 源（external_version_pinned）；
    - chart 组件绑定 ref_a（layout 绑定面）。
    """
    suffix = sid[-6:]
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"geometry": {"type": "Point", "coordinates": [116.32, 39.98]},
             "properties": {"name": "poi-a"}},
        ],
    }
    ref_a = await session_data_manager.store(sid, fc, prefix="geojson")
    await register_artifact(
        sid, artifact_id=ref_a, producer_tool="query_osm_poi",
        artifact_type="poi_feature_set",
    )
    ghost = f"ref:geojson-ghost-{suffix}"
    await register_artifact(
        sid, artifact_id=ghost, producer_tool="kde",
        inputs=[ref_a], artifact_type="density_surface",
    )
    spec = {
        "schema_version": "1.5",
        "sources": {
            "poi": {"type": "geojson", "ref": ref_a, "ref_id": ref_a},
            "density": {"type": "geojson", "ref": ghost, "ref_id": ghost},
            "tiles": {"type": "geojson",
                      "url": "https://tiles.example/pois.json"},
        },
        "layers": [
            {"id": "poi-layer", "source": "poi"},
            {"id": "density-layer", "source": "density"},
        ],
        "view": {"center": [116.32, 39.98], "zoom": 11},
        "base_layer": "osm",
        "layout": {
            "components": [
                {"id": "chart-1", "type": "chart",
                 "options": {"chartRef": ref_a}},
            ],
        },
    }
    from app.services.mapspec.store import MapSpecStore

    await MapSpecStore().save_mapspec(sid, spec, mutation_revision=3)
    if with_plan:
        from app.services.session_plan import SessionPlan, save_session_plan

        chapter: dict = {"goal": "density map"}
        if with_capabilities:
            # capability 词表会进快照并在恢复期重验证 —— 与环境相关，
            # 只有 capability 场景显式播种。
            chapter["workflow_instance"] = {
                "rows": [{"capability": "query_local_poi",
                          "status": "data_arrived",
                          "bound_ref": ref_a}],
            }
        await save_session_plan(SessionPlan(
            envelope_id=f"sp-{suffix}", session_id=sid,
            user_goal="海淀 POI 密度制图",
            gis_chapter=chapter,
        ))
    return spec, ref_a, ghost


async def _freeze(db, sid: str, *, project_id: str = "proj-rt1",
                  user_id: str = "u_rt", org_id: int | None = None,
                  label: str = "corpus", budget_bytes: int | None = None):
    from app.services.project_runtime.freeze import save_runtime_snapshot
    from app.services.project_runtime.promotion import (
        DEFAULT_FREEZE_BUDGET_BYTES,
    )

    return await save_runtime_snapshot(
        db, session_id=sid, project_id=project_id, user_id=user_id,
        org_id=org_id,
        label=label, freeze_reason="explicit_save",
        budget_bytes=budget_bytes or DEFAULT_FREEZE_BUDGET_BYTES,
    )


def _bindings_by_ref(snapshot):
    return {b.ref: b for b in snapshot.source_bindings if b.ref}


async def _kill_session(sid: str) -> None:
    """模拟 process restart + session 死亡：store purge + mapspec 磁盘清扫。"""
    await session_data_manager.clear_session(sid)


# ═══ 契约 / 存储单元 ═══════════════════════════════════════════════════


class TestContract:
    def test_defaults_and_bounds(self):
        from app.services.project_runtime.contract import (
            SOURCE_BINDING_LIMIT,
            ProjectRuntimeSnapshotV1,
        )

        s = ProjectRuntimeSnapshotV1(snapshot_id="prs-x", project_id="p1")
        assert s.schema_version == 1
        assert s.policy.observation_policy == "invalidate"
        assert s.policy.restore_in_flight_jobs is False
        # 有界校验（直接喂超量列表）
        from app.services.project_runtime.contract import SourceBinding

        s2 = ProjectRuntimeSnapshotV1(
            snapshot_id="prs-y", project_id="p1",
            source_bindings=[
                SourceBinding(source_id=f"s{i}") for i in range(100)
            ],
            datasets=[],
        )
        assert len(s2.source_bindings) == SOURCE_BINDING_LIMIT


class TestStore:
    async def test_save_load_roundtrip_and_invalid_ids(self):
        from app.services.project_runtime.contract import ProjectRuntimeSnapshotV1
        from app.services.project_runtime.store import (
            get_project_runtime_store,
        )

        store = get_project_runtime_store()
        snap = ProjectRuntimeSnapshotV1(
            snapshot_id="prs-abc", project_id="proj-rt1",
            mapspec_doc={"sources": {}}, mapspec_fingerprint="f" * 64,
        )
        assert await store.save(snap)
        loaded = store.load("proj-rt1", "prs-abc")
        assert loaded is not None and loaded.snapshot_id == "prs-abc"
        # 非法 id：拒绝（路径遍历面）
        assert store.load("../evil", "prs-abc") is None
        assert store.load("proj-rt1", "../../etc") is None

    async def test_unsupported_schema_fail_closed(self, tmp_path):
        from app.services.project_runtime.store import (
            UnsupportedSnapshotSchema,
            get_project_runtime_store,
            runtime_snapshots_dir,
        )

        directory = runtime_snapshots_dir("proj-rt1")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "prs-future.json").write_text(json.dumps({
            "snapshot_id": "prs-future", "project_id": "proj-rt1",
            "schema_version": 99,
        }), encoding="utf-8")
        store = get_project_runtime_store()
        with pytest.raises(UnsupportedSnapshotSchema):
            store.load("proj-rt1", "prs-future")

    async def test_cap_oldest_trimmed(self):
        from app.services.project_runtime.contract import ProjectRuntimeSnapshotV1
        from app.services.project_runtime.store import (
            MAX_RUNTIME_SNAPSHOTS_PER_PROJECT,
            get_project_runtime_store,
        )

        store = get_project_runtime_store()
        for i in range(MAX_RUNTIME_SNAPSHOTS_PER_PROJECT + 5):
            snap = ProjectRuntimeSnapshotV1(
                snapshot_id=f"prs-cap{i:03d}", project_id="proj-rt1",
                created_at=time.time() + i,
            )
            assert await store.save(snap)
        listed = await store.list_snapshots("proj-rt1")
        assert len(listed) <= MAX_RUNTIME_SNAPSHOTS_PER_PROJECT
        assert all(x["snapshot_id"] != "prs-cap000" for x in listed)

    async def test_resume_lease_acquire_conflict_expiry(self, monkeypatch):
        from app.services.project_runtime.store import (
            get_project_runtime_store,
        )

        store = get_project_runtime_store()
        ok, holder = store.acquire_resume_lease("proj-rt1", "prs-lease1")
        assert ok and holder is None
        ok2, holder2 = store.acquire_resume_lease("proj-rt1", "prs-lease1")
        assert not ok2 and holder2 and "pid" in holder2
        store.release_resume_lease("proj-rt1", "prs-lease1")
        ok3, _ = store.acquire_resume_lease("proj-rt1", "prs-lease1")
        assert ok3
        # 过期 lease 可抢占（stale 持有者）

        store.release_resume_lease("proj-rt1", "prs-lease1")
        lease = store._lease_path("proj-rt1", "prs-lease1")
        lease.parent.mkdir(parents=True, exist_ok=True)
        lease.write_text(json.dumps(
            {"pid": 999999, "acquired_at": time.time() - 3600}), encoding="utf-8")
        ok4, _ = store.acquire_resume_lease("proj-rt1", "prs-lease1")
        assert ok4

    async def test_delete_owner_guard(self):
        from app.services.project_runtime.contract import ProjectRuntimeSnapshotV1
        from app.services.project_runtime.store import get_project_runtime_store

        store = get_project_runtime_store()
        snap = ProjectRuntimeSnapshotV1(
            snapshot_id="prs-del1", project_id="proj-rt1", created_by="u_rt",
        )
        assert await store.save(snap)
        # 非创建者：拒绝（服务侧二道守卫）
        assert await store.delete("proj-rt1", "prs-del1", owner_id="u_other") is None
        result = await store.delete("proj-rt1", "prs-del1", owner_id="u_rt")
        assert result and result["deleted"]
        assert store.load("proj-rt1", "prs-del1") is None


# ═══ 重绑定事务单元 ═══════════════════════════════════════════════════


class TestRebind:
    def _spec(self):
        return {
            "sources": {
                "a": {"type": "geojson", "ref": "ref:geojson-old1",
                      "ref_id": "ref:geojson-old1"},
                "b": {"type": "raster", "imageRef": "ref:raster/old2.png",
                      "bounds": [1, 2, 3, 4]},
            },
            "layers": [{"id": "l1", "source": "a"}],
            "layout": {"components": [
                {"id": "c1", "type": "chart",
                 "options": {"chartRef": "ref:geojson-old1"}},
            ]},
            "view": {"zoom": 10},
        }

    def test_rewrite_only_refs_change(self):
        from app.services.project_runtime.rebind import (
            canonical_fingerprint,
            rewrite_spec_refs,
            semantics_preserved,
            _sanctioned_ref_changes,
        )

        spec = self._spec()
        ref_map = {
            "ref:geojson-old1": "ref:geojson-new1",
            "ref:raster/old2.png": "ref:raster/old2.png",  # binary 同 id 复位
        }
        missing: list = []
        rebound = rewrite_spec_refs(spec, ref_map, missing)
        assert missing == []
        assert rebound["sources"]["a"]["ref"] == "ref:geojson-new1"
        assert rebound["sources"]["a"]["ref_id"] == "ref:geojson-new1"
        assert rebound["sources"]["b"]["imageRef"] == "ref:raster/old2.png"
        assert rebound["layout"]["components"][0]["options"]["chartRef"] == (
            "ref:geojson-new1")
        assert rebound["view"] == spec["view"]
        assert semantics_preserved(spec, rebound)
        assert _sanctioned_ref_changes(spec, rebound, ref_map)
        assert canonical_fingerprint(rebound) != canonical_fingerprint(spec)

    def test_dangling_cleared_and_disclosed(self):
        from app.services.project_runtime.rebind import (
            rewrite_spec_refs,
            semantics_preserved,
            _sanctioned_ref_changes,
        )

        spec = self._spec()
        ref_map = {"ref:raster/old2.png": "ref:raster/old2.png"}
        missing: list = []
        rebound = rewrite_spec_refs(spec, ref_map, missing)
        assert missing == ["ref:geojson-old1"]
        assert rebound["sources"]["a"]["ref"] == ""
        assert rebound["sources"]["a"]["ref_id"] == ""
        # 置空是许可变化：语义守卫通过（非 ref 内容零漂移）
        assert semantics_preserved(spec, rebound)
        assert _sanctioned_ref_changes(spec, rebound, ref_map)

    def test_semantics_drift_aborts(self):
        from app.services.project_runtime.rebind import (
            rewrite_spec_refs,
            semantics_preserved,
        )

        spec = self._spec()
        ref_map = {"ref:geojson-old1": "ref:geojson-new1"}
        rebound = rewrite_spec_refs(spec, ref_map, [])
        rebound["view"]["zoom"] = 9  # ref 键位之外的漂移
        assert not semantics_preserved(spec, rebound)

    def test_unsanctioned_ref_change_aborts(self):
        from app.services.project_runtime.rebind import _sanctioned_ref_changes

        spec = self._spec()
        tampered = json.loads(json.dumps(spec))
        # 无中生有：空 ref 键位被注入（不在 ref_map 的变化）
        tampered["sources"]["a"]["url"] = "ref:geojson-injected"
        ref_map = {"ref:geojson-old1": "ref:geojson-new1"}
        assert not _sanctioned_ref_changes(spec, tampered, ref_map)

    async def test_commit_into_new_session(self):
        from app.services.mapspec.store import MapSpecStore
        from app.services.project_runtime.rebind import (
            canonical_fingerprint,
            rebind_mapspec_into_session,
        )
        from app.services.session_data import session_data_manager

        spec = self._spec()
        fp = canonical_fingerprint(spec)
        new_sid = "rebind-commit-1"
        result = await rebind_mapspec_into_session(
            new_sid, spec, fp,
            {"ref:geojson-old1": "ref:geojson-new1",
             "ref:raster/old2.png": "ref:raster/old2.png"},
        )
        assert result.committed, result.error
        restored = await MapSpecStore().get_mapspec(new_sid)
        assert restored["sources"]["a"]["ref"] == "ref:geojson-new1"
        state = await session_data_manager.get_map_state(new_sid)
        assert state["_cartographic_mutation_revision"] == 1

    async def test_fingerprint_mismatch_aborts(self):
        from app.services.project_runtime.rebind import (
            rebind_mapspec_into_session,
        )

        result = await rebind_mapspec_into_session(
            "rebind-bad-1", self._spec(), "0" * 64, {},
        )
        assert not result.committed
        assert result.error == "fingerprint_mismatch"


# ═══ 晋升规划器单元 ═══════════════════════════════════════════════════


class TestPromotion:
    async def test_classification_vocabulary(self, db):
        from app.services.project_runtime.promotion import plan_and_promote

        sid = "promo-1"
        fc = {"type": "FeatureCollection", "features": []}
        live = await session_data_manager.store(sid, fc, prefix="geojson")
        await register_artifact(sid, artifact_id=live, producer_tool="t")
        dead_with_lineage = "ref:geojson-dead-1"
        await register_artifact(
            sid, artifact_id=dead_with_lineage, producer_tool="kde",
            inputs=[live], artifact_type="density",
        )
        receipt = await plan_and_promote(
            sid, "proj-rt1",
            [("live-src", "source", live),
             ("dead-src", "source", dead_with_lineage),
             ("url-src", "source", "https://x.example/d.json"),
             ("inline-src", "source", "")],
            db=db,
        )
        assert receipt.outcomes[live].promotion_class == "promotable"
        assert receipt.outcomes[live].pointer is not None
        assert receipt.outcomes[dead_with_lineage].promotion_class == (
            "recomputable")
        assert receipt.outcomes["url-src"].promotion_class == (
            "external_version_pinned")
        assert receipt.outcomes["inline-src"].promotion_class == (
            "external_version_pinned")

    async def test_budget_oversize_honest_skip(self, db):
        from app.services.project_runtime.promotion import plan_and_promote

        sid = "promo-2"
        fc = {"type": "FeatureCollection", "features": [
            {"geometry": None, "properties": {"k": "x" * 2048}},
        ] * 8}
        big = await session_data_manager.store(sid, fc, prefix="geojson")
        receipt = await plan_and_promote(
            sid, "proj-rt1", [("big", "source", big)],
            db=db, budget_bytes=64,
        )
        assert receipt.outcomes[big].promotion_class in (
            "oversized", "quota_blocked")
        assert receipt.outcomes[big].pointer is None

    async def test_quota_gate_blocks_promotion(self, db, monkeypatch):
        from app.services.project_runtime import promotion as promo_mod
        from app.services.project_runtime.promotion import plan_and_promote

        sid = "promo-3"
        fc = {"type": "FeatureCollection", "features": []}
        ref = await session_data_manager.store(sid, fc, prefix="geojson")
        await register_artifact(sid, artifact_id=ref, producer_tool="t")

        def _tiny_quota(db_arg, project_id):
            return 1  # 1 byte 余量：任何载荷都超

        monkeypatch.setattr(promo_mod, "_quota_remaining_bytes", _tiny_quota)
        receipt = await plan_and_promote(
            sid, "proj-rt1", [("s", "source", ref)], db=db,
        )
        assert receipt.outcomes[ref].promotion_class == "quota_blocked"
        assert receipt.outcomes[ref].pointer is None
        assert receipt.quota_refusals >= 1


# ═══ 端到端主场景：freeze → 会话死亡 → verify-first resume ═══════════


class TestCrossSessionResume:
    async def test_full_lifecycle_after_session_death(self, db):
        """主场景：A 建图 → freeze → A 死 → B resume → 继续制图。"""
        from app.services.mapspec.store import MapSpecStore
        from app.services.project_runtime.resume import resume_runtime_snapshot
        from app.services.project_runtime.validation import (
            validate_runtime_snapshot,
        )
        from app.services.session_plan import load_session_plan

        sid_a = "rt-e2e-session-a"
        spec, ref_a, ghost = await _seed_session(sid_a)
        snapshot = await _freeze(db, sid_a)

        # freeze 断言：manifest 是指针集，不是载荷搬运工
        assert snapshot.mapspec_fingerprint
        assert snapshot.mapspec_mutation_revision == 3
        bindings = _bindings_by_ref(snapshot)
        assert bindings[ref_a].promotion_class == "promotable"
        assert bindings[ref_a].durable_pointer is not None
        assert bindings[ghost].promotion_class == "recomputable"
        assert bindings[ghost].durable_pointer is None
        url_binding = [
            b for b in snapshot.source_bindings
            if b.promotion_class == "external_version_pinned"
        ]
        assert len(url_binding) == 1  # 外部 URL 源如实分类
        assert snapshot.workspace_snapshot_id  # 账本权威已组合
        assert snapshot.datasets and snapshot.datasets[0].version_fingerprint == "fp-v1"

        # 会话死亡（store purge + mapspec 磁盘清扫 = restart + TTL）
        await _kill_session(sid_a)
        assert await MapSpecStore().get_mapspec(sid_a) is None

        # resume 前校验：durable 面可恢复，ghost 需要 recompute
        report = await validate_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            current_user_id="u_rt",
        )
        assert report.status in ("ready_with_stale_inputs", "ready")
        dispositions = {d.ref: d.disposition for d in report.dispositions}
        assert dispositions[ref_a] == "restore_from_durable"
        assert dispositions[ghost] == "needs_recompute"

        receipt = await resume_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            user_id="u_rt",
        )
        sid_b = receipt.session_id
        assert sid_b != sid_a
        # durable source 重水合：新 ref id（会话 ref 是能力令牌，不复用旧 id）
        assert ref_a in receipt.ref_map
        assert receipt.ref_map[ref_a] != ref_a
        assert receipt.mapspec_rebound
        assert receipt.missing_refs == [ghost]
        # 新 session 的 spec：poi 重绑定到新 ref；ghost 置空（不悬空谎报）
        spec_b = await MapSpecStore().get_mapspec(sid_b)
        assert spec_b is not None
        assert spec_b["sources"]["poi"]["ref"] == receipt.ref_map[ref_a]
        assert spec_b["sources"]["density"]["ref"] == ""
        assert spec_b["sources"]["tiles"]["url"] == "https://tiles.example/pois.json"
        # 语义面未漂移：视图/图层结构随冻结面
        assert spec_b["view"] == spec["view"]
        assert spec_b["base_layer"] == spec["base_layer"]
        # 账本连续：契约重注册（含血缘），payload 未恢复的行如实 expired
        records = {r.artifact_id: r for r in await list_artifacts(sid_b)}
        assert receipt.ref_map[ref_a] in records
        assert records[receipt.ref_map[ref_a]].metadata.get("restored_from") == (
            snapshot.snapshot_id)
        assert receipt.artifacts_expired >= 1
        # plan：fresh envelope + resumed_from_runtime 披露（goal 连续）
        plan_b = await load_session_plan(sid_b)
        assert plan_b is not None
        assert plan_b.user_goal == "海淀 POI 密度制图"
        disclosed = plan_b.gis_chapter.get("resumed_from_runtime", {})
        assert disclosed.get("snapshot_id") == snapshot.snapshot_id
        assert ghost in disclosed.get("needs_recompute", [])
        # stale observation 不复活：新 session 无旧观察键，只有恢复标记
        from app.services.session_data import session_data_manager as sdm

        state_b = await sdm.get_map_state(sid_b)
        assert "observation" not in state_b
        assert state_b.get("_resumed_from_runtime", {}).get("snapshot_id") == (
            snapshot.snapshot_id)
        # invalidated 披露（诚实：没恢复什么）
        assert "render_observations" in receipt.invalidated
        assert "in_flight_jobs" in receipt.invalidated

    async def test_agent_can_continue_after_resume(self, db):
        """场景 8：恢复后 Agent 能继续修改/分析/制图（spec 可变更）。"""
        from app.services.mapspec.store import MapSpecStore
        from app.services.project_runtime.resume import resume_runtime_snapshot

        sid_a = "rt-cont-session-a"
        await _seed_session(sid_a)
        snapshot = await _freeze(db, sid_a)
        receipt = await resume_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            user_id="u_rt",
        )
        sid_b = receipt.session_id
        store = MapSpecStore()
        spec = await store.get_mapspec(sid_b)
        spec["view"]["zoom"] = 13
        await store.save_mapspec(sid_b, spec, mutation_revision=2)
        state = await session_data_manager.get_map_state(sid_b)
        assert state["_cartographic_mutation_revision"] == 2

    async def test_descriptor_semantics_reconciled_on_resume(self, db):
        """G01/WP-B6：恢复链数据集语义身份 —— 迁移 + 记录面对账。

        - 快照期 source 带 descriptor_fingerprint（记录面，经 MapSpecStore
          绑定路径铸造）；
        - freeze 后旧 session 语义前进（数据 revision 前进）→ resume 时
          迁移 descriptor 到新 session（指针改写、指纹不变）且记录面 ≠
          当前 → recompute 披露（不静默续渲染）；
        - 对照组：语义未漂移 → valid。
        """
        from app.services.dataset_semantics import (
            derive_descriptor,
            get_dataset_semantic_store,
        )
        from app.services.dataset_semantics.store import (
            reset_dataset_semantic_store,
        )
        from app.lib.gis.dataset_profile import DatasetProfile

        reset_dataset_semantic_store()  # DATA_DIR 隔离后重建基座

        def _desc(feature_count: int, key: str):
            p = DatasetProfile(
                source="ref_descriptor", feature_count=feature_count,
                geometry_types=["Point"], crs="EPSG:4326",
                fields={"name": "string"}, fields_status="explicit",
            )
            feats = [
                {"geometry": {"type": "Point",
                              "coordinates": [116.32 + i * 0.001, 39.98]},
                 "properties": {"name": f"poi-{i}"}}
                for i in range(feature_count)
            ]
            return derive_descriptor(p, dataset_key=key, features=feats)

        sem = get_dataset_semantic_store()
        from app.services.mapspec.store import MapSpecStore
        from app.services.project_runtime.resume import resume_runtime_snapshot

        # ── 漂移场景：freeze 后旧 session 语义前进 ──────────────────────
        sid_a = "rt-dsr-session-a"
        await _seed_session(sid_a)
        spec = await MapSpecStore().get_mapspec(sid_a)
        ref_key = spec["sources"]["poi"]["ref"]
        v1 = _desc(3, ref_key)
        await sem.put(sid_a, ref_key, v1)
        # 记录面：source 落冻结时的语义指纹（生产由 source 首绑铸造；
        # 此处直接落键模拟同一记录面 —— freeze 冻结的就是这个值）。
        spec["sources"]["poi"]["descriptor_fingerprint"] = (
            v1.descriptor_fingerprint)
        await MapSpecStore().save_mapspec(sid_a, spec, mutation_revision=4)
        spec_saved = await MapSpecStore().get_mapspec(sid_a)
        assert (spec_saved["sources"]["poi"].get("descriptor_fingerprint")
                == v1.descriptor_fingerprint), "记录面指纹必须冻结在 spec 上"

        snapshot = await _freeze(db, sid_a)
        v2 = _desc(9, ref_key)  # 内容证据变化 → 语义前进
        assert v2.descriptor_fingerprint != v1.descriptor_fingerprint
        await sem.put(sid_a, ref_key, v2)

        receipt = await resume_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            user_id="u_rt",
        )
        rec = receipt.descriptor_reconciliation
        assert rec.get("v") == 1
        assert rec["migrated"] >= 1
        new_ref = receipt.ref_map[ref_key]
        check = next(c for c in rec["checks"] if c["ref"] == new_ref)
        assert check["verdict"] == "recompute"  # 记录面 v1 ≠ 当前 v2
        assert new_ref in rec["stale_refs"]
        assert any("dataset semantics" in d for d in receipt.disclosures)
        # 新 session 语义 store：迁移后 descriptor 指纹 = v2（身份连续，
        # dataset_key 指针已改写为新 ref）。
        migrated = await sem.get(receipt.session_id, new_ref)
        assert migrated.ok
        assert migrated.descriptor.descriptor_fingerprint == (
            v2.descriptor_fingerprint)
        assert migrated.descriptor.dataset_key == new_ref

        # ── 对照组：语义未漂移 → valid ─────────────────────────────────
        sid_c = "rt-dsr-session-c"
        await _seed_session(sid_c)
        spec_c = await MapSpecStore().get_mapspec(sid_c)
        ref_c = spec_c["sources"]["poi"]["ref"]
        d_c = _desc(3, ref_c)
        await sem.put(sid_c, ref_c, d_c)
        spec_c["sources"]["poi"]["descriptor_fingerprint"] = (
            d_c.descriptor_fingerprint)
        await MapSpecStore().save_mapspec(sid_c, spec_c, mutation_revision=4)
        snapshot_c = await _freeze(db, sid_c, label="corpus-c")
        receipt_c = await resume_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot_c.snapshot_id,
            user_id="u_rt",
        )
        rec_c = receipt_c.descriptor_reconciliation
        new_ref_c = receipt_c.ref_map[ref_c]
        check_c = next(
            (c for c in rec_c["checks"] if c["ref"] == new_ref_c), None)
        assert check_c is not None and check_c["verdict"] == "valid"
        assert not rec_c["stale_refs"]

    async def test_dataset_version_changed_disclosed(self, db):
        from app.models.project import ProjectDataset
        from app.services.project_runtime.resume import resume_runtime_snapshot
        from app.services.project_runtime.validation import (
            validate_runtime_snapshot,
        )

        sid_a = "rt-dsv-session-a"
        await _seed_session(sid_a)
        snapshot = await _freeze(db, sid_a)
        db.query(ProjectDataset).filter(
            ProjectDataset.id == "ds-rt1"
        ).update({"version_fingerprint": "fp-v2"})
        db.commit()

        report = await validate_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            current_user_id="u_rt",
        )
        assert report.dataset_verdicts["ds-rt1"] == "changed"
        assert report.status == "dataset_version_changed"

        receipt = await resume_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            user_id="u_rt", allow_stale=True,
        )
        assert receipt.status == "dataset_version_changed"
        assert "ds-rt1" in receipt.datasets_changed
        # allow_stale=False：显式拒绝（绝不静默带病恢复）
        from app.services.project_runtime.resume import ResumeBlocked

        with pytest.raises(ResumeBlocked):
            await resume_runtime_snapshot(
                db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
                user_id="u_rt", allow_stale=False,
            )

    async def test_dataset_detached_disclosed(self, db):
        from app.models.project import ProjectDataset
        from app.services.project_runtime.validation import (
            validate_runtime_snapshot,
        )

        sid_a = "rt-dsd-session-a"
        await _seed_session(sid_a)
        snapshot = await _freeze(db, sid_a)
        from datetime import datetime, timezone

        db.query(ProjectDataset).filter(
            ProjectDataset.id == "ds-rt1"
        ).update({"detached_at": datetime(2026, 10, 18, tzinfo=timezone.utc)})
        db.commit()
        report = await validate_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            current_user_id="u_rt",
        )
        assert report.dataset_verdicts["ds-rt1"] == "detached"

    async def test_archived_project_blocked(self, db):
        from app.models.project import Project
        from app.services.project_runtime.resume import ResumeBlocked
        from app.services.project_runtime.resume import resume_runtime_snapshot
        from app.services.project_runtime.validation import (
            validate_runtime_snapshot,
        )

        sid_a = "rt-arch-session-a"
        await _seed_session(sid_a)
        snapshot = await _freeze(db, sid_a)
        db.query(Project).filter(Project.id == "proj-rt1").update(
            {"status": "archived"})
        db.commit()
        report = await validate_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            current_user_id="u_rt",
        )
        assert report.status == "blocked"
        with pytest.raises(ResumeBlocked):
            await resume_runtime_snapshot(
                db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
                user_id="u_rt",
            )

    async def test_capability_unavailable_degrades_honestly(self, db, monkeypatch):
        from app.services.project_runtime.resume import resume_runtime_snapshot
        from app.services.project_runtime.validation import (
            validate_runtime_snapshot,
        )

        sid_a = "rt-cap-session-a"
        await _seed_session(sid_a, with_capabilities=True)
        snapshot = await _freeze(db, sid_a)
        assert snapshot.plan.required_capabilities == ["query_local_poi"]

        import app.services.gis_harness.capability_resolution as cap_res

        def _unavailable(cap, situation, **kwargs):  # 同步签名（真源）
            return "unqualified", [], []

        monkeypatch.setattr(cap_res, "capability_status", _unavailable)
        report = await validate_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            current_user_id="u_rt",
        )
        assert "query_local_poi" in report.capability_verdicts
        assert any(
            f.state == "capability_unavailable" for f in report.findings
        )
        # 降级而非假成功：resume 允许执行但回执如实披露
        receipt = await resume_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            user_id="u_rt",
        )
        assert "query_local_poi" in receipt.capabilities_unavailable
        assert any("capability unavailable" in d for d in receipt.disclosures)

    async def test_blob_corruption_missing_artifact(self, db):
        """freeze 后 blob 损坏 → pointer 校验失败 → needs_recompute。"""
        from app.services.project_runtime.validation import (
            validate_runtime_snapshot,
        )
        from app.services.workspace.durability import (
            _resolve_content_path,
        )

        sid_a = "rt-cor-session-a"
        _, ref_a, _ghost = await _seed_session(sid_a)
        snapshot = await _freeze(db, sid_a)
        binding = _bindings_by_ref(snapshot)[ref_a]
        path = _resolve_content_path(binding.durable_pointer.content_location)
        assert path is not None and path.is_file()
        path.write_bytes(b"corrupted")  # 篡改内容
        report = await validate_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            current_user_id="u_rt",
        )
        dispositions = {d.ref: d.disposition for d in report.dispositions}
        assert dispositions[ref_a] == "needs_recompute"
        assert any(
            f.state == "missing_artifact" and f.subject == ref_a
            for f in report.findings
        )

    async def test_unsupported_schema_resume_blocked(self, db):
        from app.services.project_runtime.resume import (
            ResumeBlocked,
            resume_runtime_snapshot,
        )
        from app.services.project_runtime.store import (
            get_project_runtime_store,
            runtime_snapshots_dir,
        )

        # 正常 freeze 一个（确保目录存在）
        sid_a = "rt-sch-session-a"
        await _seed_session(sid_a)
        real = await _freeze(db, sid_a)
        get_project_runtime_store()
        directory = runtime_snapshots_dir("proj-rt1")
        (directory / "prs-v99.json").write_text(json.dumps({
            "snapshot_id": "prs-v99", "project_id": "proj-rt1",
            "schema_version": 99,
        }), encoding="utf-8")
        with pytest.raises(ResumeBlocked) as exc_info:
            await resume_runtime_snapshot(
                db, project_id="proj-rt1", snapshot_id="prs-v99", user_id="u_rt",
            )
        assert exc_info.value.status == "unsupported_schema"
        # 真实快照仍可恢复（fail-closed 不误伤）
        receipt = await resume_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=real.snapshot_id,
            user_id="u_rt",
        )
        assert receipt.mapspec_rebound

    async def test_resume_lease_conflict(self, db):
        from app.services.project_runtime.resume import (
            ResumeConflict,
            resume_runtime_snapshot,
        )
        from app.services.project_runtime.store import get_project_runtime_store

        sid_a = "rt-lease-session-a"
        await _seed_session(sid_a)
        snapshot = await _freeze(db, sid_a)
        store = get_project_runtime_store()
        ok, _holder = store.acquire_resume_lease("proj-rt1", snapshot.snapshot_id)
        assert ok
        try:
            with pytest.raises(ResumeConflict):
                await resume_runtime_snapshot(
                    db, project_id="proj-rt1",
                    snapshot_id=snapshot.snapshot_id, user_id="u_rt",
                )
        finally:
            store.release_resume_lease("proj-rt1", snapshot.snapshot_id)

    async def test_sequential_resume_allowed_multiple_times(self, db):
        """resume 不改快照：顺序多次恢复各自新 session（幂等语义）。"""
        from app.services.project_runtime.resume import resume_runtime_snapshot

        sid_a = "rt-multi-session-a"
        await _seed_session(sid_a)
        snapshot = await _freeze(db, sid_a)
        r1 = await resume_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            user_id="u_rt",
        )
        r2 = await resume_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            user_id="u_rt",
        )
        assert r1.session_id != r2.session_id
        assert r2.mapspec_rebound

    async def test_freeze_reasons_and_repeated_freeze_bounded(self, db):
        from app.services.project_runtime.freeze import FreezeError
        from app.services.project_runtime.freeze import save_runtime_snapshot
        from app.services.project_runtime.store import (
            MAX_RUNTIME_SNAPSHOTS_PER_PROJECT,
            get_project_runtime_store,
        )

        sid_a = "rt-bnd-session-a"
        await _seed_session(sid_a)
        snap1 = await _freeze(db, sid_a, label="first")
        snap2 = await save_runtime_snapshot(
            db, session_id=sid_a, project_id="proj-rt1", user_id="u_rt",
            freeze_reason="named_checkpoint",
        )
        assert snap1.snapshot_id != snap2.snapshot_id
        assert snap2.freeze_reason == "named_checkpoint"
        listed = await get_project_runtime_store().list_snapshots("proj-rt1")
        assert len(listed) <= MAX_RUNTIME_SNAPSHOTS_PER_PROJECT

        # 无 spec 的会话：拒绝冻结（409 面）
        with pytest.raises(FreezeError) as exc:
            await save_runtime_snapshot(
                db, session_id="rt-empty-session", project_id="proj-rt1",
                user_id="u_rt",
            )
        assert exc.value.reason == "no_mapspec"

    async def test_permission_changed_disclosed(self, db):
        from app.services.project_runtime.validation import (
            validate_runtime_snapshot,
        )

        sid_a = "rt-perm-session-a"
        await _seed_session(sid_a)
        snapshot = await _freeze(db, sid_a, user_id="u_rt")
        report = await validate_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            current_user_id="u_colleague",  # 同项目不同用户
        )
        assert report.permission == "changed"
        assert any(
            f.state == "permission_changed" for f in report.findings
        )

    async def test_ledger_only_contract_restored_via_workspace_pointer(self, db):
        """chart/table/export 等非 spec 源契约：workspace snapshot 指针补恢复。"""
        from app.services.artifact_registry import list_artifacts
        from app.services.project_runtime.resume import resume_runtime_snapshot

        sid_a = "rt-ledger-session-a"
        await _seed_session(sid_a)
        # 账本独有产物（不在 MapSpec sources 里）—— workspace snapshot
        # materialize=claimed 会给它 durable 指针
        chart_payload = {"kind": "bar", "series": [1, 2, 3]}
        chart_ref = await session_data_manager.store(
            sid_a, chart_payload, prefix="chart")
        await register_artifact(
            sid_a, artifact_id=chart_ref, producer_tool="make_chart",
            artifact_type="chart",
        )
        snapshot = await _freeze(db, sid_a)
        assert snapshot.workspace_snapshot_id
        # spec source 面不含 chart_ref：runtime manifest 无其绑定
        assert chart_ref not in _bindings_by_ref(snapshot)

        await _kill_session(sid_a)
        receipt = await resume_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            user_id="u_rt",
        )
        sid_b = receipt.session_id
        # 账本契约仍被完整恢复（payload 从 workspace 指针读回 + 新 id 注册）
        assert chart_ref in receipt.ref_map
        records = {r.artifact_id: r for r in await list_artifacts(sid_b)}
        new_chart = receipt.ref_map[chart_ref]
        assert new_chart in records
        assert records[new_chart].status != "expired"

    async def test_live_fallback_digest_drift_refused(self, db):
        """指针读失败 + 旧会话载荷漂移 → 拒绝（不把漂移内容塞进绑定）。"""
        from app.services.project_runtime.resume import resume_runtime_snapshot
        from app.services.workspace.durability import _resolve_content_path

        sid_a = "rt-drift-session-a"
        _, ref_a, _ghost = await _seed_session(sid_a)
        snapshot = await _freeze(db, sid_a)
        binding = _bindings_by_ref(snapshot)[ref_a]
        # 1) blob 消失（指针读失败）
        path = _resolve_content_path(binding.durable_pointer.content_location)
        path.unlink()
        # 2) 旧会话载荷被改写（digest 漂移）
        await session_data_manager.overwrite(sid_a, ref_a, {
            "type": "FeatureCollection", "features": [],
            "tampered": True,
        })
        receipt = await resume_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            user_id="u_rt",
        )
        assert ref_a in receipt.missing_refs
        assert ref_a not in receipt.ref_map

    async def test_giant_raster_budget_skip_honest(self, db):
        """巨型 raster：freeze 预算跳过（零字节物化）+ 如实披露。"""
        from app.services.artifact_registry import (
            raster_png_path,
            register_artifact,
        )
        from app.services.mapspec.store import MapSpecStore
        from app.services.project_runtime.resume import resume_runtime_snapshot

        sid_a = "rt-raster-session-a"
        raster_ref = "ref:raster/giant1"
        png = raster_png_path(sid_a, raster_ref)
        png.parent.mkdir(parents=True, exist_ok=True)
        png.write_bytes(b"\x89PNG" + b"0" * 4096)  # 4KB 假 PNG
        await register_artifact(
            sid_a, artifact_id=raster_ref, producer_tool="render",
            artifact_type="raster",
        )
        spec = {
            "sources": {
                "bg": {"type": "raster", "imageRef": raster_ref,
                       "bounds": [1, 2, 3, 4]},
            },
            "layers": [{"id": "bg", "source": "bg"}],
            "view": {"zoom": 9},
        }
        await MapSpecStore().save_mapspec(sid_a, spec, mutation_revision=1)
        snapshot = await _freeze(db, sid_a, budget_bytes=64)  # 远小于 4KB
        binding = _bindings_by_ref(snapshot)[raster_ref]
        assert binding.promotion_class in ("oversized", "quota_blocked")
        assert binding.durable_pointer is None
        assert snapshot.disclosure["promotion"]["budget_skips"] >= 1

        # 恢复：raster 无指针无 JSON 载荷 → needs_recompute（诚实）
        await _kill_session(sid_a)
        receipt = await resume_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            user_id="u_rt",
        )
        assert raster_ref in receipt.missing_refs

    async def test_gc_protection_covers_runtime_manifest_pointers(self, db):
        """runtime manifest 的 durable 指针是 GC 保护输入（P1 review fix）。"""
        from app.services.data_lifecycle.quota import (
            workspace_snapshot_protected_pointers,
        )

        sid_a = "rt-gc-session-a"
        _, ref_a, _ghost = await _seed_session(sid_a)
        snapshot = await _freeze(db, sid_a)
        binding = _bindings_by_ref(snapshot)[ref_a]
        assert binding.durable_pointer is not None
        locations, shas, truncated = workspace_snapshot_protected_pointers()
        assert not truncated
        assert binding.durable_pointer.content_location in locations
        assert binding.durable_pointer.content_payload_sha256 in shas

    async def test_compose_lane_respects_shared_budget(self, db):
        """预算收紧时组合 lane（workspace claimed 物化）同受预算闸（P1 fix）。"""
        from app.services.project_runtime.resume import resume_runtime_snapshot

        sid_a = "rt-budget-session-a"
        await _seed_session(sid_a)
        chart = await session_data_manager.store(
            sid_a, {"kind": "bar", "series": list(range(32))}, prefix="chart")
        await register_artifact(
            sid_a, artifact_id=chart, producer_tool="make_chart",
            artifact_type="chart",
        )
        # 预算 8 字节：任何载荷（spec 源 + 账本契约）都超 —— 全链零字节物化
        snapshot = await _freeze(db, sid_a, budget_bytes=8)
        durable = [
            b for b in snapshot.source_bindings if b.durable_pointer is not None
        ]
        assert durable == []
        receipt = await resume_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            user_id="u_rt",
        )
        # 组合 lane 未偷跑物化：chart 载荷不在恢复面（诚实 expired 披露），
        # 且 chart 不进 missing_refs（它本就不是 spec 源绑定）。
        assert chart not in receipt.ref_map
        assert chart not in receipt.missing_refs
        assert receipt.artifacts_expired >= 1

    async def test_org_member_freeze_sees_datasets(self, db):
        """org 成员（非 owner）冻结：dataset 描述符经 org_id 透传可见（P2 fix）。"""
        from app.core.database import SessionLocal
        from app.models.db_model import Organization, User

        with SessionLocal() as s:
            s.merge(Organization(id=7, name="org-rt", slug="org-rt"))
            s.merge(User(id="u_member", username="u_member",
                         email="m@example.com", password_hash="x",
                         role="viewer", is_active=True, org_id=7))
            from app.models.project import Project

            s.query(Project).filter(Project.id == "proj-rt1").update(
                {"org_id": 7})
            s.commit()
        sid_a = "rt-org-session-a"
        await _seed_session(sid_a)
        snapshot = await _freeze(db, sid_a, user_id="u_member", org_id=7)
        assert snapshot.datasets, "org member freeze must see dataset descriptors"
        assert snapshot.datasets[0].dataset_id == "ds-rt1"

    async def test_capability_verification_failure_disclosed(self, db, monkeypatch):
        """capability 校验执行失败 → unknown 判定 + finding（不静默 ready）。"""
        from app.services.project_runtime.validation import (
            validate_runtime_snapshot,
        )

        sid_a = "rt-capx-session-a"
        await _seed_session(sid_a, with_capabilities=True)
        snapshot = await _freeze(db, sid_a)
        assert snapshot.plan.required_capabilities

        def _boom(capability_ids, **kwargs):
            raise RuntimeError("resolver down")

        monkeypatch.setattr(
            "app.services.capability_runtime.snapshot"
            ".build_capability_runtime_snapshot", _boom)
        report = await validate_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            current_user_id="u_rt",
        )
        assert report.capability_verdicts.get("query_local_poi") == "unknown"
        assert any(
            f.state == "capability_unavailable"
            and f.subject == "capabilities"
            for f in report.findings
        )
        assert report.status == "capability_unavailable"

    async def test_sensitive_binding_never_restored(self, db, monkeypatch):
        """sensitive 分类只披露不搬运：恢复拒绝其载荷（P3 review fix）。"""
        from app.services.artifact_registry import update_record_metadata
        from app.services.project_runtime.resume import resume_runtime_snapshot

        sid_a = "rt-sens-session-a"
        spec, ref_a, ghost = await _seed_session(sid_a)
        await update_record_metadata(
            sid_a, ref_a, metadata={"sensitive": True})
        snapshot = await _freeze(db, sid_a)
        binding = _bindings_by_ref(snapshot)[ref_a]
        assert binding.promotion_class == "sensitive"
        assert binding.durable_pointer is None
        receipt = await resume_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            user_id="u_rt",
        )
        # 活载荷在场也不搬运（digest 校验兜底之外的敏感面硬拒绝）
        assert ref_a not in receipt.ref_map
        assert ref_a in receipt.missing_refs

    async def test_zero_quota_compose_lane_zero_bytes(self, db, monkeypatch):
        """quota 余量恰为 0（持久稳态）：组合 lane 也不得物化（NEW-1 fix）。"""
        from app.services.project_runtime import promotion as promo_mod
        from app.services.project_runtime.freeze import save_runtime_snapshot

        sid_a = "rt-zq-session-a"
        await _seed_session(sid_a)
        chart = await session_data_manager.store(
            sid_a, {"kind": "bar", "series": [1]}, prefix="chart")
        await register_artifact(
            sid_a, artifact_id=chart, producer_tool="make_chart",
            artifact_type="chart",
        )
        monkeypatch.setattr(
            promo_mod, "_quota_remaining_bytes",
            staticmethod(lambda db_arg, pid: 0))
        snapshot = await save_runtime_snapshot(
            db, session_id=sid_a, project_id="proj-rt1", user_id="u_rt",
        )
        durable = [
            b for b in snapshot.source_bindings if b.durable_pointer is not None
        ]
        assert durable == []
        assert snapshot.disclosure["promotion"]["counts"].get(
            "quota_blocked", 0) >= 1
        # 组合面同样零指针（workspace snapshot 里 chart 不得有 durable 指针）
        from app.services.workspace.snapshot import get_workspace_snapshot_service

        ws = get_workspace_snapshot_service().load_snapshot(
            sid_a, snapshot.workspace_snapshot_id, project_id="proj-rt1")
        assert chart not in ws.durable_pointers

    async def test_sensitive_ledger_only_artifact_not_materialized(self, db):
        """ledger-only sensitive 契约：组合 lane 也不物化（NEW-4a fix）。"""
        from app.services.artifact_registry import update_record_metadata

        sid_a = "rt-sens2-session-a"
        await _seed_session(sid_a)
        secret = await session_data_manager.store(
            sid_a, {"private": True}, prefix="geojson")
        await register_artifact(
            sid_a, artifact_id=secret, producer_tool="internal",
            artifact_type="table",
        )
        await update_record_metadata(
            sid_a, secret, metadata={"sensitive": True})
        snapshot = await _freeze(db, sid_a)
        from app.services.workspace.snapshot import get_workspace_snapshot_service

        ws = get_workspace_snapshot_service().load_snapshot(
            sid_a, snapshot.workspace_snapshot_id, project_id="proj-rt1")
        assert secret not in ws.durable_pointers

    async def test_no_secret_material_in_manifest(self, db):
        """snapshot 绝不携带 secret/token/raw LLM state（红线检查）。"""
        sid_a = "rt-sec-session-a"
        await _seed_session(sid_a)
        snapshot = await _freeze(db, sid_a)
        blob = json.dumps(snapshot.model_dump(mode="json"))
        for forbidden in ("api_key", "token", "password", "secret"):
            assert forbidden not in blob.lower()
        # mapspec_doc 有界（不含 FeatureCollection 载荷——载荷在 ref 里）
        assert "features" not in json.dumps(snapshot.mapspec_doc)


# ═══ 事件循环纪律：大文档校验/取消不得在循环上同步跑 ═══════════════════


class TestEventLoopDiscipline:
    async def test_validate_offloads_load_and_fingerprint(self, db, monkeypatch):
        """≥4MB mapspec_doc 的 validate：读盘 + model_validate + 指纹必须全部
        卸载到线程（与 freeze/rebind/resume 同纪律）—— 同步跑会让同进程全部
        SSE 流停摆（#1629）。

        线程身份探针确定性断言卸载面；5ms 心跳探针佐证循环在 validate 期间
        保持响应（判据取 issue 允许的「与总时长解耦」—— Windows 上 GIL 让
        json.loads / model_validate / json.dumps 的 C 段即便在线程内也能造成
        数十 ms 级停顿，绝对阈值在本机不可达）。"""
        import asyncio
        import contextlib
        import threading
        import time
        from typing import Dict, List

        from app.services.project_runtime import validation as validation_mod
        from app.services.project_runtime.contract import (
            ProjectRuntimeSnapshotV1,
        )
        from app.services.project_runtime.rebind import canonical_fingerprint
        from app.services.project_runtime.store import (
            ProjectRuntimeStore,
            get_project_runtime_store,
        )
        from app.services.project_runtime.validation import (
            validate_runtime_snapshot,
        )

        doc = {
            "sources": {"s": {"type": "geojson",
                              "url": "https://x.example/d.json"}},
            "layers": [{"id": "l1", "source": "s"}],
            "padding": ["x" * 1024] * 16384,  # 序列化后 ≈16MB（冻结上限）
        }
        snapshot = ProjectRuntimeSnapshotV1(
            snapshot_id="prs-loop1", project_id="proj-rt1",
            mapspec_doc=doc, mapspec_fingerprint=canonical_fingerprint(doc),
        )
        assert await get_project_runtime_store().save(snapshot)

        # 间谍：记录重活实际运行的线程（委托真实现，绝不 mock 掉被测逻辑）
        loop_thread = threading.get_ident()
        seen: Dict[str, int] = {}
        real_load = ProjectRuntimeStore.load

        def _spy_load(self, project_id, snapshot_id):
            seen["load"] = threading.get_ident()
            return real_load(self, project_id, snapshot_id)

        def _spy_fingerprint(doc_arg):
            seen["fingerprint"] = threading.get_ident()
            return canonical_fingerprint(doc_arg)

        monkeypatch.setattr(ProjectRuntimeStore, "load", _spy_load)
        monkeypatch.setattr(
            validation_mod, "canonical_fingerprint", _spy_fingerprint
        )

        gaps: List[float] = []

        async def _heartbeat() -> None:
            last = time.perf_counter()
            while True:
                await asyncio.sleep(0.005)
                now = time.perf_counter()
                gaps.append((now - last) * 1000.0)
                last = now

        hb = asyncio.create_task(_heartbeat())
        await asyncio.sleep(0.1)  # 预热：心跳先稳定跑起来
        gaps.clear()
        t0 = time.perf_counter()
        report = await validate_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id="prs-loop1",
            current_user_id="u_rt",
        )
        total_ms = (time.perf_counter() - t0) * 1000.0
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb
        validate_gaps = list(gaps)

        assert report.mapspec_integrity_ok is True  # 指纹算得且一致
        assert report.status == "ready"
        assert total_ms > 15.0, "probe saw no real work (load + fingerprint)"
        # 卸载面：读盘（json 解析 + model_validate）与指纹都不得在事件循环上
        assert seen["load"] != loop_thread, "store.load ran on the event loop"
        assert seen["fingerprint"] != loop_thread, (
            "canonical_fingerprint ran on the event loop"
        )

        # 金丝雀：同样的指纹计算直接跑在循环上时，心跳必须测不到任何 tick
        # —— 证明探针能侦测循环阻塞（防假绿）。
        gaps.clear()
        canonical_fingerprint(doc)
        assert not gaps, "heartbeat probe cannot detect loop blocking"

        # 心跳佐证：循环在 validate 全程保持响应（修复前同步读盘 + 同步指纹
        # 会把循环独占到整个 validate 结束）。
        assert validate_gaps, "event loop never ran while validate was in flight"
        assert max(validate_gaps) < total_ms, (
            "event loop was blocked for the whole validate duration"
        )


# ═══ 回执诚实性：注册失败不得静默跳账 ═════════════════════════════════


class TestResumeReceiptHonesty:
    async def test_register_failure_disclosed_as_degraded(self, db, monkeypatch):
        """账本重注册失败（register_artifact 返回 None）时，该契约行绝不
        凭空消失 —— 必须进 degraded 如实披露（#1631）。"""
        from app.services.project_runtime.resume import resume_runtime_snapshot

        sid_a = "rt-regfail-session-a"
        await _seed_session(sid_a)
        snapshot = await _freeze(db, sid_a)
        await _kill_session(sid_a)

        async def _register_none(*args, **kwargs):
            return None

        monkeypatch.setattr(
            "app.services.artifact_registry.register_artifact", _register_none
        )
        receipt = await resume_runtime_snapshot(
            db, project_id="proj-rt1", snapshot_id=snapshot.snapshot_id,
            user_id="u_rt",
        )
        reasons = {d.get("reason") for d in receipt.degraded}
        assert "register_failed" in reasons
        assert receipt.artifacts_registered == 0
