"""运行态持久回归锁（goal-02 M3/M7）。

不变式：
- 载荷 round-trip 无损；未知键 additive 兼容；损坏载荷诚实返回 None；
- verdict 诚实语义：failed/cancelled/blocked/pending 绝不冒充 satisfied；
  optional skipped 不阻碍 satisfied；
- run_log 有界（≤24 环形）；
- running 陈旧判定（crashed → 可 resume）。
"""
from __future__ import annotations

import time

from app.services.gis_harness.analysis_workflows.contracts import (
    AnalysisArtifactEnvelope,
)
from app.services.gis_harness.analysis_workflows.families import build_contract
from app.services.gis_harness.analysis_workflows.state import (
    MAX_RUN_LOG,
    new_state,
    state_from_payload,
    state_to_payload,
    verdict_of,
)


def _state():
    contract = build_contract("hazard_exposure", workflow_id="awf.t.state")
    return new_state(contract, contract_fingerprint="fp0")


def test_roundtrip_lossless():
    st = _state()
    st.steps["hazard_buffer"].state = "satisfied"
    st.envelopes["hazard_buffer"] = AnalysisArtifactEnvelope(
        step_id="hazard_buffer", workflow_id=st.workflow_id,
        tool_name="buffer_analysis", ref="ref:geojson-x",
        feature_count=3, stats={"output_count": 3})
    st.log("run", step="hazard_buffer", outcome="ok")
    restored = state_from_payload(state_to_payload(st))
    assert restored is not None
    assert restored.steps["hazard_buffer"].state == "satisfied"
    assert restored.envelopes["hazard_buffer"].ref == "ref:geojson-x"
    assert restored.run_log[0]["event"] == "run"
    assert restored.contract_fingerprint == "fp0"


def test_unknown_keys_tolerated_and_corrupt_rejected():
    st = _state()
    payload = state_to_payload(st)
    payload["future_field"] = {"anything": 1}
    assert state_from_payload(payload) is not None
    assert state_from_payload({"broken": "shape"}) is None
    assert state_from_payload(None) is None


def test_verdict_honesty_matrix():
    st = _state()
    ids = list(st.steps)
    assert verdict_of(st) == "pending"          # 全 pending
    for sid in ids:
        st.steps[sid].state = "satisfied"
    assert verdict_of(st) == "satisfied"
    st.steps[ids[0]].state = "skipped"          # optional skipped 不阻碍
    assert verdict_of(st) == "satisfied"
    st.steps[ids[0]].state = "failed"
    assert verdict_of(st) == "failed"           # failed 绝不冒充
    st.steps[ids[0]].state = "cancelled"
    assert verdict_of(st) == "cancelled"
    st.steps[ids[0]].state = "blocked"
    assert verdict_of(st) == "blocked"
    st.steps[ids[0]].state = "satisfied"
    st.steps[ids[1]].state = "running"
    assert verdict_of(st) == "partial"


def test_run_log_bounded_ring():
    st = _state()
    for i in range(MAX_RUN_LOG + 10):
        st.log("evt", seq=i)
    assert len(st.run_log) == MAX_RUN_LOG
    assert st.run_log[-1]["seq"] == MAX_RUN_LOG + 9   # 保留最新


def test_running_stale_detection():
    st = _state()
    st.status = "running"
    st.updated_at = time.time()
    assert not st.running_stale()
    st.updated_at = time.time() - 400
    assert st.running_stale()
    st.status = "pending"
    assert not st.running_stale()                # 非 running 不判 stale
