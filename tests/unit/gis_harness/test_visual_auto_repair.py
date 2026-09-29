"""C13 AUTO_SAFE 自动修复通道回归（默认关；开启后有界 + user-wins）。

不变式：
1. env 关闭 = 整体缺席（零突变、零副作用 —— F15 语义保留）；
2. 开启后只执行 auto_safe 分级：error / 触锁 / 不可映射 → 不自动修
   （needs_approval 走 approval UI 面）；
3. 预算：per-revision ≤1、per-session ≤4（无新观察不连改）；
4. 拒绝记忆同样约束自动通道（rejected 是对缺陷原因的裁决）；
5. 收敛硬停诚实回执；成功后 recurrence 重置 + 决策入账。
"""

from __future__ import annotations

import shutil
import uuid

import pytest

from app.services.gis_harness.visual_observation.auto_repair import (
    BUDGET_KEY,
    MAX_AUTO_PER_SESSION,
    auto_repair_enabled,
    run_auto_repair_pass,
)
from app.services.gis_harness.visual_observation.repair_decisions import (
    DECISION_AUTO_APPLIED,
    load_decisions,
)
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    MapSpecResult,
    SetWorkbenchStateIntent,
    UpsertLayerIntent,
)
from app.services.session_data import session_data_manager
from app.services.session_plan import ensure_session_plan_slot, save_session_plan


def _geojson():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [104.0, 30.6]},
             "properties": {}},
        ],
    }


def _finding(entity="L1", severity="warning",
             fid="visual:visual_label_collision:fp01", fp="fp-label-01",
             observed_revision=0):
    return {
        "domain": "visual",
        "code": "visual_label_collision",
        "severity": severity,
        "source": "visual_observation_provider",
        "scope": "map",
        "affected_entity": entity,
        "evidence": "注记与 L1 overlap 重叠",
        "repair_class": "",
        "retryable": False,
        "blocks_completion": False,
        "degradation_only": True,
        "finding_class": "visual",
        "user_owned": False,
        "finding_id": fid,
        "recurrence_fingerprint": fp,
        "observed_revision": observed_revision,
    }


@pytest.fixture
def sid():
    return f"vauto-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
async def _clean(sid):
    from app.services.mapspec.shared_engine import (
        get_shared_lifecycle_engine,
    )

    # 共享 engine 的 healer 收敛账本是进程级实例状态 —— 逐测试清零，
    # 防止后续新增真实 apply 用例时的顺序脆弱。
    get_shared_lifecycle_engine()._visual_heal_ledger.clear()
    await session_data_manager.clear_session(sid)
    yield
    get_shared_lifecycle_engine()._visual_heal_ledger.clear()
    await session_data_manager.clear_session(sid)
    from app.services.mapspec.store import BASE_STORAGE_DIR

    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


async def _seed(sid: str, findings: list, *, lock_layer: bool = False) -> int:
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(sid, InitProjectIntent())
    await engine.apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={"id": "L1", "source": "s1", "type": "symbol",
                   "layout": {"text-field": "{name}", "text-size": 14},
                   "paint": {}},
            source_data=_geojson(),
        ),
    )
    if lock_layer:
        await engine.apply_mutation(
            sid,
            SetWorkbenchStateIntent(
                doc={"version": 5, "mode": "explore", "groups": [],
                     "lockedLayerIds": ["L1"]}),
        )
    state = await session_data_manager.get_map_state(sid)
    revision = int(state.get("_cartographic_mutation_revision") or 0)
    stamped = []
    for f in findings:
        f = dict(f)
        f["observed_revision"] = revision
        stamped.append(f)
    plan = await ensure_session_plan_slot(sid)
    plan.gis_chapter = {
        "plan_id": "p", "query": "q",
        "map_product": {"visual_findings": stamped},
    }
    await save_session_plan(plan)
    return revision


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("GIS_VISUAL_AUTO_REPAIR", raising=False)
    assert auto_repair_enabled() is False


@pytest.mark.asyncio
async def test_disabled_env_means_noop(sid, monkeypatch):
    monkeypatch.delenv("GIS_VISUAL_AUTO_REPAIR", raising=False)
    revision = await _seed(sid, [_finding()])
    receipt = await run_auto_repair_pass(sid)
    assert receipt == {"ran": True, "applied": False, "reason": "disabled",
                       "proposal_id": "", "mutation_revision": 0}
    state = await session_data_manager.get_map_state(sid)
    assert int(state.get("_cartographic_mutation_revision") or 0) == revision


