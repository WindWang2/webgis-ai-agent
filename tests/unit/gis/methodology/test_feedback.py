"""Epic 11 —— Feedback 契约（默认禁用 / 硬上限 / 无回灌路径）。"""
from __future__ import annotations

from app.lib.gis.methodology.feedback import (
    FEEDBACK_SINK_ENV,
    MAX_RECORDS,
    FeedbackCorpusWriter,
    MethodologyFeedbackRecord,
)


def test_writer_disabled_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(FEEDBACK_SINK_ENV, raising=False)
    writer = FeedbackCorpusWriter()
    assert writer.enabled is False
    record = MethodologyFeedbackRecord(kind="method_selected",
                                       method_id="interp.idw")
    assert writer.record(record) is False


def test_writer_appends_when_enabled(monkeypatch, tmp_path) -> None:
    sink = tmp_path / "feedback.jsonl"
    writer = FeedbackCorpusWriter(str(sink))
    record = MethodologyFeedbackRecord(
        kind="user_correction", project_id="p1", session_id="s1",
        query="学校分布", method_id="density.visual_heatmap",
        alternative_method_id="descriptive.simple_display",
        reason_codes=["USER_PREFERENCE"])
    assert writer.record(record) is True
    lines = sink.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert "user_correction" in lines[0]
    assert "p1" in lines[0]


def test_writer_hard_cap_and_truncation_disclosure(monkeypatch, tmp_path) -> None:
    """达到 MAX_RECORDS 后停止写入并置 truncated 披露（不静默丢）。"""
    import json
    sink = tmp_path / "capped.jsonl"
    # 预置满额文件
    with open(sink, "w", encoding="utf-8") as fh:
        for i in range(MAX_RECORDS):
            fh.write(json.dumps({"i": i}) + "\n")
    writer = FeedbackCorpusWriter(str(sink))
    record = MethodologyFeedbackRecord(kind="runtime_failure",
                                       method_id="interp.idw")
    assert writer.record(record) is False
    assert writer.truncated is True
    # 之后恒禁用
    assert writer.enabled is False


def test_invalid_kind_rejected() -> None:
    import pytest
    with pytest.raises(Exception):
        MethodologyFeedbackRecord(kind="llm_rewrite",
                                  method_id="x")  # 封闭词表外


def test_no_runtime_rewrite_path() -> None:
    """Non-goal 机器可读锁定：feedback 模块无任何读取-改写知识表的 API。"""
    import app.lib.gis.methodology.feedback as fb
    public = set(dir(fb))
    forbidden = {"load_corpus_into_registry", "rewrite_knowledge",
                 "update_taxonomy", "patch_descriptor", "apply_feedback"}
    assert not (public & forbidden)
