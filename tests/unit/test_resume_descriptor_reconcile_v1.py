"""Resume 语义对账测试（H08/C4 —— descriptor_fingerprint 进入恢复面）。

- 建锚：ref_evidence 快照携带 descriptor_fingerprint（语义 store 缺席无键）；
- 恢复：语义 store 记录跨 session 迁移（lazy upgrade，指纹不变）；
- 失效：建锚后数据语义推进 → 恢复后 drifted → 裁决 stale（复用失效）；
- 诚实迁移：老会话（无 descriptor）迁移 0 条，恢复照常 live；
- 迁移失败：计数披露，不阻断恢复，验证面按 unknown 披露不误判 stale。
"""
import uuid

import pytest

from app.lib.gis.dataset_profile import DatasetProfile
from app.services.dataset_semantics import (
    DatasetSemanticStore,
    derive_descriptor,
    get_dataset_semantic_store,
    reset_dataset_semantic_store,
)
from app.services.dataset_semantics import store as store_mod
from app.services.gis_harness.resume_anchor import (
    build_anchor,
    resume_from_anchor,
    save_anchor,
)
from app.services.gis_harness.resume_verify import (
    VERDICT_LIVE,
    VERDICT_STALE,
)
from app.services.session_data import session_data_manager
from app.services.session_plan import (
    SessionPlan,
    save_session_plan,
)


class _FakeDb:
    def __init__(self):
        self.rows = {}

    def add(self, row):
        if not getattr(row, "id", None):
            row.id = str(uuid.uuid4())
        self.rows[row.id] = row

    async def commit(self):
        return None

    async def refresh(self, row):
        return None

    async def get(self, model, pk):
        return self.rows.get(pk)

    class _Result:
        def __init__(self, row):
            self._row = row

        def scalar_one_or_none(self):
            return self._row

    async def execute(self, stmt):
        from app.models.project import WorkflowResumeAnchor

        row = None
        for r in self.rows.values():
            if isinstance(r, WorkflowResumeAnchor):
                row = r
        return self._Result(row)


def _payload(tag):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"tag": tag},
             "geometry": {"type": "Point", "coordinates": [104.0, 30.6]}},
        ],
    }


def _descriptor(value: int, key: str):
    p = DatasetProfile(
        source="ref_descriptor", feature_count=10 + value,
        geometry_types=["Polygon"], crs="EPSG:4326",
        fields={"tag": "string"}, fields_status="explicit",
    )
    return derive_descriptor(
        p, dataset_key=key,
        features=[{"properties": {"tag": f"t{i}"}} for i in range(5)])


async def _seed(sid: str) -> None:
    await session_data_manager.clear_session(sid)
    await save_session_plan(SessionPlan(
        envelope_id=f"sp-{sid[:12]}", session_id=sid,
        user_goal="h08 语义对账", gis_chapter={"query": "q"},
        progress=[],
    ))


@pytest.fixture()
def sem_store(tmp_path, monkeypatch):
    store = DatasetSemanticStore(base_dir=tmp_path)
    monkeypatch.setattr(store_mod, "_store", store)
    yield store
    reset_dataset_semantic_store()


@pytest.mark.asyncio
async def test_anchor_snapshots_descriptor_fingerprint(sem_store):
    sid = f"h08-c4-a-{uuid.uuid4().hex[:6]}"
    await _seed(sid)
    ref = await session_data_manager.store(sid, _payload("a"))
    d = _descriptor(1, ref)
    await sem_store.put(sid, ref, d)

    anchor = await build_anchor(sid)
    entry = anchor["ref_evidence"].get(ref) or {}
    assert entry.get("descriptor_fingerprint") == d.descriptor_fingerprint

    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_resume_migrates_semantic_record(sem_store):
    sid = f"h08-c4-m-{uuid.uuid4().hex[:6]}"
    await _seed(sid)
    ref = await session_data_manager.store(sid, _payload("m"))
    d = _descriptor(1, ref)
    await sem_store.put(sid, ref, d)

    db = _FakeDb()
    saved = await save_anchor(db, session_id=sid, user_id="u-h08")
    result = await resume_from_anchor(db, anchor_id=saved["anchor_id"],
                                      user_id="u-h08")
    new_sid = result["session_id"]
    new_ref = result["ref_map"][ref]
    # lazy upgrade：新 session 语义 store 现读同一指纹（内容寻址）。
    rec = await get_dataset_semantic_store().get(new_sid, new_ref)
    assert rec.ok
    assert rec.descriptor.descriptor_fingerprint == d.descriptor_fingerprint
    assert result.get("descriptor_migration", {"migrated": 0})["migrated"] == 1
    verdict = result["ref_verdicts"][new_ref]
    assert verdict["verdict"] == VERDICT_LIVE

    await session_data_manager.clear_session(sid)
    await session_data_manager.clear_session(new_sid)


