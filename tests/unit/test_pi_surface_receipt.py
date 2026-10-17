"""Turn-scoped surface qualification + SurfaceReceipt 测试（Goal 04, ADR-0224）。

钉住：
- 资格断言纪律：unknown = 不排除；只有阳性缺席事实才逐出（封闭 codes）；
- receipt 确定性/不可变性（同 turn 首次为准）；
- verify_callback_fingerprints / enforce_callback_scope 的对照语义；
- compute_turn_active_tools 生产接线：session/turn 在场 → receipt 铸造 +
  marker 仍含 native 前门；turn 清理 evict。
"""

import pytest

from app.services.chat.pi_surface_receipt import (
    DROP_CREDENTIAL_ABSENT,
    DROP_DESTRUCTIVE,
    DROP_NO_DURABLE_WORKER,
    DROP_OFFLINE_NETWORK,
    DROP_TIER3,
    SURFACE_QUALIFICATION_POLICY,
    SurfaceDrop,
    enforce_callback_scope,
    qualify_surface,
    record_turn_surface,
    surface_receipt_store,
    verify_callback_fingerprints,
)
from app.services.chat.pi_tool_spec import PiToolSpecV1


@pytest.fixture(autouse=True)
def clean_store():
    surface_receipt_store.clear()
    yield
    surface_receipt_store.clear()


@pytest.fixture(autouse=True)
def abi_on(monkeypatch):
    monkeypatch.delenv("PI_TOOL_ABI", raising=False)
    monkeypatch.delenv("PI_SURFACE_SCOPE_ENFORCE", raising=False)


def _spec(name: str, **overrides) -> PiToolSpecV1:
    return PiToolSpecV1(name=name, description=name, **overrides)


# ---------------------------------------------------------------------------
# qualify_surface：断言纪律
# ---------------------------------------------------------------------------

def test_no_situation_drops_nothing():
    specs = [_spec("a"), _spec("b")]
    selected, drops = qualify_surface(specs, situation={})
    assert selected == ("a", "b")
    assert drops == ()


def test_unknown_credential_presence_does_not_drop():
    """无来源 = unknown = 不排除（dispatch 期 bind 仍是权威）。"""
    hot = _spec("hot", requires_credentials=("amap",))
    selected, drops = qualify_surface([hot], situation={"credentials_present": {}})
    assert selected == ("hot",)
    assert drops == ()


def test_positive_credential_absence_drops():
    hot = _spec("hot", requires_credentials=("amap", "tianditu"))
    selected, drops = qualify_surface(
        [hot], situation={"credentials_present": {"amap": True, "tianditu": False}},
    )
    assert selected == ()
    assert drops[0].code == DROP_CREDENTIAL_ABSENT
    assert "tianditu" in drops[0].detail


def test_offline_drops_network_tools_only():
    net = _spec("net", network=True)
    local = _spec("local", network=False)
    unknown_net = _spec("mystery")  # network=None → unknown → 保留
    selected, drops = qualify_surface(
        [net, local, unknown_net], situation={"offline": True},
    )
    assert selected == ("local", "mystery")
    assert [d.tool for d in drops] == ["net"]
    assert drops[0].code == DROP_OFFLINE_NETWORK


def test_durable_worker_absence_drops_durable_policy_tools():
    durable = _spec("d", execution_policy="durable")
    thread = _spec("t", execution_policy="thread")
    selected, drops = qualify_surface(
        [durable, thread], situation={"runtime_availability": {"durable_worker": False}},
    )
    assert selected == ("t",)
    assert drops[0].code == DROP_NO_DURABLE_WORKER
    # unknown worker 状态 → 不排除
    selected2, _ = qualify_surface(
        [durable], situation={"runtime_availability": {}},
    )
    assert selected2 == ("d",)


def test_tier3_and_destructive_drops():
    tier3 = _spec("t3", tier=3)
    sec3 = _spec("s3", tier=1, effective_security_tier=3)
    destructive = _spec("boom", side_effect="destructive")
    ok = _spec("fine")
    selected, drops = qualify_surface([tier3, sec3, destructive, ok], situation={})
    assert selected == ("fine",)
    codes = {d.tool: d.code for d in drops}
    assert codes["t3"] == DROP_TIER3
    assert codes["s3"] == DROP_TIER3
    assert codes["boom"] == DROP_DESTRUCTIVE


def test_selection_order_is_preserved():
    specs = [_spec(f"t{i}") for i in range(5)]
    selected, _ = qualify_surface(specs, situation={})
    assert selected == tuple(f"t{i}" for i in range(5))


