"""resolver 健康因子 + certification seed + plan 凭证指纹接线测试(H05)。"""
from __future__ import annotations

import pytest

from app.services.capability_runtime.health import (
    COOL_DOWN_S,
    ProviderFailureClass,
    ProviderHealthRegistry,
    set_provider_health_registry,
)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, s: float) -> None:
        self.now += s


# ── resolver 健康因子 ────────────────────────────────────────────────────


def _find_multi_provider_capability(min_tools: int = 2) -> str:
    from app.lib.gis.capability_registry import get_capability_registry
    from app.services.gis_harness.capability_graph import get_capability_graph

    g = get_capability_graph()
    for cap_id in sorted(get_capability_registry().all_ids):
        try:
            tools = g.tools_for_capability(cap_id)
        except Exception:
            continue
        if len(tools) >= min_tools:
            return cap_id
    pytest.skip("no multi-provider capability in current registry")


class TestResolutionHealthFactor:
    def test_open_provider_scores_worse_and_is_disclosed(self, monkeypatch):
        cap_id = _find_multi_provider_capability()
        from app.services.gis_harness.capability_resolution import (
            _provider_candidates,
        )
        from app.services.gis_harness.capability_graph import (
            get_capability_graph,
        )
        from app.services.gis_harness.qualification_v8 import (
            QualificationContext,
        )

        g = get_capability_graph()
        base_ranked, _ = _provider_candidates(
            cap_id, QualificationContext(), g)
        assert len(base_ranked) >= 2
        target = base_ranked[0].id
        base_score = base_ranked[0].score

        clock = _FakeClock()
        reg = ProviderHealthRegistry(clock=clock)
        set_provider_health_registry(reg)
        for _ in range(3):
            reg.record_failure(f"tool:{target}", ProviderFailureClass.TIMEOUT)

        tripped_ranked, _ = _provider_candidates(
            cap_id, QualificationContext(), g)
        tripped = next(c for c in tripped_ranked if c.id == target)
        assert tripped.factors.get("provider_health_penalty") == 0.75
        assert tripped.score == pytest.approx(base_score + 0.75)
        # 排序实际变化:原先第一,如今不再是第一(存在未被罚分的同分候选时)
        if any(
            abs(c.score - base_score) < 0.75
            for c in base_ranked[1:]
        ):
            assert tripped_ranked[0].id != target

    def test_kill_switch_removes_factor(self, monkeypatch):
        cap_id = _find_multi_provider_capability()
        monkeypatch.setenv("GIS_PROVIDER_HEALTH", "0")
        from app.services.gis_harness.capability_resolution import (
            _provider_candidates,
        )
        from app.services.gis_harness.capability_graph import (
            get_capability_graph,
        )
        from app.services.gis_harness.qualification_v8 import (
            QualificationContext,
        )

        ranked, _ = _provider_candidates(
            cap_id, QualificationContext(), get_capability_graph())
        assert all(
            "provider_health_penalty" not in c.factors for c in ranked)


# ── certification seed(lifespan 注入口)─────────────────────────────────