@pytest.mark.asyncio
async def test_auto_safe_finding_is_applied_with_ledger(sid, monkeypatch):
    monkeypatch.setenv("GIS_VISUAL_AUTO_REPAIR", "1")
    revision = await _seed(sid, [_finding()])
    receipt = await run_auto_repair_pass(sid)
    assert receipt["applied"] is True, receipt
    assert receipt["mutation_revision"] > revision
    # spec 被 heal 推进（label layout patch 落在 L1）。
    engine = MapSpecLifecycleEngine()
    loaded = await engine.store.get_mapspec(sid)
    l1 = next(ly for ly in loaded["layers"] if ly.get("id") == "L1")
    assert any(k.startswith("text-") for k in (l1.get("layout") or {}))
    # 决策入账 + 预算消费。
    decisions = await load_decisions(sid)
    assert decisions and decisions[-1]["decision"] == DECISION_AUTO_APPLIED
    state = await session_data_manager.get_map_state(sid)
    budget = state.get(BUDGET_KEY) or {}
    assert int(budget.get("session_total") or 0) == 1


@pytest.mark.asyncio
async def test_error_severity_is_never_auto_applied(sid, monkeypatch):
    monkeypatch.setenv("GIS_VISUAL_AUTO_REPAIR", "1")
    await _seed(sid, [_finding(severity="error")])
    receipt = await run_auto_repair_pass(sid)
    assert receipt["applied"] is False
    assert receipt["reason"] == "no_auto_safe_findings"
    assert receipt["skipped"][0]["reason"] == "needs_approval"


@pytest.mark.asyncio
async def test_locked_layer_downgrades_to_approval_face(sid, monkeypatch):
    monkeypatch.setenv("GIS_VISUAL_AUTO_REPAIR", "1")
    await _seed(sid, [_finding(entity="L1")], lock_layer=True)
    revision = await _seed(sid, [_finding(entity="L1")], lock_layer=True)
    receipt = await run_auto_repair_pass(sid)
    assert receipt["applied"] is False
    assert receipt["reason"] == "no_auto_safe_findings"
    assert receipt["skipped"][0]["reason"] == "needs_approval"
    # user-pinned 状态未被触碰。
    state = await session_data_manager.get_map_state(sid)
    assert int(state.get("_cartographic_mutation_revision") or 0) == revision


@pytest.mark.asyncio
async def test_stale_findings_block_auto_pass(sid, monkeypatch):
    monkeypatch.setenv("GIS_VISUAL_AUTO_REPAIR", "1")
    await _seed(sid, [_finding()])
    # 观察后又推进一代 → 存储证据过期。
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={"id": "L2", "source": "s1", "type": "circle",
                   "paint": {"circle-color": "#0f0"}},
            source_data=_geojson(),
        ),
    )
    receipt = await run_auto_repair_pass(sid)
    assert receipt["applied"] is False
    assert receipt["reason"] == "stale_findings"


@pytest.mark.asyncio
async def test_rejected_cause_is_not_auto_applied(sid, monkeypatch):
    monkeypatch.setenv("GIS_VISUAL_AUTO_REPAIR", "1")
    await _seed(sid, [_finding()])
    from app.services.gis_harness.visual_observation.repair_decisions import (
        DECISION_REJECTED,
        record_decision,
    )

    await record_decision(
        sid, decision=DECISION_REJECTED, proposal_id="vrepair-manual-1",
        recurrence_fingerprints=["fp-label-01"], origin="user", revision=1)
    receipt = await run_auto_repair_pass(sid)
    assert receipt["applied"] is False
    assert receipt["reason"] == "no_auto_safe_findings"
    assert receipt["skipped"][0]["reason"] == "user_rejected"


@pytest.mark.asyncio
async def test_revision_budget_allows_one_pass_per_revision(
        sid, monkeypatch):
    """heal 未推进 revision 的病态场景（mock）下：同 revision 只许一次。"""
    monkeypatch.setenv("GIS_VISUAL_AUTO_REPAIR", "1")
    revision = await _seed(sid, [_finding()])
    engine = MapSpecLifecycleEngine()

    async def _flat_apply(*args, **kwargs):
        return MapSpecResult(is_error=False, origin="system",
                             mutation_revision=revision)

    monkeypatch.setattr(engine, "apply_visual_heal_patch", _flat_apply)
    first = await run_auto_repair_pass(sid, engine=engine)
    assert first["applied"] is True
    second = await run_auto_repair_pass(sid, engine=engine)
    assert second["applied"] is False
    assert second["reason"] == "revision_budget_exhausted"


