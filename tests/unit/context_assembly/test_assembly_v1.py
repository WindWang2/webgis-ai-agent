"""F04 assembly end-to-end tests — synthetic providers, no external I/O.

Pins: render order + marker-last, dedupe inside the pipeline, tenant scope
denial, explicit caller-input wins exactly once, honest omission under a
known window, receipt integrity, and the byte-equivalence of the typed
path against the legacy path on the empty-session retirement boundary.
"""
import pytest

from app.services.chat.pi_turn_context import (
    bind_turn_prompt,
    legacy_bind_turn_prompt,
)
from app.services.context_assembly import assembly as asm
from app.services.context_assembly.assembly import (
    assemble_turn_context,
    fetch_shared_facts,
)
from app.services.context_assembly.contract import (
    ContextDomain,
    ContextItem,
    SharedTurnFacts,
    TurnContextRequest,
    bounded_item,
)
from app.services.context_assembly.providers import BaseProvider
from app.services.gis_context.scope import SensitivityClass, ScopeTier


class FakeProvider(BaseProvider):
    def __init__(self, provider_id, domain, items=None, *, fail=False):
        self.provider_id = provider_id
        self.domain = domain
        self._items = items or []
        self._fail = fail

    async def build(self, req, facts):
        if self._fail:
            raise RuntimeError("boom")
        return list(self._items)


def _req(**kw):
    base = dict(session_id="s1", turn_id="t1", message="hello",
                token="tok", org_id="org1", project_id="p1")
    base.update(kw)
    return TurnContextRequest(**base)


def _patch_pipeline(monkeypatch, providers, facts=None):
    monkeypatch.setattr(asm, "build_default_providers", lambda: providers)
    facts = facts or SharedTurnFacts(map_state={}, plan=None)
    async def _fake_fetch(req):
        return facts
    monkeypatch.setattr(asm, "fetch_shared_facts", _fake_fetch)

    class _NoCard:
        @staticmethod
        async def build(*a, **k):
            return []
    from app.services.context_assembly import providers as prov_mod
    monkeypatch.setattr(prov_mod.GisContextCardProvider, "build", _NoCard.build)


@pytest.mark.asyncio
async def test_render_order_and_marker_last(monkeypatch):
    verdict = bounded_item(item_id="v", provider_id="cv",
                           domain=ContextDomain.CARTOGRAPHY_VERDICT,
                           content="VERDICT-BODY", scope_id="s1")
    plan = bounded_item(item_id="pl", provider_id="sp",
                        domain=ContextDomain.SESSION_PLAN, content="PLAN-BODY",
                        scope_id="s1")
    env = bounded_item(item_id="ev", provider_id="env",
                       domain=ContextDomain.ENVIRONMENT, content="ENV-BODY",
                       scope_id="s1")
    providers = [
        FakeProvider("sp", ContextDomain.SESSION_PLAN, [plan]),
        FakeProvider("env", ContextDomain.ENVIRONMENT, [env]),
        FakeProvider("cv", ContextDomain.CARTOGRAPHY_VERDICT, [verdict]),
        FakeProvider("um", ContextDomain.USER_MESSAGE, []),
    ]
    _patch_pipeline(monkeypatch, providers)
    message, receipt = await assemble_turn_context(_req())

    i_msg = message.index("hello")
    i_verdict = message.index("VERDICT-BODY")
    i_plan = message.index("PLAN-BODY")
    i_env = message.index("ENV-BODY")
    i_marker = message.index("[WEBGIS_TURN_CONTEXT:tok]")
    assert i_msg < i_verdict < i_plan < i_env < i_marker
    assert message.rstrip().endswith(
        "(Internal routing context; do not quote or modify this marker.)"
    )
    assert receipt.mode == "typed" and receipt.digest
    assert receipt.actual_tokens > 0


@pytest.mark.asyncio
async def test_dedupe_drops_duplicate_content_with_receipt_line(monkeypatch):
    a = bounded_item(item_id="a", provider_id="p1",
                     domain=ContextDomain.CARTOGRAPHY_VERDICT, content="SAME",
                     scope_id="s1")
    b = bounded_item(item_id="b", provider_id="p2",
                     domain=ContextDomain.CARTOGRAPHY_VERDICT, content="SAME",
                     scope_id="s1")
    providers = [
        FakeProvider("p1", ContextDomain.CARTOGRAPHY_VERDICT, [a]),
        FakeProvider("p2", ContextDomain.CARTOGRAPHY_VERDICT, [b]),
    ]
    _patch_pipeline(monkeypatch, providers)
    message, receipt = await assemble_turn_context(_req())
    assert message.count("SAME") == 1
    assert any(ln.decision == "deduped" for ln in receipt.decision_lines)


