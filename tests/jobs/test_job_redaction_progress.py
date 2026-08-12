"""载荷脱敏、体积上限与统一进度契约（ADR-0052，规范 §19/§20/§35/§38）。"""
import json

import pytest

from app.services.jobs.progress import (
    JobProgress,
    ProgressReporter,
    ProgressThrottle,
)
from app.services.jobs.redaction import (
    MAX_PARAMETERS_BYTES,
    safe_dispatch_spec,
    MAX_RESULT_BYTES,
    REDACTED,
    safe_error,
    safe_parameters,
    safe_result,
)


def _size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


# ── 脱敏 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key",
    [
        "password", "passwd", "api_key", "apiKey", "secret", "client_secret",
        "token", "access_token", "session_token", "owner_token", "authorization",
        "auth_header", "credential", "credentials", "signed_url", "signature",
        "cookie", "private_key", "access_key", "bearer_token",
    ],
)
def test_sensitive_keys_are_redacted(key):
    """凭据绝不落库、绝不经任务中心回传（规范 §35）。"""
    out = safe_parameters({key: "super-secret-value"})
    assert out[key] == REDACTED
    assert "super-secret-value" not in json.dumps(out)


def test_redaction_is_case_insensitive_and_substring_based():
    out = safe_parameters({"AWS_SECRET_ACCESS_KEY": "x", "userTokenValue": "y"})
    assert out["AWS_SECRET_ACCESS_KEY"] == REDACTED
    assert out["userTokenValue"] == REDACTED


def test_nested_sensitive_keys_are_redacted():
    out = safe_parameters({"cfg": {"inner": {"api_key": "leak-me"}}})
    assert "leak-me" not in json.dumps(out)


def test_bulk_geometry_is_summarized_not_stored():
    """规范 §38：50MB GeoJSON 绝不写进 task 行，只留摘要。"""
    features = [{"type": "Feature", "geometry": {"coordinates": [[i, i]] * 50}} for i in range(20_000)]
    out = safe_parameters({"features": features, "crs": "EPSG:4326"})
    assert out["features"] == {"__omitted__": "features", "count": 20_000}
    assert out["crs"] == "EPSG:4326"
    assert _size(out) <= MAX_PARAMETERS_BYTES


def test_geojson_featurecollection_keeps_useful_metadata():
    """摘要仍保留对用户有意义的元信息（类型、要素数）。"""
    out = safe_result({"geojson": {"type": "FeatureCollection", "features": [{}] * 1234}})
    assert out["geojson"]["type"] == "FeatureCollection"
    assert out["geojson"]["count"] == 1234


def test_result_size_is_hard_bounded(caplog):
    """无论输入多大，序列化后必须落在硬上界内。"""
    monster = {f"key_{i}": "x" * 400 for i in range(500)}
    out = safe_result(monster)
    assert _size(out) <= MAX_RESULT_BYTES
    assert "__truncated__" in out


def test_parameters_size_is_hard_bounded():
    monster = {f"k{i}": {"nested": ["v" * 200] * 5} for i in range(400)}
    out = safe_parameters(monster)
    assert _size(out) <= MAX_PARAMETERS_BYTES


def test_long_strings_are_truncated_with_marker():
    out = safe_parameters({"note": "a" * 5000})
    assert len(out["note"]) < 5000
    assert "chars]" in out["note"]


def test_long_sequences_are_capped():
    out = safe_parameters({"ids": list(range(500))})
    assert len(out["ids"]) <= 21
    assert out["ids"][-1]["__omitted__"] == "items"


def test_bytes_are_not_embedded():
    out = safe_parameters({"blob": b"\x00" * 4096})
    assert out["blob"] == {"__omitted__": "bytes", "bytes": 4096}


def test_deep_structures_are_depth_limited():
    node = {"leaf": 1}
    for _ in range(40):
        node = {"child": node}
    out = safe_parameters(node)
    assert _size(out) <= MAX_PARAMETERS_BYTES
    assert "TRUNCATED_DEPTH" in json.dumps(out)


