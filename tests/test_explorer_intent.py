"""Explorer intent detector tests"""
import pytest
from app.services.explorer.intent_detector import IntentDetector, ExploreDecision


def test_detects_poi_gap():
    detector = IntentDetector()
    result = detector.detect(
        user_query="分析海淀区学校分布",
        current_layers=[],
        session_history=[],
    )
    assert result.decision in ("auto_execute", "ask_user")
    assert result.confidence > 0.5


def test_skips_when_data_exists():
    detector = IntentDetector()
    result = detector.detect(
        user_query="分析海淀区学校分布",
        current_layers=[{"name": "学校", "feature_count": 200}],
        session_history=[],
    )
    assert result.decision == "skip"


def test_explicit_deep_search_command():
    detector = IntentDetector()
    result = detector.detect(
        user_query="帮我深度搜索一下海淀区的医院",
        current_layers=[],
        session_history=[],
    )
    assert result.decision == "auto_execute"
    assert result.confidence == 1.0


def test_low_confidence_query():
    detector = IntentDetector()
    result = detector.detect(
        user_query="你好",
        current_layers=[],
        session_history=[],
    )
    assert result.decision == "skip"
