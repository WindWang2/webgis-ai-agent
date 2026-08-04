"""Explorer task chain integration tests"""
import pytest
from unittest.mock import patch, MagicMock
from app.services.explorer.orchestrator import ExplorerOrchestrator
from app.services.explorer.models import SearchContext
from app.services.explorer.intent_detector import IntentDetector

# task_chain.py imports celery at module load; skip the task-body tests where
# celery isn't installed (CI installs it via requirements.txt).
task_chain = pytest.importorskip("app.tasks.explorer.task_chain", reason="celery not installed")


@pytest.mark.asyncio
async def test_orchestrator_start_and_status():
    """测试编排器启动任务和查询状态"""
    orchestrator = ExplorerOrchestrator()

    with patch("app.services.explorer.orchestrator.chain") as mock_chain:
        mock_result = MagicMock()
        mock_result.id = "test_task_123"
        # F-06 fix: start_exploration traverses result.parent to find the root
        # task. A MagicMock auto-creates a truthy .parent on every access, which
        # makes that traversal loop forever. Pin .parent to None so the loop
        # terminates (the result IS the root in this single-link mock).
        mock_result.parent = None
        mock_chain.return_value.apply_async.return_value = mock_result

        task_id = await orchestrator.start_exploration(
            query="海淀区学校",
            context=SearchContext(query="海淀区学校"),
        )

        assert task_id == "test_task_123"


@pytest.mark.asyncio
async def test_intent_detector_triggers_exploration():
    """测试意图检测器正确触发探索"""
    detector = IntentDetector()
    result = detector.detect(
        user_query="深度搜索北京医院",
        current_layers=[],
        session_history=[],
    )

    assert result.decision == "auto_execute"
    assert result.confidence == 1.0


def test_explore_decision_validation():
    """测试 ExploreDecision 模型验证"""
    from app.services.explorer.intent_detector import ExploreDecision

    decision = ExploreDecision(decision="auto_execute", confidence=0.8)
    assert decision.decision == "auto_execute"
    assert decision.confidence == 0.8

    with pytest.raises(ValueError):
        ExploreDecision(decision="invalid", confidence=0.5)


# ─── Session-store seam routing (review §3 item 3a) ───────────────────────
#
# _store_ref/_load_ref previously imported the module-level session_data_manager
# singleton (a per-process MemorySessionStore under USE_REDIS=false), so a ref
# stored in one prefork worker was invisible to the next stage running in a
# different worker. They now route through get_session_store(), the config-gated
# seam — which returns the Redis-backed store under USE_REDIS=true (shared across
# workers) and the memory store under eager mode. These tests pin that routing.


def test_store_load_ref_round_trip_through_seam(monkeypatch):
    """_store_ref then _load_ref round-trips data via get_session_store()."""
    from app.services.session_data_protocol import (
        get_session_store,
        set_active_session_store,
    )
    from app.services.session_data import MemorySessionStore

    # Inject a fresh memory store so the test is isolated from other tests' state.
    fake = MemorySessionStore()
    set_active_session_store(fake)
    try:
        ref_id = task_chain._store_ref({"hello": "world"}, task_id="t-seam", prefix="explorer")
        assert ref_id.startswith("ref:explorer-")
        loaded = task_chain._load_ref(ref_id, task_id="t-seam")
        assert loaded == {"hello": "world"}
        # Confirm the data landed in the injected store under the explorer namespace.
        assert get_session_store() is fake
    finally:
        set_active_session_store(None)


def test_store_ref_uses_seam_not_module_singleton(monkeypatch):
    """_store_ref must resolve the store via get_session_store() each call.

    Regression for the hard-coded `from app.services.session_data import
    session_data_manager` import: if _store_ref bound the singleton at import
    time, swapping the active store via set_active_session_store() would have
    no effect.
    """
    from app.services.session_data_protocol import set_active_session_store
    from app.services.session_data import MemorySessionStore

    seen_stores = []

    class TrackingStore(MemorySessionStore):
        async def store(self, session_id, data, prefix="data"):
            seen_stores.append(self)
            return await super().store(session_id, data, prefix=prefix)

    fake = TrackingStore()
    set_active_session_store(fake)
    try:
        task_chain._store_ref({"x": 1}, task_id="t-track", prefix="explorer")
        assert seen_stores == [fake], "_store_ref did not route through get_session_store()"
    finally:
        set_active_session_store(None)
