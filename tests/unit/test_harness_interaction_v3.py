"""
V3 Harness–Map Interaction Closed Loop — BE-2 slice unit tests.

Covers (design §9 backend items owned by this slice):
- action_id minting in ToolDispatchService (command / commands[] / no-command /
  idempotency / requested-snapshot 2KB cap), carried by SSE slim_event.
- record_map_action_issued FIFO cap + per-record run_id/turn_id params.
- heatmap ref cursor pattern (REF_CURSOR_PATTERN).
- coverage / success / convergence / recovery metric math;
  no-evidence → 0.0, never 100.
- evaluate_with_evidence map_action_reader seam: interaction section
  {issued, acked, actions}, no-ack → status stays ISSUED (missing evidence),
  _evidence_to_dict serializes map_actions.
- evaluate_evidence require_interaction gate: strict vs exempt, V2 callers
  byte-identical (interaction dims exempt when issued == 0).
- turn_id minting: step_result SSE payload additive turn_id + record threading.
"""
import asyncio
import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.lib.harness.evidence import MapActionEvidence, MapActionStatus
from app.lib.harness.evaluator import HarnessEvaluator, INTERACTION_METRICS
from app.lib.harness.pi_agent_harness import (
    CAMERA_CENTER_TOL_DEG,
    CAMERA_ZOOM_TOL,
    REF_CURSOR_PATTERN,
    PiAgentHarness,
    _ack_converged,
    _ack_is_well_formed,
    _camera_match,
    _is_verifiable_ack,
)
from app.services.tool_dispatch_service import (
    MAP_ACTION_ID_PREFIX,
    REQUESTED_SNAPSHOT_MAX_BYTES,
    ToolDispatchResult,
    ToolDispatchService,
)

import app.agent_pi_bridge as bridge_mod


# ── shared fixtures ──────────────────────────────────────────────────────

@pytest.fixture
async def clean_session():
    from app.services.session_data import session_data_manager
    sid = "test-harness-interaction-v3-session"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


@pytest.fixture
def fake_registry():
    return MagicMock(dispatch=AsyncMock())


def _tc(name: str, args: dict, tc_id: str = "call_1") -> dict:
    return {"id": tc_id, "function": {"name": name, "arguments": args}}


def _make_reader(acks):
    """Build an async map_action_reader returning the given ack list."""
    async def reader(session_id):
        return acks
    return reader


# ── action_id minting in ToolDispatchService ─────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_mints_action_id_single_command(fake_registry, clean_session):
    """result.command（单命令形态）→ 铸 ma-<hex16>，写回 command dict + slim_event。"""
    fake_registry.dispatch.return_value = {
        "success": True,
        "command": "fly_to",
        "params": {"center": [116.0, 39.0], "zoom": 12},
        "message": "flown",
    }
    svc = ToolDispatchService(registry=fake_registry)
    result = await svc.dispatch(_tc("webgis_view_set", {}), clean_session, set())

    assert result.status == "ok"
    assert len(result.map_actions) == 1
    ma = result.map_actions[0]
    assert ma["action_id"].startswith(MAP_ACTION_ID_PREFIX)
    assert len(ma["action_id"]) == len(MAP_ACTION_ID_PREFIX) + 16
    assert ma["command"] == "fly_to"
    assert ma["requested"] == {"center": [116.0, 39.0], "zoom": 12}
    # action_id 写入 command dict（SSE step_result 由此携带）
    assert result.raw_result["action_id"] == ma["action_id"]
    assert result.slim_event["action_id"] == ma["action_id"]


