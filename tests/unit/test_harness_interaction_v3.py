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
from app.lib.harness.ref_resolver import make_session_store_resolver
from app.lib.harness.tool_call_event import ToolCallEvent
from app.services.tool_dispatch_service import (
    MAP_ACTION_ID_PREFIX,
    REQUESTED_SNAPSHOT_MAX_BYTES,
    ToolDispatchResult,
    ToolDispatchService,
)
from app.tools.harness_runner import run_benchmark_scenario

import app.agent_pi_bridge as bridge_mod


class _FakeStore:
    """Minimal async session store for ref-resolution tests (mirror test_pi_harness)."""

    def __init__(self, data: dict | None = None):
        self._data = data or {}

    async def get(self, session_id, ref):
        return self._data.get(ref)


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
async def test_dispatch_minting_mints_fresh_id_over_preexisting(fake_registry, clean_session):
    """F25: 已带 action_id 的 command dict（跨 turn 缓存/持久化对象）→ 仍铸新 id。

    复用陈旧 id 会让 ACK store 的 first-terminal-wins 把第二次真实执行的终态
    当作重复丢弃（重复副作用不可区分）。每次派发必须铸新 id；同 turn 去重由
    `_session_executed_sets` 保证，前端以 SSE 事件携带的 action_id 为准。
    """
    fake_registry.dispatch.return_value = {
        "success": True,
        "command": "fly_to",
        "params": {"center": [116.0, 39.0], "zoom": 12},
        "action_id": "ma-preexisting1234",  # 陈旧 id（来自之前某次执行）
    }
    svc = ToolDispatchService(registry=fake_registry)
    result = await svc.dispatch(_tc("webgis_view_set", {}), clean_session, set())
    assert result.map_actions[0]["action_id"] != "ma-preexisting1234"
    assert result.map_actions[0]["action_id"].startswith("ma-")
    assert result.raw_result["action_id"] == result.map_actions[0]["action_id"]


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


def test_store_direct_acks_not_verifiable_even_with_converged_hint():
    """Round-2 P1：存储侧直接 ACK（store_mounted / store_updated）只证明挂载/写入
    成功，不证明地图状态收敛 —— 即使前端附带 converged:true hint 也不可验证（否则
    前端可凭"store 挂上了"自我表扬收敛）。confirmed 必须为 True 才算（键存在不算）。"""
    store_mounted = _ev(
        command="store_load", requested={},
        actual={"store_mounted": True, "converged": True},
    )
    assert _is_verifiable_ack(store_mounted) is False
    assert _ack_converged(store_mounted) is False

    store_updated = _ev(
        command="store_write", requested={},
        actual={"store_updated": True, "converged": True},
    )
    assert _is_verifiable_ack(store_updated) is False
    assert _ack_converged(store_updated) is False

    # confirmed 键存在但为 False → 不算可验证（键存在 ≠ 收敛证据）。
    confirmed_false = _ev(command="add_layer", requested={}, actual={"confirmed": False})
    assert _is_verifiable_ack(confirmed_false) is False
    assert _ack_converged(confirmed_false) is False


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


