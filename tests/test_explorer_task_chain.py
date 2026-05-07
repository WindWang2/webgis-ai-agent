"""Explorer task chain integration tests"""
import pytest
from unittest.mock import patch, MagicMock
from app.services.explorer.orchestrator import ExplorerOrchestrator
from app.services.explorer.models import SearchContext
from app.services.explorer.intent_detector import IntentDetector


@pytest.mark.asyncio
async def test_orchestrator_start_and_status():
    """测试编排器启动任务和查询状态"""
    orchestrator = ExplorerOrchestrator()

    with patch("app.services.explorer.orchestrator.chain") as mock_chain:
        mock_result = MagicMock()
        mock_result.id = "test_task_123"
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
