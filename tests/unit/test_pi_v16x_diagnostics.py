"""WP2 / ADR-0301 D5：v1.6.x 诊断事件与 stderr 诊断环契约测试。

覆盖：
- `extension_error` RPC 事件：脱敏（内部路径→basename）、截断、不进 SSE、
  有界统计；
- 真正未知的 vendor 事件：WARN + 计数；已知但有意不映射的高频事件
  （queue_update 等）：静默且不计入；
- `PiStderrDiagnostics`：路径脱敏、截断、有界环、快照形状；
- 终态判据不受诊断事件影响（extension_error 后 agent_settled 仍是唯一
  终判——mapper 层负例）。
"""
from __future__ import annotations

import logging

import pytest

from app.services.chat import pi_event_mapper as em
from app.services.chat.pi_rpc_client import PiStderrDiagnostics


@pytest.fixture(autouse=True)
def _clean_diag_stats():
    em.reset_vendor_event_diagnostics()
    yield
    em.reset_vendor_event_diagnostics()


# ── extension_error ──────────────────────────────────────────────────────────

def test_extension_error_is_not_sse_and_is_recorded_redacted(caplog):
    event = {
        "type": "extension_error",
        "extensionPath": "/home/kevin/internal/webgis-tools/index.mjs",
        "event": "tool_call",
        "error": "boom at /home/kevin/secret.py:42",
    }
    with caplog.at_level(logging.WARNING, logger=em.__name__):
        assert em.map_event_to_sse(event, session_id="s1") is None
    snap = em.vendor_event_diagnostics()
    assert snap["counts"] == {"extension_error": 1}
    entry = snap["recent"][0]
    assert entry["extension"] == "index.mjs"      # basename only — no internal path
    assert "kevin" not in entry["extension"]
    assert "secret" not in entry["error"]         # path-redacted error text
    assert entry["event"] == "tool_call"


def test_extension_error_message_is_bounded():
    event = {
        "type": "extension_error",
        "extensionPath": "x.mjs",
        "event": "session_start",
        "error": "E" * 5000,
    }
    em.map_event_to_sse(event, session_id="s")
    entry = em.vendor_event_diagnostics()["recent"][0]
    assert len(entry["error"]) <= 200


def test_extension_error_does_not_break_terminal_judgement():
    """负例：extension_error 在 turn 中段到达后，agent_settled 仍正常映射终态。"""
    mid = {"type": "extension_error", "extensionPath": "e.mjs", "event": "tool_call", "error": "x"}
    assert em.map_event_to_sse(mid, session_id="s1") is None
    settled = {"type": "agent_settled", "aborted": False}
    sse = em.map_event_to_sse(settled, session_id="s1")
    # 仓库 SSE 词表：agent_settled → task_complete（唯一终判事件）。
    assert sse is not None and "task_complete" in sse


# ── 未知事件 vs 已知不映射 ────────────────────────────────────────────────────

def test_genuinely_unknown_event_warns_and_counts(caplog):
    with caplog.at_level(logging.WARNING, logger=em.__name__):
        assert em.map_event_to_sse({"type": "brand_new_vendor_thing"}, session_id="s") is None
    assert "brand_new_vendor_thing" in caplog.text
    assert em.vendor_event_diagnostics()["counts"] == {"unmapped_event": 1}


def test_known_unmapped_events_stay_silent_and_uncounted():
    """queue_update 等高频已知事件：静默、零计数（防刷屏/防统计污染）。"""
    for etype in ("queue_update", "bash_execution_update", "entry_appended"):
        assert em.map_event_to_sse({"type": etype}, session_id="s") is None
    assert em.vendor_event_diagnostics() == {"counts": {}, "recent": []}


def test_diag_stats_ring_is_bounded():
    for i in range(50):
        em.map_event_to_sse({"type": f"unmapped_{i}"}, session_id="s")
    snap = em.vendor_event_diagnostics()
    assert len(snap["recent"]) <= 32
    assert snap["counts"]["unmapped_event"] == 50  # 计数完整，摘录有界