def test_non_dict_payloads_are_wrapped():
    assert safe_parameters("just a string")["value"] == "just a string"
    assert safe_parameters(None) == {}
    assert safe_result(None) is None
    assert safe_result([1, 2, 3])["value"] == [1, 2, 3]


def test_safe_error_never_contains_traceback():
    """规范 §10：raw traceback 不得泄漏给前端。"""
    multiline = 'Traceback (most recent call last):\n  File "x.py", line 1\n    boom\nValueError: bad'
    out = safe_error(multiline)
    assert "\n" not in out
    assert out.startswith("Traceback (most recent call last):")  # 只留首行，不含栈帧
    assert "x.py" not in out


def test_safe_error_from_exception():
    out = safe_error(ValueError("invalid CRS"))
    assert out == "ValueError: invalid CRS"


def test_safe_error_handles_empty_and_none():
    assert safe_error(None) is None
    assert safe_error("") is None
    assert safe_error(RuntimeError()) == "RuntimeError: RuntimeError"


def test_safe_error_is_length_bounded():
    out = safe_error("x" * 5000)
    assert len(out) <= 501


# ── 进度契约 ────────────────────────────────────────────────────────


def test_progress_clamped_to_valid_range():
    assert JobProgress(progress=150).clamped().progress == 100
    assert JobProgress(progress=-5).clamped().progress == 0
    assert JobProgress(progress=None).clamped().progress is None
    snap = JobProgress(progress=50)
    assert snap.clamped() is snap  # 已合法则不复制


def test_indeterminate_progress_is_explicit():
    """规范 §19：允许 progress=null，禁止编造假百分比。"""
    snap = JobProgress(progress=None, message="处理中")
    assert snap.indeterminate is True


def test_progress_message_composition_and_length():
    snap = JobProgress(progress=10, message="重投影", phase="reproject", current_step=2, total_steps=7)
    msg = snap.as_message()
    assert msg == "[reproject] 重投影 (2/7)"
    long = JobProgress(message="x" * 500).as_message()
    assert len(long) <= 255  # progress_message 是 String(255)


def test_progress_message_none_when_empty():
    assert JobProgress(progress=5).as_message() is None


# ── 节流 ────────────────────────────────────────────────────────────


def test_throttle_first_report_always_emits():
    t = ProgressThrottle(min_delta_pct=10, min_interval_s=100, clock=lambda: 0.0)
    assert t.should_emit(0) is True


def test_throttle_suppresses_sub_threshold_updates():
    now = [0.0]
    t = ProgressThrottle(min_delta_pct=5.0, min_interval_s=100.0, clock=lambda: now[0])
    assert t.should_emit(0) is True
    assert t.should_emit(1) is False
    assert t.should_emit(3) is False
    assert t.should_emit(5) is True  # 达到 5% delta


def test_throttle_time_based_release_for_stuck_progress():
    """进度长时间不变也要偶尔刷一次 —— 心跳靠它维持。"""
    now = [0.0]
    t = ProgressThrottle(min_delta_pct=50.0, min_interval_s=1.0, clock=lambda: now[0])
    assert t.should_emit(10) is True
    assert t.should_emit(11) is False
    now[0] = 2.0
    assert t.should_emit(11) is True


def test_throttle_always_emits_hundred_percent():
    now = [0.0]
    t = ProgressThrottle(min_delta_pct=90.0, min_interval_s=999.0, clock=lambda: now[0])
    assert t.should_emit(0) is True
    assert t.should_emit(50) is False
    assert t.should_emit(100) is True


def test_throttle_force_bypasses_all_limits():
    now = [0.0]
    t = ProgressThrottle(min_delta_pct=99.0, min_interval_s=999.0, clock=lambda: now[0])
    t.should_emit(0)
    assert t.should_emit(1, force=True) is True


def test_throttle_indeterminate_uses_time_only():
    now = [0.0]
    t = ProgressThrottle(min_interval_s=1.0, clock=lambda: now[0])
    assert t.should_emit(None) is True
    assert t.should_emit(None) is False
    now[0] = 1.5
    assert t.should_emit(None) is True