@pytest.mark.asyncio
async def test_semantic_advance_invalidates_reuse_on_resume(sem_store):
    """建锚后数据语义推进 → 恢复后 drifted → stale（stale reuse 失效）。"""
    sid = f"h08-c4-s-{uuid.uuid4().hex[:6]}"
    await _seed(sid)
    ref = await session_data_manager.store(sid, _payload("s"))
    old = _descriptor(1, ref)
    await sem_store.put(sid, ref, old)

    db = _FakeDb()
    saved = await save_anchor(db, session_id=sid, user_id="u-h08")
    # 建锚后：同 ref 语义版本推进（feature_count/值域证据变化 → 异指纹）
    new = _descriptor(2, ref)
    assert new.descriptor_fingerprint != old.descriptor_fingerprint
    await sem_store.put(sid, ref, new)

    result = await resume_from_anchor(db, anchor_id=saved["anchor_id"],
                                      user_id="u-h08")
    new_ref = result["ref_map"][ref]
    verdict = result["ref_verdicts"][new_ref]
    assert verdict["verdict"] == VERDICT_STALE
    assert any("descriptor" in r for r in verdict.get("reasons", []))

    await session_data_manager.clear_session(sid)
    await session_data_manager.clear_session(result["session_id"])


@pytest.mark.asyncio
async def test_legacy_session_without_descriptor_restores_live(sem_store):
    sid = f"h08-c4-l-{uuid.uuid4().hex[:6]}"
    await _seed(sid)
    ref = await session_data_manager.store(sid, _payload("l"))  # 老会话：无语义记录

    db = _FakeDb()
    saved = await save_anchor(db, session_id=sid, user_id="u-h08")
    result = await resume_from_anchor(db, anchor_id=saved["anchor_id"],
                                      user_id="u-h08")
    new_ref = result["ref_map"][ref]
    assert result["ref_verdicts"][new_ref]["verdict"] == VERDICT_LIVE
    chapter = (await __import__(
        "app.services.session_plan", fromlist=["load_session_plan"])
        .load_session_plan(result["session_id"]))
    migration = chapter.gis_chapter["resumed_from"]["descriptor_migration"]
    assert migration == {"migrated": 0, "failed": 0}

    await session_data_manager.clear_session(sid)
    await session_data_manager.clear_session(result["session_id"])


@pytest.mark.asyncio
async def test_migration_failure_disclosed_not_stale(sem_store, monkeypatch):
    """迁移失败 → 计数披露 + 验证面 unknown（绝不误判 stale）。"""
    sid = f"h08-c4-f-{uuid.uuid4().hex[:6]}"
    await _seed(sid)
    ref = await session_data_manager.store(sid, _payload("f"))
    d = _descriptor(1, ref)
    await sem_store.put(sid, ref, d)

    from app.services.dataset_semantics.store import PutResult

    async def _reject(self, session_id, dataset_key, descriptor):
        return PutResult(ok=False, reason_code="DESCRIPTOR_STORE_WRITE_FAILED")

    monkeypatch.setattr(
        "app.services.dataset_semantics.store.DatasetSemanticStore.put",
        _reject)
    db = _FakeDb()
    saved = await save_anchor(db, session_id=sid, user_id="u-h08")
    result = await resume_from_anchor(db, anchor_id=saved["anchor_id"],
                                      user_id="u-h08")
    new_ref = result["ref_map"][ref]
    assert result["descriptor_migration"]["failed"] == 1
    verdict = result["ref_verdicts"][new_ref]
    # 载荷/内容身份全部 live；语义对账 unknown 只披露，不推翻 live。
    assert verdict["verdict"] == VERDICT_LIVE
    assert any("descriptor" in r for r in verdict.get("reasons", []))

    await session_data_manager.clear_session(sid)
    await session_data_manager.clear_session(result["session_id"])
