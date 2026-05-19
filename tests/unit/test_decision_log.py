"""decision_log 单元测试 — 记录构造与 JSONL 序列化。"""
import json

from app.services.chat.decision_log import ToolDecisionRecord, log_tool_decision


def _record(**over):
    base = dict(
        session_id="s1",
        round=0,
        user_message="成都的医院分布",
        active_domains=["statistics", "chinese"],
        from_plan=True,
        subset_size=24,
        total_tools=82,
        tool_chosen="h3_binning",
        tool_args={"resolution": 8},
        result_quality="ok",
        plan_step_matched=3,
    )
    base.update(over)
    return ToolDecisionRecord(**base)


def test_record_to_dict_has_all_fields():
    d = _record().to_dict()
    assert d["session_id"] == "s1"
    assert d["tool_chosen"] == "h3_binning"
    assert d["result_quality"] == "ok"
    assert d["plan_step_matched"] == 3
    assert "ts" in d  # 时间戳自动注入


def test_log_writes_one_jsonl_line(tmp_path, monkeypatch):
    log_file = tmp_path / "tool_decisions.jsonl"
    monkeypatch.setattr("app.services.chat.decision_log._LOG_PATH", log_file)
    log_tool_decision(_record())
    log_tool_decision(_record(tool_chosen="buffer_analysis"))
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    parsed = json.loads(lines[1])
    assert parsed["tool_chosen"] == "buffer_analysis"


def test_log_failure_does_not_raise(monkeypatch):
    """写盘失败只记 warning，绝不影响主流程。"""
    def boom(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr("app.services.chat.decision_log._append_line", boom)
    log_tool_decision(_record())  # 不抛异常即通过