@pytest.mark.asyncio
async def test_scope_denial_drops_foreign_project_item(monkeypatch):
    foreign = ContextItem(
        item_id="f", provider_id="fp",
        domain=ContextDomain.CARTOGRAPHY_MEMORY,
        content="FOREIGN-PROJECT-Facts",
        scope=ScopeTier.PROJECT, scope_id="p-other",
        project_id="p-other", sensitivity=SensitivityClass.PROJECT_SCOPED,
    )
    providers = [
        FakeProvider("fp", ContextDomain.CARTOGRAPHY_MEMORY, [foreign]),
    ]
    _patch_pipeline(monkeypatch, providers)
    message, receipt = await assemble_turn_context(_req(project_id="p-mine"))
    assert "FOREIGN-PROJECT-Facts" not in message
    assert any(ln.decision == "scope_denied" for ln in receipt.decision_lines)


@pytest.mark.asyncio
async def test_explicit_legacy_cartography_block_wins_once(monkeypatch):
    derived = bounded_item(item_id="d", provider_id="cv",
                           domain=ContextDomain.CARTOGRAPHY_VERDICT,
                           content="DERIVED-VERDICT")
    providers = [
        FakeProvider("cv", ContextDomain.CARTOGRAPHY_VERDICT, [derived]),
    ]
    _patch_pipeline(monkeypatch, providers)
    message, receipt = await assemble_turn_context(_req(
        legacy_cartography_block="EXPLICIT-VERDICT"
    ))
    assert message.count("EXPLICIT-VERDICT") == 1
    assert "DERIVED-VERDICT" not in message
    assert receipt.skipped_providers.get("cartography_derived") == \
        "caller_injected_block"


@pytest.mark.asyncio
async def test_provider_failure_is_fail_open_with_reason(monkeypatch):
    bad = FakeProvider("bad", ContextDomain.GIS_MEMORY, fail=True)
    good = bounded_item(item_id="g", provider_id="ok",
                        domain=ContextDomain.ENVIRONMENT, content="ENV-OK",
                        scope_id="s1")
    providers = [
        bad,
        FakeProvider("ok", ContextDomain.ENVIRONMENT, [good]),
    ]
    _patch_pipeline(monkeypatch, providers)
    message, receipt = await assemble_turn_context(_req())
    assert "ENV-OK" in message
    assert receipt.skipped_providers.get("bad") == "error:RuntimeError"


@pytest.mark.asyncio
async def test_known_window_enforce_omits_with_reason(monkeypatch):
    from app.core.config import settings

    floods = [
        bounded_item(item_id=f"big{i}", provider_id="p",
                     domain=ContextDomain.GIS_MEMORY,
                     content=f"z{i}-" + "z" * 2000,
                     scope_id="s1")
        for i in range(5)
    ]
    providers = [FakeProvider("p", ContextDomain.GIS_MEMORY, floods)]
    _patch_pipeline(monkeypatch, providers)
    monkeypatch.setattr(settings, "LLM_CONTEXT_WINDOW", 1000, raising=False)
    message, receipt = await assemble_turn_context(_req())
    assert "zzz" not in message  # honestly omitted, not random-truncated
    assert any(
        ln.decision == "omitted"
        and ("pool_cap" in ln.reason_code or "global_cap" in ln.reason_code)
        for ln in receipt.decision_lines
    )


@pytest.mark.asyncio
async def test_harness_state_block_rendered_when_turn_found(monkeypatch):
    from types import SimpleNamespace

    harness = SimpleNamespace(
        context_schema="hk.ctx.v1", turn_found=True, phase="executing",
        turn_status="running", completion="", steps_total=3, steps_open=1,
        steps_succeeded=2, steps_failed=0, tool_calls=4,
        open_capabilities=["map_product"], evidence_refs=["art_1"],
        mission_ref="m-1", recovery_pending=False, resumed_from_turn_id="",
        last_verdict="",
    )
    facts = SharedTurnFacts(map_state={}, plan=None, harness_context=harness)
    from app.services.context_assembly.providers import HarnessStateProvider

    _patch_pipeline(monkeypatch, [HarnessStateProvider()], facts=facts)
    message, receipt = await assemble_turn_context(_req())
    assert "[执行状态" in message
    assert "total=3" in message
    assert any(ln.domain == "harness_state" for ln in receipt.decision_lines)