@pytest.mark.asyncio
async def test_dispatch_mints_action_id_commands_list(fake_registry, clean_session):
    """result.commands[]（批量命令形态）→ 逐条铸唯一 action_id。"""
    fake_registry.dispatch.return_value = {
        "success": True,
        "commands": [
            {"command": "export_map", "params": {"title": "A"}},
            {"command": "export_map", "params": {"title": "B"}},
        ],
        "count": 2,
    }
    svc = ToolDispatchService(registry=fake_registry)
    result = await svc.dispatch(_tc("export_batch_maps", {}), clean_session, set())

    assert len(result.map_actions) == 2
    assert result.map_actions[0]["command"] == "export_map"
    assert result.map_actions[0]["action_id"] != result.map_actions[1]["action_id"]
    assert result.raw_result["commands"][0]["action_id"] == result.map_actions[0]["action_id"]
    assert result.raw_result["commands"][1]["action_id"] == result.map_actions[1]["action_id"]


@pytest.mark.asyncio
async def test_dispatch_no_command_no_minting(fake_registry, clean_session):
    """无 command / commands[] 的结果 → 不铸 id，不写多余键。"""
    fake_registry.dispatch.return_value = {"success": True, "message": "ok"}
    svc = ToolDispatchService(registry=fake_registry)
    result = await svc.dispatch(_tc("st_dbscan", {}), clean_session, set())
    assert result.map_actions == []
    assert "action_id" not in result.raw_result


@pytest.mark.asyncio
async def test_dispatch_minting_preserves_preexisting_action_id(fake_registry, clean_session):
    """已铸过 action_id 的 command dict → 复用，不重复铸造。"""
    fake_registry.dispatch.return_value = {
        "success": True,
        "command": "fly_to",
        "params": {"center": [116.0, 39.0], "zoom": 12},
        "action_id": "ma-preexisting1234",
    }
    svc = ToolDispatchService(registry=fake_registry)
    result = await svc.dispatch(_tc("webgis_view_set", {}), clean_session, set())
    assert result.map_actions[0]["action_id"] == "ma-preexisting1234"
    assert result.raw_result["action_id"] == "ma-preexisting1234"


@pytest.mark.asyncio
async def test_dispatch_requested_snapshot_capped(fake_registry, clean_session):
    """requested 参数快照 ~2KB 封顶：超限键被剔除，快照本身 ≤ 上限。"""
    fake_registry.dispatch.return_value = {
        "success": True,
        "command": "fly_to",
        "params": {
            "center": [116.0, 39.0],
            "zoom": 12,
            "note": "x" * 10000,  # 单独超过 2KB
        },
    }
    svc = ToolDispatchService(registry=fake_registry)
    result = await svc.dispatch(_tc("webgis_view_set", {}), clean_session, set())
    ma = result.map_actions[0]
    snap = json.dumps(ma["requested"], ensure_ascii=False)
    assert len(snap.encode("utf-8")) <= REQUESTED_SNAPSHOT_MAX_BYTES
    assert "note" not in ma["requested"]


# ── record_map_action_issued: FIFO cap + per-record correlation ──────────

def test_record_map_action_issued_bounded_fifo():
    harness = PiAgentHarness(session_id="s")
    for i in range(PiAgentHarness.MAX_EVENTS + 50):
        harness.record_map_action_issued(
            session_id="s", tool_call_id=f"tc{i}", turn_id=f"t{i}",
            action_id=f"ma-{i}", command="fly_to",
            requested={"center": [116.0, 39.0], "zoom": 12},
        )
    assert len(harness.map_actions_issued) <= PiAgentHarness.MAX_EVENTS
    # 最旧的 50 条被淘汰
    assert harness.map_actions_issued[0]["action_id"] == f"ma-{50}"
    last = harness.map_actions_issued[-1]
    assert last["action_id"] == f"ma-{PiAgentHarness.MAX_EVENTS + 49}"
    assert last["run_id"] == "s"  # 未显式给 run → 回退 active（= session_id）
    assert last["turn_id"] == f"t{PiAgentHarness.MAX_EVENTS + 49}"