@pytest.mark.asyncio
async def test_store_mounted_ack_counts_terminal_but_excluded_from_convergence():
    """Round-2 P1：前端 store 挂载直接 ACK（actual.store_mounted，FIX-A 形态）是
    终态 ACK —— InteractionEvidenceCoverage / MapCommandExecutionSuccessRate 照算；
    但不可验证（无相机状态、非 confirmed，即使带 converged:true hint）→ 排除出
    InteractionStateConvergenceRate 分母。"""
    harness = PiAgentHarness(session_id="s-store")
    for action_id, cmd in [
        ("ma-cam1", "fly_to"), ("ma-cam2", "fly_to"), ("ma-store", "store_load"),
    ]:
        harness.record_map_action_issued(
            session_id="s-store", tool_call_id="c1", turn_id="t1",
            action_id=action_id, command=cmd,
            requested=(
                {"center": [116.0, 39.0], "zoom": 12}
                if cmd == "fly_to" else {}
            ),
        )
    acks = [
        # 相机 1：succeeded + 收敛（可验证）
        {"action_id": "ma-cam1", "status": "succeeded",
         "actual": {"center": [116.0005, 39.0004], "zoom": 12.01}},
        # 相机 2：succeeded 但未收敛（可验证，分母在）
        {"action_id": "ma-cam2", "status": "succeeded",
         "actual": {"center": [116.05, 39.0], "zoom": 12}},
        # store：succeeded + 仅 store_mounted（附 converged hint 也无效）→ 终态但不可验证
        {"action_id": "ma-store", "status": "succeeded",
         "actual": {"store_mounted": True, "converged": True}},
    ]
    result = await harness.evaluate_with_evidence(
        expected_tools=[], ideal_step_count=0, map_action_reader=_make_reader(acks),
    )
    m = result["metrics"]
    # 终态 3/3；成功 3/3；收敛 1/2（store 不在分母 —— 若算入分母并采信 hint 会是 3/3 假 100）
    assert m["InteractionEvidenceCoverage"] == 100.0
    assert m["MapCommandExecutionSuccessRate"] == 100.0
    assert m["InteractionStateConvergenceRate"] == 50.0

    actions = {a["action_id"]: a for a in result["interaction"]["actions"]}
    assert actions["ma-store"]["status"] == "succeeded"
    assert actions["ma-store"]["verifiable"] is False
    assert actions["ma-store"]["converged"] is None


def test_evaluate_all_omits_interaction_metrics_without_issued_evidence():
    """无 issued 交互证据 → evaluate_all 省略 4 个交互键（而非报 0.0）。

    缺失证据绝不伪装成 100；同时省略比 0.0 更诚实 —— 同步 gate evaluate_session
    对缺失的交互维度直接跳过，无交互 run 的 V2 gate 不会被 0.0 恒定拉垮
    （等价 evaluate_evidence 的 not_applicable_exempt）。"""
    harness = PiAgentHarness(session_id="s6")
    metrics = harness.evaluate_all(expected_tools=[], ideal_step_count=0)
    assert all(name not in metrics for name in INTERACTION_METRICS)


# ── V2 sync gate regression（P1）：evaluate_all→evaluate_session 真实组合 ──


def test_sync_gate_run_benchmark_scenario_omits_interaction_dims():
    """harness_runner.run_benchmark_scenario（evaluate_all→evaluate_session 真实
    组合）在无交互场景下：metrics/checks 均不含 interaction 4 键，reported
    overall_passed 与只用 5 个 V2 维度评估一致（此前 interaction 恒为 0.0 会把
    gate 恒定拉垮，V2 调用方无交互 run 每跑必 fail）。"""
    res = run_benchmark_scenario(
        scenario_id="scenario_no_interaction",
        expected_tools=["webgis_layer_upsert"],
        ideal_step_count=1,
        simulated_tool_calls=[{
            "id": "c1", "name": "webgis_layer_upsert",
            "arguments": {"layer": {"id": "L"}},
        }],
        simulated_tool_results=[{
            "id": "c1", "name": "webgis_layer_upsert",
            "result": {"success": True, "is_compiled": True},
        }],
    )
    assert all(name not in res["metrics"] for name in INTERACTION_METRICS)
    assert all(name not in res["evaluation"]["checks"] for name in INTERACTION_METRICS)
    v2_only = {k: v for k, v in res["metrics"].items() if k not in INTERACTION_METRICS}
    assert res["evaluation"]["overall_passed"] == HarnessEvaluator().evaluate_session(v2_only)["overall_passed"]