class TestCertificationSeed:
    def test_seed_writes_singleton_with_evidence_fingerprint(self):
        import app.lib.gis.execution_catalog as ec
        from app.lib.gis.execution_catalog import (
            get_execution_catalog,
            seed_execution_catalog,
        )

        index = {"acme": {
            "extension_id": "acme.pack", "version": "1.0.0",
            "state": "valid", "certified": True, "signed": True,
        }}
        catalog = seed_execution_catalog(index)
        assert catalog.certification_evidence_fingerprint
        assert get_execution_catalog() is catalog  # 单例已播种
        # 确定性:同 index 同指纹
        again = seed_execution_catalog(index)
        assert again.certification_evidence_fingerprint == \
            catalog.certification_evidence_fingerprint

    def test_certification_flip_changes_evidence_fingerprint(self):
        """认证升级/过期(状态翻转)→ 证据指纹翻转(旧快照可感知)。"""
        from app.lib.gis.execution_catalog import seed_execution_catalog

        valid = seed_execution_catalog({"acme": {
            "state": "valid", "certified": True, "signed": True}})
        stale = seed_execution_catalog({"acme": {
            "state": "stale", "certified": False, "signed": True}})
        assert valid.certification_evidence_fingerprint != \
            stale.certification_evidence_fingerprint

    def test_seed_empty_index_is_core_projection(self):
        from app.lib.gis.execution_catalog import seed_execution_catalog

        catalog = seed_execution_catalog({})
        assert catalog.certification_evidence_fingerprint == ""
        assert catalog.entries  # core 投影仍然完整

    def test_projection_refresher_reseeds_and_tolerates_absent_host(
            self, monkeypatch):
        from unittest.mock import MagicMock

        import app.extensions_platform.host as host_mod
        import app.lib.gis.execution_catalog as ec
        from app.extensions_platform.refresh import make_projection_refresher

        hook = make_projection_refresher(MagicMock())
        # host 缺席(未启用扩展)→ 不播种、不抛
        monkeypatch.setattr(host_mod, "get_extension_host", lambda: None)
        hook("ext", "deactivate")
        assert ec._cached_catalog is None

        # host 在场 → 重播种;认证索引翻转进入单例
        class _FakeHost:
            def extension_ids(self):
                return ["acme.pack"]

            def get_record(self, ext_id):
                class _M:
                    namespace = "acme"
                    version = "1.0.0"

                class _R:
                    manifest = _M()

                return _R()

        monkeypatch.setattr(host_mod, "get_extension_host", lambda: _FakeHost())
        import app.extensions_platform.pack_catalog as pc_mod

        monkeypatch.setattr(
            pc_mod, "certification_status_for",
            lambda record: {"state": "valid", "certified": True,
                            "signed": False})
        hook("acme.pack", "activate")
        assert ec._cached_catalog is not None
        assert ec._cached_catalog.certification_evidence_fingerprint

        hook("acme.pack", "deactivate")

    def test_registry_refresh_function_smoke(self):
        from app.lib.gis.execution_catalog import (
            get_execution_catalog,
            refresh_execution_catalog,
        )

        cat = refresh_execution_catalog()
        assert get_execution_catalog() is cat


# ── capability_runtime_status 生产接线形态(review P2-2)─────────────────


class TestRuntimeStatusToolWiring:
    @pytest.mark.asyncio
    async def test_production_tool_passes_registry(self, monkeypatch):
        """生产工具必须把 app registry 传进快照 —— 否则
        credential_missing/policy_denied 在生产不可达(review P2-2)。

        直接钉接线契约:capture build_capability_runtime_snapshot 的 kwargs。
        """
        import app.services.capability_runtime.snapshot as snap_mod
        from app.tools.registry import ToolRegistry

        captured = {}

        def _fake_build(caps, *, situation=None, session_id="",
                        registry=None, **kw):
            captured["registry"] = registry
            return snap_mod.CapabilityRuntimeSnapshot()

        monkeypatch.setattr(snap_mod, "build_capability_runtime_snapshot",
                            _fake_build)

        class _MetaRegistry:
            def metadata(self, tool_id):
                return {"requires_credentials": ["smtp"]} \
                    if tool_id == "send_mail" else {}

        monkeypatch.setattr(
            "app.agent_pi_bridge.try_get_tool_registry",
            lambda: _MetaRegistry(),
        )

        registry = ToolRegistry()
        from app.tools.catalog_discovery_tools import (
            register_catalog_discovery_tools,
        )

        register_catalog_discovery_tools(registry)
        await registry.dispatch("capability_runtime_status", {})
        assert captured.get("registry") is _MetaRegistry or \
            isinstance(captured.get("registry"), _MetaRegistry)

    @pytest.mark.asyncio
    async def test_production_tool_registry_absent_tolerated(self, monkeypatch):
        """registry 注入缺席(单例未就绪)→ 工具仍可用(fail-open)。"""
        import app.services.capability_runtime.snapshot as snap_mod
        from app.tools.registry import ToolRegistry

        monkeypatch.setattr(
            "app.agent_pi_bridge.try_get_tool_registry",
            lambda: None,
        )
        registry = ToolRegistry()
        from app.tools.catalog_discovery_tools import (
            register_catalog_discovery_tools,
        )

        register_catalog_discovery_tools(registry)
        result = await registry.dispatch("capability_runtime_status", {})
        assert isinstance(result, dict)


