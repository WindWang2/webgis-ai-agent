"""differential replay 契约（ADR-0214 D7，WP6）：digest 漂移 → step 级 delta。"""
from __future__ import annotations

import json

import pytest

from app.lib.harness.replay.bench import diff_payloads, diff_reports

pytestmark = pytest.mark.cartography


def _entry(sid: str, digest: str, *, ok: bool = True,
           projection: dict | None = None) -> dict:
    return {
        "scenario_id": sid, "ok": ok, "replay_digest": digest,
        **({"projection": projection} if projection is not None else {}),
    }


def _projection(gate_passed=True, checks=None, goal="pass",
                mutations=None, dispatch=None, receipt=None) -> dict:
    return {"turns": [{
        "turn": 0,
        "gate": {"overall_passed": gate_passed,
                 "checks": checks or {}},
        "goal": {"status": goal},
        "mutations": mutations or [],
        "dispatch": dispatch or [],
        "receipt": receipt or {},
    }]}


class TestDiffPayloads:
    def test_identical_runs_are_unchanged(self):
        proj = _projection()
        base = {"green": 1, "entries": [_entry("s1", "d1", projection=proj)]}
        cur = {"green": 1, "entries": [_entry("s1", "d1", projection=proj)]}
        out = diff_payloads(base, cur)
        assert out["summary"]["unchanged"] == 1
        assert out["summary"]["drifted"] == 0
        assert out["deltas"] == []

    def test_new_and_missing_scenarios(self):
        base = {"green": 1, "entries": [_entry("old", "d")] }
        cur = {"green": 1, "entries": [_entry("new", "d")] }
        out = diff_payloads(base, cur)
        kinds = {d["scenario_id"]: d["kind"] for d in out["deltas"]}
        assert kinds == {"old": "missing", "new": "new"}

    def test_gate_check_flip_localized(self):
        base = {"green": 1, "entries": [_entry("s", "d1", projection=_projection(
            checks={"MapSpecValidity": {"passed": True, "evaluated": True}}))]}
        cur = {"green": 0, "entries": [_entry("s", "d2", ok=False, projection=_projection(
            checks={"MapSpecValidity": {"passed": False, "evaluated": True}}))]}
        out = diff_payloads(base, cur)
        assert out["summary"]["drifted"] == 1
        delta = out["deltas"][0]
        assert delta["scenario_id"] == "s"
        item = delta["delta"][0]
        assert item["kind"] == "gate_check_flip"
        assert item["turn"] == 0
        assert item["key"] == "MapSpecValidity"
        assert item["baseline"]["passed"] is True
        assert item["current"]["passed"] is False

    def test_goal_status_change(self):
        base = {"entries": [_entry("s", "d1", projection=_projection(goal="pass"))]}
        cur = {"entries": [_entry("s", "d2", projection=_projection(goal="fail"))]}
        out = diff_payloads(base, cur)
        item = out["deltas"][0]["delta"][0]
        assert item["kind"] == "goal_status_changed"
        assert (item["baseline"], item["current"]) == ("pass", "fail")

    def test_mutation_drift_localized(self):
        base = {"entries": [_entry("s", "d1", projection=_projection(
            mutations=[{"op": "layer_upsert", "success": True,
                        "spec_fingerprint": "aaa"}]))]}
        cur = {"entries": [_entry("s", "d2", projection=_projection(
            mutations=[{"op": "layer_upsert", "success": True,
                        "spec_fingerprint": "bbb"}]))]}
        out = diff_payloads(base, cur)
        item = out["deltas"][0]["delta"][0]
        assert item["kind"] == "mutation_drift"
        assert item["key"] == "[0]layer_upsert"
        assert item["baseline"]["spec_fingerprint"] == "aaa"
        assert item["current"]["spec_fingerprint"] == "bbb"

    def test_dispatch_flip_and_receipt_drift(self):
        base = {"entries": [_entry("s", "d1", projection=_projection(
            dispatch=[{"call_id": "c1", "allowed": True}],
            receipt={"receipt": {"c1": {"status": "ok", "ref_minted": True}},
                     "receipt_repeat": {"c1": "repeated"}}))]}
        cur = {"entries": [_entry("s", "d2", projection=_projection(
            dispatch=[{"call_id": "c1", "allowed": False,
                       "capability": "layer_management"}],
            receipt={"receipt": {"c1": {"status": "error",
                                        "ref_minted": False,
                                        "error_code": "TOOL_ERROR"}}}))]}
        out = diff_payloads(base, cur)
        kinds = {d["kind"] for d in out["deltas"][0]["delta"]}
        assert kinds == {"dispatch_flip", "receipt_drift",
                         "receipt_repeat_drift"}
        dispatch_item = next(
            d for d in out["deltas"][0]["delta"]
            if d["kind"] == "dispatch_flip")
        assert dispatch_item["key"] == "c1"

    def test_diff_reports_reads_files(self, tmp_path):
        base = {"green": 1, "entries": [_entry("s", "d1")]}
        cur = {"green": 1, "entries": [_entry("s", "d2")]}
        base_path = tmp_path / "master.json"
        cur_path = tmp_path / "feature.json"
        base_path.write_text(json.dumps(base), encoding="utf-8")
        cur_path.write_text(json.dumps(cur), encoding="utf-8")
        out = diff_reports(str(base_path), str(cur_path))
        assert out["summary"]["drifted"] == 1
        assert out["green_baseline"] == 1

    def test_delta_output_bounded(self):
        proj_base = _projection(checks={
            f"Check{i}": {"passed": True, "evaluated": True}
            for i in range(100)})
        proj_cur = _projection(checks={
            f"Check{i}": {"passed": False, "evaluated": True}
            for i in range(100)})
        base = {"entries": [_entry("s", "d1", projection=proj_base)]}
        cur = {"entries": [_entry("s", "d2", projection=proj_cur)]}
        out = diff_payloads(base, cur)
        assert len(out["deltas"][0]["delta"]) <= 64