@pytest.mark.asyncio
async def test_harness_state_block_killed_by_flag(monkeypatch):
    from types import SimpleNamespace

    harness = SimpleNamespace(
        context_schema="hk.ctx.v1", turn_found=True, phase="executing",
        turn_status="running", completion="", steps_total=1, steps_open=0,
        steps_succeeded=1, steps_failed=0, tool_calls=0,
        open_capabilities=[], evidence_refs=[], mission_ref="",
        recovery_pending=False, resumed_from_turn_id="", last_verdict="",
    )
    facts = SharedTurnFacts(map_state={}, plan=None, harness_context=harness)
    from app.services.context_assembly.providers import HarnessStateProvider

    _patch_pipeline(monkeypatch, [HarnessStateProvider()], facts=facts)
    monkeypatch.setenv("GIS_HARNESS_STATE_BLOCK", "0")
    message, _ = await assemble_turn_context(_req())
    assert "[执行状态" not in message


# ─── byte-equivalence on the retirement boundary (empty session) ─────────


@pytest.mark.asyncio
async def test_typed_path_byte_equal_to_legacy_empty_session():
    req = "hello", "tok", ""
    typed = await bind_turn_prompt(*req)
    legacy = await legacy_bind_turn_prompt(*req)
    assert typed == legacy


@pytest.mark.asyncio
async def test_typed_path_byte_equal_with_cartography_block():
    block = "[CARTOGRAPHY_VERDICT]\n{'status': 'ok'}"
    typed = await bind_turn_prompt("hello", "tok", "", cartography_block=block)
    legacy = await legacy_bind_turn_prompt("hello", "tok", "", block)
    assert typed == legacy


@pytest.mark.asyncio
async def test_typed_path_byte_equal_with_env_block():
    env = "[环境感知 — 当前地图实时状态，必读]\n- 视口中心(WGS84 经纬度): lng=120.0, lat=30.0, zoom=10.00"
    typed = await bind_turn_prompt("hello", "tok", "", env_block=env)
    legacy = await legacy_bind_turn_prompt("hello", "tok", "", env_block=env)
    assert typed == legacy


@pytest.mark.asyncio
async def test_kill_switch_uses_legacy_path(monkeypatch):
    monkeypatch.setenv("GIS_TYPED_CONTEXT_ASSEMBLY", "0")
    typed = await bind_turn_prompt("hello", "tok", "")
    legacy = await legacy_bind_turn_prompt("hello", "tok", "")
    assert typed == legacy


@pytest.mark.asyncio
async def test_typed_failure_falls_back_to_legacy(monkeypatch):
    async def _boom(req):
        raise RuntimeError("pipeline down")

    monkeypatch.setattr(asm, "fetch_shared_facts", _boom)
    out = await bind_turn_prompt("hello", "tok", "", env_block="")
    legacy = await legacy_bind_turn_prompt("hello", "tok", "", "")
    assert out == legacy


# ─── fetch discipline (N+1 guard) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_loads_plan_once_and_projects_harness_context(monkeypatch):
    calls = {"load": 0, "state": 0, "spec": 0}

    class FakePlan:
        session_id = "s9"
        envelope_id = "e9"
        turns = []
        steps = []
        progress = []
        gis_chapter = {}
        recovery = None
        replaced = False
        superseded = False
        user_goal = "goal"

    async def _fake_load(sid):
        calls["load"] += 1
        return FakePlan()

    async def _fake_ensure(sid):
        return None

    async def _fake_state(sid):
        calls["state"] += 1
        return {}

    class _FakeSpecStore:
        async def get_mapspec(self, sid):
            calls["spec"] += 1
            return None

    import app.services.session_plan as sp
    import app.services.session_data as sd
    import app.services.mapspec.store as ms

    monkeypatch.setattr(sp, "ensure_session_plan_slot", _fake_ensure)
    monkeypatch.setattr(sp, "load_session_plan", _fake_load)
    monkeypatch.setattr(sd.session_data_manager, "get_map_state", _fake_state)
    monkeypatch.setattr(ms, "mapspec_store_instance", _FakeSpecStore())

    req = TurnContextRequest(session_id="s9", turn_id="turn-9", message="m")
    facts = await fetch_shared_facts(req)
    assert calls["load"] == 1
    assert calls["state"] == 1
    assert calls["spec"] == 1
    assert facts.harness_context is not None
    assert facts.harness_context.turn_id == "turn-9"
