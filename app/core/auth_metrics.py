"""Auth-related Prometheus metrics.

#473：deploy/alerts-rules.json 的 Auth_JWT_Errors 告警此前引用
`auth_jwt_validation_errors_total`，但应用从未暴露该指标 —— 规则永远
evaluate 为空。此模块把该计数器变为真实指标（注册进 prometheus_client
默认 REGISTRY，随 instrumentator 的 /metrics 端点一起暴露），由
app/core/auth.py 的 token 校验失败路径递增。

测试 tests/test_alerts_metrics_consistency.py 会以本模块的存在为告警
表达式的一致性前提 —— 删除它会让 CI 失败。
"""
import logging

from prometheus_client import Counter

logger = logging.getLogger(__name__)

# JWT 校验失败（签名/exp/格式错误）。无 label：告警聚合不需要维度，
# 也避免攻击者用随机 label 值撑高 cardinality。
AUTH_JWT_VALIDATION_ERRORS = Counter(
    "auth_jwt_validation_errors_total",
    "Total number of JWT tokens that failed cryptographic validation "
    "(bad signature, expired, malformed).",
)


def inc_jwt_validation_error() -> None:
    """Record one rejected JWT (fire-and-forget; metrics must never break auth)."""
    try:
        AUTH_JWT_VALIDATION_ERRORS.inc()
    except Exception:  # noqa: BLE001
        logger.debug("auth_jwt_validation_errors_total inc failed", exc_info=True)


__all__ = ["AUTH_JWT_VALIDATION_ERRORS", "inc_jwt_validation_error"]
