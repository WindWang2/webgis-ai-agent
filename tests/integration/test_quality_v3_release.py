"""Release readiness 政策断言测试（Quality V3 W15）。

R7 修订的核心：not-run 不得被"合法化全绿"——gates 全绿 + 车道
not-run = NOT-READY（显式 violations）；READY 只能来自 pass 证据或
带 reason 的逐条 waiver。全部用合成 runner 报告驱动，不跑重车道。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location(
        "gen_release_readiness",
        REPO / "scripts/gen_release_readiness.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def fast_gates(monkeypatch, mod):
    """政策测试不重复执行真闸（真闸语义由 test_no_evidence 一处锁定）。"""
    def fake_gate(cmd):
        return {"command": " ".join(str(c) for c in cmd), "status": "pass",
                "detail": []}
    monkeypatch.setattr(mod, "_run_gate", fake_gate)


def _write_report(tmp_path: Path, lanes: dict[str, bool]) -> str:
    payload = {
        "lane": "full",
        "lanes_run": list(lanes),
        "all_ok": all(lanes.values()),
        "total_s": 1.0,
        "lanes": [{"lane": lane, "ok": ok, "steps": []}
                  for lane, ok in lanes.items()],
    }
    p = tmp_path / "runner-report.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


def test_no_evidence_is_not_ready_with_explicit_violations(mod):
    """M-7 核心：gates 全绿 + 无证据 ≠ 全绿。must 显式 NOT-READY。"""
    state, violations = mod.build_state(None, None)
    assert state["verdict"] == "NOT-READY"
    assert state["gates"] and all(
        g["status"] == "pass" for g in state["gates"].values()), (
        "本测试前提：现场闸在当前提交上全绿")
    assert set(state["lanes"].values()) == {"not-run"}
    assert sorted(violations) == [
        "lane backend 状态 not-run（需 pass 证据或显式 waiver）",
        "lane quick 状态 not-run（需 pass 证据或显式 waiver）"]


def test_pass_evidence_yields_ready(mod, fast_gates, tmp_path):
    report = _write_report(tmp_path, {"quick": True, "backend": True})
    state, violations = mod.build_state(report, None)
    assert violations == []
    assert state["verdict"] == "READY"
    ev = state["runner_evidence"]
    assert ev and ev["sha256"], "证据必须带 sha256（可复现锚点）"
    assert state["lanes"]["real"] == "not-run", (
        "未跑的 real lane 必须显式 not-run，不被 ready 掩盖")


def test_failed_lane_blocks_ready(mod, fast_gates, tmp_path):
    report = _write_report(tmp_path, {"quick": True, "backend": False})
    state, violations = mod.build_state(report, None)
    assert state["verdict"] == "NOT-READY"
    assert any("backend" in v for v in violations)


def test_waiver_with_reason_unblocks(mod, fast_gates, tmp_path):
    report = _write_report(tmp_path, {"quick": True, "backend": False})
    waiver = tmp_path / "waiver.json"
    waiver.write_text(json.dumps(
        {"waivers": [{"lane": "backend",
                      "reason": "backend lane 在 CI release DAG 执行"}]}),
        encoding="utf-8")
    state, violations = mod.build_state(report, str(waiver))
    assert violations == []
    assert state["verdict"] == "READY"
    assert state["waivers"][0]["reason"], "waiver 必须留 reason"


def test_waiver_without_reason_rejected(mod, fast_gates, tmp_path):
    waiver = tmp_path / "waiver.json"
    waiver.write_text(json.dumps({"waivers": [{"lane": "backend"}]}),
                      encoding="utf-8")
    with pytest.raises(SystemExit, match="reason"):
        mod.build_state(None, str(waiver))


def test_missing_report_is_not_silent_pass(mod, fast_gates):
    state, _ = mod.build_state("/no/such/report.json", None)
    assert state["verdict"] == "NOT-READY"
    assert "报告不存在" in state["lane_evidence_reason"]


def test_known_gaps_disclose_existing_limitations(mod, fast_gates):
    state, _ = mod.build_state(None, None)
    gap_ids = {g["id"] for g in state["known_gaps"]}
    assert "adr-duplicate-118" in gap_ids, "存量 ADR 撞号必须披露"
    assert "real-lane-opt-in" in gap_ids


def test_state_is_deterministic_and_serializable(mod, fast_gates):
    a, _ = mod.build_state(None, None)
    b, _ = mod.build_state(None, None)
    assert json.loads(json.dumps(a)) == b
    assert json.loads(json.dumps(a)) == a
