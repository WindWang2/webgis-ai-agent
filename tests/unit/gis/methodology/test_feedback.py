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
    import hashlib
    assert "p1" not in lines[0]
    assert hashlib.sha256(b"p1").hexdigest() in lines[0]


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


def test_writer_repairs_half_line_left_by_crash(tmp_path) -> None:
    """崩溃残留的半行（末行无 \n）不得把下一条 JSON 拼成同一行。

    旧实现直接 append：半行 + 新记录挤在一行，两条记录同时损坏且行数
    少计（JSONL 逐行解析对两者都失败）。
    """
    import json

    sink = tmp_path / "half_line.jsonl"
    sink.write_text('{"schema_version":"x","kind":"meth', encoding="utf-8")
    writer = FeedbackCorpusWriter(str(sink))
    record = MethodologyFeedbackRecord(kind="method_selected",
                                       method_id="interp.idw")
    assert writer.record(record) is True

    lines = sink.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2, "半行必须被换行补齐，行数不得少计"
    # 半行原样保留（不伪造内容），新记录是独立可解析的一行
    assert lines[0] == '{"schema_version":"x","kind":"meth'
    payload = json.loads(lines[1])
    assert payload["kind"] == "method_selected"
    assert payload["method_id"] == "interp.idw"


def test_writer_does_not_double_newline_on_clean_sink(tmp_path) -> None:
    """末行已以 \n 结束时不得插入空行（行数/逐行解析不回退）。"""
    sink = tmp_path / "clean.jsonl"
    sink.write_text('{"i":0}\n', encoding="utf-8")
    writer = FeedbackCorpusWriter(str(sink))
    assert writer.record(MethodologyFeedbackRecord(
        kind="method_selected", method_id="interp.idw")) is True
    lines = sink.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0] == '{"i":0}'