# ---------------------------------------------------------------------------
# Receipt store：确定性 / 不可变 / 生命周期
# ---------------------------------------------------------------------------

def test_receipt_is_deterministic_and_sorted():
    specs = [_spec("b"), _spec("a")]
    drops = [SurfaceDrop("z", "code_z"), SurfaceDrop("y", "code_y")]
    r1 = record_turn_surface(specs, session_id="s", turn_id="t1",
                             candidates=["b", "a"], selected=["b", "a"], drops=drops)
    r2 = record_turn_surface(specs, session_id="s2", turn_id="t2",
                             candidates=["b", "a"], selected=["b", "a"], drops=drops)
    assert r1.surface_fingerprint == r2.surface_fingerprint
    assert r1.dropped == tuple(sorted(drops, key=lambda d: (d.tool, d.code)))
    assert r1.policy_version == SURFACE_QUALIFICATION_POLICY
    assert r1.tool_fingerprints.keys() == {"a", "b"}


def test_receipt_immutable_within_turn():
    specs = [_spec("a")]
    first = record_turn_surface(specs, session_id="s", turn_id="t",
                                candidates=["a"], selected=["a"], drops=[])
    changed = [_spec("a"), _spec("zzz")]
    record_turn_surface(changed, session_id="s", turn_id="t",
                        candidates=["a", "zzz"], selected=["a", "zzz"], drops=[])
    stored = surface_receipt_store.lookup("s", "t")
    assert stored.selected == first.selected
    assert stored.surface_fingerprint == first.surface_fingerprint


def test_store_eviction_by_turn_and_session():
    specs = [_spec("a")]
    record_turn_surface(specs, session_id="s1", turn_id="t1",
                        candidates=["a"], selected=["a"], drops=[])
    record_turn_surface(specs, session_id="s1", turn_id="t2",
                        candidates=["a"], selected=["a"], drops=[])
    record_turn_surface(specs, session_id="s2", turn_id="t9",
                        candidates=["a"], selected=["a"], drops=[])
    surface_receipt_store.evict_turn("s1", "t1")
    assert surface_receipt_store.lookup("s1", "t1") is None
    assert surface_receipt_store.lookup("s1", "t2") is not None
    surface_receipt_store.evict_session("s1")
    assert surface_receipt_store.lookup("s1", "t2") is None
    assert surface_receipt_store.lookup("s2", "t9") is not None


def test_store_bounded_capacity():
    specs = [_spec("a")]
    for i in range(200):
        record_turn_surface(specs, session_id=f"s{i}", turn_id=f"t{i}",
                            candidates=["a"], selected=["a"], drops=[])
    total = sum(
        1 for key in surface_receipt_store._receipts  # noqa: SLF001 — 测试观测
    )
    assert total <= 128


def test_receipt_without_ids_is_not_stored():
    specs = [_spec("a")]
    receipt = record_turn_surface(specs, session_id="", turn_id="",
                                  candidates=["a"], selected=["a"], drops=[])
    assert receipt.session_id == ""
    assert surface_receipt_store.lookup("", "") is None


# ---------------------------------------------------------------------------
# 回调校验语义
# ---------------------------------------------------------------------------

def _mint(session_id="s", turn_id="t", names=("a", "b")):
    specs = [_spec(n) for n in names]
    receipt = record_turn_surface(specs, session_id=session_id, turn_id=turn_id,
                                  candidates=list(names), selected=list(names), drops=[])
    return receipt, {n: s.fingerprint() for n, s in zip(names, specs)}


def test_echo_match_passes():
    receipt, fps = _mint()
    assert verify_callback_fingerprints(
        session_id="s", turn_id="t", resolved_tool="a",
        tool_fingerprint=fps["a"],
    ) is None


def test_echo_mismatch_rejected():
    receipt, fps = _mint()
    assert verify_callback_fingerprints(
        session_id="s", turn_id="t", resolved_tool="a",
        tool_fingerprint=fps["a"][:-1] + "0",
    ) == "SURFACE_FINGERPRINT_MISMATCH"


def test_echo_for_tool_outside_selected_is_skipped():
    """dormant 裸调（不在 selected）无指纹可验 → 跳过（不误拒）。"""
    receipt, fps = _mint(names=("a",))
    out_of_surface = _spec("zzz")
    assert verify_callback_fingerprints(
        session_id="s", turn_id="t", resolved_tool="zzz",
        tool_fingerprint=out_of_surface.fingerprint(),
    ) is None


