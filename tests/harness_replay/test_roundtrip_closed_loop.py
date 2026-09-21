"""「录制 → 重放」闭环 + drift/delta + committed 基线门（方向 8 / ADR-0212）。

任务书必列验收：
- trace boundedness / 秘密不内联 / 大 ref 不内联（roundtrip 侧）；
- replay 无生产副作用（不写真实 session/map）；
- 多轮 trace 链接（同 session traces → multi-turn 场景，跨轮证据累积）；
- frozen tool output replay（录制收据驱动 gate 重评测）+ 确定性双跑；
- manifest/registry drift 检测 + 决策重推导 delta（「哪里变了」）；
- regression ratchet：committed baseline 漂移 → exit 1 + 可读消息 + 指引。
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

from app.lib.harness.replay.bench import (
    compare_results,
    run_one,
    run_suite,
    write_baseline,
)
from app.lib.harness.replay.drift import capability_registry_digest
from app.lib.harness.replay.drift import registry_drift
from app.lib.harness.replay.drift import (
    diff_decisions,
    rederive_capability_decision,
)
from app.lib.harness.replay.replayer import OfflineReplayer
from app.lib.harness.replay.roundtrip import traces_to_scenario
from app.lib.harness.replay.schema import build_trace
from app.lib.runtime.decision_record import (
    DECISION_KIND_CAPABILITY_RESOLUTION,
    DECISION_KIND_PLAN_SELECTION,
    decision_record,
)
from app.lib.runtime.gis_trace import GisTraceChain, Stage

pytestmark = pytest.mark.cartography

REPO = Path(__file__).resolve().parents[2]
BASELINE_PATH = REPO / "tests/fixtures/replay/baseline.json"


def _recorded_trace(
    *,
    turn_id: str,
    session_id: str,
    registry_digest: str = "",
    with_decisions: bool = True,
    big_result: bool = False,
) -> Dict[str, Any]:
    """合成一条「生产录制形态」的 ReplayTrace（走真实 build_trace 链路）。"""
    chain = GisTraceChain(turn_id=turn_id, session_id=session_id)
    if with_decisions:
        chain.record(
            Stage.CANDIDATE_WORKFLOWS,
            candidates=["poi_distribution_overview"],
            selected="poi_distribution_overview",
            decision=decision_record(
                DECISION_KIND_PLAN_SELECTION,
                selected="poi_distribution_overview",
                alternatives=[
                    {"id": "poi_distribution_overview", "rank": 0},
                ],
                inputs={
                    "task": "heatmap",
                    "situation": {"crs": "EPSG:4326", "feature_count": 12},
                },
                policy_version="recipe_select.v1",
            ),
        )
    chain.record(Stage.TOOL_CALLS, tool="webgis_layer_upsert",
                 call_id=f"{turn_id}-c1")
    if big_result:
        # 大 ref 不内联：结果体不入链，生产 dispatch 面只发顶层 ref 键。
        chain.record(Stage.TOOL_RESULTS, tool="webgis_layer_upsert",
                     status="ok", geojson_ref="ref:geojson:big")
    else:
        chain.record(Stage.TOOL_RESULTS, tool="webgis_layer_upsert",
                     status="ok")
    return build_trace(
        session_id=session_id,
        turn_id=turn_id,
        chain_dict=chain.as_dict(),
        turn_summary={"outcome": {"outcome": "succeeded"}},
        env={"registry_digest": registry_digest} if registry_digest else None,
    ).to_dict()


class TestRoundtripClosedLoop:
    def test_recorded_trace_replays_as_scenario(self):
        trace = _recorded_trace(turn_id="rt-a", session_id="rs-a")
        scenario = traces_to_scenario([trace])
        assert scenario is not None
        assert scenario.category == "recorded"
        assert scenario.tags == ["recorded"]
        assert scenario.turns[0].ops[0].tool == "webgis_layer_upsert"
        assert scenario.decisions[0]["kind"] == DECISION_KIND_PLAN_SELECTION

    def test_roundtrip_replay_is_deterministic(self):
        """frozen tool output replay + 确定性：同一录制件两次重放同 digest。"""
        trace = _recorded_trace(turn_id="rt-b", session_id="rs-b")
        scenario = traces_to_scenario([trace])

        async def _go():
            return await OfflineReplayer(seed=2).replay_scenario(scenario)

        r1 = asyncio.run(_go())
        r2 = asyncio.run(_go())
        assert r1.replay_digest == r2.replay_digest
        assert r1.ok

    def test_multi_turn_trace_linkage(self):
        """同 session 两条 trace → multi-turn 场景，跨轮证据累积。"""
        t1 = _recorded_trace(turn_id="rt-m1", session_id="rs-m")
        t2 = _recorded_trace(turn_id="rt-m2", session_id="rs-m")
        scenario = traces_to_scenario([t2, t1])  # 乱序输入 → epoch/turn 排序
        assert scenario is not None
        assert [len(t.ops) for t in scenario.turns] == [1, 1]
        assert "multi_turn" in scenario.tags

        async def _go():
            return await OfflineReplayer(seed=3).replay_scenario(scenario)

        result = asyncio.run(_go())
        # 跨轮情境持续性：共享 harness，证据第 2 轮 ≥ 第 1 轮。
        assert result.turns[1].evidence_count >= result.turns[0].evidence_count

    def test_roundtrip_replay_no_production_side_effect(self, tmp_path,
                                                        monkeypatch):
        """重放不写真实 session/map：存储目录不动、无落盘会话。"""
        from app.services.mapspec import store as mapspec_store_module

        monkeypatch.setattr(mapspec_store_module,
                            "BASE_STORAGE_DIR", tmp_path)
        before = sorted(p.name for p in tmp_path.iterdir())
        trace = _recorded_trace(turn_id="rt-c", session_id="rs-c")
        scenario = traces_to_scenario([trace])

        async def _go():
            return await OfflineReplayer(seed=4).replay_scenario(scenario)

        asyncio.run(_go())
        after = sorted(p.name for p in tmp_path.iterdir())
        assert after == before

    def test_large_ref_not_inlined_in_roundtrip_receipt(self):
        trace = _recorded_trace(turn_id="rt-d", session_id="rs-d",
                                big_result=True)
        assert trace["truncated"] is False  # 结果体本就不入 trace
        scenario = traces_to_scenario([trace])
        receipt = scenario.turns[0].ops[0].result
        assert receipt.get("geojson_ref") == "ref:geojson:big"
        assert "features" not in receipt  # 载荷不入收据

    def test_trace_boundedness_decisions_cap(self):
        """决策索引有界（≤16）：阶段窗口（8 条）× 每条 2 决策 = 16 封顶。"""
        chain = GisTraceChain(turn_id="rt-e", session_id="rs-e")
        for i in range(24):
            chain.record(
                Stage.SELECTED_WORKFLOW,
                recipe_id=f"r{i}",
                decisions=[
                    decision_record(
                        DECISION_KIND_CAPABILITY_RESOLUTION,
                        selected=f"tool:a{i}",
                        inputs={"capability": f"cap_a{i}"},
                    ),
                    decision_record(
                        DECISION_KIND_CAPABILITY_RESOLUTION,
                        selected=f"tool:b{i}",
                        inputs={"capability": f"cap_b{i}"},
                    ),
                ],
            )
        trace = build_trace(session_id="rs-e", turn_id="rt-e",
                            chain_dict=chain.as_dict(), turn_summary={})
        assert len(trace.decisions) == 16
        assert trace.truncated is False

    def test_secret_redaction_in_decision_inputs(self):
        chain = GisTraceChain(turn_id="rt-f", session_id="rs-f")
        chain.record(
            Stage.CANDIDATE_WORKFLOWS,
            decision=decision_record(
                DECISION_KIND_PLAN_SELECTION,
                selected="r",
                inputs={"task": "t",
                        "api_key": "sk-abcdefgh12345678"},
            ),
        )
        trace = build_trace(session_id="rs-f", turn_id="rt-f",
                            chain_dict=chain.as_dict(), turn_summary={})
        blob = json.dumps(trace.to_dict())
        assert "sk-abcdefgh12345678" not in blob

    def test_domain_situation_maps_survive_trace_freeze(self):
        """review P1-3 端到端：credentials/dependency 域映射穿过
        build_trace 后保真（rederive 行为输入不被子串 marker 误杀）。"""
        chain = GisTraceChain(turn_id="rt-h", session_id="rs-h")
        chain.record(
            Stage.CANDIDATE_WORKFLOWS,
            decision=decision_record(
                DECISION_KIND_PLAN_SELECTION,
                selected="r",
                inputs={
                    "task": "t",
                    "situation": {
                        "credentials_present": {"api_key:upstream": True},
                        "dependency_available": {"postgis": False},
                        "auth_tier": 2,
                    },
                },
            ),
        )
        trace = build_trace(session_id="rs-h", turn_id="rt-h",
                            chain_dict=chain.as_dict(), turn_summary={})
        situation = trace.decisions[0]["inputs"]["situation"]
        assert situation["credentials_present"] == {"api_key:upstream": True}
        assert situation["dependency_available"] == {"postgis": False}
        assert situation["auth_tier"] == 2
        # situation_revision 同源保真。
        assert trace.situation_revision["credentials_present"] == {
            "api_key:upstream": True}


class TestDriftAndDelta:
    def test_registry_digest_stable_and_sensitive(self):
        d1 = capability_registry_digest()
        d2 = capability_registry_digest()
        assert d1 and d1 == d2  # 静态 graph → 稳定

        # 行为面变化（capability→provider 边）→ digest 变。
        # stub 严格按生产契约：capability_providers → Dict[face, List[id]]
        # （review P1-2：list 形状的假契约曾掩盖 provider 边盲区）。
        class _Node:
            def __init__(self, nid: str, kind: str):
                self.id = nid
                self.kind = kind
                self.extras: Dict[str, Any] = {}

        class _Graph:
            def __init__(self, tools: list):
                self._tools = tools

            def nodes_by_kind(self, kind: str):
                if kind == "capability":
                    return [_Node("cap_a", kind), _Node("cap_b", kind)]
                return []

            def capability_providers(self, cap: str):
                # 生产形态：{"tools": [...], "models": [...], ...}
                return {"tools": list(self._tools), "models": [],
                        "workflows": [], "templates": []}

            def fallback_chain(self, kind: str, cap: str):
                return []

            def conflicts_of_capability(self, cap: str):
                return []

        baseline = capability_registry_digest(graph=_Graph(["tool_x"]))
        mutated = capability_registry_digest(graph=_Graph(["tool_y"]))
        assert mutated != baseline, (
            "provider re-wiring must change the registry digest")

    def test_registry_drift_report_shape(self):
        assert registry_drift("", "abc") is None  # 缺席不制造假漂移
        assert registry_drift("abc", "abc") is None
        drift = registry_drift("a" * 64, "b" * 64)
        assert drift is not None and drift["kind"] == "registry_drift"

    def test_rederive_and_decision_delta(self):
        """capability_resolution 决策用冻结 inputs 重跑 → 决策级 delta。"""
        from app.services.gis_harness.capability_graph import (
            get_capability_graph,
        )

        graph = get_capability_graph()
        cap_nodes = sorted(
            graph.nodes_by_kind("capability"), key=lambda n: n.id)
        assert cap_nodes, "capability graph must not be empty"
        cap_id = str(cap_nodes[0].id)
        record = decision_record(
            DECISION_KIND_CAPABILITY_RESOLUTION,
            selected="tool:does_not_exist_anywhere",
            alternatives=[{"id": "does_not_exist_anywhere",
                           "score": 0.5, "status": "eligible"}],
            inputs={"capability": cap_id, "required": True,
                    "situation": {}},
            policy_version="capability_resolution.v1",
        )
        rederived = rederive_capability_decision(record)
        assert rederived is not None
        assert rederived["capability"] == cap_id
        # 录制的 selected 在当前 registry 不存在 → delta 精确钉出。
        diffs = diff_decisions([record], [rederived])
        assert diffs
        assert any(d["type"] == "selected_changed" for d in diffs)
        # 一致重推导（按当前最优 selected/score/status 重建录制面）→ 无
        # delta（这是「registry 未变 ⇒ 决策不变」的确定性自洽证明）。
        best = rederived["selected"]
        assert best, "graph capability must have a provider for this case"
        best_alt = rederived["alternatives"][0]
        consistent = decision_record(
            DECISION_KIND_CAPABILITY_RESOLUTION,
            selected=best,
            alternatives=[dict(best_alt)],
            inputs={"capability": cap_id, "required": True,
                    "situation": {}},
            policy_version="capability_resolution.v1",
        )
        consistent_rederived = rederive_capability_decision(consistent)
        assert consistent_rederived is not None
        assert consistent_rederived["selected"] == best
        assert diff_decisions([consistent], [consistent_rederived]) == []

    def test_rederive_restores_full_fidelity_situation(self):
        """review P1-3：全息情境快照往返 —— 凭据/依赖面不得在冻结中丢失。"""
        from app.services.gis_harness.capability_resolution import (
            build_situation,
        )
        from app.lib.harness.replay.drift import _situation_from_projection

        ctx = build_situation(task_hint="buffer analysis")
        ctx.credentials_present = {"api_key:upstream": True}
        ctx.dependency_available = {"postgis": True}
        ctx.field_names = ["population", "area_km2"]
        snapshot = ctx.to_rederive_dict()
        assert snapshot["credentials_present"] == {"api_key:upstream": True}
        assert snapshot["dependency_available"] == {"postgis": True}
        assert snapshot["field_names"] == ["population", "area_km2"]
        restored = _situation_from_projection(snapshot)
        assert restored.credentials_present == {"api_key:upstream": True}
        assert restored.dependency_available == {"postgis": True}
        assert restored.field_names == ["population", "area_km2"]

    def test_cli_flags_mutual_exclusion(self, tmp_path):
        """review P2-4：--write-baseline 与 --baseline 互斥（写时不比）。"""
        proc = subprocess.run(
            [sys.executable, str(REPO / "scripts/replay_bench.py"),
             "--suite", "all", "--limit", "1", "--seed", "0",
             "--write-baseline", str(tmp_path / "b.json"),
             "--baseline", str(tmp_path / "c.json")],
            capture_output=True, text=True, timeout=120,
            cwd=str(REPO),
        )
        assert proc.returncode == 2
        assert "互斥" in proc.stderr

    @pytest.mark.asyncio
    async def test_bench_run_one_attaches_drift_attributes(self):
        trace = _recorded_trace(
            turn_id="rt-g", session_id="rs-g",
            registry_digest="f" * 64,
            with_decisions=False,
        )
        scenario = traces_to_scenario([trace])
        assert scenario.registry_digest == "f" * 64
        entry = await run_one(
            OfflineReplayer(seed=5), scenario,
            current_registry_digest=capability_registry_digest())
        assert entry["registry_drift"]["kind"] == "registry_drift"
        assert entry["decision_count"] == 0


class TestCommittedBaselineRatchet:
    @pytest.mark.asyncio
    async def test_committed_baseline_matches_current_corpus(self):
        """回归 ratchet：提交语料的 digest 基线不得静默漂移。"""
        from app.lib.harness.replay.scenarios import build_corpus

        report = await run_suite(build_corpus(), seed=0, profile="small")
        comparison = compare_results(report, str(BASELINE_PATH))
        assert comparison["drifts"] == [], (
            "committed replay baseline drifted; if intentional, regenerate "
            "with: python scripts/replay_bench.py --suite all "
            f"--write-baseline {BASELINE_PATH}"
        )

    def test_baseline_file_shape(self):
        payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        assert payload["kind"] == "replay_bench_baseline"
        assert payload["corpus_version"] == 1
        assert payload["green"] == 140 and payload["red"] == 0
        assert len(payload["entries"]) == 140
        assert all(set(e) == {"scenario_id", "ok", "replay_digest"}
                   for e in payload["entries"])

    def test_ratchet_failure_message_is_actionable(self, tmp_path):
        """基线漂移 → exit 1 + 场景级消息 + 显式重建指引（禁裸 snapshot）。

        资源纪律（review P2-7）：本用例用 --limit 4 小语料走 CLI 门
        （committed 140 场景基线的一致性由上一个测试单独保证），避免
        一次测试运行内重复全量 suite。
        """
        from app.lib.harness.replay.bench import select_scenarios
        from app.lib.harness.replay.scenarios import build_corpus

        subset = select_scenarios(build_corpus(), "all", limit=4)

        async def _mk():
            return await run_suite(subset, seed=0, profile="small")

        report = asyncio.run(_mk())
        baseline_path = tmp_path / "baseline.json"
        write_baseline(report, str(baseline_path), corpus_version=1)
        # 篡改一个 digest 模拟回归 → 基线形态喂给 CLI 门。
        tampered = json.loads(json.dumps(report))
        tampered["entries"][0]["replay_digest"] = "0" * 64
        tampered_baseline = tmp_path / "tampered.json"
        write_baseline(tampered, str(tampered_baseline), corpus_version=1)
        proc = subprocess.run(
            [sys.executable, str(REPO / "scripts/replay_bench.py"),
             "--suite", "all", "--limit", "4", "--seed", "0",
             "--baseline", str(tampered_baseline)],
            capture_output=True, text=True, timeout=300,
            cwd=str(REPO),
        )
        assert proc.returncode == 1, proc.stderr[-800:]
        assert "replay baseline drift" in proc.stderr
        assert "replay_digest drift" in proc.stderr
        assert "--write-baseline" in proc.stderr