@pytest.mark.asyncio
async def test_sync_gate_full_pass_with_real_composition():
    """5 个 V2 维度全部通过的无交互场景：真实 evaluate_with_evidence（内部
    evaluate_all）→ evaluate_session 组合必须 overall_passed=True —— 回归前
    interaction 0.0 会把一个本该通过的 run 拉成 False。"""
    store = _FakeStore({"ref:geojson-ok": {"type": "FeatureCollection", "features": []}})
    harness = PiAgentHarness(
        session_id="sync-gate-pass",
        ref_resolver=make_session_store_resolver(store),
    )
    harness.record_tool_call("c1", "webgis_layer_upsert", {"src": "ref:geojson-ok"})
    harness.record_tool_result(
        "c1", "webgis_layer_upsert", {"success": True, "is_compiled": True}
    )
    ev_result = await harness.evaluate_with_evidence(
        expected_tools=["webgis_layer_upsert"], ideal_step_count=1,
    )
    # 无交互 → interaction 段存在但 metrics 不含 interaction 键
    assert all(name not in ev_result["metrics"] for name in INTERACTION_METRICS)
    metrics = harness.evaluate_all(
        expected_tools=["webgis_layer_upsert"], ideal_step_count=1,
    )
    result = HarnessEvaluator().evaluate_session(metrics)
    assert all(name not in result["checks"] for name in INTERACTION_METRICS)
    assert result["overall_passed"] is True


# ── V3: harness 会话隔离（issued 侧按 session_id 过滤） ───────────────────


@pytest.mark.asyncio
async def test_map_action_evidence_session_isolation():
    """共享 harness 跨 session 累积 issued 时，评估只匹配当前 session：
    map_action_reader 是 session-scoped（只返回 A 的 ack），issued 侧必须按
    session_id 过滤 —— B 的 issued（哪怕 action_id 与 A 重名）绝不参与 A 的
    coverage/action 列表。"""
    harness = PiAgentHarness(session_id="sessA")
    # session A：2 个动作，其中 ma-a1 有 ACK
    harness.record_map_action_issued(
        session_id="sessA", tool_call_id="a1", turn_id="t1",
        action_id="ma-a1", command="fly_to",
        requested={"center": [116.0, 39.0], "zoom": 12},
    )
    harness.record_map_action_issued(
        session_id="sessA", tool_call_id="a2", turn_id="t1",
        action_id="ma-a2", command="fly_to",
        requested={"center": [116.0, 39.0], "zoom": 12},
    )
    # session B：同 action_id 的 issued（跨会话重名/重放场景）—— 绝不能算进 A
    harness.record_map_action_issued(
        session_id="sessB", tool_call_id="b1", turn_id="t9",
        action_id="ma-a1", command="fly_to",
        requested={"center": [1.0, 2.0], "zoom": 3},
    )

    acks = [{
        "action_id": "ma-a1", "status": "succeeded",
        "actual": {"center": [116.0001, 39.0001], "zoom": 12.01},
    }]
    result = await harness.evaluate_with_evidence(
        expected_tools=[], ideal_step_count=0, map_action_reader=_make_reader(acks),
    )
    inter = result["interaction"]
    assert inter["issued"] == 2
    assert {a["action_id"] for a in inter["actions"]} == {"ma-a1", "ma-a2"}
    assert all(a["session_id"] == "sessA" for a in inter["actions"])
    assert inter["acked"] == 1
    # coverage 只按 A 的 issued 计算：1 终态 / 2 issued
    assert result["metrics"]["InteractionEvidenceCoverage"] == 50.0
    # B 的动作完全不出现
    assert all(a["tool_call_id"] != "b1" for a in inter["actions"])


def test_record_map_action_issued_skips_empty_session():
    """session_id 为空 → 不记录（无法按 session 隔离的 issued 会污染评估）。"""
    harness = PiAgentHarness(session_id="s")
    entry = harness.record_map_action_issued(
        session_id="", tool_call_id="c1", action_id="ma-x", command="fly_to",
    )
    assert entry == {}
    assert harness.map_actions_issued == []