@pytest.mark.asyncio
async def test_session_budget_exhaustion(sid, monkeypatch):
    monkeypatch.setenv("GIS_VISUAL_AUTO_REPAIR", "1")
    revision = await _seed(sid, [_finding()])
    engine = MapSpecLifecycleEngine()

    async def _flat_apply(*args, **kwargs):
        return MapSpecResult(is_error=False, origin="system",
                             mutation_revision=revision)

    monkeypatch.setattr(engine, "apply_visual_heal_patch", _flat_apply)
    # 直接预置会话总预算到上限。
    await session_data_manager.set_map_state(
        sid, BUDGET_KEY,
        {"session_total": MAX_AUTO_PER_SESSION, "revisions": {}})
    receipt = await run_auto_repair_pass(sid, engine=engine)
    assert receipt["reason"] == "session_budget_exhausted"


@pytest.mark.asyncio
async def test_convergence_exhausted_is_honest_hard_stop(
        sid, monkeypatch):
    monkeypatch.setenv("GIS_VISUAL_AUTO_REPAIR", "1")
    revision = await _seed(sid, [_finding()])
    engine = MapSpecLifecycleEngine()

    async def _exhausted(*args, **kwargs):
        return MapSpecResult(
            is_error=True, origin="system",
            error_code="HEAL_CONVERGENCE_EXHAUSTED",
            error_msg="exhausted", correction_hint="请人工研判。",
            mutation_revision=revision)

    monkeypatch.setattr(engine, "apply_visual_heal_patch", _exhausted)
    receipt = await run_auto_repair_pass(sid, engine=engine)
    assert receipt["applied"] is False
    assert receipt["hard_stop"] is True
    assert receipt["reason"] == "HEAL_CONVERGENCE_EXHAUSTED"
    # P2-3：失败收场如实入账（auto_failed，不与 auto_applied 混淆）。
    from app.services.gis_harness.visual_observation.repair_decisions import (
        DECISION_AUTO_FAILED,
    )

    decisions = await load_decisions(sid)
    assert decisions and decisions[-1]["decision"] == DECISION_AUTO_FAILED
    assert decisions[-1]["reason"] == "HEAL_CONVERGENCE_EXHAUSTED"


@pytest.mark.asyncio
async def test_superseded_and_apply_error_record_auto_failed(
        sid, monkeypatch):
    monkeypatch.setenv("GIS_VISUAL_AUTO_REPAIR", "1")
    revision = await _seed(sid, [_finding()])
    engine = MapSpecLifecycleEngine()

    async def _superseded(*args, **kwargs):
        return MapSpecResult(is_error=False, origin="system",
                             superseded=True, mutation_revision=revision)

    monkeypatch.setattr(engine, "apply_visual_heal_patch", _superseded)
    receipt = await run_auto_repair_pass(sid, engine=engine)
    assert receipt["applied"] is False
    assert receipt["reason"] == "revision_conflict"
    from app.services.gis_harness.visual_observation.repair_decisions import (
        DECISION_AUTO_FAILED,
    )

    decisions = await load_decisions(sid)
    assert decisions and decisions[-1]["decision"] == DECISION_AUTO_FAILED


@pytest.mark.asyncio
async def test_duplicate_apply_still_records_decision(sid, monkeypatch):
    """幂等重放（duplicate 世代）不推进预算，但决策面如实记录。"""
    monkeypatch.setenv("GIS_VISUAL_AUTO_REPAIR", "1")
    revision = await _seed(sid, [_finding()])
    engine = MapSpecLifecycleEngine()

    async def _dup_apply(*args, **kwargs):
        return MapSpecResult(is_error=False, origin="system",
                             duplicate=True, mutation_revision=revision)

    monkeypatch.setattr(engine, "apply_visual_heal_patch", _dup_apply)
    receipt = await run_auto_repair_pass(sid, engine=engine)
    assert receipt["applied"] is True and receipt["duplicate"] is True
    state = await session_data_manager.get_map_state(sid)
    budget = state.get(BUDGET_KEY) or {}
    assert int(budget.get("session_total") or 0) == 0   # duplicate 不计预算
    decisions = await load_decisions(sid)
    assert decisions and decisions[-1]["decision"] == DECISION_AUTO_APPLIED
