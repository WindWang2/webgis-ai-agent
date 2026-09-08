"""Trace Completeness 红线（ADR-0104 Wave 5）。

行为认证（真实 trace 形态）：
- GisTraceChain 认证：task class 必备阶段 / correlation / 失败码 / 体量上界；
- geocompute 事件序列认证：真实 emit() → 环形缓冲 → 序列契约
  （起点/终点/失败码/取消后无完成/重试关系）；
- 脱敏认证：敏感键与超长值必须被抓住；
- 认证表字节一致（contract-only 缺口如实披露，不许伪造 passed）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from gen_trace_certification import DEFAULT_OUT, generate  # noqa: E402

from app.lib.quality.trace_contract import (  # noqa: E402
    MAX_RECORD_BYTES,
    TRACE_CLASS_REQUIREMENTS,
    certify_chain,
    certify_geocompute_run,
    validate_chain,
)


# ── 真实形态构造器（与 GisTraceChain.as_dict() 拍平投影一致）─────────────


def _chain(task_class: str, *, complete: bool = True, **overrides):
    from app.lib.runtime.gis_trace import GisTraceChain

    chain = GisTraceChain(turn_id="turn-t1", session_id="sess-s1")
    stages = TRACE_CLASS_REQUIREMENTS[task_class]
    for name in stages:
        payload = {}
        if name == "FINAL_VERDICT":
            payload = {"failure_code": "DATA_UNQUALIFIED", "verdict": "failed"}
        chain.record(getattr(__import__(
            "app.lib.runtime.gis_trace", fromlist=["Stage"]).Stage, name),
            **payload)
    if not complete:
        # 摘掉一个必备阶段
        victim = sorted(stages)[0]
        chain._records = {
            k: v for k, v in chain._records.items()
            if any(r.stage.name != victim for r in v)
        }
        chain._total = sum(len(v) for v in chain._records.values())
    d = chain.as_dict()
    d.update(overrides)
    return d


def test_requirements_vocabulary_aligned_with_stage_enum():
    """契约里的阶段名必须全部真实存在于 18 规范阶段（防手拼词表漂移）。"""
    from app.lib.runtime.gis_trace import Stage

    valid = {s.name for s in Stage}
    for tc, stages in TRACE_CLASS_REQUIREMENTS.items():
        assert stages <= valid, f"{tc}: unknown stages {stages - valid}"


@pytest.mark.parametrize("task_class", sorted(TRACE_CLASS_REQUIREMENTS))
def test_complete_chain_certifies(task_class):
    cert = certify_chain(_chain(task_class), task_class)
    assert cert.passed, cert.to_dict()


def test_missing_stage_is_blocker():
    gaps = validate_chain(_chain("map_product", complete=False), "map_product")
    codes = {g.code for g in gaps}
    assert "MISSING_STAGES" in codes
    cert = certify_chain(_chain("map_product", complete=False), "map_product")
    assert not cert.passed


def test_missing_correlation_is_blocker():
    d = _chain("plan_only")
    d["turn_id"] = ""
    codes = {g.code for g in validate_chain(d, "plan_only")}
    assert "MISSING_CORRELATION" in codes


def test_failure_without_failure_code_is_blocker():
    from app.lib.runtime.gis_trace import GisTraceChain, Stage

    chain = GisTraceChain(turn_id="t", session_id="s")
    for name in TRACE_CLASS_REQUIREMENTS["failure_path"]:
        if name == "FINAL_VERDICT":
            chain.record(Stage.FINAL_VERDICT, verdict="failed")  # 缺 failure_code
        else:
            chain.record(getattr(Stage, name))
    codes = {g.code for g in validate_chain(chain.as_dict(), "failure_path")}
    assert "FAILURE_CODE_MISSING" in codes


def test_oversized_record_is_major():
    d = _chain("plan_only")
    d["stages"][0]["blob"] = "x" * (MAX_RECORD_BYTES + 1)
    codes = {g.code for g in validate_chain(d, "plan_only")}
    assert "RECORD_OVERSIZE" in codes


# ── geocompute 真实 emit 路径 ────────────────────────────────────────────


def test_geocompute_good_run_certifies_via_real_ring():
    from app.services.geocompute import tracing

    tracing._ring.clear()
    tracing.emit("run_started", run_id="r-ok", plan_fingerprint="pf")
    tracing.emit("node_dispatched", run_id="r-ok", node_id="n1")
    tracing.emit("node_completed", run_id="r-ok", node_id="n1", duration_s=0.1)
    tracing.emit("run_finished", run_id="r-ok", status="completed")
    events = [e for e in tracing.recent_events(50) if e.get("run_id") == "r-ok"]
    cert = certify_geocompute_run(events)
    assert cert.passed, cert.to_dict()


def test_geocompute_cancel_then_complete_is_blocker():
    from app.services.geocompute import tracing

    tracing._ring.clear()
    tracing.emit("run_started", run_id="r-bad")
    tracing.emit("node_dispatched", run_id="r-bad", node_id="n1")
    tracing.emit("node_cancelled", run_id="r-bad", node_id="n1")
    tracing.emit("node_completed", run_id="r-bad", node_id="n1")  # 取消后完成
    tracing.emit("run_finished", run_id="r-bad", status="cancelled")
    events = [e for e in tracing.recent_events(50) if e.get("run_id") == "r-bad"]
    codes = {g.code for g in certify_geocompute_run(events).gaps}
    assert "COMPLETED_AFTER_CANCEL" in codes


def test_geocompute_failure_without_error_code_is_blocker():
    from app.services.geocompute import tracing

    tracing._ring.clear()
    tracing.emit("run_started", run_id="r-fail")
    tracing.emit("node_failed", run_id="r-fail", node_id="n1")  # 无 error_code
    tracing.emit("run_finished", run_id="r-fail", status="failed")
    events = [e for e in tracing.recent_events(50) if e.get("run_id") == "r-fail"]
    codes = {g.code for g in certify_geocompute_run(events).gaps}
    assert "FAILURE_CODE_MISSING" in codes


def test_geocompute_missing_terminal_is_blocker():
    from app.services.geocompute import tracing

    tracing._ring.clear()
    tracing.emit("run_started", run_id="r-hang")
    tracing.emit("node_dispatched", run_id="r-hang", node_id="n1")
    events = [e for e in tracing.recent_events(50) if e.get("run_id") == "r-hang"]
    codes = {g.code for g in certify_geocompute_run(events).gaps}
    assert "TERMINAL_EVENT_MISSING" in codes


# ── 脱敏 ─────────────────────────────────────────────────────────────────


def test_sensitive_keys_are_blocked():
    from app.lib.runtime.gis_trace import GisTraceChain, Stage

    chain = GisTraceChain(turn_id="t", session_id="s")
    chain.record(Stage.TOOL_CALLS, api_key="sk-should-not-be-here")
    codes = {g.code for g in validate_chain(chain.as_dict(), "tool_execution")}
    assert "SENSITIVE_KEY" in codes


# ── 认证表 ───────────────────────────────────────────────────────────────


def test_certification_table_current_and_honest():
    content = generate()
    assert DEFAULT_OUT.exists(), "认证表未生成：python scripts/gen_trace_certification.py"
    assert DEFAULT_OUT.read_text(encoding="utf-8") == content
    # 诚实性：18 阶段全部出现；contract-only 缺口如实标注
    for stage in ("USER_INTENT", "FINAL_VERDICT", "MAP_OBSERVATION"):
        assert stage in content
    assert "contract-only" in content