@pytest.mark.asyncio
async def test_two_sessions_isolated_on_all_read_surfaces():
    """Round-2 P2：单例 harness 跨 session 累积时，所有读取面按 session 隔离 ——
    evaluate_with_evidence 的 evidence 只含 A 的工具调用；五个 compute_* 指标全部
    只按 A 的证据计算（B 的无证据 mutation / 缺失 ref / 错误绝不混入）；record_event
    不再改写 self.session_id（评估目标保持 A）。"""
    store = _FakeStore({"ref:geojson-ok": {"type": "FeatureCollection", "features": []}})
    harness = PiAgentHarness(
        session_id="sessA",
        ref_resolver=make_session_store_resolver(store),
    )

    # session A：mutation（is_compiled 证据）+ ref + 错误→恢复
    harness.record_event(ToolCallEvent(
        tool_call_id="a1", tool_name="webgis_layer_upsert",
        arguments={"src": "ref:geojson-ok"},
        result={"success": True, "is_compiled": True},
        session_id="sessA",
    ))
    harness.record_event(ToolCallEvent(
        tool_call_id="a2", tool_name="st_dbscan",
        arguments={}, is_error=True, error_msg="a-fail",
        result={}, session_id="sessA",
    ))
    # session B 交错（同一共享 harness）：无证据 mutation + 缺失 ref + 错误
    harness.record_event(ToolCallEvent(
        tool_call_id="b1", tool_name="webgis_layer_upsert",
        arguments={"src": "ref:geojson-missing"},
        result={"success": True},
        session_id="sessB",
    ))
    harness.record_event(ToolCallEvent(
        tool_call_id="b2", tool_name="st_dbscan",
        arguments={}, is_error=True, error_msg="b-fail",
        result={}, session_id="sessB",
    ))
    # A 恢复成功 —— B 的错误插在中间不得清除 A 的错误状态，也不得计入 A
    harness.record_event(ToolCallEvent(
        tool_call_id="a3", tool_name="st_dbscan",
        arguments={"crs": "EPSG:4326"},
        result={"success": True},
        session_id="sessA",
    ))

    # record_event 不得把评估目标改写成最后一个事件的 session
    assert harness.session_id == "sessA"
    # 记录按真实 session 归属（tool_results 同样带 session 戳）
    assert {t["session_id"] for t in harness.tool_calls} == {"sessA", "sessB"}
    assert all(t["session_id"] in ("sessA", "sessB") for t in harness.tool_results)
    # 两个 session 各有一次错误：异常条目按真实 session 归属
    assert {e["session_id"] for e in harness.exceptions} == {"sessA", "sessB"}

    ev_result = await harness.evaluate_with_evidence(
        expected_tools=["webgis_layer_upsert", "st_dbscan"], ideal_step_count=1,
    )
    # evidence 只含 A 的工具调用
    assert [e["tool_call_id"] for e in ev_result["evidence"]] == ["a1", "a2", "a3"]
    assert all(e["session_id"] == "sessA" for e in ev_result["evidence"])
    # A 的 mutation 带 is_compiled 证据 → SEMANTIC_VALID（B 的无证据 mutation 不算）
    assert ev_result["evidence"][0]["mapspec_validity"]["tier"] == "SEMANTIC_VALID"

    # 五个 V2 指标全部只按 A 的证据计算
    assert harness.compute_tool_choice_accuracy(["webgis_layer_upsert", "st_dbscan"]) == 100.0
    assert harness.compute_mapspec_validity() == 100.0   # 1 valid / 1 mutation（A）
    assert harness.compute_cursor_resolution_rate() == 100.0  # A 的 ref 解析成功（B 的缺失 ref 不计）
    assert harness.compute_step_efficiency(1) == pytest.approx(33.33, abs=0.01)  # 1 ideal / 3 A 步（B 的 2 步不计）
    assert harness.compute_error_recovery_rate() == 100.0  # A: 1 异常 1 恢复（B 的错误不计）