def test_record_tool_call_result_per_record_run_turn_params():
    """V3 新增可选 per-record run_id/turn_id（additive，向后兼容）。"""
    harness = PiAgentHarness(session_id="s")
    harness.record_tool_call("c1", "webgis_view_set", {}, run_id="run_x", turn_id="turn_y")
    harness.record_tool_result("c1", "webgis_view_set", {"success": True}, run_id="run_x", turn_id="turn_y")
    assert harness.tool_calls[0]["run_id"] == "run_x"
    assert harness.tool_calls[0]["turn_id"] == "turn_y"
    assert harness.tool_results[0]["run_id"] == "run_x"
    assert harness.tool_results[0]["turn_id"] == "turn_y"
    # 缺省 → 回退 active（向后兼容）
    harness2 = PiAgentHarness(session_id="s2")
    harness2.set_correlation(run_id="r_active", turn_id="t_active")
    harness2.record_tool_call("c2", "st_dbscan", {})
    assert harness2.tool_calls[0]["run_id"] == "r_active"
    assert harness2.tool_calls[0]["turn_id"] == "t_active"


def test_ref_cursor_pattern_matches_heatmap():
    """REF_CURSOR_PATTERN 命中 heatmap ref（BE-2 audit 项，与 tool_dispatch_service 前缀一致）。"""
    harness = PiAgentHarness(session_id="s")
    harness.record_tool_call("c1", "render", {"data": "ref:heatmap-abc123"})
    cursors = [c["ref_cursor"] for c in harness.ref_cursors]
    assert "ref:heatmap-abc123" in cursors
    assert REF_CURSOR_PATTERN.search("ref:heatmap-abc123") is not None


# ── convergence / recovery helpers (backend 权威重算，不信 hint 单独) ────

def _ev(**kw):
    base = dict(
        action_id="x", command="fly_to", session_id="s",
        status=MapActionStatus.SUCCEEDED,
    )
    base.update(kw)
    return MapActionEvidence(**base)


def test_camera_match_tolerances():
    """center ≤0.001°、zoom ≤0.05 的容差判定（含边界）。"""
    req = {"center": [116.0, 39.0], "zoom": 12}
    assert _camera_match(req, {"center": [116.0005, 39.0005], "zoom": 12.04}) is True
    assert _camera_match(req, {"center": [116.001, 39.0], "zoom": 12.05}) is True    # 恰在容差内
    assert _camera_match(req, {"center": [116.0011, 39.0], "zoom": 12.05}) is False  # lon 超差
    assert _camera_match(req, {"center": [116.0, 39.0011], "zoom": 12.05}) is False  # lat 超差
    assert _camera_match(req, {"center": [116.0, 39.0], "zoom": 12.06}) is False     # zoom 超差
    assert _camera_match(req, {"center": [116.0, 39.0], "zoom": 11.95}) is True      # 负向在容差内
    assert _camera_match(req, {"center": [116.0, 39.0]}) is False                    # actual 缺 zoom
    assert CAMERA_CENTER_TOL_DEG == 0.001
    assert CAMERA_ZOOM_TOL == 0.05


def test_convergence_never_trusts_hint_alone_when_data_present():
    """数据齐时后端重算：hint 说收敛但数据超差 → 未收敛。"""
    ev = _ev(
        requested={"center": [116.0, 39.0], "zoom": 12},
        actual={"center": [116.05, 39.0], "zoom": 12, "converged": True},
    )
    assert _is_verifiable_ack(ev) is True
    assert _ack_converged(ev) is False


def test_convergence_layer_confirmed_and_hint_fallback():
    """图层增删走 actual.confirmed；其它命令走 converged hint；无数据不可验证。"""
    layer = _ev(command="add_layer", requested={}, actual={"confirmed": True})
    assert _is_verifiable_ack(layer) is True
    assert _ack_converged(layer) is True

    hint = _ev(command="export_map", requested={}, actual={"converged": True})
    assert _is_verifiable_ack(hint) is True
    assert _ack_converged(hint) is True

    nothing = _ev(command="export_map", requested={}, actual={})
    assert _is_verifiable_ack(nothing) is False