# ── session plan 凭证指纹 ────────────────────────────────────────────────


class TestSessionPlanCredentialFingerprint:
    def test_drift_false_without_stamp(self):
        from app.services.session_plan import (
            SessionPlan,
            session_plan_credential_drift,
        )

        plan = SessionPlan(envelope_id="e", session_id="s",
                           gis_chapter={"query": "x"})
        assert session_plan_credential_drift(plan) is False

    def test_drift_true_when_presence_changes(self, monkeypatch):
        from app.services.session_plan import (
            SessionPlan,
            session_plan_credential_drift,
            session_plan_stale,
        )

        monkeypatch.setenv("GIS_TOOL_CREDENTIALS", "smtp")
        from app.lib.tool_security import presence_fingerprint

        plan = SessionPlan(
            envelope_id="e", session_id="s",
            gis_chapter={
                "query": "x",
                "credential_presence_fingerprint":
                    presence_fingerprint({}),
            },
        )
        # 盖章时 presence 为空,现在有 smtp → drift
        assert session_plan_credential_drift(plan) is True
        assert session_plan_stale(plan) is True

    def test_no_drift_when_presence_stable(self, monkeypatch):
        monkeypatch.setenv("GIS_TOOL_CREDENTIALS", "smtp")
        from app.lib.tool_security import (
            presence_fingerprint,
            resolve_credential_presence,
        )

        from app.services.session_plan import (
            SessionPlan,
            session_plan_credential_drift,
        )

        plan = SessionPlan(
            envelope_id="e", session_id="s",
            gis_chapter={
                "credential_presence_fingerprint":
                    presence_fingerprint(resolve_credential_presence()),
            },
        )
        assert session_plan_credential_drift(plan) is False

    def test_projection_discloses_credential_change(self, monkeypatch):
        monkeypatch.setenv("GIS_TOOL_CREDENTIALS", "smtp")
        from app.lib.tool_security import presence_fingerprint

        from app.services.session_plan import (
            SessionPlan,
            format_session_plan_projection,
        )

        plan = SessionPlan(
            envelope_id="e", session_id="s",
            gis_chapter={
                "query": "x",
                "recipe_id": "r1",
                "credential_presence_fingerprint": presence_fingerprint({}),
            },
        )
        text = format_session_plan_projection(plan)
        assert "STALE_PLAN=true" in text
        assert "credential_presence_changed=true" in text

    @pytest.mark.asyncio
    async def test_ingest_stamps_fingerprint(self):
        """webgis_map_intent 结果 ingest 时盖章(gis_chapter 持久面)。"""
        from app.services.session_data import session_data_manager
        from app.services.session_plan import (
            apply_tool_result,
            load_session_plan,
        )

        sid = "sess-h05-stamp"
        await session_data_manager.clear_session(sid)
        try:
            await apply_tool_result(
                sid, "webgis_map_intent",
                {"success": True,
                 "plan": {"plan_id": "p1", "query": "q",
                          "recipe_id": "r"},
                 "intent": {"query": "q"}},
                success=True,
            )
            plan = await load_session_plan(sid)
            stored = plan.gis_chapter.get(
                "credential_presence_fingerprint")
            assert isinstance(stored, str) and len(stored) == 16
        finally:
            await session_data_manager.clear_session(sid)