def test_record_sse_event_stamps_session_id():
    """Round-2 P2：record_sse_event 补 session_id 归属戳（事件自带则保留，否则
    回退评估目标），sse_events 可被 session 过滤/审计。"""
    harness = PiAgentHarness(session_id="sse-sess")
    harness.record_sse_event({"type": "token", "content": "hi"})
    harness.record_sse_event({"type": "step_result", "session_id": "other-sess"})
    assert harness.sse_events[0]["session_id"] == "sse-sess"
    assert harness.sse_events[1]["session_id"] == "other-sess"


# ── V3: 收敛防伪 —— ACK 声称的 requested 不能覆盖后端快照 ─────────────────


@pytest.mark.asyncio
async def test_client_ack_cannot_spoof_requested_to_flip_convergence():
    """客户端 ACK 声称 requested==actual（与后端铸造的 issued 快照不同）时，
    收敛判定必须仍用 issued 快照 —— 后端重算否决假自证，convergence 如实 0。"""
    harness = PiAgentHarness(session_id="s-spoof")
    harness.record_map_action_issued(
        session_id="s-spoof", tool_call_id="c1", turn_id="t1",
        action_id="ma-spoof", command="fly_to",
        # 后端铸造（ToolDispatchService mint）时截取的 requested 快照
        requested={"center": [116.0, 39.0], "zoom": 12},
    )
    acks = [{
        "action_id": "ma-spoof",
        "status": "succeeded",
        # 客户端试图用 requested==actual 的假快照"自证收敛"
        "requested": {"center": [116.05, 39.0], "zoom": 12},
        "actual": {"center": [116.05, 39.0], "zoom": 12, "converged": True},
    }]
    result = await harness.evaluate_with_evidence(
        expected_tools=[], ideal_step_count=0, map_action_reader=_make_reader(acks),
    )
    action = result["interaction"]["actions"][0]
    assert action["status"] == "succeeded"
    assert action["requested"]["center"] == [116.0, 39.0]  # issued 快照优先
    assert action["verifiable"] is True
    assert action["converged"] is False
    assert result["metrics"]["InteractionStateConvergenceRate"] == 0.0


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

    def fake_get(tool_call_id, session_id=None):
        return hits.pop(tool_call_id, None) or real_get(tool_call_id, session_id)

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
        # run_id is now a first-class run handle (run-<hex>), not the session id
        # (the prior "run_id == session_id" reuse is retired — see RuntimeContext).
        assert ev["run_id"].startswith("run-")
    # 同一 turn 内 turn_id 一致
    assert len({ev["turn_id"] for ev in harness.sse_events}) == 1
    assert len({ev["run_id"] for ev in harness.sse_events}) == 1


@pytest.mark.asyncio
async def test_view_set_summary_does_not_claim_camera_moved(clean_session):
    """Round-2 P2：webgis_view_set 的 LLM 可见 summary 不得断言相机已移动 ——
    工具层只完成了 mapspec.view 写入 + 下发 fly_to 指令（success 只指写入本身），
    实时相机是否落定由前端 ACK 证据闭环判定（后端 _is_verifiable_ack 重算收敛）。"""
    from app.tools.registry import ToolRegistry
    from app.tools.cartography_tools import register_mapspec_cartography_tools

    reg = ToolRegistry()
    register_mapspec_cartography_tools(reg)
    await reg.dispatch("webgis_project_init", {}, session_id=clean_session)
    res = await reg.dispatch(
        "webgis_view_set",
        {"center": [116.4, 39.9], "zoom": 12.0},
        session_id=clean_session,
    )
    assert res["success"] is True            # 写入成功本身是真的
    assert res["command"] == "fly_to"        # 指令仍在（前端据此执行）
    assert res["summary"] == "视图指令已下发，等待前端执行"
