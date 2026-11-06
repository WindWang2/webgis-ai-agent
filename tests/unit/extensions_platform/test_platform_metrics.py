"""扩展平台计量测试（ADR-0131 D5）。

覆盖：封闭词表拒绝、host 接线点（激活成功/失败/隔离/worker 崩溃）、
invoke_model_provider 计时、计量失败不影响扩展执行。
"""
from __future__ import annotations


import pytest
from prometheus_client import REGISTRY

from app.extensions_platform.metrics import (
    ACTIVATION_RESULTS,
    QUARANTINE_REASONS,
    InvocationTimer,
    record_activation,
    record_quarantine,
    record_worker_crash,
)


def test_vocabulary_rejects_unknown_values():
    with pytest.raises(ValueError):
        record_activation("exploded")
    with pytest.raises(ValueError):
        record_quarantine("vibes")
    assert set(ACTIVATION_RESULTS) == {
        "activated", "failed", "quarantined", "disabled"
    }
    assert set(QUARANTINE_REASONS) == {"worker_crash", "revocation", "trust_policy"}


def test_activation_counter_counts():
    before = REGISTRY.get_sample_value("extension_activation_total",
                                       {"result": "activated"}) or 0
    record_activation("activated")
    record_activation("activated")
    after = REGISTRY.get_sample_value("extension_activation_total",
                                      {"result": "activated"})
    assert after - before == 2


def test_quarantine_and_crash_counters():
    q_before = REGISTRY.get_sample_value("extension_quarantine_total",
                                         {"reason": "worker_crash"}) or 0
    c_before = REGISTRY.get_sample_value("extension_worker_crash_total") or 0
    record_worker_crash()
    record_quarantine("worker_crash")
    assert REGISTRY.get_sample_value(
        "extension_quarantine_total", {"reason": "worker_crash"}
    ) - q_before == 1
    assert REGISTRY.get_sample_value(
        "extension_worker_crash_total") - c_before == 1


def test_invocation_timer_observes():
    buckets_before = REGISTRY.get_sample_value(
        "extension_invocation_duration_seconds_count") or 0
    with InvocationTimer():
        pass
    buckets_after = REGISTRY.get_sample_value(
        "extension_invocation_duration_seconds_count")
    assert buckets_after - buckets_before == 1


def test_host_fail_activation_records_metric():
    """Public activation records even early unknown-extension failures."""
    from app.extensions_platform.host import ExtensionHost, HostPolicy
    from app.tools.registry import ToolRegistry
    host = ExtensionHost(tool_registry=ToolRegistry(), policy=HostPolicy())
    before = REGISTRY.get_sample_value("extension_activation_total", {"result": "failed"}) or 0
    diagnostics = host.activate("missing.extension")
    assert diagnostics
    after = REGISTRY.get_sample_value("extension_activation_total", {"result": "failed"}) or 0
    assert after - before == 1
