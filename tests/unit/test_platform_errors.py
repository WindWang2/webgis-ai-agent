"""平台错误分类学测试（ADR-0131 D3）。

覆盖：封闭词表完整性、PlatformError 显式声明、类型级 classify 矩阵、
重试决策语义、exception handler additive 字段、以及"分类永不抛"红线。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.core.errors import (
    CATEGORY_DEFAULTS,
    DEFAULT_RETRY_POLICY,
    ErrorCategory,
    PlatformError,
    category_defaults,
    classify_exception,
    decide_retry,
    http_status_for,
)


# ── 词表完整性 ──────────────────────────────────────────────────────────────


def test_every_category_has_defaults():
    """新增类目漏登记裁决 = 结构性红（append-only 的机器强制部分）。"""
    for category in ErrorCategory:
        spec = CATEGORY_DEFAULTS.get(category)
        assert spec is not None, f"category {category} missing CATEGORY_DEFAULTS"
        assert isinstance(spec.retryable, bool)
        assert 400 <= spec.http_status <= 599
        assert spec.user_message, "user_message 必须非空"


def test_vocabulary_matches_goal_list():
    """目标 Phase E 词表逐项落位（防手滑改名）。"""
    expected = {
        "validation", "permission", "data_unavailable", "crs",
        "resource_exhausted", "timeout", "cancellation", "retryable",
        "permanent", "dependency_failure", "worker_lost", "model_failure",
        "render_failure", "storage_corruption",
    }
    assert {c.value for c in ErrorCategory} == expected


def test_retryable_and_permanent_fallbacks():
    assert category_defaults(ErrorCategory.RETRYABLE).retryable is True
    assert category_defaults(ErrorCategory.PERMANENT).retryable is False


# ── PlatformError ───────────────────────────────────────────────────────────


def test_platform_error_defaults_from_category():
    err = PlatformError("ndvi window 3 failed", category=ErrorCategory.TIMEOUT)
    assert err.retryable is True
    assert err.http_status == 504
    assert err.user_message == "操作超时，请稍后重试"
    assert err.degraded is None


def test_platform_error_explicit_overrides_win():
    err = PlatformError(
        "quota exhausted for session",
        category=ErrorCategory.TIMEOUT,
        retryable=False,
        user_message="会话配额已用尽",
        degraded="cache_only",
        context={"window": 3, "secret_key": "should-not-leak-but-bounded"},
    )
    assert err.retryable is False
    assert err.user_message == "会话配额已用尽"
    assert err.degraded == "cache_only"
    assert err.context == {"window": "3", "secret_key": "should-not-leak-but-bounded"}


def test_platform_error_context_bounded():
    context = {f"k{i}": "v" * 500 for i in range(50)}
    err = PlatformError("boom", context=context)
    assert len(err.context) == 16
    assert all(len(v) <= 256 for v in err.context.values())


# ── classify 矩阵 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("exc", "expected_category", "expected_retryable"),
    [
        (ValueError("bad input"), ErrorCategory.VALIDATION, False),
        (KeyError("missing"), ErrorCategory.PERMANENT, False),
        (TypeError("bug"), ErrorCategory.PERMANENT, False),
        (PermissionError("denied"), ErrorCategory.PERMISSION, False),
        (FileNotFoundError("nope"), ErrorCategory.DATA_UNAVAILABLE, False),
        (TimeoutError("slow"), ErrorCategory.TIMEOUT, True),
        (ConnectionError("refused"), ErrorCategory.DEPENDENCY_FAILURE, True),
        (MemoryError("oom"), ErrorCategory.RESOURCE_EXHAUSTED, True),
        (OSError(28, "enospc"), ErrorCategory.RESOURCE_EXHAUSTED, True),
        (OSError(13, "eacces"), ErrorCategory.PERMISSION, False),
        (RuntimeError("mystery"), ErrorCategory.PERMANENT, False),
    ],
)
def test_classify_stdin_types(exc, expected_category, expected_retryable):
    cls = classify_exception(exc)
    assert cls.category is expected_category
    assert cls.retryable is expected_retryable
    assert cls.declared is False
    assert cls.http_status == http_status_for(expected_category)


def test_classify_cancel_semantics():
    from app.lib.cancellation import OperationCancelled

    for exc in (OperationCancelled("user cancel"), asyncio.CancelledError()):
        cls = classify_exception(exc)
        assert cls.category is ErrorCategory.CANCELLATION
        assert cls.retryable is False
        assert cls.http_status == 499


def test_classify_httpx_timeouts():
    httpx = pytest.importorskip("httpx")
    cls = classify_exception(httpx.ReadTimeout("t"))
    assert cls.category is ErrorCategory.TIMEOUT
    cls = classify_exception(httpx.ConnectError("refused"))
    assert cls.category is ErrorCategory.DEPENDENCY_FAILURE
    assert cls.retryable is True


def test_classify_sqlalchemy_operational_error():
    sa_exc = pytest.importorskip("sqlalchemy.exc")
    cls = classify_exception(
        sa_exc.OperationalError("SELECT 1", {}, Exception("connection reset"))
    )
    assert cls.category is ErrorCategory.DEPENDENCY_FAILURE
    assert cls.retryable is True


def test_classify_platform_error_declares():
    err = PlatformError(
        "model 503", category=ErrorCategory.MODEL_FAILURE, degraded="chatengine"
    )
    cls = classify_exception(err)
    assert cls.declared is True
    assert cls.category is ErrorCategory.MODEL_FAILURE
    assert cls.degraded == "chatengine"


def test_classify_never_raises_on_weird_exceptions():
    class Exploding(Exception):
        def __init_subclass__(cls, **kw):  # pragma: no cover
            raise RuntimeError("no subclass")

        def __str__(self):
            raise RuntimeError("str explodes")

    exc = Exploding()
    cls = classify_exception(exc)
    assert cls.category is ErrorCategory.PERMANENT


# ── 重试决策 ────────────────────────────────────────────────────────────────


def test_decide_retry_budget_exhaustion():
    policy = DEFAULT_RETRY_POLICY
    for attempt in (1, 2):
        decision = decide_retry(TimeoutError("t"), attempt, policy)
        assert decision.will_retry is True
    decision = decide_retry(TimeoutError("t"), 3, policy)
    assert decision.will_retry is False
    assert decision.delay_s == 0.0


def test_decide_retry_backoff_bounds():
    policy = DEFAULT_RETRY_POLICY
    d1 = decide_retry(TimeoutError("t"), 1, policy)
    d2 = decide_retry(TimeoutError("t"), 2, policy)
    assert d1.delay_s == pytest.approx(0.5)
    assert d2.delay_s == pytest.approx(1.0)
    # 指数增长被 max_delay_s 真实钳制（长预算 policy 下 2^6 超过上界）
    long_policy = DEFAULT_RETRY_POLICY.__class__(max_attempts=10)
    d7 = decide_retry(TimeoutError("t"), 7, long_policy)
    assert d7.will_retry is True
    assert d7.delay_s == pytest.approx(long_policy.max_delay_s)


def test_decide_retry_non_retryable_category():
    decision = decide_retry(PermissionError("no"), 1, DEFAULT_RETRY_POLICY)
    assert decision.will_retry is False
    assert decision.category is ErrorCategory.PERMISSION


def test_decide_retry_attempt_floor():
    """attempt<1 被钳到 1（防御调用方传 0/-1 造成预算旁路）。"""
    decision = decide_retry(TimeoutError("t"), 0, DEFAULT_RETRY_POLICY)
    assert decision.attempt == 1


# ── exception handler 接线（additive 字段）──────────────────────────────────


def test_format_error_response_carries_category():
    from fastapi import Request

    from app.core.exception import format_error_response

    scope = {
        "type": "http", "method": "GET",
        "url": "http://testserver/x", "path": "/x", "query_string": b"",
        "headers": [], "client": ("testclient", 123),
    }
    request = Request(scope)

    resp = format_error_response(
        PlatformError("db down", category=ErrorCategory.DEPENDENCY_FAILURE),
        request,
        include_details=False,
    )
    assert resp["code"] == "SERVER_ERROR"  # 既有字段不变
    assert resp["category"] == "dependency_failure"
    assert resp["retryable"] is True

    resp = format_error_response(HTTPException(status_code=404), request)
    assert resp["code"] == "NOT_FOUND"  # 既有 M5 映射不受影响
    assert resp["category"] == "data_unavailable"


def test_httpexception_401_maps_permission():
    """401 → UNAUTHORIZED（既有 code 映射）且 category=permission（对齐）。"""
    from fastapi import Request

    from app.core.exception import format_error_response

    scope = {
        "type": "http", "method": "GET",
        "url": "http://testserver/x", "path": "/x", "query_string": b"",
        "headers": [], "client": ("testclient", 123),
    }
    resp = format_error_response(HTTPException(status_code=401), Request(scope))
    assert resp["code"] == "UNAUTHORIZED"
    assert resp["category"] == "permission"
    assert resp["retryable"] is False


def test_start_span_survives_exploding_attribute():
    """__str__ 爆炸的属性：span 降级、with 体内业务照常执行（R1-m4）。"""
    from app.lib.observability.spans import (
        RingSpanExporter,
        SpanStage,
        SpanStatus,
        install_exporter,
        reset_exporters_for_tests,
        start_span,
    )

    reset_exporters_for_tests()
    ring = RingSpanExporter()
    install_exporter(ring)

    class _Boom:
        def __str__(self):
            raise RuntimeError("str explodes")

    with start_span(SpanStage.TOOL, "boom-attrs", x=_Boom()) as span:
        assert span.attributes == {}  # 爆炸项被跳过，业务照常进入
    (rec,) = ring.snapshot()
    assert rec.status == SpanStatus.OK.value


def test_classify_survives_exploding_status_property():
    """status_code 是会抛错的 property：分类退 permanent 而不是抛（R1-m4）。"""
    class _Evil(Exception):
        @property
        def status_code(self):
            raise RuntimeError("property explodes")

    cls = classify_exception(_Evil())
    assert cls.category is ErrorCategory.PERMANENT
