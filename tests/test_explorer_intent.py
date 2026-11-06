"""Explorer intent detector tests"""
from app.services.explorer.intent_detector import IntentDetector


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


def test_empty_triggers_no_zero_division():
    detector = IntentDetector()
    detector.EXPLORATION_TRIGGERS["empty_trigger_set"] = []
    result = detector.detect(
        user_query="测试查询",
        current_layers=[],
        session_history=[],
    )
    assert result is not None



def test_chinese_layer_topic_matches_without_spaces_but_shared_city_is_insufficient():
    detector = IntentDetector()
    layers = [{"name": "海淀区学校名录"}]
    matching = detector.detect("查询海淀区学校分布", layers, [])
    other_topic = detector.detect("查询海淀区医院分布", layers, [])
    assert matching.decision == "skip" and "已有" in matching.reason
    assert other_topic.decision == "ask_user" and "已有" not in other_topic.reason


def test_generic_layer_words_do_not_suppress_exploration():
    result = IntentDetector().detect("show school locations", [{"name": "the data"}], [])
    assert "已有" not in result.reason
