"""Durable Recovery Ledger 测试（ADR-0119 决策 D5）。

覆盖：跨进程持久（write-through + 独立实例模拟另一 worker）、重启不丢、
成功回写清零、TTL 惰性衰减、有界、resume 预算续接、dispatch seam
通道选择（durable authority / 进程账兜底 / 注入账本优先）。
"""
import json
import time

import pytest

from app.services.gis_harness.recovery_ledger import (
    MAX_ENTRIES_PER_SESSION,
    RecoveryLedger,
    get_recovery_ledger,
    reset_recovery_ledger_for_tests,
)


@pytest.fixture()
def sid(tmp_path, monkeypatch):
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path))
    reset_recovery_ledger_for_tests()
    return "sess-ledger-1"


def test_failure_recording_and_write_through(sid, tmp_path):
    led = get_recovery_ledger()
    assert led.record_failure(sid, "buffer_analysis", "tool_error") == 1
    assert led.record_failure(sid, "buffer_analysis", "tool_error") == 2
    assert led.record_failure(sid, "buffer_analysis", "timeout") == 1
    # write-through：新实例（模拟另一 worker / 重启后进程）直接读盘可见
    led2 = RecoveryLedger()
    assert led2.attempts(sid, "buffer_analysis", "tool_error") == 2
    assert led2.attempts(sid, "buffer_analysis", "timeout") == 1
    assert led2.attempts(sid, "buffer_analysis", "crs_error") == 0
    path = tmp_path / ".webgis-agent" / sid / "recovery_ledger.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["entries"]["buffer_analysis||tool_error"]["attempts"] == 2


def test_success_write_back_clears_budget(sid):
    led = get_recovery_ledger()
    led.record_failure(sid, "buffer_analysis", "tool_error")
    led.record_failure(sid, "buffer_analysis", "tool_error")
    led.record_failure(sid, "buffer_analysis", "timeout")
    assert led.record_success(sid, "buffer_analysis") == 2
    assert led.attempts(sid, "buffer_analysis", "tool_error") == 0
    assert led.attempts(sid, "buffer_analysis", "timeout") == 0
    # 幂等：重复成功回写计数 0
    assert led.record_success(sid, "buffer_analysis") == 0
    # 其他工具不受影响
    led.record_failure(sid, "kriging_interpolation", "tool_error")
    led.record_success(sid, "buffer_analysis")
    assert led.attempts(sid, "kriging_interpolation", "tool_error") == 1


def test_ttl_lazy_decay(sid):
    led = RecoveryLedger(ttl_s=0.05)
    led.record_failure(sid, "clip_layer", "timeout")
    assert led.attempts(sid, "clip_layer", "timeout") == 1
    time.sleep(0.06)
    assert led.attempts(sid, "clip_layer", "timeout") == 0
    # 衰减后重新记账从 1 起（不继承旧账）
    assert led.record_failure(sid, "clip_layer", "timeout") == 1


def test_bounded_per_session(sid):
    led = get_recovery_ledger()
    for i in range(MAX_ENTRIES_PER_SESSION + 20):
        led.record_failure(sid, f"tool_{i}", "tool_error")
    snap = led.snapshot(sid, max_entries=MAX_ENTRIES_PER_SESSION + 50)
    assert len(snap) <= MAX_ENTRIES_PER_SESSION


def test_copy_between_sessions_carries_budget(sid):
    led = get_recovery_ledger()
    led.record_failure(sid, "buffer_analysis", "tool_error")
    led.record_failure(sid, "buffer_analysis", "tool_error")
    new_sid = "sess-ledger-2"
    carried = led.copy_between_sessions(sid, new_sid)
    assert carried == 1
    assert led.attempts(new_sid, "buffer_analysis", "tool_error") == 2
    # 恢复后继续记账 —— 预算不复活
    assert led.record_failure(new_sid, "buffer_analysis", "tool_error") == 3