def test_ack_is_well_formed():
    """非成功终态恢复证据：具名 status + error/reason 才结构完整。"""
    failed_named = _ev(status=MapActionStatus.FAILED, error="target_not_found", actual={})
    assert _ack_is_well_formed(failed_named) is True
    failed_bare = _ev(status=MapActionStatus.FAILED, actual={})
    assert _ack_is_well_formed(failed_bare) is False
    superseded = _ev(status=MapActionStatus.SUPERSEDED, actual={"reason": "newer_camera_command"})
    assert _ack_is_well_formed(superseded) is True
    cancelled = _ev(status=MapActionStatus.CANCELLED, error="superseded_by_user", actual={})
    assert _ack_is_well_formed(cancelled) is True
    succeeded = _ev(status=MapActionStatus.SUCCEEDED, actual={})
    assert _ack_is_well_formed(succeeded) is False


# ── evaluate_with_evidence: map_action_reader + interaction section ──────

@pytest.mark.asyncio
async def test_evaluate_with_evidence_builds_interaction_section():
    """issued ∪ ack 匹配构建 MapActionEvidence；interaction 段 {issued, acked, actions}。"""
    harness = PiAgentHarness(session_id="s1")
    harness.record_map_action_issued(
        session_id="s1", tool_call_id="c1", turn_id="t1",
        action_id="ma-1", command="fly_to",
        requested={"center": [116.0, 39.0], "zoom": 12},
    )
    harness.record_tool_call("c1", "webgis_view_set", {})
    harness.record_tool_result("c1", "webgis_view_set", {"success": True})

    acks = [{
        "action_id": "ma-1",
        "status": "succeeded",
        "actual": {"center": [116.0001, 39.0001], "zoom": 12.01},
        "correlation": {"turn_id": "turn-ack", "sse_event_id": "7"},
        "duration_ms": 210.0,
    }]
    result = await harness.evaluate_with_evidence(
        expected_tools=["webgis_view_set"], ideal_step_count=1,
        map_action_reader=_make_reader(acks),
    )

    inter = result["interaction"]
    assert inter["issued"] == 1
    assert inter["acked"] == 1
    action = inter["actions"][0]
    assert action["action_id"] == "ma-1"
    assert action["status"] == "succeeded"
    assert action["turn_id"] == "turn-ack"      # ack 侧 correlation 优先
    assert action["sse_event_id"] == "7"
    assert action["duration_ms"] == 210.0
    assert action["verifiable"] is True
    assert action["converged"] is True
    # _evidence_to_dict 序列化 map_actions（按 tool_call_id 归属）
    assert result["evidence"][0]["map_actions"][0]["action_id"] == "ma-1"
    assert result["evidence"][0]["map_actions"][0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_no_ack_status_stays_issued():
    """无 ACK → status 保持 issued（缺失终态证据），coverage 诚实 0。"""
    harness = PiAgentHarness(session_id="s2")
    harness.record_map_action_issued(
        session_id="s2", tool_call_id="c1", turn_id="t1",
        action_id="ma-2", command="fly_to",
        requested={"center": [116.0, 39.0], "zoom": 12},
    )
    result = await harness.evaluate_with_evidence(
        expected_tools=[], ideal_step_count=0, map_action_reader=_make_reader([]),
    )
    inter = result["interaction"]
    assert inter["issued"] == 1
    assert inter["acked"] == 0
    assert inter["actions"][0]["status"] == "issued"
    assert inter["actions"][0]["verifiable"] is False
    assert inter["actions"][0]["converged"] is None
    assert result["metrics"]["InteractionEvidenceCoverage"] == 0.0


@pytest.mark.asyncio
async def test_map_action_reader_error_is_observable_not_crash():
    """reader 抛异常 → 评估不崩，按无 ACK 处理（缺失证据）。"""

    async def broken(session_id):
        raise RuntimeError("store down")

    harness = PiAgentHarness(session_id="s3")
    harness.record_map_action_issued(
        session_id="s3", tool_call_id="c1", turn_id="t1",
        action_id="ma-3", command="fly_to",
    )
    result = await harness.evaluate_with_evidence(
        expected_tools=[], ideal_step_count=0, map_action_reader=broken,
    )
    assert result["interaction"]["issued"] == 1
    assert result["interaction"]["acked"] == 0
    assert result["metrics"]["InteractionEvidenceCoverage"] == 0.0


@pytest.mark.asyncio
async def test_ack_with_unparseable_status_not_trusted():
    """ACK 的 status 无法解析 → 保持 issued（不可信终态 = 缺失证据）。"""
    harness = PiAgentHarness(session_id="s4")
    harness.record_map_action_issued(
        session_id="s4", tool_call_id="c1", turn_id="t1",
        action_id="ma-4", command="fly_to",
    )
    acks = [{"action_id": "ma-4", "status": "melted", "error": "?"}]
    result = await harness.evaluate_with_evidence(
        expected_tools=[], ideal_step_count=0, map_action_reader=_make_reader(acks),
    )
    assert result["interaction"]["actions"][0]["status"] == "issued"
    assert result["interaction"]["acked"] == 0


# ── metric math via evaluate_with_evidence ───────────────────────────────

@pytest.mark.asyncio
async def test_interaction_metrics_math():
    """coverage / success / convergence / recovery 的完整数学（design §5）。"""
    harness = PiAgentHarness(session_id="s5")
    for action_id, cmd in [
        ("ma-1", "fly_to"), ("ma-2", "fly_to"), ("ma-3", "fly_to"),
        ("ma-4", "fly_to"), ("ma-5", "fly_to"), ("ma-6", "add_layer"),
        ("ma-7", "fly_to"),
    ]:
        harness.record_map_action_issued(
            session_id="s5", tool_call_id="c1", turn_id="t1",
            action_id=action_id, command=cmd,
            requested={"center": [116.0, 39.0], "zoom": 12},
        )
    acks = [
        # ma-1: succeeded + 相机收敛
        {"action_id": "ma-1", "status": "succeeded",
         "actual": {"center": [116.0005, 39.0004], "zoom": 12.01}},
        # ma-2: failed + 具名 error → well-formed 恢复证据
        {"action_id": "ma-2", "status": "failed", "error": "target_not_found"},
        # ma-3: superseded + reason → well-formed 恢复证据
        {"action_id": "ma-3", "status": "superseded",
         "actual": {"reason": "newer_camera_command"}},
        # ma-5: succeeded 但相机未收敛（后端重算否决 hint 的缺席）
        {"action_id": "ma-5", "status": "succeeded",
         "actual": {"center": [116.05, 39.0], "zoom": 12}},
        # ma-6: succeeded + 图层 confirmed
        {"action_id": "ma-6", "status": "succeeded",
         "actual": {"confirmed": True}},
        # ma-7: failed 但无 error/reason → 非 well-formed 恢复证据
        {"action_id": "ma-7", "status": "failed", "actual": {}},
    ]
    result = await harness.evaluate_with_evidence(
        expected_tools=[], ideal_step_count=0, map_action_reader=_make_reader(acks),
    )
    m = result["metrics"]
    # 终态 6/7；成功 3/6；收敛 2/3；恢复 well-formed 2/3
    assert m["InteractionEvidenceCoverage"] == 85.71
    assert m["MapCommandExecutionSuccessRate"] == 50.0
    assert m["InteractionStateConvergenceRate"] == 66.67
    assert m["InteractionRecoveryRate"] == 66.67


def test_interaction_metrics_no_evidence_zero_not_hundred():
    """无 issued 记录 → 4 个交互指标全 0.0（缺失证据，绝不为 100）。"""
    harness = PiAgentHarness(session_id="s6")
    metrics = harness.evaluate_all(expected_tools=[], ideal_step_count=0)
    assert metrics["InteractionEvidenceCoverage"] == 0.0
    assert metrics["MapCommandExecutionSuccessRate"] == 0.0
    assert metrics["InteractionStateConvergenceRate"] == 0.0
    assert metrics["InteractionRecoveryRate"] == 0.0


# ── evaluate_evidence gate: require_interaction strict vs exempt ─────────

def _evidence_result(issued: int, coverage: float = 0.0):
    return {
        "run_id": "r1",
        "session_id": "s1",
        "evidence": [],
        "metrics": {
            "ToolChoiceAccuracy": 100.0,
            "MapSpecValidity": 0.0,
            "CursorResolutionRate": 0.0,
            "StepEfficiency": 100.0,
            "ErrorRecoveryRate": 100.0,
            "InteractionEvidenceCoverage": coverage,
            "MapCommandExecutionSuccessRate": 0.0,
            "InteractionStateConvergenceRate": 0.0,
            "InteractionRecoveryRate": 0.0,
        },
        "interaction": {
            "issued": issued,
            "acked": 0,
            "actions": [],
        },
    }


def test_require_interaction_false_exempts_unevaluated_dims():
    """issued == 0 → 交互维度豁免（not_applicable_exempt，score 诚实 0.0）。"""
    evaluator = HarnessEvaluator()
    gated = evaluator.evaluate_evidence(_evidence_result(issued=0))
    for name in INTERACTION_METRICS:
        chk = gated["checks"][name]
        assert chk["reason"] == "not_applicable_exempt"
        assert chk["passed"] is True
        assert chk["evaluated"] is False
        assert chk["score"] == 0.0


def test_require_interaction_true_is_strict():
    """require_interaction=True 且 issued == 0 → 未评估交互维度策略失败。"""
    evaluator = HarnessEvaluator()
    gated = evaluator.evaluate_evidence(_evidence_result(issued=0), require_interaction=True)
    for name in INTERACTION_METRICS:
        chk = gated["checks"][name]
        assert chk["reason"] == "not_evaluated_policy_fail"
        assert chk["passed"] is False
        assert gated["overall_passed"] is False


def test_issued_without_acks_fails_honestly():
    """issued > 0 但无 ACK → evaluated=True，Coverage=0 天然 fail（诚实，不豁免）。"""
    evaluator = HarnessEvaluator()
    gated = evaluator.evaluate_evidence(_evidence_result(issued=2))
    chk = gated["checks"]["InteractionEvidenceCoverage"]
    assert chk["reason"] == "evaluated"
    assert chk["evaluated"] is True
    assert chk["passed"] is False
    assert gated["overall_passed"] is False


def test_v2_gate_callers_byte_identical_behavior():
    """V2 调用方（无 interaction 段的旧 evidence result）行为不变：
    交互维度豁免，既有的 not_evaluated_policy_fail 判定保持不变。"""
    evaluator = HarnessEvaluator()
    legacy = {
        "run_id": "r",
        "session_id": "s",
        "evidence": [],
        "metrics": {
            "ToolChoiceAccuracy": 100.0,
            "MapSpecValidity": 0.0,
            "CursorResolutionRate": 0.0,
            "StepEfficiency": 100.0,
            "ErrorRecoveryRate": 100.0,
        },
        # 没有 interaction 段（旧版 evaluate_with_evidence 输出）
    }
    gated = evaluator.evaluate_evidence(legacy)
    assert gated["overall_passed"] is False  # MapSpecValidity 未评估 → fail（同 V2）
    assert gated["checks"]["MapSpecValidity"]["reason"] == "not_evaluated_policy_fail"
    for name in INTERACTION_METRICS:
        assert gated["checks"][name]["reason"] == "not_applicable_exempt"
        assert gated["checks"][name]["passed"] is True


def test_evaluate_session_v2_partial_metrics_unchanged():
    """同步 gate：只传 5 维 → 新增交互维度缺省不参与，行为与 V2 一致。"""
    evaluator = HarnessEvaluator()
    metrics = {
        "ToolChoiceAccuracy": 95.0,
        "MapSpecValidity": 100.0,
        "CursorResolutionRate": 100.0,
        "StepEfficiency": 85.0,
        "ErrorRecoveryRate": 90.0,
    }
    result = evaluator.evaluate_session(metrics)
    assert result["overall_passed"] is True
    assert "InteractionEvidenceCoverage" not in result["checks"]

    # 传全 9 维（含交互 0.0）→ 交互维度如实 fail。
    metrics9 = {**metrics, **{n: 0.0 for n in INTERACTION_METRICS}}
    assert evaluator.evaluate_session(metrics9)["overall_passed"] is False


# ── turn_id minting: step_result SSE + record threading ──────────────────

def _make_event_rpc(events):
    rpc = MagicMock()
    rpc.events = asyncio.Queue()
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    rpc.process_died = False

    async def _seed(cmd, data=None):
        for ev in events:
            await rpc.events.put(ev)

    rpc.request = AsyncMock(side_effect=_seed)
    return rpc


@pytest.fixture(autouse=True)
def _clean_bridge_state():
    bridge_mod._session_executed_sets.clear()
    bridge_mod._dispatch_result_cache.clear()
    yield
    bridge_mod._session_executed_sets.clear()
    bridge_mod._dispatch_result_cache.clear()


def test_inject_turn_id_only_step_result():
    sse = 'event: step_result\nid: 3\ndata: {"tool": "x", "result": {"ok": true}}\n\n'
    out = bridge_mod._inject_turn_id(sse, "turn-abc123")
    assert "event: step_result" in out
    assert "id: 3" in out          # DUP-1 编号保留
    assert '"turn_id": "turn-abc123"' in out
    # 非 step_result 事件原样不动
    token = 'event: token\ndata: {"content": "hi"}\n\n'
    assert bridge_mod._inject_turn_id(token, "turn-abc123") == token


@pytest.mark.asyncio
async def test_stream_prompt_mints_turn_id_and_threads_into_records(monkeypatch):
    """stream_prompt 每 turn 铸 turn_id：step_result SSE 负载携带；record_sse_event
    补 run/turn correlation（显式透传，绝不用全局 set_correlation）。"""
    harness = PiAgentHarness(session_id="turn-sess")
    monkeypatch.setattr(bridge_mod, "_harness", harness)

    # 预置一次 fly_to dispatch（模拟 HTTP 回调已调度并缓存结果）
    dispatch_result = ToolDispatchResult(
        status="ok",
        llm_payload="ok",
        slim_event={
            "success": True, "command": "fly_to", "action_id": "ma-turn-test",
            "params": {"center": [116.0, 39.0], "zoom": 12},
        },
        geojson_ref=None,
        raw_result={},
        error_msg=None,
        map_actions=[{"action_id": "ma-turn-test", "command": "fly_to",
                      "requested": {"center": [116.0, 39.0], "zoom": 12}}],
    )
    real_get = bridge_mod.get_cached_dispatch_result
    hits = {"tc-fly": dispatch_result}

    def fake_get(tool_call_id):
        return hits.pop(tool_call_id, None) or real_get(tool_call_id)

    monkeypatch.setattr(bridge_mod, "get_cached_dispatch_result", fake_get)

    rpc = _make_event_rpc([
        {"type": "tool_execution_start", "toolCallId": "tc-fly", "toolName": "webgis_view_set", "stepIndex": 0},
        {"type": "tool_execution_end", "toolCallId": "tc-fly", "toolName": "webgis_view_set", "result": {}},
        {"type": "agent_end", "message": {"content": ""}},
    ])
    br = bridge_mod.PiBridge(rpc=rpc)
    collected = [s async for s in br.stream_prompt("hi", session_id="turn-sess")]

    step_results = [s for s in collected if s.startswith("event: step_result")]
    assert len(step_results) == 1
    data_line = [line for line in step_results[0].split("\n") if line.startswith("data: ")][0]
    payload = json.loads(data_line[len("data: "):])
    assert payload["turn_id"].startswith("turn-")
    assert payload["result"]["action_id"] == "ma-turn-test"  # 铸的 action_id 随 SSE 到达

    # record_sse_event 补 run/turn correlation
    assert len(harness.sse_events) >= 2
    for ev in harness.sse_events:
        assert ev["turn_id"].startswith("turn-")
        assert ev["run_id"] == "turn-sess"
    # 同一 turn 内 turn_id 一致
    assert len({ev["turn_id"] for ev in harness.sse_events}) == 1