def test_throttle_bounds_write_rate_for_hot_loop():
    """规范 §20：10 万次上报必须被压到两位数写入。"""
    now = [0.0]
    t = ProgressThrottle(min_delta_pct=1.0, min_interval_s=0.5, clock=lambda: now[0])
    for i in range(100_000):
        t.should_emit(i * 100 // 100_000)
    assert t.emitted <= 110, t.emitted


def test_throttle_reset():
    t = ProgressThrottle(clock=lambda: 0.0)
    t.should_emit(10)
    t.reset()
    assert t.emitted == 0
    assert t.should_emit(10) is True


def test_reporter_only_forwards_released_updates():
    now = [0.0]
    seen: list[JobProgress] = []
    reporter = ProgressReporter(
        seen.append, ProgressThrottle(min_delta_pct=10.0, min_interval_s=999.0, clock=lambda: now[0])
    )
    assert reporter.report(0, "start") is True
    assert reporter.report(2, "tick") is False
    assert reporter.report(20, "tick") is True
    assert [s.progress for s in seen] == [0, 20]
    assert reporter.last.progress == 20


def test_reporter_flush_always_forwards():
    seen: list[JobProgress] = []
    reporter = ProgressReporter(
        seen.append, ProgressThrottle(min_delta_pct=99.0, min_interval_s=999.0, clock=lambda: 0.0)
    )
    reporter.report(0)
    assert reporter.flush(1, "terminal") is True
    assert len(seen) == 2


def test_reporter_clamps_before_sink():
    seen: list[JobProgress] = []
    reporter = ProgressReporter(seen.append)
    reporter.report(9999, "over")
    assert seen[0].progress == 100


# ── dispatch 描述符（round-2 审计） ─────────────────────────────────


def test_dispatch_spec_roundtrips_faithfully():
    """重跑描述符必须**不**被截断 —— 截断后的参数无法忠实重跑。"""
    spec = {"task": "app.tasks.ndvi", "args": ["/data/a.tif", 4, None], "kwargs": {"session_id": "s"}}
    out = safe_dispatch_spec(spec)
    assert out == spec


def test_dispatch_spec_rejects_top_level_sensitive_kwarg():
    out = safe_dispatch_spec({"task": "t", "args": [], "kwargs": {"api_key": "leak"}})
    assert out["__truncated__"] == "dispatch_spec"
    assert "leak" not in json.dumps(out)


def test_dispatch_spec_rejects_nested_sensitive_kwarg():
    """嵌套结构里的凭据同样不得落库（只筛顶层 key 是不够的）。"""
    out = safe_dispatch_spec(
        {"task": "t", "args": [], "kwargs": {"cfg": {"inner": {"password": "leak"}}}}
    )
    assert out["__truncated__"] == "dispatch_spec"
    assert "leak" not in json.dumps(out)


def test_dispatch_spec_rejects_sensitive_key_inside_args():
    """args 是位置参数，但里面的 dict 也可能夹带凭据。"""
    out = safe_dispatch_spec(
        {"task": "t", "args": [{"properties": {"access_token": "leak"}}], "kwargs": {}}
    )
    assert out["__truncated__"] == "dispatch_spec"
    assert "leak" not in json.dumps(out)


def test_dispatch_spec_rejects_oversized_payload():
    out = safe_dispatch_spec({"task": "t", "args": [["x" * 200] * 200], "kwargs": {}})
    assert out["__truncated__"] == "dispatch_spec"
    assert out["reason"] == "too_large"


def test_dispatch_spec_rejects_malformed_input():
    assert safe_dispatch_spec(None) is None
    assert safe_dispatch_spec({"args": []}) is None
    assert safe_dispatch_spec({"task": ""}) is None
    assert safe_dispatch_spec({"task": "t", "args": "not-a-list"}) is None
    assert safe_dispatch_spec({"task": "t", "kwargs": "not-a-dict"}) is None