def test_dispatch_seam_uses_durable_channel(sid, monkeypatch):
    """有 session_id → durable 账本（重启后 attempts 不归零）。"""
    from app.services.gis_harness import failure_taxonomy as ft

    monkeypatch.setattr(ft, "_global_ledger", ft.RemediationLedger())
    fclass = None
    for _ in range(2):
        payload = ft.classify_and_remediate(
            code="TOOL_ERROR", message="boom",
            tool_name="clip_layer", session_id=sid)
        fclass = payload["class"]  # 分类器权威词汇，不预设具体类
    # 进程账未被重复记账（durable 为 authority）
    assert ft._global_ledger.attempts(("", "clip_layer", fclass)) == 0
    # 新实例（模拟重启）读到同一 durable 预算
    fresh = RecoveryLedger()
    assert fresh.attempts(sid, "clip_layer", fclass) == 2


def test_dispatch_seam_process_fallback_without_session(monkeypatch):
    from app.services.gis_harness import failure_taxonomy as ft

    proc = ft.RemediationLedger()
    monkeypatch.setattr(ft, "_global_ledger", proc)
    payload = ft.classify_and_remediate(
        code="TOOL_ERROR", message="boom", tool_name="clip_layer",
        session_id="")
    assert proc.attempts(("", "clip_layer", payload["class"])) == 1


def test_injected_ledger_takes_precedence(sid):
    from app.services.gis_harness import failure_taxonomy as ft

    injected = ft.RemediationLedger()
    payload = ft.classify_and_remediate(
        code="TOOL_ERROR", message="boom", tool_name="clip_layer",
        session_id=sid, ledger=injected)
    # 注入账本记账（进程账 key 含 session —— V5 语义）；durable 未写
    assert injected.attempts((sid, "clip_layer", payload["class"])) == 1
    assert get_recovery_ledger().attempts(sid, "clip_layer",
                                          payload["class"]) == 0


def test_disabled_switch_degrades(sid, monkeypatch):
    monkeypatch.setenv("GIS_RECOVERY_LEDGER", "0")
    led = get_recovery_ledger()
    assert led.record_failure(sid, "buffer_analysis", "tool_error") == 0
    assert led.attempts(sid, "buffer_analysis", "tool_error") == 0


def test_budget_exhaustion_via_durable_budget(sid):
    """跨 worker 预算耗尽语义：durable 计数到上限 → abort_with_disclosure。"""
    from app.services.gis_harness.failure_taxonomy import (
        classify_and_remediate,
        remediation_for,
    )

    from app.services.gis_harness.failure_taxonomy import HarnessFailureClass

    led = get_recovery_ledger()
    decision = None
    for _ in range(3):
        attempts = led.record_failure(sid, "fly_to_location", "tool_error")
        decision = remediation_for(
            HarnessFailureClass.TOOL_ERROR, attempts=attempts)
    assert decision.exhausted is True
    assert decision.action == "abort_with_disclosure"
    # 分类器对该输入的实际类 → durable 预算耗尽后 retry_allowed=False
    payload = classify_and_remediate(
        code="TOOL_ERROR", message="boom",
        tool_name="fly_to_location", session_id=sid)
    assert led.attempts(sid, "fly_to_location", payload["class"]) >= 1
    assert payload["retry_allowed"] is False


def test_two_process_concurrent_accounting(tmp_path, monkeypatch):
    """跨进程预算一致：两个独立进程对同 key 各记 5 次 → attempts=10。"""
    import os
    import subprocess
    import sys

    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path))
    monkeypatch_env = dict(os.environ)
    worker = (
        "import sys, time;"
        "from app.services.gis_harness.recovery_ledger import RecoveryLedger;"
        "led = RecoveryLedger();"
        "[led.record_failure(sys.argv[1], 'buffer_analysis', 'tool_error') for _ in range(5)];"
        "print('done')"
    )
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", worker, "sess-xproc"],
            env=monkeypatch_env, cwd=os.getcwd(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    for p in procs:
        out, err = p.communicate(timeout=60)
        assert p.returncode == 0, err.decode()[:400]
    led = RecoveryLedger()
    assert led.attempts("sess-xproc", "buffer_analysis", "tool_error") == 10