def test_auto_retry_events_enter_typed_diagnostics():
    """WP5/C24：auto_retry_start/end 进有界诊断（脱敏、计数、SSE 不受影响）。"""
    s1 = {"type": "auto_retry_start", "attempt": 1, "maxAttempts": 3,
          "delayMs": 2000, "errorMessage": "529 overloaded /home/x"}
    assert em.map_event_to_sse(s1, session_id="s") is not None  # SSE 帧仍在
    s2 = {"type": "auto_retry_end", "attempt": 2, "success": True}
    assert em.map_event_to_sse(s2, session_id="s") is not None
    s3 = {"type": "auto_retry_end", "attempt": 3, "success": False,
          "finalError": "exhausted /home/secret"}
    assert em.map_event_to_sse(s3, session_id="s") is not None
    snap = em.vendor_event_diagnostics()
    assert snap["counts"] == {"auto_retry_start": 1, "auto_retry_end": 2}
    start_entry = snap["recent"][0]
    assert start_entry["attempt"] == "1" and "secret" not in start_entry["error"]
    fail_entry = snap["recent"][-1]
    assert fail_entry["success"] == "0" and "secret" not in fail_entry["final_error"]


def test_documented_wire_vocabulary_produces_zero_warn(caplog):
    """R1-P1-1 负例：v1.6.1 docs/json.md 全部文档化事件类型经过 mapper
    都不得产生 unmapped WARN/计数（含每 turn 必达的 5 个冗余投影事件）。"""
    documented = [
        "agent_start", "agent_end", "agent_settled", "turn_start", "turn_end",
        "message_start", "message_update", "message_end",
        "tool_execution_start", "tool_execution_update", "tool_execution_end",
        "queue_update", "entry_appended", "session_info_changed",
        "thinking_level_changed", "compaction_start", "compaction_end",
        "auto_retry_start", "auto_retry_end",
        "summarization_retry_scheduled", "summarization_retry_attempt_start",
        "summarization_retry_finished", "bash_execution_update",
        "extension_error",
    ]
    with caplog.at_level(logging.WARNING, logger=em.__name__):
        for etype in documented:
            em.map_event_to_sse({"type": etype}, session_id="s")
    snap = em.vendor_event_diagnostics()
    # 有 handler 的事件会合法记录自己的诊断段（脱敏 unknown 缺省值）——
    # 本负例只锁「文档化词表零 unmapped WARN/计数」。
    assert "unmapped_event" not in snap["counts"], snap
    assert not any("unmapped" in k for k in snap["counts"]), snap
    assert not any(r.get("type") for r in snap["recent"]
                   if r.get("kind") == "unmapped" or "unmapped" in str(r)), snap


def test_diag_stats_kind_cap_overflows_to_single_key():
    """R2-P3：MAX_KINDS 封顶——record() 的 kind 维度有上界（wire 路径的
    kind 词表封闭不会触顶；此测试直接驱动 record 保护未来调用方）。"""
    stats = em._BoundedEventStats()
    for i in range(em._BoundedEventStats.MAX_KINDS + 10):
        stats.record(f"alien_kind_{i}", {"i": str(i)})
    snap = stats.snapshot()
    assert len(snap["counts"]) == em._BoundedEventStats.MAX_KINDS + 1
    assert snap["counts"].get("_overflow") == 10
    assert "alien_kind_0" in snap["counts"]  # 首个超顶前的键保留


def test_diag_stats_reset():
    em.map_event_to_sse({"type": "whatever_unknown"}, session_id="s")
    em.reset_vendor_event_diagnostics()
    assert em.vendor_event_diagnostics() == {"counts": {}, "recent": []}


# ── PiStderrDiagnostics ──────────────────────────────────────────────────────

def test_stderr_diag_redacts_and_bounds_lines():
    ring = PiStderrDiagnostics()
    ring.record("warn about /home/kevin/very/secret/path")
    ring.record("S" * 500)
    snap = ring.snapshot()
    assert snap["total_lines"] == 2
    assert "kevin" not in snap["recent"][0]
    assert len(snap["recent"][1]) <= PiStderrDiagnostics.MAX_LINE_CHARS


def test_stderr_diag_ring_max_lines():
    ring = PiStderrDiagnostics(max_lines=8)
    for i in range(20):
        ring.record(f"line {i} no-paths-here")
    snap = ring.snapshot()
    assert len(snap["recent"]) == 8
    assert snap["recent"][-1] == "line 19 no-paths-here"
    assert snap["total_lines"] == 20


def test_stderr_diag_never_raises_on_redact_failure(monkeypatch):
    """脱敏工具不可用时诊断记录必须继续工作（读循环安全）。"""
    ring = PiStderrDiagnostics()
    monkeypatch.setattr(
        "app.utils.security.redact_paths",
        lambda _t: (_ for _ in ()).throw(RuntimeError("redactor down")),
    )
    ring.record("plain line /some/path")
    assert ring.snapshot()["recent"][0].startswith("plain line")
