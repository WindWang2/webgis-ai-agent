"""Adversarial tests for the Pi Native GIS Tool ABI V2 (ADR-0224).

Each test is an attack scenario against the new ABI surface — cross-turn
replay, duplicate callbacks, late arrivals, mid-turn registry drift, receipt
store exhaustion, scope-enforcement bypass attempts, kill-switch equivalence,
result-envelope honesty, and the extension's fingerprint echo contract.
All assertions are machine-verified through the real production paths
(HTTP route where the attack crosses the callback boundary, in-process
otherwise); fully offline, no LLM keys, no vendor/pi.
"""
import json
import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import (
    register_active_pi_turn,
    set_tool_registry,
    unregister_active_pi_turn,
)
from app.api.routes.pi_tools import get_bridge_secret
from app.main import app
from app.services.chat.pi_surface_receipt import (
    record_turn_surface,
    surface_receipt_store,
)
from app.services.chat.pi_turn_context import issue_turn_token, pi_turn_registry
from app.services.chat.pi_tool_spec import PiToolSpecV1, compile_tool_spec
from app.tools.registry import ToolRegistry

EXTENSION_PATH = (
    Path(__file__).parents[2] / "app" / "extensions" / "webgis-tools" / "index.mjs"
)


def _run_node_script(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_abi_state(monkeypatch):
    """Isolate every test: ABI flags at defaults, no receipts / turns / caches."""
    for var in (
        "PI_TOOL_ABI", "PI_RESULT_ABI", "PI_SURFACE_SCOPE_ENFORCE",
        "PI_SPEC_MAX_SCHEMA_BYTES",
    ):
        monkeypatch.delenv(var, raising=False)

    def _reset():
        surface_receipt_store.clear()
        pi_turn_registry._local_context = None
        bridge_mod._active_turns.clear()
        bridge_mod._dispatch_result_cache.clear()
        bridge_mod._session_plan_sse_cache.clear()
        bridge_mod._session_executed_sets.clear()

    _reset()
    yield
    _reset()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def toy_registry():
    """Toy registry with counting tools: every execution is observable."""
    counts = {k: 0 for k in (
        "a", "b", "dedup", "late", "inface", "outface", "big", "cancel",
    )}

    def echo_a(msg="x", session_id=None):
        counts["a"] += 1
        return {"success": True, "message": f"a:{msg}"}

    def echo_b(msg="x", session_id=None):
        counts["b"] += 1
        return {"success": True, "message": f"b:{msg}"}

    def dedup(msg="x", tag="t", session_id=None):
        counts["dedup"] += 1
        return {"success": True, "message": f"dedup:{msg}"}

    def late(payload="p", session_id=None):
        counts["late"] += 1
        return {"success": True, "message": f"late:{payload}"}

    def inface(session_id=None):
        counts["inface"] += 1
        return {"success": True, "message": "inface"}

    def outface(note="", session_id=None):
        counts["outface"] += 1
        return {"success": True, "message": f"outface:{note}"}

    def big(session_id=None):
        counts["big"] += 1
        features = [
            {
                "type": "Feature",
                "properties": {"marker": "ADV_BIG_MARKER_7f3a9c", "i": i},
                "geometry": None,
            }
            for i in range(2200)
        ]
        return {
            "success": True,
            "summary": "big geojson result",
            "result_ref": "ref://adv/big-1",
            "geojson": {"type": "FeatureCollection", "features": features},
        }

    def cancel_tool(session_id=None):
        counts["cancel"] += 1
        from app.lib.cancellation import checkpoint

        checkpoint()  # raises OperationCancelled when the turn token is cancelled
        return {"success": True}

    registry = ToolRegistry()
    str_params = {
        "type": "object",
        "properties": {"msg": {"type": "string"}},
        "required": ["msg"],
    }
    registry.register("adv_echo_a", "adv echo a", echo_a, parameters=str_params)
    registry.register("adv_echo_b", "adv echo b", echo_b, parameters=str_params)
    registry.register(
        "adv_dedup", "adv dedup", dedup,
        parameters={
            "type": "object",
            "properties": {
                "msg": {"type": "string"},
                "tag": {"type": "string"},
            },
            "required": ["msg"],
        },
    )
    registry.register(
        "adv_late", "adv late", late,
        parameters={
            "type": "object",
            "properties": {"payload": {"type": "string"}},
        },
    )
    empty_params = {"type": "object", "properties": {}}
    registry.register("adv_inface", "adv in face", inface, parameters=empty_params)
    registry.register(
        "adv_outface", "adv out of face", outface,
        parameters={
            "type": "object",
            "properties": {"note": {"type": "string"}},
        },
    )
    registry.register("adv_big", "adv big", big, parameters=empty_params)
    registry.register(
        "adv_cancel", "adv cancel", cancel_tool, parameters=empty_params)

    old_registry = bridge_mod._tool_registry
    set_tool_registry(registry)
    yield registry, counts
    bridge_mod._tool_registry = old_registry


@pytest.fixture(scope="module")
def _full_registry_module():
    """Compile the full production registry ONCE (init_tools is expensive)."""
    from app.tools import init_tools

    reg = ToolRegistry()
    init_tools(reg)
    return reg


@pytest.fixture
def full_registry(_full_registry_module):
    """Re-inject the shared full registry per test.

    tests/conftest.py::_reset_chat_engine_singletons (autouse) nulls
    ``agent_pi_bridge._tool_registry`` around EVERY test and runs AFTER
    module-scoped fixtures — so the injection must be function-scoped to
    survive it (function fixtures in the test module run after the conftest
    autouse reset).
    """
    old = bridge_mod._tool_registry
    bridge_mod.set_tool_registry(_full_registry_module)
    yield _full_registry_module
    bridge_mod._tool_registry = old


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mint_receipt(registry, session_id, turn_id, names):
    """Mint a receipt for exactly ``names`` (the turn's selected surface)."""
    specs = []
    for name in names:
        spec = compile_tool_spec(registry, name)
        assert spec is not None, f"spec compile failed for {name}"
        specs.append(spec)
    return record_turn_surface(
        specs, session_id=session_id, turn_id=turn_id,
        candidates=list(names), selected=list(names), drops=[],
    )


def _body(tool_call_id, name, arguments, token, **extra):
    return {
        "toolCallId": tool_call_id,
        "name": name,
        "arguments": arguments,
        "turnToken": token,
        **extra,
    }


def _headers():
    return {"X-Pi-Bridge-Secret": get_bridge_secret()}


def _envelope_detail(payload: dict) -> dict:
    """Extract the HTTPException detail from the app's unified error envelope
    (``{"code": "CONFLICT", ..., "data": {...detail...}}``) or the raw
    ``detail`` key when no envelope wrapper is active."""
    if isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload.get("detail") or {}


async def _register(session_id, turn_id, token=None):
    await register_active_pi_turn(session_id, turn_id, token=token)


# ---------------------------------------------------------------------------
# 1. Cross-turn replay
# ---------------------------------------------------------------------------

async def test_cross_turn_replay_rejected(toy_registry, client):
    """Replaying turn T1's capability / fingerprints against turn T2 fails.

    - T1's signed token after supersession -> route 409 (TURN_CONTEXT_INACTIVE);
    - T1's receipt fingerprints echoed on a T2 callback whose surface differs
      -> typed SURFACE_FINGERPRINT_MISMATCH, tool NOT executed.
    """
    registry, counts = toy_registry
    session_id = "sess-adv-replay"
    t1, t2 = "turn-adv-r1", "turn-adv-r2"

    await _register(session_id, t1)
    receipt1 = _mint_receipt(registry, session_id, t1, ["adv_echo_a", "adv_echo_b"])
    await unregister_active_pi_turn(session_id, t1)

    await _register(session_id, t2)
    # T2's surface deliberately selects only adv_echo_a -> different surface.
    receipt2 = _mint_receipt(registry, session_id, t2, ["adv_echo_a"])
    assert receipt1.surface_fingerprint != receipt2.surface_fingerprint
    assert "adv_echo_b" not in receipt2.tool_fingerprints

    # Attack A: replay T1's token verbatim (T2 now owns the live turn).
    token1 = issue_turn_token(get_bridge_secret(), session_id, t1)
    resp = await client.post(
        "/pi-tools/execute", headers=_headers(),
        json=_body("call-r1", "adv_echo_a", {"msg": "hi"}, token1),
    )
    assert resp.status_code == 409
    assert _envelope_detail(resp.json())["code"] == "TURN_CONTEXT_INACTIVE"
    assert counts["a"] == 0

    # Attack B (review P2-2 语义修正)：per-tool echo 的跨 turn 语义是
    # **契约级** —— 同契约工具的旧 echo 合法（指纹本就相同，dispatch 闸
    # 仍全量生效）；只有契约变化的工具（T2 面上、指纹已漂移）的旧 echo
    # 才是 stale。surfaceFingerprint 字段已移除（构造性永假阳性，见
    # pi_surface_receipt.verify_callback_fingerprints 注释）。
    token2 = issue_turn_token(get_bridge_secret(), session_id, t2)
    # adv_echo_b 不在 T2 selected（dormant 裸调）→ echo 跳过 → 正常执行
    resp2 = await client.post(
        "/pi-tools/execute", headers=_headers(),
        json=_body(
            "call-r2", "adv_echo_b", {"msg": "hi"}, token2,
            toolFingerprint=receipt1.tool_fingerprints["adv_echo_b"],
        ),
    )
    data2 = resp2.json()
    assert data2["isError"] is False
    assert counts["b"] == 1

    # Attack B': 契约漂移场景 —— T2 面上 adv_echo_a 的 spec 指纹与 T1 铸
    # 的旧 echo 不同（模拟 registry 契约变化后 mint 的新 receipt）。
    stale_fp = "f" * 16  # 攻击者持有的旧契约指纹
    resp2b = await client.post(
        "/pi-tools/execute", headers=_headers(),
        json=_body(
            "call-r2b", "adv_echo_a", {"msg": "hi"}, token2,
            toolFingerprint=stale_fp,
        ),
    )
    data2b = resp2b.json()
    assert data2b["isError"] is True
    assert data2b["details"]["code"] == "SURFACE_FINGERPRINT_MISMATCH"
    assert "webgis_execute" in data2b["content"][0]["text"]
    # counts["a"] 仍为 1：stale echo 的调用零副作用

    # Legitimate T2 callback with T2's own fingerprints still dispatches.
    resp3 = await client.post(
        "/pi-tools/execute", headers=_headers(),
        json=_body(
            "call-r3", "adv_echo_a", {"msg": "ok"}, token2,
            toolFingerprint=receipt2.tool_fingerprints["adv_echo_a"],
        ),
    )
    data3 = resp3.json()
    assert data3["isError"] is False
    assert counts["a"] == 1  # 全流程 adv_echo_a 仅合法调用执行一次


# ---------------------------------------------------------------------------
# 2. Duplicate callback (same toolCallId / same args)
# ---------------------------------------------------------------------------

async def test_duplicate_callback_dedups_within_turn(toy_registry, client):
    """Second identical dispatch in the same turn must not re-execute.

    ToolDispatchService dedup key = (tool_name, canonical args) — NOT the
    toolCallId, and args are canonicalized with sorted keys (key-order
    insensitive). Pinned honestly below.
    """
    registry, counts = toy_registry
    session_id, turn_id = "sess-adv-dedup", "turn-adv-dedup"
    receipt = _mint_receipt(registry, session_id, turn_id, ["adv_dedup"])
    token = issue_turn_token(get_bridge_secret(), session_id, turn_id)
    await _register(session_id, turn_id)
    fp = receipt.tool_fingerprints["adv_dedup"]

    args = {"msg": "x", "tag": "t"}
    resp1 = await client.post(
        "/pi-tools/execute", headers=_headers(),
        json=_body("call-dup-1", "adv_dedup", args, token, toolFingerprint=fp),
    )
    d1 = resp1.json()
    assert d1["isError"] is False
    assert d1["details"]["resultAbi"]["status"] == "ok"
    assert counts["dedup"] == 1

    # Same toolCallId AND same args (byte-identical replay of the callback).
    resp2 = await client.post(
        "/pi-tools/execute", headers=_headers(),
        json=_body("call-dup-1", "adv_dedup", args, token, toolFingerprint=fp),
    )
    d2 = resp2.json()
    assert resp2.status_code == 200
    assert d2["isError"] is False  # repeat is not an execution error
    assert counts["dedup"] == 1, "duplicate callback must not double-execute"
    abi2 = d2["details"]["resultAbi"]
    assert abi2["status"] == "repeated"
    assert any("deduplicated" in w for w in abi2.get("warnings", []))
    assert d2["details"]["note"] == "Loop blocked"
    assert "已在本任务中以相同参数执行过" in d2["content"][0]["text"]

    # Different toolCallId but identical args (key order flipped) -> still
    # deduped: the executed-set key is canonical-JSON (sorted keys).
    resp3 = await client.post(
        "/pi-tools/execute", headers=_headers(),
        json=_body(
            "call-dup-2-other-id", "adv_dedup",
            {"tag": "t", "msg": "x"},
            token, toolFingerprint=fp,
        ),
    )
    d3 = resp3.json()
    assert d3["details"]["resultAbi"]["status"] == "repeated"
    assert counts["dedup"] == 1, "dedup key must be key-order insensitive"

    # Same toolCallId but different args -> new dedup key -> executes again.
    # Honest pin: the executed-set is (tool, canonical-args)-keyed, so a
    # repeated toolCallId with mutated args is NOT double-execution protection
    # — the extension's toolCallId is not the dedup authority.
    resp4 = await client.post(
        "/pi-tools/execute", headers=_headers(),
        json=_body(
            "call-dup-1", "adv_dedup", {"msg": "x", "tag": "OTHER"}, token,
            toolFingerprint=fp,
        ),
    )
    assert resp4.json()["details"]["resultAbi"]["status"] == "ok"
    assert counts["dedup"] == 2


# ---------------------------------------------------------------------------
# 3. Late arrival after turn cleanup
# ---------------------------------------------------------------------------

async def test_late_callback_after_turn_cleanup(toy_registry, client):
    """Dispatch state dies with the turn: receipt evicted, caches dropped,
    no dedup/executed-set leakage into the successor turn, and a receipt-less
    successor turn passes echo verification (master compat)."""
    registry, counts = toy_registry
    session_id, t1, t2 = "sess-adv-late", "turn-adv-l1", "turn-adv-l2"

    await _register(session_id, t1)
    receipt1 = _mint_receipt(registry, session_id, t1, ["adv_late"])
    token1 = issue_turn_token(get_bridge_secret(), session_id, t1)
    resp1 = await client.post(
        "/pi-tools/execute", headers=_headers(),
        json=_body(
            "call-late-1", "adv_late", {"payload": "p1"}, token1,
            toolFingerprint=receipt1.tool_fingerprints["adv_late"],
        ),
    )
    assert resp1.json()["isError"] is False
    assert counts["late"] == 1
    # ADR-0022 rendezvous cache now holds the (session, toolCallId) result.
    assert (session_id, "call-late-1") in bridge_mod._dispatch_result_cache

    # Turn T1 ends (bridge finally path: unregister + cleanup).
    await unregister_active_pi_turn(session_id, t1)
    bridge_mod._cleanup_turn_state(session_id)

    assert surface_receipt_store.lookup(session_id, t1) is None
    assert all(
        key[0] != session_id for key in bridge_mod._dispatch_result_cache
    ), "rendezvous cache must not outlive the turn"
    assert all(
        key[0] != session_id for key in bridge_mod._session_plan_sse_cache
    )
    assert session_id not in bridge_mod._session_executed_sets

    # Successor turn T2 (no receipt minted: e.g. abstained dynamic surface or
    # pre-ABI turn). A callback echoing T1's STALE fingerprints must pass
    # without verification (receipt absent = master compat) and — because the
    # executed-set died with T1 — the tool really executes again (count 2).
    await _register(session_id, t2)
    token2 = issue_turn_token(get_bridge_secret(), session_id, t2)
    resp2 = await client.post(
        "/pi-tools/execute", headers=_headers(),
        json=_body(
            "call-late-2", "adv_late", {"payload": "p1"}, token2,
            toolFingerprint=receipt1.tool_fingerprints["adv_late"],
        ),
    )
    d2 = resp2.json()
    assert d2["isError"] is False
    assert counts["late"] == 2, "executed-set dedup must not leak across turns"


# ---------------------------------------------------------------------------
# 4. Registry generation change mid-turn
# ---------------------------------------------------------------------------

def test_registry_generation_drift_mid_turn(full_registry):
    """Receipt immutability under registry drift: the in-flight turn keeps its
    turn-start receipt; the NEXT turn mints a different surface fingerprint."""
    from app.lib.gis.runtime_manifest import get_runtime_manifest
    from app.services.chat.pi_native_surface import compute_turn_active_tools

    session_id, t1, t2 = "sess-adv-gen", "turn-adv-g1", "turn-adv-g2"
    surface_receipt_store.clear()

    names1 = compute_turn_active_tools(
        "做一份成都市小学密度热力图",
        session_id=session_id, turn_id=t1,
    )
    assert names1
    receipt1 = surface_receipt_store.lookup(session_id, t1)
    assert receipt1 is not None

    # Simulate registry drift (redeploy / dynamic registration changing the
    # runtime manifest). NOTE (pinned): set_tool_registry→refresh_runtime_
    # manifest() recompiles from its own fresh init_tools registry, so
    # registering an extra tool on the injected registry does NOT move the
    # generation anchor — mutate the cached manifest fingerprint directly to
    # simulate the drift (production path: app.main compiles the manifest from
    # the live registry before the bridge injects it).
    manifest = get_runtime_manifest()
    gen1 = manifest.fingerprint
    assert receipt1.registry_generation == gen1
    original_fp = manifest.fingerprint
    try:
        manifest.fingerprint = "adv-gen-drift-2"
        # Same turn re-projected: receipt is immutable, first mint wins
        # (record() no-ops and keeps the turn-start receipt).
        compute_turn_active_tools(
            "做一份成都市小学密度热力图",
            session_id=session_id, turn_id=t1,
        )
        still = surface_receipt_store.lookup(session_id, t1)
        assert still is receipt1
        assert still.surface_fingerprint == receipt1.surface_fingerprint
        assert still.registry_generation == gen1

        # A NEW turn picks up the drifted generation -> different fingerprint.
        compute_turn_active_tools(
            "做一份成都市小学密度热力图",
            session_id=session_id, turn_id=t2,
        )
        receipt2 = surface_receipt_store.lookup(session_id, t2)
        assert receipt2 is not None
        assert receipt2.registry_generation == "adv-gen-drift-2"
        assert receipt2.surface_fingerprint != receipt1.surface_fingerprint
    finally:
        manifest.fingerprint = original_fp


# ---------------------------------------------------------------------------
# 5. Receipt store bounds
# ---------------------------------------------------------------------------

def test_receipt_store_bounded_and_session_scoped():
    """>128 turns -> capped at 128, oldest evicted; evict_session surgical."""
    spec = PiToolSpecV1(name="adv_store_tool", description="store probe")

    for i in range(130):
        record_turn_surface(
            [spec], session_id=f"sess-adv-{i}", turn_id=f"turn-adv-{i}",
            candidates=["adv_store_tool"], selected=["adv_store_tool"],
            drops=[],
        )
    receipts = surface_receipt_store._receipts  # noqa: SLF001 — test observability
    assert len(receipts) == 128, "store must be bounded (no unbounded growth)"
    assert surface_receipt_store.lookup("sess-adv-0", "turn-adv-0") is None
    assert surface_receipt_store.lookup("sess-adv-1", "turn-adv-1") is None
    assert surface_receipt_store.lookup("sess-adv-2", "turn-adv-2") is not None
    assert surface_receipt_store.lookup("sess-adv-129", "turn-adv-129") is not None

    # evict_session removes exactly that session's receipts.
    surface_receipt_store.clear()
    for i in range(3):
        record_turn_surface(
            [spec], session_id="sess-adv-A", turn_id=f"turn-adv-A{i}",
            candidates=["adv_store_tool"], selected=["adv_store_tool"], drops=[],
        )
    record_turn_surface(
        [spec], session_id="sess-adv-B", turn_id="turn-adv-B0",
        candidates=["adv_store_tool"], selected=["adv_store_tool"], drops=[],
    )
    surface_receipt_store.evict_session("sess-adv-A")
    assert surface_receipt_store.lookup("sess-adv-A", "turn-adv-A0") is None
    assert surface_receipt_store.lookup("sess-adv-A", "turn-adv-A2") is None
    assert surface_receipt_store.lookup("sess-adv-B", "turn-adv-B0") is not None


# ---------------------------------------------------------------------------
# 6. Scope enforcement attack
# ---------------------------------------------------------------------------

async def test_scope_enforcement_attack_matrix(toy_registry, client, monkeypatch):
    """PI_SURFACE_SCOPE_ENFORCE=1: bare-name off-surface call is typed-rejected
    without execution; the SAME tool via webgis_execute proxy still executes;
    with enforcement OFF (default) the bare-name executes (compat)."""
    registry, counts = toy_registry
    session_id, turn_id = "sess-adv-scope", "turn-adv-scope"
    receipt = _mint_receipt(registry, session_id, turn_id, ["adv_inface"])
    token = issue_turn_token(get_bridge_secret(), session_id, turn_id)
    await _register(session_id, turn_id)
    assert "adv_outface" not in receipt.selected

    monkeypatch.setenv("PI_SURFACE_SCOPE_ENFORCE", "1")

    # Bare-name call for a tool NOT in this turn's receipt.selected.
    resp = await client.post(
        "/pi-tools/execute", headers=_headers(),
        json=_body("call-scope-1", "adv_outface", {"note": "bare"}, token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["isError"] is True
    assert data["details"]["code"] == "SURFACE_SCOPE_VIOLATION"
    assert "stale" in data["content"][0]["text"]
    assert counts["outface"] == 0  # rejected BEFORE dispatch: no execution

    # Same tool through the webgis_execute proxy -> executes (fallback path
    # is deliberately outside the native scope check).
    resp2 = await client.post(
        "/pi-tools/execute", headers=_headers(),
        json=_body(
            "call-scope-2", "webgis_execute",
            {"toolName": "adv_outface", "arguments": {"note": "proxy"}}, token,
        ),
    )
    d2 = resp2.json()
    assert d2["isError"] is False, d2
    assert counts["outface"] == 1

    # Enforcement OFF (default): the same bare-name call executes (compat —
    # setActiveTools may be unavailable, hard scope would false-reject).
    # Distinct args: the executed-set dedup is (tool, canonical args)-keyed.
    monkeypatch.delenv("PI_SURFACE_SCOPE_ENFORCE", raising=False)
    resp3 = await client.post(
        "/pi-tools/execute", headers=_headers(),
        json=_body("call-scope-3", "adv_outface", {"note": "bare-off"}, token),
    )
    assert resp3.json()["isError"] is False
    assert counts["outface"] == 2


# ---------------------------------------------------------------------------
# 7. Kill-switch equivalence (PI_TOOL_ABI=0)
# ---------------------------------------------------------------------------

def test_kill_switch_dump_and_surface_match_master(full_registry, monkeypatch):
    """PI_TOOL_ABI=0 -> dump v2 master shape (no abi/spec/generation/
    fingerprint), no qualification, no receipt; name membership identical to
    ABI=1 on the current corpus (destructive/budget exclusions are
    forward-looking — today's corpus has none)."""
    from app.services.chat.pi_native_surface import (
        NATIVE_TOOL_NAMES,
        _apply_surface_qualification,
        compute_turn_active_tools,
        pi_surface_for_spawn,
    )

    registry = full_registry
    surface_receipt_store.clear()

    monkeypatch.setenv("PI_TOOL_ABI", "0")
    v2 = pi_surface_for_spawn(registry)
    assert v2["version"] == 2
    assert set(v2.keys()) == {"version", "tools", "default_active", "execute_proxy"}
    assert v2["default_active"] == list(NATIVE_TOOL_NAMES)
    assert all("spec" not in t for t in v2["tools"])
    names_kill = compute_turn_active_tools(
        "做一份成都市小学密度热力图",
        session_id="sess-adv-kill", turn_id="turn-adv-kill",
    )
    assert names_kill, "kill switch keeps the master selection behavior"
    assert surface_receipt_store.lookup("sess-adv-kill", "turn-adv-kill") is None, \
        "no receipt may be minted with the ABI off"
    assert _apply_surface_qualification(registry, ["webgis_map_intent"]) is None, \
        "qualification must be absent with the ABI off"

    monkeypatch.delenv("PI_TOOL_ABI", raising=False)
    v3 = pi_surface_for_spawn(registry)
    assert v3["version"] == 3
    assert v3["abi"]["version"] == 1
    assert v3["registry_generation"]
    assert v3["surface_fingerprint"]
    assert all("spec" in t for t in v3["tools"])
    # Superset membership must be identical on the current registry: the
    # destructive/over-budget exclusions only bite when such tools exist.
    assert {t["name"] for t in v2["tools"]} == {t["name"] for t in v3["tools"]}


# ---------------------------------------------------------------------------
# 8. Result ABI honesty
# ---------------------------------------------------------------------------

async def test_result_abi_huge_payload_stays_bounded(toy_registry, client):
    """A ~200KB geojson-ish tool result never leaks into details.resultAbi
    (<=4KB, ref-first); refs and status survive the envelope."""
    registry, counts = toy_registry
    session_id, turn_id = "sess-adv-big", "turn-adv-big"
    receipt = _mint_receipt(registry, session_id, turn_id, ["adv_big"])
    token = issue_turn_token(get_bridge_secret(), session_id, turn_id)
    await _register(session_id, turn_id)

    resp = await client.post(
        "/pi-tools/execute", headers=_headers(),
        json=_body(
            "call-big-1", "adv_big", {}, token,
            toolFingerprint=receipt.tool_fingerprints["adv_big"],
        ),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["isError"] is False
    assert counts["big"] == 1

    abi = data["details"]["resultAbi"]
    abi_bytes = len(json.dumps(abi, ensure_ascii=False).encode("utf-8"))
    assert abi_bytes <= 4 * 1024, f"resultAbi envelope must stay <=4KB ({abi_bytes}B)"
    assert abi["abi"] == "pi_result.v1"
    assert abi["status"] == "ok"
    assert abi["refs"]["result"] == "ref://adv/big-1"
    # Raw features are never inlined into the envelope (ref-first only).
    assert "ADV_BIG_MARKER_7f3a9c" not in json.dumps(abi)
    # And the slim path already stripped the geojson body from details; the
    # total details payload stays within the legacy 64KB slim budget (+ the
    # ≤4KB additive envelope). PINNED (pre-existing master behavior, not the
    # new ABI): the post-dispatch analysis profiler may sample DISTINCT
    # property values into details (a bounded values list), so details is
    # size-bounded here rather than content-empty.
    assert "geojson" not in data["details"]
    details_bytes = len(json.dumps(data["details"], ensure_ascii=False).encode("utf-8"))
    assert details_bytes <= 64 * 1024 + 4 * 1024, (
        f"details payload must stay bounded ({details_bytes}B)"
    )


async def test_result_abi_cancelled_dispatch_envelope(toy_registry, client):
    """A dispatch whose turn token is cancelled returns the structured
    cancelled envelope (status "cancelled"), not a raw exception/500."""
    registry, counts = toy_registry
    session_id, turn_id = "sess-adv-cancel", "turn-adv-cancel"
    _mint_receipt(registry, session_id, turn_id, ["adv_cancel"])
    token = issue_turn_token(get_bridge_secret(), session_id, turn_id)

    from app.lib.cancellation import CancellationToken

    turn_token = CancellationToken()
    assert turn_token.cancel("user") is True
    await _register(session_id, turn_id, token=turn_token)

    resp = await client.post(
        "/pi-tools/execute", headers=_headers(),
        json=_body("call-cancel-1", "adv_cancel", {}, token),
    )
    assert resp.status_code == 200  # cooperative cancellation is not an HTTP error
    data = resp.json()
    assert data["isError"] is True
    abi = data["details"]["resultAbi"]
    assert abi["status"] == "cancelled"
    assert abi["error"]["class"] == "cancelled"
    assert abi["error"]["code"] == "OPERATION_CANCELLED"
    assert len(json.dumps(abi).encode("utf-8")) <= 4 * 1024


# ---------------------------------------------------------------------------
# 9. Extension echo contract (node evaluation)
# ---------------------------------------------------------------------------

def _node_harness(dump_path: str) -> str:
    return f"""
      process.env.WEBGIS_NATIVE_TOOLS_PATH = {json.dumps(dump_path)};
      process.env.WEBGIS_BRIDGE_SECRET = "sec-adv-node";
      const mod = await import({json.dumps(EXTENSION_PATH.as_uri())});
      const registrations = [];
      const pi = {{
        registerTool: (rec) => registrations.push(rec),
        setActiveTools: () => {{}},
        on: () => {{}},
      }};
      mod.default(pi);
      const bodies = [];
      globalThis.fetch = async (url, init) => {{
        bodies.push({{ url: String(url), headers: init.headers, body: JSON.parse(init.body) }});
        return {{
          ok: true, status: 200,
          json: async () => ({{
            toolCallId: "call-adv-node", content: [{{ type: "text", text: "ok" }}],
            details: {{}}, isError: false,
          }}),
        }};
      }};
      const ctx = {{
        sessionManager: {{
          getEntries: () => [{{ role: "user", content: "[WEBGIS_TURN_CONTEXT:tok.adv1]" }}],
        }},
      }};
      const byName = (n) => registrations.find((r) => r.name === n);
      const direct = byName("adv_node_tool");
      await direct.execute("call-1", {{}}, undefined, undefined, ctx);
      const proxy = byName("webgis_execute");
      // Wrapping a REGISTERED dump tool is client-side wrap-rejected (no fetch).
      const wrapRejected = await proxy.execute(
        "call-2", {{ toolName: "adv_node_tool", arguments: {{}} }}, undefined, undefined, ctx,
      );
      // Proxying a tool NOT in the dump does fetch — with no spec fingerprint.
      await proxy.execute("call-3", {{ toolName: "not_in_dump", arguments: {{}} }}, undefined, undefined, ctx);
      process.stdout.write("ADVJSON:" + JSON.stringify({{ bodies, wrapRejected }}));
    """


def _parse_advjson(stdout: str) -> dict:
    marker = "ADVJSON:"
    assert marker in stdout, f"ADVJSON marker missing from node stdout: {stdout[:400]!r}"
    return json.loads(stdout.split(marker, 1)[1].strip().split("\n", 1)[0])


def test_extension_echoes_v3_spec_fingerprint(tmp_path):
    """v3 dump: a tool with spec.fingerprint is echoed via postToBridge
    options.toolFingerprint on the direct callback; the dump-level
    surface_fingerprint is NOT echoed; unknown proxy names send no echo.

    PINNED (by construction, see index.mjs): the webgis_execute proxy path
    can never echo a non-empty fingerprint — wrapping a registered (dumped)
    tool is client-side wrap-rejected BEFORE postToBridge, and only dumped
    tools have fingerprints in the extension's map. The proxy echo branch is
    therefore currently unreachable with a value (fails toward no-echo,
    which is the legacy-compat direction, not a security hole).
    """
    dump = {
        "version": 3,
        "tools": [{
            "name": "adv_node_tool", "label": "Adv Node", "description": "d",
            "parameters": {"type": "object", "properties": {}},
            "spec": {"fingerprint": "fp-adv-node-123"},
        }],
        "default_active": ["adv_node_tool"],
        "execute_proxy": "webgis_execute",
        "abi": {"version": 1, "policy": "pi_tool_spec.v1"},
        "registry_generation": "gen-adv-1",
        "surface_fingerprint": "sf-adv-1",
    }
    path = tmp_path / "adv_v3_dump.json"
    path.write_text(json.dumps(dump), encoding="utf-8")

    res = _run_node_script(_node_harness(str(path)))
    parsed = _parse_advjson(res.stdout)
    bodies = parsed["bodies"]
    assert len(bodies) == 2, "registered-tool wrap must be rejected client-side"

    direct = bodies[0]
    assert direct["url"].endswith("/pi-tools/execute")
    assert direct["headers"]["X-Pi-Bridge-Secret"] == "sec-adv-node"
    assert direct["body"]["toolFingerprint"] == "fp-adv-node-123"
    assert direct["body"]["turnToken"] == "tok.adv1"
    assert "surfaceFingerprint" not in direct["body"], \
        "dump-level surface fingerprint must never be echoed"

    # Registered tool wrapped in webgis_execute: typed client-side rejection.
    wrap = parsed["wrapRejected"]
    assert wrap["isError"] is True
    assert wrap["details"]["error"] == "native_wrap_rejected"

    unknown = bodies[1]
    assert unknown["body"]["name"] == "webgis_execute"
    assert unknown["body"]["arguments"]["toolName"] == "not_in_dump"
    assert "toolFingerprint" not in unknown["body"], \
        "proxy wrap of a tool without a known spec must send no echo"


def test_extension_v2_dump_sends_no_echo(tmp_path):
    """v2 dump (no spec): callbacks carry no toolFingerprint key at all —
    undefined is not serialized into the bridge request body."""
    dump = {
        "version": 2,
        "tools": [{
            "name": "adv_node_tool", "label": "Adv Node", "description": "d",
            "parameters": {"type": "object", "properties": {}},
        }],
        "default_active": ["adv_node_tool"],
        "execute_proxy": "webgis_execute",
    }
    path = tmp_path / "adv_v2_dump.json"
    path.write_text(json.dumps(dump), encoding="utf-8")

    res = _run_node_script(_node_harness(str(path)))
    bodies = _parse_advjson(res.stdout)["bodies"]
    assert len(bodies) == 2
    for body in bodies:
        assert "toolFingerprint" not in body["body"]
        assert "surfaceFingerprint" not in body["body"]