def test_absent_receipt_passes_compat():
    assert verify_callback_fingerprints(
        session_id="nope", turn_id="nope", resolved_tool="a",
        tool_fingerprint="whatever",
    ) is None


def test_no_echo_passes():
    _mint()
    assert verify_callback_fingerprints(
        session_id="s", turn_id="t", resolved_tool="a",
    ) is None


def test_scope_enforcement_default_off_then_opt_in(monkeypatch):
    _mint(names=("a",))
    # 默认 OFF：面外工具不拒
    assert enforce_callback_scope(session_id="s", turn_id="t", resolved_tool="zzz") is None
    monkeypatch.setenv("PI_SURFACE_SCOPE_ENFORCE", "1")
    assert enforce_callback_scope(
        session_id="s", turn_id="t", resolved_tool="zzz",
    ) == "SURFACE_SCOPE_VIOLATION"
    assert enforce_callback_scope(
        session_id="s", turn_id="t", resolved_tool="a",
    ) is None
    # receipt 缺席 → 放行（兼容）
    assert enforce_callback_scope(
        session_id="nope", turn_id="nope", resolved_tool="zzz",
    ) is None


# ---------------------------------------------------------------------------
# 生产接线：compute_turn_active_tools → receipt
# ---------------------------------------------------------------------------

@pytest.fixture
def bridge_registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    import app.agent_pi_bridge as bridge_mod

    old = bridge_mod._tool_registry
    bridge_mod.set_tool_registry(reg)
    yield reg
    bridge_mod._tool_registry = old


def test_compute_turn_active_tools_mints_receipt(bridge_registry):
    from app.services.chat.pi_native_surface import (
        NATIVE_TOOL_NAMES,
        compute_turn_active_tools,
    )

    surface_receipt_store.clear()
    disclosure: dict = {}
    names = compute_turn_active_tools(
        "做一份成都市小学密度热力图",
        disclosure=disclosure,
        session_id="sess-abi",
        turn_id="turn-abi1",
    )
    assert set(NATIVE_TOOL_NAMES) <= set(names)
    receipt = surface_receipt_store.lookup("sess-abi", "turn-abi1")
    assert receipt is not None, "session/turn 在场时必须铸造 receipt"
    assert set(NATIVE_TOOL_NAMES) <= set(receipt.selected)
    assert receipt.selected == tuple(names)
    assert receipt.tool_fingerprints
    # fingerprint 对照真相成立：selected 工具的 echo 全部可验
    for name, fp in receipt.tool_fingerprints.items():
        assert verify_callback_fingerprints(
            session_id="sess-abi", turn_id="turn-abi1",
            resolved_tool=name, tool_fingerprint=fp,
        ) is None
    # 换 turn 重投影 → 新 receipt（同 session 各 turn 独立）
    compute_turn_active_tools(
        "换个问题：北京市行政区边界",
        session_id="sess-abi", turn_id="turn-abi2",
    )
    assert surface_receipt_store.lookup("sess-abi", "turn-abi2") is not None


def test_compute_turn_active_tools_without_turn_ids_skips_receipt(bridge_registry):
    from app.services.chat.pi_native_surface import compute_turn_active_tools

    surface_receipt_store.clear()
    compute_turn_active_tools("做一份成都市小学密度热力图")
    # 无 session/turn → 无 receipt（旧调用路径兼容）
    assert not surface_receipt_store._receipts  # noqa: SLF001 — 测试观测


def test_abi_kill_switch_skips_qualification_and_receipt(
        bridge_registry, monkeypatch):
    monkeypatch.setenv("PI_TOOL_ABI", "0")
    from app.services.chat.pi_native_surface import compute_turn_active_tools

    surface_receipt_store.clear()
    names = compute_turn_active_tools(
        "做一份成都市小学密度热力图",
        session_id="s-kill", turn_id="t-kill",
    )
    assert names  # 仍返回选择器产物（master 行为）
    assert surface_receipt_store.lookup("s-kill", "t-kill") is None


def test_turn_cleanup_evicts_receipts(bridge_registry):
    from app.agent_pi_bridge import _cleanup_turn_state
    from app.services.chat.pi_native_surface import compute_turn_active_tools

    surface_receipt_store.clear()
    compute_turn_active_tools(
        "做一份成都市小学密度热力图",
        session_id="sess-cleanup", turn_id="turn-cleanup",
    )
    assert surface_receipt_store.lookup("sess-cleanup", "turn-cleanup") is not None
    _cleanup_turn_state("sess-cleanup")
    assert surface_receipt_store.lookup("sess-cleanup", "turn-cleanup") is None
